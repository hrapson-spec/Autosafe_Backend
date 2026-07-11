"""Tests for the 12-month lead-deletion retention job."""

import asyncio
import os
import sys
import unittest
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(REPO_ROOT, "scripts"))

import lead_retention_sweep  # noqa: E402


CUTOFF = datetime(2025, 7, 11, 12, 0, 0)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, pages=None, stale=0, candidate_count=0):
        self.pages = list(pages or [[]])
        self.stale = stale
        self.candidate_count = candidate_count
        self.calls = []

    def transaction(self):
        self.calls.append(("transaction", "", ()))
        return FakeTransaction()

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "GROUP BY lead_type" in sql:
            return []
        if "SELECT id" in sql:
            if "FOR UPDATE SKIP LOCKED" in sql:
                return self.pages.pop(0) if self.pages else []
            return [{"id": "sample-1"}]
        raise AssertionError(f"unexpected fetch SQL: {sql}")

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "WITH deleted AS" in sql:
            return len(args[0])
        if "created_at < $1" in sql:
            if "verification" in sql:
                return self.stale
            return self.candidate_count
        raise AssertionError(f"unexpected fetchval SQL: {sql}")

    async def close(self):
        return None


class TestLeadRetentionSweep(unittest.TestCase):
    def test_calendar_month_cutoff(self):
        self.assertEqual(
            lead_retention_sweep.compute_cutoff(datetime(2026, 7, 31), 12),
            datetime(2025, 7, 31),
        )

    def test_dry_run_never_deletes_or_selects_pii(self):
        conn = FakeConnection(candidate_count=3, stale=3)

        result = asyncio.run(
            lead_retention_sweep.run_sweep(
                conn, cutoff=CUTOFF, batch_size=500, execute=False
            )
        )

        self.assertEqual(result["stale_leads"], 3)
        all_sql = "\n".join(call[1] for call in conn.calls)
        self.assertNotIn("DELETE", all_sql.upper())
        self.assertNotIn("email", all_sql.lower())
        self.assertNotIn("postcode", all_sql.lower())
        self.assertNotIn("registration", all_sql.lower())

    def test_execute_deletes_assignments_before_each_lead_batch(self):
        pages = [[{"id": "id-1"}, {"id": "id-2"}], []]
        conn = FakeConnection(pages=pages, stale=0)

        result = asyncio.run(
            lead_retention_sweep.run_sweep(
                conn, cutoff=CUTOFF, batch_size=500, execute=True
            )
        )

        delete_calls = [call for call in conn.calls if "WITH deleted AS" in call[1]]
        self.assertEqual(len(delete_calls), 2)
        self.assertIn("DELETE FROM lead_assignments", delete_calls[0][1])
        self.assertIn("DELETE FROM leads", delete_calls[1][1])
        self.assertEqual(delete_calls[0][2][0], ["id-1", "id-2"])
        self.assertEqual(delete_calls[1][2][0], ["id-1", "id-2"])
        self.assertEqual(result, {"deleted_leads": 2, "stale_leads": 0})

    def test_execute_verification_reports_remaining_old_leads(self):
        conn = FakeConnection(pages=[[]], stale=2)
        result = asyncio.run(
            lead_retention_sweep.run_sweep(
                conn, cutoff=CUTOFF, batch_size=10, execute=True
            )
        )
        self.assertEqual(result["stale_leads"], 2)

    def test_module_has_safe_defaults_and_main_guard(self):
        self.assertEqual(lead_retention_sweep.DEFAULT_RETENTION_MONTHS, 12)
        self.assertTrue(callable(lead_retention_sweep.main))
        path = os.path.join(REPO_ROOT, "scripts", "lead_retention_sweep.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('if __name__ == "__main__":', source)


if __name__ == "__main__":
    unittest.main()
