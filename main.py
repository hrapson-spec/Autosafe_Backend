"""
AutoSafe API - MOT Risk Prediction (V55)
=========================================

Uses V55 CatBoost model with DVSA MOT History API for real-time predictions.
Falls back to SQLite lookup for vehicles without DVSA history.
"""
# Load environment variables FIRST (before other imports read them)
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional
from datetime import datetime
import os
import time
import secrets

# Import database module for fallback
import database as db
from utils import get_age_band, get_mileage_band
from confidence import wilson_interval, classify_confidence
from consolidate_models import extract_base_model
from repair_costs import calculate_expected_repair_cost
from regional_defaults import validate_postcode, get_corrosion_index

# Lead distribution
from lead_distributor import distribute_lead

# V55 imports
from dvsa_client import (
    DVSAClient, get_dvsa_client, close_dvsa_client,
    VRMValidationError, VehicleNotFoundError, DVSAAPIError
)
from feature_engineering_v55 import engineer_features
import model_v55

import logging
import sys
import hashlib

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Kill-switch and model version configuration
# Set PREDICTIONS_ENABLED=false to disable all V55 predictions (emergency kill-switch)
# Set MODEL_VERSION to rollback to a different model version
PREDICTIONS_ENABLED = os.environ.get("PREDICTIONS_ENABLED", "true").lower() == "true"
MODEL_VERSION = os.environ.get("MODEL_VERSION", "v55")

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

# CORS Middleware - Configure cross-origin requests
# In production, set CORS_ORIGINS env var to restrict (e.g., "https://autosafe.co.uk,https://www.autosafe.co.uk")
from fastapi.middleware.cors import CORSMiddleware

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
if CORS_ORIGINS == "*":
    # Development: allow all origins but disable credentials to prevent CSRF
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # Disabled when using wildcard
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
else:
    # Production: restrict to specific origins
    # NOTE: CSRF protection is implicit because we use X-API-Key header auth,
    # not cookies. Browsers don't auto-send custom headers cross-origin.
    # If cookie-based auth is ever added, explicit CSRF tokens would be required.
    origins = [o.strip() for o in CORS_ORIGINS.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # HSTS - enable HTTPS enforcement (1 year)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # CSP - allow self, inline, CDNs, Umami analytics, and Google Ads
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://esm.sh https://umami-production-cb51.up.railway.app https://www.googletagmanager.com https://www.google-analytics.com https://googleads.g.doubleclick.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://www.googletagmanager.com https://www.google-analytics.com https://www.google.com https://googleads.g.doubleclick.net; connect-src 'self' https://esm.sh https://umami-production-cb51.up.railway.app https://www.google-analytics.com https://region1.google-analytics.com https://www.google.com; frame-src https://www.googletagmanager.com"
    return response


# Global Exception Handler - prevent stack trace leakage
from fastapi.responses import JSONResponse
import uuid as uuid_module

def generate_correlation_id() -> str:
    """Generate a short correlation ID for error tracking."""
    return uuid_module.uuid4().hex[:12]

def mask_email(email: str) -> str:
    """Mask email for logging: john@example.com -> j***@example.com"""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"

def mask_pii(text: str) -> str:
    """Mask potential PII in text for safe logging."""
    if not text:
        return text
    # Don't log anything that looks like it might contain user data
    return "[REDACTED]"

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch unhandled exceptions and return sanitized error response."""
    correlation_id = generate_correlation_id()
    # Log only the exception type and correlation ID - NOT the full exception or request data
    logger.error(f"error_id={correlation_id} path={request.url.path} exception_type={type(exc).__name__}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again later.",
            "error_id": correlation_id
        }
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


def hash_vrm(vrm: str) -> str:
    """Hash VRM for logging to protect privacy (P1-10 fix)."""
    return hashlib.sha256(vrm.encode()).hexdigest()[:8]


# Dynamic year validation - current year + 1 (P2-1 fix)
def get_max_year() -> int:
    """Get maximum valid year (current + 1 for pre-registered vehicles)."""
    return datetime.now().year + 1

@app.get("/health")
async def health_check():
    """
    Liveness check with component status.
    Always returns 200 if the app is up (for container orchestration).
    Use /ready for readiness checks that verify database connectivity.
    """
    # Database status
    db_status = "disconnected"
    if DATABASE_URL:
        try:
            pool = await db.get_pool()
            if pool:
                db_status = "connected"
        except Exception:
            db_status = "error"
    elif os.path.exists(DB_FILE):
        db_status = "sqlite"

    # Model status
    model_status = "loaded" if model_v55.is_model_loaded() else "not_loaded"

    # DVSA client status - detailed diagnostics
    dvsa_client = get_dvsa_client()
    dvsa_diag = dvsa_client.get_diagnostic_status()
    dvsa_status = "fully_configured" if dvsa_diag["is_configured"] else "missing_credentials"

    # DVLA client status
    dvla_status = "configured" if DVLA_API_KEY else "demo_mode"

    # Overall status
    overall_status = "ok" if model_status == "loaded" else "degraded"

    return {
        "status": overall_status,
        "timestamp": datetime.now().isoformat(),
        "predictions_enabled": PREDICTIONS_ENABLED,
        "model_version": MODEL_VERSION,
        "components": {
            "database": db_status,
            "model_v55": model_status,
            "dvsa_api": {
                "status": dvsa_status,
                "client_id": dvsa_diag["client_id_set"],
                "client_secret": dvsa_diag["client_secret_set"],
                "token_url": dvsa_diag["token_url_set"],
                "api_key": dvsa_diag["api_key_set"],
                "token_valid": dvsa_diag["token_valid"],
                "base_url": dvsa_diag["base_url"],
                "env_vars_found": dvsa_diag.get("env_vars_found", []),
            },
            "dvla_api": dvla_status,
        }
    }


@app.get("/ready")
async def readiness_check():
    """
    Readiness check - is the application ready to serve traffic?
    Returns 503 if PostgreSQL is unavailable.

    Use this endpoint for UptimeRobot / load balancer health checks.
    """
    # Check PostgreSQL connectivity
    db_available = await db.is_postgres_available()

    if not db_available:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "timestamp": datetime.now().isoformat(),
                "database": "disconnected",
                "message": "Database connection failed"
            }
        )

    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": "connected"
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
    
    # Demo mode - Fix: Return empty list for unknown makes instead of all mock models
    if make.upper() in ["FORD", "VAUXHALL", "VOLKSWAGEN"]:
        return MOCK_MODELS
    return []  # No demo models for other makes


@app.get("/api/risk")
@limiter.limit("20/minute")
async def get_risk(
    request: Request,
    make: str = Query(..., min_length=1, max_length=50, description="Vehicle make (e.g., FORD)"),
    model: str = Query(..., min_length=1, max_length=50, description="Vehicle model (e.g., FIESTA)"),
    year: int = Query(..., ge=1990, description="Year of manufacture")
):
    """
    Calculate MOT failure risk using lookup table.

    Interim solution using pre-computed population averages by make/model/age.
    Returns confidence level based on sample size in the lookup data.
    """
    # P2-1 fix: Dynamic year validation
    max_year = get_max_year()
    if year > max_year:
        raise HTTPException(status_code=422, detail=f"Year must be <= {max_year}")
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
        # P0-5 fix: Use exact match first, then match variants with space separator
        # This prevents "FORD F" from matching "FORD FOCUS"
        base_model_id = f"{make_upper} {model_upper}"

        # Try exact match first
        query = """
            SELECT * FROM risks
            WHERE model_id = ? AND age_band = ?
            ORDER BY Total_Tests DESC
            LIMIT 1
        """
        row = conn.execute(query, (base_model_id, age_band)).fetchone()

        # If not found, try matching model variants (e.g., "FORD FIESTA" matches "FORD FIESTA ZETEC")
        if not row:
            query = """
                SELECT * FROM risks
                WHERE (model_id = ? OR model_id LIKE ? || ' %') AND age_band = ?
                ORDER BY Total_Tests DESC
                LIMIT 1
            """
            row = conn.execute(query, (base_model_id, base_model_id, age_band)).fetchone()

        # If still not found, try just the make (for aggregated make-level data)
        if not row:
            query = """
                SELECT * FROM risks
                WHERE model_id = ? AND age_band = ?
                ORDER BY Total_Tests DESC
                LIMIT 1
            """
            row = conn.execute(query, (make_upper, age_band)).fetchone()

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
            # Fix: Use correct column names (with "And", without "Exhaust")
            "risk_lamps": result.get('Risk_Lamps_Reflectors_And_Electrical_Equipment', 0.03),
            "risk_body": result.get('Risk_Body_Chassis_Structure', 0.02),
            "repair_cost_estimate": repair_cost_formatted,
        }

    except Exception as e:
        logger.error(f"Database error during lookup: {e}")
        if conn:
            conn.close()
        return default_response


@app.get("/api/risk/v55")
@limiter.limit("20/minute")
async def get_risk_v55(
    request: Request,
    registration: str = Query(..., min_length=2, max_length=8, description="Vehicle registration mark (e.g., AB12CDE)"),
    postcode: str = Query("", max_length=10, description="UK postcode for regional factors (optional)")
):
    """
    V55 model prediction using real-time DVSA MOT history.

    Returns calibrated failure probability and component-level risks.
    Falls back to lookup table if DVSA data unavailable.
    """
    # Kill-switch check - allows emergency disabling of predictions
    if not PREDICTIONS_ENABLED:
        logger.warning(f"Predictions disabled via kill-switch, rejecting request")
        raise HTTPException(
            status_code=503,
            detail="Predictions temporarily disabled for maintenance"
        )

    # Check if model is loaded
    if not model_v55.is_model_loaded():
        raise HTTPException(
            status_code=503,
            detail="Prediction model not available"
        )

    # Get DVSA client
    dvsa_client = get_dvsa_client()

    # Validate and normalize VRM
    try:
        vrm = dvsa_client.normalize_vrm(registration)
    except VRMValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # P2-2 fix: Validate postcode format if provided
    validated_postcode = ""
    if postcode:
        postcode_result = validate_postcode(postcode)
        if postcode_result.get('valid'):
            validated_postcode = postcode_result.get('normalized', postcode)
        else:
            # Use postcode as-is for corrosion lookup (will get default value)
            validated_postcode = postcode.strip().upper()

    if not dvsa_client.is_configured:
        logger.warning(f"DVSA not configured for {hash_vrm(vrm)}, falling back to lookup")
        return await _fallback_prediction(
            registration=vrm,
            make="",
            model="",
            year=None,
            postcode=validated_postcode,
            note="DVSA integration not configured"
        )

    # Fetch MOT history from DVSA (P1-10 fix: use hashed VRM in logs)
    vrm_hash = hash_vrm(vrm)
    try:
        history = await dvsa_client.fetch_vehicle_history(vrm)
        logger.info(f"Fetched DVSA history for {vrm_hash}: {history.make} {history.model}")

    except VehicleNotFoundError:
        logger.info(f"Vehicle {vrm_hash} not found in DVSA, falling back to lookup")
        return await _fallback_prediction(
            registration=vrm,
            make="",
            model="",
            year=None,
            postcode=validated_postcode,
            note="Vehicle not found in DVSA database"
        )

    except DVSAAPIError as e:
        logger.warning(f"DVSA API error for {vrm_hash}: {e}, falling back to lookup")
        return await _fallback_prediction(
            registration=vrm,
            make="",
            model="",
            year=None,
            postcode=validated_postcode,
            note=f"DVSA API unavailable: {str(e)}"
        )

    # Engineer features from MOT history
    try:
        features = engineer_features(history, validated_postcode)
        logger.info(f"Engineered {len(features)} features for {vrm_hash}")
    except Exception as e:
        logger.error(f"Feature engineering failed for {vrm_hash}: {e}")
        # Fall back to lookup with vehicle info from DVSA
        year = history.manufacture_date.year if history.manufacture_date else None
        return await _fallback_prediction(
            registration=vrm,
            make=history.make,
            model=history.model,
            year=year,
            postcode=validated_postcode,
            note=f"Feature engineering error: {str(e)}"
        )

    # Get V55 model prediction
    try:
        prediction = model_v55.predict_risk(features)
    except Exception as e:
        logger.error(f"V55 prediction failed for {vrm_hash}: {e}")
        year = history.manufacture_date.year if history.manufacture_date else None
        return await _fallback_prediction(
            registration=vrm,
            make=history.make,
            model=history.model,
            year=year,
            postcode=validated_postcode,
            note=f"Model prediction error: {str(e)}"
        )

    # Extract vehicle info
    year = history.manufacture_date.year if history.manufacture_date else None
    last_test = history.mot_tests[0] if history.mot_tests else None

    # Calculate repair cost estimate
    repair_cost = _estimate_repair_cost(
        prediction['failure_risk'],
        prediction['risk_components']
    )

    return {
        "registration": vrm,
        "vehicle": {
            "make": history.make,
            "model": history.model,
            "year": year,
            "fuel_type": history.fuel_type,
        },
        "mileage": last_test.odometer_value if last_test else None,
        "last_mot_date": last_test.test_date.isoformat() if last_test else None,
        "last_mot_result": last_test.test_result if last_test else None,
        "failure_risk": prediction['failure_risk'],
        "confidence_level": prediction['confidence_level'],
        "risk_components": prediction['risk_components'],
        "repair_cost_estimate": repair_cost,
        "model_version": "v55",
    }


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
    Returns population average if vehicle data insufficient.
    """
    # If no vehicle data, return population average
    if not make or not model:
        return {
            "registration": registration,
            "vehicle": None,
            "year": year,
            "mileage": None,
            "last_mot_date": None,
            "last_mot_result": None,
            "failure_risk": 0.28,  # UK population average
            "confidence_level": "Low",
            "risk_components": {
                "brakes": 0.05,
                "suspension": 0.04,
                "tyres": 0.03,
                "steering": 0.02,
                "visibility": 0.02,
                "lamps": 0.03,
                "body": 0.02,
            },
            "repair_cost_estimate": {"expected": 250, "range_low": 100, "range_high": 500},
            "model_version": "lookup",
            "note": note or "Vehicle not found - using UK population average",
        }

    model_id = f"{make.upper()} {model.upper()}"

    # Calculate age band if year available (P1-3 fix: use get_age_band for consistency)
    if year:
        age = datetime.now().year - year
        age_band = get_age_band(age)
    else:
        # Default to middle of typical age range (7 years old)
        age_band = get_age_band(7)

    # P0-5 fix: Use exact match or variant match pattern instead of broad LIKE
    # Try SQLite fallback with proper connection handling (P2-4 fix)
    conn = None
    try:
        conn = get_sqlite_connection()
        if conn:
            base_model_id = f"{make.upper()} {model.upper()}"
            # Try exact match first, then variants
            query = """
                SELECT * FROM risks
                WHERE (model_id = ? OR model_id LIKE ? || ' %') AND age_band = ?
                ORDER BY Total_Tests DESC
                LIMIT 1
            """
            row = conn.execute(query, (base_model_id, base_model_id, age_band)).fetchone()

            if row:
                result = dict(row)
                result = add_confidence_intervals(result)
                result = add_repair_cost_estimate(result)

                return {
                    "registration": registration,
                    "vehicle": {"make": make.upper(), "model": model.upper(), "year": year},
                    "mileage": None,
                    "last_mot_date": None,
                    "last_mot_result": None,
                    "failure_risk": result.get('Failure_Risk', 0.28),
                    "confidence_level": "Medium",
                    "risk_components": {
                        "brakes": result.get('Risk_Brakes', 0.05),
                        "suspension": result.get('Risk_Suspension', 0.04),
                        "tyres": result.get('Risk_Tyres', 0.03),
                        "steering": result.get('Risk_Steering', 0.02),
                        "visibility": result.get('Risk_Visibility', 0.02),
                        "lamps": result.get('Risk_Lamps_Reflectors_Electrical_Equipment', 0.03),
                        "body": result.get('Risk_Body_Chassis_Structure_Exhaust', 0.02),
                    },
                    "repair_cost_estimate": result.get('Repair_Cost_Estimate'),
                    "model_version": "lookup",
                    "note": note,
                }
    finally:
        # P2-4 fix: Always close connection in finally block
        if conn:
            conn.close()

    # Default fallback - population average
    return {
        "registration": registration,
        "vehicle": {"make": make.upper(), "model": model.upper(), "year": year},
        "mileage": None,
        "last_mot_date": None,
        "last_mot_result": None,
        "failure_risk": 0.28,  # UK average
        "confidence_level": "Low",
        "risk_components": {
            "brakes": 0.05,
            "suspension": 0.04,
            "tyres": 0.03,
            "steering": 0.02,
            "visibility": 0.02,
            "lamps": 0.03,
            "body": 0.02,
        },
        "repair_cost_estimate": {"expected": 250, "range_low": 100, "range_high": 500},
        "model_version": "lookup",
        "note": note or "Limited data - using population averages",
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

    # P1-11 fix: Clamp the scaling factor to avoid extreme values
    # Scale by overall failure probability relative to average
    avg_fail_rate = 0.28
    if failure_risk > 0:
        # Clamp scaling factor between 0.5x and 3x
        scale_factor = max(0.5, min(3.0, failure_risk / avg_fail_rate))
        expected = expected * scale_factor

    # Ensure minimum reasonable cost estimate
    expected = max(expected, 100)

    return {
        "expected": int(round(expected, -1)),  # Round to nearest 10
        "range_low": int(round(max(expected * 0.5, 50), -1)),
        "range_high": int(round(expected * 2, -1)),
    }


# ============================================================================
# Vehicle Lookup Endpoint
# ============================================================================

# DVLA API key for vehicle lookup
DVLA_API_KEY = os.environ.get("DVLA_API_KEY") or os.environ.get("DVLA_Api_Key")


@app.get("/api/vehicle")
@limiter.limit("10/minute")  # P1-1 fix: Add rate limiting to prevent enumeration
async def get_vehicle(
    request: Request,
    registration: str = Query(..., min_length=2, max_length=8, description="UK vehicle registration number")
):
    """Look up vehicle details by registration number.

    Returns make, model, year, fuel type from DVLA/DVSA data.
    Rate limited to prevent enumeration attacks.
    """
    # Normalize registration
    dvsa_client = get_dvsa_client()
    try:
        vrm = dvsa_client.normalize_vrm(registration)
    except VRMValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    vrm_hash = hash_vrm(vrm)

    # Try DVSA first (has MOT history)
    if dvsa_client.is_configured:
        try:
            history = await dvsa_client.fetch_vehicle_history(vrm)
            year = history.manufacture_date.year if history.manufacture_date else None
            return {
                "registration": vrm,
                "make": history.make,
                "model": history.model,
                "year": year,
                "fuel_type": history.fuel_type,
                "colour": history.colour,
                "source": "dvsa"
            }
        except VehicleNotFoundError:
            logger.info(f"Vehicle {vrm_hash} not found in DVSA")
        except DVSAAPIError as e:
            logger.warning(f"DVSA API error for {vrm_hash}: {e}")

    # Vehicle not found
    raise HTTPException(status_code=404, detail="Vehicle not found")


# ============================================================================
# Lead Capture Endpoints
# ============================================================================

from pydantic import BaseModel, EmailStr, field_validator
import re


class VehicleInfo(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    mileage: Optional[int] = None


class RiskData(BaseModel):
    failure_risk: Optional[float] = None
    reliability_score: Optional[int] = None
    top_risks: Optional[List[str]] = None


class LeadSubmission(BaseModel):
    email: str
    postcode: str
    name: Optional[str] = None
    phone: Optional[str] = None
    lead_type: str = "garage"
    services_requested: Optional[List[str]] = None
    vehicle: Optional[VehicleInfo] = None
    risk_data: Optional[RiskData] = None

    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        # Basic email validation: must contain @ with something before and after
        if not v or '@' not in v:
            raise ValueError('Invalid email format')
        local, domain = v.rsplit('@', 1)
        if not local or not domain or '.' not in domain:
            raise ValueError('Invalid email format')
        return v.lower().strip()

    @field_validator('postcode')
    @classmethod
    def validate_postcode(cls, v):
        if not v or len(v.strip()) < 3:
            raise ValueError('Postcode must be at least 3 characters')
        return v.upper().strip()


@app.post("/api/leads", status_code=201)
@limiter.limit("10/minute")
async def submit_lead(request: Request, lead: LeadSubmission):
    """
    Submit a lead for garage matching.

    Rate limited to 10 requests per minute per IP.

    IMPORTANT: This endpoint requires PostgreSQL to be available.
    We do NOT fall back to SQLite for lead persistence to prevent data loss.
    """
    # CRITICAL: Check PostgreSQL is available before accepting lead
    # We must NOT silently fall back to SQLite for writes (data loss risk)
    if not await db.is_postgres_available():
        logger.error("Lead submission rejected: PostgreSQL unavailable")
        raise HTTPException(
            status_code=503,
            detail="Service temporarily unavailable. Please try again in a few minutes."
        )

    # Convert Pydantic models to dicts
    lead_data = {
        "email": lead.email,
        "postcode": lead.postcode,
        "name": lead.name,
        "phone": lead.phone,
        "lead_type": lead.lead_type,
        "services_requested": lead.services_requested,
        "vehicle": lead.vehicle.model_dump() if lead.vehicle else {},
        "risk_data": lead.risk_data.model_dump() if lead.risk_data else {}
    }

    # Save to database (PostgreSQL only - verified above)
    lead_id = await db.save_lead(lead_data)

    if not lead_id:
        raise HTTPException(
            status_code=500,
            detail="Failed to save lead. Please try again."
        )

    # Distribute lead to matching garages (async, don't block response)
    distribution_result = await distribute_lead(lead_id)
    logger.info(f"Lead distribution: {distribution_result}")

    return {
        "success": True,
        "lead_id": lead_id,
        "message": "Thanks! We'll be in touch soon.",
        "garages_notified": distribution_result.get("emails_sent", 0)
    }


# Admin API key for accessing leads
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")


def _verify_admin_api_key(api_key: Optional[str]) -> bool:
    """
    Verify admin API key using constant-time comparison.

    Prevents timing attacks by using secrets.compare_digest() which
    takes the same amount of time regardless of where strings differ.
    """
    if not ADMIN_API_KEY or not api_key:
        return False
    return secrets.compare_digest(api_key, ADMIN_API_KEY)


@app.get("/api/leads")
@limiter.limit("30/minute")
async def get_leads(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Max leads to return"),
    offset: int = Query(0, ge=0, description="Number of leads to skip"),
    since: Optional[str] = Query(None, description="ISO date string to filter leads after")
):
    """
    Get leads (admin only).

    Requires X-API-Key header matching ADMIN_API_KEY environment variable.
    """
    # Check API key
    api_key = request.headers.get("X-API-Key")

    if not ADMIN_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Admin access not configured"
        )

    if not _verify_admin_api_key(api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key"
        )

    # Get leads from database
    leads = await db.get_leads(limit=limit, offset=offset, since=since)

    if leads is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve leads"
        )

    total = await db.count_leads(since=since)

    return {
        "leads": leads,
        "count": len(leads),
        "total": total,
        "limit": limit,
        "offset": offset
    }


# ============================================================================
# Garage Admin Endpoints
# ============================================================================

class GarageSubmission(BaseModel):
    """Pydantic model for garage submission."""
    name: str
    email: str
    postcode: str
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = "active"
    tier: Optional[str] = "free"
    source: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/admin/garages", status_code=201)
@limiter.limit("10/minute")
async def create_garage(request: Request, garage: GarageSubmission):
    """
    Create a new garage (admin only).

    Requires X-API-Key header matching ADMIN_API_KEY.
    Requires PostgreSQL to be available (no SQLite fallback for writes).
    """
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # CRITICAL: Check PostgreSQL is available before write
    if not await db.is_postgres_available():
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    # If no coordinates provided, geocode the postcode
    lat = garage.latitude
    lng = garage.longitude
    if lat is None or lng is None:
        from postcode_service import get_postcode_coordinates
        coords = await get_postcode_coordinates(garage.postcode)
        if coords:
            lat, lng = coords

    garage_data = {
        "name": garage.name,
        "email": garage.email,
        "postcode": garage.postcode.upper(),
        "contact_name": garage.contact_name,
        "phone": garage.phone,
        "latitude": lat,
        "longitude": lng,
        "status": garage.status,
        "tier": garage.tier,
        "source": garage.source,
        "notes": garage.notes,
    }

    garage_id = await db.save_garage(garage_data)

    if not garage_id:
        raise HTTPException(status_code=500, detail="Failed to save garage")

    return {
        "success": True,
        "garage_id": garage_id,
        "coordinates_set": lat is not None
    }


@app.get("/api/admin/garages")
@limiter.limit("30/minute")
async def list_garages(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status")
):
    """
    List all garages (admin only).

    Requires X-API-Key header matching ADMIN_API_KEY.
    """
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    garages = await db.get_all_garages(status=status)

    return {
        "garages": garages,
        "count": len(garages)
    }


@app.get("/api/admin/garages/{garage_id}")
@limiter.limit("30/minute")
async def get_garage(request: Request, garage_id: str):
    """
    Get a single garage by ID (admin only).
    """
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    garage = await db.get_garage_by_id(garage_id)

    if not garage:
        raise HTTPException(status_code=404, detail="Garage not found")

    return garage


@app.patch("/api/admin/garages/{garage_id}")
@limiter.limit("10/minute")
async def update_garage(request: Request, garage_id: str):
    """
    Update a garage (admin only).
    Requires PostgreSQL to be available (no SQLite fallback for writes).
    """
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # CRITICAL: Check PostgreSQL is available before write
    if not await db.is_postgres_available():
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    body = await request.json()

    success = await db.update_garage(garage_id, body)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update garage")

    return {"success": True}


# ============================================================================
# DVSA Debug Endpoint
# ============================================================================

@app.get("/api/admin/dvsa-test")
@limiter.limit("5/minute")
async def test_dvsa_connection(
    request: Request,
    registration: str = Query("ZZ99ABC", description="Test registration (default: ZZ99ABC)")
):
    """
    Test DVSA API connectivity (admin only).

    Attempts OAuth token fetch and test lookup to diagnose issues.
    Returns detailed diagnostic information.
    """
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    dvsa_client = get_dvsa_client()
    result = {
        "timestamp": datetime.now().isoformat(),
        "test_registration": registration,
        "diagnostics": dvsa_client.get_diagnostic_status(),
        "oauth_test": None,
        "api_test": None,
    }

    # Test 1: OAuth token fetch
    if dvsa_client.is_configured:
        try:
            token = await dvsa_client._get_access_token()
            result["oauth_test"] = {
                "success": True,
                "token_length": len(token) if token else 0,
                "message": "OAuth token obtained successfully"
            }
        except Exception as e:
            result["oauth_test"] = {
                "success": False,
                "error": str(e),
                "message": "OAuth token fetch failed"
            }
    else:
        result["oauth_test"] = {
            "success": False,
            "error": "DVSA client not configured",
            "message": "Missing required credentials"
        }

    # Test 2: API lookup (only if OAuth succeeded)
    if result["oauth_test"] and result["oauth_test"]["success"]:
        try:
            vrm = dvsa_client.normalize_vrm(registration)
            history = await dvsa_client.fetch_vehicle_history(vrm)
            result["api_test"] = {
                "success": True,
                "vehicle_found": True,
                "make": history.make,
                "model": history.model,
                "mot_tests_count": len(history.mot_tests),
                "message": "DVSA API working correctly"
            }
        except VehicleNotFoundError:
            result["api_test"] = {
                "success": True,
                "vehicle_found": False,
                "message": "API responded - vehicle not found (this is OK for test VRM)"
            }
        except DVSAAPIError as e:
            result["api_test"] = {
                "success": False,
                "error": str(e),
                "message": "DVSA API call failed"
            }
        except Exception as e:
            result["api_test"] = {
                "success": False,
                "error": str(e),
                "message": "Unexpected error during API test"
            }

    return result


# ============================================================================
# Outcome Tracking Endpoints
# ============================================================================

@app.get("/api/garage/outcome/{assignment_id}")
async def get_outcome_page(assignment_id: str, result: Optional[str] = None):
    """
    Handle outcome reporting from email links.

    If result is provided, record the outcome and return confirmation.
    Otherwise, return info about the assignment.
    """
    assignment = await db.get_lead_assignment_by_id(assignment_id)

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # If result provided via query param, record it
    if result in ['won', 'lost', 'no_response']:
        # CRITICAL: Check PostgreSQL is available before write
        if not await db.is_postgres_available():
            raise HTTPException(status_code=503, detail="Database temporarily unavailable")
        success = await db.update_lead_assignment_outcome(assignment_id, result)
        if success:
            return {
                "success": True,
                "message": "Thanks for letting us know!",
                "outcome": result,
                "vehicle": f"{assignment['vehicle_year']} {assignment['vehicle_make']} {assignment['vehicle_model']}"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to record outcome")

    # Return assignment info
    return {
        "assignment_id": assignment_id,
        "garage_name": assignment['garage_name'],
        "vehicle": f"{assignment['vehicle_year']} {assignment['vehicle_make']} {assignment['vehicle_model']}",
        "outcome": assignment.get('outcome'),
        "outcome_reported_at": assignment.get('outcome_reported_at')
    }


@app.post("/api/garage/outcome/{assignment_id}")
async def report_outcome(assignment_id: str, request: Request):
    """
    Report outcome for a lead assignment.

    Body should contain: {"outcome": "won" | "lost" | "no_response"}
    Requires PostgreSQL to be available (no SQLite fallback for writes).
    """
    # CRITICAL: Check PostgreSQL is available before write
    if not await db.is_postgres_available():
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    assignment = await db.get_lead_assignment_by_id(assignment_id)

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    body = await request.json()
    outcome = body.get('outcome')

    if outcome not in ['won', 'lost', 'no_response']:
        raise HTTPException(status_code=400, detail="Invalid outcome. Must be: won, lost, or no_response")

    success = await db.update_lead_assignment_outcome(assignment_id, outcome)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to record outcome")

    return {
        "success": True,
        "message": "Outcome recorded. Thanks!",
        "outcome": outcome
    }


# Mount static files (only if the folder exists)
if os.path.isdir("static"):
    # Mount assets at root /assets for React build compatibility
    if os.path.isdir("static/assets"):
        app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    async def read_index():
        return FileResponse('static/index.html')

    # Catch-all route for SPA client-side routing (must be after API routes)
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        # Don't serve index.html for API routes or static files
        if path.startswith("api/") or path.startswith("static/"):
            return {"detail": "Not Found"}
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
