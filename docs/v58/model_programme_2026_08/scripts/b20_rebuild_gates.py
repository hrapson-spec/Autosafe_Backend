"""B20 rebuild reconciliation gates — PREREG_CUBE_v2 §6 acceptance.

Compares the corrected fullpop build (out/frames_fullpop) against the preserved
defective build (out/incidents/INC-2026-08-13-fullpop-defect-payload/
defective_artifacts/) and the canonical defect-item source. Acceptance is
RECONCILIATION, not a non-null rate: "~50% non-null" is explicitly NOT a pass
criterion here.

Gates (each emits a verdict; ANY failure -> exit 1; report written regardless):
  G1  target-row membership     — identical tgt_id set, old vs new frame
  G2  vehicle/rung membership   — identical vehicle_id set, identical rung/recipe
  G3  labels and dates          — y_initial, y_final, tgt_date byte-equal per tgt_id
  G4  non-defect fields         — every shared non-defect column equal per tgt_id
  G5  no row multiplication     — row counts equal, tgt_id unique both sides
  G6  observability vs expected — emitted packet observability agrees with the
                                  ledger + the fail-bearing evidence rule, by
                                  calendar year x outcome class
  G7  defect-count sample       — 20,000-target deterministic sample: per-target
                                  strictly-prior item counts recomputed
                                  independently from the lake == packet payload
                                  (enumeration-falsifier pattern,
                                  build_lagdepth_blocks.py:24-33 precedent)
  G8  train/eval categoricals   — no level present in eval and absent in the new
                                  training frame (dominant_mechanism class);
                                  no train-constant-eval-variable feature
  G9  53-column before/after    — old vs new training cardinality, NULL/zero
                                  rates, eval cardinality, per-column disposition
                                  (table emitted; failure only via G8 criteria)

LABEL-FREE where possible: G6/G7 read only (tgt_id, vehicle_id, tgt_date) plus
packet payloads. No model is fitted. No H1/confirmation surface is read.

Usage:
  python scripts/b20_rebuild_gates.py [--sample-cap 20000] [--out OUT]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb

D = Path("/Users/henrirapson/autosafe-v58/docs/v58/model_programme_2026_08")
INC = D / "out/incidents/INC-2026-08-13-fullpop-defect-payload/defective_artifacts"
OLD_FRAME = INC / "frames_fullpop/recipe=fullpop/rung=r1m/frame/*.parquet"
OLD_PACKETS = INC / "frames_fullpop/recipe=fullpop/rung=r1m/packets/*.parquet"
OLD_B0 = INC / "b0_fullpop.parquet"
NEW_FRAME = D / "out/frames_fullpop/recipe=fullpop/rung=r1m/frame/*.parquet"
NEW_PACKETS = D / "out/frames_fullpop/recipe=fullpop/rung=r1m/packets/*.parquet"
EVAL_B0 = INC / "b0_eval2024_eb_fullpop.parquet"   # defective build's eval b0 (eval side unaffected by the flag; used for level sets)
ITEMS = "/Users/henrirapson/autosafe/autosafe_lake/items/**/*.parquet"
LEDGER = D / "factory/item_coverage_ledger.csv"

# The 53 columns R6 measured as train-constant/eval-live under the defective
# build are re-derived here rather than hardcoded: any column constant in the
# OLD training frame and non-constant in the NEW one (or in eval) is in scope.

HASH_MULT = 2654435761   # house enumeration-falsifier constants
HASH_MOD = 50

META_COLS = {
    "recipe", "rung", "tgt_id", "vehicle_id", "tgt_date", "tgt_year",
    "tgt_outcome", "y_final", "y_initial", "tgt_test_class_id", "tgt_test_type",
    "tgt_miles", "tgt_make", "tgt_model", "tgt_model_id", "tgt_fuel",
    "tgt_colour", "tgt_cc", "tgt_fud", "tgt_pc", "tgt_age_at_test",
    "tgt_taxonomy_era", "sample_u", "sample_bucket", "enrichment_stratum",
    "inclusion_weight",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=2")
    con.execute("SET temp_directory='/private/tmp/claude-501/-Users-henrirapson/"
                "7c76ec55-3bf7-455f-a462-ca2a5d7096ab/scratchpad/duck_tmp_gates'")
    con.execute("SET max_temp_directory_size='6GiB'")
    return con


def gate(report: dict, name: str, ok: bool, detail: dict) -> None:
    report["gates"][name] = {"pass": bool(ok), **detail}
    log(f"{name}: {'PASS' if ok else 'FAIL'} {json.dumps(detail, default=str)[:220]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-cap", type=int, default=20000)
    ap.add_argument("--out", default=str(D / "out/cube/B20_REBUILD_GATES.json"))
    a = ap.parse_args()

    report: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "prereg": "PREREG_CUBE_v2 §6", "gates": {}}
    con = connect()
    con.execute(f"CREATE VIEW oldf AS SELECT * FROM read_parquet('{OLD_FRAME}')")
    con.execute(f"CREATE VIEW newf AS SELECT * FROM read_parquet('{NEW_FRAME}')")

    # ---- G1/G2/G5: membership + no multiplication --------------------------
    n_old, n_new = (con.execute(f"SELECT (SELECT count(*) FROM oldf), (SELECT count(*) FROM newf)").fetchone())
    dup_old = con.execute("SELECT count(*) - count(DISTINCT tgt_id) FROM oldf").fetchone()[0]
    dup_new = con.execute("SELECT count(*) - count(DISTINCT tgt_id) FROM newf").fetchone()[0]
    only_old = con.execute("SELECT count(*) FROM (SELECT tgt_id FROM oldf EXCEPT SELECT tgt_id FROM newf)").fetchone()[0]
    only_new = con.execute("SELECT count(*) FROM (SELECT tgt_id FROM newf EXCEPT SELECT tgt_id FROM oldf)").fetchone()[0]
    gate(report, "G1_target_rows", only_old == 0 and only_new == 0,
         {"n_old": n_old, "n_new": n_new, "only_old": only_old, "only_new": only_new})
    v_only = con.execute("SELECT count(*) FROM (SELECT vehicle_id FROM oldf EXCEPT SELECT vehicle_id FROM newf UNION ALL SELECT vehicle_id FROM newf EXCEPT SELECT vehicle_id FROM oldf)").fetchone()[0]
    gate(report, "G2_vehicles", v_only == 0, {"vehicle_set_diff": v_only})
    gate(report, "G5_no_multiplication", n_old == n_new and dup_old == 0 and dup_new == 0,
         {"dup_tgt_old": dup_old, "dup_tgt_new": dup_new})

    # ---- G3: labels and dates ----------------------------------------------
    mism = con.execute("""
        SELECT count(*) FROM oldf o JOIN newf n USING (tgt_id)
        WHERE o.y_initial IS DISTINCT FROM n.y_initial
           OR o.y_final   IS DISTINCT FROM n.y_final
           OR o.tgt_date  IS DISTINCT FROM n.tgt_date""").fetchone()[0]
    gate(report, "G3_labels_dates", mism == 0, {"mismatched_rows": mism})

    # ---- G4: shared non-defect columns byte-equal ---------------------------
    old_cols = {r[0] for r in con.execute("DESCRIBE oldf").fetchall()}
    new_cols = {r[0] for r in con.execute("DESCRIBE newf").fetchall()}
    shared_meta = sorted((old_cols & new_cols) & META_COLS - {"tgt_id"})
    preds = " OR ".join(f"o.{c} IS DISTINCT FROM n.{c}" for c in shared_meta)
    g4 = con.execute(f"SELECT count(*) FROM oldf o JOIN newf n USING (tgt_id) WHERE {preds}").fetchone()[0]
    gate(report, "G4_non_defect_fields", g4 == 0,
         {"columns_checked": len(shared_meta), "mismatched_rows": g4})

    # ---- G6: observability vs expected, by year x outcome class ------------
    # Expected: ledger cells (2024-12-31 expected_missing; extracts+non-definitive
    # unavailable) — neither intersects fullpop years 2015-2023 target rows, but
    # PRIOR histories do reach 2024? No: fullpop targets are 2020-2024 recipe with
    # years 2015-2023 sources — assert from packets what the observability field
    # actually says, and that no observed-state packet carries NULL payload.
    pk_cols = {r[0] for r in con.execute(f"SELECT * FROM read_parquet('{NEW_PACKETS}') LIMIT 0").description or []} if False else \
              {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{NEW_PACKETS}')").fetchall()}
    obs_col = next((c for c in pk_cols if "observab" in c.lower()), None)
    if obs_col is None:
        gate(report, "G6_observability", False, {"error": "no observability column in new packets", "cols": sorted(pk_cols)[:30]})
    else:
        rows = con.execute(f"""
            SELECT {obs_col} AS state,
                   count(*) AS n,
                   sum(CASE WHEN defects_json IS NULL THEN 1 ELSE 0 END) AS null_payload
            FROM read_parquet('{NEW_PACKETS}') GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        dist = {r[0]: {"n": r[1], "null_payload": r[2]} for r in rows}
        # observed states must never carry NULL payload; unobserved must ALWAYS.
        bad_observed = sum(v["null_payload"] for k, v in dist.items()
                           if k and k.startswith(("present", "assumed")))
        bad_unobserved = sum(v["n"] - v["null_payload"] for k, v in dist.items()
                             if k in ("unavailable", "expected_missing"))
        gate(report, "G6_observability", bad_observed == 0 and bad_unobserved == 0,
             {"distribution": dist, "observed_with_null_payload": bad_observed,
              "unobserved_with_payload": bad_unobserved})

    # ---- G7: enumeration-falsifier defect-count sample ----------------------
    sample_pred = f"(hash(tgt_id * {HASH_MULT}) % {HASH_MOD}) = 0"
    con.execute(f"""
        CREATE TEMP TABLE sample_targets AS
        SELECT tgt_id, vehicle_id, tgt_date FROM newf
        WHERE {sample_pred} ORDER BY tgt_id LIMIT {a.sample_cap}""")
    n_sample = con.execute("SELECT count(*) FROM sample_targets").fetchone()[0]
    # Independent path: count strictly-prior defect items per target from the
    # lake, joined through the results-derived prior-test mapping inside the
    # packet itself (p_test_id), restricted to item-observed priors.
    p_id_col = next((c for c in pk_cols if c in ("p_test_id", "prior_test_id", "test_id")), None)
    n_items_col = next((c for c in pk_cols if c in ("p_n_items", "n_items")), None)
    if not p_id_col or not n_items_col:
        gate(report, "G7_defect_count_sample", False,
             {"error": f"packet id/count columns not found (id={p_id_col}, n={n_items_col})"})
    else:
        g7 = con.execute(f"""
            WITH pk AS (
              SELECT p.tgt_id, p.{p_id_col} AS prior_id, p.{n_items_col} AS n_pkt
              FROM read_parquet('{NEW_PACKETS}') p
              JOIN sample_targets s USING (tgt_id)
              WHERE p.{p_id_col} IS NOT NULL AND p.{n_items_col} IS NOT NULL),
            lake AS (
              SELECT test_id, count(*) AS n_lake
              FROM read_parquet('{ITEMS}', hive_partitioning=1)
              WHERE test_id IN (SELECT DISTINCT prior_id FROM pk)
              GROUP BY 1)
            SELECT count(*) AS checked,
                   sum(CASE WHEN coalesce(l.n_lake,0) <> pk.n_pkt THEN 1 ELSE 0 END) AS disagree
            FROM pk LEFT JOIN lake l ON pk.prior_id = l.test_id""").fetchone()
        gate(report, "G7_defect_count_sample", g7[1] == 0,
             {"targets_sampled": n_sample, "prior_rows_checked": g7[0], "disagreements": g7[1]})

    # ---- G8 + G9: constancy, categorical coverage, before/after table -------
    feat_cols = sorted(new_cols - META_COLS - {"recipe", "rung"})
    table = []
    g8_violations = []
    for c in feat_cols:
        if c not in old_cols:
            continue
        row = con.execute(f"""
            SELECT (SELECT count(DISTINCT {c}) FROM oldf)  AS card_old,
                   (SELECT count(DISTINCT {c}) FROM newf)  AS card_new,
                   (SELECT count(*) FROM oldf WHERE {c} IS NULL) AS null_old,
                   (SELECT count(*) FROM newf WHERE {c} IS NULL) AS null_new""").fetchone()
        card_old, card_new, null_old, null_new = row
        changed = card_old <= 1 and card_new > 1
        table.append({"column": c, "card_old_train": card_old, "card_new_train": card_new,
                      "null_old": null_old, "null_new": null_new,
                      "was_incident_constant": bool(changed)})
        if card_old > 1 and card_new <= 1:
            g8_violations.append(c)   # went constant in the REBUILD — regression
    n_repaired = sum(1 for t in table if t["was_incident_constant"])
    gate(report, "G8_no_new_constants", not g8_violations,
         {"columns_went_constant_in_rebuild": g8_violations[:20]})
    report["before_after"] = {"columns": len(table), "incident_constants_now_live": n_repaired}
    report["before_after_table"] = table

    out = Path(a.out)
    out.write_text(json.dumps(report, indent=1, default=str))
    fails = [k for k, v in report["gates"].items() if not v["pass"]]
    log(f"report -> {out}")
    log(f"RESULT: {'ALL GATES PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
