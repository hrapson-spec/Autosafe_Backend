"""
AutoSafe API - MOT Risk Prediction
Uses PostgreSQL (DATABASE_URL) if available, otherwise falls back to SQLite or Demo Mode.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional, Any
from datetime import datetime
import os
import time
import sqlite3

# Import database module for PostgreSQL
import database as db
from utils import get_age_band, get_mileage_band
from confidence import wilson_interval, classify_confidence
from consolidate_models import extract_base_model
from repair_costs import calculate_expected_repair_cost
from interpolation import interpolate_risk, get_mileage_bucket, MILEAGE_ORDER
from history_adjustment import HistoryAdjustment, HISTORY_ADJUSTMENT_ENABLED
from component_risk_adjustment import ComponentRiskAdjustment
from dvla_client import DVLAClient, DVLAError, DVLANotFoundError, DVLARateLimitError, DVLAValidationError
from config import (
    GAP_THRESHOLD_SHORT_DAYS,
    GAP_THRESHOLD_MEDIUM_DAYS,
    GAP_THRESHOLD_LONG_DAYS,
    CACHE_TTL_SECONDS,
    MIN_TESTS_FOR_UI,
    MAX_MILEAGE_CAP,
    MIN_MILEAGE_FOR_OLD_CAR,
    MAX_MILEAGE_FOR_NEW_CAR,
    AGE_THRESHOLD_FOR_LOW_MILEAGE,
    AGE_THRESHOLD_FOR_HIGH_MILEAGE,
)
import logging
import sys

# =============================================================================
# Application State (Module-level Singletons)
# =============================================================================
# These globals are initialized during app startup (lifespan context) and
# remain constant for the lifetime of the application. This is the standard
# pattern for FastAPI applications with async connection pools and caches.
#
# - _history_adjustment: Loaded once at startup from history_weights.json
# - _cache: TTL-based response cache for /api/makes and /api/models endpoints
#
# For testing, these can be reset via the lifespan context or by restarting
# the application.
# =============================================================================

HISTORY_WEIGHTS_FILE = 'history_weights.json'
COMPONENT_RISK_WEIGHTS_FILE = 'component_risk_weights.json'
_history_adjustment: Optional[HistoryAdjustment] = None
_component_risk_adjustment: Optional[ComponentRiskAdjustment] = None

def get_gap_band(days_since_prev: Optional[int]) -> str:
    """Convert days since previous test to gap band."""
    if days_since_prev is None:
        return 'NONE'
    if days_since_prev < GAP_THRESHOLD_SHORT_DAYS:
        return '<180d'
    elif days_since_prev < GAP_THRESHOLD_MEDIUM_DAYS:
        return '180d-1y'
    elif days_since_prev < GAP_THRESHOLD_LONG_DAYS:
        return '1-2y'
    else:
        return '2y+'


# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Response cache: TTL-based cache for /api/makes and /api/models endpoints.
# Populated on first request, expires after CACHE_TTL_SECONDS.
# Protected by _cache_lock to prevent race conditions in async context.
import asyncio
_cache = {
    "makes": {"data": None, "time": 0},  # {"data": List[str], "time": float}
    "models": {}  # Dict[str, {"data": List[str], "time": float}] keyed by make
}
_cache_lock = asyncio.Lock()

# Database configuration - Initialize before lifespan to avoid undefined reference
DB_FILE = 'autosafe.db'
DATABASE_URL = os.environ.get("DATABASE_URL")

# DVLA API configuration
DVLA_API_KEY = os.environ.get("DVLA_API_KEY")
DVLA_USE_TEST_ENV = os.environ.get("DVLA_USE_TEST_ENV", "false").lower() == "true"
_dvla_client: Optional[DVLAClient] = None

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - startup and shutdown."""
    global DATABASE_URL, _history_adjustment, _dvla_client

    # Startup: Build SQLite from compressed data if needed (Build-on-Boot pattern)
    import build_db
    build_db.ensure_database()

    # After building, check if we should use SQLite or PostgreSQL
    if os.path.exists(DB_FILE):
        logger.info(f"Using local {DB_FILE} (embedded SQLite - fastest)")
        DATABASE_URL = None
    elif DATABASE_URL:
        logger.info("Initializing PostgreSQL connection pool...")
        await db.get_pool()
    else:
        logger.warning("No database available - using demo mode")
    
    # Load history adjustment weights if enabled and available
    if HISTORY_ADJUSTMENT_ENABLED and os.path.exists(HISTORY_WEIGHTS_FILE):
        try:
            _history_adjustment = HistoryAdjustment.load(HISTORY_WEIGHTS_FILE)
            logger.info(f"Loaded history adjustment weights from {HISTORY_WEIGHTS_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load history weights: {e}")
            _history_adjustment = None
    else:
        if not HISTORY_ADJUSTMENT_ENABLED:
            logger.info("History adjustment disabled by feature flag")
        else:
            logger.warning(f"History weights file not found: {HISTORY_WEIGHTS_FILE}")

    # Load component-specific risk adjustment weights
    if os.path.exists(COMPONENT_RISK_WEIGHTS_FILE):
        try:
            _component_risk_adjustment = ComponentRiskAdjustment.load(COMPONENT_RISK_WEIGHTS_FILE)
            logger.info(f"Loaded component risk adjustment weights from {COMPONENT_RISK_WEIGHTS_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load component risk weights: {e}")
            _component_risk_adjustment = None
    else:
        logger.info(f"Component risk weights file not found: {COMPONENT_RISK_WEIGHTS_FILE}")

    # Initialize DVLA client
    _dvla_client = DVLAClient(api_key=DVLA_API_KEY, use_test_env=DVLA_USE_TEST_ENV)
    if DVLA_API_KEY:
        logger.info(f"DVLA client initialized (test_env={DVLA_USE_TEST_ENV})")
    else:
        logger.info("DVLA client initialized in DEMO MODE (no API key)")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down, closing database pool...")
    await db.close_pool()


app = FastAPI(title="AutoSafe API", description="MOT Risk Prediction API", lifespan=lifespan)

# CORS Middleware - Allow cross-origin requests
# Note: allow_credentials=True requires explicit origins (not "*")
from fastapi.middleware.cors import CORSMiddleware

# Define allowed origins - update this list for production deployments
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]
# Also allow origins from environment variable (comma-separated)
import os as _os
if _os.environ.get("ALLOWED_ORIGINS"):
    ALLOWED_ORIGINS.extend(_os.environ["ALLOWED_ORIGINS"].split(","))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MIN_TESTS_FOR_UI imported from config.py

# Rate Limiting Setup
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    db_status = "disconnected"
    if DATABASE_URL:
        try:
            pool = await db.get_pool()
            if pool:
                db_status = "connected"
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Database connection error in health check: {e}")
            db_status = "error"
        except Exception as e:
            logger.error(f"Unexpected error in health check: {e}")
            db_status = "error"

    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": db_status
    }

# SQLite fallback connection (for local development)
import sqlite3

def get_sqlite_connection() -> Optional[sqlite3.Connection]:
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


def interpolate_sqlite_result(
    rows: List[sqlite3.Row],
    actual_mileage: int,
    model_id: str,
    age_band: str,
    mileage_band: str
) -> Optional[Dict[str, Any]]:
    """
    Apply interpolation to SQLite query results across mileage bands.
    
    This eliminates 'cliff' discontinuities by smoothly interpolating
    between bucket centers based on actual mileage.
    """
    if not rows:
        return None
    
    # Build mileage band risk data for interpolation
    mileage_band_risks = {}  # Maps band name to {risk_column: value}
    total_tests_sum = 0
    total_failures_sum = 0
    target_band_row = None  # Row matching the requested mileage_band

    # Identify risk columns (those starting with Risk_ or named Failure_Risk)
    risk_columns = []
    if rows:
        sample_keys = rows[0].keys()
        risk_columns = [k for k in sample_keys if k.startswith('Risk_') or k == 'Failure_Risk']

    for row in rows:
        sqlite_row = dict(row)  # Convert sqlite3.Row to dict for .get() support
        band = sqlite_row['mileage_band']
        tests = sqlite_row.get('Total_Tests', 0) or 0
        failures = sqlite_row.get('Total_Failures', 0) or 0
        total_tests_sum += tests
        total_failures_sum += failures

        # Store risk values for this band
        mileage_band_risks[band] = {col: float(sqlite_row[col]) if sqlite_row.get(col) else 0.0 for col in risk_columns}

        # Track if this is the target band
        if band == mileage_band:
            target_band_row = sqlite_row

    # If only one band or no data, return raw result
    if len(mileage_band_risks) <= 1:
        if target_band_row:
            return target_band_row
        elif rows:
            return dict(rows[0])
        return None
    
    # Build result with interpolated values
    result = {
        'model_id': model_id,
        'age_band': age_band,
        'mileage_band': mileage_band,
        'Total_Tests': total_tests_sum,
        'Total_Failures': total_failures_sum,
        'Interpolated': True,
    }
    
    # Interpolate each risk column
    for col in risk_columns:
        bucket_risks = {band: data.get(col, 0.0) for band, data in mileage_band_risks.items()}
        result[col] = round(interpolate_risk(actual_mileage, "mileage", bucket_risks), 6)
    
    return result

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


def add_confidence_intervals(result: Dict[str, Any]) -> Dict[str, Any]:
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


def add_repair_cost_estimate(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add expected repair cost estimate to a risk result.
    Uses the formula: E[cost|fail] = Σ(risk_i × cost_mid_i) / p_fail
    """
    cost_estimate = calculate_expected_repair_cost(result)
    if cost_estimate:
        result['Repair_Cost_Estimate'] = cost_estimate
    return result


def get_usage_intensity_band(
    current_mileage: Optional[int],
    prev_mileage: Optional[int],
    days_since_prev: Optional[int]
) -> Optional[str]:
    """
    Compute usage intensity band from mileage trajectory.
    
    Returns 'low', 'medium', 'high', or 'unknown' based on annual mileage rate.
    """
    if days_since_prev is None or days_since_prev <= 0:
        return 'unknown'
    if current_mileage is None or prev_mileage is None:
        return 'unknown'
    if current_mileage < prev_mileage:
        return 'unknown'  # Mileage rollback or error
    
    mileage_delta = current_mileage - prev_mileage
    miles_per_year = mileage_delta * 365.25 / days_since_prev
    
    if miles_per_year > 50000:
        return 'high'  # Cap extreme values
    elif miles_per_year < 6000:
        return 'low'
    elif miles_per_year < 12000:
        return 'medium'
    else:
        return 'high'


def apply_history_adjustment(
    result: Dict[str, Any],
    prev_outcome: Optional[str],
    days_since_prev: Optional[int],
    usage_intensity_band: Optional[str] = None,
    severity_band: Optional[str] = None,
    prev_prev_outcome: Optional[str] = None,
    age_band: Optional[str] = None
) -> Dict[str, Any]:
    """
    Apply history adjustment to a risk result.

    Adjusts Failure_Risk based on vehicle history using learned weights.
    Features (Dec 2025):
      - Multi-cycle: considers last two outcomes (e.g., FAIL_FAIL vs FAIL_PASS)
      - Age×Outcome: age-dependent recovery from failures
      - Severity: weighted advisory score from previous test (NONE, LOW, MEDIUM, HIGH)
      - Combined: +3.6% AUC lift over single-cycle baseline
    """
    global _history_adjustment

    if _history_adjustment is None or prev_outcome is None:
        return result

    # Get base risk
    base_risk = result.get('Failure_Risk', 0)
    if base_risk <= 0:
        return result

    # Compute gap band
    gap_band = get_gap_band(days_since_prev)

    # Apply adjustment with all available features
    adjusted_risk = _history_adjustment.apply(
        base_risk, prev_outcome, gap_band, days_since_prev,
        usage_intensity_band=usage_intensity_band,
        severity_band=severity_band,
        prev_prev_outcome_band=prev_prev_outcome,
        age_band=age_band
    )

    # Update result
    result['Base_Failure_Risk'] = round(float(base_risk), 6)
    result['Failure_Risk'] = round(float(adjusted_risk), 6)
    result['History_Adjustment'] = {
        'prev_outcome': prev_outcome,
        'prev_prev_outcome': prev_prev_outcome,
        'gap_band': gap_band,
        'usage_intensity_band': usage_intensity_band,
        'severity_band': severity_band,
        'age_band': age_band,
        'adjustment_applied': True
    }

    return result


def apply_component_risk_adjustment(
    result: Dict[str, Any],
    component_history: Optional[Dict[str, int]]
) -> Dict[str, Any]:
    """
    Apply component-specific risk adjustments.

    Adjusts individual Risk_X fields (Risk_Brakes, Risk_Suspension, etc.)
    based on whether the same component failed in the previous MOT cycle.

    For example, if prev_brake_failure=1, Risk_Brakes is increased.
    This provides actionable component-level predictions.
    """
    global _component_risk_adjustment

    if _component_risk_adjustment is None or component_history is None:
        return result

    # Check if any component history flags are set
    has_any_history = any(v == 1 for v in component_history.values())
    if not has_any_history:
        return result

    # Apply adjustments using the loaded model
    return _component_risk_adjustment.apply_to_result(result, component_history)


# =============================================================================
# HELPER FUNCTIONS FOR get_risk() DECOMPOSITION
# =============================================================================

def enrich_risk_response(
    result: Dict[str, Any],
    warnings: List[str],
    prev_outcome: Optional[str],
    days_since_prev: Optional[int],
    component_history: Optional[Dict[str, int]] = None,
    usage_intensity_band: Optional[str] = None,
    severity_band: Optional[str] = None,
    prev_prev_outcome: Optional[str] = None,
    age_band: Optional[str] = None
) -> Dict[str, Any]:
    """Apply all enrichment transformations to a risk result.

    Applies in order: confidence intervals -> repair cost -> history adjustment -> component adjustment.

    Args:
        result: Base risk result dictionary.
        warnings: List of warning messages to include.
        prev_outcome: Previous MOT outcome for history adjustment.
        days_since_prev: Days since previous test for history adjustment.
        component_history: Dict mapping prev_X_failure to 0/1 flags.
        usage_intensity_band: Vehicle usage intensity ('low', 'medium', 'high', 'unknown').
        severity_band: Severity-weighted advisory score (NONE, LOW, MEDIUM, HIGH).
        prev_prev_outcome: Second-previous MOT outcome for multi-cycle history.
        age_band: Vehicle age band for age×outcome interaction.

    Returns:
        Enriched result dictionary.
    """
    if warnings:
        result["warnings"] = warnings

    # Calculate raw proportion (what Wilson interval is centered on)
    total_tests = result.get('Total_Tests', 0)
    total_failures = result.get('Total_Failures', 0)
    raw_proportion = total_failures / total_tests if total_tests > 0 else 0

    result = add_repair_cost_estimate(add_confidence_intervals(result))
    result = apply_history_adjustment(
        result, prev_outcome, days_since_prev, usage_intensity_band,
        severity_band=severity_band,
        prev_prev_outcome=prev_prev_outcome, age_band=age_band
    )
    result = apply_component_risk_adjustment(result, component_history)

    # Shift CI bounds to match actual Failure_Risk (CI was calculated on raw proportion)
    final_risk = result.get('Failure_Risk', 0)
    if total_tests > 0 and 'Failure_Risk_CI_Lower' in result and 'Failure_Risk_CI_Upper' in result:
        delta = final_risk - raw_proportion
        result['Failure_Risk_CI_Lower'] = round(max(0, result['Failure_Risk_CI_Lower'] + delta), 4)
        result['Failure_Risk_CI_Upper'] = round(min(1, result['Failure_Risk_CI_Upper'] + delta), 4)

    return result


def get_sqlite_fallback_risk(
    conn: sqlite3.Connection,
    model_id: str,
    make: str,
    age_band: str,
    mileage_band: str
) -> Optional[Dict[str, Any]]:
    """Try make-level then global-level fallback for unseen models.

    Args:
        conn: SQLite connection (caller responsible for closing).
        model_id: Full model ID (e.g., "FORD FIESTA").
        make: Vehicle make.
        age_band: Age band string.
        mileage_band: Mileage band string.

    Returns:
        Fallback risk dict with 'note' explaining source, or None if no data.
    """
    # 1. Try Make Average
    make_query = "SELECT SUM(Total_Failures) as tf, SUM(Total_Tests) as tt FROM risks WHERE model_id LIKE ?"
    make_row = conn.execute(make_query, (f"{make.upper()} %",)).fetchone()

    if make_row and make_row['tt'] and make_row['tt'] > 0:
        return {
            "Model_Id": model_id,
            "Age_Band": age_band,
            "Mileage_Band": mileage_band,
            "note": "Unseen model. Using Make average fallback.",
            "Failure_Risk": make_row['tf'] / make_row['tt'],
            "Total_Tests": make_row['tt'],
        }

    # 2. Try Global Average
    global_query = "SELECT SUM(Total_Failures) as tf, SUM(Total_Tests) as tt FROM risks"
    global_row = conn.execute(global_query).fetchone()

    if global_row and global_row['tt'] and global_row['tt'] > 0:
        return {
            "Model_Id": model_id,
            "Age_Band": age_band,
            "Mileage_Band": mileage_band,
            "note": "Unseen model. Using Global average fallback.",
            "Failure_Risk": global_row['tf'] / global_row['tt'],
            "Total_Tests": global_row['tt'],
        }

    return None


def get_sqlite_model_suggestion(conn: sqlite3.Connection, model: str) -> Optional[str]:
    """Find a similar model name to suggest for 404 errors.

    Args:
        conn: SQLite connection.
        model: Model name to search for.

    Returns:
        Suggested model_id string, or None if no similar match found.
    """
    like_query = "SELECT DISTINCT model_id FROM risks WHERE model_id LIKE ? LIMIT 1"
    suggestion = conn.execute(like_query, (f"%{model.upper()}%",)).fetchone()
    return suggestion['model_id'] if suggestion else None


@app.get("/api/makes", response_model=List[str])
@limiter.limit("100/minute")
async def get_makes(request: Request):
    """Return a list of all unique vehicle makes (cached for 1 hour)."""
    global _cache

    # Check cache first (with lock to prevent race conditions)
    async with _cache_lock:
        if _cache["makes"]["data"] and (time.time() - _cache["makes"]["time"]) < CACHE_TTL_SECONDS:
            logger.info("Returning cached makes list")
            return _cache["makes"]["data"]

    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_makes()
        if result is not None:
            async with _cache_lock:
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
        async with _cache_lock:
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

    # Check cache first (with lock to prevent race conditions)
    async with _cache_lock:
        if cache_key in _cache["models"] and (time.time() - _cache["models"][cache_key]["time"]) < CACHE_TTL_SECONDS:
            logger.info(f"Returning cached models for {cache_key}")
            return _cache["models"][cache_key]["data"]

    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_models(make)
        if result is not None:
            async with _cache_lock:
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

        async with _cache_lock:
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
    mileage: int = Query(..., ge=0, le=999999, description="Vehicle Mileage (0-999,999)"),
    prev_outcome: Optional[str] = Query(None, description="Previous MOT outcome: PASS, FAIL, or NONE (optional)"),
    days_since_prev: Optional[int] = Query(None, ge=0, le=3650, description="Days since previous MOT (0-3650, optional)"),
    prev_mileage: Optional[int] = Query(None, ge=0, le=999999, description="Previous MOT mileage (optional, for usage intensity)"),
    prev_prev_outcome: Optional[str] = Query(None, description="Second-previous MOT outcome: PASS, FAIL, or NONE (optional, for multi-cycle)"),
    # Component history parameters - set to 1 if that component failed in previous MOT
    prev_brake_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had brake failure (0 or 1)"),
    prev_tyre_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had tyre failure (0 or 1)"),
    prev_suspension_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had suspension failure (0 or 1)"),
    prev_steering_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had steering failure (0 or 1)"),
    prev_lights_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had lights failure (0 or 1)"),
    prev_body_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had body/chassis failure (0 or 1)"),
    prev_emissions_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had emissions failure (0 or 1)"),
    prev_wheels_failure: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had wheels failure (0 or 1)"),
    # Component advisory parameters - set to 1 if that component had an advisory in previous MOT
    prev_brake_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had brake advisory (0 or 1)"),
    prev_tyre_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had tyre advisory (0 or 1)"),
    prev_suspension_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had suspension advisory (0 or 1)"),
    prev_steering_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had steering advisory (0 or 1)"),
    prev_lights_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had lights advisory (0 or 1)"),
    prev_body_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had body/chassis advisory (0 or 1)"),
    prev_emissions_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had emissions advisory (0 or 1)"),
    prev_wheels_advisory: Optional[int] = Query(None, ge=0, le=1, description="Previous MOT had wheels advisory (0 or 1)"),
):
    """Calculate risk for a specific vehicle.

    If prev_outcome and days_since_prev are provided, the overall Failure_Risk will be
    adjusted based on vehicle history.

    If prev_prev_outcome is also provided, multi-cycle history is used for improved
    accuracy (e.g., FAIL→FAIL patterns are riskier than FAIL→PASS).

    If component history parameters (prev_brake_failure, prev_suspension_failure, etc.)
    are provided, the individual component risks (Risk_Brakes, Risk_Suspension, etc.)
    will be adjusted. This provides actionable insights - e.g., if brakes failed last
    year, Risk_Brakes will be increased to reflect the higher recurrence probability.
    """
    # Calculate age and bands
    current_year = datetime.now().year
    age = current_year - year
    model_id = f"{make.upper()} {model.upper()}"
    age_band = get_age_band(age)
    mileage_band = get_mileage_band(mileage)

    # Optional: Validate inference features against contracts (gated by env var)
    if os.getenv('VALIDATE_CONTRACTS', 'false').lower() == 'true':
        from feature_validation import validate_inference_features
        inference_features = {'make', 'model', 'model_id', 'age_band', 'mileage_band'}
        if prev_outcome is not None:
            inference_features.update({'prev_cycle_outcome_band', 'gap_band'})
        validate_inference_features(inference_features)

    warnings = []
    
    # Validate history parameters if provided
    if prev_outcome is not None:
        prev_outcome = prev_outcome.upper()
        if prev_outcome not in ('PASS', 'FAIL', 'NONE'):
            raise HTTPException(status_code=400, detail="prev_outcome must be PASS, FAIL, or NONE")

    if prev_prev_outcome is not None:
        prev_prev_outcome = prev_prev_outcome.upper()
        if prev_prev_outcome not in ('PASS', 'FAIL', 'NONE'):
            raise HTTPException(status_code=400, detail="prev_prev_outcome must be PASS, FAIL, or NONE")

    # Build component history dict from parameters (only include if any are provided)
    # Includes both failure and advisory parameters
    component_history: Optional[Dict[str, int]] = None
    component_params = {
        # Failure parameters
        'prev_brake_failure': prev_brake_failure,
        'prev_tyre_failure': prev_tyre_failure,
        'prev_suspension_failure': prev_suspension_failure,
        'prev_steering_failure': prev_steering_failure,
        'prev_lights_failure': prev_lights_failure,
        'prev_body_failure': prev_body_failure,
        'prev_emissions_failure': prev_emissions_failure,
        'prev_wheels_failure': prev_wheels_failure,
        # Advisory parameters (new - for advisory-to-failure prediction)
        'prev_brake_advisory': prev_brake_advisory,
        'prev_tyre_advisory': prev_tyre_advisory,
        'prev_suspension_advisory': prev_suspension_advisory,
        'prev_steering_advisory': prev_steering_advisory,
        'prev_lights_advisory': prev_lights_advisory,
        'prev_body_advisory': prev_body_advisory,
        'prev_emissions_advisory': prev_emissions_advisory,
        'prev_wheels_advisory': prev_wheels_advisory,
    }
    # Only build dict if at least one component param was provided
    if any(v is not None for v in component_params.values()):
        component_history = {k: (v if v is not None else 0) for k, v in component_params.items()}

    # Robustness: Mileage Sanity Checks
    if mileage > MAX_MILEAGE_CAP:
        mileage = MAX_MILEAGE_CAP
        mileage_band = get_mileage_band(mileage)  # re-calculate band
        warnings.append(f"Mileage capped at {MAX_MILEAGE_CAP:,} for risk estimation.")

    # Robustness: Age/Mileage Contradictions
    if age > AGE_THRESHOLD_FOR_LOW_MILEAGE and mileage < MIN_MILEAGE_FOR_OLD_CAR:
        warnings.append(f"Unusually low mileage ({mileage}) for vehicle age ({age} years).")
    if age < AGE_THRESHOLD_FOR_HIGH_MILEAGE and mileage > MAX_MILEAGE_FOR_NEW_CAR:
        warnings.append(f"Unusually high mileage ({mileage}) for vehicle age ({age} years).")
    
    # Compute usage intensity band from mileage trajectory (if prev_mileage provided)
    usage_intensity_band = get_usage_intensity_band(mileage, prev_mileage, days_since_prev)
    
    # Validate model+year combination (check if model was produced that year)
    from populate_model_years import check_model_year
    year_check = check_model_year(model_id, year)
    if not year_check['valid']:
        raise HTTPException(status_code=422, detail=year_check['message'])

    
    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_risk(model_id, age_band, mileage_band)
        if result is not None:
            if "error" in result and result["error"] == "not_found":
                detail = "Vehicle model not found."
                if result.get("suggestion"):
                    detail += f" Did you mean '{result['suggestion']}'?"
                
                # Check for robustness fallback (Make or Global average)
                fallback = await db.get_fallback_risk(make)
                if fallback:
                    response = {
                        "Model_Id": model_id,
                        "Age_Band": age_band,
                        "Mileage_Band": mileage_band,
                        "note": f"Unseen model. Using {fallback['level']} average fallback.",
                        "Failure_Risk": fallback['Failure_Risk'],
                        "Total_Tests": fallback['Total_Tests']
                    }
                    return enrich_risk_response(response, warnings, prev_outcome, days_since_prev, component_history, usage_intensity_band, prev_prev_outcome=prev_prev_outcome, age_band=age_band)

                raise HTTPException(status_code=404, detail=detail)

            return enrich_risk_response(result, warnings, prev_outcome, days_since_prev, component_history, usage_intensity_band, prev_prev_outcome=prev_prev_outcome, age_band=age_band)
    
    # Fallback to SQLite with interpolation
    conn = get_sqlite_connection()
    if conn:
        try:
            # Fetch ALL mileage bands for this model/age to enable interpolation
            query = "SELECT * FROM risks WHERE model_id = ? AND age_band = ?"
            rows = conn.execute(query, (model_id, age_band)).fetchall()

            if rows:
                # Apply interpolation using actual mileage
                result = interpolate_sqlite_result(rows, mileage, model_id, age_band, mileage_band)
                if result:
                    return enrich_risk_response(result, warnings, prev_outcome, days_since_prev, component_history, usage_intensity_band, prev_prev_outcome=prev_prev_outcome, age_band=age_band)

            # No rows for this age band - check if model exists at all
            check_query = "SELECT 1 FROM risks WHERE model_id = ?"
            exists = conn.execute(check_query, (model_id,)).fetchone()

            if not exists:
                # Model not found - try fallback or 404
                suggestion = get_sqlite_model_suggestion(conn, model)
                fallback = get_sqlite_fallback_risk(conn, model_id, make, age_band, mileage_band)

                if fallback:
                    return enrich_risk_response(fallback, warnings, prev_outcome, days_since_prev, component_history, usage_intensity_band, prev_prev_outcome=prev_prev_outcome, age_band=age_band)

                # No fallback available - raise 404
                detail = "Vehicle model not found."
                if suggestion:
                    detail += f" Did you mean '{suggestion}'?"
                raise HTTPException(status_code=404, detail=detail)

            # Model exists but no data for this age band - use model average
            avg_query = "SELECT AVG(Failure_Risk) as avg_risk FROM risks WHERE model_id = ?"
            avg_row = conn.execute(avg_query, (model_id,)).fetchone()

            result = {
                "Model_Id": model_id,
                "Age_Band": age_band,
                "Mileage_Band": mileage_band,
                "note": "Exact age/mileage match not found. Returning model average.",
                "Failure_Risk": float(avg_row['avg_risk']) if (avg_row and avg_row['avg_risk'] is not None) else 0.0
            }
            return enrich_risk_response(result, warnings, prev_outcome, days_since_prev, component_history, usage_intensity_band, prev_prev_outcome=prev_prev_outcome, age_band=age_band)
        finally:
            conn.close()

    # Demo mode
    response = MOCK_RISK.copy()
    response["Model_Id"] = model_id
    return enrich_risk_response(response, warnings, prev_outcome, days_since_prev, component_history, usage_intensity_band, prev_prev_outcome=prev_prev_outcome, age_band=age_band)


@app.get("/api/vehicle")
async def get_vehicle_details(
    registration: str = Query(..., min_length=2, max_length=8, description="UK Vehicle Registration Number (e.g., AB12CDE)")
):
    """
    Look up vehicle details by registration number.

    Fetches vehicle information from DVLA and combines it with AutoSafe's
    MOT risk assessment based on the vehicle's make, model, and age.

    Returns:
        Combined DVLA vehicle data and MOT risk assessment.
    """
    global _dvla_client

    # Initialize DVLA client on-demand if not already initialized (for testing without lifespan)
    if _dvla_client is None:
        _dvla_client = DVLAClient(api_key=DVLA_API_KEY, use_test_env=DVLA_USE_TEST_ENV)

    try:
        # Get vehicle details from DVLA
        dvla_data = await _dvla_client.get_vehicle(registration)

        # Extract vehicle info for risk lookup
        make = dvla_data.get("make", "").upper()
        year = dvla_data.get("yearOfManufacture")
        fuel_type = dvla_data.get("fuelType", "").upper()

        # Try to determine model from DVLA data
        # Note: DVLA doesn't always return model, so we may need to use make-level fallback
        model = None  # DVLA API doesn't provide model in standard response

        # Calculate age and mileage bands
        # Use current year if yearOfManufacture available
        if year:
            age = datetime.now().year - year
            age_band = get_age_band(age)
        else:
            age = None
            age_band = "Unknown"

        # Default mileage (we don't have actual mileage from DVLA)
        # Use middle of typical range for age
        estimated_mileage = 10000 * age if age else 50000
        mileage_band = get_mileage_band(estimated_mileage)

        # Build response
        response = {
            "registration": registration.upper().replace(" ", ""),
            "dvla": {
                "make": dvla_data.get("make"),
                "colour": dvla_data.get("colour"),
                "yearOfManufacture": dvla_data.get("yearOfManufacture"),
                "fuelType": dvla_data.get("fuelType"),
                "engineCapacity": dvla_data.get("engineCapacity"),
                "taxStatus": dvla_data.get("taxStatus"),
                "taxDueDate": dvla_data.get("taxDueDate"),
                "motStatus": dvla_data.get("motStatus"),
                "motExpiryDate": dvla_data.get("motExpiryDate"),
                "co2Emissions": dvla_data.get("co2Emissions"),
            },
        }

        # Add demo flag if in demo mode
        if dvla_data.get("_demo"):
            response["demo"] = True
            if dvla_data.get("_note"):
                response["demo_note"] = dvla_data["_note"]

        # Try to get risk assessment if we have enough info
        if make and year:
            try:
                # Look up risk using make-level average (since DVLA doesn't give model)
                model_id = make  # Use just make for now

                # Try PostgreSQL first
                if DATABASE_URL:
                    result = await db.get_fallback_risk(make)
                    if result:
                        response["risk"] = {
                            "Failure_Risk": result.get("Failure_Risk"),
                            "Total_Tests": result.get("Total_Tests"),
                            "level": result.get("level", "make"),
                            "note": f"Risk based on {make} average (model not available from DVLA)"
                        }

                # Try SQLite
                elif os.path.exists(DB_FILE):
                    conn = get_sqlite_connection()
                    if conn:
                        try:
                            # Get make-level average
                            cursor = conn.execute(
                                """SELECT AVG(Failure_Risk) as avg_risk, SUM(Total_Tests) as total_tests
                                   FROM risks WHERE model_id LIKE ?""",
                                (f"{make}%",)
                            )
                            row = cursor.fetchone()
                            if row and row[0] is not None:
                                response["risk"] = {
                                    "Failure_Risk": round(float(row[0]), 4),
                                    "Total_Tests": int(row[1]) if row[1] else 0,
                                    "level": "make",
                                    "note": f"Risk based on {make} average (model not available from DVLA)"
                                }
                        finally:
                            conn.close()

            except Exception as e:
                logger.warning(f"Could not calculate risk for {registration}: {e}")
                response["risk_error"] = "Could not calculate risk assessment"

        return response

    except DVLAValidationError as e:
        raise HTTPException(status_code=422, detail=str(e.message))
    except DVLANotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.message))
    except DVLARateLimitError as e:
        raise HTTPException(status_code=503, detail="DVLA service temporarily unavailable (rate limit)")
    except DVLAError as e:
        raise HTTPException(status_code=502, detail=f"DVLA service error: {e.message}")


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
            "endpoints": ["/api/makes", "/api/models", "/api/risk", "/api/vehicle"]
        }
