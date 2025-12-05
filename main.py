from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
import sqlite3
import pandas as pd
from utils import get_age_band, get_mileage_band
from typing import List, Dict, Optional
from datetime import datetime

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AutoSafe API", description="MOT Risk Prediction API")

import os

DB_FILE = 'autosafe.db'

def get_db_connection():
    # Check if DB exists and has data, otherwise return None (Demo Mode)
    if not os.path.exists(DB_FILE):
        return None
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        # Quick check
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
    "note": "DEMO MODE: Database not found. Deploy via VPS for full data."
}


@app.get("/api/makes", response_model=List[str])
def get_makes():
    """Return a list of all unique vehicle makes."""
    conn = get_db_connection()
    if not conn:
        return sorted(MOCK_MAKES)
        
    # Extract make from model_id (assuming format "MAKE MODEL")
    rows = conn.execute("SELECT DISTINCT model_id FROM risks").fetchall()
    conn.close()
    
    makes = set()
    for row in rows:
        parts = row['model_id'].split(' ', 1)
        if len(parts) > 0:
            makes.add(parts[0])
            
    return sorted(list(makes))

@app.get("/api/models", response_model=List[str])
def get_models(make: str = Query(..., description="Vehicle Make (e.g., FORD)")):
    """Return a list of models for a given make."""
    conn = get_db_connection()
    if not conn:
        return [m for m in MOCK_MODELS if make in ["FORD", "VAUXHALL", "VOLKSWAGEN"]] or MOCK_MODELS

    # Filter by make prefix
    query = "SELECT DISTINCT model_id FROM risks WHERE model_id LIKE ?"
    rows = conn.execute(query, (f"{make.upper()}%",)).fetchall()
    conn.close()
    
    models = []
    for row in rows:
        models.append(row['model_id'])
        
    return sorted(models)

@app.get("/api/risk")
def get_risk(
    make: str = Query(..., description="Vehicle Make (e.g., FORD)"),
    model: str = Query(..., description="Vehicle Model (e.g., FIESTA)"),
    year: int = Query(..., description="Vehicle Registration Year"),
    mileage: int = Query(..., description="Vehicle Mileage")
):
    """Calculate risk for a specific vehicle."""
    conn = get_db_connection()
    if not conn:
        # Return mock data with dynamic ID
        response = MOCK_RISK.copy()
        response["model_id"] = f"{make} {model}"
        return response

    # 1. Calculate Age
    current_year = datetime.now().year
    age = current_year - year
    
    # 2. Construct Model ID
    model_id = f"{make.upper()} {model.upper()}"
    
    # 3. Get Bands
    age_band = get_age_band(age)
    mileage_band = get_mileage_band(mileage)
    
    # Try exact match first
    query = "SELECT * FROM risks WHERE model_id = ? AND age_band = ? AND mileage_band = ?"
    row = conn.execute(query, (model_id, age_band, mileage_band)).fetchone()
    
    # If not found, try to find the model_id in the DB to see if it exists at all
    if not row:
        check_query = "SELECT 1 FROM risks WHERE model_id = ?"
        exists = conn.execute(check_query, (model_id,)).fetchone()
        
        if not exists:
            # Try fuzzy match? Or just fail.
            like_query = "SELECT DISTINCT model_id FROM risks WHERE model_id LIKE ? LIMIT 1"
            suggestion = conn.execute(like_query, (f"%{model.upper()}%",)).fetchone()
            conn.close()
            
            detail = "Vehicle model not found."
            if suggestion:
                detail += f" Did you mean '{suggestion['model_id']}'?"
            raise HTTPException(status_code=404, detail=detail)
            
        # If model exists but specific band doesn't, fallback to average
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

# Mount static files at /static (only if the folder exists)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    async def read_index():
        return FileResponse('static/index.html')
else:
    @app.get("/")
    def read_root():
        return {"status": "ok", "message": "AutoSafe API (Demo Mode)", "note": "Frontend not deployed. Use /api/makes, /api/models, /api/risk endpoints."}

