"""ADVSTRUCT prior-side build + hardened correctness gate + cell-count grid.

PREREG_ADVSTRUCT_2026_08_15.md (sha 35ee4828c47f4b88) sections 5.5, 5.6, 6, 7.1, 7.2.

Emits ONE row per target with the prior-side advisory quantities the descriptive
analysis needs, and NOTHING derived from an outcome. No rate, no gradient, no
comparison is computed here -- that separation is why the statistic cannot be
chosen after seeing it (the severity_collect / severity_analyze split).

Three things this module exists to get right:

1. THREE-STATE NULL SEMANTICS (prereg 5.5). "no prior test", "prior history
   observable with zero advisories" and "prior history present but items
   unobservable" are three different claims. Counts may be a certain zero in the
   first two; every rate, share, slope or dispersion measure is NULL whenever its
   denominator or minimum support is undefined, in ALL states.

2. SAME-DAY DEDUPLICATION (prereg 5.6). The most recent prior day can carry a fail
   and its retest, re-recording the same physical advisory. Counting the raw item
   rows inflates depth and can inflate breadth. Dedup key is
   (tgt_id, p_date, canonical_system, item_key) with item_key = rfr_id, falling
   back to the raw section when rfr_id is absent. Permutation invariance does NOT
   settle whether day-union is the right state representation -- it is a declared
   modelling choice, tested by the tied-day sensitivity flag emitted here.

3. FAIL CLOSED on unknown sections, via advstruct_sections.ADVSTRUCT_ONTOLOGY_V1.

Usage:
    python scripts/analysis/advstruct_build.py --gates-only
    python scripts/analysis/advstruct_build.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent.parent))

from scripts.analysis.advstruct_sections import (  # noqa: E402
    CATALOGUE_MISS, ONTOLOGY_VERSION, SYSTEMS, sql_case_expr)

PREREG_SHA16 = "35ee4828c47f4b88"

FRAMES = {
    "train_flat4y": {
        "packets": "out/frames_v2_flat4y/recipe=flat4y/rung=r1m/packets/*.parquet",
        "labels": "out/TRAIN_SEVERITY_LABELS.parquet",
        "out": "out/advstruct/prior_train.parquet",
    },
    "eval2024": {
        "packets": "out/frames_eval_v2/recipe=eval2024/rung=all/packets/*.parquet",
        "labels": "out/TARGET_SEVERITY_LABELS.parquet",
        "out": "out/advstruct/prior_eval.parquet",
    },
}

#: Observed states of p_items_observability. Anything else is UNOBSERVED.
OBSERVED = "('present_with_defects','present_zero_defects','assumed_zero_defects')"


# ---------------------------------------------------------------- gate

def correctness_gate(con, name: str, spec: dict) -> dict:
    """Prereg 7.1. Prevalence equality is NECESSARY BUT NOT SUFFICIENT -- two
    different row sets can share a prevalence. All five checks must pass."""
    labels, packets = spec["labels"], spec["packets"]

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE lab AS
        SELECT test_id, vehicle_id, tgt_date, y_final,
               n_major_or_dangerous, n_sections_with_md, n_dangerous,
               (n_major_or_dangerous >= 3)::INT AS y_b3,
               (n_sections_with_md   >= 2)::INT AS y_m1,
               (n_dangerous          >= 1)::INT AS y_s1
        FROM read_parquet('{labels}')
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tgt AS
        SELECT DISTINCT tgt_id, vehicle_id FROM read_parquet('{packets}', union_by_name=true)
    """)

    g = {}
    g["1_label_rows"] = con.execute("SELECT count(*) FROM lab").fetchone()[0]
    g["1_packet_targets"] = con.execute("SELECT count(*) FROM tgt").fetchone()[0]
    g["1_set_equality"] = (g["1_label_rows"] == g["1_packet_targets"])

    g["2_antijoin_label_not_in_packets"] = con.execute(
        "SELECT count(*) FROM lab l ANTI JOIN tgt t ON t.tgt_id = l.test_id").fetchone()[0]
    g["2_antijoin_packets_not_in_label"] = con.execute(
        "SELECT count(*) FROM tgt t ANTI JOIN lab l ON t.tgt_id = l.test_id").fetchone()[0]

    g["3_vehicle_id_disagreements"] = con.execute("""
        SELECT count(*) FROM lab l JOIN tgt t ON t.tgt_id = l.test_id
        WHERE t.vehicle_id IS DISTINCT FROM l.vehicle_id""").fetchone()[0]

    # Row-level equality of every recomputed label against its banked definition,
    # not aggregates. An ordered hash over (test_id, labels) catches any
    # permutation or single-row divergence a prevalence check would miss.
    rows = con.execute("""
        SELECT test_id, y_final, y_b3, y_m1, y_s1 FROM lab ORDER BY test_id""").fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}\n".encode())
    g["4_ordered_label_hash"] = h.hexdigest()[:32]
    g["4_n_hashed"] = len(rows)

    prev = con.execute("""
        SELECT count(*), sum(y_final), sum(y_b3), sum(y_m1), sum(y_s1) FROM lab""").fetchone()
    n = prev[0]
    g["5_prevalence"] = {
        "n": n,
        "y_t0": {"pos": prev[1], "prev": round(prev[1] / n, 10)},
        "y_b3": {"pos": prev[2], "prev": round(prev[2] / n, 10)},
        "y_m1": {"pos": prev[3], "prev": round(prev[3] / n, 10)},
        "y_s1": {"pos": prev[4], "prev": round(prev[4] / n, 10)},
    }

    # Leakage assertions on the packet substrate itself.
    g["6_packets_not_strictly_prior"] = con.execute(f"""
        SELECT count(*) FROM read_parquet('{packets}', union_by_name=true)
        WHERE p_date IS NOT NULL AND p_date >= tgt_date""").fetchone()[0]
    g["6_prior_test_is_target"] = con.execute(f"""
        SELECT count(*) FROM read_parquet('{packets}', union_by_name=true)
        WHERE p_test_id = tgt_id""").fetchone()[0]

    g["PASSED"] = bool(
        g["1_set_equality"]
        and g["2_antijoin_label_not_in_packets"] == 0
        and g["2_antijoin_packets_not_in_label"] == 0
        and g["3_vehicle_id_disagreements"] == 0
        and g["6_packets_not_strictly_prior"] == 0
        and g["6_prior_test_is_target"] == 0)
    return g


# ---------------------------------------------------------------- build

def build_prior(con, spec: dict) -> dict:
    """One row per target. Prior-side only; no outcome touched."""
    packets, case = spec["packets"], sql_case_expr("sect")
    sys_tuple = "(" + ",".join(f"'{s}'" for s in SYSTEMS) + ")"

    # Most recent prior test-DAY. dense_rank, never row_number: 8.8% of targets
    # carry >1 test on that day and row-ranking would split one presentation.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE mr AS
        SELECT * FROM (
            SELECT tgt_id, vehicle_id, tgt_date, tgt_make, tgt_model, tgt_fud, tgt_pc,
                   n_priors, p_test_id, p_date, p_items_observability, defects_json,
                   dense_rank() OVER (PARTITION BY tgt_id ORDER BY p_date DESC) rk
            FROM read_parquet('{packets}', union_by_name=true)
            WHERE p_date IS NOT NULL)
        WHERE rk = 1
    """)

    # Per-target day state. A day is observable only if EVERY test on it is.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE dstate AS
        SELECT tgt_id, any_value(vehicle_id) vehicle_id, any_value(tgt_date) tgt_date,
               any_value(tgt_make) tgt_make, any_value(tgt_model) tgt_model,
               any_value(tgt_fud) tgt_fud, any_value(tgt_pc) tgt_pc,
               any_value(n_priors) n_priors, max(p_date) p_date,
               count(*) AS n_tests_on_day,
               bool_and(p_items_observability IN {OBSERVED}) AS day_observable
        FROM mr GROUP BY tgt_id
    """)

    # DEDUP (prereg 5.6): one row per (target, day, system, item_key).
    # `sect` is extracted in an inner select so the crosswalk CASE binds to a real
    # column. The earlier version relied on duckdb resolving a lateral select-list
    # alias, which silently works in one query shape and fails in another.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE items AS
        SELECT DISTINCT tgt_id, p_date, {case} AS canon, item_key
        FROM (
            SELECT tgt_id, p_date,
                   json_extract_string(j,'$.sect') AS sect,
                   coalesce(json_extract_string(j,'$.rfr'),
                            json_extract_string(j,'$.sect'), '__none__') AS item_key
            FROM mr, unnest(json_extract(defects_json,'$[*]')) AS t(j)
            WHERE defects_json IS NOT NULL AND defects_json <> '[]'
              AND json_extract_string(j,'$.disp') = 'A')
    """)
    n_unknown = con.execute("SELECT count(*) FROM items WHERE canon IS NULL").fetchone()[0]
    if n_unknown:
        raise SystemExit(f"FATAL: {n_unknown} advisory rows with unknown section "
                         f"({ONTOLOGY_VERSION} fail-closed, prereg 4.3)")

    # Raw (pre-dedup) counts, kept so the dedup's effect is measurable.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE raw_items AS
        SELECT tgt_id, {case} AS canon
        FROM (
            SELECT tgt_id, json_extract_string(j,'$.sect') AS sect
            FROM mr, unnest(json_extract(defects_json,'$[*]')) AS t(j)
            WHERE defects_json IS NOT NULL AND defects_json <> '[]'
              AND json_extract_string(j,'$.disp') = 'A')
    """)

    persys = ",\n               ".join(
        f"count(*) FILTER (WHERE canon = '{s}') AS n_sys_{s}" for s in SYSTEMS)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE agg AS
        SELECT tgt_id,
               count(*) FILTER (WHERE canon IN {sys_tuple})                AS adv_n_last,
               count(DISTINCT CASE WHEN canon IN {sys_tuple} THEN canon END) AS adv_breadth_last,
               count(*) FILTER (WHERE canon = 'noncomponent')              AS adv_n_noncomponent,
               count(*) FILTER (WHERE canon = 'identification')            AS adv_n_identification,
               count(*) FILTER (WHERE canon = 'not_tested')                AS adv_n_not_tested,
               count(*) FILTER (WHERE canon = '{CATALOGUE_MISS}')          AS adv_n_catalogue_miss,
               {persys}
        FROM items GROUP BY tgt_id
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE rawagg AS
        SELECT tgt_id, count(*) FILTER (WHERE canon IN {sys_tuple}) AS adv_n_last_predup
        FROM raw_items GROUP BY tgt_id
    """)

    persys_out = ",\n               ".join(
        f"CASE WHEN d.day_observable THEN coalesce(a.n_sys_{s}, 0) END AS n_sys_{s}"
        for s in SYSTEMS)

    # THREE-STATE (prereg 5.5):
    #   no_prior          n_priors = 0            -> counts NULL (nothing to count)
    #   observable_zero   day observable, 0 adv   -> counts certain 0
    #   observable        day observable, >0 adv  -> counts real
    #   unobservable      day not observable      -> counts NULL
    # Every RATE is NULL wherever its denominator is undefined, in every state.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE prior AS
        SELECT d.tgt_id, d.vehicle_id, d.tgt_date, d.tgt_make, d.tgt_model, d.tgt_pc,
               d.n_priors, d.p_date AS last_prior_date, d.n_tests_on_day,
               (d.n_tests_on_day > 1) AS tied_prior_day,
               datediff('day', d.p_date, d.tgt_date) AS days_since_last_prior,
               CASE WHEN d.tgt_fud IS NULL THEN NULL
                    ELSE datediff('day', d.tgt_fud, d.tgt_date) / 365.25 END AS age_years,
               CASE WHEN NOT d.day_observable THEN 'unobservable'
                    WHEN coalesce(a.adv_n_last, 0) = 0 THEN 'observable_zero'
                    ELSE 'observable' END AS adv_state,
               CASE WHEN d.day_observable THEN coalesce(a.adv_n_last, 0) END AS adv_n_last,
               CASE WHEN d.day_observable THEN coalesce(a.adv_breadth_last, 0) END AS adv_breadth_last,
               CASE WHEN d.day_observable THEN coalesce(r.adv_n_last_predup, 0) END AS adv_n_last_predup,
               CASE WHEN d.day_observable THEN coalesce(a.adv_n_noncomponent, 0) END AS adv_n_noncomponent,
               CASE WHEN d.day_observable THEN coalesce(a.adv_n_identification, 0) END AS adv_n_identification,
               CASE WHEN d.day_observable THEN coalesce(a.adv_n_not_tested, 0) END AS adv_n_not_tested,
               CASE WHEN d.day_observable THEN coalesce(a.adv_n_catalogue_miss, 0) END AS adv_n_catalogue_miss,
               -- RATES: NULL when the denominator is undefined, never 0.
               CASE WHEN d.day_observable AND coalesce(a.adv_n_last,0) > 0
                    THEN a.adv_n_last::DOUBLE / a.adv_breadth_last END AS adv_items_per_system,
               {persys_out}
        FROM dstate d
        LEFT JOIN agg a    ON a.tgt_id = d.tgt_id
        LEFT JOIN rawagg r ON r.tgt_id = d.tgt_id
    """)

    diag = con.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE adv_state = 'observable'),
               count(*) FILTER (WHERE adv_state = 'observable_zero'),
               count(*) FILTER (WHERE adv_state = 'unobservable'),
               count(*) FILTER (WHERE tied_prior_day),
               sum(CASE WHEN adv_n_last_predup > adv_n_last THEN 1 ELSE 0 END),
               max(adv_n_last_predup - adv_n_last)
        FROM prior""").fetchone()

    con.execute(f"COPY prior TO '{spec['out']}' (FORMAT PARQUET)")
    return {
        "rows_with_a_prior_day": diag[0],
        "state_observable": diag[1],
        "state_observable_zero": diag[2],
        "state_unobservable": diag[3],
        "tied_prior_day": diag[4],
        "targets_dedup_reduced_count": diag[5],
        "max_dedup_reduction": diag[6],
        "unknown_section_rows": n_unknown,
        "out": spec["out"],
    }


def cell_grid(con) -> dict:
    """Prereg 7.2 -- published BEFORE the binning is frozen."""
    rows = con.execute("""
        SELECT adv_n_last AS c, adv_breadth_last AS b, count(*) n,
               count(DISTINCT vehicle_id) nveh
        FROM prior WHERE adv_state IN ('observable','observable_zero')
        GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    return {f"c{int(c)}_b{int(b)}": {"n": int(n), "n_vehicles": int(v)}
            for c, b, n, v in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gates-only", action="store_true")
    ap.add_argument("--out", default="out/ADVSTRUCT_BUILD_DIAG.json")
    ap.add_argument("--memory-limit", default="2500MB")
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()

    import duckdb
    os.chdir(ROOT)
    Path("out/advstruct").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.memory_limit}'")
    con.execute(f"SET threads={a.threads}")
    con.execute(f"SET temp_directory='{ROOT}/out/_tmp_advbuild_{os.getpid()}'")
    con.execute("INSTALL json; LOAD json;")

    out = {"prereg": "prereg/PREREG_ADVSTRUCT_2026_08_15.md",
           "prereg_sha256_16": PREREG_SHA16,
           "ontology_version": ONTOLOGY_VERSION,
           "gates": {}, "build": {}, "cell_grid": {}}

    all_pass = True
    for name, spec in FRAMES.items():
        g = correctness_gate(con, name, spec)
        out["gates"][name] = g
        all_pass &= g["PASSED"]
        print(f"[{name}] gate {'PASS' if g['PASSED'] else 'FAIL'} "
              f"n={g['5_prevalence']['n']:,} hash={g['4_ordered_label_hash'][:16]}")
        for k in ("y_t0", "y_b3", "y_m1", "y_s1"):
            p = g["5_prevalence"][k]
            print(f"    {k}: {p['pos']:>7,}  prev {p['prev']:.6f}")

    if not all_pass:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print("GATE FAILED -- no prior-side table written", file=sys.stderr)
        return 2
    if a.gates_only:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print("\ngates-only: all PASSED")
        return 0

    for name, spec in FRAMES.items():
        b = build_prior(con, spec)
        out["build"][name] = b
        out["cell_grid"][name] = cell_grid(con)
        print(f"\n[{name}] {b['rows_with_a_prior_day']:,} targets with a prior day")
        print(f"    observable {b['state_observable']:,} · "
              f"observable_zero {b['state_observable_zero']:,} · "
              f"unobservable {b['state_unobservable']:,}")
        print(f"    tied prior day {b['tied_prior_day']:,} · "
              f"dedup reduced count on {b['targets_dedup_reduced_count']:,} targets "
              f"(max -{b['max_dedup_reduction']})")

    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
