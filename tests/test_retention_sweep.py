"""
Tests for scripts/retention_sweep.py (24-month retention pseudonymisation).

Self-contained: no conftest.py, no real database. A minimal in-file fake
stands in for asyncpg.Connection, recording every SQL statement/args it
receives so tests can assert on what was actually sent to "the database"
(e.g. that dry-run never issues an UPDATE, or that --execute's UPDATEs
collectively cover every candidate id exactly once).
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scripts"))

import retention_sweep  # noqa: E402


# ---------------------------------------------------------------------------
# Fake connection
# ---------------------------------------------------------------------------

class FakeConnection:
    """Minimal stand-in for asyncpg.Connection.

    - `fetch`/`fetchval` dispatch on distinguishing substrings of the SQL
      text (each query in retention_sweep.py is written so its shape is
      unambiguous -- see the comments below for exactly which substring
      identifies which query).
    - The candidate-batch query ("SELECT id, registration ...") is the one
      exception: it's called repeatedly in a loop, so responses are popped
      off an explicit queue (`batch_pages`) in call order, letting a test
      script "500 candidates, then 120, then 0" across successive calls.
    - `executemany` calls are recorded verbatim in `executemany_calls` so
      tests can inspect exactly which (hmac, id, cutoff) tuples were sent.
    - `calls` records every method/sql/args tuple across fetch/fetchval/
      executemany/execute, for blanket assertions like "nothing sent here
      starts with UPDATE".
    """

    def __init__(self, batch_pages=None, responses=None):
        self.calls = []
        self.executemany_calls = []
        self._batch_pages = list(batch_pages) if batch_pages is not None else [[]]
        defaults = {
            "count_candidates": 0,
            "year_breakdown": [],
            "sample_ids": [],
            "stale_sensitive_fields": 0,
            "pseudonymised_total": 0,
            "pseudonymised_with_hmac": 0,
            "pseudonymised_with_sensitive_fields": 0,
        }
        defaults.update(responses or {})
        self._responses = defaults

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "SELECT id, registration" in sql:
            # fetch_candidate_batch -- pop the next scripted page.
            if self._batch_pages:
                return self._batch_pages.pop(0)
            return []
        if "date_trunc" in sql:
            # year_breakdown
            return self._responses["year_breakdown"]
        if "SELECT id" in sql:
            # sample_ids (checked after the more specific "SELECT id,
            # registration" branch above, so this only matches the
            # single-column sample-ids query)
            return self._responses["sample_ids"]
        raise AssertionError(f"FakeConnection.fetch: unrecognized SQL:\n{sql}")

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "stale sensitive fields" in sql:
            return self._responses["stale_sensitive_fields"]
        if "pseudonymised sensitive fields" in sql:
            return self._responses["pseudonymised_with_sensitive_fields"]
        # "report_token" only appears in the CANDIDATE_FILTER_SQL fragment
        # (count_candidates); none of the verification queries mention it.
        if "report_token" in sql:
            return self._responses["count_candidates"]
        if "vrm_hmac IS NOT NULL" in sql:
            return self._responses["pseudonymised_with_hmac"]
        if "pseudonymised_at IS NOT NULL" in sql:
            return self._responses["pseudonymised_total"]
        raise AssertionError(f"FakeConnection.fetchval: unrecognized SQL:\n{sql}")

    async def executemany(self, sql, args_list):
        args_list = list(args_list)
        self.calls.append(("executemany", sql, args_list))
        self.executemany_calls.append((sql, args_list))
        return None

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return None

    async def close(self):
        pass


def _row(id_, registration=None):
    return {"id": id_, "registration": registration}


VALID_KEY = "k" * 32
CUTOFF = datetime(2026, 1, 1)


# ---------------------------------------------------------------------------
# vrm_hmac
# ---------------------------------------------------------------------------

class TestVrmHmac(unittest.TestCase):

    def test_deterministic(self):
        self.assertEqual(
            retention_sweep.vrm_hmac(VALID_KEY, "AB12CDE"),
            retention_sweep.vrm_hmac(VALID_KEY, "AB12CDE"),
        )

    def test_normalization_space_and_case(self):
        """"ab12 cde" and "AB12CDE" must hash identically."""
        self.assertEqual(
            retention_sweep.vrm_hmac(VALID_KEY, "ab12 cde"),
            retention_sweep.vrm_hmac(VALID_KEY, "AB12CDE"),
        )

    def test_different_keys_different_digests(self):
        self.assertNotEqual(
            retention_sweep.vrm_hmac("a" * 32, "AB12CDE"),
            retention_sweep.vrm_hmac("b" * 32, "AB12CDE"),
        )

    def test_different_vrms_different_digests(self):
        self.assertNotEqual(
            retention_sweep.vrm_hmac(VALID_KEY, "AB12CDE"),
            retention_sweep.vrm_hmac(VALID_KEY, "XY99ZZZ"),
        )

    def test_hexdigest_length_and_charset(self):
        digest = retention_sweep.vrm_hmac(VALID_KEY, "AB12CDE")
        self.assertEqual(len(digest), 64)
        self.assertRegex(digest, r'^[0-9a-f]{64}$')


# ---------------------------------------------------------------------------
# Month-cutoff arithmetic
# ---------------------------------------------------------------------------

class TestComputeCutoff(unittest.TestCase):

    def test_24_months_back_jan_31_no_clamp_needed(self):
        """24 months back from 31 Jan is exactly 31 Jan two years earlier
        -- both Januaries have 31 days, so no clamping is exercised, but
        this pins the exact-years case explicitly."""
        self.assertEqual(
            retention_sweep.compute_cutoff(datetime(2026, 1, 31), 24),
            datetime(2024, 1, 31),
        )

    def test_mar_31_minus_1_month_clamps_to_feb_28_non_leap(self):
        self.assertEqual(
            retention_sweep.compute_cutoff(datetime(2025, 3, 31), 1),
            datetime(2025, 2, 28),
        )

    def test_mar_31_minus_1_month_clamps_to_feb_29_leap(self):
        self.assertEqual(
            retention_sweep.compute_cutoff(datetime(2024, 3, 31), 1),
            datetime(2024, 2, 29),
        )

    def test_mar_31_minus_2_months_no_clamp_jan_has_31(self):
        self.assertEqual(
            retention_sweep.compute_cutoff(datetime(2025, 3, 31), 2),
            datetime(2025, 1, 31),
        )

    def test_mar_31_minus_3_months_no_clamp_dec_has_31(self):
        self.assertEqual(
            retention_sweep.compute_cutoff(datetime(2025, 3, 31), 3),
            datetime(2024, 12, 31),
        )


# ---------------------------------------------------------------------------
# Key-length enforcement
# ---------------------------------------------------------------------------

class TestRequireHmacKey(unittest.TestCase):

    def test_execute_with_missing_key_refuses(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VRM_HMAC_KEY", None)
            with self.assertRaises(SystemExit) as ctx:
                retention_sweep.require_hmac_key(execute=True)
            self.assertEqual(ctx.exception.code, 1)

    def test_execute_with_short_key_refuses(self):
        with patch.dict(os.environ, {"VRM_HMAC_KEY": "too-short"}):
            with self.assertRaises(SystemExit) as ctx:
                retention_sweep.require_hmac_key(execute=True)
            self.assertEqual(ctx.exception.code, 1)

    def test_execute_with_valid_key_returns_it(self):
        with patch.dict(os.environ, {"VRM_HMAC_KEY": VALID_KEY}):
            self.assertEqual(retention_sweep.require_hmac_key(execute=True), VALID_KEY)

    def test_dry_run_without_key_does_not_raise(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VRM_HMAC_KEY", None)
            self.assertIsNone(retention_sweep.require_hmac_key(execute=False))

    def test_dry_run_with_short_key_does_not_raise(self):
        """dry-run never validates the key at all -- a short/garbage key
        present in the environment must not block a dry-run."""
        with patch.dict(os.environ, {"VRM_HMAC_KEY": "x"}):
            self.assertEqual(retention_sweep.require_hmac_key(execute=False), "x")


# ---------------------------------------------------------------------------
# run_sweep: dry-run issues zero UPDATEs
# ---------------------------------------------------------------------------

class TestDryRunNoUpdates(unittest.TestCase):

    def test_dry_run_issues_zero_update_statements(self):
        conn = FakeConnection(
            responses={
                "count_candidates": 3,
                "year_breakdown": [{"yr": 2023, "n": 2}, {"yr": 2024, "n": 1}],
                "sample_ids": [{"id": "id-1"}, {"id": "id-2"}],
            }
        )

        import asyncio
        result = asyncio.run(retention_sweep.run_sweep(conn, None, CUTOFF, 500, execute=False))

        self.assertEqual(conn.executemany_calls, [])
        for _method, sql, _args in conn.calls:
            self.assertFalse(
                sql.strip().upper().startswith("UPDATE"),
                f"dry-run must never issue an UPDATE, but got: {sql}",
            )
        # Verification block still ran and returned real counts.
        self.assertEqual(result["stale_sensitive_fields"], 0)


# ---------------------------------------------------------------------------
# run_sweep: --execute batching
# ---------------------------------------------------------------------------

class TestExecuteBatching(unittest.TestCase):

    def test_two_batches_cover_all_ids_exactly_once(self):
        batch_1 = [_row(f"a-{i}") for i in range(500)]
        batch_2 = [_row(f"b-{i}") for i in range(120)]
        conn = FakeConnection(batch_pages=[batch_1, batch_2, []])

        import asyncio
        asyncio.run(retention_sweep.run_sweep(conn, VALID_KEY, CUTOFF, 500, execute=True))

        self.assertEqual(len(conn.executemany_calls), 2)
        self.assertEqual(len(conn.executemany_calls[0][1]), 500)
        self.assertEqual(len(conn.executemany_calls[1][1]), 120)

        all_ids = []
        for _sql, args_list in conn.executemany_calls:
            for digest, row_id, cutoff_arg in args_list:
                all_ids.append(row_id)
                self.assertEqual(cutoff_arg, CUTOFF)
                self.assertIsNone(digest)  # these rows had no registration

        expected_ids = [r["id"] for r in batch_1] + [r["id"] for r in batch_2]
        self.assertEqual(len(all_ids), 620)
        self.assertEqual(len(all_ids), len(set(all_ids)), "an id was updated more than once")
        self.assertEqual(set(all_ids), set(expected_ids))

    def test_execute_computes_hmac_only_for_rows_with_registration(self):
        batch = [_row("has-reg", registration="AB12CDE"), _row("no-reg", registration=None)]
        conn = FakeConnection(batch_pages=[batch, []])

        import asyncio
        asyncio.run(retention_sweep.run_sweep(conn, VALID_KEY, CUTOFF, 500, execute=True))

        sql, args_list = conn.executemany_calls[0]
        by_id = {row_id: digest for digest, row_id, _cutoff in args_list}
        self.assertEqual(by_id["has-reg"], retention_sweep.vrm_hmac(VALID_KEY, "AB12CDE"))
        self.assertIsNone(by_id["no-reg"])


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

class TestIdempotence(unittest.TestCase):

    def test_rerun_with_zero_candidates_issues_zero_updates(self):
        conn = FakeConnection(batch_pages=[[]])

        import asyncio
        result = asyncio.run(retention_sweep.run_sweep(conn, VALID_KEY, CUTOFF, 500, execute=True))

        self.assertEqual(conn.executemany_calls, [])
        self.assertEqual(result["stale_sensitive_fields"], 0)


# ---------------------------------------------------------------------------
# Verification block runs in both modes
# ---------------------------------------------------------------------------

class TestVerificationBothModes(unittest.TestCase):

    def _verification_calls(self, conn):
        return [c for c in conn.calls if c[0] == "fetchval"]

    def test_verification_runs_in_dry_run_mode(self):
        conn = FakeConnection(
            responses={
                "stale_sensitive_fields": 7,
                "pseudonymised_total": 100,
                "pseudonymised_with_hmac": 95,
                "pseudonymised_with_sensitive_fields": 0,
            }
        )
        import asyncio
        result = asyncio.run(retention_sweep.run_sweep(conn, None, CUTOFF, 500, execute=False))

        # 1 fetchval for count_candidates + 4 for verification = 5.
        self.assertEqual(len(self._verification_calls(conn)), 5)
        self.assertEqual(
            result,
            {
                "stale_sensitive_fields": 7,
                "pseudonymised_total": 100,
                "pseudonymised_with_hmac": 95,
                "pseudonymised_with_sensitive_fields": 0,
            },
        )

    def test_verification_runs_in_execute_mode(self):
        conn = FakeConnection(
            batch_pages=[[]],
            responses={
                "stale_sensitive_fields": 0,
                "pseudonymised_total": 50,
                "pseudonymised_with_hmac": 50,
                "pseudonymised_with_sensitive_fields": 0,
            },
        )
        import asyncio
        result = asyncio.run(retention_sweep.run_sweep(conn, VALID_KEY, CUTOFF, 500, execute=True))

        # 4 verification fetchval calls (no count_candidates in execute mode).
        self.assertEqual(len(self._verification_calls(conn)), 4)
        self.assertEqual(result["pseudonymised_total"], 50)


# ---------------------------------------------------------------------------
# No PII in stdout
# ---------------------------------------------------------------------------

class TestNoPiiInStdout(unittest.TestCase):

    def test_execute_never_prints_registration_value(self):
        distinctive_plate = "XX99XXX"
        batch = [_row("row-1", registration=distinctive_plate)]
        conn = FakeConnection(batch_pages=[batch, []])

        buf = io.StringIO()
        import asyncio
        with redirect_stdout(buf):
            asyncio.run(retention_sweep.run_sweep(conn, VALID_KEY, CUTOFF, 500, execute=True))

        self.assertNotIn(distinctive_plate, buf.getvalue())

    def test_dry_run_never_selects_registration_column(self):
        """The real safety property isn't just "the word doesn't appear in
        stdout" (the verification block legitimately prints the LABEL
        "...plaintext registration : 0", which is fine -- it's a count,
        not a value). The property that actually matters is that dry-run
        never even SELECTs the registration column, so no plaintext VRM
        ever enters Python memory in the first place. Only
        fetch_candidate_batch's query ("SELECT id, registration ...")
        does that, and dry-run must never call it."""
        conn = FakeConnection(
            responses={
                "count_candidates": 1,
                "year_breakdown": [{"yr": 2023, "n": 1}],
                "sample_ids": [{"id": "row-1"}],
            }
        )
        import asyncio
        asyncio.run(retention_sweep.run_sweep(conn, None, CUTOFF, 500, execute=False))

        for _method, sql, _args in conn.calls:
            self.assertNotIn("SELECT id, registration", sql)


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------

class TestImportSafety(unittest.TestCase):

    def test_module_has_main_guard_and_no_side_effects_on_import(self):
        """Importing the module must not parse argv, connect to a
        database, or call sys.exit -- the fact that this test file's
        module-level `import retention_sweep` above already succeeded
        (without DATABASE_URL/VRM_HMAC_KEY set, and without a real event
        loop) is itself most of this assertion; this test additionally
        pins the presence of the callable entry points tests/CLI rely on.
        """
        self.assertTrue(callable(retention_sweep.main))
        self.assertTrue(callable(retention_sweep.run_sweep))
        with open(os.path.join(REPO_ROOT, "scripts", "retention_sweep.py")) as f:
            source = f.read()
        self.assertIn('if __name__ == "__main__":', source)


if __name__ == '__main__':
    unittest.main()
