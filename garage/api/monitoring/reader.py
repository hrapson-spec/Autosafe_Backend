"""Read-only database reader for the monitor-g7 command.

This is an operator, cross-workshop read of settled outcomes (the monitoring population is pooled across
workshops by specification, PLAN_rev3_20261026.md:96), so it is not a workshop-scoped store method. It lives
here, not in garage/api/*.py, because test_isolation.test_only_the_store_names_private_tables scans only the
top-level modules and allows store.py and retention.py to name the private tables. Whether this reader should
instead be a system-scope method in store.py/retention.py is an open decision (REPORT_T1a.md). It only SELECTs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ._stats import MonitoringInputError

# The score column is a REQUIRED NAME in every monitoring policy (APP_COMPLETION_V1 / T1b, REPORT_T1b.md task 1). Both columns
# hold the scorer's Platt-calibrated probability, but they are populated in different modes: `shadow_probability` (0003) only
# for an unqualified bundle, `calibrated_probability` (0005) for every scored assessment once the writer patch is applied.
# Only these two literals are ever placed in SQL; the policy value selects one, it is never interpolated.
SCORE_COLUMNS = {"shadow_probability": "a.shadow_probability", "calibrated_probability": "a.calibrated_probability"}
# Column holding the manifest hash of the bundle that produced the stored score.
SCORE_BUNDLE_COLUMNS = {"shadow_probability": "a.shadow_bundle_sha256", "calibrated_probability": "a.calibrated_bundle_sha256"}


def check_score_column(name) -> str:
    if name not in SCORE_COLUMNS:
        raise MonitoringInputError(f"score_column must be one of {sorted(SCORE_COLUMNS)}")
    return name

# One row per booking: the settled outcome row, the latest assessment that carries the NAMED score column and the
# vehicle history stored with the outcome snapshot. The F-or-PRS label is NOT read from outcomes.status: the
# PRS-aware classifier re-reads the stored tests (a PRS fail->pass pair has status 'ambiguous').
_G7_SQL = """
SELECT o.booking_id::text AS booking_id, o.workshop_id::text AS workshop_id, o.status, o.schedule_date,
       o.checked_at, {score} AS score,
       encode(sha256(convert_to(b.registration, 'UTF8')), 'hex') AS vehicle_id,
       s.history -> 'tests' AS tests
FROM outcomes o
JOIN bookings b ON b.workshop_id = o.workshop_id AND b.id = o.booking_id
JOIN source_snapshots s ON s.workshop_id = o.workshop_id AND s.id = o.snapshot_id
JOIN LATERAL (SELECT {score_bare} FROM assessments a
              WHERE a.workshop_id = o.workshop_id AND a.booking_id = o.booking_id
                AND {score} IS NOT NULL
              ORDER BY a.version DESC LIMIT 1) a ON true
WHERE o.check_day = %(check_day)s AND o.status = ANY(%(statuses)s) AND b.booking_type = ANY(%(types)s)
  AND o.checked_at IS NOT NULL AND s.upstream_status = 'ok' AND s.history IS NOT NULL
"""


def g7_sql(column: str) -> str:
    column = check_score_column(column)
    return _G7_SQL.replace("{score_bare}", column).replace("{score}", SCORE_COLUMNS[column])


def settled_rows(cur, *, check_day: int, statuses: Sequence[str], booking_types: Sequence[str],
                 score_column: str) -> List[Dict[str, Any]]:
    cur.execute(g7_sql(score_column), {"check_day": check_day, "statuses": list(statuses), "types": list(booking_types)})
    return [{"booking_id": r["booking_id"], "workshop_id": r["workshop_id"], "vehicle_id": r["vehicle_id"],
             "status": r["status"], "schedule_date": r["schedule_date"], "score": r["score"],
             "observed_utc": r["checked_at"].isoformat(), "tests": r["tests"]} for r in cur.fetchall()]


# ---------------------------------------------------------------------------------------------- G6 replay
# One candidate per booking: its latest completed assessment (version DESC) in the window, with the stored score (named
# column), the manifest hash that produced it, the raw DVSA bytes of the assessment's own snapshot, and the first 8 bytes of
# sha256(registration) as hex (the scorer's vehicle id is derived from it; the registration itself never leaves the database).
_REPLAY_SQL = """
SELECT a.booking_id::text AS booking_id, a.workshop_id::text AS workshop_id, a.state,
       a.input_cutoff_date AS cutoff_date, a.input_cutoff_at AS cutoff_at,
       {score} AS score, {score_bundle} AS score_bundle_sha256,
       s.raw_response AS raw_response,
       encode(substr(sha256(convert_to(b.registration, 'UTF8')), 1, 8), 'hex') AS vehicle_sha_prefix_hex,
       encode(sha256(convert_to(b.registration, 'UTF8')), 'hex') AS vehicle_id
FROM (SELECT DISTINCT ON (booking_id) * FROM assessments
       WHERE state IN ('available', 'unavailable', 'unsupported') AND completed_at IS NOT NULL
       ORDER BY booking_id, version DESC) a
JOIN bookings b ON b.workshop_id = a.workshop_id AND b.id = a.booking_id
LEFT JOIN source_snapshots s ON s.workshop_id = a.workshop_id AND s.id = a.snapshot_id AND s.upstream_status = 'ok'
WHERE a.completed_at >= %(start)s AND a.completed_at < %(end)s AND b.booking_type = ANY(%(types)s)
ORDER BY a.completed_at, a.booking_id
LIMIT %(limit)s
"""


def replay_sql(column: str) -> str:
    column = check_score_column(column)
    return _REPLAY_SQL.replace("{score_bundle}", SCORE_BUNDLE_COLUMNS[column]).replace("{score}", SCORE_COLUMNS[column])


def replay_candidates(cur, *, window_start, window_end, booking_types: Sequence[str], score_column: str,
                      max_bookings: int) -> List[Dict[str, Any]]:
    """Refuses (rather than truncating) when the window holds more than `max_bookings` bookings."""
    cur.execute(replay_sql(score_column), {"start": window_start, "end": window_end, "types": list(booking_types),
                                           "limit": int(max_bookings) + 1})
    rows = cur.fetchall()
    if len(rows) > max_bookings:
        raise MonitoringInputError(f"the window holds more than max_bookings={max_bookings} bookings: narrow the window "
                                   f"or raise the limit explicitly (rows are never silently dropped)")
    out = []
    for r in rows:
        d = dict(r)
        d["raw_response"] = bytes(d["raw_response"]) if d["raw_response"] is not None else None
        out.append(d)
    return out
