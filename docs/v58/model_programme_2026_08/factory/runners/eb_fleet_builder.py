#!/usr/bin/env python3
"""Fixed-fleet EB prior columns for the `s2.EB.fixedfleet.1m` cell.

PREREG_STAGE2 §5: "the adopted-set primary config with prior tables fitted ONCE
ON THE FIXED FLEET instead of per-rung. Report-only; feeds the Q1
rows-vs-information decomposition by separating prior-scale value."

So the variant is a change of PRIOR SCOPE, nothing else. This builder recomputes
the three TABLE-derived EB columns over the whole panel population and leaves
everything else in the B0-104 frame untouched:

    model_age_fail_rate_eb   3-level hierarchy: (model_id, age_band) shrunk
                             toward (make, age_band) shrunk toward global
    make_age_fail_rate_eb    (make, age_band) shrunk toward global
    eb_unified_prior         = model_age_fail_rate_eb (module :725, :755)

    prior_fail_rate_smoothed CARRIED THROUGH UNCHANGED. It is a per-VEHICLE
                             history ratio (module :403-406), not a fleet table,
                             so the prior-scope variant must not move it.
                             Changing it would confound this cell.

STRICT-DATE DISCIPLINE. "Fitted once" must not mean "fitted on the future". Each
target row reads the tables as they stood using events STRICTLY EARLIER than its
own target date: one expanding pass over the fixed fleet, ordered by date. That
is one fit over one population (not per-rung) AND leakage-free. A single
frozen-at-the-end table would leak every later outcome into every earlier row.

Emits a drop-in `extra_frame`: the full B0-104 column set keyed by test_id, with
the three columns above replaced. Sidecar JSON records every simplification.
"""
import argparse
import json
import os
import sys
from typing import List, Optional

#: Module semantics being mirrored (feature_engineering_v55, repaired copy).
AGE_BANDS = (("0-3", 0, 3), ("3-5", 4, 5), ("6-10", 6, 10), ("11-15", 11, 15),
             ("15+", 16, 10_000))
BASE_RATE = 0.28                     # module :675 fallback
#: EB shrinkage strength (pseudo-counts toward the parent level). The module
#: consumes fitted artifacts and does not expose m; this is the builder's own
#: prior weight, recorded in the sidecar.
DEFAULT_PRIOR_STRENGTH = 50.0

REPLACED_COLUMNS = ("model_age_fail_rate_eb", "make_age_fail_rate_eb",
                    "eb_unified_prior")
CARRIED_UNCHANGED = ("prior_fail_rate_smoothed",)


def relation(globs: str) -> str:
    """One FROM-able relation over one or more comma-separated globs.

    The fixed fleet is ONE population: the train and eval frames must be fitted
    together, not once each, or "fitted once on the fixed fleet" is false.
    """
    parts = [g.strip() for g in str(globs).split(",") if g.strip()]
    listed = ", ".join("'" + g.replace("'", "''") + "'" for g in parts)
    return f"read_parquet([{listed}], union_by_name=true)"


def age_band_sql(age_years_expr: str) -> str:
    """SQL twin of feature_engineering_v55.get_age_band (int-truncated years)."""
    branches = " ".join(
        f"WHEN {lo} <= floor({age_years_expr}) AND floor({age_years_expr}) <= {hi} "
        f"THEN '{name}'" for name, lo, hi in AGE_BANDS)
    return f"(CASE WHEN {age_years_expr} IS NULL THEN '3-5' {branches} ELSE '15+' END)"


def build_sql(frame_glob: str, label: str, prior_strength: float) -> str:
    """Expanding, strictly-prior EB tables over the whole fixed fleet.

    Every rate at target date t uses events with tgt_date < t only. Ties on the
    same calendar date are excluded from their own rate (RANGE ... PRECEDING with
    an exclusive bound via the day-shifted frame), which is the same
    strictly-earlier-day rule the factory's as_of_state uses.
    """
    rel = relation(frame_glob)
    age = "(date_diff('day', tgt_fud, tgt_date) / 365.25)"
    return f"""
WITH ev AS (
    SELECT tgt_id, vehicle_id, tgt_date,
           coalesce(nullif(trim(tgt_model_id), ''), 'UNKNOWN') AS model_key,
           coalesce(nullif(trim(tgt_make), ''), 'UNKNOWN')     AS make_key,
           {age_band_sql(age)}                                 AS age_band,
           CAST({label} AS INTEGER)                            AS y
    FROM {rel}
),
day_global AS (
    SELECT tgt_date, count(*) AS n, sum(y) AS k FROM ev GROUP BY tgt_date
),
day_make AS (
    SELECT make_key, age_band, tgt_date, count(*) AS n, sum(y) AS k
    FROM ev GROUP BY make_key, age_band, tgt_date
),
day_model AS (
    SELECT model_key, age_band, tgt_date, count(*) AS n, sum(y) AS k
    FROM ev GROUP BY model_key, age_band, tgt_date
),
-- expanding sums EXCLUDING the current date: sum(...) - the day's own value
cum_global AS (
    SELECT tgt_date,
           sum(n) OVER w - n AS n_prior, sum(k) OVER w - k AS k_prior
    FROM day_global WINDOW w AS (ORDER BY tgt_date ROWS UNBOUNDED PRECEDING)
),
cum_make AS (
    SELECT make_key, age_band, tgt_date,
           sum(n) OVER w - n AS n_prior, sum(k) OVER w - k AS k_prior
    FROM day_make
    WINDOW w AS (PARTITION BY make_key, age_band ORDER BY tgt_date
                 ROWS UNBOUNDED PRECEDING)
),
cum_model AS (
    SELECT model_key, age_band, tgt_date,
           sum(n) OVER w - n AS n_prior, sum(k) OVER w - k AS k_prior
    FROM day_model
    WINDOW w AS (PARTITION BY model_key, age_band ORDER BY tgt_date
                 ROWS UNBOUNDED PRECEDING)
),
rates AS (
    SELECT e.tgt_id, e.vehicle_id, e.tgt_date,
           CASE WHEN g.n_prior > 0 THEN CAST(g.k_prior AS DOUBLE) / g.n_prior
                ELSE {BASE_RATE} END AS r_global,
           coalesce(mk.n_prior, 0) AS n_make, coalesce(mk.k_prior, 0) AS k_make,
           coalesce(md.n_prior, 0) AS n_model, coalesce(md.k_prior, 0) AS k_model
    FROM ev e
    JOIN cum_global g ON g.tgt_date = e.tgt_date
    LEFT JOIN cum_make mk ON mk.make_key = e.make_key
                         AND mk.age_band = e.age_band AND mk.tgt_date = e.tgt_date
    LEFT JOIN cum_model md ON md.model_key = e.model_key
                          AND md.age_band = e.age_band AND md.tgt_date = e.tgt_date
),
eb AS (
    SELECT tgt_id, vehicle_id, tgt_date, r_global,
           (k_make + {prior_strength} * r_global) / (n_make + {prior_strength})
               AS make_age_fail_rate_eb,
           n_make, n_model
    FROM rates
),
eb2 AS (
    SELECT e.tgt_id, e.vehicle_id, e.tgt_date, e.r_global, e.make_age_fail_rate_eb,
           (r.k_model + {prior_strength} * e.make_age_fail_rate_eb)
               / (r.n_model + {prior_strength}) AS model_age_fail_rate_eb,
           e.n_make, e.n_model
    FROM eb e JOIN rates r ON r.tgt_id = e.tgt_id
)
SELECT tgt_id AS test_id, vehicle_id, tgt_date,
       model_age_fail_rate_eb,
       make_age_fail_rate_eb,
       model_age_fail_rate_eb AS eb_unified_prior,
       r_global AS eb_global_rate_asof,
       n_make AS eb_n_prior_make_age,
       n_model AS eb_n_prior_model_age
FROM eb2
"""


def build_frozen_sql(tables_from_glob: str, frame_glob: str, label: str,
                     prior_strength: float) -> str:
    """EB tables FROZEN at the end of the source window, applied to later rows.

    This is the serving-faithful semantics for an EVAL/CONFIRM/DRIFT frame: the
    deployed artifact ships tables built from its training window and does not
    refresh them per row. It is leakage-safe ONLY because every target row is
    strictly later than every source row — run() hard-asserts the windows are
    disjoint and refuses otherwise (a frozen table applied INSIDE its own
    window would leak later outcomes into earlier rows).
    """
    src = relation(tables_from_glob)
    rel = relation(frame_glob)
    age = "(date_diff('day', tgt_fud, tgt_date) / 365.25)"
    return f"""
WITH src AS (
    SELECT coalesce(nullif(trim(tgt_model_id), ''), 'UNKNOWN') AS model_key,
           coalesce(nullif(trim(tgt_make), ''), 'UNKNOWN')     AS make_key,
           {age_band_sql(age)}                                 AS age_band,
           CAST({label} AS INTEGER)                            AS y
    FROM {src}
),
tot_make AS (
    SELECT make_key, age_band, count(*) AS n, sum(y) AS k
    FROM src GROUP BY make_key, age_band
),
tot_model AS (
    SELECT model_key, age_band, count(*) AS n, sum(y) AS k
    FROM src GROUP BY model_key, age_band
),
ev AS (
    SELECT tgt_id, vehicle_id, tgt_date,
           coalesce(nullif(trim(tgt_model_id), ''), 'UNKNOWN') AS model_key,
           coalesce(nullif(trim(tgt_make), ''), 'UNKNOWN')     AS make_key,
           {age_band_sql(age)}                                 AS age_band
    FROM {rel}
),
rates AS (
    SELECT e.tgt_id, e.vehicle_id, e.tgt_date,
           (SELECT CASE WHEN count(*) > 0
                        THEN CAST(sum(y) AS DOUBLE) / count(*)
                        ELSE {BASE_RATE} END FROM src)          AS r_global,
           coalesce(mk.n, 0) AS n_make, coalesce(mk.k, 0) AS k_make,
           coalesce(md.n, 0) AS n_model, coalesce(md.k, 0) AS k_model
    FROM ev e
    LEFT JOIN tot_make mk ON mk.make_key = e.make_key AND mk.age_band = e.age_band
    LEFT JOIN tot_model md ON md.model_key = e.model_key AND md.age_band = e.age_band
),
eb AS (
    SELECT tgt_id, vehicle_id, tgt_date, r_global,
           (k_make + {prior_strength} * r_global) / (n_make + {prior_strength})
               AS make_age_fail_rate_eb,
           n_make, n_model, k_model
    FROM rates
)
SELECT tgt_id AS test_id, vehicle_id, tgt_date,
       (k_model + {prior_strength} * make_age_fail_rate_eb)
           / (n_model + {prior_strength}) AS model_age_fail_rate_eb,
       make_age_fail_rate_eb,
       (k_model + {prior_strength} * make_age_fail_rate_eb)
           / (n_model + {prior_strength}) AS eb_unified_prior,
       r_global AS eb_global_rate_asof,
       n_make AS eb_n_prior_make_age,
       n_model AS eb_n_prior_model_age
FROM eb
"""


def run(frame_glob: str, out_path: str, *, b0_frame: Optional[str] = None,
        label: str = "y_final", prior_strength: float = DEFAULT_PRIOR_STRENGTH,
        tables_from: Optional[str] = None, con=None, memory_limit: str = "3GB",
        temp_directory: Optional[str] = None) -> dict:
    import duckdb

    con = con or duckdb.connect()
    con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    if temp_directory:
        os.makedirs(temp_directory, exist_ok=True)
        con.execute(f"PRAGMA temp_directory='{temp_directory}'")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    frozen_at = None
    if tables_from:
        max_src = con.execute(
            f"SELECT max(tgt_date) FROM {relation(tables_from)}").fetchone()[0]
        min_tgt = con.execute(
            f"SELECT min(tgt_date) FROM {relation(frame_glob)}").fetchone()[0]
        if max_src is None or min_tgt is None or not max_src < min_tgt:
            raise ValueError(
                f"frozen-mode windows must be disjoint: tables_from ends "
                f"{max_src} but the target frame starts {min_tgt}. A frozen "
                f"table applied inside its own window leaks later outcomes "
                f"into earlier rows; use the expanding mode instead.")
        frozen_at = max_src
        eb_sql = build_frozen_sql(tables_from, frame_glob, label, prior_strength)
    else:
        eb_sql = build_sql(frame_glob, label, prior_strength)
    if b0_frame:
        # drop-in replacement: the whole B0-104 set with three columns swapped
        b0 = relation(b0_frame)
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {b0}").fetchall()]
        keep = [c for c in cols if c not in REPLACED_COLUMNS and c != "vehicle_id"]
        projected = ", ".join(f'b."{c}"' for c in keep)
        select = (f"SELECT {projected}, "
                  f"e.model_age_fail_rate_eb, e.make_age_fail_rate_eb, "
                  f"e.eb_unified_prior, e.eb_global_rate_asof, "
                  f"e.eb_n_prior_make_age, e.eb_n_prior_model_age "
                  f"FROM {b0} b JOIN ({eb_sql}) e ON e.test_id = b.test_id")
    else:
        select = eb_sql
    con.execute(f"COPY ({select}) TO '{out_path}' "
                f"(FORMAT parquet, COMPRESSION zstd)")

    n_rows, n_veh, mn, mx = con.execute(
        f"SELECT count(*), count(DISTINCT vehicle_id), min(eb_unified_prior), "
        f"max(eb_unified_prior) FROM ({eb_sql})").fetchone()
    manifest = {
        "out_path": out_path,
        "source_frame": frame_glob,
        "b0_frame": b0_frame,
        "drop_in_replacement": bool(b0_frame),
        "label": label,
        "rows": int(n_rows), "vehicles": int(n_veh),
        "eb_unified_prior_range": [float(mn), float(mx)],
        "prior_strength_pseudocounts": prior_strength,
        "replaced_columns": list(REPLACED_COLUMNS),
        "carried_unchanged": list(CARRIED_UNCHANGED),
        "tables_from": tables_from,
        "frozen_at": str(frozen_at) if frozen_at else None,
        "scope": (("FROZEN TABLES: fitted once over the source window "
                   f"(tables_from, ends {frozen_at}) and applied unchanged to "
                   "every later target row — the serving-faithful semantics "
                   "for eval/confirm/drift frames.") if tables_from else
                  ("PER-RUNG / FIXED FLEET: expanding tables fitted over the "
                   "frame glob itself (PREREG_STAGE2 section 5, OWNER-AMEND-10).")),
        "as_of_rule": (("frozen at source-window end; leakage-safe ONLY because "
                        "the windows are disjoint (hard-asserted: every target "
                        "row is strictly later than every source row).")
                       if tables_from else
                       ("expanding, STRICTLY EARLIER CALENDAR DAYS: each target's "
                        "rates exclude its own date. A frozen end-of-period table "
                        "would leak every later outcome into every earlier row.")),
        "simplifications": [
            "EB shrinkage is a fixed pseudo-count prior (m=%g) toward the parent "
            "level, not a fitted Beta-Binomial; the module consumes artifacts and "
            "never exposes m." % prior_strength,
            "age_band mirrors get_age_band on int-truncated years; a NULL "
            "first_use_date takes the module's default band '3-5' (module :629 "
            "vehicle_age default 5).",
            "model key is the lake model_id ('MAKE MODEL'); serving keys on "
            "f'{make} {model}' -- the [KEY] consolidate_models caveat applies.",
            "prior_fail_rate_smoothed is NOT recomputed: it is a per-vehicle "
            "ratio, not a fleet table, and moving it would confound the "
            "prior-scope contrast.",
            "rates are fitted on the same label as the cell (%s); a rate fitted "
            "on the other basis would not match the target." % label,
        ],
    }
    with open(out_path + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, default=str)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.eb_fleet_builder",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frame", required=True,
                    help="the FULL panel population (the fixed fleet); comma-separate "
                         "several globs to fit train+eval as ONE fleet")
    ap.add_argument("--out", required=True)
    ap.add_argument("--b0-frame", default=None,
                    help="B0-104 frame(s), comma-separated, for a drop-in extra_frame")
    ap.add_argument("--tables-from", default=None,
                    help="FROZEN mode: fit the tables over THIS glob (a training "
                         "window) and apply them to --frame rows, which must all "
                         "be strictly later. Serving-faithful for eval/confirm/"
                         "drift frames; refuses overlapping windows.")
    ap.add_argument("--label", default="y_final", choices=("y_final", "y_initial"))
    ap.add_argument("--prior-strength", type=float, default=DEFAULT_PRIOR_STRENGTH)
    ap.add_argument("--memory-limit", default="3GB")
    ap.add_argument("--temp-dir", default=None)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    manifest = run(a.frame, a.out, b0_frame=a.b0_frame, label=a.label,
                   prior_strength=a.prior_strength, tables_from=a.tables_from,
                   memory_limit=a.memory_limit, temp_directory=a.temp_dir)
    print(json.dumps(manifest, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
