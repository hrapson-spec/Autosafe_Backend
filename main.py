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
from starlette.responses import StreamingResponse
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
from email_service import close_email_client

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

# Response caching for expensive queries using TTLCache with bounded size
from cachetools import TTLCache

# Cache configuration
CACHE_TTL = 3600  # 1 hour cache TTL
CACHE_MAX_SIZE = 500  # Maximum number of entries (makes + models combined)

# Bounded TTL cache - automatically evicts old entries and limits size
# This prevents unbounded memory growth from accumulating make/model entries
_cache = TTLCache(maxsize=CACHE_MAX_SIZE, ttl=CACHE_TTL)
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

    # Initialize PostgreSQL if configured (for writes: leads, risk_checks, garages)
    global DATABASE_URL
    if DATABASE_URL:
        logger.info("Initializing PostgreSQL connection pool...")
        await db.get_pool()
        if await db.is_postgres_available():
            logger.info("PostgreSQL connected successfully")
        else:
            logger.warning("PostgreSQL configured but connection failed - writes will be rejected")

    # SQLite is fallback for reads only (mot_risk lookups)
    if os.path.exists(DB_FILE):
        logger.info(f"SQLite fallback ready for reads: {DB_FILE}")
    else:
        logger.warning("No SQLite fallback available")

    # Initialize SEO landing page data (needs SQLite)
    from seo_pages import initialize_seo_data
    initialize_seo_data(get_sqlite_connection)

    # Initialize DVSA client
    dvsa_client = get_dvsa_client()
    if dvsa_client.is_configured:
        logger.info("DVSA client initialized with OAuth credentials")
    else:
        logger.error("DVSA OAuth credentials NOT configured - all V55 predictions will fall back to population averages!")
        logger.error("Set DVSA_CLIENT_ID, DVSA_CLIENT_SECRET, DVSA_TOKEN_URL, and DVSA_API_KEY to enable real predictions")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down...")
    await close_dvsa_client()
    await close_email_client()
    await db.close_pool()


app = FastAPI(title="AutoSafe API", description="MOT Risk Prediction API", lifespan=lifespan)

# CORS Middleware - Configure cross-origin requests
# In production, set CORS_ORIGINS env var to restrict (e.g., "https://autosafe.co.uk,https://www.autosafe.co.uk")
from fastapi.middleware.cors import CORSMiddleware

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
if CORS_ORIGINS == "*":
    logger.warning("CORS_ORIGINS not set - defaulting to wildcard '*'. Set CORS_ORIGINS env var in production.")
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


# Non-WWW to WWW Redirect Middleware
from starlette.responses import RedirectResponse

@app.middleware("http")
async def redirect_non_www(request, call_next):
    """Redirect autosafe.one to www.autosafe.one for SEO canonicalization."""
    host = request.headers.get("host", "")
    if host == "autosafe.one":
        url = request.url.replace(scheme="https")
        new_url = str(url).replace("://autosafe.one", "://www.autosafe.one", 1)
        return RedirectResponse(url=new_url, status_code=301)
    return await call_next(request)


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
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://esm.sh https://umami-production-cb51.up.railway.app https://www.googletagmanager.com https://www.google-analytics.com https://googleads.g.doubleclick.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://www.googletagmanager.com https://www.google-analytics.com https://www.google.com https://googleads.g.doubleclick.net https://www.google.co.uk https://www.googleadservices.com; connect-src 'self' https://esm.sh https://umami-production-cb51.up.railway.app https://www.google-analytics.com https://region1.google-analytics.com https://www.google.com; frame-src https://www.googletagmanager.com"
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
DB_FILE = '/tmp/autosafe.db'
DATABASE_URL = os.environ.get("DATABASE_URL")

# Minimum total tests required for a make/model to appear in UI dropdowns
# This filters out typos, garbage entries, and extremely rare vehicles
# Configurable via environment variable for tuning without redeployment
MIN_TESTS_FOR_UI = int(os.environ.get("MIN_TESTS_FOR_UI", "100"))

# Rate Limiting Setup
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request


def get_real_client_ip(request: Request) -> str:
    """
    Extract the real client IP address from the request.

    When behind a trusted reverse proxy (Railway, Cloudflare, AWS ALB),
    we take the FIRST IP from X-Forwarded-For, which is the original client.
    Subsequent IPs are added by each proxy in the chain.

    An attacker can add fake IPs to the END of X-Forwarded-For, but cannot
    control the FIRST entry when behind a properly configured reverse proxy.

    Falls back to direct client IP if no X-Forwarded-For header is present.
    """
    # Check for X-Forwarded-For header (set by reverse proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP (leftmost) - this is the original client IP
        # Format: "client, proxy1, proxy2, ..."
        client_ip = forwarded_for.split(",")[0].strip()
        # Basic validation - ensure it looks like an IP
        if client_ip and ("." in client_ip or ":" in client_ip):
            return client_ip

    # Fallback to direct client connection
    if request.client and request.client.host:
        return request.client.host

    return "unknown"


limiter = Limiter(key_func=get_real_client_ip)
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
async def health_check(request: Request):
    """
    Liveness check with component status.
    Always returns 200 if the app is up (for container orchestration).
    Detailed diagnostics require X-API-Key header matching ADMIN_API_KEY.
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

    # Overall status
    overall_status = "ok" if model_status == "loaded" else "degraded"

    # Minimal response for unauthenticated requests
    response = {
        "status": overall_status,
        "timestamp": datetime.now().isoformat(),
    }

    # Detailed diagnostics only with admin API key
    api_key = request.headers.get("X-API-Key")
    if _verify_admin_api_key(api_key):
        dvsa_client = get_dvsa_client()
        dvsa_diag = dvsa_client.get_diagnostic_status()
        dvsa_status = "fully_configured" if dvsa_diag["is_configured"] else "missing_credentials"
        dvla_status = "configured" if DVLA_API_KEY else "demo_mode"

        response.update({
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
        })

    return response


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

# SQLite fallback connection pool (for local development)
import sqlite3
from queue import Queue, Empty
from contextlib import contextmanager
import threading

# SQLite connection pool configuration
SQLITE_POOL_SIZE = 5
SQLITE_POOL_TIMEOUT = 5.0  # seconds to wait for a connection

# Global connection pool
_sqlite_pool: Optional[Queue] = None
_sqlite_pool_lock = threading.Lock()


def _init_sqlite_pool():
    """Initialize the SQLite connection pool."""
    global _sqlite_pool
    # Acquire lock first to prevent race condition in double-checked locking
    with _sqlite_pool_lock:
        if _sqlite_pool is not None:
            return

        if not os.path.exists(DB_FILE):
            logger.warning(f"SQLite database not found: {DB_FILE}")
            return

        _sqlite_pool = Queue(maxsize=SQLITE_POOL_SIZE)
        for _ in range(SQLITE_POOL_SIZE):
            try:
                conn = sqlite3.connect(DB_FILE, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # Test the connection
                conn.execute("SELECT 1 FROM risks LIMIT 1")
                _sqlite_pool.put(conn)
            except sqlite3.Error as e:
                logger.error(f"Failed to create SQLite connection: {e}")

        logger.info(f"SQLite connection pool initialized with {_sqlite_pool.qsize()} connections")


@contextmanager
def get_sqlite_connection():
    """
    Get a SQLite connection from the pool.

    Usage:
        with get_sqlite_connection() as conn:
            if conn:
                result = conn.execute("SELECT ...").fetchone()

    Returns connection to pool automatically when done.
    """
    # Initialize pool on first use
    if _sqlite_pool is None:
        _init_sqlite_pool()

    if _sqlite_pool is None:
        yield None
        return

    # Don't check qsize() - it's a race condition (TOCTOU bug).
    # Instead, rely on get() with timeout to handle empty pool.
    conn = None
    try:
        conn = _sqlite_pool.get(timeout=SQLITE_POOL_TIMEOUT)
        yield conn
    except Empty:
        logger.warning("SQLite connection pool exhausted, timeout waiting for connection")
        yield None
    except Exception as e:
        logger.error(f"Error getting SQLite connection: {e}")
        yield None
    finally:
        if conn is not None:
            try:
                # Return connection to pool
                _sqlite_pool.put_nowait(conn)
            except Exception:
                # Pool is full or connection is bad, close it
                try:
                    conn.close()
                except Exception:
                    pass


def get_sqlite_connection_direct():
    """Get SQLite connection directly (legacy compatibility, prefer context manager)."""
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
    cache_key = "makes"

    # Check cache first (TTLCache handles expiration automatically)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info("Returning cached makes list")
        return cached

    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_makes()
        if result is not None:
            _cache[cache_key] = result
            logger.info(f"Cached {len(result)} makes from PostgreSQL")
            return result

    # Fallback to SQLite
    with get_sqlite_connection() as conn:
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
            makes = sorted(set(row['make'] for row in rows))
            _cache[cache_key] = makes
            logger.info(f"Cached {len(makes)} makes from SQLite")
            return makes

    # Demo mode
    return sorted(MOCK_MAKES)


@app.get("/api/models", response_model=List[str])
@limiter.limit("100/minute")
async def get_models(request: Request, make: str = Query(..., description="Vehicle Make (e.g., FORD)")):
    """Return a list of models for a given make (cached for 1 hour)."""
    # Use prefixed cache key to differentiate from makes cache
    cache_key = f"models:{make.upper()}"

    # Check cache first (TTLCache handles expiration automatically)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info(f"Returning cached models for {make.upper()}")
        return cached

    # Try PostgreSQL first
    if DATABASE_URL:
        result = await db.get_models(make)
        if result is not None:
            _cache[cache_key] = result
            logger.info(f"Cached {len(result)} models for {make.upper()} from PostgreSQL")
            return result

    # Fallback to SQLite
    with get_sqlite_connection() as conn:
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

            _cache[cache_key] = result
            logger.info(f"Cached {len(result)} models for {make.upper()} from SQLite")
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
    with get_sqlite_connection() as conn:
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
                return default_response

            result = dict(row)

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
    # Get DVSA client (needed for VRM validation)
    dvsa_client = get_dvsa_client()

    # Validate and normalize VRM FIRST - return 400 for invalid input
    # before checking service availability (503)
    try:
        vrm = dvsa_client.normalize_vrm(registration)
    except VRMValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

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

    # Build response
    response = {
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

    # Log risk check for model training data (fire-and-forget, don't block response)
    try:
        await db.save_risk_check({
            'registration': vrm,
            'postcode': validated_postcode,
            'vehicle_make': history.make,
            'vehicle_model': history.model,
            'vehicle_year': year,
            'vehicle_fuel_type': history.fuel_type,
            'mileage': last_test.odometer_value if last_test else None,
            'last_mot_date': last_test.test_date if last_test else None,
            'last_mot_result': last_test.test_result if last_test else None,
            'failure_risk': prediction['failure_risk'],
            'confidence_level': prediction['confidence_level'],
            'risk_components': prediction['risk_components'],
            'repair_cost_estimate': repair_cost,
            'model_version': 'v55',
            'prediction_source': 'dvsa',
            'is_dvsa_data': True,
        })
    except Exception as e:
        logger.warning(f"Failed to log risk check: {e}")

    return response


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
        response = {
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
        # Log fallback prediction
        try:
            await db.save_risk_check({
                'registration': registration,
                'postcode': postcode,
                'vehicle_make': None,
                'vehicle_model': None,
                'vehicle_year': year,
                'failure_risk': 0.28,
                'confidence_level': 'Low',
                'risk_components': response['risk_components'],
                'repair_cost_estimate': response['repair_cost_estimate'],
                'model_version': 'lookup',
                'prediction_source': 'fallback',
                'is_dvsa_data': False,
            })
        except Exception as e:
            logger.warning(f"Failed to log fallback risk check: {e}")
        return response

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
    with get_sqlite_connection() as conn:
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

                response = {
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
                        "lamps": result.get('Risk_Lamps_Reflectors_And_Electrical_Equipment', 0.03),
                        "body": result.get('Risk_Body_Chassis_Structure', 0.02),
                    },
                    "repair_cost_estimate": result.get('Repair_Cost_Estimate'),
                    "model_version": "lookup",
                    "note": note,
                }
                # Log lookup prediction
                try:
                    await db.save_risk_check({
                        'registration': registration,
                        'postcode': postcode,
                        'vehicle_make': make.upper(),
                        'vehicle_model': model.upper(),
                        'vehicle_year': year,
                        'failure_risk': response['failure_risk'],
                        'confidence_level': 'Medium',
                        'risk_components': response['risk_components'],
                        'repair_cost_estimate': response['repair_cost_estimate'],
                        'model_version': 'lookup',
                        'prediction_source': 'lookup',
                        'is_dvsa_data': False,
                    })
                except Exception as e:
                    logger.warning(f"Failed to log lookup risk check: {e}")
                return response

    # Default fallback - population average
    response = {
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
    # Log default fallback prediction
    try:
        await db.save_risk_check({
            'registration': registration,
            'postcode': postcode,
            'vehicle_make': make.upper() if make else None,
            'vehicle_model': model.upper() if model else None,
            'vehicle_year': year,
            'failure_risk': 0.28,
            'confidence_level': 'Low',
            'risk_components': response['risk_components'],
            'repair_cost_estimate': response['repair_cost_estimate'],
            'model_version': 'lookup',
            'prediction_source': 'fallback',
            'is_dvsa_data': False,
        })
    except Exception as e:
        logger.warning(f"Failed to log default fallback risk check: {e}")
    return response


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


# Demo vehicle data for when DVSA/DVLA is not configured
DEMO_VEHICLES = {
    "AB12CDE": {"make": "FORD", "model": "FIESTA", "yearOfManufacture": 2012, "fuelType": "PETROL", "colour": "BLUE"},
    "CD34EFG": {"make": "VAUXHALL", "model": "CORSA", "yearOfManufacture": 2015, "fuelType": "PETROL", "colour": "RED"},
    "EF56GHI": {"make": "BMW", "model": "3 SERIES", "yearOfManufacture": 2018, "fuelType": "DIESEL", "colour": "BLACK"},
}


@app.get("/api/vehicle")
@limiter.limit("10/minute")  # P1-1 fix: Add rate limiting to prevent enumeration
async def get_vehicle(
    request: Request,
    registration: str = Query(..., min_length=2, max_length=8, description="UK vehicle registration number")
):
    """Look up vehicle details by registration number.

    Returns make, model, year, fuel type from DVLA/DVSA data.
    Rate limited to prevent enumeration attacks.
    Falls back to demo data when DVSA not configured.
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
                "dvla": {
                    "make": history.make,
                    "model": history.model,
                    "yearOfManufacture": year,
                    "fuelType": history.fuel_type,
                    "colour": history.colour,
                },
                "source": "dvsa"
            }
        except VehicleNotFoundError:
            logger.info(f"Vehicle {vrm_hash} not found in DVSA")
        except DVSAAPIError as e:
            logger.warning(f"DVSA API error for {vrm_hash}: {e}")
    else:
        # Demo mode: return sample data when DVSA not configured
        logger.info(f"DVSA not configured, returning demo data for {vrm_hash}")
        demo_data = DEMO_VEHICLES.get(vrm, {
            "make": "DEMO",
            "model": "VEHICLE",
            "yearOfManufacture": 2020,
            "fuelType": "PETROL",
            "colour": "SILVER"
        })
        return {
            "registration": vrm,
            "dvla": demo_data,
            "source": "demo",
            "demo": True
        }

    # Vehicle not found in DVSA
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

    @field_validator('mileage')
    @classmethod
    def validate_mileage(cls, v):
        """Validate mileage is within reasonable bounds (0 to 1,000,000 miles)."""
        if v is None:
            return v
        if v < 0:
            raise ValueError('Mileage cannot be negative')
        if v > 1_000_000:
            raise ValueError('Mileage cannot exceed 1,000,000 miles')
        return v

    @field_validator('year')
    @classmethod
    def validate_year(cls, v):
        """Validate year is within reasonable bounds."""
        if v is None:
            return v
        current_year = datetime.now().year
        if v < 1900:
            raise ValueError('Year cannot be before 1900')
        if v > current_year + 1:
            raise ValueError(f'Year cannot be greater than {current_year + 1}')
        return v


class RiskData(BaseModel):
    failure_risk: Optional[float] = None
    reliability_score: Optional[int] = None
    top_risks: Optional[List[str]] = None

    @field_validator('failure_risk')
    @classmethod
    def validate_failure_risk(cls, v):
        """Validate failure_risk is a valid probability (0 to 1)."""
        if v is None:
            return v
        if v < 0.0 or v > 1.0:
            raise ValueError('failure_risk must be between 0 and 1')
        return v

    @field_validator('reliability_score')
    @classmethod
    def validate_reliability_score(cls, v):
        """Validate reliability_score is between 0 and 100."""
        if v is None:
            return v
        if v < 0 or v > 100:
            raise ValueError('reliability_score must be between 0 and 100')
        return v


class LeadSubmission(BaseModel):
    email: str
    postcode: str
    name: Optional[str] = None
    phone: Optional[str] = None
    lead_type: str = "garage"
    services_requested: Optional[List[str]] = None
    vehicle: Optional[VehicleInfo] = None
    risk_data: Optional[RiskData] = None

    @field_validator('name')
    @classmethod
    def sanitize_name(cls, v):
        """Sanitize name field to prevent XSS (defense-in-depth with Jinja2 escaping)."""
        if v is None:
            return v
        # Use bleach to safely strip all HTML tags (more robust than regex)
        import bleach
        v = bleach.clean(v, tags=[], strip=True)
        # Limit length to prevent abuse
        v = v.strip()[:100]
        return v if v else None

    @field_validator('phone')
    @classmethod
    def sanitize_phone(cls, v):
        """Sanitize phone field - keep only digits, spaces, and common phone chars."""
        if v is None:
            return v
        import re
        # Keep only digits, spaces, +, -, (, )
        v = re.sub(r'[^\d\s+\-()]', '', v)
        v = v.strip()[:20]  # Limit length
        return v if v else None

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
if ADMIN_API_KEY:
    logger.info(f"ADMIN_API_KEY loaded ({len(ADMIN_API_KEY)} chars)")
else:
    logger.warning("ADMIN_API_KEY not set - admin endpoints will be inaccessible")


def _verify_admin_api_key(api_key: Optional[str]) -> bool:
    """
    Verify admin API key using constant-time comparison.

    Reads ADMIN_API_KEY from environment at call time (not module load)
    so that Railway env var changes take effect without redeployment.
    """
    admin_key = os.environ.get("ADMIN_API_KEY") or ADMIN_API_KEY
    if not admin_key or not api_key:
        return False
    return secrets.compare_digest(api_key, admin_key)


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


@app.get("/api/admin/export-risk-checks")
@limiter.limit("5/minute")
async def export_risk_checks(
    request: Request,
    since: Optional[str] = Query(None, description="ISO date string (e.g. 2026-01-23)"),
    format: str = Query("csv", description="Export format: csv or json")
):
    """Export risk_checks as CSV or JSON (admin only)."""
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    pool = await db.get_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        async with pool.acquire() as conn:
            if since:
                rows = await conn.fetch(
                    """SELECT created_at, registration, postcode, vehicle_make, vehicle_model,
                              vehicle_year, vehicle_fuel_type, mileage, last_mot_date, last_mot_result,
                              failure_risk, confidence_level, model_version, prediction_source
                       FROM risk_checks WHERE created_at >= $1::timestamp
                       ORDER BY created_at DESC""",
                    since
                )
            else:
                rows = await conn.fetch(
                    """SELECT created_at, registration, postcode, vehicle_make, vehicle_model,
                              vehicle_year, vehicle_fuel_type, mileage, last_mot_date, last_mot_result,
                              failure_risk, confidence_level, model_version, prediction_source
                       FROM risk_checks ORDER BY created_at DESC"""
                )

            if format == "json":
                data = []
                for row in rows:
                    data.append({
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "registration": row["registration"],
                        "postcode": row["postcode"],
                        "vehicle_make": row["vehicle_make"],
                        "vehicle_model": row["vehicle_model"],
                        "vehicle_year": row["vehicle_year"],
                        "fuel_type": row["vehicle_fuel_type"],
                        "mileage": row["mileage"],
                        "last_mot_date": str(row["last_mot_date"]) if row["last_mot_date"] else None,
                        "last_mot_result": row["last_mot_result"],
                        "failure_risk": float(row["failure_risk"]) if row["failure_risk"] else None,
                        "confidence_level": row["confidence_level"],
                        "model_version": row["model_version"],
                        "prediction_source": row["prediction_source"],
                    })
                return {"count": len(data), "risk_checks": data}

            # CSV format
            import io, csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "created_at", "registration", "postcode", "vehicle_make", "vehicle_model",
                "vehicle_year", "fuel_type", "mileage", "last_mot_date", "last_mot_result",
                "failure_risk", "confidence_level", "model_version", "prediction_source"
            ])
            for row in rows:
                writer.writerow([
                    row["created_at"].isoformat() if row["created_at"] else "",
                    row["registration"], row["postcode"],
                    row["vehicle_make"], row["vehicle_model"],
                    row["vehicle_year"], row["vehicle_fuel_type"],
                    row["mileage"],
                    str(row["last_mot_date"]) if row["last_mot_date"] else "",
                    row["last_mot_result"],
                    float(row["failure_risk"]) if row["failure_risk"] else "",
                    row["confidence_level"], row["model_version"], row["prediction_source"]
                ])

            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=risk_checks_export.csv"}
            )

    except Exception as e:
        logger.error(f"Export risk checks failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Debug endpoint for testing risk_checks save (admin-only)
@app.get("/api/admin/test-risk-check")
@limiter.limit("5/minute")
async def test_risk_check(request: Request):
    """Test endpoint to debug risk_checks saving (admin only)."""
    api_key = request.headers.get("X-API-Key")
    if not _verify_admin_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    from datetime import date

    pool = await db.get_pool()
    if not pool:
        return {"error": "No database pool"}

    try:
        async with pool.acquire() as conn:
            count_before = await conn.fetchval("SELECT COUNT(*) FROM risk_checks")

        result = await db.save_risk_check({
            'registration': 'DEBUG123',
            'postcode': 'SW1A1AA',
            'vehicle_make': 'TEST',
            'vehicle_model': 'DEBUG',
            'vehicle_year': 2020,
            'vehicle_fuel_type': 'Petrol',
            'mileage': 10000,
            'last_mot_date': date(2024, 1, 15),
            'last_mot_result': 'PASSED',
            'failure_risk': 0.25,
            'confidence_level': 'High',
            'risk_components': {'brakes': 0.05},
            'repair_cost_estimate': {'min': 50, 'max': 200},
            'model_version': 'v55',
            'prediction_source': 'debug',
            'is_dvsa_data': False,
        })

        async with pool.acquire() as conn:
            count_after = await conn.fetchval("SELECT COUNT(*) FROM risk_checks")

        return {
            "success": True,
            "risk_check_id": result,
            "count_before": count_before,
            "count_after": count_after
        }
    except Exception as e:
        logger.error(f"Test risk check failed: {type(e).__name__}: {e}")
        return {"error": str(e), "type": type(e).__name__}


# Mount static files (only if the folder exists)
if os.path.isdir("static"):
    # Mount assets at root /assets for React build compatibility
    if os.path.isdir("static/assets"):
        app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Register SEO pages (must be before SPA catch-all)
from seo_pages import register_seo_routes
register_seo_routes(app, get_sqlite_connection)

if os.path.isdir("static"):
    @app.get("/")
    async def read_index():
        return FileResponse('static/index.html')

    # Catch-all route for SPA client-side routing (must be after API routes and SEO routes)
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        # Don't serve index.html for API routes, static files, or SEO pages
        if path.startswith(("api/", "static/", "mot-check")):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
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
