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
import functools
import hashlib
import logging
import os
import re
import secrets
import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

import database as db
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
    ReportCreateRequest,
    ReportPersistence,
    ReportResponse,
    VehicleDataSource,
)
from utils import hash_vrm

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
    optional on the v2 contract and only ever used for the DB row's
    analytics field (LIA-covered) and, if regional_defaults has an
    opinion, its normalized form; it is never a gate on report creation
    and never appears in the response payload at all (ReportResponse has
    no postcode field)."""
    if not raw or not raw.strip():
        return None
    try:
        result = _validate_postcode(raw)
        normalized = result.get('normalized') if isinstance(result, dict) else None
        if normalized:
            return normalized
    except Exception:
        logger.warning("postcode normalization raised; falling back to strip+upper", exc_info=True)
    return raw.strip().upper()


def _share_url(token: str) -> str:
    return BASE_URL.rstrip('/') + SHARE_URL_PATH.format(token=token)


@functools.lru_cache(maxsize=1)
def _compute_frontend_bundle_hash() -> Optional[str]:
    """sha256 of static/index.html's bytes, truncated to 16 hex chars, or
    None if the file is absent (e.g. a backend-only checkout with no
    frontend build). lru_cache(maxsize=1) on a zero-arg function is the
    "compute lazily once, cache module-level" idiom: the file is a build
    artifact that cannot change during a running process's lifetime (a
    fresh deploy is a fresh process), so caching for the process lifetime
    is exactly right, not just an optimization."""
    try:
        with open("static/index.html", "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def build_version_info(app: FastAPI) -> Dict[str, Any]:
    """Payload for GET /api/version -- build/release identifiers for
    release-verification tooling (e.g. confirming a deploy actually
    shipped the expected backend commit + frontend bundle together)."""
    return {
        "backend_sha": os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_SHA") or "unknown",
        "frontend_bundle_hash": _compute_frontend_bundle_hash(),
        "contract_version": REPORT_CONTRACT_VERSION,
        "app_version": app.version,
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
            "rate_limited correlation_id=%s path=%s", correlation_id, request.url.path,
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
    or stack trace ever reaches the response body; the real traceback is
    logged server-side (exc_info=True) under the same correlation_id the
    client sees, so it's still fully debuggable from the logs.
    """
    try:
        return await coro
    except ReportAPIError:
        raise
    except Exception:
        correlation_id = _correlation_id()
        logger.error(
            "report_unhandled_exception correlation_id=%s path=%s",
            correlation_id, request.url.path, exc_info=True,
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

async def _replay_idempotent(idempotency_key: str, vrm_hash: str) -> Optional[ReportResponse]:
    """Look up a previously-created report by idempotency_key and return
    its stored payload verbatim, or None if there is nothing usable to
    replay (no row, no payload, Postgres unreachable, or -- the FLAGGED
    policy below -- a payload that no longer validates against the
    current contract).

    FLAGGED POLICY: if the stored report_payload fails
    ReportResponse.model_validate, this logs and returns None (i.e. "no
    hit") rather than raising -- the caller falls through to a fresh
    creation instead of erroring. Rationale: an idempotency replay
    failing to parse is a data/contract-drift problem, not something the
    original caller did wrong, and the honest, available alternative
    (recompute a fresh report) is strictly more useful to them than a 500.
    The trade-off is that a client relying on idempotency_key for
    exactly-once *identity* (not just exactly-once *effect*) could see a
    second report_token minted for what they consider the same logical
    request, in this one edge case. Given callers are not told this
    happened, this is worth a second look if idempotency replay is ever
    load-bearing for something stricter than "don't double-charge/double-
    submit" (its LIA-covered use here).
    """
    try:
        existing = await db.get_report_by_idempotency_key(idempotency_key)
    except db.PostgresUnavailable:
        logger.warning("idempotency_lookup_unavailable hash_vrm=%s", vrm_hash)
        return None  # Postgres down -> skip idempotency, proceed fresh (spec'd behaviour).

    if existing is None:
        return None

    payload = existing.get('report_payload')
    if payload is None:
        return None

    try:
        return ReportResponse.model_validate(payload)
    except ValidationError:
        logger.warning(
            "idempotency_replay_payload_invalid hash_vrm=%s report_id=%s",
            vrm_hash, existing.get('id'),
        )
        return None


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
        'model_version': 'lookup_v2',
        'prediction_source': assessment.prediction_source.value,
        'is_dvsa_data': vehicle_data_source == VehicleDataSource.DVSA,
        'referrer': request.headers.get('referer'),
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
        replayed = await _replay_idempotent(body.idempotency_key, vrm_hash)
        if replayed is not None:
            return replayed

    identity, history, vehicle_data_source = await _resolve_vehicle_and_history(vrm)

    assessment = await report_service.build_assessment(
        identity,
        history,
        mileage_user=body.mileage_user,
        sqlite_conn_factory=sqlite_conn_factory,
        now=None,
    )

    now_dt = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(16)
    report_uuid = uuid_module.uuid4()
    expires_dt = now_dt + timedelta(days=REPORT_TTL_DAYS)

    response = ReportResponse(
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
            replayed = await _replay_idempotent(body.idempotency_key, vrm_hash)
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
            "report_payload_invalid_on_fetch correlation_id=%s token_prefix=%s",
            _correlation_id(), token[:8],
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
            exc.error_code.value, exc.status_code, correlation_id, request.url.path,
        )
        envelope = ErrorEnvelope(error_code=exc.error_code, message=exc.message, correlation_id=correlation_id)
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode='json'))

    _ERROR_RESPONSES_POST = {
        400: {"model": ErrorEnvelope},
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
