#!/usr/bin/env python3
"""PERSIST correctness gate — the five checks of PREREG_PERSIST §7 / ADVSTRUCT §7.1.

NO OUTCOME NUMBER MAY BE COMPUTED UNTIL ALL FIVE PASS. Exit 2 on any failure.

What this does and does not do
------------------------------
It verifies that the label substrate PERSIST will join to is the same object the banked
studies used: identical target sets, identical row-level labels, identical ordering hash,
zero anti-joins, and exact agreement on already-published prevalences.

It computes population-wide label statistics only. It computes NOTHING conditional on
persistence state — that is Phase 2, and it is licensed by this gate passing.

Prevalence check note
---------------------
SEVERITY_RESULT.json publishes per-sect_NN positive_n on eval2024 only. On train_flat4y
the corresponding banked figure is the label parquet itself, so check 5 degrades to an
internal consistency check there and says so rather than silently passing.
"""
import hashlib
import json
import sys
from pathlib import Path

import duckdb

PROG = Path(__file__).resolve().parents[2]
LABELS = {"eval2024": "out/TARGET_SEVERITY_LABELS.parquet",
          "train_flat4y": "out/TRAIN_SEVERITY_LABELS.parquet"}
PACKETS = {"eval2024": "out/frames_eval_v2/recipe=eval2024/rung=all/packets/*.parquet",
           "train_flat4y": "out/frames/recipe=flat4y/rung=r1m/packets/*.parquet"}
STATE = "out/persist/state_{}.parquet"

results, failed = {}, []


def rec(frame, name, ok, detail):
    results.setdefault(frame, {})[name] = {"pass": bool(ok), "detail": detail}
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        failed.append(f"{frame}/{name}")


def gate(con, frame):
    print(f"\n=== {frame} ===")
    lab = str(PROG / LABELS[frame])
    pk = str(PROG / PACKETS[frame])
    st = str(PROG / STATE.format(frame))
    if not Path(st).exists():
        rec(frame, "0_state_exists", False, f"{st} missing — run persist_build_state.py first")
        return

    # ---- 1. exact tgt_id set equality: labels vs packet-derived targets ----------------
    n_lab = con.execute(f"SELECT count(DISTINCT test_id) FROM read_parquet('{lab}')").fetchone()[0]
    n_pk = con.execute(f"SELECT count(DISTINCT tgt_id) FROM read_parquet('{pk}')").fetchone()[0]
    n_both = con.execute(f"""
        SELECT count(*) FROM (SELECT DISTINCT test_id AS i FROM read_parquet('{lab}')) a
        JOIN (SELECT DISTINCT tgt_id AS i FROM read_parquet('{pk}')) b USING (i)""").fetchone()[0]
    rec(frame, "1_tgt_id_set_equality", n_lab == n_pk == n_both,
        f"labels {n_lab:,} | packets {n_pk:,} | intersection {n_both:,}")

    # ---- 2. row-level equality of the same-system outcome vs banked columns ------------
    # Recompute n_sections_with_md from the 14 sect_NN columns and compare ROW BY ROW
    idx = json.loads((PROG / "out/PERSIST_SECT_INDEX.json").read_text())
    cols = [r["column"] for r in idx["eval2024"]]
    expr = " + ".join(f"CASE WHEN {c} > 0 THEN 1 ELSE 0 END" for c in cols)
    n_mismatch = con.execute(f"""
        SELECT count(*) FROM read_parquet('{lab}')
        WHERE ({expr}) <> n_sections_with_md""").fetchone()[0]
    rec(frame, "2_row_level_label_equality", n_mismatch == 0,
        f"{n_mismatch:,} rows where sum(sect_NN>0) != banked n_sections_with_md "
        f"(of {n_lab:,})")

    # 2b. The per-section counts must RECONCILE to the M/D total, allowing exactly one
    # documented leak: catalogue misses. `n_major_or_dangerous` counts every F/P item;
    # `sect_NN` counts only those whose rfr_id resolves to a class-scoped section name
    # (severity_collect.py:169-172 filters `section_name IS NOT NULL`). So the invariant
    # is NOT equality — it is: the gap is non-negative, and every gap row is a catalogue
    # miss. A negative gap would mean a section carried items the total does not know
    # about, which would be a genuine defect.
    tot_expr = " + ".join(cols)
    neg, gap_rows, gap_explained, items_tot, items_sect = con.execute(f"""
        SELECT
          sum(CASE WHEN ({tot_expr}) > n_major_or_dangerous THEN 1 ELSE 0 END),
          sum(CASE WHEN ({tot_expr}) < n_major_or_dangerous THEN 1 ELSE 0 END),
          sum(CASE WHEN ({tot_expr}) < n_major_or_dangerous AND n_catalogue_miss > 0
                   THEN 1 ELSE 0 END),
          sum(n_major_or_dangerous), sum({tot_expr})
        FROM read_parquet('{lab}')""").fetchone()
    unsectioned = items_tot - items_sect
    ok = (neg == 0) and (gap_rows == gap_explained)
    rec(frame, "2b_section_sum_reconciles_to_md_total", ok,
        f"negative gaps {neg:,} (must be 0) | gap rows {gap_rows:,}, all catalogue misses "
        f"{gap_explained:,} | M/D items invisible to the same-system outcome "
        f"{unsectioned:,} of {items_tot:,} ({unsectioned/max(items_tot,1):.4%})")
    results[frame]["2b_section_sum_reconciles_to_md_total"]["same_system_outcome_coverage"] = {
        "md_items_total": int(items_tot), "md_items_sectioned": int(items_sect),
        "md_items_unsectioned": int(unsectioned),
        "unsectioned_share": round(unsectioned / max(items_tot, 1), 8),
        "meaning": ("these M/D items carry no catalogue section, so they are invisible to the "
                    "same-system outcome and appear only in any-failure outcomes")}

    # ---- 3. ordered hash equality -------------------------------------------------------
    rows = con.execute(f"""
        SELECT test_id, n_major_or_dangerous, n_sections_with_md, {tot_expr} AS sect_sum
        FROM read_parquet('{lab}') ORDER BY test_id""").fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(("|".join(str(x) for x in r) + "\n").encode())
    digest = h.hexdigest()
    hp = PROG / f"out/persist/LABEL_HASH_{frame}.txt"
    if hp.exists():
        prev = hp.read_text().strip()
        rec(frame, "3_ordered_hash_equality", prev == digest,
            f"{'stable' if prev == digest else 'CHANGED'} vs banked {prev[:16]}… "
            f"(now {digest[:16]}…)")
    else:
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(digest + "\n")
        rec(frame, "3_ordered_hash_equality", True,
            f"first run — pinned {digest[:16]}… to {hp.name} ({len(rows):,} rows)")

    # ---- 4. anti-join count = 0, both directions ---------------------------------------
    a1 = con.execute(f"""
        SELECT count(*) FROM (SELECT DISTINCT test_id AS i FROM read_parquet('{lab}')) a
        ANTI JOIN (SELECT DISTINCT tgt_id AS i FROM read_parquet('{pk}')) b USING (i)""").fetchone()[0]
    a2 = con.execute(f"""
        SELECT count(*) FROM (SELECT DISTINCT tgt_id AS i FROM read_parquet('{pk}')) a
        ANTI JOIN (SELECT DISTINCT test_id AS i FROM read_parquet('{lab}')) b USING (i)""").fetchone()[0]
    rec(frame, "4_antijoin_both_directions", a1 == 0 and a2 == 0,
        f"labels-not-in-packets {a1:,} | packets-not-in-labels {a2:,}")

    # ---- 5. prevalence exact vs the banked publication ---------------------------------
    if frame == "eval2024":
        banked = json.loads((PROG / "out/SEVERITY_RESULT.json").read_text())
        comp = banked["seeds"]["101"]["result"]["components"]
        bad = []
        for c in cols:
            got = con.execute(
                f"SELECT count(*) FROM read_parquet('{lab}') WHERE {c} > 0").fetchone()[0]
            want = comp[c]["positive_n"]
            if got != want:
                bad.append((c, want, got))
        rec(frame, "5_prevalence_exact_vs_banked", not bad,
            f"14/14 sect_NN positive_n match SEVERITY_RESULT seed101"
            if not bad else f"MISMATCH {bad[:3]}")
    else:
        n_pos = con.execute(
            f"SELECT count(*) FROM read_parquet('{lab}') WHERE n_major_or_dangerous >= 3"
        ).fetchone()[0]
        rec(frame, "5_prevalence_internal", True,
            f"no per-section publication exists for this frame; Y_B3 positives "
            f"{n_pos:,} / {n_lab:,} = {n_pos/n_lab:.6f} (internal consistency only)")

    # ---- state-build coverage against the label set (informational) --------------------
    n_state_tgt = con.execute(
        f"SELECT count(DISTINCT tgt_id) FROM read_parquet('{st}')").fetchone()[0]
    rec(frame, "6_state_coverage", True,
        f"{n_state_tgt:,} of {n_lab:,} targets have >=1 reconstructed episode "
        f"({n_state_tgt/n_lab:.2%}); the remainder have no prior NT and are a declared "
        f"category, never folded into CLEAN")


def main():
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'"); con.execute("SET threads=4")
    con.execute("SET preserve_insertion_order=false")
    frames = sys.argv[1:] or ["eval2024", "train_flat4y"]
    for f in frames:
        gate(con, f)

    out = {"artifact": "PERSIST correctness gate (PREREG_PERSIST §7 / ADVSTRUCT §7.1)",
           "prereg_sha256_16": "424dfdd4af84ea56",
           "all_pass": not failed, "failed_checks": failed, "frames": results,
           "licenses": ("Phase 2 outcome computation, IF AND ONLY IF all_pass is true"
                        if not failed else "NOTHING — outcome computation remains barred")}
    (PROG / "out/PERSIST_CORRECTNESS_GATE.json").write_text(json.dumps(out, indent=1))
    print("\n" + "=" * 64)
    if failed:
        print(f"GATE FAILED — {len(failed)} check(s). NO OUTCOME NUMBER MAY BE COMPUTED.")
        for f in failed:
            print("  -", f)
        sys.exit(2)
    print("GATE PASSED — Phase 2 outcome computation is licensed.")


if __name__ == "__main__":
    main()
