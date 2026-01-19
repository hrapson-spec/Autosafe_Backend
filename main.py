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
from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path
import os
import time
import asyncio

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
import sqlite3

# ============================================================================
# Constants - Centralized magic numbers and configuration
# ============================================================================

# Population average MOT failure rate (UK national average)
POPULATION_AVERAGE_FAILURE_RATE = 0.28

# Confidence level thresholds based on sample size
CONFIDENCE_THRESHOLD_HIGH = 1000
CONFIDENCE_THRESHOLD_MEDIUM = 100

# Minimum total tests required for a make/model to appear in UI dropdowns
# This filters out typos, garbage entries, and extremely rare vehicles
MIN_TESTS_FOR_UI = 100

# Cache TTL in seconds (1 hour)
CACHE_TTL = 3600

# Default component risk values (UK averages)
DEFAULT_COMPONENT_RISKS = {
    "brakes": 0.05,
    "suspension": 0.04,
    "tyres": 0.03,
    "steering": 0.02,
    "visibility": 0.02,
    "lamps": 0.03,
    "body": 0.02,
}

# Component repair cost estimates (GBP mid-points)
COMPONENT_REPAIR_COSTS = {
    'brakes': 200,
    'suspension': 350,
    'tyres': 150,
    'steering': 300,
    'visibility': 80,
    'lamps': 60,
    'body': 400,
}

# Default repair cost estimate
DEFAULT_REPAIR_COST = {"expected": 250, "range_low": 100, "range_high": 500}

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================================
# Response caching with thread-safe access
# ============================================================================

_cache: Dict[str, Any] = {
    "makes": {"data": None, "time": 0},
    "models": {}  # Keyed by make
}
_cache_lock = asyncio.Lock()

# ============================================================================
# Path Configuration - Use absolute paths
# ============================================================================

# Get the directory where this script is located
BASE_DIR = Path(__file__).parent.resolve()
DB_FILE = str(BASE_DIR / 'autosafe.db')
STATIC_DIR = str(BASE_DIR / 'static')

# Database URL from environment
DATABASE_URL = os.environ.get("DATABASE_URL")

# ============================================================================
# Application Lifecycle
# ============================================================================

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
    try:
        import build_db
        build_db.ensure_database()
    except Exception as e:
        logger.error(f"Failed to build fallback database: {e}")
        # Continue - PostgreSQL might still work

    # After building, check if we should use SQLite or PostgreSQL for fallback
    global DATABASE_URL
    if os.path.exists(DB_FILE):
        logger.info(f"Fallback database ready: {DB_FILE}")
        DATABASE_URL = None
    elif DATABASE_URL:
        logger.info("Initializing PostgreSQL connection pool for fallback...")
        try:
            pool = await db.get_pool()
            if pool is None:
                logger.warning("PostgreSQL pool initialization returned None")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL pool: {e}")
    else:
        logger.warning("No fallback database available")

    # Initialize DVSA client
    dvsa_client = get_dvsa_client()
    if dvsa_client and dvsa_client.is_configured:
        logger.info("DVSA client initialized with OAuth credentials")
    else:
        logger.warning("DVSA OAuth credentials not configured - V55 predictions will fail")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down...")
    await close_dvsa_client()
    await db.close_pool()


app = FastAPI(
    title="AutoSafe API",
    description="MOT Risk Prediction API",
    lifespan=lifespan,
    version="1.0.0"
)

# ============================================================================
# CORS Middleware
# ============================================================================

from fastapi.middleware.cors import CORSMiddleware

# Define allowed origins - update these for production deployment
ALLOWED_ORIGINS = [
    "https://autosafebackend-production.up.railway.app",
    "https://autosafe.co.uk",
    "https://www.autosafe.co.uk",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# ============================================================================
# Rate Limiting
# ============================================================================

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ============================================================================
# Pydantic Response Models
# ============================================================================

from pydantic import BaseModel, Field, field_validator
import re


class RepairCostEstimate(BaseModel):
    """Estimated repair costs if MOT fails."""
    expected: int = Field(..., description="Expected repair cost in GBP")
    range_low: int = Field(..., description="Low estimate in GBP")
    range_high: int = Field(..., description="High estimate in GBP")


class RiskComponents(BaseModel):
    """Component-specific failure risks."""
    brakes: float = Field(..., ge=0, le=1)
    suspension: float = Field(..., ge=0, le=1)
    tyres: float = Field(..., ge=0, le=1)
    steering: float = Field(..., ge=0, le=1)
    visibility: float = Field(..., ge=0, le=1)
    lamps: float = Field(..., ge=0, le=1)
    body: float = Field(..., ge=0, le=1)


class VehicleInfo(BaseModel):
    """Vehicle information."""
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    mileage: Optional[int] = None
    fuel_type: Optional[str] = None


class RiskResponse(BaseModel):
    """Standard risk prediction response."""
    vehicle: str
    year: Optional[int]
    mileage: Optional[int]
    last_mot_date: Optional[str]
    last_mot_result: Optional[str]
    failure_risk: float = Field(..., ge=0, le=1)
    confidence_level: str
    risk_brakes: float = Field(..., ge=0, le=1)
    risk_suspension: float = Field(..., ge=0, le=1)
    risk_tyres: float = Field(..., ge=0, le=1)
    risk_steering: float = Field(..., ge=0, le=1)
    risk_visibility: float = Field(..., ge=0, le=1)
    risk_lamps: float = Field(..., ge=0, le=1)
    risk_body: float = Field(..., ge=0, le=1)
    repair_cost_estimate: Dict[str, Any]


class V55RiskResponse(BaseModel):
    """V55 model risk prediction response."""
    registration: str
    vehicle: Optional[Dict[str, Any]]
    mileage: Optional[int]
    last_mot_date: Optional[str]
    last_mot_result: Optional[str]
    failure_risk: float = Field(..., ge=0, le=1)
    confidence_level: str
    risk_components: Dict[str, float]
    repair_cost_estimate: Dict[str, Any]
    model_version: str
    note: Optional[str] = None


class RiskData(BaseModel):
    """Risk data for lead submission."""
    failure_risk: Optional[float] = None
    reliability_score: Optional[int] = None
    top_risks: Optional[List[str]] = None


class LeadSubmission(BaseModel):
    """Lead capture form submission."""
    email: str
    postcode: str
    name: Optional[str] = None
    phone: Optional[str] = None
    lead_type: str = "garage"
    vehicle: Optional[VehicleInfo] = None
    risk_data: Optional[RiskData] = None

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Validate email format."""
        if not v or '@' not in v:
            raise ValueError('Invalid email format')
        local, domain = v.rsplit('@', 1)
        if not local or not domain or '.' not in domain:
            raise ValueError('Invalid email format')
        # Basic sanitization
        return v.lower().strip()[:255]

    @field_validator('postcode')
    @classmethod
    def validate_postcode(cls, v: str) -> str:
        """Validate UK postcode format."""
        if not v or len(v.strip()) < 3:
            raise ValueError('Postcode must be at least 3 characters')
        # Basic sanitization
        return v.upper().strip()[:10]


class LeadResponse(BaseModel):
    """Response for lead submission."""
    success: bool
    lead_id: str
    message: str


class LeadsListResponse(BaseModel):
    """Response for leads list."""
    leads: List[Dict[str, Any]]
    count: int
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: str
    database: str
    model_loaded: bool


# ============================================================================
# Database Utilities
# ============================================================================

def get_sqlite_connection() -> Optional[sqlite3.Connection]:
    """Get SQLite connection if available."""
    if not os.path.exists(DB_FILE):
        return None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Verify the table exists
        conn.execute("SELECT 1 FROM risks LIMIT 1")
        return conn
    except sqlite3.Error as e:
        logger.warning(f"SQLite connection failed: {e}")
        return None


# ============================================================================
# Mock Data for Demo Mode
# ============================================================================

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

# ============================================================================
# Helper Functions
# ============================================================================

def add_confidence_intervals(result: dict) -> dict:
    """
    Add Wilson confidence intervals to a risk result.
    Modifies the result dict in place and returns it.
    """
    if 'Total_Tests' in result and 'Total_Failures' in result:
        total_tests = result.get('Total_Tests', 0)
        total_failures = result.get('Total_Failures', 0)

        # Validate inputs
        if isinstance(total_tests, (int, float)) and isinstance(total_failures, (int, float)):
            total_tests = int(total_tests)
            total_failures = int(total_failures)

            if total_tests > 0 and total_failures >= 0:
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
    try:
        cost_estimate = calculate_expected_repair_cost(result)
        if cost_estimate:
            result['Repair_Cost_Estimate'] = cost_estimate
    except Exception as e:
        logger.warning(f"Failed to calculate repair cost: {e}")
    return result


def get_confidence_level(total_tests: int) -> str:
    """Determine confidence level based on sample size."""
    if total_tests >= CONFIDENCE_THRESHOLD_HIGH:
        return "High"
    elif total_tests >= CONFIDENCE_THRESHOLD_MEDIUM:
        return "Medium"
    else:
        return "Low"


def create_default_response(vehicle: str, year: Optional[int]) -> Dict[str, Any]:
    """Create default response with population averages."""
    return {
        "vehicle": vehicle,
        "year": year,
        "mileage": None,
        "last_mot_date": None,
        "last_mot_result": None,
        "failure_risk": POPULATION_AVERAGE_FAILURE_RATE,
        "confidence_level": "Low",
        "risk_brakes": DEFAULT_COMPONENT_RISKS["brakes"],
        "risk_suspension": DEFAULT_COMPONENT_RISKS["suspension"],
        "risk_tyres": DEFAULT_COMPONENT_RISKS["tyres"],
        "risk_steering": DEFAULT_COMPONENT_RISKS["steering"],
        "risk_visibility": DEFAULT_COMPONENT_RISKS["visibility"],
        "risk_lamps": DEFAULT_COMPONENT_RISKS["lamps"],
        "risk_body": DEFAULT_COMPONENT_RISKS["body"],
        "repair_cost_estimate": {"expected": "£250", "range_low": 100, "range_high": 500},
    }


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for monitoring."""
    db_status = "disconnected"

    # Check PostgreSQL if configured
    if DATABASE_URL:
        try:
            pool = await db.get_pool()
            if pool:
                # Actually verify database is responsive
                async with pool.acquire() as conn:
                    result = await conn.fetchval("SELECT 1")
                    db_status = "connected" if result == 1 else "error"
        except Exception as e:
            logger.warning(f"Health check DB error: {e}")
            db_status = "error"
    # Check SQLite if available
    elif os.path.exists(DB_FILE):
        conn = get_sqlite_connection()
        if conn:
            try:
                conn.execute("SELECT 1")
                db_status = "connected"
            except Exception:
                db_status = "error"
            finally:
                conn.close()

    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": db_status,
        "model_loaded": model_v55.is_model_loaded()
    }


@app.get("/api/makes", response_model=List[str])
@limiter.limit("100/minute")
async def get_makes(request: Request):
    """Return a list of all unique vehicle makes (cached for 1 hour)."""
    # Check cache first (thread-safe read)
    async with _cache_lock:
        if _cache["makes"]["data"] and (time.time() - _cache["makes"]["time"]) < CACHE_TTL:
            logger.debug("Returning cached makes list")
            return _cache["makes"]["data"]

    # Try PostgreSQL first
    if DATABASE_URL:
        try:
            result = await db.get_makes()
            if result is not None:
                async with _cache_lock:
                    _cache["makes"] = {"data": result, "time": time.time()}
                logger.info(f"Cached {len(result)} makes from PostgreSQL")
                return result
        except Exception as e:
            logger.error(f"PostgreSQL get_makes failed: {e}")

    # Fallback to SQLite
    conn = get_sqlite_connection()
    if conn:
        try:
            # Only return makes with sufficient test volume
            query = """
                SELECT SUBSTR(model_id, 1, INSTR(model_id || ' ', ' ') - 1) as make,
                       SUM(Total_Tests) as test_count
                FROM risks
                GROUP BY make
                HAVING SUM(Total_Tests) >= ?
            """
            rows = conn.execute(query, (MIN_TESTS_FOR_UI,)).fetchall()
            makes = sorted(set(row['make'] for row in rows if row['make']))
            async with _cache_lock:
                _cache["makes"] = {"data": makes, "time": time.time()}
            logger.info(f"Cached {len(makes)} makes from SQLite")
            return makes
        except sqlite3.Error as e:
            logger.error(f"SQLite get_makes failed: {e}")
        finally:
            conn.close()

    # Demo mode
    logger.warning("Returning mock makes - no database available")
    return sorted(MOCK_MAKES)


@app.get("/api/models", response_model=List[str])
@limiter.limit("100/minute")
async def get_models(
    request: Request,
    make: str = Query(..., min_length=1, max_length=50, description="Vehicle Make (e.g., FORD)")
):
    """Return a list of models for a given make (cached for 1 hour)."""
    cache_key = make.upper().strip()

    # Check cache first (thread-safe read)
    async with _cache_lock:
        if cache_key in _cache["models"] and (time.time() - _cache["models"][cache_key]["time"]) < CACHE_TTL:
            logger.debug(f"Returning cached models for {cache_key}")
            return _cache["models"][cache_key]["data"]

    # Try PostgreSQL first
    if DATABASE_URL:
        try:
            result = await db.get_models(make)
            if result is not None:
                async with _cache_lock:
                    _cache["models"][cache_key] = {"data": result, "time": time.time()}
                logger.info(f"Cached {len(result)} models for {cache_key} from PostgreSQL")
                return result
        except Exception as e:
            logger.error(f"PostgreSQL get_models failed: {e}")

    # Fallback to SQLite
    conn = get_sqlite_connection()
    if conn:
        try:
            from consolidate_models import get_canonical_models_for_make

            # Only return models with sufficient test volume
            query = """
                SELECT model_id, SUM(Total_Tests) as test_count
                FROM risks
                WHERE model_id LIKE ?
                GROUP BY model_id
                HAVING SUM(Total_Tests) >= ?
            """
            rows = conn.execute(query, (f"{cache_key}%", MIN_TESTS_FOR_UI)).fetchall()

            # Extract base models from found entries
            found_models: Dict[str, int] = {}
            for row in rows:
                base_model = extract_base_model(row['model_id'], make)
                if base_model and len(base_model) > 1:
                    test_count = row['test_count'] or 0
                    if base_model not in found_models or test_count > found_models[base_model]:
                        found_models[base_model] = test_count

            # Get curated list of known models for this make
            known_models = get_canonical_models_for_make(make)

            if known_models:
                # Only return models from curated list that exist in data
                result = sorted([m for m in known_models if m in found_models])
            else:
                # For non-curated makes, return models with alphanumeric names (allows "3 SERIES", "A6")
                result = sorted([
                    m for m in found_models.keys()
                    if len(m) >= 2 and any(c.isalpha() for c in m)
                ])[:30]

            async with _cache_lock:
                _cache["models"][cache_key] = {"data": result, "time": time.time()}
            logger.info(f"Cached {len(result)} models for {cache_key} from SQLite")
            return result
        except (sqlite3.Error, Exception) as e:
            logger.error(f"SQLite get_models failed: {e}")
        finally:
            conn.close()

    # Demo mode - return mock models
    logger.warning(f"Returning mock models for {cache_key} - no database available")
    return MOCK_MODELS


@app.get("/api/risk")
@limiter.limit("20/minute")
async def get_risk(
    request: Request,
    make: str = Query(..., min_length=1, max_length=50, description="Vehicle make (e.g., FORD)"),
    model: str = Query(..., min_length=1, max_length=50, description="Vehicle model (e.g., FIESTA)"),
    year: int = Query(..., ge=1900, le=datetime.now().year + 1, description="Year of manufacture")
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
    current_year = datetime.now().year
    age = current_year - year
    if age < 0:
        age = 0  # Future vehicle, treat as new
    age_band = get_age_band(age)

    # Default response (population average)
    default_response = create_default_response(model_id, year)

    # Try SQLite lookup
    conn = get_sqlite_connection()
    if not conn:
        logger.warning("No database connection available, returning population average")
        return default_response

    try:
        # Query for exact make/model match with age band
        query = """
            SELECT * FROM risks
            WHERE model_id LIKE ? AND age_band = ?
            ORDER BY Total_Tests DESC
            LIMIT 1
        """
        row = conn.execute(query, (f"{make_upper} {model_upper}%", age_band)).fetchone()

        # If not found, try just the make with age band
        if not row:
            row = conn.execute(query, (f"{make_upper}%", age_band)).fetchone()

        if not row:
            logger.info(f"No lookup data for {model_id} age_band={age_band}, returning population average")
            return default_response

        result = dict(row)

        # Calculate confidence level based on sample size
        total_tests = result.get('Total_Tests', 0) or 0
        confidence_level = get_confidence_level(total_tests)

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
            "failure_risk": result.get('Failure_Risk', POPULATION_AVERAGE_FAILURE_RATE),
            "confidence_level": confidence_level,
            "risk_brakes": result.get('Risk_Brakes', DEFAULT_COMPONENT_RISKS["brakes"]),
            "risk_suspension": result.get('Risk_Suspension', DEFAULT_COMPONENT_RISKS["suspension"]),
            "risk_tyres": result.get('Risk_Tyres', DEFAULT_COMPONENT_RISKS["tyres"]),
            "risk_steering": result.get('Risk_Steering', DEFAULT_COMPONENT_RISKS["steering"]),
            "risk_visibility": result.get('Risk_Visibility', DEFAULT_COMPONENT_RISKS["visibility"]),
            "risk_lamps": result.get('Risk_Lamps_Reflectors_Electrical_Equipment', DEFAULT_COMPONENT_RISKS["lamps"]),
            "risk_body": result.get('Risk_Body_Chassis_Structure_Exhaust', DEFAULT_COMPONENT_RISKS["body"]),
            "repair_cost_estimate": repair_cost_formatted,
        }

    except sqlite3.Error as e:
        logger.error(f"SQLite error during risk lookup: {e}")
        return default_response
    except Exception as e:
        logger.error(f"Unexpected error during risk lookup: {e}")
        return default_response
    finally:
        conn.close()


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
    # Validate postcode if provided
    if postcode and len(postcode.strip()) > 0:
        postcode = postcode.strip().upper()
    else:
        postcode = ""

    # Check if model is loaded
    if not model_v55.is_model_loaded():
        raise HTTPException(
            status_code=503,
            detail="Prediction model not available"
        )

    # Get DVSA client
    dvsa_client = get_dvsa_client()
    if not dvsa_client:
        logger.error("DVSA client not available")
        return await _fallback_prediction(
            registration=registration.upper().replace(" ", ""),
            make="",
            model="",
            year=None,
            postcode=postcode,
            note="DVSA client not initialized"
        )

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
            make=history.make or "",
            model=history.model or "",
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
            make=history.make or "",
            model=history.model or "",
            year=year,
            postcode=postcode,
            note=f"Model prediction error: {str(e)}"
        )

    # Extract vehicle info safely
    year = history.manufacture_date.year if history.manufacture_date else None
    last_test = history.mot_tests[0] if history.mot_tests and len(history.mot_tests) > 0 else None

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
        "last_mot_date": last_test.test_date.isoformat() if last_test and last_test.test_date else None,
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
) -> Dict[str, Any]:
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
            "failure_risk": POPULATION_AVERAGE_FAILURE_RATE,
            "confidence_level": "Low",
            "risk_components": DEFAULT_COMPONENT_RISKS.copy(),
            "repair_cost_estimate": DEFAULT_REPAIR_COST.copy(),
            "model_version": "lookup",
            "note": note or "Vehicle not found - using UK population average",
        }

    model_id = f"{make.upper()} {model.upper()}"

    # Calculate age band if year available
    if year and year > 0:
        current_year = datetime.now().year
        age = current_year - year
        if age < 0:
            age = 0
        age_band = get_age_band(age)
    else:
        age_band = "6-10"  # Default to middle band

    # Try SQLite fallback
    conn = get_sqlite_connection()
    if conn:
        try:
            query = "SELECT * FROM risks WHERE model_id LIKE ? AND age_band = ? LIMIT 1"
            row = conn.execute(query, (f"{make.upper()}%", age_band)).fetchone()

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
                    "failure_risk": result.get('Failure_Risk', POPULATION_AVERAGE_FAILURE_RATE),
                    "confidence_level": "Medium",
                    "risk_components": {
                        "brakes": result.get('Risk_Brakes', DEFAULT_COMPONENT_RISKS["brakes"]),
                        "suspension": result.get('Risk_Suspension', DEFAULT_COMPONENT_RISKS["suspension"]),
                        "tyres": result.get('Risk_Tyres', DEFAULT_COMPONENT_RISKS["tyres"]),
                        "steering": result.get('Risk_Steering', DEFAULT_COMPONENT_RISKS["steering"]),
                        "visibility": result.get('Risk_Visibility', DEFAULT_COMPONENT_RISKS["visibility"]),
                        "lamps": result.get('Risk_Lamps_Reflectors_Electrical_Equipment', DEFAULT_COMPONENT_RISKS["lamps"]),
                        "body": result.get('Risk_Body_Chassis_Structure_Exhaust', DEFAULT_COMPONENT_RISKS["body"]),
                    },
                    "repair_cost_estimate": result.get('Repair_Cost_Estimate', DEFAULT_REPAIR_COST.copy()),
                    "model_version": "lookup",
                    "note": note,
                }
        except sqlite3.Error as e:
            logger.error(f"SQLite error in fallback prediction: {e}")
        finally:
            conn.close()

    # Default fallback - population average
    return {
        "registration": registration,
        "vehicle": {"make": make.upper(), "model": model.upper(), "year": year},
        "mileage": None,
        "last_mot_date": None,
        "last_mot_result": None,
        "failure_risk": POPULATION_AVERAGE_FAILURE_RATE,
        "confidence_level": "Low",
        "risk_components": DEFAULT_COMPONENT_RISKS.copy(),
        "repair_cost_estimate": DEFAULT_REPAIR_COST.copy(),
        "model_version": "lookup",
        "note": note or "Limited data - using population averages",
    }


def _estimate_repair_cost(failure_risk: float, risk_components: Dict[str, float]) -> Dict[str, int]:
    """Estimate repair costs based on risk prediction."""
    # Validate inputs
    if not isinstance(failure_risk, (int, float)) or failure_risk <= 0:
        return DEFAULT_REPAIR_COST.copy()

    if not isinstance(risk_components, dict):
        return DEFAULT_REPAIR_COST.copy()

    # Expected cost = sum of (component_risk * component_cost)
    expected = 0.0
    for comp, cost in COMPONENT_REPAIR_COSTS.items():
        comp_risk = risk_components.get(comp, 0)
        if isinstance(comp_risk, (int, float)) and comp_risk >= 0:
            expected += comp_risk * cost

    # Scale by overall failure probability (avoid division by zero)
    if POPULATION_AVERAGE_FAILURE_RATE > 0:
        expected = expected * (failure_risk / POPULATION_AVERAGE_FAILURE_RATE)

    # Ensure minimum expected cost
    expected = max(expected, 50)

    return {
        "expected": int(round(expected, -1)),  # Round to nearest 10
        "range_low": int(round(expected * 0.5, -1)),
        "range_high": int(round(expected * 2, -1)),
    }


# ============================================================================
# Lead Capture Endpoints
# ============================================================================

# Admin API key for accessing leads
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")


@app.post("/api/leads", status_code=201, response_model=LeadResponse)
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
    try:
        lead_id = await db.save_lead(lead_data)
    except Exception as e:
        logger.error(f"Failed to save lead: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save lead. Please try again."
        )

    if not lead_id:
        raise HTTPException(
            status_code=500,
            detail="Failed to save lead. Please try again."
        )

    return {
        "success": True,
        "lead_id": lead_id,
        "message": "Thanks! We'll be in touch soon."
    }


@app.get("/api/leads", response_model=LeadsListResponse)
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

    if not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key"
        )

    # Get leads from database
    try:
        leads = await db.get_leads(limit=limit, offset=offset, since=since)
    except Exception as e:
        logger.error(f"Failed to retrieve leads: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve leads"
        )

    if leads is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve leads"
        )

    try:
        total = await db.count_leads(since=since)
    except Exception:
        total = len(leads)

    return {
        "leads": leads,
        "count": len(leads),
        "total": total or len(leads),
        "limit": limit,
        "offset": offset
    }


# ============================================================================
# Static Files and Root Route
# ============================================================================

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def read_index():
        index_path = os.path.join(STATIC_DIR, 'index.html')
        if os.path.exists(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Index page not found")
else:
    @app.get("/")
    def read_root():
        db_status = "PostgreSQL" if DATABASE_URL else ("SQLite" if os.path.exists(DB_FILE) else "Demo Mode")
        return {
            "status": "ok",
            "message": "AutoSafe API",
            "database": db_status,
            "endpoints": ["/api/makes", "/api/models", "/api/risk", "/api/risk/v55"]
        }
