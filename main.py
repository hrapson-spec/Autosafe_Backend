"""
AutoSafe API - MOT Risk Prediction (V55)
=========================================

Uses V55 CatBoost model with DVSA MOT History API for real-time predictions.
Falls back to SQLite lookup for vehicles without DVSA history.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional
from datetime import datetime
import os
import time

# Import database module for fallback
import database as db
from utils import get_age_band, get_mileage_band
from confidence import wilson_interval, classify_confidence
from consolidate_models import extract_base_model
from repair_costs import calculate_expected_repair_cost
from regional_defaults import validate_postcode, get_corrosion_index

# V55 imports
from dvsa_client import (
    DVSAClient, get_dvsa_client, close_dvsa_client,
    VRMValidationError, VehicleNotFoundError, DVSAAPIError
)
from feature_engineering_v55 import engineer_features
import model_v55

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
    # Startup: Load V55 model
    logger.info("Loading V55 CatBoost model...")
    if model_v55.load_model():
        logger.info("V55 model loaded successfully")
    else:
        logger.error("Failed to load V55 model - predictions will fail")

    # Build SQLite from compressed data for fallback
    import build_db
    build_db.ensure_database()

    # After building, check if we should use SQLite or PostgreSQL for fallback
    global DATABASE_URL
    if os.path.exists(DB_FILE):
        logger.info(f"Fallback database ready: {DB_FILE}")
        DATABASE_URL = None
    elif DATABASE_URL:
        logger.info("Initializing PostgreSQL connection pool for fallback...")
        await db.get_pool()
    else:
        logger.warning("No fallback database available")

    # Initialize DVSA client
    dvsa_client = get_dvsa_client()
    if dvsa_client.is_configured:
        logger.info("DVSA client initialized with OAuth credentials")
    else:
        logger.warning("DVSA OAuth credentials not configured - V55 predictions will fail")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down...")
    await close_dvsa_client()
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
@limiter.limit("20/minute")
async def get_risk(
    request: Request,
    make: str = Query(..., min_length=1, max_length=50, description="Vehicle make (e.g., FORD)"),
    model: str = Query(..., min_length=1, max_length=50, description="Vehicle model (e.g., FIESTA)"),
    year: int = Query(..., ge=1990, le=2026, description="Year of manufacture")
):
    """
    Calculate MOT failure risk using lookup table.

    Interim solution using pre-computed population averages by make/model/age.
    Returns confidence level based on sample size in the lookup data.
    """
    # Normalize inputs
    make_upper = make.strip().upper()
    model_upper = model.strip().upper()
    model_id = f"{make_upper} {model_upper}"

    # Calculate age band from year
    age = datetime.now().year - year
    age_band = get_age_band(age)

    # Default response (population average)
    default_response = {
        "vehicle": model_id,
        "year": year,
        "mileage": None,
        "last_mot_date": None,
        "last_mot_result": None,
        "failure_risk": 0.28,  # UK population average
        "confidence_level": "Low",
        "risk_brakes": 0.05,
        "risk_suspension": 0.04,
        "risk_tyres": 0.03,
        "risk_steering": 0.02,
        "risk_visibility": 0.02,
        "risk_lamps": 0.03,
        "risk_body": 0.02,
        "repair_cost_estimate": {"expected": "£250", "range_low": 100, "range_high": 500},
    }

    # Try SQLite lookup
    conn = get_sqlite_connection()
    if not conn:
        logger.warning("No database connection available, returning population average")
        return default_response

    try:
        # Query for exact make/model match with age band
        # Try with model_id pattern matching
        query = """
            SELECT * FROM risks
            WHERE model_id LIKE ? AND age_band = ?
            ORDER BY Total_Tests DESC
            LIMIT 1
        """
        row = conn.execute(query, (f"{make_upper} {model_upper}%", age_band)).fetchone()

        # If not found, try just the make with age band
        if not row:
            query = """
                SELECT * FROM risks
                WHERE model_id LIKE ? AND age_band = ?
                ORDER BY Total_Tests DESC
                LIMIT 1
            """
            row = conn.execute(query, (f"{make_upper}%", age_band)).fetchone()

        if not row:
            logger.info(f"No lookup data for {model_id} age_band={age_band}, returning population average")
            conn.close()
            return default_response

        result = dict(row)
        conn.close()

        # Calculate confidence level based on sample size
        total_tests = result.get('Total_Tests', 0)
        if total_tests >= 1000:
            confidence_level = "High"
        elif total_tests >= 100:
            confidence_level = "Medium"
        else:
            confidence_level = "Low"

        # Add confidence intervals and repair cost estimate
        result = add_confidence_intervals(result)
        result = add_repair_cost_estimate(result)

        # Format repair cost for display
        repair_cost = result.get('Repair_Cost_Estimate', {})
        if isinstance(repair_cost, dict):
            expected = repair_cost.get('expected', 250)
            repair_cost_formatted = {
                "expected": f"£{expected}",
                "range_low": repair_cost.get('range_low', 100),
                "range_high": repair_cost.get('range_high', 500),
            }
        else:
            repair_cost_formatted = {"expected": "£250", "range_low": 100, "range_high": 500}

        return {
            "vehicle": model_id,
            "year": year,
            "mileage": None,
            "last_mot_date": None,
            "last_mot_result": None,
            "failure_risk": result.get('Failure_Risk', 0.28),
            "confidence_level": confidence_level,
            "risk_brakes": result.get('Risk_Brakes', 0.05),
            "risk_suspension": result.get('Risk_Suspension', 0.04),
            "risk_tyres": result.get('Risk_Tyres', 0.03),
            "risk_steering": result.get('Risk_Steering', 0.02),
            "risk_visibility": result.get('Risk_Visibility', 0.02),
            "risk_lamps": result.get('Risk_Lamps_Reflectors_Electrical_Equipment', 0.03),
            "risk_body": result.get('Risk_Body_Chassis_Structure_Exhaust', 0.02),
            "repair_cost_estimate": repair_cost_formatted,
        }

    except Exception as e:
        logger.error(f"Database error during lookup: {e}")
        if conn:
            conn.close()
        return default_response


async def _fallback_prediction(
    registration: str,
    make: str,
    model: str,
    year: Optional[int],
    postcode: str,
    note: str = ""
) -> Dict:
    """
    Fallback prediction using SQLite lookup when DVSA data unavailable.
    """
    if not make or not model:
        raise HTTPException(
            status_code=404,
            detail="Insufficient vehicle data for fallback prediction"
        )

    model_id = f"{make.upper()} {model.upper()}"

    # Calculate age band if year available
    if year:
        age = datetime.now().year - year
        age_band = get_age_band(age)
    else:
        age_band = "6-10"  # Default to middle band

    mileage_band = "30k-60k"  # Default

    # Try SQLite fallback
    conn = get_sqlite_connection()
    if conn:
        query = "SELECT * FROM risks WHERE model_id LIKE ? AND age_band = ? LIMIT 1"
        row = conn.execute(query, (f"{make.upper()}%", age_band)).fetchone()

        if row:
            result = dict(row)
            conn.close()

            result = add_confidence_intervals(result)
            result = add_repair_cost_estimate(result)

            return {
                "registration": registration,
                "vehicle": model_id,
                "year": year,
                "mileage": None,
                "last_mot_date": None,
                "failure_risk": result.get('Failure_Risk', 0.28),
                "confidence_level": "Medium",
                "risk_brakes": result.get('Risk_Brakes', 0.05),
                "risk_suspension": result.get('Risk_Suspension', 0.04),
                "risk_tyres": result.get('Risk_Tyres', 0.03),
                "risk_steering": result.get('Risk_Steering', 0.02),
                "risk_visibility": result.get('Risk_Visibility', 0.02),
                "risk_lamps": result.get('Risk_Lamps_Reflectors_Electrical_Equipment', 0.03),
                "risk_body": result.get('Risk_Body_Chassis_Structure_Exhaust', 0.02),
                "repair_cost_estimate": result.get('Repair_Cost_Estimate'),
                "prediction_source": "Lookup (no MOT history)",
                "note": note,
            }

        conn.close()

    # Default fallback
    return {
        "registration": registration,
        "vehicle": model_id,
        "year": year,
        "mileage": None,
        "failure_risk": 0.28,  # UK average
        "confidence_level": "Low",
        "risk_brakes": 0.05,
        "risk_suspension": 0.04,
        "risk_tyres": 0.03,
        "risk_steering": 0.02,
        "risk_visibility": 0.02,
        "risk_lamps": 0.03,
        "risk_body": 0.02,
        "repair_cost_estimate": {"expected": 250, "range_low": 100, "range_high": 500},
        "prediction_source": "Lookup (limited data)",
        "note": note or "Limited prediction - using population averages",
    }


def _estimate_repair_cost(failure_risk: float, risk_components: Dict[str, float]) -> Dict:
    """Estimate repair costs based on risk prediction."""
    # Component repair cost ranges (mid-point estimates)
    component_costs = {
        'brakes': 200,
        'suspension': 350,
        'tyres': 150,
        'steering': 300,
        'visibility': 80,
        'lamps': 60,
        'body': 400,
    }

    # Expected cost = sum of (component_risk * component_cost)
    expected = sum(
        risk_components.get(comp, 0) * cost
        for comp, cost in component_costs.items()
    )

    # Scale by overall failure probability
    expected = expected * (failure_risk / 0.28)  # Normalize to average fail rate

    return {
        "expected": int(round(expected, -1)),  # Round to nearest 10
        "range_low": int(round(expected * 0.5, -1)),
        "range_high": int(round(expected * 2, -1)),
    }


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
