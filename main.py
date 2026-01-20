"""
AutoSafe API - MOT Risk Prediction (V55)
=========================================

Uses V55 CatBoost model with DVSA MOT History API for real-time predictions.
Falls back to SQLite lookup for vehicles without DVSA history.
"""
# Load environment variables FIRST (before other imports read them)
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional
from datetime import datetime
from cachetools import TTLCache
import os
import time
import uuid

# Import database module for fallback
import database as db
from utils import get_age_band, get_mileage_band
from confidence import wilson_interval, classify_confidence
from consolidate_models import extract_base_model
from repair_costs import calculate_expected_repair_cost
from regional_defaults import validate_postcode, get_corrosion_index

# Lead distribution
from lead_distributor import distribute_lead

# Security utilities
from security import (
    generate_outcome_token, verify_outcome_token,
    generate_request_id, audit_log, get_actor_from_api_key,
    validate_base_url, sanitize_error_message
)

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

# Response caching for expensive queries (bounded to prevent memory exhaustion)
# Thread-safe: TTLCache is not thread-safe, so we use a lock for concurrent access
import threading
CACHE_TTL = 3600  # 1 hour cache TTL
MAX_CACHE_ENTRIES = 500  # Maximum cached entries
_makes_cache: TTLCache = TTLCache(maxsize=1, ttl=CACHE_TTL)
_models_cache: TTLCache = TTLCache(maxsize=MAX_CACHE_ENTRIES, ttl=CACHE_TTL)
_cache_lock = threading.Lock()  # Protects both caches from concurrent access
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
# Configure allowed origins via CORS_ORIGINS env var (comma-separated)
# Defaults to localhost for development; set explicitly in production
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000")
allowed_origins = [origin.strip() for origin in CORS_ORIGINS.split(",") if origin.strip()]

# Validate and set BASE_URL for email links
BASE_URL = validate_base_url(os.environ.get("BASE_URL")) or "https://autosafe.co.uk"
if BASE_URL and BASE_URL not in allowed_origins:
    allowed_origins.append(BASE_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Request-ID"],
)


# HTTPS Redirect Middleware (for production behind TLS-terminating proxy)
from starlette.responses import RedirectResponse


@app.middleware("http")
async def https_redirect(request, call_next):
    """
    Redirect HTTP requests to HTTPS in production.

    Railway (and most cloud platforms) terminate TLS at the load balancer
    and forward requests to the app over HTTP. They set X-Forwarded-Proto
    to indicate the original protocol used by the client.

    This middleware redirects HTTP requests to HTTPS, except:
    - Health check endpoints (for load balancer probes)
    - Localhost/development environments
    """
    # Skip redirect for localhost/development
    host = request.headers.get("host", "")
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return await call_next(request)

    # Skip redirect for health checks (load balancer probes use HTTP)
    if request.url.path == "/health":
        return await call_next(request)

    # Check X-Forwarded-Proto (set by Railway/proxy)
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "https")

    if forwarded_proto == "http":
        # Build HTTPS URL
        https_url = request.url.replace(scheme="https")
        return RedirectResponse(url=str(https_url), status_code=301)

    return await call_next(request)


# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)

    # Prevent MIME sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Clickjacking protection
    response.headers["X-Frame-Options"] = "DENY"

    # XSS protection (legacy browsers)
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # HTTPS enforcement (Railway handles TLS, but we set HSTS for clients)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Content Security Policy
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )

    # Referrer policy - stricter for endpoints with tokens to prevent leakage
    if "/outcome/" in str(request.url.path) or "token=" in str(request.url.query):
        # No referrer for pages with sensitive tokens to prevent leakage
        response.headers["Referrer-Policy"] = "no-referrer"
    else:
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Permissions policy (disable unnecessary browser features)
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    return response


# Request ID Middleware for tracing
@app.middleware("http")
async def add_request_id(request, call_next):
    """Add unique request ID for tracing."""
    request_id = request.headers.get("X-Request-ID") or generate_request_id()
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    return response

# Check for PostgreSQL first, then SQLite
# OPTIMIZATION: Prefer local built-on-start SQLite DB if available (faster, fresher data)
DB_FILE = 'autosafe.db'
DATABASE_URL = os.environ.get("DATABASE_URL")

# Minimum total tests required for a make/model to appear in UI dropdowns
# This filters out typos, garbage entries, and extremely rare vehicles
MIN_TESTS_FOR_UI = 100

# Rate Limiting Setup
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address from request, handling proxies properly.

    Priority:
    1. X-Forwarded-For (first IP in chain - the original client)
    2. X-Real-IP (set by some proxies)
    3. Direct client address

    Railway (our hosting platform) sets X-Forwarded-For automatically.
    """
    # X-Forwarded-For contains: client, proxy1, proxy2, ...
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP (original client) - trim whitespace
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip

    # Fallback to X-Real-IP
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Direct connection (no proxy)
    if request.client:
        return request.client.host

    return "unknown"


limiter = Limiter(key_func=get_client_ip)
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
    # Check bounded TTL cache first (thread-safe access)
    with _cache_lock:
        if "makes" in _makes_cache:
            logger.info("Returning cached makes list")
            return _makes_cache["makes"]

    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_makes()
        if result is not None:
            with _cache_lock:
                _makes_cache["makes"] = result
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
        with _cache_lock:
            _makes_cache["makes"] = makes
        logger.info(f"Cached {len(makes)} makes from SQLite")
        return makes

    # Demo mode
    return sorted(MOCK_MAKES)


@app.get("/api/models", response_model=List[str])
@limiter.limit("100/minute")
async def get_models(request: Request, make: str = Query(..., description="Vehicle Make (e.g., FORD)")):
    """Return a list of models for a given make (cached for 1 hour)."""
    cache_key = make.upper()

    # Check bounded TTL cache first (thread-safe access)
    with _cache_lock:
        if cache_key in _models_cache:
            logger.info(f"Returning cached models for {cache_key}")
            return _models_cache[cache_key]

    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_models(make)
        if result is not None:
            with _cache_lock:
                _models_cache[cache_key] = result
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

        with _cache_lock:
            _models_cache[cache_key] = result
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
    if not dvsa_client.is_configured:
        logger.warning("DVSA not configured, falling back to lookup")
        return await _fallback_prediction(
            registration=vrm,
            make="",
            model="",
            year=None,
            postcode=postcode,
            note="DVSA integration not configured"
        )

    # Fetch MOT history from DVSA
    try:
        history = await dvsa_client.fetch_vehicle_history(vrm)
        logger.info(f"Fetched DVSA history for {vrm}: {history.make} {history.model}")

    except VehicleNotFoundError:
        logger.info(f"Vehicle {vrm} not found in DVSA, falling back to lookup")
        return await _fallback_prediction(
            registration=vrm,
            make="",
            model="",
            year=None,
            postcode=postcode,
            note="Vehicle not found in DVSA database"
        )

    except DVSAAPIError as e:
        logger.warning(f"DVSA API error for {vrm}: {e}, falling back to lookup")
        return await _fallback_prediction(
            registration=vrm,
            make="",
            model="",
            year=None,
            postcode=postcode,
            note=f"DVSA API unavailable: {str(e)}"
        )

    # Engineer features from MOT history
    try:
        features = engineer_features(history, postcode)
        logger.info(f"Engineered {len(features)} features for {vrm}")
    except Exception as e:
        logger.error(f"Feature engineering failed for {vrm}: {e}")
        # Fall back to lookup with vehicle info from DVSA
        year = history.manufacture_date.year if history.manufacture_date else None
        return await _fallback_prediction(
            registration=vrm,
            make=history.make,
            model=history.model,
            year=year,
            postcode=postcode,
            note=f"Feature engineering error: {str(e)}"
        )

    # Get V55 model prediction
    try:
        prediction = model_v55.predict_risk(features)
    except Exception as e:
        logger.error(f"V55 prediction failed for {vrm}: {e}")
        year = history.manufacture_date.year if history.manufacture_date else None
        return await _fallback_prediction(
            registration=vrm,
            make=history.make,
            model=history.model,
            year=year,
            postcode=postcode,
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

    # Scale by overall failure probability
    expected = expected * (failure_risk / 0.28)  # Normalize to average fail rate

    return {
        "expected": int(round(expected, -1)),  # Round to nearest 10
        "range_low": int(round(expected * 0.5, -1)),
        "range_high": int(round(expected * 2, -1)),
    }


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
        if len(v) > 254:  # RFC 5321 limit
            raise ValueError('Email address too long')
        return v.lower().strip()

    @field_validator('postcode')
    @classmethod
    def validate_postcode(cls, v):
        if not v or len(v.strip()) < 3:
            raise ValueError('Postcode must be at least 3 characters')
        if len(v.strip()) > 10:
            raise ValueError('Postcode too long')
        return v.upper().strip()

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is not None:
            v = v.strip()
            if len(v) > 100:
                raise ValueError('Name must be 100 characters or less')
            if len(v) < 1:
                return None
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None:
            # Remove common formatting characters
            v = re.sub(r'[\s\-\(\)\.]', '', v.strip())
            if len(v) > 20:
                raise ValueError('Phone number too long')
            if len(v) < 7:
                raise ValueError('Phone number too short')
            # Must be mostly digits (allow + at start)
            if not re.match(r'^\+?[\d]+$', v):
                raise ValueError('Invalid phone number format')
        return v


@app.post("/api/leads", status_code=201)
@limiter.limit("10/minute")
async def submit_lead(request: Request, lead: LeadSubmission):
    """
    Submit a lead for garage matching.

    Rate limited to 10 requests per minute per IP.
    """
    # Convert Pydantic models to dicts
    lead_data = {
        "email": lead.email,
        "postcode": lead.postcode,
        "name": lead.name,
        "phone": lead.phone,
        "lead_type": lead.lead_type,
        "vehicle": lead.vehicle.model_dump() if lead.vehicle else {},
        "risk_data": lead.risk_data.model_dump() if lead.risk_data else {}
    }

    # Save to database
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


@app.get("/api/leads")
@limiter.limit("30/minute")
async def get_leads(
    request: Request,
    limit: int = Query(50, ge=1, le=100, description="Max leads to return (max 100)"),
    offset: int = Query(0, ge=0, le=10000, description="Number of leads to skip (max 10000)"),
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

    if not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key"
        )

    # Audit log the access
    audit_log.log(
        action="read",
        actor=get_actor_from_api_key(api_key),
        resource_type="leads",
        details={"limit": limit, "offset": offset},
        request_id=getattr(request.state, 'request_id', None),
        ip_address=request.client.host if request.client else None
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
@limiter.limit("20/minute")
async def create_garage(request: Request, garage: GarageSubmission):
    """
    Create a new garage (admin only).

    Requires X-API-Key header matching ADMIN_API_KEY.
    """
    api_key = request.headers.get("X-API-Key")
    if not ADMIN_API_KEY or not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Audit log
    audit_log.log(
        action="create",
        actor=get_actor_from_api_key(api_key),
        resource_type="garage",
        details={"name": garage.name, "postcode": garage.postcode},
        request_id=getattr(request.state, 'request_id', None),
        ip_address=request.client.host if request.client else None
    )

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
    if not ADMIN_API_KEY or not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    audit_log.log(
        action="list",
        actor=get_actor_from_api_key(api_key),
        resource_type="garages",
        details={"status_filter": status},
        request_id=getattr(request.state, 'request_id', None),
        ip_address=request.client.host if request.client else None
    )

    garages = await db.get_all_garages(status=status)

    return {
        "garages": garages,
        "count": len(garages)
    }


@app.get("/api/admin/garages/{garage_id}")
@limiter.limit("60/minute")
async def get_garage(request: Request, garage_id: str):
    """
    Get a single garage by ID (admin only).
    """
    api_key = request.headers.get("X-API-Key")
    if not ADMIN_API_KEY or not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    audit_log.log(
        action="read",
        actor=get_actor_from_api_key(api_key),
        resource_type="garage",
        resource_id=garage_id,
        request_id=getattr(request.state, 'request_id', None),
        ip_address=request.client.host if request.client else None
    )

    garage = await db.get_garage_by_id(garage_id)

    if not garage:
        raise HTTPException(status_code=404, detail="Garage not found")

    return garage


@app.patch("/api/admin/garages/{garage_id}")
@limiter.limit("20/minute")
async def update_garage(request: Request, garage_id: str):
    """
    Update a garage (admin only).
    """
    api_key = request.headers.get("X-API-Key")
    if not ADMIN_API_KEY or not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    body = await request.json()

    audit_log.log(
        action="update",
        actor=get_actor_from_api_key(api_key),
        resource_type="garage",
        resource_id=garage_id,
        details={"fields_updated": list(body.keys())},
        request_id=getattr(request.state, 'request_id', None),
        ip_address=request.client.host if request.client else None
    )

    success = await db.update_garage(garage_id, body)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update garage")

    return {"success": True}


# ============================================================================
# Outcome Tracking Endpoints
# ============================================================================

@app.get("/api/garage/outcome/{assignment_id}")
@limiter.limit("30/minute")
async def get_outcome_page(
    request: Request,
    assignment_id: str,
    result: Optional[str] = None,
    token: Optional[str] = Query(None, description="Signed verification token")
):
    """
    Handle outcome reporting from email links.

    Security:
    - Requires valid signed token (48h expiry, HMAC-SHA256 signed)
    - Generic error messages prevent information disclosure
    - Response includes Referrer-Policy: no-referrer to prevent token leakage

    If result is provided, record the outcome and return confirmation.
    Otherwise, return info about the assignment.
    """
    # Verify token if provided (required for recording outcomes)
    if result and not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )

    if token:
        token_result = verify_outcome_token(token, assignment_id)
        if not token_result["valid"]:
            # Generic error message - don't reveal why validation failed
            raise HTTPException(
                status_code=401,
                detail="Authentication required"
            )

    assignment = await db.get_lead_assignment_by_id(assignment_id)

    if not assignment:
        # Generic error - don't reveal whether assignment exists
        raise HTTPException(status_code=404, detail="Not found")

    # If result provided via query param, record it
    if result in ['won', 'lost', 'no_response']:
        # Single-use token behavior: prevent overwriting existing outcomes
        if assignment.get('outcome'):
            # Already recorded - return success without revealing previous outcome
            return {
                "success": True,
                "message": "Thanks for letting us know!",
                "outcome": result,
                "vehicle": f"{assignment['vehicle_year']} {assignment['vehicle_make']} {assignment['vehicle_model']}"
            }

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
@limiter.limit("30/minute")
async def report_outcome(assignment_id: str, request: Request):
    """
    Report outcome for a lead assignment.

    Body should contain: {"outcome": "won" | "lost" | "no_response", "token": "signed_token"}
    Token is required for authentication (included in email links).
    """
    body = await request.json()
    outcome = body.get('outcome')
    token = body.get('token')

    # Verify token (required) - generic error messages for security
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )

    token_result = verify_outcome_token(token, assignment_id)
    if not token_result["valid"]:
        # Generic error - don't reveal why validation failed
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )

    assignment = await db.get_lead_assignment_by_id(assignment_id)

    if not assignment:
        # Generic error - don't reveal whether assignment exists
        raise HTTPException(status_code=404, detail="Not found")

    if outcome not in ['won', 'lost', 'no_response']:
        raise HTTPException(status_code=400, detail="Invalid outcome")

    # Single-use token behavior: prevent overwriting existing outcomes
    # Return success without changes to avoid revealing state
    if assignment.get('outcome'):
        return {
            "success": True,
            "message": "Outcome recorded. Thanks!",
            "outcome": outcome
        }

    success = await db.update_lead_assignment_outcome(assignment_id, outcome)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to record outcome")

    audit_log.log(
        action="outcome_reported",
        actor=f"garage:{assignment.get('garage_id', 'unknown')}",
        resource_type="lead_assignment",
        resource_id=assignment_id,
        details={"outcome": outcome},
        request_id=getattr(request.state, 'request_id', None),
        ip_address=request.client.host if request.client else None
    )

    return {
        "success": True,
        "message": "Outcome recorded. Thanks!",
        "outcome": outcome
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
