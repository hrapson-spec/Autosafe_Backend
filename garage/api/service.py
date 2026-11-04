"""Application services: imports, bookings, assessments, decisions, account management.

Every private read/write goes through `Ctx.scoped(scope)`.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from . import auth_store, briefing, importing, ranking, serialize
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


def _hydrate(ctx, s, rows: list[dict]) -> list[dict]:
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    a, d, o = s.latest_assessments(ids), s.latest_decisions(ids), s.latest_outcomes(ids)
    return [serialize.booking(r, a.get(r["id"]), d.get(r["id"]), o.get(r["id"]), allow_simulated=ctx.settings.allow_simulated_assessments) for r in rows]


# ------------------------------------------------------------------ imports
def _hhmm(t) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


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
                "appointment_time": _hhmm(r.appointment_time),
                "booking_type": r.booking_type, "existing_booking_id": None, "duplicate_of_row": None,
                "errors": [i.as_dict() for i in r.errors], "warnings": [i.as_dict() for i in r.warnings]}
        if not r.valid:
            item["status"] = "invalid"
        else:
            # Identity is registration + date + type. A different time on the same key is the same booking.
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
    parsed = [importing.validate_row(i + 1, c[0], c[1], c[2], london_date(now), s_, c[3]) for i, c in enumerate(raw)]
    with ctx.scoped(scope) as s:
        keys = [(p.registration, p.appointment_date, p.booking_type) for p in parsed if p.valid]
        items = _classify(parsed, s.find_existing_bookings(keys))
        counts = _counts(items)
        rec = s.create_import(fmt, items, counts, now + timedelta(minutes=s_.preview_ttl_minutes))
    return {"import_id": rec["id"], "status": "previewed", "counts": counts, "rows": items,
            "progress": None, "expires_at": london_iso(rec["expires_at"])}


def import_record(imp: dict, now: datetime, already_committed: bool | None = None) -> dict:
    """The one JSON shape for an import in every state (preview, committing, committed, failed, expired)."""
    expired = imp["status"] == "previewed" and imp["expires_at"] < now
    status = "expired" if expired else imp["status"]
    out = {"import_id": imp["id"], "status": status, "counts": imp["counts"], "rows": imp["rows"],
           "progress": ({"processed": imp["progress_processed"], "total": imp["progress_total"]}
                        if imp["progress_total"] is not None else None),
           "expires_at": london_iso(imp["expires_at"]), "committed_at": london_iso(imp["committed_at"])}
    if status == "committed" and imp["result"]:
        out["created_booking_ids"] = imp["result"]["created_booking_ids"]
    if status == "failed":
        out["failure_reason"] = "The import stopped before it finished. Rows already added are kept; commit again to continue."
    if already_committed is not None:
        out["already_committed"] = already_committed
    return out


def commit_import(ctx: Ctx, scope: Scope, import_id: str) -> tuple[dict, int]:
    """Queue the commit and return at once (202). The `import_commit` job creates the bookings in batches and
    GET /imports/{id} reports N of M. Idempotent: committing again never adds a booking twice."""
    now = ctx.clock()
    with ctx.scoped(scope) as s:
        imp = s.get_import(import_id, lock=True)
        if imp is None:
            raise not_found("import")
        if imp["status"] == "committed":
            return import_record(imp, now, already_committed=True), 200
        if imp["status"] == "committing":
            return import_record(imp, now, already_committed=False), 202
        if imp["status"] == "previewed" and imp["expires_at"] < now:
            raise ApiError(410, "import_expired", "This preview has expired; preview the rows again")
        total = sum(1 for r in imp["rows"] if r["status"] == "new")
        imp = s.start_import_commit(import_id, total, now)
        s.enqueue("import_commit", {"import_id": import_id}, dedupe_key=f"import:{import_id}",
                  max_attempts=ctx.settings.job_max_attempts)
        return import_record(imp, now, already_committed=False), 202


def import_commit_batch(ctx: Ctx, workshop_id: str, import_id: str) -> str:
    """One batch of the commit job (one transaction). Returns 'more', 'done' or 'skip'.
    Raises LookupError when the import is not in this workshop (the worker treats that as a scope violation)."""
    with ctx.scoped(Scope.system(workshop_id)) as s0:
        head = s0.get_import(import_id)
    if head is None:
        raise LookupError("import not in workshop")
    if head["status"] != "committing":
        return "skip"
    # Bookings are attributed to the member who pressed "commit".
    scope = Scope(workshop_id=workshop_id, membership_id=head["commit_membership_id"], user_id=None, email=None,
                  role="system")
    now = head["commit_requested_at"]          # the import time for the R10 cutoff, stable across retries
    with ctx.scoped(scope) as s:
        imp = s.get_import(import_id, lock=True)
        if imp["status"] != "committing":
            return "skip"
        s.lock_bookings_write()
        rows = [dict(r) for r in imp["rows"]]
        idx, processed = imp["progress_index"], imp["progress_processed"]
        created = list((imp["result"] or {}).get("created_booking_ids", []))
        handled = 0
        while idx < len(rows) and handled < ctx.settings.import_commit_batch_size:
            r = rows[idx]
            idx += 1
            if r["status"] != "new":
                continue
            handled += 1
            processed += 1
            d = date.fromisoformat(r["appointment_date"])
            # Re-check now: another import or manual entry may have landed since the preview.
            hit = [e for e in s.find_existing_bookings([(r["registration"], d, r["booking_type"])])
                   if d in (e["import_key_date"], e["appointment_date"])]
            if hit:
                r["status"], r["existing_booking_id"] = "duplicate_existing", hit[0]["id"]
                continue
            tm = time.fromisoformat(r["appointment_time"]) if r.get("appointment_time") else None
            b = s.insert_booking(r["registration"], d, r["booking_type"], "import", import_id, tm)
            if b is None:
                r["status"] = "duplicate_existing"
                continue
            created.append(b["id"])
            _plan_new_booking(ctx, s, b, "import", now)
        done = idx >= len(rows)
        counts = _counts(rows)
        result = {"import_id": import_id, "created_booking_ids": created}
        s.advance_import(import_id, rows, counts, idx, processed, result, done)
        return "done" if done else "more"


def get_import(ctx: Ctx, scope: Scope, import_id: str) -> dict:
    with ctx.scoped(scope) as s:
        imp = s.get_import(import_id)
        if imp is None:
            raise not_found("import")
    return import_record(imp, ctx.clock())


# ----------------------------------------------------------------- bookings
def create_booking(ctx: Ctx, scope: Scope, reg, d, typ, tm=None) -> tuple[dict, bool]:
    now = ctx.clock()
    row = importing.validate_row(1, reg, d, typ, london_date(now), ctx.settings, tm)
    if not row.valid:
        raise ApiError(422, "validation_error", "invalid booking",
                       {"errors": [i.as_dict() for i in row.errors]})
    with ctx.scoped(scope) as s:
        s.lock_bookings_write()
        for e in s.find_existing_bookings([(row.registration, row.appointment_date, row.booking_type)]):
            if row.appointment_date in (e["import_key_date"], e["appointment_date"]):
                return _hydrate(ctx, s, [s.get_booking(e["id"])])[0], False
        b = s.insert_booking(row.registration, row.appointment_date, row.booking_type, "manual",
                             appointment_time=row.appointment_time)
        _plan_new_booking(ctx, s, b, "manual_entry", now)
        return _hydrate(ctx, s, [b])[0], True


def list_bookings(ctx: Ctx, scope: Scope, **filters) -> dict:
    with ctx.scoped(scope) as s:
        rows, nxt = s.list_bookings(**filters)
        # Rank the complete filtered cohort before applying chronological pagination.
        # Detail endpoints intentionally carry no comparable rank keys.
        cohort, _ = s.list_bookings(**{**filters, "cursor": None, "limit": None})
        assessments = s.latest_assessments([b["id"] for b in cohort]) if cohort else {}
        scores = {}
        for b in cohort:
            a = assessments.get(b["id"])
            if a is None or b["status"] != "active" or b["booking_type"] not in SUPPORTED_FOR_ASSESSMENT:
                continue
            shown = serialize.assessment_json(a, allow_simulated=ctx.settings.allow_simulated_assessments)
            if shown["state"] == "available" and (shown["display_policy"] or {}).get("ranking_enabled") is True and a["score_unrounded"] is not None:
                scores[b["id"]] = a["score_unrounded"]
        keys = ranking.ordinal_keys(scores)
        items = _hydrate(ctx, s, rows)
        for b in items:
            if b["assessment"]:
                b["assessment"]["rank_key"] = keys.get(b["id"])
        return {"items": items, "next_cursor": nxt}


def get_booking(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        b = s.get_booking(booking_id)
        if b is None:
            raise not_found("booking")
        return _hydrate(ctx, s, [b])[0]


_UNSET = object()


def update_booking(ctx: Ctx, scope: Scope, booking_id: str, new_date: date | None, cancel: bool,
                   new_time=_UNSET) -> dict:
    """`new_time`: _UNSET leaves it alone; None clears it; a string sets it (HH:MM)."""
    now = ctx.clock()
    parsed_time = None
    if new_time is not _UNSET and new_time is not None:
        parsed_time, errs = importing.parse_time(new_time)
        if errs or parsed_time is None:
            raise ApiError(422, "validation_error", "invalid time", {"errors": [i.as_dict() for i in errs] or [
                {"code": "invalid_time", "field": "appointment_time", "message": "Use a 24-hour time such as 09:30"}]})
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
        if new_time is not _UNSET and parsed_time != b["appointment_time"]:
            old = b["appointment_time"]
            b = s.set_appointment_time(booking_id, parsed_time)
            s.add_event(booking_id, "rescheduled", {"time_from": _hhmm(old), "time_to": _hhmm(parsed_time)})
        if cancel:
            s.cancel_open_outcomes(booking_id, "booking_cancelled")
            b = s.cancel_booking(booking_id)
            s.add_event(booking_id, "cancelled")
        return _hydrate(ctx, s, [b])[0]


def list_assessments(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        return {"items": [serialize.assessment_json(a, allow_simulated=ctx.settings.allow_simulated_assessments) for a in s.list_assessments(booking_id)]}


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
            return serialize.assessment_json(a, allow_simulated=ctx.settings.allow_simulated_assessments)
        if s.usage(london_date(now)) >= ctx.settings.fair_use_assessments_per_day:
            raise ApiError(429, "fair_use_limit",
                           f"Fair-use limit of {ctx.settings.fair_use_assessments_per_day} assessments per day reached")
        cd, ca = _cutoff(now, b["appointment_date"])
        a = s.create_assessment(booking_id, "refresh", cd, ca)
        s.enqueue("assess_booking", {"assessment_id": a["id"]}, dedupe_key=f"assess:{a['id']}",
                  max_attempts=ctx.settings.job_max_attempts)
        return serialize.assessment_json(a, allow_simulated=ctx.settings.allow_simulated_assessments)


def list_outcomes(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        return {"items": [serialize.outcome_json(o) for o in s.list_outcomes(booking_id)]}


# ---------------------------------------------------------------- briefing
FEEDBACK_VALUES = ("helpful", "not_helpful")
FEEDBACK_REASONS = ("inaccurate_records", "not_relevant", "too_long", "missing_information", "other")


def _feedback_json(fb: dict | None, assessment_version: int | None = None) -> dict | None:
    if fb is None:
        return None
    return {"value": fb["value"], "reason_code": fb["reason_code"], "briefing_version": fb["briefing_version"],
            "assessment_version": assessment_version, "updated_at": london_iso(fb["updated_at"])}


def get_briefing(ctx: Ctx, scope: Scope, booking_id: str) -> dict:
    """Rule-generated briefing from the stored source snapshot of the newest completed assessment version.
    It does not depend on whether the model produced an estimate (or exists)."""
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        src = s.latest_briefing_source(booking_id)
        versions = s.list_assessments(booking_id)
        fb = s.get_feedback(booking_id, src["assessment_id"]) if src else None
    base = {"booking_id": booking_id, "rule_table_version": briefing.RULE_TABLE_VERSION, "reason": None,
            "reason_text": None, "assessment_id": None, "assessment_version": None, "briefing_version": None,
            "history_cutoff_date": None, "history_cutoff_at": None, "source": None, "summary": None,
            "source_records": [], "evidence_gaps": [], "follow_up_questions": [], "feedback": None}
    if src is None:
        a = versions[0] if versions else None
        pending = a is None or a["state"] == "pending"
        reason = a["reason"] if a else None
        return {**base, "status": "pending" if pending else "unavailable", "reason": reason,
                "reason_text": (a["reason_text"] or serialize.REASON_TEXT.get(reason)) if a else None}
    out = briefing.build(src["history"], src["upstream_status"], src["content_sha256"], src["input_cutoff_date"],
                         src["input_cutoff_at"])
    return {**base, **out, "status": "ready", "assessment_id": src["assessment_id"], "assessment_version": src["version"],
            "history_cutoff_at": london_iso(src["input_cutoff_at"]),
            "source": {"snapshot_sha256": src["content_sha256"], "upstream_status": src["upstream_status"],
                       "fetched_at": london_iso(src["fetched_at"])},
            "feedback": _feedback_json(fb, src["version"])}


def put_feedback(ctx: Ctx, scope: Scope, booking_id: str, value: str, reason_code: str | None,
                 briefing_version: str | None) -> dict:
    """One feedback row per member per booking version (assessment version); sending again replaces it."""
    if value not in FEEDBACK_VALUES or (reason_code is not None and reason_code not in FEEDBACK_REASONS) \
            or (reason_code is not None and value != "not_helpful"):
        raise ApiError(422, "validation_error", "invalid feedback")
    with ctx.scoped(scope) as s:
        if s.get_booking(booking_id) is None:
            raise not_found("booking")
        src = s.latest_briefing_source(booking_id)
        if src is None:
            raise ApiError(409, "briefing_not_ready", "There is no briefing to give feedback on yet")
        current = briefing.briefing_version(src["content_sha256"])
        if briefing_version is not None and briefing_version != current:
            raise ApiError(409, "briefing_version_changed",
                           "The briefing was regenerated; reload it before giving feedback")
        row = s.put_feedback(booking_id, src["assessment_id"], value, reason_code, current)
    return _feedback_json(row, src["version"])


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
