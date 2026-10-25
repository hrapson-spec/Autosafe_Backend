"""Application services: imports, bookings, assessments, decisions, account management.

Every private read/write goes through `Ctx.scoped(scope)`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import auth_store, importing, serialize
from .context import Ctx
from .errors import ApiError, not_found
from .scope import Scope
from .timeutil import london_at, london_date, london_iso

SUPPORTED_FOR_ASSESSMENT = ("mot", "service_mot")


# ------------------------------------------------------------------ helpers
def _cutoff(now: datetime, appointment: date) -> tuple[date, datetime]:
    """R10: inputs are tests strictly before min(import time, appointment date)."""
    return min(london_date(now), appointment), now


def usage_json(ctx: Ctx, s, now: datetime) -> dict:
    day = london_date(now)
    used = s.usage(day)
    lim = ctx.settings.fair_use_assessments_per_day
    return {"date": day.isoformat(), "assessments_used": used, "assessments_limit": lim,
            "assessments_remaining": max(lim - used, 0)}


def _plan_new_booking(ctx: Ctx, s, b: dict, trigger: str, now: datetime) -> None:
    """Initial assessment version + outcome schedule for a freshly created booking."""
    cutoff_date, cutoff_at = _cutoff(now, b["appointment_date"])
    if b["booking_type"] == "service":
        s.create_assessment(b["id"], trigger, cutoff_date, cutoff_at, state="unsupported", reason="service_only")
    elif b["booking_type"] == "retest":
        s.create_assessment(b["id"], trigger, cutoff_date, cutoff_at, state="unsupported", reason="retest_booking")
    else:
        a = s.create_assessment(b["id"], trigger, cutoff_date, cutoff_at)
        s.enqueue("assess_booking", {"assessment_id": a["id"]}, dedupe_key=f"assess:{a['id']}",
                  max_attempts=ctx.settings.job_max_attempts)
    _schedule_outcomes(ctx, s, b, now)
    s.add_event(b["id"], "created", {"source": b["source"]})


def _schedule_outcomes(ctx: Ctx, s, b: dict, now: datetime) -> None:
    for day in ctx.settings.outcome_check_days:
        due_date = b["appointment_date"] + timedelta(days=day)
        due_at = london_at(due_date, ctx.settings.outcome_hour_london)
        if b["booking_type"] == "service":
            o = s.schedule_outcome(b["id"], day, b["appointment_date"], due_at, status="not_applicable")
            if o:
                s.complete_outcome(o["id"], "not_applicable", None, None, "service_only_no_mot_expected", None)
            continue
        s.schedule_outcome(b["id"], day, b["appointment_date"], due_at)
        s.enqueue("outcome_check", {"booking_id": b["id"], "check_day": day,
                                    "schedule_date": b["appointment_date"].isoformat()},
                  run_at=due_at, dedupe_key=f"outcome:{b['id']}:{day}:{b['appointment_date'].isoformat()}",
                  max_attempts=ctx.settings.job_max_attempts)


def _hydrate(s, rows: list[dict]) -> list[dict]:
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    a, d, o = s.latest_assessments(ids), s.latest_decisions(ids), s.latest_outcomes(ids)
    return [serialize.booking(r, a.get(r["id"]), d.get(r["id"]), o.get(r["id"])) for r in rows]


# ------------------------------------------------------------------ imports
def _classify(rows: list[importing.ParsedRow], existing: list[dict]) -> list[dict]:
    seen: dict[tuple, int] = {}
    out = []
    ex_index: dict[tuple, str] = {}
    for e in existing:
        ex_index[(e["registration"], e["booking_type"], e["import_key_date"])] = e["id"]
        ex_index[(e["registration"], e["booking_type"], e["appointment_date"])] = e["id"]
    for r in rows:
        item = {"row_number": r.row_number, "registration": r.registration,
                "appointment_date": r.appointment_date.isoformat() if r.appointment_date else None,
                "booking_type": r.booking_type, "existing_booking_id": None, "duplicate_of_row": None,
                "errors": [i.as_dict() for i in r.errors], "warnings": [i.as_dict() for i in r.warnings]}
        if not r.valid:
            item["status"] = "invalid"
        else:
            key = (r.registration, r.booking_type, r.appointment_date)
            if key in ex_index:
                item["status"], item["existing_booking_id"] = "duplicate_existing", ex_index[key]
            elif key in seen:
                item["status"], item["duplicate_of_row"] = "duplicate_in_batch", seen[key]
            else:
                item["status"] = "new"
                seen[key] = r.row_number
        out.append(item)
    return out


def _counts(items: list[dict]) -> dict:
    c = {"total": len(items), "new": 0, "duplicate_existing": 0, "duplicate_in_batch": 0, "invalid": 0}
    for i in items:
        c[i["status"]] += 1
    return c


def preview_import(ctx: Ctx, scope: Scope, fmt: str, content, rows) -> dict:
    s_ = ctx.settings
    now = ctx.clock()
    try:
        raw = importing.raw_rows_from_request(fmt, content, rows, s_.max_import_rows)
    except importing.ImportFormatError as e:
        raise ApiError(422, e.code, str(e))
    parsed = [importing.validate_row(i + 1, *cells, london_date(now), s_) for i, cells in enumerate(raw)]
    with ctx.scoped(scope) as s:
        keys = [(p.registration, p.appointment_date, p.booking_type) for p in parsed if p.valid]
        items = _classify(parsed, s.find_existing_bookings(keys))
        counts = _counts(items)
        rec = s.create_import(fmt, items, counts, now + timedelta(minutes=s_.preview_ttl_minutes))
    return {"import_id": rec["id"], "status": "previewed", "counts": counts, "rows": items,
            "expires_at": london_iso(rec["expires_at"])}


def commit_import(ctx: Ctx, scope: Scope, import_id: str) -> dict:
    now = ctx.clock()
    with ctx.scoped(scope) as s:
        imp = s.get_import(import_id, lock=True)
        if imp is None:
            raise not_found("import")
        if imp["status"] == "committed":
            return {**imp["result"], "already_committed": True}
        if imp["expires_at"] < now:
            raise ApiError(410, "import_expired", "This preview has expired; preview the rows again")
        s.lock_bookings_write()
        # Re-check against the database now: another import may have landed since the preview.
        keys = [(r["registration"], date.fromisoformat(r["appointment_date"]), r["booking_type"])
                for r in imp["rows"] if r["status"] == "new"]
        existing = s.find_existing_bookings(keys)
        ex = {(e["registration"], e["booking_type"], d): e["id"] for e in existing
              for d in (e["import_key_date"], e["appointment_date"])}
        rows, created = [], []
        for r in imp["rows"]:
            r = dict(r)
            if r["status"] == "new":
                d = date.fromisoformat(r["appointment_date"])
                if (r["registration"], r["booking_type"], d) in ex:
                    r["status"], r["existing_booking_id"] = "duplicate_existing", ex[(r["registration"], r["booking_type"], d)]
                else:
                    b = s.insert_booking(r["registration"], d, r["booking_type"], "import", import_id)
                    if b is None:
                        r["status"] = "duplicate_existing"
                    else:
                        created.append(b["id"])
                        _plan_new_booking(ctx, s, b, "import", now)
            rows.append(r)
        counts = _counts(rows)
        result = {"import_id": import_id, "status": "committed", "counts": counts, "created_booking_ids": created,
                  "committed_at": london_iso(now)}
        s.mark_import_committed(import_id, rows, counts, result)
    return {**result, "already_committed": False}


def get_import(ctx: Ctx, scope: Scope, import_id: str) -> dict:
    with ctx.scoped(scope) as s:
        imp = s.get_import(import_id)
        if imp is None:
            raise not_found("import")
    expired = imp["status"] == "previewed" and imp["expires_at"] < ctx.clock()
    out = {"import_id": imp["id"], "status": "expired" if expired else imp["status"], "counts": imp["counts"],
           "rows": imp["rows"], "expires_at": london_iso(imp["expires_at"]),
           "committed_at": london_iso(imp["committed_at"])}
    if imp["result"]:
        out["created_booking_ids"] = imp["result"]["created_booking_ids"]
    return out


# ----------------------------------------------------------------- bookings
def create_booking(ctx: Ctx, scope: Scope, reg, d, typ) -> tuple[dict, bool]:
    now = ctx.clock()
    row = importing.validate_row(1, reg, d, typ, london_date(now), ctx.settings)
    if not row.valid:
        raise ApiError(422, "validation_error", "invalid booking",
                       {"errors": [i.as_dict() for i in row.errors]})
    with ctx.scoped(scope) as s:
        s.lock_bookings_write()
        for e in s.find_existing_bookings([(row.registration, row.appointment_date, row.booking_type)]):
            if row.appointment_date in (e["import_key_date"], e["appointment_date"]):
                return _hydrate(s, [s.get_booking(e["id"])])[0], False
        b = s.insert_booking(row.registration, row.appointment_date, row.booking_type, "manual")
        _plan_new_booking(ctx, s, b, "manual_entry", now)
        return _hydrate(s, [b])[0], True


def list_bookings(ctx: Ctx, scope: Scope, **filters) -> dict:
    with ctx.scoped(scope) as s:
        rows, nxt = s.list_bookings(**filters)
        return {"items": _hydrate(s, rows), "next_cursor": nxt}


def get_booking(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        b = s.get_booking(booking_id)
        if b is None:
            raise not_found("booking")
        return _hydrate(s, [b])[0]


def update_booking(ctx: Ctx, scope: Scope, booking_id: str, new_date: date | None, cancel: bool) -> dict:
    now = ctx.clock()
    with ctx.scoped(scope) as s:
        b = s.get_booking(booking_id, lock=True)
        if b is None:
            raise not_found("booking")
        if b["status"] == "cancelled":
            raise ApiError(409, "booking_cancelled", "Booking is cancelled")
        if new_date is not None:
            today = london_date(now)
            if new_date > today + timedelta(days=ctx.settings.max_days_ahead) or \
                    new_date < today - timedelta(days=ctx.settings.max_days_back):
                raise ApiError(422, "validation_error", "date out of range", {"errors": [
                    {"code": "date_out_of_range", "field": "appointment_date", "message": "date out of range"}]})
            if new_date != b["appointment_date"]:
                s.lock_bookings_write()
                s.cancel_open_outcomes(booking_id, "rescheduled")
                old = b["appointment_date"]
                b = s.reschedule_booking(booking_id, new_date)
                s.add_event(booking_id, "rescheduled", {"from": old.isoformat(), "to": new_date.isoformat()})
                _schedule_outcomes(ctx, s, b, now)
        if cancel:
            s.cancel_open_outcomes(booking_id, "booking_cancelled")
            b = s.cancel_booking(booking_id)
            s.add_event(booking_id, "cancelled")
        return _hydrate(s, [b])[0]


def list_assessments(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        return {"items": [serialize.assessment_json(a) for a in s.list_assessments(booking_id)]}


def refresh_assessment(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    now = ctx.clock()
    with ctx.scoped(scope) as s:
        b = s.get_booking(booking_id, lock=True)
        if b is None:
            raise not_found("booking")
        if b["status"] == "cancelled":
            raise ApiError(409, "booking_cancelled", "Booking is cancelled")
        latest = s.list_assessments(booking_id)
        if latest and latest[0]["state"] == "pending":
            raise ApiError(409, "assessment_pending", "An assessment is already pending")
        if b["booking_type"] not in SUPPORTED_FOR_ASSESSMENT:
            reason = "service_only" if b["booking_type"] == "service" else "retest_booking"
            cd, ca = _cutoff(now, b["appointment_date"])
            a = s.create_assessment(booking_id, "refresh", cd, ca, state="unsupported", reason=reason)
            return serialize.assessment_json(a)
        if s.usage(london_date(now)) >= ctx.settings.fair_use_assessments_per_day:
            raise ApiError(429, "fair_use_limit",
                           f"Fair-use limit of {ctx.settings.fair_use_assessments_per_day} assessments per day reached")
        cd, ca = _cutoff(now, b["appointment_date"])
        a = s.create_assessment(booking_id, "refresh", cd, ca)
        s.enqueue("assess_booking", {"assessment_id": a["id"]}, dedupe_key=f"assess:{a['id']}",
                  max_attempts=ctx.settings.job_max_attempts)
        return serialize.assessment_json(a)


def list_outcomes(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        return {"items": [serialize.outcome_json(o) for o in s.list_outcomes(booking_id)]}


# ---------------------------------------------------------------- decisions
def record_decision(ctx: Ctx, scope: Scope, booking_id: str, decision_type: str, note: str | None) -> dict:
    with ctx.scoped(scope) as s:
        b = s.get_booking(booking_id, lock=True)
        if b is None:
            raise not_found("booking")
        prev = s.list_decisions(booking_id)
        latest_a = s.list_assessments(booking_id)
        d = s.insert_decision(booking_id, decision_type, note.strip() if note and note.strip() else None,
                              latest_a[0]["id"] if latest_a else None, prev[0]["id"] if prev else None)
        return serialize.decision_json(s.get_decision(d["id"]))


def list_decisions(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        return {"items": [serialize.decision_json(d) for d in s.list_decisions(booking_id)]}


# ------------------------------------------------------------------ account
def link_url(ctx: Ctx, token: str) -> str:
    return f"{ctx.settings.base_url}/api/garage/v1/auth/links/{token}"


def send_link(ctx: Ctx, email: str, token: str, subject: str = "Your AutoSafe Garage sign-in link") -> None:
    mins = ctx.settings.link_ttl_minutes
    text = (f"Use this link to sign in to AutoSafe Garage:\n\n{link_url(ctx, token)}\n\n"
            f"It works once and expires in {mins} minutes. If you did not ask for it, ignore this email.")
    ctx.mailer.send(email, subject, text)


def add_staff(ctx: Ctx, scope: Scope, email: str) -> dict:
    with ctx.scoped(scope) as s:
        m = s.add_staff(email)
        if m is None:
            raise ApiError(409, "already_member", "Already a member of this workshop")
        token = auth_store.issue_login_token_for(s.cur, m["user_id"], scope.workshop_id, ctx.settings.link_ttl_minutes)
    send_link(ctx, email, token, "You have been added to AutoSafe Garage")
    return _member_json(m)


def _member_json(m: dict) -> dict:
    return {"membership_id": m["membership_id"], "email": m["email"], "role": m["role"], "status": m["status"],
            "created_at": london_iso(m["created_at"])}


def list_members(ctx: Ctx, scope: Scope) -> dict:
    with ctx.scoped(scope) as s:
        return {"items": [_member_json(m) for m in s.list_members()]}


def remove_member(ctx: Ctx, scope: Scope, membership_id: str) -> None:
    with ctx.scoped(scope) as s:
        m = s.get_member(membership_id)
        if m is None or m["status"] != "active":
            raise not_found("member")
        if m["role"] == "owner":
            raise ApiError(409, "cannot_remove_owner", "The workshop owner cannot be removed")
        s.remove_member(membership_id)
