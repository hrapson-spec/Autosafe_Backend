"""All queries on workshop-private tables. Every statement is filtered by the scope's workshop_id.

This is the only module (with retention.py's system sweep) allowed to name the private tables; a test
scans the package to keep it that way.
"""
from __future__ import annotations

import base64
from datetime import date, datetime

from psycopg2.extras import Json

from .scope import Scope


class Scoped:
    def __init__(self, cur, scope: Scope):
        if not isinstance(scope, Scope):
            raise TypeError("Scoped requires a Scope")
        self.cur = cur
        self.scope = scope
        self.wid = scope.workshop_id

    # ------------------------------------------------------------ helpers
    def _one(self, sql: str, args: tuple = ()):
        self.cur.execute(sql, args)
        return self.cur.fetchone()

    def _all(self, sql: str, args: tuple = ()):
        self.cur.execute(sql, args)
        return self.cur.fetchall()

    # ----------------------------------------------------------- workshop
    def get_workshop(self):
        return self._one("SELECT id, name FROM workshops WHERE id = %s", (self.wid,))

    def rename_workshop(self, name: str):
        return self._one("UPDATE workshops SET name = %s WHERE id = %s RETURNING id, name", (name, self.wid))

    def usage(self, day: date, kind: str = "assessment") -> int:
        r = self._one("SELECT count FROM usage_counters WHERE workshop_id = %s AND usage_date = %s AND kind = %s",
                      (self.wid, day, kind))
        return r["count"] if r else 0

    def try_consume_quota(self, day: date, limit: int, kind: str = "assessment") -> bool:
        """Atomically take one unit of today's allowance; False when exhausted."""
        r = self._one(
            """INSERT INTO usage_counters (workshop_id, usage_date, kind, count) VALUES (%s, %s, %s, 1)
               ON CONFLICT (workshop_id, usage_date, kind)
               DO UPDATE SET count = usage_counters.count + 1
                  WHERE usage_counters.count < %s
               RETURNING count""", (self.wid, day, kind, limit))
        return r is not None

    # ------------------------------------------------------------ members
    def memberships_for_user(self, user_id: str):
        return self._all("SELECT id FROM memberships WHERE workshop_id = %s AND user_id = %s", (self.wid, user_id))

    def list_members(self):
        return self._all(
            """SELECT m.id AS membership_id, u.email, m.role, m.status, m.created_at
                 FROM memberships m JOIN users u ON u.id = m.user_id
                WHERE m.workshop_id = %s AND m.status = 'active' ORDER BY m.created_at, m.id""", (self.wid,))

    def get_member(self, membership_id: str):
        return self._one(
            """SELECT m.id AS membership_id, m.user_id, u.email, m.role, m.status, m.created_at
                 FROM memberships m JOIN users u ON u.id = m.user_id
                WHERE m.workshop_id = %s AND m.id = %s""", (self.wid, membership_id))

    def add_staff(self, email: str):
        """Find-or-create the user, then add/reactivate a staff membership. Returns (member, created)."""
        u = self._one("INSERT INTO users (email) VALUES (%s) ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email "
                      "RETURNING id", (email,))
        existing = self._one("SELECT id, status FROM memberships WHERE workshop_id = %s AND user_id = %s",
                             (self.wid, u["id"]))
        if existing and existing["status"] == "active":
            return None
        if existing:
            self.cur.execute("UPDATE memberships SET status = 'active', removed_at = NULL, role = 'staff' "
                             "WHERE workshop_id = %s AND id = %s", (self.wid, existing["id"]))
            mid = existing["id"]
        else:
            mid = self._one("INSERT INTO memberships (workshop_id, user_id, role) VALUES (%s, %s, 'staff') "
                            "RETURNING id", (self.wid, u["id"]))["id"]
        return self.get_member(mid)

    def remove_member(self, membership_id: str) -> None:
        self.cur.execute("UPDATE memberships SET status = 'removed', removed_at = now() "
                         "WHERE workshop_id = %s AND id = %s", (self.wid, membership_id))
        self.cur.execute("UPDATE sessions SET revoked_at = now(), revoked_reason = 'member_removed' "
                         "WHERE workshop_id = %s AND membership_id = %s AND revoked_at IS NULL",
                         (self.wid, membership_id))

    def create_login_token(self, user_id: str, token_hash: str, expires_at: datetime):
        self.cur.execute("INSERT INTO login_tokens (token_hash, user_id, workshop_id, expires_at) "
                         "VALUES (%s, %s, %s, %s)", (token_hash, user_id, self.wid, expires_at))

    # ----------------------------------------------------------- sessions
    def list_sessions(self, all_members: bool):
        sql = """SELECT s.id, s.membership_id, u.email, s.created_at, s.last_seen_at, s.expires_at
                   FROM sessions s JOIN users u ON u.id = s.user_id
                  WHERE s.workshop_id = %s AND s.revoked_at IS NULL AND s.expires_at > now()"""
        args: tuple = (self.wid,)
        if not all_members:
            sql += " AND s.membership_id = %s"
            args += (self.scope.membership_id,)
        return self._all(sql + " ORDER BY s.created_at DESC, s.id", args)

    def revoke_session(self, session_id: str, all_members: bool) -> bool:
        sql = ("UPDATE sessions SET revoked_at = now(), revoked_reason = 'user' WHERE workshop_id = %s AND id = %s "
               "AND revoked_at IS NULL")
        args: tuple = (self.wid, session_id)
        if not all_members:
            sql += " AND membership_id = %s"
            args += (self.scope.membership_id,)
        self.cur.execute(sql + " RETURNING id", args)
        return self.cur.fetchone() is not None

    # ------------------------------------------------------------ imports
    def create_import(self, fmt: str, rows: list, counts: dict, expires_at: datetime):
        return self._one(
            """INSERT INTO imports (workshop_id, membership_id, format, rows, counts, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (self.wid, self.scope.membership_id, fmt, Json(rows), Json(counts), expires_at))

    def get_import(self, import_id: str, lock: bool = False):
        return self._one("SELECT * FROM imports WHERE workshop_id = %s AND id = %s" + (" FOR UPDATE" if lock else ""),
                         (self.wid, import_id))

    def mark_import_committed(self, import_id: str, rows: list, counts: dict, result: dict):
        return self._one(
            """UPDATE imports SET status = 'committed', rows = %s, counts = %s, result = %s, committed_at = now()
                WHERE workshop_id = %s AND id = %s RETURNING *""",
            (Json(rows), Json(counts), Json(result), self.wid, import_id))

    def lock_bookings_write(self) -> None:
        """Serialise booking creation per workshop (import commit / manual entry)."""
        self.cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("bookings:" + self.wid,))

    # ----------------------------------------------------------- bookings
    def find_existing_bookings(self, keys: list[tuple]):
        """keys: (registration, date, type). A booking matches on its import date or its current date."""
        if not keys:
            return []
        return self._all(
            """SELECT b.id, b.registration, b.booking_type, b.import_key_date, b.appointment_date
                 FROM bookings b
                WHERE b.workshop_id = %s AND (b.registration, b.booking_type) IN
                      (SELECT k.r, k.t FROM unnest(%s::text[], %s::text[]) AS k(r, t))""",
            (self.wid, [k[0] for k in keys], [k[2] for k in keys]))

    def insert_booking(self, registration, appointment_date, booking_type, source, import_id=None):
        return self._one(
            """INSERT INTO bookings (workshop_id, registration, appointment_date, import_key_date, booking_type,
                                     source, import_id, created_by_membership_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (workshop_id, registration, import_key_date, booking_type) DO NOTHING
               RETURNING *""",
            (self.wid, registration, appointment_date, appointment_date, booking_type, source, import_id,
             self.scope.membership_id))

    def get_booking(self, booking_id: str, lock: bool = False):
        return self._one("SELECT * FROM bookings WHERE workshop_id = %s AND id = %s" + (" FOR UPDATE" if lock else ""),
                         (self.wid, booking_id))

    def get_booking_by_key(self, registration, import_key_date, booking_type):
        return self._one("SELECT * FROM bookings WHERE workshop_id = %s AND registration = %s AND "
                         "import_key_date = %s AND booking_type = %s",
                         (self.wid, registration, import_key_date, booking_type))

    def add_event(self, booking_id: str, event: str, detail: dict | None = None):
        self.cur.execute("INSERT INTO booking_events (workshop_id, booking_id, event, detail, membership_id) "
                         "VALUES (%s, %s, %s, %s, %s)",
                         (self.wid, booking_id, event, Json(detail or {}), self.scope.membership_id))

    def reschedule_booking(self, booking_id: str, new_date: date):
        return self._one("UPDATE bookings SET appointment_date = %s, rescheduled_at = now() "
                         "WHERE workshop_id = %s AND id = %s RETURNING *", (new_date, self.wid, booking_id))

    def cancel_booking(self, booking_id: str):
        return self._one("UPDATE bookings SET status = 'cancelled', cancelled_at = now() "
                         "WHERE workshop_id = %s AND id = %s RETURNING *", (self.wid, booking_id))

    def list_bookings(self, date_from=None, date_to=None, booking_type=None, status=None,
                      assessment_state=None, limit=50, cursor: str | None = None):
        where, args = ["b.workshop_id = %s"], [self.wid]
        if date_from:
            where.append("b.appointment_date >= %s"); args.append(date_from)
        if date_to:
            where.append("b.appointment_date <= %s"); args.append(date_to)
        if booking_type:
            where.append("b.booking_type = %s"); args.append(booking_type)
        if status:
            where.append("b.status = %s"); args.append(status)
        if assessment_state:
            where.append("(SELECT a.state FROM assessments a WHERE a.workshop_id = b.workshop_id AND "
                         "a.booking_id = b.id ORDER BY a.version DESC LIMIT 1) = %s")
            args.append(assessment_state)
        if cursor:
            d, i = decode_cursor(cursor)
            where.append("(b.appointment_date, b.id) > (%s, %s)"); args += [d, i]
        rows = self._all(f"SELECT b.* FROM bookings b WHERE {' AND '.join(where)} "
                         "ORDER BY b.appointment_date, b.id LIMIT %s", tuple(args) + (limit + 1,))
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = encode_cursor(rows[-1]["appointment_date"], rows[-1]["id"])
        return rows, next_cursor

    def latest_assessments(self, booking_ids: list[str]) -> dict:
        rows = self._all("SELECT DISTINCT ON (booking_id) * FROM assessments WHERE workshop_id = %s AND "
                         "booking_id = ANY(%s::uuid[]) ORDER BY booking_id, version DESC", (self.wid, booking_ids))
        return {r["booking_id"]: r for r in rows}

    def latest_decisions(self, booking_ids: list[str]) -> dict:
        rows = self._all(self._decision_sql("AND d.booking_id = ANY(%s::uuid[])", distinct=True),
                         (self.wid, booking_ids))
        return {r["booking_id"]: r for r in rows}

    def latest_outcomes(self, booking_ids: list[str]) -> dict:
        rows = self._all("SELECT DISTINCT ON (o.booking_id) o.* FROM outcomes o JOIN bookings b "
                         "ON b.workshop_id = o.workshop_id AND b.id = o.booking_id AND b.appointment_date = o.schedule_date "
                         "WHERE o.workshop_id = %s AND o.booking_id = ANY(%s::uuid[]) AND o.checked_at IS NOT NULL "
                         "AND o.status <> 'cancelled' ORDER BY o.booking_id, o.check_day DESC, o.checked_at DESC",
                         (self.wid, booking_ids))
        return {r["booking_id"]: r for r in rows}

    # -------------------------------------------------------- assessments
    def next_version(self, booking_id: str) -> int:
        return self._one("SELECT coalesce(max(version), 0) + 1 AS v FROM assessments "
                         "WHERE workshop_id = %s AND booking_id = %s", (self.wid, booking_id))["v"]

    def create_assessment(self, booking_id, trigger, cutoff_date, cutoff_at, *, state="pending", reason=None,
                          reason_text=None, retry_at=None):
        return self._one(
            """INSERT INTO assessments (workshop_id, booking_id, version, trigger, state, reason, reason_text,
                                        input_cutoff_date, input_cutoff_at, retry_at, requested_by_membership_id,
                                        completed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       CASE WHEN %s = 'pending' THEN NULL ELSE now() END) RETURNING *""",
            (self.wid, booking_id, self.next_version(booking_id), trigger, state, reason, reason_text,
             cutoff_date, cutoff_at, retry_at, self.scope.membership_id, state))

    def get_assessment(self, assessment_id: str, lock: bool = False):
        return self._one("SELECT * FROM assessments WHERE workshop_id = %s AND id = %s" + (" FOR UPDATE" if lock else ""),
                         (self.wid, assessment_id))

    def list_assessments(self, booking_id: str):
        return self._all("SELECT * FROM assessments WHERE workshop_id = %s AND booking_id = %s "
                         "ORDER BY version DESC", (self.wid, booking_id))

    def mark_assessment_quota_counted(self, assessment_id: str):
        self.cur.execute("UPDATE assessments SET quota_counted = true WHERE workshop_id = %s AND id = %s",
                         (self.wid, assessment_id))

    def set_assessment_pending_reason(self, assessment_id, reason, reason_text, retry_at):
        self.cur.execute("UPDATE assessments SET reason = %s, reason_text = %s, retry_at = %s "
                         "WHERE workshop_id = %s AND id = %s AND state = 'pending'",
                         (reason, reason_text, retry_at, self.wid, assessment_id))

    def complete_assessment(self, assessment_id, state, reason=None, reason_text=None, result=None,
                            evidence=None, snapshot_id=None, provider_name=None, provider_version=None):
        return self._one(
            """UPDATE assessments SET state = %s, reason = %s, reason_text = %s, result = %s, evidence = %s,
                      snapshot_id = %s, provider_name = %s, provider_version = %s, retry_at = NULL,
                      completed_at = now()
                WHERE workshop_id = %s AND id = %s AND state = 'pending' RETURNING *""",
            (state, reason, reason_text, Json(result) if result is not None else None,
             Json(evidence) if evidence is not None else None, snapshot_id, provider_name, provider_version,
             self.wid, assessment_id))

    # ---------------------------------------------------------- snapshots
    def insert_snapshot(self, booking_id, kind, upstream_status, history: dict | None, sha: str):
        return self._one(
            """INSERT INTO source_snapshots (workshop_id, booking_id, kind, upstream_status, history, content_sha256)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (self.wid, booking_id, kind, upstream_status, Json(history) if history is not None else None, sha))

    # ---------------------------------------------------------- decisions
    @staticmethod
    def _decision_sql(extra: str, distinct: bool = False) -> str:
        return f"""SELECT {'DISTINCT ON (d.booking_id)' if distinct else ''} d.*, u.email AS actor_email,
                          m.role AS actor_role
                     FROM decisions d JOIN memberships m ON m.workshop_id = d.workshop_id AND m.id = d.membership_id
                     JOIN users u ON u.id = m.user_id
                    WHERE d.workshop_id = %s {extra}
                    ORDER BY {'d.booking_id, ' if distinct else ''}d.created_at DESC, d.id DESC"""

    def insert_decision(self, booking_id, decision_type, note, assessment_id, supersedes_id):
        return self._one(
            """INSERT INTO decisions (workshop_id, booking_id, decision_type, note, membership_id, assessment_id,
                                      supersedes_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (self.wid, booking_id, decision_type, note, self.scope.membership_id, assessment_id, supersedes_id))

    def get_decision(self, decision_id: str):
        return self._one(self._decision_sql("AND d.id = %s"), (self.wid, decision_id))

    def list_decisions(self, booking_id: str):
        return self._all(self._decision_sql("AND d.booking_id = %s"), (self.wid, booking_id))

    # ----------------------------------------------------------- outcomes
    def schedule_outcome(self, booking_id, check_day, schedule_date, due_at, *, status="scheduled"):
        return self._one(
            """INSERT INTO outcomes (workshop_id, booking_id, check_day, schedule_date, due_at, status)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (booking_id, check_day, schedule_date) DO UPDATE
                  SET status = EXCLUDED.status, detail = NULL, matched = NULL, candidates = NULL, checked_at = NULL
                  WHERE outcomes.status = 'cancelled'
               RETURNING *""",
            (self.wid, booking_id, check_day, schedule_date, due_at, status))

    def get_outcome(self, booking_id, check_day, schedule_date, lock=False):
        return self._one("SELECT * FROM outcomes WHERE workshop_id = %s AND booking_id = %s AND check_day = %s "
                         "AND schedule_date = %s" + (" FOR UPDATE" if lock else ""),
                         (self.wid, booking_id, check_day, schedule_date))

    def list_outcomes(self, booking_id: str):
        # Only the current schedule: results recorded against a previous appointment date are history, not outcome.
        return self._all("SELECT o.* FROM outcomes o JOIN bookings b ON b.workshop_id = o.workshop_id AND "
                         "b.id = o.booking_id AND b.appointment_date = o.schedule_date WHERE o.workshop_id = %s AND "
                         "o.booking_id = %s AND o.status <> 'cancelled' ORDER BY o.check_day", (self.wid, booking_id))

    def complete_outcome(self, outcome_id, status, matched, candidates, detail, snapshot_id):
        return self._one(
            """UPDATE outcomes SET status = %s, matched = %s, candidates = %s, detail = %s, snapshot_id = %s,
                      checked_at = now()
                WHERE workshop_id = %s AND id = %s RETURNING *""",
            (status, Json(matched) if matched is not None else None,
             Json(candidates) if candidates is not None else None, detail, snapshot_id, self.wid, outcome_id))

    def cancel_open_outcomes(self, booking_id: str, detail: str):
        self.cur.execute("UPDATE outcomes SET status = 'cancelled', detail = %s WHERE workshop_id = %s AND "
                         "booking_id = %s AND status = 'scheduled'", (detail, self.wid, booking_id))
        self.cur.execute("UPDATE jobs SET status = 'cancelled', finished_at = now() WHERE workshop_id = %s AND "
                         "kind = 'outcome_check' AND status = 'queued' AND payload->>'booking_id' = %s",
                         (self.wid, booking_id))

    # --------------------------------------------------------------- jobs
    def enqueue(self, kind: str, payload: dict, run_at: datetime | None = None, dedupe_key: str | None = None,
                max_attempts: int = 3):
        return self._one(
            """INSERT INTO jobs (workshop_id, kind, payload, run_at, dedupe_key, max_attempts)
               VALUES (%s, %s, %s, coalesce(%s, now()), %s, %s)
               ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')
               DO NOTHING RETURNING id""",
            (self.wid, kind, Json(payload), run_at, dedupe_key, max_attempts))

    def job_for_dedupe(self, dedupe_key: str):
        return self._one("SELECT * FROM jobs WHERE workshop_id = %s AND dedupe_key = %s ORDER BY id DESC LIMIT 1",
                         (self.wid, dedupe_key))


def encode_cursor(d: date, i: str) -> str:
    return base64.urlsafe_b64encode(f"{d.isoformat()}|{i}".encode()).decode()


def decode_cursor(c: str):
    from .errors import ApiError
    try:
        d, i = base64.urlsafe_b64decode(c.encode()).decode().split("|")
        import uuid
        uuid.UUID(i)
        return date.fromisoformat(d), i
    except Exception:
        raise ApiError(422, "validation_error", "invalid cursor")
