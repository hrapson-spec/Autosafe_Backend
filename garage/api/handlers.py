"""Background job handlers. Each runs bound to Scope.system(job.workshop_id) and nothing else."""
from __future__ import annotations

import logging
from datetime import date

from . import matching
from .context import Ctx
from .dvsa import DvsaHistory, DvsaNotFound, DvsaPermanentError, DvsaTransientError
from .jobs import Defer, Done, Fail, Retry
from .provider import AssessmentRequest
from .retention import run_sweep
from .scope import Scope
from .security import sha256_json
from .timeutil import london_date, next_london_midnight

log = logging.getLogger("garage.worker")
BANDS = ("low", "medium", "high")
PROVIDER_STATES = ("available", "unavailable", "unsupported", "failed")


def dispatch(ctx: Ctx, job: dict):
    kind = job["kind"]
    if kind == "retention_sweep":
        return run_sweep(ctx)
    scope = Scope.system(job["workshop_id"])
    try:
        if kind == "assess_booking":
            return assess_booking(ctx, scope, job)
        if kind == "outcome_check":
            return outcome_check(ctx, scope, job)
    except ScopeViolation as e:
        # The payload names something this workshop does not own: refuse, touch nothing.
        log.error("job %s scope violation: %s", job["id"], e)
        return Fail(f"scope_violation:{e}")
    return Fail(f"unknown_kind:{kind}")


class ScopeViolation(Exception):
    pass


def _fetch(ctx: Ctx, registration: str):
    """('ok', history) | ('not_found', None) | ('transient', err) | ('permanent', err)."""
    try:
        return "ok", ctx.dvsa.fetch_history(registration)
    except DvsaNotFound:
        return "not_found", None
    except DvsaTransientError as e:
        return "transient", str(e)
    except DvsaPermanentError as e:
        return "permanent", str(e)


# --------------------------------------------------------------- assessment
def assess_booking(ctx: Ctx, scope: Scope, job: dict):
    now = ctx.clock()
    aid = (job["payload"] or {}).get("assessment_id")
    with ctx.scoped(scope) as s:
        a = s.get_assessment(aid, lock=True) if aid else None
        if a is None:
            raise ScopeViolation("assessment not in workshop")
        if a["state"] != "pending":
            return Done()
        b = s.get_booking(a["booking_id"])
        if b["status"] == "cancelled":
            s.complete_assessment(a["id"], "unavailable", "booking_cancelled")
            return Done()
        if not a["quota_counted"]:
            if not s.try_consume_quota(london_date(now), ctx.settings.fair_use_assessments_per_day):
                at = next_london_midnight(now)
                s.set_assessment_pending_reason(a["id"], "fair_use_deferred", None, at)
                return Defer(at)
            s.mark_assessment_quota_counted(a["id"])
        reg = b["registration"]
        booking_id = b["id"]

    status, res = _fetch(ctx, reg)          # network: no transaction held

    if status in ("transient", "permanent"):
        final = status == "permanent" or job["attempts"] >= job["max_attempts"]
        with ctx.scoped(scope) as s:
            if final:
                code = "upstream_error" if status == "permanent" else "upstream_unavailable"
                s.complete_assessment(a["id"], "failed", code)
                return Fail(f"{status}:{res}")
            s.set_assessment_pending_reason(a["id"], "upstream_retrying", None, None)
        return Retry(f"transient:{res}", ctx.settings.job_backoff_seconds)

    with ctx.scoped(scope) as s:
        if status == "not_found":
            snap = s.insert_snapshot(booking_id, "assessment_input", "not_found", None, sha256_json({"nf": reg}))
            s.complete_assessment(a["id"], "unsupported", "no_mot_history", snapshot_id=snap["id"])
            return Done()
        full: DvsaHistory = res
        hist_json = full.to_json()
        snap = s.insert_snapshot(booking_id, "assessment_input", "ok", hist_json, sha256_json(hist_json))
        prior = full.before(a["input_cutoff_date"], a["input_cutoff_at"])
        evidence = {"prior_test_count": len(prior.tests),
                    "latest_prior_test_date": prior.tests[-1].test_date.isoformat() if prior.tests else None,
                    "history_fetched_at": now.isoformat()}
        if not prior.tests:
            s.complete_assessment(a["id"], "unsupported", "no_prior_mot", evidence=evidence, snapshot_id=snap["id"])
            return Done()
        req = AssessmentRequest(registration=reg, booking_type=b["booking_type"],
                                appointment_date=b["appointment_date"], cutoff_date=a["input_cutoff_date"],
                                cutoff_at=a["input_cutoff_at"], history=prior)
        try:
            pr = ctx.provider.assess(req)
        except Exception:  # provider bugs must not crash the worker
            log.exception("provider failed")
            s.complete_assessment(a["id"], "failed", "provider_error", evidence=evidence, snapshot_id=snap["id"])
            return Done()
        err = _validate_provider(pr)
        if err:
            s.complete_assessment(a["id"], "failed", "invalid_provider_output", err, evidence=evidence,
                                  snapshot_id=snap["id"], provider_name=pr.provider_name,
                                  provider_version=pr.provider_version)
            return Done()
        result = ({"percent_rounded": pr.percent_rounded, "band": pr.band} if pr.state == "available" else None)
        s.complete_assessment(a["id"], pr.state, pr.reason, pr.reason_text, result, evidence, snap["id"],
                              pr.provider_name, pr.provider_version)
        return Done()


def _validate_provider(pr) -> str | None:
    if pr.state not in PROVIDER_STATES:
        return f"bad state {pr.state!r}"
    if pr.state == "available":
        if not isinstance(pr.percent_rounded, int) or not 0 <= pr.percent_rounded <= 100 or pr.percent_rounded % 5:
            return "percent must be an integer multiple of 5 in 0..100"
        if pr.band not in BANDS:
            return "band must be low/medium/high"
    elif pr.percent_rounded is not None or pr.band is not None:
        return "result fields only allowed when available"
    elif not pr.reason:
        return "a reason is required when not available"
    return None


# ------------------------------------------------------------------ outcomes
def outcome_check(ctx: Ctx, scope: Scope, job: dict):
    p = job["payload"] or {}
    try:
        day, sched = int(p["check_day"]), date.fromisoformat(p["schedule_date"])
        booking_id = p["booking_id"]
    except (KeyError, ValueError, TypeError):
        raise ScopeViolation("malformed payload")
    with ctx.scoped(scope) as s:
        b = s.get_booking(booking_id, lock=True)
        o = s.get_outcome(booking_id, day, sched, lock=True) if b else None
        if b is None or o is None:
            raise ScopeViolation("booking/outcome not in workshop")
        if o["status"] != "scheduled":
            return Done()
        if b["status"] == "cancelled" or b["appointment_date"] != sched:
            s.complete_outcome(o["id"], "cancelled", None, None,
                               "booking_cancelled" if b["status"] == "cancelled" else "rescheduled", None)
            return Done()
        if b["booking_type"] == "service":
            s.complete_outcome(o["id"], "not_applicable", None, None, "service_only_no_mot_expected", None)
            return Done()
        reg, appt, btype = b["registration"], b["appointment_date"], b["booking_type"]

    status, res = _fetch(ctx, reg)

    with ctx.scoped(scope) as s:
        if status in ("transient", "permanent"):
            if status == "transient" and job["attempts"] < job["max_attempts"]:
                return Retry(f"transient:{res}", ctx.settings.job_backoff_seconds)
            s.complete_outcome(o["id"], "failed", None, None, f"upstream_{status}", None)
            return Fail(f"{status}:{res}")
        if status == "not_found":
            snap = s.insert_snapshot(booking_id, "outcome_check", "not_found", None, sha256_json({"nf": reg}))
            s.complete_outcome(o["id"], "no_subsequent_test", None, [], "vehicle_not_found", snap["id"])
            return Done()
        hj = res.to_json()
        snap = s.insert_snapshot(booking_id, "outcome_check", "ok", hj, sha256_json(hj))
        st, matched, cands, detail = matching.match_outcome(res.tests, appt, btype, ctx.settings.outcome_window_days)
        s.complete_outcome(o["id"], st, matched, cands, detail, snap["id"])
        return Done()
