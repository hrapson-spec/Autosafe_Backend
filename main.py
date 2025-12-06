"""
AutoSafe API - MOT Risk Prediction
Uses PostgreSQL (DATABASE_URL) if available, otherwise falls back to SQLite or Demo Mode.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Dict
from datetime import datetime
import os
import time

# Import database module for PostgreSQL
import database as db
from utils import get_age_band, get_mileage_band
from confidence import wilson_interval, classify_confidence
from consolidate_models import extract_base_model
from repair_costs import calculate_expected_repair_cost
import logging
import sys

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Response caching for expensive queries
_cache = {
    "makes": {"data": None, "time": 0},
    "models": {}  # Keyed by make
}
CACHE_TTL = 3600  # 1 hour cache TTL

app = FastAPI(title="AutoSafe API", description="MOT Risk Prediction API")

# CORS Middleware - Allow cross-origin requests
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


def add_confidence_intervals(result: dict) -> dict:
    """
    Add Wilson confidence intervals to a risk result.
    Modifies the result dict in place and returns it.
    """
    if 'Total_Tests' in result and 'Total_Failures' in result:
        total_tests = result['Total_Tests']
        total_failures = result['Total_Failures']
        
        # Calculate 95% confidence interval for failure risk
        ci_lower, ci_upper = wilson_interval(total_failures, total_tests)
        result['Failure_Risk_CI_Lower'] = round(ci_lower, 4)
        result['Failure_Risk_CI_Upper'] = round(ci_upper, 4)
        result['Confidence_Level'] = classify_confidence(total_tests)
    
    return result


def add_repair_cost_estimate(result: dict) -> dict:
    """
    Add expected repair cost estimate to a risk result.
    Uses the formula: E[cost|fail] = Σ(risk_i × cost_mid_i) / p_fail
    """
    cost_estimate = calculate_expected_repair_cost(result)
    if cost_estimate:
        result['Repair_Cost_Estimate'] = cost_estimate
    return result


@app.get("/api/makes", response_model=List[str])
@limiter.limit("100/minute")
async def get_makes(request: Request):
    """Return a list of all unique vehicle makes (cached for 1 hour)."""
    global _cache
    
    # Check cache first
    if _cache["makes"]["data"] and (time.time() - _cache["makes"]["time"]) < CACHE_TTL:
        logger.info("Returning cached makes list")
        return _cache["makes"]["data"]
    
    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_makes()
        if result is not None:
            _cache["makes"] = {"data": result, "time": time.time()}
            logger.info(f"Cached {len(result)} makes from PostgreSQL")
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
        result = sorted(list(makes))
        _cache["makes"] = {"data": result, "time": time.time()}
        logger.info(f"Cached {len(result)} makes from SQLite")
        return result
    
    # Demo mode
    return sorted(MOCK_MAKES)


@app.get("/api/models", response_model=List[str])
@limiter.limit("100/minute")
async def get_models(request: Request, make: str = Query(..., description="Vehicle Make (e.g., FORD)")):
    """Return a list of models for a given make (cached for 1 hour)."""
    global _cache
    cache_key = make.upper()
    
    # Check cache first
    if cache_key in _cache["models"] and (time.time() - _cache["models"][cache_key]["time"]) < CACHE_TTL:
        logger.info(f"Returning cached models for {cache_key}")
        return _cache["models"][cache_key]["data"]
    
    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_models(make)
        if result is not None:
            _cache["models"][cache_key] = {"data": result, "time": time.time()}
            logger.info(f"Cached {len(result)} models for {cache_key} from PostgreSQL")
            return result
    
    # Fallback to SQLite
    conn = get_sqlite_connection()
    if conn:
        query = "SELECT DISTINCT model_id FROM risks WHERE model_id LIKE ?"
        rows = conn.execute(query, (f"{make.upper()}%",)).fetchall()
        conn.close()
        
        all_models = set(row['model_id'] for row in rows)
        display_models = set()
        
        for mid in all_models:
            clean = extract_base_model(mid, make)
            if not clean:
                display_models.add(mid)
                continue
                
            clean_full_id = f"{make.upper()} {clean}"
            
            if clean_full_id in all_models:
                # The base model exists in the DB.
                # Only add it if THIS is the base model.
                if mid == clean_full_id:
                    display_models.add(mid)
            else:
                # The base model does NOT exist in the DB.
                # So we must show this variant.
                display_models.add(mid)
        
        result = sorted(list(display_models))
        _cache["models"][cache_key] = {"data": result, "time": time.time()}
        logger.info(f"Cached {len(result)} models for {cache_key} from SQLite")
        return result
    
    # Demo mode
    return [m for m in MOCK_MODELS if make.upper() in ["FORD", "VAUXHALL", "VOLKSWAGEN"]] or MOCK_MODELS


@app.get("/api/risk")
@limiter.limit("50/minute")
async def get_risk(
    request: Request,
    make: str = Query(..., max_length=50, description="Vehicle Make (e.g., FORD)"),
    model: str = Query(..., max_length=100, description="Vehicle Model (e.g., FIESTA)"),
    year: int = Query(..., ge=1900, le=datetime.now().year + 1, description="Vehicle Registration Year"),
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
            return add_repair_cost_estimate(add_confidence_intervals(result))
    
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
        return add_repair_cost_estimate(add_confidence_intervals(dict(row)))
    
    # Demo mode
    response = MOCK_RISK.copy()
    response["model_id"] = model_id
    return add_repair_cost_estimate(add_confidence_intervals(response))


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
