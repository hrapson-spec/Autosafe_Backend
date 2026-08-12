"""Test-cycle reconstruction: fail -> retest chains collapse to one MOT event.

Counting retests as independent tests double-counts near-certain passes and
depresses the failure rate, which is why the lost pipeline kept
cycle_history/ and cycle_history_dedup_dataset/ trees. This module rebuilds
that logic in-repo, deterministically.

Cycle rule (decisions D7 + D13, docs/v58/DECISIONS.md):
- ordering is per vehicle by the SEMANTIC chronology key
  (test_date, type_rank, outcome_rank, test_id) — see chronology_key().
  test_id is a determinism tiebreak ONLY: within a day it agrees with
  outcome order 49.91% of the time (= chance, measured 2026-08-12), so it
  must never carry chronological meaning (decision D13);
- a test CONTINUES the previous cycle iff the previous test's canonical
  outcome was FAIL and the gap is <= gap_days (default 45: the free-retest
  window is 10 working days, but paid partial retests within a repair
  attempt belong to the same underlying event);
- PASS and PRS are definitive outcomes that close their cycle (PRS is a
  pass whose defects were rectified at the station);
- ABANDONED/ABORTED/REFUSED/UNKNOWN never open a "previous cycle" link but
  do belong to the cycle they follow when within the gap of a FAIL.

The reconciliation gate (pipeline/aggregates) must reproduce the checked-in
artifact's 26.9% global rate on an equivalent window; if it cannot, this
rule -- especially gap_days -- is the first suspect and is config, not code.

Both implementations below are asserted equivalent in tests:
- assign_cycles(): pure-Python rule of record;
- build_cycles_sql(): the DuckDB window-function twin used in production.
"""
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

DEFAULT_CYCLE_GAP_DAYS = 45

# --- Within-day chronology (decision D13) -----------------------------------
# The initial test precedes its retests precedes appeals; an interrupted
# attempt precedes the completed test that follows it; a failure precedes its
# same-day rectified resolution. Strict indexing on test_type: an unknown code
# must fail loud, not sort somewhere silently.
TEST_TYPE_RANK = {"NT": 0, "PL": 1, "PV": 1, "RT": 1, "ES": 2, "EI": 2}
OUTCOME_RANK = {"ABANDONED": 0, "ABORTED": 0, "ABORTED_VE": 0, "REFUSED": 0,
                "UNKNOWN": 0, "FAIL": 1, "PRS": 2, "PASS": 3}

TYPE_RANK_SQL = ("CASE test_type WHEN 'NT' THEN 0 WHEN 'PL' THEN 1 "
                 "WHEN 'PV' THEN 1 WHEN 'RT' THEN 1 WHEN 'ES' THEN 2 "
                 "WHEN 'EI' THEN 2 ELSE 3 END")
OUTCOME_RANK_SQL = ("CASE outcome WHEN 'FAIL' THEN 1 WHEN 'PRS' THEN 2 "
                    "WHEN 'PASS' THEN 3 ELSE 0 END")

# The four-part key, as it must appear in every window ORDER BY / row() of the
# SQL twin. Tests assert the generated SQL never orders by a bare test_id.
CHRONOLOGY_ORDER_SQL = "test_date, type_rank, outcome_rank, test_id"
CHRONOLOGY_ROW_SQL = "row(test_date, type_rank, outcome_rank, test_id)"


def chronology_key(row: dict) -> Tuple:
    """Deterministic semantic order of one vehicle's tests (decision D13)."""
    return (row["test_date"], TEST_TYPE_RANK[row["test_type"]],
            OUTCOME_RANK[row["outcome"]], row["test_id"])


@dataclass
class CycleRow:
    """Cycle assignment for one test row (mirrors the cycles parquet)."""

    test_id: int
    vehicle_id: int
    cycle_id: int                 # test_id of the cycle's first test
    is_cycle_first: bool
    cycle_outcome: str            # final canonical outcome within the cycle
    prev_cycle_test_id: Optional[int]   # last test of the previous cycle
    prev_cycle_outcome: Optional[str]   # that cycle's final outcome
    days_since_prev_cycle: Optional[int]


def assign_cycles(rows: Iterable[dict], gap_days: int = DEFAULT_CYCLE_GAP_DAYS) -> List[CycleRow]:
    """Pure-Python cycle assignment (rule of record; production uses the SQL
    twin). `rows` are dicts with test_id, vehicle_id, test_date (date),
    test_type (DVSA code) and outcome (canonical) -- any vehicle mix, any
    order. Physical input order can never affect the result: ordering comes
    from chronology_key() alone (decision D13)."""
    out: List[CycleRow] = []
    by_vehicle: dict = {}
    for row in rows:
        by_vehicle.setdefault(row["vehicle_id"], []).append(row)

    for vehicle_id, tests in by_vehicle.items():
        if len({r["test_id"] for r in tests}) != len(tests):
            raise ValueError(
                f"duplicate test_id within vehicle {vehicle_id}: test_id is a "
                f"primary key and a duplicate would fan out the SQL twin's "
                f"self-join -- refusing to assign cycles")
        tests.sort(key=chronology_key)
        cycles: List[List[dict]] = []
        for row in tests:
            if cycles:
                prev = cycles[-1][-1]
                gap = (row["test_date"] - prev["test_date"]).days
                if prev["outcome"] == "FAIL" and gap <= gap_days:
                    cycles[-1].append(row)
                    continue
            cycles.append([row])

        prev_cycle: Optional[List[dict]] = None
        for cycle in cycles:
            cycle_id = cycle[0]["test_id"]
            outcome = _cycle_outcome(cycle)
            for i, row in enumerate(cycle):
                if prev_cycle is None:
                    prev_test_id = prev_outcome = prev_days = None
                else:
                    prev_last = prev_cycle[-1]
                    prev_test_id = prev_last["test_id"]
                    prev_outcome = _cycle_outcome(prev_cycle)
                    prev_days = (cycle[0]["test_date"] - prev_last["test_date"]).days
                out.append(CycleRow(
                    test_id=row["test_id"],
                    vehicle_id=vehicle_id,
                    cycle_id=cycle_id,
                    is_cycle_first=(i == 0),
                    cycle_outcome=outcome,
                    prev_cycle_test_id=prev_test_id,
                    prev_cycle_outcome=prev_outcome,
                    days_since_prev_cycle=prev_days,
                ))
            prev_cycle = cycle
    out.sort(key=lambda r: (r.vehicle_id, r.cycle_id, not r.is_cycle_first, r.test_id))
    return out


def _cycle_outcome(cycle: List[dict]) -> str:
    """A cycle's outcome is its last DEFINITIVE result (PASS/FAIL/PRS);
    a cycle of only abandoned/aborted tests keeps its last raw outcome."""
    for row in reversed(cycle):
        if row["outcome"] in ("PASS", "FAIL", "PRS"):
            return row["outcome"]
    return cycle[-1]["outcome"]


def build_cycles_sql(results_relation: str, gap_days: int = DEFAULT_CYCLE_GAP_DAYS) -> str:
    """DuckDB SQL twin of assign_cycles over a results relation/glob.

    `results_relation` is any FROM-able expression exposing test_id,
    vehicle_id, test_date, test_type, outcome. Emits one row per test with
    the CycleRow columns (plus test_year for partitioning). Every window
    orders by the D13 chronology key -- never by bare test_id.
    """
    return f"""
WITH ranked AS (
    SELECT test_id, vehicle_id, test_date, outcome,
           {TYPE_RANK_SQL} AS type_rank,
           {OUTCOME_RANK_SQL} AS outcome_rank
    FROM {results_relation}
),
ordered AS (
    SELECT *,
           lag(test_date) OVER w AS prev_date,
           lag(outcome)   OVER w AS prev_outcome_row
    FROM ranked
    WINDOW w AS (PARTITION BY vehicle_id ORDER BY {CHRONOLOGY_ORDER_SQL})
),
flagged AS (
    SELECT *,
           CASE WHEN prev_date IS NULL THEN 1
                WHEN prev_outcome_row = 'FAIL'
                     AND date_diff('day', prev_date, test_date) <= {gap_days} THEN 0
                ELSE 1 END AS is_cycle_first_int
    FROM ordered
),
numbered AS (
    SELECT *,
           sum(is_cycle_first_int) OVER (PARTITION BY vehicle_id
                                         ORDER BY {CHRONOLOGY_ORDER_SQL}
                                         ROWS UNBOUNDED PRECEDING) AS cycle_seq
    FROM flagged
),
cycles AS (
    -- 2026-08-12 twin repair (#16): chronological positions, not min/max ids.
    -- 2026-08-12 D13 repair (#18): the chronological order itself is the
    -- semantic key -- within a day, test_id order is a coin flip (49.91%%),
    -- so type_rank/outcome_rank decide and test_id only breaks exact ties.
    SELECT *,
           arg_min(test_id, {CHRONOLOGY_ROW_SQL}) OVER c AS cycle_id,
           coalesce(
               arg_max(CASE WHEN outcome IN ('PASS','FAIL','PRS') THEN test_id END,
                       CASE WHEN outcome IN ('PASS','FAIL','PRS')
                            THEN {CHRONOLOGY_ROW_SQL} END) OVER c,
               arg_max(test_id, {CHRONOLOGY_ROW_SQL}) OVER c
           ) AS outcome_test_id,
           arg_max(test_id, {CHRONOLOGY_ROW_SQL}) OVER c AS last_test_id,
           min(test_date) OVER c AS cycle_start_date,
           max(test_date) OVER c AS cycle_end_date
    FROM numbered
    WINDOW c AS (PARTITION BY vehicle_id, cycle_seq)
),
with_outcome AS (
    SELECT c.*, o.outcome AS cycle_outcome
    FROM cycles c
    JOIN cycles o
      ON o.vehicle_id = c.vehicle_id AND o.cycle_seq = c.cycle_seq
     AND o.test_id = c.outcome_test_id
),
cycle_level AS (
    SELECT DISTINCT vehicle_id, cycle_seq, cycle_id, cycle_outcome,
                    last_test_id, cycle_start_date, cycle_end_date
    FROM with_outcome
),
linked AS (
    SELECT vehicle_id, cycle_seq, cycle_id,
           lag(last_test_id)    OVER v AS prev_cycle_test_id,
           lag(cycle_outcome)   OVER v AS prev_cycle_outcome,
           lag(cycle_end_date)  OVER v AS prev_cycle_end_date
    FROM cycle_level
    WINDOW v AS (PARTITION BY vehicle_id ORDER BY cycle_seq)
)
SELECT w.test_id,
       w.vehicle_id,
       w.cycle_id,
       (w.is_cycle_first_int = 1) AS is_cycle_first,
       w.cycle_outcome,
       l.prev_cycle_test_id,
       l.prev_cycle_outcome,
       CASE WHEN l.prev_cycle_end_date IS NULL THEN NULL
            ELSE date_diff('day', l.prev_cycle_end_date, w.cycle_start_date)
       END AS days_since_prev_cycle,
       year(w.test_date) AS test_year
FROM with_outcome w
JOIN linked l
  ON l.vehicle_id = w.vehicle_id AND l.cycle_seq = w.cycle_seq
"""
