"""
AutoSafe v2 report API -- HTTP layer.

``register_report_routes(app, limiter, get_sqlite_connection)`` registers,
directly on the passed-in FastAPI ``app``:

    POST /api/v2/reports          create (or idempotently replay) a report
    GET  /api/v2/reports/{token}  fetch a previously created report

plus the ``ReportAPIError`` exception handler that turns every typed v2
error into an ``ErrorEnvelope`` JSON body. This mirrors
``seo_pages.register_seo_routes``'s composition style: a single function
that closes over its FastAPI/db-pool dependencies and registers routes as
decorators directly on ``app``, rather than an ``APIRouter``.

Also exported for main.py to wire in with the same "one import" (see the
module docstring on why these three names, not just
``register_report_routes``, need to come from here -- main.py's edit
surface for the v2 release is invariant-limited to one new import
statement, so every piece of v2-aware logic main.py needs, including the
rate-limit dispatcher and the /api/version payload builder, is exposed
from this module rather than written inline in main.py):

    rate_limit_exceeded_dispatcher  main.py's RateLimitExceeded handler,
                                     swapped in for slowapi's default so
                                     /api/v2/* gets an ErrorEnvelope 429
                                     while every legacy route keeps
                                     slowapi's exact original 429 shape.
    build_version_info(app)         the GET /api/version payload.

No fabricated evidence, ever
-----------------------------
This module inherits report_contract.py's core invariant: a DVSA failure
is a hard typed error (VEHICLE_NOT_FOUND / DVSA_UNAVAILABLE), never a 200
with made-up data, and GET of a token that cannot be honestly served
(absent, expired, pseudonymised, storage down, or -- defensively --
unparseable) is 404/410/503, never a reconstructed report. Demo mode is
the one deliberate exception: it is a clearly-labelled synthetic path
(``vehicle_data_source='demo'``), never presented as real DVSA evidence.
"""
import asyncio
import functools
import hashlib
import logging
import os
import re
import secrets
import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

import database as db
import prediction_service
import report_service
from dvsa_client import (
    DVSAAPIError,
    MOTTest,
    VehicleHistory,
    VehicleNotFoundError,
    get_dvsa_client,
)
from regional_defaults import validate_postcode as _validate_postcode
from report_contract import (
    ERROR_CODE_STATUS,
    REPORT_CONTRACT_VERSION,
    REPORT_TTL_DAYS,
    SHARE_URL_PATH,
    ErrorCode,
    ErrorEnvelope,
    PredictionSource,
    ReportCreateRequest,
    ReportPersistence,
    ReportResponse,
    VehicleDataSource,
)
from utils import hash_vrm, safe_log_path, safe_referrer

logger = logging.getLogger(__name__)

# Read once at import time, mirroring email_templates.py's BASE_URL
# convention exactly (same env var, same default) rather than re-reading
# os.environ on every request.
BASE_URL = os.environ.get("BASE_URL", "https://www.autosafe.one")

# Hard rule for a normalized VRN: 2-8 alphanumeric characters, upper case.
# Matches dvsa_client.DVSAClient.VRM_BASIC_PATTERN's rule exactly (kept as
# an independent constant, not an import, so this module's validation
# doesn't depend on dvsa_client's exception type -- ReportAPIError is the
# only error type this module's callers need to handle).
_VRN_PATTERN = re.compile(r'^[A-Z0-9]{2,8}$')

# Share-token shape guard for GET /api/v2/reports/{token}: matches
# secrets.token_urlsafe(16)'s alphabet (base64url: A-Za-z0-9_-) and gives
# generous length headroom (16 raw bytes -> 22 chars, no padding) without
# encoding the exact byte count into the contract.
_TOKEN_SHAPE_PATTERN = re.compile(r'^[A-Za-z0-9_-]{10,64}$')

# Same fallback identity main.py's /api/vehicle uses today (main.py,
# DEMO_VEHICLES.get(vrm, {...})) for a VRM that doesn't match one of the
# three curated DEMO_VEHICLES entries -- reused verbatim so an
# unrecognised demo VRM degrades identically in both places instead of
# drifting into a second, silently-different "unknown demo vehicle".
_DEMO_FALLBACK_ENTRY: Dict[str, Any] = {
    "make": "DEMO",
    "model": "VEHICLE",
    "yearOfManufacture": 2020,
    "fuelType": "PETROL",
    "colour": "SILVER",
}

# Module-load timestamp for GET /api/version's started_at. Computed once,
# here, rather than in main.py, per this module's docstring on why
# version-info assembly lives in this file.
_STARTED_AT = datetime.now(timezone.utc).isoformat()


class ReportAPIError(Exception):
    """Typed v2 API error. error_code drives both the HTTP status (via
    ERROR_CODE_STATUS) and the machine-readable field on ErrorEnvelope.

    correlation_id is optional: most call sites let the exception handler
    mint one. The one exception is the internal-error catch-all in
    _guard_internal_errors, which mints its own correlation_id so the
    *same* id appears in both the full-traceback server log line and the
    client-visible envelope -- passing it through here keeps those two
    log/response artifacts joinable instead of each minting an
    independent, unrelated id.
    """

    def __init__(self, error_code: ErrorCode, message: str, correlation_id: Optional[str] = None):
        self.error_code = error_code
        self.message = message
        self.status_code = ERROR_CODE_STATUS[error_code]
        self.correlation_id = correlation_id
        super().__init__(message)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _correlation_id() -> str:
    """Mirrors main.py's generate_correlation_id() exactly (same shape:
    12 hex chars from a uuid4), kept as an independent copy rather than an
    import so this module's error handling doesn't depend on main.py (main
    imports *this* module, not the other way around -- see
    _build_demo_history's deferred import for the one place that
    necessarily runs the other direction, and why it has to be deferred)."""
    return uuid_module.uuid4().hex[:12]


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize a possibly-aware, possibly-naive datetime to naive-UTC for
    safe comparison. asyncpg decodes a Postgres ``TIMESTAMP`` (no time
    zone) column -- what expires_at is (migrations/add_report_contract_columns.py)
    -- back into a *naive* datetime.datetime (verified against the
    installed asyncpg 0.29.0's pgproto timestamp codec: encoding a
    tz-aware value into that column type raises, so nothing in this
    codebase's write path could have put a tz-aware value in there
    either). This helper still defends the aware case so a comparison
    never raises TypeError even if the column is ever migrated to
    TIMESTAMPTZ in the future.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _normalize_vrn(raw: str) -> str:
    """Strip all whitespace (not just leading/trailing -- a plate typed or
    pasted with an internal tab/newline should normalize the same as one
    with an internal space), upper-case, and hard-validate against
    ``_VRN_PATTERN``. Raises ReportAPIError(invalid_registration) rather
    than returning an error tuple, so callers get FastAPI's normal
    exception-handler dispatch for free."""
    vrn = re.sub(r'\s+', '', raw).upper()
    if not _VRN_PATTERN.match(vrn):
        raise ReportAPIError(
            ErrorCode.INVALID_REGISTRATION,
            "That doesn't look like a valid UK registration number.",
        )
    return vrn


def _normalize_postcode(raw: Optional[str]) -> Optional[str]:
    """Best-effort postcode normalization. Never rejects -- postcode is
    optional on the v2 contract and is used for (a) the DB row's analytics
    field (LIA-covered) and (b) the V55 prediction path's regional
    corrosion-index input (prediction_service.build_v55_assessment; an
    unresolvable or missing postcode falls back to the default index, it
    never blocks the prediction). It is never a gate on report creation
    and never appears in the response payload at all (ReportResponse has
    no postcode field)."""
    if not raw or not raw.strip():
        return None
    try:
        result = _validate_postcode(raw)
        normalized = result.get('normalized') if isinstance(result, dict) else None
        if normalized:
            return normalized
    except Exception as exc:
        logger.warning(
            "postcode normalization raised; falling back to strip+upper (%s)",
            type(exc).__name__,
        )
    return raw.strip().upper()


def _share_url(token: str) -> str:
    return BASE_URL.rstrip('/') + SHARE_URL_PATH.format(token=token)


def _read_build_value(filename: str) -> Optional[str]:
    """Read a small image-build metadata file, returning None outside a
    packaged image instead of inventing an identity."""
    try:
        value = (Path(__file__).resolve().parent / filename).read_text().strip()
    except OSError:
        return None
    return value or None


@functools.lru_cache(maxsize=1)
def _compute_frontend_bundle_hash() -> Optional[str]:
    """Return the full SHA-256 of the built JavaScript entry bundle.

    The Vite-generated ``static/index.html`` is used only to discover the
    hashed entry filename; the digest is over the JavaScript bytes themselves,
    matching the release packet's meaning of "frontend bundle hash". Returning
    an index-document checksum (and truncating it) would give a reproducible
    value, but it would not identify the deployed bundle the browser executes.

    ``None`` is honest when a backend-only checkout has no built entry bundle.
    The zero-argument cache is safe because build artifacts cannot change
    during a running image's lifetime.
    """
    static_dir = Path(__file__).resolve().parent / "static"
    try:
        index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    script_sources = re.findall(r'<script[^>]+src=["\']([^"\']+\.js)["\']', index_html, re.I)
    for source in script_sources:
        relative = source.split("?", 1)[0].lstrip("/")
        candidate = (static_dir / relative).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            continue
        try:
            return hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            continue
    return None


def build_version_info(app: FastAPI) -> Dict[str, Any]:
    """Payload for GET /api/version -- build/release identifiers for
    release-verification tooling (e.g. confirming a deploy actually
    shipped the expected backend commit + frontend bundle together)."""
    backend_sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_SHA") or "unknown"
    frontend_sha = os.environ.get("FRONTEND_BUILD_SHA") or _read_build_value(".frontend_sha") or "unknown"
    build_timestamp = os.environ.get("BUILD_TIMESTAMP") or _read_build_value(".build_timestamp")
    return {
        "backend_sha": backend_sha,
        "frontend_sha": frontend_sha,
        "frontend_bundle_hash": _compute_frontend_bundle_hash(),
        "contract_version": REPORT_CONTRACT_VERSION,
        "app_version": app.version,
        "build_timestamp": build_timestamp,
        "started_at": _STARTED_AT,
    }


# ---------------------------------------------------------------------------
# Rate-limit dispatcher (shared with main.py's RateLimitExceeded handler)
# ---------------------------------------------------------------------------

async def rate_limit_exceeded_dispatcher(request: Request, exc: RateLimitExceeded):
    """Replaces slowapi's default handler app-wide. /api/v2/* gets the v2
    ErrorEnvelope shape; every other (legacy) route keeps slowapi's
    original handler and its exact original response shape
    (``{"error": "Rate limit exceeded: ..."}``, verified against the
    installed slowapi 0.1.9 source) -- legacy byte-identical-response is a
    hard invariant for this release, and 429 is explicitly called out as
    part of it."""
    if request.url.path.startswith("/api/v2/"):
        correlation_id = _correlation_id()
        logger.warning(
            "rate_limited correlation_id=%s path=%s", correlation_id, safe_log_path(request.url.path),
        )
        envelope = ErrorEnvelope(
            error_code=ErrorCode.RATE_LIMITED,
            message="Too many requests -- please slow down and try again shortly.",
            correlation_id=correlation_id,
        )
        return JSONResponse(status_code=429, content=envelope.model_dump(mode='json'))
    return _rate_limit_exceeded_handler(request, exc)


# ---------------------------------------------------------------------------
# Undeclared-query-parameter guard (shared dependency, both v2 routes)
# ---------------------------------------------------------------------------

async def _reject_undeclared_query_params(request: Request) -> None:
    """Neither v2 route declares any query parameters (POST takes its
    input as a JSON body; GET takes its input as the {token} path
    parameter) -- so *any* query string on either route is, by
    definition, undeclared. FastAPI does not reject unknown query params
    by default (unlike the request body's ``extra="forbid"`` pydantic
    models), so this dependency exists to make the v2 surface strict in
    both directions, not just on the body."""
    if request.query_params:
        raise ReportAPIError(
            ErrorCode.UNDECLARED_PARAMETER,
            "Unexpected query parameter(s): {}.".format(", ".join(sorted(request.query_params.keys()))),
        )


async def _guard_internal_errors(request: Request, coro):
    """Run `coro` (an already-constructed, not-yet-awaited coroutine),
    letting ReportAPIError propagate untouched but converting any other
    exception into a 500 internal_error envelope. The envelope's message
    is a fixed, generic string -- never str(exc) -- so no exception detail
    or stack trace ever reaches the response body or application logs. The
    exception type and correlation id preserve an operational breadcrumb
    without risking request values embedded in an exception message.
    """
    try:
        return await coro
    except ReportAPIError:
        raise
    except Exception as exc:
        correlation_id = _correlation_id()
        logger.error(
            "report_unhandled_exception correlation_id=%s path=%s exception_type=%s",
            correlation_id, safe_log_path(request.url.path), type(exc).__name__,
        )
        raise ReportAPIError(
            ErrorCode.INTERNAL_ERROR,
            "An internal error occurred. Please try again later.",
            correlation_id=correlation_id,
        )


# ---------------------------------------------------------------------------
# Demo-mode vehicle history (DVSA not configured)
# ---------------------------------------------------------------------------

def _stable_hash_int(vrm: str, salt: str) -> int:
    """Deterministic, VRM-derived pseudo-random integer used to vary
    synthetic demo MOT figures per VRM -- never `random`, so the same VRM
    always produces byte-identical demo report figures (this is what
    makes demo mode idempotency-safe and testable)."""
    digest = hashlib.sha256("{}:{}".format(vrm, salt).encode()).hexdigest()
    return int(digest[:8], 16)


def _build_demo_history(vrm: str) -> VehicleHistory:
    """Deterministic synthetic VehicleHistory for demo mode (DVSA not
    configured). Reuses main.py's curated DEMO_VEHICLES identity fields
    when the VRM matches one of its three known demo plates; otherwise
    falls back to the same generic entry main.py's /api/vehicle already
    uses today for an unrecognised VRM in demo mode
    (_DEMO_FALLBACK_ENTRY, copied verbatim from main.py's DEMO_VEHICLES.get
    default). 2-3 MOT tests, newest-first, with a plausible constant
    annual mileage rate derived from a stable hash of the VRM -- never
    random -- so observed_mot mileage resolution is honestly exercisable
    and reproducible.

    Deferred import: main.py is what imports *this* module to register
    the v2 routes (register_report_routes is called from main.py), so an
    `from main import DEMO_VEHICLES` at this module's top level would be
    a circular import evaluated before DEMO_VEHICLES exists in main's
    namespace. Deferring the import into this function body means it
    only runs at request-handling time, long after main.py has finished
    executing its module body -- standard fix for this shape of cycle.
    """
    from main import DEMO_VEHICLES  # noqa: PLC0415 - deferred to avoid an import cycle; see docstring.

    entry = DEMO_VEHICLES.get(vrm, _DEMO_FALLBACK_ENTRY)
    year = entry.get("yearOfManufacture")
    manufacture_date = datetime(year, 1, 1) if year else None

    n_tests = 2 + (_stable_hash_int(vrm, 'n_tests') % 2)  # 2 or 3, deterministic per VRM
    annual_miles = 6000 + (_stable_hash_int(vrm, 'annual_miles') % 9000)  # 6,000-14,999 mi/yr
    today = datetime.now()

    tests = []
    for i in range(n_tests):
        test_date = datetime(today.year - i, today.month, 15)
        tests.append(MOTTest(
            test_date=test_date,
            test_result='PASSED',
            expiry_date=test_date + timedelta(days=365),
            odometer_value=annual_miles * (n_tests - i),
            odometer_unit='mi',
            test_number='DEMO{}{}'.format(vrm, i),
            defects=[],
        ))

    return VehicleHistory(
        registration=vrm,
        make=entry.get("make", "DEMO"),
        model=entry.get("model", "VEHICLE"),
        fuel_type=entry.get("fuelType"),
        colour=entry.get("colour"),
        registration_date=manufacture_date,
        manufacture_date=manufacture_date,
        engine_size=None,
        mot_tests=tests,
    )


async def _resolve_vehicle_and_history(vrm: str) -> Tuple[Dict[str, Any], VehicleHistory, VehicleDataSource]:
    """Resolve vehicle identity + MOT history, real DVSA or honest demo.

    identity mirrors main.py's own year-from-manufacture_date derivation
    exactly (see /api/risk/v55: `year = history.manufacture_date.year if
    history.manufacture_date else None`), so v2 vehicle-year semantics
    match the v1 DVSA-backed endpoint's.
    """
    client = get_dvsa_client()
    if client.is_configured:
        try:
            history = await client.fetch_vehicle_history(vrm)
        except VehicleNotFoundError:
            raise ReportAPIError(
                ErrorCode.VEHICLE_NOT_FOUND,
                "We couldn't find that registration in the MOT database.",
            )
        except DVSAAPIError:
            raise ReportAPIError(
                ErrorCode.DVSA_UNAVAILABLE,
                "The vehicle-data service is temporarily unavailable — please try again shortly.",
            )
        vehicle_data_source = VehicleDataSource.DVSA
    else:
        history = _build_demo_history(vrm)
        vehicle_data_source = VehicleDataSource.DEMO

    year = history.manufacture_date.year if history.manufacture_date else None
    identity = {
        'registration': vrm,
        'make': history.make,
        'model': history.model,
        'year': year,
        'fuel_type': history.fuel_type,
        'colour': history.colour,
    }
    return identity, history, vehicle_data_source


# ---------------------------------------------------------------------------
# Idempotency replay (shared by the "check first" path and the
# UniqueViolationError race-resolution path)
# ---------------------------------------------------------------------------

async def _replay_idempotent(idempotency_key: str, vrm: str) -> Optional[ReportResponse]:
    """Look up a previously-created report by idempotency_key and return
    its stored payload verbatim, or None if there is nothing usable to
    replay (no row or Postgres unreachable).

    Once a row exists, the key is consumed. An expired, pseudonymised,
    missing, or contract-invalid stored payload returns the typed 409 used
    for every unusable key. The browser then clears that key and can submit
    a fresh logical operation; silently recomputing under the consumed key
    would only collide with its unique index and blur exactly-once identity.
    """
    try:
        existing = await db.get_report_by_idempotency_key(idempotency_key)
    except db.PostgresUnavailable:
        logger.warning("idempotency_lookup_unavailable hash_vrm=%s", hash_vrm(vrm))
        return None  # Postgres down -> skip idempotency, proceed fresh (spec'd behaviour).

    if existing is None:
        return None

    stored_vrm = existing.get('registration')
    normalized_stored_vrm = re.sub(r'\s+', '', stored_vrm).upper() if isinstance(stored_vrm, str) else None
    if normalized_stored_vrm != vrm:
        raise ReportAPIError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "That retry key was already used for a different report request.",
        )

    expires_at = _to_naive_utc(existing.get('expires_at'))
    if existing.get('pseudonymised_at') is not None or (
        expires_at is not None and expires_at < _naive_utc_now()
    ):
        raise ReportAPIError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "That retry key no longer refers to an active report. Submit again with a new key.",
        )

    payload = existing.get('report_payload')
    if payload is None:
        raise ReportAPIError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "That retry key no longer refers to an active report. Submit again with a new key.",
        )

    try:
        return ReportResponse.model_validate(payload)
    except ValidationError:
        logger.warning(
            "idempotency_replay_payload_invalid hash_vrm=%s report_id=%s",
            hash_vrm(vrm), existing.get('id'),
        )
        raise ReportAPIError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "That retry key no longer refers to an active report. Submit again with a new key.",
        )


# ---------------------------------------------------------------------------
# Persistence record
# ---------------------------------------------------------------------------

def _build_persist_record(
    request: Request,
    vrm: str,
    postcode: Optional[str],
    history: VehicleHistory,
    assessment: report_service.Assessment,
    vehicle_data_source: VehicleDataSource,
    token: str,
    payload_dict: Dict[str, Any],
    report_uuid: "uuid_module.UUID",
    expires_at_naive: datetime,
    idempotency_key: Optional[str],
) -> Dict[str, Any]:
    """Build the dict passed to database.save_report, matching its
    documented keys exactly (28 keys; see save_report's own docstring and
    its INSERT column list -- both enumerate the identical set). 'id' is
    the pre-minted report uuid so the persisted payload's report_id and
    the row's primary key agree byte-for-byte.

    last_mot_date/last_mot_result are read from `history` directly
    (history.mot_tests[0]), NOT from assessment.mot.last_test_date --
    that contract field is already an ISO *string*
    (report_service._build_mot), while the risk_checks.last_mot_date
    column is Postgres DATE. asyncpg's date/timestamp codecs require a
    real datetime.date/datetime.datetime, not a string (confirmed against
    the installed asyncpg 0.29.0 source: timestamp_encode raises TypeError
    for anything that isn't a date/datetime instance). This mirrors
    main.py's own /api/risk/v55 -> save_risk_check call exactly
    (`'last_mot_date': last_test.test_date if last_test else None`).
    """
    last_test = history.mot_tests[0] if history.mot_tests else None

    components_dict = None
    if assessment.components.available and assessment.components.items:
        components_dict = {item.key: item.risk for item in assessment.components.items}

    repair_dict = assessment.repair_estimate.model_dump() if assessment.repair_estimate is not None else None

    return {
        'id': report_uuid,
        'registration': vrm,
        'postcode': postcode,
        'vehicle_make': assessment.vehicle.make,
        'vehicle_model': assessment.vehicle.model,
        'vehicle_year': assessment.vehicle.year,
        'vehicle_fuel_type': assessment.vehicle.fuel_type,
        'mileage': assessment.mileage.effective_value,
        'last_mot_date': last_test.test_date if last_test else None,
        'last_mot_result': last_test.test_result if last_test else None,
        'failure_risk': assessment.risk.failure_risk,
        'confidence_level': assessment.risk.confidence.value,
        'risk_components': components_dict,
        'repair_cost_estimate': repair_dict,
        'model_version': (
            'v55' if assessment.prediction_source == PredictionSource.MODEL_V55 else 'lookup_v2'
        ),
        'prediction_source': assessment.prediction_source.value,
        'is_dvsa_data': vehicle_data_source == VehicleDataSource.DVSA,
        'referrer': safe_referrer(request.headers.get('referer')),
        'contract_version': payload_dict.get('contract_version'),
        'report_token': token,
        'report_payload': payload_dict,
        'mileage_source': assessment.mileage.source.value,
        'effective_mileage': assessment.mileage.effective_value,
        'match_scope': assessment.evidence.match_scope.value,
        'total_tests': assessment.evidence.total_tests,
        'total_failures': assessment.evidence.total_failures,
        'idempotency_key': idempotency_key,
        'expires_at': expires_at_naive,
    }


def _degrade(response: ReportResponse) -> ReportResponse:
    """Never hand out a token/id/share_url that could not be confirmed
    persisted. Mutates and returns the same (already fully-built) response
    object -- see _create_report_core's docstring for the "optimistic,
    then degrade" ordering this depends on."""
    response.report_id = None
    response.report_token = None
    response.share_url = None
    response.expires_at = None
    response.persistence = ReportPersistence(saved=False, share_available=False)
    return response


# ---------------------------------------------------------------------------
# Route bodies
# ---------------------------------------------------------------------------

async def _create_report_core(request: Request, body: ReportCreateRequest, sqlite_conn_factory) -> ReportResponse:
    """POST /api/v2/reports.

    Ordering, deliberately: normalize/validate the VRN first (cheap,
    local); check idempotency next, before touching DVSA or building an
    assessment, so a replay costs nothing beyond one DB read; only then
    resolve vehicle identity + history and run the evidence ladder.

    Persistence ordering: build the *optimistic* response and its
    model_dump(mode='json') snapshot for the DB record BEFORE calling
    save_report, so the persisted report_payload and the payload the
    caller sees are the same computation, not two independently-built
    ones that could drift. This module mints BOTH identifiers itself
    before persistence — report_token (secrets.token_urlsafe) and
    report_id (uuid4, inserted explicitly as the risk_checks.id) — so
    the persisted payload and the live POST response are byte-identical
    for every field, and a later GET replay returns exactly what POST
    returned. (Staging acceptance check 4f enforces the byte-equality;
    the pre-RC1 design let Postgres assign the id post-snapshot, which
    made report_id None on every replay.)
    """
    vrm = _normalize_vrn(body.registration)
    postcode = _normalize_postcode(body.postcode)
    vrm_hash = hash_vrm(vrm)

    if body.idempotency_key:
        replayed = await _replay_idempotent(body.idempotency_key, vrm)
        if replayed is not None:
            return replayed

    identity, history, vehicle_data_source = await _resolve_vehicle_and_history(vrm)

    # V55 prediction first, on real DVSA data with at least one recorded MOT
    # test: a synthetic demo history must never be presented as a per-vehicle
    # prediction, and a zero-history vehicle must not receive a result whose
    # copy claims it was produced from recorded MOT history. Only the typed
    # PredictionUnavailable failures fall back to the comparison assessment;
    # a contract/programming error propagates to the 500 guard instead of
    # being silently relabelled as a comparison. Inference is CPU-bound
    # (feature engineering + CatBoost), so it runs in a worker thread rather
    # than blocking the event loop.
    assessment = None
    if vehicle_data_source == VehicleDataSource.DVSA and getattr(history, 'mot_tests', None):
        try:
            assessment = await asyncio.to_thread(
                prediction_service.build_v55_assessment,
                identity, history, postcode,
            )
            logger.info(
                "v55_prediction outcome=success model_version=v55 correlation_id=%s hash_vrm=%s",
                _correlation_id(), vrm_hash,
            )
        except prediction_service.PredictionUnavailable as exc:
            logger.warning(
                "v55_prediction outcome=fallback reason=%s correlation_id=%s hash_vrm=%s",
                type(exc).__name__, _correlation_id(), vrm_hash,
            )

    if assessment is None:
        assessment = await report_service.build_assessment(
            identity,
            history,
            sqlite_conn_factory=sqlite_conn_factory,
            now=None,
        )

    now_dt = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(16)
    report_uuid = uuid_module.uuid4()
    expires_dt = now_dt + timedelta(days=REPORT_TTL_DAYS)

    response = ReportResponse(
        result_kind=assessment.result_kind,
        report_id=str(report_uuid),
        report_token=token,
        share_url=_share_url(token),
        created_at=now_dt.isoformat(),
        expires_at=expires_dt.isoformat(),
        registration=vrm,
        vehicle=assessment.vehicle,
        mot=assessment.mot,
        mileage=assessment.mileage,
        evidence=assessment.evidence,
        risk=assessment.risk,
        components=assessment.components,
        repair_estimate=assessment.repair_estimate,
        persistence=ReportPersistence(saved=True, share_available=True),
        prediction_source=assessment.prediction_source,
        vehicle_data_source=vehicle_data_source,
        note=assessment.note,
    )
    payload_dict = response.model_dump(mode='json')

    record = _build_persist_record(
        request=request,
        vrm=vrm,
        postcode=postcode,
        history=history,
        assessment=assessment,
        vehicle_data_source=vehicle_data_source,
        token=token,
        report_uuid=report_uuid,
        payload_dict=payload_dict,
        expires_at_naive=expires_dt.replace(tzinfo=None),
        idempotency_key=body.idempotency_key,
    )

    try:
        report_id = await db.save_report(record)
    except asyncpg.exceptions.UniqueViolationError:
        if body.idempotency_key:
            replayed = await _replay_idempotent(body.idempotency_key, vrm)
            if replayed is not None:
                return replayed
        logger.warning(
            "report_persist_failed correlation_id=%s hash_vrm=%s reason=unique_violation_no_replay",
            _correlation_id(), vrm_hash,
        )
        return _degrade(response)
    except db.PostgresUnavailable:
        logger.warning(
            "report_persist_unavailable correlation_id=%s hash_vrm=%s",
            _correlation_id(), vrm_hash,
        )
        return _degrade(response)

    if report_id is None:
        logger.warning(
            "report_persist_failed correlation_id=%s hash_vrm=%s",
            _correlation_id(), vrm_hash,
        )
        return _degrade(response)

    # report_id was minted pre-insert and passed to save_report as the
    # explicit row id; the returned id is the same value by construction.
    return response


async def _get_report_core(token: str) -> ReportResponse:
    """GET /api/v2/reports/{token}.

    Every failure mode here is 404/410/503 -- never a reconstructed
    report (report_contract.py's hard invariant). The bad-token-shape
    case is folded into the same 404/report_not_found as "well-formed but
    absent" specifically so the response can never be used to distinguish
    "malformed" from "no such report" (don't leak validity information).
    """
    if not _TOKEN_SHAPE_PATTERN.match(token):
        raise ReportAPIError(ErrorCode.REPORT_NOT_FOUND, "That report link isn't valid.")

    try:
        row = await db.get_report_by_token(token)
    except db.PostgresUnavailable:
        raise ReportAPIError(
            ErrorCode.STORAGE_UNAVAILABLE,
            "Report storage is temporarily unavailable — please try again shortly.",
        )

    if row is None:
        raise ReportAPIError(ErrorCode.REPORT_NOT_FOUND, "That report link isn't valid.")

    payload = row.get('report_payload')
    expires_at = _to_naive_utc(row.get('expires_at'))
    pseudonymised_at = row.get('pseudonymised_at')
    expired = expires_at is not None and expires_at < _naive_utc_now()

    if payload is None or pseudonymised_at is not None or expired:
        raise ReportAPIError(
            ErrorCode.REPORT_EXPIRED, "This report link has expired — run a fresh check.",
        )

    try:
        return ReportResponse.model_validate(payload)
    except ValidationError:
        # Defensive: a stored payload that no longer validates is, from
        # the caller's point of view, indistinguishable from "no longer
        # servable" -- report_contract.py's invariant #5 constrains GET
        # failures to exactly {404, 410, 503}, and of those three,
        # "expired" is the honest closest fit for "this link can't be
        # honoured anymore, go run a fresh check" (a 500 would also be
        # honest but falls outside that enumerated set).
        logger.error(
            "report_payload_invalid_on_fetch correlation_id=%s report_id=%s",
            _correlation_id(), row.get('id'),
        )
        raise ReportAPIError(
            ErrorCode.REPORT_EXPIRED, "This report link has expired — run a fresh check.",
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_report_routes(app: FastAPI, limiter, get_sqlite_connection) -> None:
    """Register the v2 report API routes + their ErrorEnvelope exception
    handler on `app`. Mirrors seo_pages.register_seo_routes(app,
    get_sqlite_connection)'s composition style."""

    @app.exception_handler(ReportAPIError)
    async def _handle_report_api_error(request: Request, exc: ReportAPIError):
        correlation_id = exc.correlation_id or _correlation_id()
        level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        logger.log(
            level,
            "report_api_error error_code=%s status=%s correlation_id=%s path=%s",
            exc.error_code.value, exc.status_code, correlation_id, safe_log_path(request.url.path),
        )
        envelope = ErrorEnvelope(error_code=exc.error_code, message=exc.message, correlation_id=correlation_id)
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode='json'))

    _ERROR_RESPONSES_POST = {
        400: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        429: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    }
    _ERROR_RESPONSES_GET = {
        400: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        410: {"model": ErrorEnvelope},
        429: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    }

    @app.post(
        "/api/v2/reports",
        response_model=ReportResponse,
        responses=_ERROR_RESPONSES_POST,
        dependencies=[Depends(_reject_undeclared_query_params)],
    )
    @limiter.limit("20/minute")
    async def create_report(request: Request, body: ReportCreateRequest) -> ReportResponse:
        return await _guard_internal_errors(
            request, _create_report_core(request, body, get_sqlite_connection)
        )

    @app.get(
        "/api/v2/reports/{token}",
        response_model=ReportResponse,
        responses=_ERROR_RESPONSES_GET,
        dependencies=[Depends(_reject_undeclared_query_params)],
    )
    @limiter.limit("60/minute")
    async def get_report(request: Request, token: str) -> ReportResponse:
        return await _guard_internal_errors(request, _get_report_core(token))
