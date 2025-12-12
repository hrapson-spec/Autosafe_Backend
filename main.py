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
from interpolation import (
    interpolate_risk,
    MILEAGE_ORDER,
    AGE_ORDER,
    MILEAGE_BUCKETS,
    AGE_BUCKETS
)
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
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - startup and shutdown."""
    # Startup: Build SQLite from compressed data if needed (Build-on-Boot pattern)
    import build_db
    build_db.ensure_database()
    
    # After building, check if we should use SQLite or PostgreSQL
    global DATABASE_URL
    if os.path.exists(DB_FILE):
        logger.info(f"Using local {DB_FILE} (embedded SQLite - fastest)")
        DATABASE_URL = None
    elif DATABASE_URL:
        logger.info("Initializing PostgreSQL connection pool...")
        await db.get_pool()
    else:
        logger.warning("No database available - using demo mode")
    
    yield  # Application runs here
    
    # Shutdown
    logger.info("Shutting down, closing database pool...")
    await db.close_pool()


app = FastAPI(title="AutoSafe API", description="MOT Risk Prediction API", lifespan=lifespan)

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
# OPTIMIZATION: Prefer local built-on-start SQLite DB if available (faster, fresher data)
DB_FILE = 'autosafe.db'
DATABASE_URL = os.environ.get("DATABASE_URL")

# Minimum total tests required for a make/model to appear in UI dropdowns
# This filters out typos, garbage entries, and extremely rare vehicles
MIN_TESTS_FOR_UI = 100

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


def get_adjacent_mileage_bands(mileage_band: str) -> List[str]:
    """Get the current and adjacent mileage bands for interpolation."""
    try:
        idx = MILEAGE_ORDER.index(mileage_band)
        bands = [mileage_band]
        if idx > 0:
            bands.append(MILEAGE_ORDER[idx - 1])
        if idx < len(MILEAGE_ORDER) - 1:
            bands.append(MILEAGE_ORDER[idx + 1])
        return bands
    except ValueError:
        return [mileage_band]


def get_adjacent_age_bands(age_band: str) -> List[str]:
    """Get the current and adjacent age bands for interpolation."""
    try:
        idx = AGE_ORDER.index(age_band)
        bands = [age_band]
        if idx > 0:
            bands.append(AGE_ORDER[idx - 1])
        if idx < len(AGE_ORDER) - 1:
            bands.append(AGE_ORDER[idx + 1])
        return bands
    except ValueError:
        return [age_band]


def interpolate_risk_result(
    base_result: dict,
    bucket_data: Dict[str, Dict[str, float]],
    actual_mileage: int,
    actual_age: float,
    mileage_band: str,
    age_band: str
) -> dict:
    """
    Apply bilinear interpolation to risk values using actual mileage and age.

    This preserves the continuous signal from age/mileage instead of
    discretizing into coarse bins, improving ranking (AUC).

    Args:
        base_result: The base result dict from the exact bucket match
        bucket_data: Dict mapping (age_band, mileage_band) tuples to risk dicts
        actual_mileage: The actual vehicle mileage
        actual_age: The actual vehicle age in years
        mileage_band: The mileage band the vehicle falls into
        age_band: The age band the vehicle falls into

    Returns:
        Result dict with interpolated risk values
    """
    result = dict(base_result)

    # Get all risk field names
    risk_fields = [k for k in base_result.keys()
                   if k.startswith("Risk_") or k == "Failure_Risk"]

    if not risk_fields:
        return result

    # Step 1: Interpolate along mileage axis (for the current age band)
    mileage_risks_at_current_age = {}
    for mb in MILEAGE_ORDER:
        key = (age_band, mb)
        if key in bucket_data:
            mileage_risks_at_current_age[mb] = bucket_data[key]

    # Step 2: If we have adjacent age bands, interpolate along age axis too
    # For now, primarily interpolate on mileage (as noted in interpolation.py,
    # mileage is the primary continuous variable)

    for field in risk_fields:
        # Build dict of mileage_band -> risk for this field
        field_by_mileage = {
            mb: data.get(field, 0.0)
            for mb, data in mileage_risks_at_current_age.items()
            if field in data
        }

        if len(field_by_mileage) >= 2:
            # We have enough data points to interpolate
            interpolated = interpolate_risk(actual_mileage, "mileage", field_by_mileage)
            result[field] = round(interpolated, 6)
        # Otherwise keep the original value

    # Add metadata about interpolation
    result["interpolated"] = True
    result["actual_mileage"] = actual_mileage
    result["actual_age"] = actual_age

    return result


def fetch_bucket_data_sqlite(conn, model_id: str, age_band: str, mileage_bands: List[str]) -> Dict[tuple, dict]:
    """
    Fetch risk data for multiple mileage bands from SQLite.

    Returns dict mapping (age_band, mileage_band) -> risk data dict
    """
    placeholders = ",".join("?" * len(mileage_bands))
    query = f"""
        SELECT * FROM risks
        WHERE model_id = ? AND age_band = ? AND mileage_band IN ({placeholders})
    """
    params = [model_id, age_band] + mileage_bands
    rows = conn.execute(query, params).fetchall()

    result = {}
    for row in rows:
        row_dict = dict(row)
        key = (row_dict.get('age_band'), row_dict.get('mileage_band'))
        result[key] = row_dict

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
        # Only return makes with sufficient test volume
        query = """
            SELECT SUBSTR(model_id, 1, INSTR(model_id || ' ', ' ') - 1) as make,
                   SUM(Total_Tests) as test_count
            FROM risks
            GROUP BY make
            HAVING SUM(Total_Tests) >= ?
        """
        rows = conn.execute(query, (MIN_TESTS_FOR_UI,)).fetchall()
        conn.close()
        makes = sorted(set(row['make'] for row in rows))
        _cache["makes"] = {"data": makes, "time": time.time()}
        logger.info(f"Cached {len(makes)} makes from SQLite")
        return makes
    
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
        from consolidate_models import get_canonical_models_for_make
        
        # Only return models with sufficient test volume
        query = """
            SELECT model_id, SUM(Total_Tests) as test_count
            FROM risks 
            WHERE model_id LIKE ?
            GROUP BY model_id
            HAVING SUM(Total_Tests) >= ?
        """
        rows = conn.execute(query, (f"{make.upper()}%", MIN_TESTS_FOR_UI)).fetchall()
        conn.close()
        
        # Extract base models from found entries
        found_models = {}
        for row in rows:
            base_model = extract_base_model(row['model_id'], make)
            if base_model and len(base_model) > 1:
                if base_model not in found_models or row['test_count'] > found_models[base_model]:
                    found_models[base_model] = row['test_count']
        
        # Get curated list of known models for this make
        known_models = get_canonical_models_for_make(make)
        
        if known_models:
            # Only return models from curated list that exist in data
            result = sorted([m for m in known_models if m in found_models])
        else:
            # For non-curated makes, return alphabetic models only (capped)
            result = sorted([m for m in found_models.keys() if len(m) >= 3 and m.isalpha()])[:30]
        
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
    
    # Validate model+year combination (check if model was produced that year)
    from populate_model_years import check_model_year
    year_check = check_model_year(model_id, year)
    if not year_check['valid']:
        raise HTTPException(status_code=400, detail=year_check['message'])
    
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
    
    # Fallback to SQLite with interpolation
    conn = get_sqlite_connection()
    if conn:
        # Fetch adjacent mileage bands for interpolation
        mileage_bands = get_adjacent_mileage_bands(mileage_band)
        bucket_data = fetch_bucket_data_sqlite(conn, model_id, age_band, mileage_bands)

        # Check if we have any data for the current bucket
        current_key = (age_band, mileage_band)
        if current_key not in bucket_data:
            # No exact match - check if model exists at all
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

            # Model exists but not for this age/mileage combination
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

        # Get the base result from the exact bucket match
        base_result = bucket_data[current_key]

        # Apply interpolation using actual mileage and age values
        interpolated_result = interpolate_risk_result(
            base_result=base_result,
            bucket_data=bucket_data,
            actual_mileage=mileage,
            actual_age=age,
            mileage_band=mileage_band,
            age_band=age_band
        )

        return add_repair_cost_estimate(add_confidence_intervals(interpolated_result))
    
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
