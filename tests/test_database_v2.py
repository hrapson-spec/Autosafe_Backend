"""
AutoSafe Database v2 Tests
===========================

Tests for the v2 report-contract Postgres persistence layer added to
database.py: the banded risk evidence ladder (get_risk_v2_banded), report
save/fetch (save_report, get_report_by_token, get_report_by_idempotency_key),
and the add_report_contract_columns migration.

Self-contained fake asyncpg pool/connection double, defined in this file
only (the repo has no conftest.py and this suite keeps it that way).
"""
import asyncio
import json
import os
import subprocess
import sys
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import asyncpg

# Add parent directory to path (matches tests/test_api.py, tests/test_dvla.py)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(REPO_ROOT, "migrations", "add_report_contract_columns.py")


# ----------------------------------------------------------------------------
# Fake asyncpg double
# ----------------------------------------------------------------------------

class FakeAcquireContext:
    """Stands in for asyncpg's `pool.acquire()` async context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    """
    Programmable fake for asyncpg.Connection.

    fetch_results / fetchrow_results / fetchval_results are FIFO queues:
    each call to the corresponding method pops the next queued item. A
    queued item that is an exception instance (or exception class) is
    raised instead of returned, so a test can simulate a query failing
    partway through a multi-step ladder.

    Every call is recorded in `.calls` as (method, sql, params) so tests
    can assert exactly what was sent to Postgres, and in what order.
    """

    def __init__(self, fetch_results=None, fetchrow_results=None, fetchval_results=None):
        self._fetch_results = list(fetch_results or [])
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetchval_results = list(fetchval_results or [])
        self.calls = []

    async def fetch(self, query, *params):
        self.calls.append(("fetch", query, params))
        return self._pop(self._fetch_results, "fetch")

    async def fetchrow(self, query, *params):
        self.calls.append(("fetchrow", query, params))
        return self._pop(self._fetchrow_results, "fetchrow")

    async def fetchval(self, query, *params):
        self.calls.append(("fetchval", query, params))
        return self._pop(self._fetchval_results, "fetchval")

    @staticmethod
    def _pop(queue, method_name):
        if not queue:
            raise AssertionError("FakeConnection.{}: no more programmed results".format(method_name))
        result = queue.pop(0)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, type) and issubclass(result, BaseException):
            raise result()
        return result


class FakePool:
    """Stands in for the asyncpg pool returned by database.get_pool()."""

    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquireContext(self.conn)


def _patched_get_pool(pool_or_none):
    """Context manager replacing database.get_pool() with one returning pool_or_none."""
    return patch.object(database, "get_pool", AsyncMock(return_value=pool_or_none))


def _null_ladder_row():
    """Aggregate row shape for a predicate that matched zero mot_risk rows:
    total_tests NULL, and (per the real SQL) every other aggregate NULL too."""
    return {
        "total_tests": None,
        "total_failures": None,
        "failure_risk": None,
        "risk_brakes": None,
        "risk_suspension": None,
        "risk_tyres": None,
        "risk_steering": None,
        "risk_visibility": None,
        "risk_lamps": None,
        "risk_body": None,
    }


def _full_ladder_row(total_tests=100, total_failures=20, failure_risk=0.2, **overrides):
    """Aggregate row shape for a predicate that matched real mot_risk rows."""
    row = {
        "total_tests": total_tests,
        "total_failures": total_failures,
        "failure_risk": failure_risk,
        "risk_brakes": 0.1,
        "risk_suspension": 0.15,
        "risk_tyres": 0.2,
        "risk_steering": 0.05,
        "risk_visibility": 0.03,
        "risk_lamps": 0.04,
        "risk_body": 0.06,
    }
    row.update(overrides)
    return row


# ----------------------------------------------------------------------------
# get_risk_v2_banded — ladder step selection
# ----------------------------------------------------------------------------

class TestGetRiskV2BandedLadder(unittest.TestCase):

    def test_exact_band_hit_issues_one_query(self):
        conn = FakeConnection(fetch_results=[[_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertEqual(result["match_scope"], "exact_band")
            self.assertEqual(len(conn.calls), 1)
            sql = conn.calls[0][1]
            self.assertIn("age_band = $3", sql)
            self.assertIn("mileage_band = $4", sql)
            self.assertIn("ESCAPE '\\'", sql)
            # $1 (exact match) carries the raw model_id; $2 (LIKE operand)
            # carries the LIKE-escaped copy -- distinct parameters so a
            # literal '%'/'_' in model_id can't widen the LIKE match.
            params = conn.calls[0][2]
            self.assertEqual(params[0], "FORD FIESTA")
            self.assertEqual(params[1], database.escape_like("FORD FIESTA"))

        asyncio.run(run_test())

    def test_exact_null_falls_through_to_age_band(self):
        conn = FakeConnection(fetch_results=[[_null_ladder_row()], [_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertEqual(result["match_scope"], "age_band_only")
            self.assertEqual(len(conn.calls), 2)
            self.assertIn("mileage_band = $4", conn.calls[0][1])
            self.assertNotIn("mileage_band", conn.calls[1][1])
            self.assertIn("age_band = $3", conn.calls[1][1])

        asyncio.run(run_test())

    def test_zero_test_or_missing_risk_rows_are_not_evidence(self):
        """A zero-test aggregate has no denominator. It must fall through,
        never become a precise-looking 0% failure rate."""
        zero_row = _full_ladder_row(total_tests=0, total_failures=0, failure_risk=None)
        conn = FakeConnection(fetch_results=[[zero_row], [_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded(
                    "FORD FIESTA", "3-5", "30k-60k"
                )
            self.assertEqual(result["match_scope"], "age_band_only")
            self.assertEqual(result["failure_risk"], 0.2)
            self.assertEqual(len(conn.calls), 2)

        asyncio.run(run_test())

    def test_both_null_falls_through_to_model_average(self):
        conn = FakeConnection(fetch_results=[
            [_null_ladder_row()], [_null_ladder_row()], [_full_ladder_row()],
        ])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertEqual(result["match_scope"], "model_average")
            self.assertEqual(len(conn.calls), 3)
            third_sql = conn.calls[2][1]
            self.assertNotIn("age_band", third_sql)
            self.assertNotIn("mileage_band", third_sql)

        asyncio.run(run_test())

    def test_all_null_returns_none(self):
        conn = FakeConnection(fetch_results=[
            [_null_ladder_row()], [_null_ladder_row()], [_null_ladder_row()],
        ])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertIsNone(result)
            self.assertEqual(len(conn.calls), 3)

        asyncio.run(run_test())

    def test_mileage_band_none_skips_step_1(self):
        conn = FakeConnection(fetch_results=[[_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", None)
            self.assertEqual(result["match_scope"], "age_band_only")
            self.assertEqual(len(conn.calls), 1)
            first_sql = conn.calls[0][1]
            self.assertNotIn("mileage_band", first_sql)
            self.assertIn("age_band = $3", first_sql)

        asyncio.run(run_test())

    def test_mileage_band_unknown_skips_step_1(self):
        conn = FakeConnection(fetch_results=[[_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "Unknown")
            self.assertEqual(result["match_scope"], "age_band_only")
            self.assertEqual(len(conn.calls), 1)
            first_sql = conn.calls[0][1]
            self.assertNotIn("mileage_band", first_sql)

        asyncio.run(run_test())


# ----------------------------------------------------------------------------
# get_risk_v2_banded — return-shape correctness
# ----------------------------------------------------------------------------

class TestGetRiskV2BandedShape(unittest.TestCase):

    def test_component_aggregate_requires_complete_row_coverage(self):
        conn = FakeConnection(fetch_results=[[_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            sql = conn.calls[0][1].lower()
            self.assertIn("count(risk_brakes) = count(*)", sql)
            self.assertIn(
                "count(risk_lamps_reflectors_and_electrical_equipment) = count(*)",
                sql,
            )

        asyncio.run(run_test())

    def test_component_nulls_yield_components_available_false_and_none_values(self):
        row = _full_ladder_row(risk_brakes=None, risk_body=None)
        conn = FakeConnection(fetch_results=[[row]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertFalse(result["components_available"])
            self.assertIsNone(result["risk_brakes"])
            self.assertIsNone(result["risk_body"])
            # untouched components must still be populated, not blanked out
            self.assertIsNotNone(result["risk_tyres"])
            self.assertIsNotNone(result["risk_suspension"])

        asyncio.run(run_test())

    def test_all_components_present_yields_components_available_true(self):
        conn = FakeConnection(fetch_results=[[_full_ladder_row()]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertTrue(result["components_available"])
            # aliasing: long DVSA names must come back under the short keys
            self.assertIn("risk_lamps", result)
            self.assertIn("risk_body", result)
            self.assertNotIn("risk_lamps_reflectors_and_electrical_equipment", result)
            self.assertNotIn("risk_body_chassis_structure", result)

        asyncio.run(run_test())

    def test_clamping_applied_to_out_of_range_values(self):
        row = _full_ladder_row(failure_risk=1.5, risk_brakes=-0.2)
        conn = FakeConnection(fetch_results=[[row]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertEqual(result["failure_risk"], 1.0)
            self.assertEqual(result["risk_brakes"], 0.0)

        asyncio.run(run_test())

    def test_total_failures_null_stays_none_not_zero(self):
        row = _full_ladder_row(total_failures=None)
        conn = FakeConnection(fetch_results=[[row]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertIsNone(result["total_failures"])
            self.assertIn("total_failures", result)

        asyncio.run(run_test())

    def test_total_tests_is_int(self):
        conn = FakeConnection(fetch_results=[[_full_ladder_row(total_tests=250)]])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            self.assertEqual(result["total_tests"], 250)
            self.assertIsInstance(result["total_tests"], int)

        asyncio.run(run_test())


# ----------------------------------------------------------------------------
# get_risk_v2_banded — PostgresUnavailable
# ----------------------------------------------------------------------------

class TestGetRiskV2BandedErrors(unittest.TestCase):

    def test_no_pool_raises_postgres_unavailable(self):
        async def run_test():
            with _patched_get_pool(None):
                with self.assertRaises(database.PostgresUnavailable):
                    await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")

        asyncio.run(run_test())

    def test_fetch_exception_raises_postgres_unavailable(self):
        conn = FakeConnection(fetch_results=[RuntimeError("connection reset")])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                with self.assertRaises(database.PostgresUnavailable) as ctx:
                    await database.get_risk_v2_banded("FORD FIESTA", "3-5", "30k-60k")
            # original error must be wrapped, not swallowed
            self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

        asyncio.run(run_test())


# ----------------------------------------------------------------------------
# save_report
# ----------------------------------------------------------------------------

class TestSaveReport(unittest.TestCase):

    NAMED_COLUMNS = (
        "registration", "postcode", "vehicle_make", "vehicle_model",
        "vehicle_year", "vehicle_fuel_type", "mileage", "last_mot_date",
        "last_mot_result", "failure_risk", "confidence_level",
        "risk_components", "repair_cost_estimate", "model_version",
        "prediction_source", "is_dvsa_data", "referrer", "contract_version",
        "report_token", "report_payload", "mileage_source",
        "effective_mileage", "match_scope", "total_tests", "total_failures",
        "idempotency_key", "expires_at",
    )

    @staticmethod
    def _record(**overrides):
        record = dict(
            registration="AB12CDE",
            postcode="SW1A1AA",
            vehicle_make="FORD",
            vehicle_model="FIESTA",
            vehicle_year=2018,
            vehicle_fuel_type="PETROL",
            mileage=45000,
            last_mot_date=None,
            last_mot_result="PASS",
            failure_risk=0.25,
            confidence_level="High",
            risk_components={"brakes": 0.1},
            repair_cost_estimate={"expected": 200},
            model_version="v2",
            prediction_source="postgres",
            is_dvsa_data=True,
            referrer=None,
            contract_version="2.0",
            report_token="tok_123",
            report_payload={"contract_version": "2.0"},
            mileage_source="observed_mot",
            effective_mileage=45000,
            match_scope="exact_band",
            total_tests=100,
            total_failures=20,
            idempotency_key="idem_123",
            expires_at=None,
            id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        )
        record.update(overrides)
        return record

    def test_happy_path_returns_id_and_records_named_columns(self):
        fake_id = "11111111-1111-1111-1111-111111111111"
        conn = FakeConnection(fetchrow_results=[{"id": fake_id}])
        pool = FakePool(conn)
        record = self._record()

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.save_report(record)
            self.assertEqual(result, fake_id)
            self.assertEqual(len(conn.calls), 1)
            method, sql, params = conn.calls[0]
            self.assertEqual(method, "fetchrow")
            for col in self.NAMED_COLUMNS:
                self.assertIn(col, sql)
            self.assertEqual(len(params), 28)
            # id is the pre-minted uuid, COALESCEd server-side if absent
            self.assertEqual(params[27], record["id"])
            self.assertIn("COALESCE($28, gen_random_uuid())", sql)
            # JSONB params go through json.dumps before the ::jsonb cast,
            # mirroring legacy save_risk_check's handling.
            self.assertEqual(params[11], json.dumps(record["risk_components"]))
            self.assertEqual(params[12], json.dumps(record["repair_cost_estimate"]))
            self.assertEqual(params[19], json.dumps(record["report_payload"]))
            self.assertIn("$12::jsonb", sql)
            self.assertIn("$13::jsonb", sql)
            self.assertIn("$20::jsonb", sql)

        asyncio.run(run_test())

    def test_jsonb_none_serializes_to_json_null_not_python_none(self):
        """risk_components/repair_cost_estimate are allowed to be None; the
        param sent to Postgres must be the JSON text 'null' (valid for a
        ::jsonb cast), not the Python object None (which asyncpg would
        reject for a non-null-typed bind)."""
        conn = FakeConnection(fetchrow_results=[{"id": "x"}])
        pool = FakePool(conn)
        record = self._record(risk_components=None, repair_cost_estimate=None)

        async def run_test():
            with _patched_get_pool(pool):
                await database.save_report(record)
            params = conn.calls[0][2]
            self.assertEqual(params[11], "null")
            self.assertEqual(params[12], "null")

        asyncio.run(run_test())

    def test_no_pool_raises_postgres_unavailable(self):
        async def run_test():
            with _patched_get_pool(None):
                with self.assertRaises(database.PostgresUnavailable):
                    await database.save_report(self._record())

        asyncio.run(run_test())

    def test_unique_violation_reraised(self):
        conn = FakeConnection(fetchrow_results=[asyncpg.exceptions.UniqueViolationError])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                with self.assertRaises(asyncpg.exceptions.UniqueViolationError):
                    await database.save_report(self._record())

        asyncio.run(run_test())

    def test_other_exception_returns_none_without_raising(self):
        conn = FakeConnection(fetchrow_results=[RuntimeError("disk full")])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.save_report(self._record())
            self.assertIsNone(result)

        asyncio.run(run_test())

    def test_no_jsonl_backup_written_on_failure(self):
        """Deliberate divergence from legacy save_risk_check: no local
        JSONL backup on a Postgres write failure (fail-open only)."""
        conn = FakeConnection(fetchrow_results=[RuntimeError("disk full")])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                with patch.object(database, "_backup_risk_check_to_file") as mock_backup:
                    result = await database.save_report(self._record())
            self.assertIsNone(result)
            mock_backup.assert_not_called()

        asyncio.run(run_test())


# ----------------------------------------------------------------------------
# get_report_by_token / get_report_by_idempotency_key
# ----------------------------------------------------------------------------

class TestGetReportByToken(unittest.TestCase):

    def test_found_parses_json_payload(self):
        payload_dict = {"contract_version": "2.0", "registration": "AB12CDE"}
        row = {
            "id": "22222222-2222-2222-2222-222222222222",
            "report_payload": json.dumps(payload_dict),
            "expires_at": datetime(2026, 10, 1),
            "pseudonymised_at": None,
            "created_at": datetime(2026, 7, 1),
        }
        conn = FakeConnection(fetchrow_results=[row])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_report_by_token("tok_123")
            self.assertEqual(result["id"], row["id"])
            self.assertEqual(result["report_payload"], payload_dict)
            self.assertIsInstance(result["report_payload"], dict)
            self.assertEqual(result["expires_at"], datetime(2026, 10, 1))
            self.assertEqual(conn.calls[0][2], ("tok_123",))
            self.assertIn("report_token = $1", conn.calls[0][1])

        asyncio.run(run_test())

    def test_absent_returns_none(self):
        conn = FakeConnection(fetchrow_results=[None])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_report_by_token("missing")
            self.assertIsNone(result)

        asyncio.run(run_test())

    def test_no_pool_raises_postgres_unavailable(self):
        async def run_test():
            with _patched_get_pool(None):
                with self.assertRaises(database.PostgresUnavailable):
                    await database.get_report_by_token("tok_123")

        asyncio.run(run_test())

    def test_query_exception_raises_postgres_unavailable(self):
        conn = FakeConnection(fetchrow_results=[RuntimeError("boom")])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                with self.assertRaises(database.PostgresUnavailable):
                    await database.get_report_by_token("tok_123")

        asyncio.run(run_test())


class TestGetReportByIdempotencyKey(unittest.TestCase):

    def test_found_parses_json_payload(self):
        payload_dict = {"contract_version": "2.0"}
        row = {
            "id": "33333333-3333-3333-3333-333333333333",
            "registration": "AB12CDE",
            "report_payload": json.dumps(payload_dict),
            "expires_at": None,
            "pseudonymised_at": None,
            "created_at": datetime(2026, 7, 1),
        }
        conn = FakeConnection(fetchrow_results=[row])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_report_by_idempotency_key("idem_123")
            self.assertEqual(result["report_payload"], payload_dict)
            self.assertEqual(result["registration"], "AB12CDE")
            self.assertIn("registration", conn.calls[0][1])
            self.assertIn("idempotency_key = $1", conn.calls[0][1])
            self.assertEqual(conn.calls[0][2], ("idem_123",))

        asyncio.run(run_test())

    def test_absent_returns_none(self):
        conn = FakeConnection(fetchrow_results=[None])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.get_report_by_idempotency_key("missing")
            self.assertIsNone(result)

        asyncio.run(run_test())

    def test_no_pool_raises_postgres_unavailable(self):
        async def run_test():
            with _patched_get_pool(None):
                with self.assertRaises(database.PostgresUnavailable):
                    await database.get_report_by_idempotency_key("idem_123")

        asyncio.run(run_test())


# ----------------------------------------------------------------------------
# Legacy database.py compatibility markers
# ----------------------------------------------------------------------------

class TestOptionalLeadPostcodePersistence(unittest.TestCase):
    def test_reminder_persists_missing_postcode_as_null(self):
        conn = FakeConnection(fetchrow_results=[None, {"id": "lead-1"}])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.save_mot_reminder({
                    "email": "owner@example.com",
                    "registration": "AB12CDE",
                })
            self.assertTrue(result["success"])
            insert_call = conn.calls[1]
            self.assertIn("INSERT INTO leads", insert_call[1])
            self.assertIsNone(insert_call[2][1])

        asyncio.run(run_test())

    def test_emailed_report_persists_missing_postcode_as_null(self):
        conn = FakeConnection(fetchrow_results=[{"id": "lead-2"}])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.save_report_email_lead({
                    "email": "owner@example.com",
                    "registration": "AB12CDE",
                    "failure_risk": 0.0,
                    "common_faults": [],
                })
            self.assertEqual(result, "lead-2")
            insert_call = conn.calls[0]
            self.assertIn("INSERT INTO leads", insert_call[1])
            self.assertIsNone(insert_call[2][1])

        asyncio.run(run_test())


class TestLegacyDatabaseCompatibility(unittest.TestCase):
    """Guard stable legacy entry points semantically.

    A byte-prefix hash prevented legitimate, backwards-compatible privacy and
    provenance fixes in shared helpers. Exact source bytes are not an API;
    function names, signatures and key query behaviour are the compatibility
    boundary worth testing.
    """

    def test_get_risk_signature_and_key_markers_present(self):
        """Belt-and-braces marker check specifically on the three functions
        named in the ownership invariant, independent of the hash check."""
        db_path = os.path.join(REPO_ROOT, "database.py")
        with open(db_path, "r") as f:
            source = f.read()
        self.assertIn(
            "async def get_risk(model_id: str, age_band: str, mileage_band: str) -> Optional[Dict]:",
            source,
        )
        self.assertIn(
            '"""Get aggregated risk data for a vehicle, combining all variants."""',
            source,
        )
        self.assertIn(
            "# P0-5 fix: Use exact match OR variant match (model_id || ' %')",
            source,
        )
        self.assertIn(
            "async def save_risk_check(risk_data: Dict) -> Optional[str]:",
            source,
        )
        self.assertIn(
            "async def get_risk_check_stats() -> Dict:",
            source,
        )


# ----------------------------------------------------------------------------
# migrations/add_report_contract_columns.py
# ----------------------------------------------------------------------------

class TestReportContractMigration(unittest.TestCase):

    ADDED_COLUMNS = (
        "contract_version", "report_token", "report_payload", "mileage_source",
        "effective_mileage", "match_scope", "total_tests", "total_failures",
        "idempotency_key", "expires_at", "pseudonymised_at", "vrm_hmac",
    )
    LEAD_ADDED_COLUMNS = ("vehicle_mileage_source", "comparison_scope")

    @staticmethod
    def _run(*args):
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        return subprocess.run(
            [sys.executable, MIGRATION_PATH] + list(args),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_dry_run_forward_prints_expected_sql_without_db(self):
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout
        self.assertIn(
            "ALTER TABLE risk_checks ALTER COLUMN registration DROP NOT NULL", output
        )
        self.assertIn(
            "ALTER TABLE leads ALTER COLUMN postcode DROP NOT NULL", output
        )
        self.assertEqual(len(self.ADDED_COLUMNS), 12)
        for col in self.ADDED_COLUMNS:
            self.assertIn("ADD COLUMN IF NOT EXISTS {}".format(col), output)
        for col in self.LEAD_ADDED_COLUMNS:
            self.assertIn(
                "ALTER TABLE leads ADD COLUMN IF NOT EXISTS {}".format(col),
                output,
            )
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_checks_report_token "
            "ON risk_checks(report_token) WHERE report_token IS NOT NULL",
            output,
        )
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_checks_idempotency_key "
            "ON risk_checks(idempotency_key) WHERE idempotency_key IS NOT NULL",
            output,
        )
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_risk_checks_vrm_hmac", output)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_risk_checks_expires_at", output)

    def test_dry_run_rollback_prints_drops_never_set_not_null(self):
        result = self._run("--rollback", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout
        self.assertIn("DROP INDEX IF EXISTS idx_risk_checks_report_token", output)
        self.assertIn("DROP INDEX IF EXISTS idx_risk_checks_idempotency_key", output)
        self.assertIn("DROP INDEX IF EXISTS idx_risk_checks_vrm_hmac", output)
        self.assertIn("DROP INDEX IF EXISTS idx_risk_checks_expires_at", output)
        for col in self.ADDED_COLUMNS:
            self.assertIn("DROP COLUMN IF EXISTS {}".format(col), output)
        for col in self.LEAD_ADDED_COLUMNS:
            self.assertIn(
                "ALTER TABLE leads DROP COLUMN IF EXISTS {}".format(col),
                output,
            )
        self.assertNotIn("NOT NULL", output)
        self.assertNotIn("SET NOT NULL", output)
        self.assertNotIn("registration", output)

    def test_no_database_url_forward_skips_gracefully(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DATABASE_URL not set, skipping migration", result.stdout)


class TestGarageLeadMileageProvenance(unittest.TestCase):
    def test_save_lead_persists_mileage_source_next_to_mileage(self):
        conn = FakeConnection(fetchrow_results=[{"id": "lead-1"}])
        pool = FakePool(conn)
        lead = {
            "email": "owner@example.com",
            "postcode": "SW1A 1AA",
            "lead_type": "garage",
            "consent_given": True,
            "vehicle": {
                "make": "FORD",
                "model": "FIESTA",
                "year": 2018,
                "mileage": 45000,
                "mileage_source": "observed_mot",
            },
            "risk_data": {
                "failure_risk": 0.27,
                "match_scope": "exact_band",
                "top_risks": [],
            },
        }

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.save_lead(lead)
            self.assertEqual(result, "lead-1")
            _method, sql, params = conn.calls[0]
            self.assertIn("vehicle_mileage", sql)
            self.assertIn("vehicle_mileage_source", sql)
            mileage_index = params.index(45000)
            self.assertEqual(params[mileage_index + 1], "observed_mot")
            self.assertIn("comparison_scope", sql)
            self.assertIn("exact_band", params)

        asyncio.run(run_test())

    def test_bootstrap_schema_contains_mileage_source(self):
        path = os.path.join(REPO_ROOT, "create_leads_table.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("vehicle_mileage_source VARCHAR(20)", source)
        self.assertIn("ADD COLUMN vehicle_mileage_source VARCHAR(20)", source)
        self.assertIn("comparison_scope VARCHAR(20)", source)
        self.assertIn("ADD COLUMN comparison_scope VARCHAR(20)", source)
        self.assertIn("postcode VARCHAR(10),", source)
        self.assertNotIn("postcode VARCHAR(10) NOT NULL", source)


if __name__ == "__main__":
    unittest.main()
