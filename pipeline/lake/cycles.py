"""Test-cycle reconstruction: fail -> retest chains collapse to one MOT event.

Counting retests as independent tests double-counts near-certain passes and
depresses the failure rate, which is why the lost pipeline kept
cycle_history/ and cycle_history_dedup_dataset/ trees. This module rebuilds
that logic in-repo, deterministically. Since decision D7 (revised) the cycle
outputs feed HISTORY FEATURES only -- target-population membership comes from
DVSA test_type and is order-free by construction.

Cycle rule (decisions D7 + D13-amended, docs/v58/DECISIONS.md):

- CHRONOLOGY IS NEVER MANUFACTURED. What the data establishes: test_date
  orders days; within a day, DVSA type semantics order STRATA (initial NT
  before retests PL/PV/RT before appeals ES/EI). Order within a stratum is
  unidentified. outcome_rank and test_id give deterministic ROW
  REPRESENTATION only -- no emitted model information may depend on them.
- Cycles therefore operate at (vehicle_id, test_date) DAY-CLUSTER grain.
  A cluster's outcome is a set computation over its rows:
    * no definitive rows: the unanimous outcome, else 'AMBIGUOUS';
    * definitive rows, no FAIL: 'PASS' if any PASS else 'PRS';
    * FAIL and no definitive pass: 'FAIL';
    * FAIL resolved by a definitive pass in a strictly LATER stratum
      (NT-FAIL -> RT-PASS): that resolution -- identified chronology;
    * FAIL and a definitive pass in the SAME stratum: 'AMBIGUOUS' --
      the sequence is not identified and is not invented.
- A cluster CONTINUES the previous cycle iff the previous cluster's outcome
  is 'FAIL' and the day gap is <= gap_days (default 45: the free-retest
  window is 10 working days, but paid partial retests within a repair
  attempt belong to the same underlying event). 'AMBIGUOUS' does not extend
  a chain: extending would assert an unresolved failure the data does not
  establish.
- cycle_outcome is the outcome of the cycle's LAST definitive-bearing
  cluster (else its last cluster).
- cycle_id is min(test_id) over the cycle: a pure identifier, not a claim
  that the row was first. prev_cycle_test_id is the id of the prior cycle's
  outcome-determining row ONLY when that row is unique; otherwise NULL -- an
  explicit unknown, never an arbitrary pick (it is joined downstream for
  prev-mileage features, which are model information).
- is_cycle_first marks the representation-first row of the cycle's first
  cluster: single-row clusters (the overwhelming case) make it exact;
  multi-row first clusters make it bookkeeping, and the permutation
  falsifiers assert no model information depends on which row carries it.

The permutation invariant (enforced in tests/test_pipeline_lake.py):
changing an arbitrary ordering choice -- physical input order, id
assignment within a same-day stratum -- must not change cycle partition,
cluster or cycle outcomes, linkage outcomes, or gap arithmetic.

Both implementations below are asserted equivalent in tests:
- assign_cycles(): pure-Python rule of record;
- build_cycles_sql(): the DuckDB window-SQL twin used in production.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_CYCLE_GAP_DAYS = 45

# --- Within-day semantics (decision D13, amended) ----------------------------
# type_rank encodes ESTABLISHED strata. outcome_rank exists for deterministic
# row representation ONLY and must never decide model information. Strict
# indexing on test_type: an unknown code fails loud, never sorts silently.
TEST_TYPE_RANK = {"NT": 0, "PL": 1, "PV": 1, "RT": 1, "ES": 2, "EI": 2}
OUTCOME_RANK = {"ABANDONED": 0, "ABORTED": 0, "ABORTED_VE": 0, "REFUSED": 0,
                "UNKNOWN": 0, "FAIL": 1, "PRS": 2, "PASS": 3}
DEFINITIVE = ("PASS", "FAIL", "PRS")
AMBIGUOUS = "AMBIGUOUS"

TYPE_RANK_SQL = ("CASE test_type WHEN 'NT' THEN 0 WHEN 'PL' THEN 1 "
                 "WHEN 'PV' THEN 1 WHEN 'RT' THEN 1 WHEN 'ES' THEN 2 "
                 "WHEN 'EI' THEN 2 ELSE 3 END")
OUTCOME_RANK_SQL = ("CASE outcome WHEN 'FAIL' THEN 1 WHEN 'PRS' THEN 2 "
                    "WHEN 'PASS' THEN 3 ELSE 0 END")

# Deterministic REPRESENTATION order (never chronology beyond date+stratum).
CHRONOLOGY_ORDER_SQL = "test_date, type_rank, outcome_rank, test_id"
CHRONOLOGY_ROW_SQL = "row(test_date, type_rank, outcome_rank, test_id)"


def chronology_key(row: dict) -> Tuple:
    """Deterministic representation order for one vehicle's tests.

    (test_date, type_rank) is established chronology; (outcome_rank, test_id)
    is determinism only -- decision D13 (amended) forbids model information
    from depending on it.
    """
    return (row["test_date"], TEST_TYPE_RANK[row["test_type"]],
            OUTCOME_RANK[row["outcome"]], row["test_id"])


@dataclass
class CycleRow:
    """Cycle assignment for one test row (mirrors the cycles parquet)."""

    test_id: int
    vehicle_id: int
    cycle_id: int                 # min(test_id) in the cycle: identifier only
    is_cycle_first: bool
    cycle_outcome: str            # set-based; may be 'AMBIGUOUS'
    prev_cycle_test_id: Optional[int]   # prior cycle's resolving row, iff unique
    prev_cycle_outcome: Optional[str]
    days_since_prev_cycle: Optional[int]


# --- Cluster (vehicle-day) set computations ----------------------------------

def _cluster_outcome(rows: List[dict]) -> str:
    """Set-based outcome of one (vehicle, date) cluster. Order-free."""
    definitive = [r for r in rows if r["outcome"] in DEFINITIVE]
    if not definitive:
        outcomes = {r["outcome"] for r in rows}
        return outcomes.pop() if len(outcomes) == 1 else AMBIGUOUS
    fails = [r for r in definitive if r["outcome"] == "FAIL"]
    passes = [r for r in definitive if r["outcome"] in ("PASS", "PRS")]
    if not fails:
        return "PASS" if any(r["outcome"] == "PASS" for r in passes) else "PRS"
    if not passes:
        return "FAIL"
    max_fail = max(TEST_TYPE_RANK[r["test_type"]] for r in fails)
    max_pass = max(TEST_TYPE_RANK[r["test_type"]] for r in passes)
    if max_pass > max_fail:   # resolution in a strictly later stratum: identified
        resolving = [r for r in passes if TEST_TYPE_RANK[r["test_type"]] == max_pass]
        return "PASS" if any(r["outcome"] == "PASS" for r in resolving) else "PRS"
    if max_pass < max_fail:   # established pass BEFORE the failure: ends failed
        return "FAIL"
    return AMBIGUOUS          # same stratum: sequence unidentified, not invented


def _cluster_resolver_id(rows: List[dict], outcome: str) -> Optional[int]:
    """The id of the row that determined the cluster outcome, iff unique.

    Joined downstream for prev-mileage features (model information), so an
    ambiguous determination must be an explicit NULL, never an arbitrary row.
    """
    if outcome == AMBIGUOUS:
        return None
    if outcome in DEFINITIVE:
        matching = [r for r in rows if r["outcome"] == outcome]
        if outcome in ("PASS", "PRS") and any(r["outcome"] == "FAIL" for r in rows):
            top = max(TEST_TYPE_RANK[r["test_type"]] for r in matching)
            matching = [r for r in matching if TEST_TYPE_RANK[r["test_type"]] == top]
    else:
        matching = [r for r in rows if r["outcome"] == outcome]
    return matching[0]["test_id"] if len(matching) == 1 else None


def assign_cycles(rows: Iterable[dict], gap_days: int = DEFAULT_CYCLE_GAP_DAYS) -> List[CycleRow]:
    """Pure-Python cycle assignment (rule of record; production uses the SQL
    twin). `rows` are dicts with test_id, vehicle_id, test_date (date),
    test_type (DVSA code) and outcome (canonical) -- any vehicle mix, any
    order. Physical input order and within-stratum id assignment can never
    affect emitted model information (decision D13, amended)."""
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

        # Day clusters, in date order (identified chronology).
        clusters: List[Dict] = []
        for row in tests:
            if clusters and clusters[-1]["date"] == row["test_date"]:
                clusters[-1]["rows"].append(row)
            else:
                clusters.append({"date": row["test_date"], "rows": [row]})
        for cl in clusters:
            cl["outcome"] = _cluster_outcome(cl["rows"])
            cl["resolver_id"] = _cluster_resolver_id(cl["rows"], cl["outcome"])
            cl["definitive"] = any(r["outcome"] in DEFINITIVE for r in cl["rows"])

        # Chain clusters into cycles: only an established FAIL extends.
        cycles: List[List[Dict]] = []
        for cl in clusters:
            if cycles:
                prev = cycles[-1][-1]
                gap = (cl["date"] - prev["date"]).days
                if prev["outcome"] == "FAIL" and gap <= gap_days:
                    cycles[-1].append(cl)
                    continue
            cycles.append([cl])

        prev_cycle: Optional[List[Dict]] = None
        for cycle in cycles:
            cycle_id = min(r["test_id"] for cl in cycle for r in cl["rows"])
            outcome_cl = next((cl for cl in reversed(cycle) if cl["definitive"]),
                              cycle[-1])
            outcome = outcome_cl["outcome"]
            if prev_cycle is None:
                prev_test_id = prev_outcome = prev_days = None
            else:
                prev_outcome_cl = next(
                    (cl for cl in reversed(prev_cycle) if cl["definitive"]),
                    prev_cycle[-1])
                prev_test_id = prev_outcome_cl["resolver_id"]
                prev_outcome = prev_outcome_cl["outcome"]
                prev_days = (cycle[0]["date"] - prev_cycle[-1]["date"]).days
            first = True
            for cl in cycle:
                for row in cl["rows"]:
                    out.append(CycleRow(
                        test_id=row["test_id"],
                        vehicle_id=vehicle_id,
                        cycle_id=cycle_id,
                        is_cycle_first=first,
                        cycle_outcome=outcome,
                        prev_cycle_test_id=prev_test_id,
                        prev_cycle_outcome=prev_outcome,
                        days_since_prev_cycle=prev_days,
                    ))
                    first = False
            prev_cycle = cycle
    out.sort(key=lambda r: (r.vehicle_id, r.cycle_id, not r.is_cycle_first, r.test_id))
    return out


def build_cycles_sql(results_relation: str, gap_days: int = DEFAULT_CYCLE_GAP_DAYS) -> str:
    """DuckDB SQL twin of assign_cycles over a results relation/glob.

    `results_relation` is any FROM-able expression exposing test_id,
    vehicle_id, test_date, test_type, outcome. Emits one row per test with
    the CycleRow columns (plus test_year for partitioning). Clusters are
    (vehicle_id, test_date) sets; the only window orderings are over dates
    and established strata -- never bare test_id (decision D13, amended).
    """
    return f"""
WITH ranked AS (
    SELECT test_id, vehicle_id, test_date, outcome,
           {TYPE_RANK_SQL} AS type_rank,
           {OUTCOME_RANK_SQL} AS outcome_rank
    FROM {results_relation}
),
cl AS (  -- one row per (vehicle, day): set aggregates only
    SELECT vehicle_id, test_date,
           min(test_id)                                            AS cluster_min_id,
           bool_or(outcome IN ('PASS','FAIL','PRS'))               AS any_def,
           bool_or(outcome = 'FAIL')                               AS any_fail,
           bool_or(outcome IN ('PASS','PRS'))                      AS any_passlike,
           bool_or(outcome = 'PASS')                               AS any_pass,
           max(CASE WHEN outcome = 'FAIL' THEN type_rank END)      AS max_fail_str,
           max(CASE WHEN outcome IN ('PASS','PRS') THEN type_rank END) AS max_pass_str,
           max(CASE WHEN outcome = 'PASS' THEN type_rank END)      AS max_strict_pass_str,
           count(DISTINCT outcome)                                 AS n_out,
           min(outcome)                                            AS one_out
    FROM ranked
    GROUP BY vehicle_id, test_date
),
cl2 AS (
    SELECT *,
           CASE
             WHEN NOT any_def THEN
                  CASE WHEN n_out = 1 THEN one_out ELSE 'AMBIGUOUS' END
             WHEN NOT any_fail THEN
                  CASE WHEN any_pass THEN 'PASS' ELSE 'PRS' END
             WHEN NOT any_passlike THEN 'FAIL'
             WHEN max_pass_str > max_fail_str THEN
                  CASE WHEN coalesce(max_strict_pass_str, -1) = max_pass_str
                       THEN 'PASS' ELSE 'PRS' END
             WHEN max_pass_str < max_fail_str THEN 'FAIL'
             ELSE 'AMBIGUOUS'
           END AS cluster_outcome
    FROM cl
),
resolver AS (  -- unique outcome-determining row per cluster, else NULL
    SELECT c.vehicle_id, c.test_date,
           CASE WHEN count(*) = 1 THEN min(r.test_id) END AS resolver_id
    FROM cl2 c
    JOIN ranked r
      ON r.vehicle_id = c.vehicle_id AND r.test_date = c.test_date
     AND r.outcome = c.cluster_outcome
     AND (NOT (c.cluster_outcome IN ('PASS','PRS') AND c.any_fail)
          OR r.type_rank = c.max_pass_str)
    WHERE c.cluster_outcome <> 'AMBIGUOUS'
    GROUP BY c.vehicle_id, c.test_date
),
ord_cl AS (
    SELECT c.*, res.resolver_id,
           lag(c.cluster_outcome) OVER v AS prev_cl_outcome,
           lag(c.test_date)       OVER v AS prev_cl_date
    FROM cl2 c
    LEFT JOIN resolver res
      ON res.vehicle_id = c.vehicle_id AND res.test_date = c.test_date
    WINDOW v AS (PARTITION BY c.vehicle_id ORDER BY c.test_date)
),
flagged AS (
    SELECT *,
           CASE WHEN prev_cl_date IS NULL THEN 1
                WHEN prev_cl_outcome = 'FAIL'
                     AND date_diff('day', prev_cl_date, test_date) <= {gap_days} THEN 0
                ELSE 1 END AS is_cycle_start_int
    FROM ord_cl
),
numbered AS (
    SELECT *,
           sum(is_cycle_start_int) OVER (PARTITION BY vehicle_id
                                         ORDER BY test_date
                                         ROWS UNBOUNDED PRECEDING) AS cycle_seq
    FROM flagged
),
cycle_level AS (
    SELECT vehicle_id, cycle_seq,
           min(cluster_min_id)                       AS cycle_id,
           min(test_date)                            AS cycle_start_date,
           max(test_date)                            AS cycle_end_date,
           arg_max(cluster_outcome,
                   row(CASE WHEN any_def THEN 1 ELSE 0 END, test_date)) AS cycle_outcome,
           arg_max(resolver_id,
                   row(CASE WHEN any_def THEN 1 ELSE 0 END, test_date)) AS outcome_resolver_id
    FROM numbered
    GROUP BY vehicle_id, cycle_seq
),
linked AS (
    SELECT *,
           lag(outcome_resolver_id) OVER v AS prev_cycle_test_id,
           lag(cycle_outcome)       OVER v AS prev_cycle_outcome,
           lag(cycle_end_date)      OVER v AS prev_cycle_end_date
    FROM cycle_level
    WINDOW v AS (PARTITION BY vehicle_id ORDER BY cycle_seq)
),
rows_first AS (  -- representation-first row of each cycle (bookkeeping flag)
    SELECT r.test_id, r.vehicle_id, r.test_date, n.cycle_seq,
           row_number() OVER (PARTITION BY r.vehicle_id, n.cycle_seq
                              ORDER BY r.test_date, r.type_rank,
                                       r.outcome_rank, r.test_id) AS rep_pos
    FROM ranked r
    JOIN numbered n
      ON n.vehicle_id = r.vehicle_id AND n.test_date = r.test_date
)
SELECT rf.test_id,
       rf.vehicle_id,
       l.cycle_id,
       (rf.rep_pos = 1) AS is_cycle_first,
       l.cycle_outcome,
       l.prev_cycle_test_id,
       l.prev_cycle_outcome,
       CASE WHEN l.prev_cycle_end_date IS NULL THEN NULL
            ELSE date_diff('day', l.prev_cycle_end_date, l.cycle_start_date)
       END AS days_since_prev_cycle,
       year(rf.test_date) AS test_year
FROM rows_first rf
JOIN linked l
  ON l.vehicle_id = rf.vehicle_id AND l.cycle_seq = rf.cycle_seq
"""
