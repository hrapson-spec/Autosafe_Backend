"""First-party acquisition collector (OA-005, DECISIONS.md D-005).

``POST /api/acquisition/events`` receives the cookieless, identity-free
measurement events defined by docs/acquisition/EVENT_SCHEMA_v1.md plus the
D-005 envelope fields (session_id, landing_id, page_family, source_group,
release_sha). It is OFF unless ``ACQUISITION_INGEST_ENABLED`` is truthy in
the server environment, and the browser transport is separately OFF behind
``ACQUISITION_COLLECTOR_ENABLED`` in utils/acquisitionEvents.ts.

Privacy rules enforced here (D-005):

* The body is parsed by hand against strict models (``extra='forbid'``), so a
  rejected body is never echoed or logged and FastAPI's default 422 (which
  echoes the offending input) is never produced.
* The server derives only ``received_at`` and ``is_bot``. The User-Agent is
  read to compute ``is_bot`` and is not stored; the client IP is used by the
  in-memory rate limiter only; Referer, query string and URL are never read.
* Nothing from the body (no event, session, landing or operation id) is ever
  logged; logs carry fixed reason codes and exception type names only.
* Responses are ``Cache-Control: no-store``.

Failure semantics: the client ignores the response. A recording failure
returns 503 quickly (bounded by ``RECORD_TIMEOUT_SECONDS`` plus a short
circuit breaker) rather than 202, so the transport's single retry with the
same ``event_id`` can succeed if the store recovers, while a transient outage
is not reported as a successful write. Either way it never touches other
routes: the work is a single awaited store call with its own timeout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Annotated, Any, Literal, Optional, Union

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, TypeAdapter,
    ValidationError, model_validator,
)
from starlette.requests import Request

import acquisition_store as store_mod
from acquisition_store import AcquisitionRecord

logger = logging.getLogger(__name__)

ROUTE_PATH = "/api/acquisition/events"
MAX_BODY_BYTES = 2048
RATE_LIMIT = "120/minute"
RECORD_TIMEOUT_SECONDS = 2.0
BREAKER_SECONDS = 10.0

# ---------------------------------------------------------------------------
# Enums (docs/acquisition/event_schema_v1.json + D-004/D-005)
# ---------------------------------------------------------------------------

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
Uuid = Annotated[str, StringConstraints(pattern=UUID_PATTERN)]
MetricVersion = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{0,31}$")]
ReleaseSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")]



def _must_be_true(v: bool) -> bool:
    if v is not True:
        raise ValueError("must be true")
    return v


def _must_be_one(v: int) -> int:
    if v != 1:
        raise ValueError("must be 1")
    return v


# Literal[1]/Literal[True] would also accept True/1 (Python equality); these
# strict forms reject the wrong JSON type.
SchemaVersion = Annotated[StrictInt, AfterValidator(_must_be_one)]
AlwaysTrue = Annotated[StrictBool, AfterValidator(_must_be_true)]

PageFamily = Literal[
    "home", "app", "guide", "make", "model", "comparison", "pillar", "problem_hub", "other_public"
]
# `internal` is a same-site referrer (never an organic landing). `paid_search`
# is a landing whose URL carried a paid-click marker (D-006; the marker is never
# sent). `internal_test` is synthetic/test traffic. Schema v1 had only the latter; D-005 adds the
# landing-page referrer classes.
SourceGroup = Literal[
    "google_organic", "other_search", "direct", "referral", "unknown", "internal", "paid_search", "internal_test"
]
ObservationState = Literal["observed", "consent_not_given", "unsupported"]
EntryMode = Literal["fresh_check", "restored_link"]
PersistenceMode = Literal["saved", "inline_unsaved"]
ResultKind = Literal["comparison", "vehicle_prediction"]
MatchScope = Literal[
    "exact_band", "age_band_only", "model_average", "population_default", "unavailable", "model_prediction"
]
RenderedMatchScope = Literal["exact_band", "age_band_only", "model_average", "population_default", "model_prediction"]
RenderedOutcomeGroup = Literal[
    "prediction", "exact_comparison", "broader_supported_comparison", "dataset_reference", "demo"
]
UnavailableReason = Literal["not_found", "expired", "unavailable", "error"]
RenderFailedStage = Literal["lazy_load", "render", "contract_invalid"]
CheckErrorCategory = Literal[
    "invalid_registration", "vehicle_not_found", "dvsa_unavailable", "rate_limited", "internal_error",
    "report_not_found", "report_expired", "storage_unavailable", "idempotency_conflict",
    "undeclared_parameter", "network_error", "invalid_response", "unknown",
]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: SchemaVersion
    metric_version: MetricVersion
    event_id: Uuid
    session_id: Uuid
    landing_id: Optional[Uuid] = None
    page_family: PageFamily
    source_group: SourceGroup
    release_sha: Optional[ReleaseSha] = None


class LandingObserved(_Event):
    event: Literal["landing_observed"]
    observation_state: ObservationState

    @model_validator(mode="after")
    def _needs_landing_id(self):
        if self.landing_id is None:
            raise ValueError("landing_id required")
        return self


class CheckStarted(_Event):
    event: Literal["check_started"]
    operation_id: Uuid
    entry_mode: Literal["fresh_check"]


class ReportCreated(_Event):
    event: Literal["report_created"]
    operation_id: Uuid
    entry_mode: Literal["fresh_check"]
    result_kind: ResultKind
    match_scope: MatchScope
    persistence_mode: PersistenceMode

    @model_validator(mode="after")
    def _prediction_pairing(self):
        if (self.result_kind == "vehicle_prediction") != (self.match_scope == "model_prediction"):
            raise ValueError("result_kind/match_scope mismatch")
        return self


class ResultRendered(_Event):
    event: Literal["result_rendered"]
    operation_id: Optional[Uuid] = None
    entry_mode: EntryMode
    persistence_mode: PersistenceMode
    render_delivered: AlwaysTrue
    supported_result: bool
    outcome_group: RenderedOutcomeGroup
    rate_valid: bool
    sample_nonzero: Optional[bool] = None
    scope_visible: bool
    result_kind: ResultKind
    match_scope: RenderedMatchScope

    @model_validator(mode="after")
    def _combinations(self):
        # Mirrors the allOf rules of docs/acquisition/event_schema_v1.json;
        # tests/test_acquisition_collector.py checks parity over a full grid.
        o, k, m = self.outcome_group, self.result_kind, self.match_scope
        has_sample = self.sample_nonzero is not None
        if m == "model_prediction":
            if o not in ("prediction", "demo") or has_sample:
                raise ValueError("combination")
        if k == "vehicle_prediction" and m != "model_prediction":
            raise ValueError("combination")
        if o == "prediction":
            if not (k == "vehicle_prediction" and m == "model_prediction" and self.supported_result
                    and self.rate_valid and self.scope_visible and not has_sample):
                raise ValueError("combination")
        elif o == "exact_comparison":
            if not (k == "comparison" and m == "exact_band" and self.rate_valid and has_sample):
                raise ValueError("combination")
        elif o == "broader_supported_comparison":
            if not (k == "comparison" and m in ("age_band_only", "model_average")
                    and self.rate_valid and has_sample):
                raise ValueError("combination")
        elif o == "dataset_reference":
            if not (k == "comparison" and m == "population_default"
                    and self.supported_result is False and has_sample):
                raise ValueError("combination")
        elif o == "demo":
            if self.supported_result is not False:
                raise ValueError("combination")
            if k == "comparison" and not has_sample:
                raise ValueError("combination")
        if k == "comparison" and m in ("exact_band", "age_band_only", "model_average", "population_default"):
            if not has_sample:
                raise ValueError("combination")
        if self.supported_result:
            if not (self.rate_valid and self.scope_visible
                    and o in ("prediction", "exact_comparison", "broader_supported_comparison")):
                raise ValueError("combination")
        if o in ("exact_comparison", "broader_supported_comparison") and self.scope_visible:
            if not (self.supported_result and self.sample_nonzero is True):
                raise ValueError("combination")
        return self


class ResultUnavailable(_Event):
    event: Literal["result_unavailable"]
    operation_id: Optional[Uuid] = None
    entry_mode: EntryMode
    reason: UnavailableReason


class CheckFailed(_Event):
    event: Literal["check_failed"]
    operation_id: Uuid
    entry_mode: Literal["fresh_check"]
    error_category: CheckErrorCategory
    stage: Literal["create_report"]


class RenderFailed(_Event):
    event: Literal["render_failed"]
    operation_id: Optional[Uuid] = None
    entry_mode: EntryMode
    stage: RenderFailedStage


AcquisitionEventModel = Annotated[
    Union[LandingObserved, CheckStarted, ReportCreated, ResultRendered, ResultUnavailable, CheckFailed, RenderFailed],
    Field(discriminator="event"),
]
_EVENT_ADAPTER: TypeAdapter = TypeAdapter(AcquisitionEventModel)


def parse_event(payload: Any) -> BaseModel:
    """Validate a decoded JSON value. Raises ValidationError on any deviation."""
    return _EVENT_ADAPTER.validate_python(payload)


# ---------------------------------------------------------------------------
# Server-derived fields
# ---------------------------------------------------------------------------

# Conservative: only clear automation/crawler/preview/monitoring signatures.
# Real browsers (including mobile and in-app webviews) must not match. An
# absent User-Agent is treated as a bot: every real browser sends one.
_BOT_RE = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|headless|lighthouse|pagespeed|gtmetrix|pingdom|uptime|"
    r"monitor|facebookexternalhit|embedly|curl/|wget/|python-requests|python-urllib|aiohttp|httpx|"
    r"go-http-client|java/|okhttp/|libwww|scrapy|phantomjs|selenium|playwright|puppeteer|"
    r"node-fetch|axios/|postmanruntime|http_request2|apache-httpclient",
    re.IGNORECASE,
)


def is_bot_user_agent(user_agent: Optional[str]) -> bool:
    if not user_agent or not user_agent.strip():
        return True
    return _BOT_RE.search(user_agent) is not None


def ingest_enabled() -> bool:
    return os.environ.get("ACQUISITION_INGEST_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _record_from_event(event: BaseModel, received_at, is_bot: bool) -> AcquisitionRecord:
    data = event.model_dump()
    data["received_at"] = received_at
    data["is_bot"] = is_bot
    return AcquisitionRecord(**data)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

_breaker_until = 0.0
_store_override = None
_store_cache: dict = {}


def _get_store():
    """Resolve the store lazily so tests can set the environment per test."""
    if _store_override is not None:
        return _store_override
    key = (os.environ.get("ACQUISITION_SQLITE_PATH"), bool(os.environ.get("DATABASE_URL")))
    if key not in _store_cache:
        _store_cache.clear()
        _store_cache[key] = store_mod.select_store()
    return _store_cache[key]


def set_store_for_tests(store) -> None:
    global _store_override, _breaker_until
    _store_override = store
    _breaker_until = 0.0
    _store_cache.clear()


async def _ensure_schema_once(store) -> None:
    """Create the tables on first use of this store object (CREATE ... IF NOT
    EXISTS, so concurrent workers are safe). The flag lives on the store
    object itself: keying a module-level cache by id(store) would be wrong
    once an object is freed and its address reused."""
    if getattr(store, "_acquisition_schema_ready", False):
        return
    await store.ensure_schema()
    store._acquisition_schema_ready = True


def _json(status: int, body: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers={"Cache-Control": "no-store"})


def _reject(status: int, reason: str) -> JSONResponse:
    # Fixed reason codes only: nothing from the body or headers is logged.
    logger.info("acquisition_event_rejected reason=%s", reason)
    return _json(status, {"detail": reason})


async def _read_limited_body(request: Request) -> Optional[bytes]:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                return None
        except ValueError:
            return b"\xff"  # not valid JSON -> 400
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def register_acquisition_routes(app: FastAPI, limiter) -> None:
    @app.middleware("http")
    async def _acquisition_no_store(request, call_next):
        response = await call_next(request)
        if request.url.path == ROUTE_PATH:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post(
        ROUTE_PATH,
        status_code=202,
        include_in_schema=True,
        summary="Record one cookieless acquisition measurement event",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "object", "additionalProperties": False}}},
                "description": "Strictly validated; see docs/acquisition/COLLECTOR.md. Max 2048 bytes.",
            }
        },
        responses={
            202: {"description": "Accepted or duplicate (idempotent on event_id)."},
            400: {"description": "Malformed or non-conforming event."},
            404: {"description": "Collector not enabled on this server."},
            413: {"description": "Body larger than 2048 bytes."},
            429: {"description": "Rate limited."},
            503: {"description": "Event could not be recorded; the client may retry once."},
        },
    )
    @limiter.limit(RATE_LIMIT)
    async def record_acquisition_event(request: Request):
        global _breaker_until
        if not ingest_enabled():
            return _json(404, {"detail": "Not Found"})
        # Global Privacy Control: honour the objection server-side too.
        if request.headers.get("sec-gpc", "").strip() == "1":
            return _json(202, {"status": "accepted"})

        body = await _read_limited_body(request)
        if body is None:
            return _reject(413, "payload_too_large")
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return _reject(400, "invalid_content_type")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return _reject(400, "invalid_json")
        if not isinstance(payload, dict):
            return _reject(400, "invalid_event")
        try:
            event = parse_event(payload)
        except ValidationError:
            return _reject(400, "invalid_event")

        store = _get_store()
        now = time.monotonic()
        if store is None or now < _breaker_until:
            return _json(503, {"detail": "unavailable"})

        record = _record_from_event(
            event,
            store_mod.utcnow(),
            is_bot_user_agent(request.headers.get("user-agent")),
        )
        try:
            async def _write():
                await _ensure_schema_once(store)
                return await store.insert_event(record)

            await asyncio.wait_for(_write(), timeout=RECORD_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - log the type name only
            _breaker_until = time.monotonic() + BREAKER_SECONDS
            logger.error("acquisition_record_failed exception_type=%s", type(exc).__name__)
            return _json(503, {"detail": "unavailable"})
        return _json(202, {"status": "accepted"})


# ---------------------------------------------------------------------------
# Background retention (startup + daily); never blocks startup or /health
# ---------------------------------------------------------------------------

STARTUP_DELAY_SECONDS = 20.0
DAILY_SECONDS = 24 * 3600.0
RETRY_AFTER_FAILURE_SECONDS = 3600.0

_retention_task: Optional["asyncio.Task[None]"] = None


async def run_retention_once(store=None, now=None) -> Optional[store_mod.RetentionResult]:
    """One idempotent retention pass. Safe to call from any worker, any time."""
    store = store or _get_store()
    if store is None:
        return None
    await _ensure_schema_once(store)
    result = await store.run_retention(now or store_mod.utcnow())
    logger.info(
        "acquisition_retention skipped_locked=%s rolled_up=%d deleted_raw=%d deleted_aggregate_rows=%d",
        result.skipped_locked, result.rolled_up_events, result.deleted_raw_events, result.deleted_aggregate_rows,
    )
    return result


async def _retention_loop(startup_delay: float, daily: float, retry: float) -> None:
    await asyncio.sleep(startup_delay)
    while True:
        try:
            await run_retention_once()
            delay = daily
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("acquisition_retention_failed exception_type=%s", type(exc).__name__)
            delay = retry
        await asyncio.sleep(delay)


def start_background_retention(
    startup_delay: float = STARTUP_DELAY_SECONDS,
    daily: float = DAILY_SECONDS,
    retry: float = RETRY_AFTER_FAILURE_SECONDS,
) -> Optional["asyncio.Task[None]"]:
    """Schedule the retention loop as a background task. Returns None (and
    touches nothing) unless the server-side ingest flag is on, so a
    deployment with the collector disabled never creates tables or runs DDL."""
    global _retention_task
    if not ingest_enabled():
        return None
    if _retention_task is not None and not _retention_task.done():
        return _retention_task
    _retention_task = asyncio.get_running_loop().create_task(_retention_loop(startup_delay, daily, retry))
    return _retention_task


async def stop_background_retention() -> None:
    global _retention_task
    task, _retention_task = _retention_task, None
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
