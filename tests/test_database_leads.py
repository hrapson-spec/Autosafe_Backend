"""
Tests for database.py's update_lead_assignment_outcome (B2): the outcome
UPDATE and the conditional garages.leads_converted increment must run
inside a single `conn.transaction()` so a failure between them cannot
leave one written without the other.

Self-contained fake asyncpg pool/connection double, following the pattern
in tests/test_database_v2.py (this suite keeps its own copy rather than
sharing one, matching that file's no-conftest.py convention -- the repo
has none and these tests keep it that way).
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


# ----------------------------------------------------------------------------
# Fake asyncpg double (execute + transaction, not fetch -- this function
# under test never fetches)
# ----------------------------------------------------------------------------

class FakeAcquireContext:
    """Stands in for asyncpg's `pool.acquire()` async context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeTransactionContext:
    """Stands in for asyncpg's `conn.transaction()` async context manager.

    Records enter/exit into the owning connection's shared `.calls` log,
    tagged with whether an exception is propagating out (asyncpg commits
    on clean exit, rolls back on exception), so tests can assert ordering
    and commit/rollback outcome relative to the execute() calls made
    inside the block.
    """

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._conn.transaction_depth += 1
        self._conn.calls.append(("transaction_enter",))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._conn.transaction_depth -= 1
        self._conn.calls.append(("transaction_exit", exc_type is None))
        return False  # never swallow exceptions


class FakeConnection:
    """Programmable fake for asyncpg.Connection: execute() only."""

    def __init__(self, execute_side_effects=None):
        # Optional list of exceptions (or None) to raise/return per execute()
        # call, consumed in order; None means "succeed with default result".
        self._execute_side_effects = list(execute_side_effects or [])
        self.calls = []
        self.transaction_depth = 0

    def transaction(self):
        return FakeTransactionContext(self)

    async def execute(self, query, *params):
        in_transaction = self.transaction_depth > 0
        self.calls.append(("execute", query, params, in_transaction))
        if self._execute_side_effects:
            effect = self._execute_side_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if isinstance(effect, type) and issubclass(effect, BaseException):
                raise effect()
        return "UPDATE 1"


class FakePool:
    """Stands in for the asyncpg pool returned by database.get_pool()."""

    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquireContext(self.conn)


def _patched_get_pool(pool_or_none):
    """Context manager replacing database.get_pool() with one returning pool_or_none."""
    return patch.object(database, "get_pool", AsyncMock(return_value=pool_or_none))


# ----------------------------------------------------------------------------
# update_lead_assignment_outcome -- transaction wrap
# ----------------------------------------------------------------------------

class TestUpdateLeadAssignmentOutcomeTransaction(unittest.TestCase):

    def test_outcome_update_runs_in_transaction(self):
        """A non-'won' outcome issues exactly one execute, and it must
        happen inside an entered/exited transaction (commit on success)."""
        conn = FakeConnection()
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.update_lead_assignment_outcome("assign-1", "lost")
            self.assertTrue(result)

        asyncio.run(run_test())

        kinds = [c[0] for c in conn.calls]
        self.assertEqual(kinds, ["transaction_enter", "execute", "transaction_exit"])

        # The execute happened while inside the transaction.
        execute_call = conn.calls[1]
        self.assertTrue(execute_call[3], "execute() ran outside the transaction")
        self.assertIn("UPDATE lead_assignments", execute_call[1])

        # Clean exit -> commit (no exception propagating).
        exit_call = conn.calls[2]
        self.assertTrue(exit_call[1], "transaction exited as a rollback, not a commit")

    def test_won_outcome_increments_garage_counter_atomically(self):
        """A 'won' outcome issues two executes (outcome update, then the
        garages.leads_converted increment) and BOTH must run inside the
        same transaction so they commit or roll back together."""
        conn = FakeConnection()
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.update_lead_assignment_outcome("assign-1", "won")
            self.assertTrue(result)

        asyncio.run(run_test())

        kinds = [c[0] for c in conn.calls]
        self.assertEqual(kinds, ["transaction_enter", "execute", "execute", "transaction_exit"])

        first_execute, second_execute = conn.calls[1], conn.calls[2]
        self.assertTrue(first_execute[3], "outcome UPDATE ran outside the transaction")
        self.assertTrue(second_execute[3], "garages increment ran outside the transaction")
        self.assertIn("UPDATE lead_assignments", first_execute[1])
        self.assertIn("UPDATE garages", second_execute[1])
        self.assertIn("leads_converted = leads_converted + 1", second_execute[1])

        exit_call = conn.calls[3]
        self.assertTrue(exit_call[1], "transaction exited as a rollback, not a commit")

    def test_no_pool_returns_false(self):
        """Unchanged pre-existing behaviour: no pool -> False, no attempt
        to acquire a connection or open a transaction."""
        async def run_test():
            with _patched_get_pool(None):
                result = await database.update_lead_assignment_outcome("assign-1", "lost")
            self.assertFalse(result)

        asyncio.run(run_test())

    def test_failure_inside_transaction_returns_false_not_partial_success(self):
        """If the second (garage-increment) execute fails, the whole call
        must report failure -- proving the two writes are coupled, not
        independently best-effort."""
        conn = FakeConnection(execute_side_effects=[None, RuntimeError("boom")])
        pool = FakePool(conn)

        async def run_test():
            with _patched_get_pool(pool):
                result = await database.update_lead_assignment_outcome("assign-1", "won")
            self.assertFalse(result)

        asyncio.run(run_test())

        # The transaction was entered, both executes were attempted (the
        # second raised), and exit was recorded as a rollback (exception
        # propagating out of the `async with` block).
        kinds = [c[0] for c in conn.calls]
        self.assertEqual(kinds, ["transaction_enter", "execute", "execute", "transaction_exit"])
        exit_call = conn.calls[3]
        self.assertFalse(exit_call[1], "transaction exited as a commit despite the failure")


if __name__ == "__main__":
    unittest.main()
