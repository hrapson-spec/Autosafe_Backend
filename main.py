"""
AutoSafe API - MOT Risk Prediction
Uses PostgreSQL (DATABASE_URL) if available, otherwise falls back to SQLite or Demo Mode.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional
from datetime import datetime
import os

# Import database module for PostgreSQL
import database as db
from utils import get_age_band, get_mileage_band
import logging
import sys

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="AutoSafe API", description="MOT Risk Prediction API")

# Check for PostgreSQL first, then SQLite
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_FILE = 'autosafe.db'

# Rate Limiting Setup
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    db_status = "disconnected"
    if DATABASE_URL:
        try:
            pool = await db.get_pool()
            if pool:
                db_status = "connected"
        except Exception:
            db_status = "error"
    
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": db_status
    }

# SQLite fallback connection (for local development)
import sqlite3

def get_sqlite_connection():
    """Get SQLite connection if available."""
    if not os.path.exists(DB_FILE):
        return None
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM risks LIMIT 1")
        return conn
    except sqlite3.Error:
        return None

# Mock Data for Demo Mode
MOCK_MAKES = ["FORD", "VAUXHALL", "VOLKSWAGEN", "BMW", "AUDI"]
MOCK_MODELS = ["FIESTA", "FOCUS", "CORSA", "GOLF", "3 SERIES"]
MOCK_RISK = {
    "model_id": "DEMO VEHICLE",
    "age_band": "3-5 years",
    "mileage_band": "20k-40k",
    "Total_Tests": 1000,
    "Total_Failures": 250,
    "Failure_Risk": 0.25,
    "Risk_Brakes": 0.05,
    "Risk_Suspension": 0.08,
    "Risk_Tyres": 0.04,
    "note": "DEMO MODE: No database connected."
}


@app.get("/api/makes", response_model=List[str])
@limiter.limit("100/minute")
async def get_makes(request: Request):
    """Return a list of all unique vehicle makes."""
    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_makes()
        if result is not None:
            return result
    
    # Fallback to SQLite
    conn = get_sqlite_connection()
    if conn:
        rows = conn.execute("SELECT DISTINCT model_id FROM risks").fetchall()
        conn.close()
        makes = set()
        for row in rows:
            parts = row['model_id'].split(' ', 1)
            if len(parts) > 0:
                makes.add(parts[0])
        return sorted(list(makes))
    
    # Demo mode
    return sorted(MOCK_MAKES)


@app.get("/api/models", response_model=List[str])
@limiter.limit("100/minute")
async def get_models(request: Request, make: str = Query(..., description="Vehicle Make (e.g., FORD)")):
    """Return a list of models for a given make."""
    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_models(make)
        if result is not None:
            return result
    
    # Fallback to SQLite
    conn = get_sqlite_connection()
    if conn:
        query = "SELECT DISTINCT model_id FROM risks WHERE model_id LIKE ?"
        rows = conn.execute(query, (f"{make.upper()}%",)).fetchall()
        conn.close()
        return sorted([row['model_id'] for row in rows])
    
    # Demo mode
    return [m for m in MOCK_MODELS if make.upper() in ["FORD", "VAUXHALL", "VOLKSWAGEN"]] or MOCK_MODELS


@app.get("/api/risk")
@limiter.limit("50/minute")
async def get_risk(
    request: Request,
    make: str = Query(..., description="Vehicle Make (e.g., FORD)"),
    model: str = Query(..., description="Vehicle Model (e.g., FIESTA)"),
    year: int = Query(..., ge=1900, le=datetime.now().year + 1, description="Vehicle Registration Year (1900-2025)"),
    mileage: int = Query(..., ge=0, le=999999, description="Vehicle Mileage (0-999,999)")
):
    """Calculate risk for a specific vehicle."""
    # Calculate age and bands
    current_year = datetime.now().year
    age = current_year - year
    model_id = f"{make.upper()} {model.upper()}"
    age_band = get_age_band(age)
    mileage_band = get_mileage_band(mileage)
    
    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_risk(model_id, age_band, mileage_band)
        if result is not None:
            if "error" in result and result["error"] == "not_found":
                detail = "Vehicle model not found."
                if result.get("suggestion"):
                    detail += f" Did you mean '{result['suggestion']}'?"
                raise HTTPException(status_code=404, detail=detail)
            return result
    
    # Fallback to SQLite
    conn = get_sqlite_connection()
    if conn:
        query = "SELECT * FROM risks WHERE model_id = ? AND age_band = ? AND mileage_band = ?"
        row = conn.execute(query, (model_id, age_band, mileage_band)).fetchone()
        
        if not row:
            check_query = "SELECT 1 FROM risks WHERE model_id = ?"
            exists = conn.execute(check_query, (model_id,)).fetchone()
            
            if not exists:
                like_query = "SELECT DISTINCT model_id FROM risks WHERE model_id LIKE ? LIMIT 1"
                suggestion = conn.execute(like_query, (f"%{model.upper()}%",)).fetchone()
                conn.close()
                
                detail = "Vehicle model not found."
                if suggestion:
                    detail += f" Did you mean '{suggestion['model_id']}'?"
                raise HTTPException(status_code=404, detail=detail)
            
            avg_query = "SELECT AVG(Failure_Risk) as avg_risk FROM risks WHERE model_id = ?"
            avg_row = conn.execute(avg_query, (model_id,)).fetchone()
            conn.close()
            
            return {
                "model_id": model_id,
                "age_band": age_band,
                "mileage_band": mileage_band,
                "note": "Exact age/mileage match not found. Returning model average.",
                "Failure_Risk": avg_row['avg_risk'] if avg_row else 0.0
            }
        
        conn.close()
        return dict(row)
    
    # Demo mode
    response = MOCK_RISK.copy()
    response["model_id"] = model_id
    return response


# Mount static files at /static (only if the folder exists)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    async def read_index():
        return FileResponse('static/index.html')
else:
    @app.get("/")
    def read_root():
        db_status = "PostgreSQL" if DATABASE_URL else ("SQLite" if os.path.exists(DB_FILE) else "Demo Mode")
        return {
            "status": "ok",
            "message": "AutoSafe API",
            "database": db_status,
            "endpoints": ["/api/makes", "/api/models", "/api/risk"]
        }
