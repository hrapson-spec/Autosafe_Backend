"""Aggregate builder: lake -> the 13-column comparison frame.

Successor to the never-committed calculate_risk.py. The methodology is the
one empirically recovered from the checked-in artifact (fit stdev 0.251)
and matching hierarchical_make_adjustment.py's documented constants:

    make_rate    = (make_failures + K_GLOBAL * global_rate) / (make_tests + K_GLOBAL)
    segment_rate = (seg_failures  + K_SEGMENT * make_rate)  / (seg_tests  + K_SEGMENT)

with K_GLOBAL=10, K_SEGMENT=5, make = FIRST WHITESPACE TOKEN of model_id
(the recovered convention -- yes, it splits 'LAND ROVER'; fidelity to the
recovered formula wins, and the match-rate gate protects the vocabulary).
Component Risk_* columns get the same two-level shrinkage per category:
the legacy audit's sparse-cell gate requires non-zero component risks on
zero-failure cells, which is only satisfiable if components are smoothed.

Grain and semantics (decision D7, REVISED 2026-08-12):
- denominator: DVSA INITIAL tests (test_type='NT' with a recorded result)
  of the configured class inside the coverage window. The cycle-first
  reconstruction is retired as the population authority -- see
  pipeline/lake/target_population.py for the DVSA definition and evidence;
- failure: the test's own outcome is FAIL. This is DVSA's FINAL failure
  basis (a PRS is the pass it was recorded as) and is UNCHANGED from the
  historical AutoSafe label. Note this is NOT DVSA's *initial* failure
  rate, which counts FAIL+PRS; the earlier docstring called it an
  "initial-failure basis", which was wrong. Whether AutoSafe should adopt
  FAIL+PRS is open decision D12;
- Risk_<cat> numerator: failing tests with >=1 failing item in that
  category (items on PRS tests feed model features, not these rates).

Passing `cycles_relation` restores the retired cycle-first population so the
historical methodology stays reproducible; it is not the default.

Band edges are asserted equivalent to utils.get_age_band /
utils.get_mileage_band by tests -- the SQL below is a twin, not a second
authority.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from pipeline.lake import target_population
from pipeline.lake.rfr_mapping import COMPONENT_CATEGORIES

DEFAULT_K_GLOBAL = 10   # shrinkage of make toward global (hierarchical_make_adjustment.py:65)
DEFAULT_K_SEGMENT = 5   # shrinkage of segment toward make  (hierarchical_make_adjustment.py:66)
MAX_MODEL_ID_LEN = 255  # mot_risk model_id is VARCHAR(255); enforce at build, never silently DB-side

ARTIFACT_COLUMNS = [
    "model_id", "age_band", "mileage_band", "Total_Tests", "Total_Failures",
    "Failure_Risk",
    "Risk_Brakes", "Risk_Suspension", "Risk_Tyres", "Risk_Steering",
    "Risk_Visibility", "Risk_Lamps_Reflectors_And_Electrical_Equipment",
    "Risk_Body_Chassis_Structure",
]

_CATEGORY_TO_COLUMN = {
    "Brakes": "Risk_Brakes",
    "Suspension": "Risk_Suspension",
    "Tyres": "Risk_Tyres",
    "Steering": "Risk_Steering",
    "Visibility": "Risk_Visibility",
    "Lamps_Reflectors_And_Electrical_Equipment":
        "Risk_Lamps_Reflectors_And_Electrical_Equipment",
    "Body_Chassis_Structure": "Risk_Body_Chassis_Structure",
}


@dataclass
class AggregateConfig:
    coverage_start: date
    coverage_end: date
    k_global: int = DEFAULT_K_GLOBAL
    k_segment: int = DEFAULT_K_SEGMENT
    test_class: str = "4"
    dropped_long_model_ids_logged: int = field(default=0, init=False)


# SQL twins of utils.get_age_band / get_mileage_band (equivalence-tested).
AGE_BAND_SQL = """
CASE WHEN age_at_test IS NULL OR age_at_test < 0 THEN 'Unknown'
     WHEN age_at_test < 3 THEN '0-2'
     WHEN age_at_test < 6 THEN '3-5'
     WHEN age_at_test < 11 THEN '6-10'
     WHEN age_at_test < 16 THEN '11-15'
     ELSE '15+' END
"""

MILEAGE_BAND_SQL = """
CASE WHEN test_mileage IS NULL OR test_mileage < 0 OR test_mileage > 500000 THEN 'Unknown'
     WHEN test_mileage < 30000 THEN '0-30k'
     WHEN test_mileage < 60000 THEN '30k-60k'
     WHEN test_mileage < 100000 THEN '60k-100k'
     ELSE '100k+' END
"""


def segment_counts_sql(results_relation: str, items_relation: str,
                       config: AggregateConfig,
                       cycles_relation: Optional[str] = None) -> str:
    """Segment-level raw counts: tests, failures, and per-category failing
    tests, at (model_id, age_band, mileage_band) grain.

    Population is DVSA initial tests. Passing `cycles_relation` selects the
    RETIRED cycle-first population instead (kept for historical reproduction).
    """
    cat_sums = ",\n           ".join(
        f"count(DISTINCT CASE WHEN cat.component_category = '{category}' "
        f"THEN base.test_id END) AS fails_{column}"
        for category, column in _CATEGORY_TO_COLUMN.items()
    )
    if cycles_relation is None:
        population_join = ""
        population_pred = target_population.initial_test_sql("r")
    else:  # legacy path: cycle-first reconstruction (D7, retired 2026-08-12)
        population_join = f"JOIN {cycles_relation} c ON c.test_id = r.test_id"
        population_pred = "c.is_cycle_first"
    return f"""
WITH base AS (
    SELECT r.test_id, r.model_id,
           ({AGE_BAND_SQL}) AS age_band,
           ({MILEAGE_BAND_SQL}) AS mileage_band,
           {target_population.final_failure_sql('r')} AS is_fail
    FROM {results_relation} r
    {population_join}
    WHERE {population_pred}
      AND r.test_class_id = '{config.test_class}'
      AND r.test_date BETWEEN DATE '{config.coverage_start.isoformat()}'
                          AND DATE '{config.coverage_end.isoformat()}'
      AND r.model_id IS NOT NULL
      AND length(r.model_id) <= {MAX_MODEL_ID_LEN}
),
cat AS (
    SELECT DISTINCT i.test_id, i.component_category
    FROM {items_relation} i
    WHERE i.is_fail_item AND i.component_category IS NOT NULL
)
SELECT base.model_id, base.age_band, base.mileage_band,
       count(DISTINCT base.test_id) AS tests,
       count(DISTINCT CASE WHEN base.is_fail THEN base.test_id END) AS failures,
       {cat_sums}
FROM base
LEFT JOIN cat ON cat.test_id = base.test_id AND base.is_fail
GROUP BY 1, 2, 3
"""


def build_segment_counts(con, results_relation: str, items_relation: str,
                         config: AggregateConfig,
                         cycles_relation: Optional[str] = None) -> pd.DataFrame:
    sql = segment_counts_sql(results_relation, items_relation, config,
                             cycles_relation=cycles_relation)
    frame = con.execute(sql).fetchdf()
    dropped = con.execute(f"""
        SELECT count(*) FROM {results_relation}
        WHERE model_id IS NOT NULL AND length(model_id) > {MAX_MODEL_ID_LEN}
    """).fetchone()[0]
    config.dropped_long_model_ids_logged = int(dropped)
    return frame


def _shrunk(counts: pd.Series, trials: pd.Series, prior: pd.Series, k: float) -> pd.Series:
    return (counts + k * prior) / (trials + k)


def compute_aggregate_frame(seg: pd.DataFrame, config: AggregateConfig) -> pd.DataFrame:
    """Apply the recovered two-level EB shrinkage to raw segment counts.

    Input columns: model_id, age_band, mileage_band, tests, failures,
    fails_<Risk_column> per category. Output: the exact 13-column artifact
    frame, column order pinned by ARTIFACT_COLUMNS.
    """
    if seg.empty:
        raise ValueError("no segment counts -- empty coverage window or broken lake join")
    out = seg.copy()
    out["make"] = out["model_id"].str.split().str[0]

    global_rate = out["failures"].sum() / out["tests"].sum()
    make_totals = out.groupby("make")[["tests", "failures"]].sum()
    make_rate = _shrunk(make_totals["failures"], make_totals["tests"],
                        pd.Series(global_rate, index=make_totals.index), config.k_global)
    out = out.merge(make_rate.rename("make_rate"), left_on="make", right_index=True)
    out["Failure_Risk"] = _shrunk(out["failures"], out["tests"],
                                  out["make_rate"], config.k_segment)

    for category, column in _CATEGORY_TO_COLUMN.items():
        fails_col = f"fails_{column}"
        cat_global = out[fails_col].sum() / out["tests"].sum()
        cat_make_totals = out.groupby("make")[[fails_col, "tests"]].sum()
        cat_make_rate = _shrunk(cat_make_totals[fails_col], cat_make_totals["tests"],
                                pd.Series(cat_global, index=cat_make_totals.index),
                                config.k_global)
        out = out.merge(cat_make_rate.rename(f"make_rate_{column}"),
                        left_on="make", right_index=True)
        out[column] = _shrunk(out[fails_col], out["tests"],
                              out[f"make_rate_{column}"], config.k_segment)

    out = out.rename(columns={"tests": "Total_Tests", "failures": "Total_Failures"})
    out["Total_Tests"] = out["Total_Tests"].astype(int)
    out["Total_Failures"] = out["Total_Failures"].astype(int)
    result = out[ARTIFACT_COLUMNS].sort_values(
        ["model_id", "age_band", "mileage_band"]).reset_index(drop=True)
    return result


def coverage_totals(frame: pd.DataFrame) -> Dict[str, int]:
    """The DATASET_TOTAL_* constants for report_contract.py, from the frame
    (claim_sweep re-verifies these from the exported artifact)."""
    return {
        "DATASET_TOTAL_TESTS": int(frame["Total_Tests"].sum()),
        "DATASET_TOTAL_FAILURES": int(frame["Total_Failures"].sum()),
        "rows": int(len(frame)),
    }
