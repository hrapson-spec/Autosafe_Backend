"""Append-only record of monitoring runs (migration 0006, APP_COMPLETION_V1 / T1b task 3).

Only aggregates are stored. `assert_aggregate_only` refuses a result that carries a row-level key (booking id, vehicle id,
registration, feature values, test lists, ...) anywhere in its JSON, so the table cannot hold personal data beyond what the
existing tables hold. The operator commands call `record_run` only when `--record` is given; nothing is written otherwise.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from psycopg2.extras import Json

from ._stats import MonitoringInputError

KINDS = ("g6_replay", "g7")
# Keys that identify a booking, vehicle or person, or carry per-row values. None may appear at any depth of a stored result.
FORBIDDEN_KEYS = frozenset({
    "booking_id", "booking_ids", "vehicle_id", "vehicle_ids", "registration", "registrations", "vrm", "rows", "row", "features",
    "tests", "history", "raw_response", "email", "predicted", "records", "observed_utc", "schedule_date"})


def assert_aggregate_only(obj: Any, _path: str = "result") -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise MonitoringInputError(f"monitoring result holds a row-level key {k!r} at {_path}: refused")
            assert_aggregate_only(v, f"{_path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_aggregate_only(v, f"{_path}[{i}]")


def record_run(cur, *, kind: str, result: Mapping[str, Any], policy_sha256: str, window_start=None, window_end=None,
               reference_sha256: Optional[str] = None, bundle_manifest_sha256: Optional[str] = None) -> str:
    """INSERT one row, return its id. The caller owns the transaction (Database.tx)."""
    if kind not in KINDS:
        raise MonitoringInputError(f"kind must be one of {KINDS}")
    assert_aggregate_only(result)
    payload = json.loads(json.dumps(result, sort_keys=True, default=str))     # JSON-safe copy (dates -> strings)
    cur.execute(
        """INSERT INTO monitoring_runs (kind, window_start, window_end, policy_sha256, reference_sha256,
                                        bundle_manifest_sha256, result)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id::text AS id""",
        (kind, window_start, window_end, policy_sha256, reference_sha256, bundle_manifest_sha256, Json(payload)))
    return cur.fetchone()["id"]
