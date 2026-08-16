#!/usr/bin/env python3
"""Recover the sect_NN -> section_name mapping for the banked SEVERITY label parquets.

BLOCKER 1 of the PERSIST plan. severity_collect.write_labels() assigns sect_00..sect_NN
positionally from `SELECT DISTINCT section_name FROM sect_md ORDER BY 1` on that run's
data, and persists the names NOWHERE. This script replays exactly that query for both
frames, using severity_collect's OWN expressions (never a reimplementation), and proves
whether TRAIN and EVAL share an identical mapping.

Read-only. Emits out/PERSIST_SECT_INDEX.json.
"""
import json
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve()
PROG = Path("/Users/henrirapson/autosafe-v58/docs/v58/model_programme_2026_08")
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(PROG / "scripts" / "analysis"))
sys.path.insert(0, "/Users/henrirapson/autosafe-v58")

import severity_collect as SC  # noqa: E402
from factory import severity as sev  # noqa: E402

TMP = HERE.parent / "duck_tmp_sect"
TMP.mkdir(exist_ok=True)


def section_list(con, frame_glob, years, label):
    """Replay severity_collect.build()'s it/sect_md construction, section names only."""
    results_rel = SC._year_glob("results", years)
    items_rel = SC._year_glob("items", years)
    era = sev.era_expr("t.tgt_date")
    disp = sev.disposition_expr("i.rfr_type_code", era)

    con.execute("DROP TABLE IF EXISTS tgt; DROP TABLE IF EXISTS tgt_r; DROP TABLE IF EXISTS it")
    con.execute(f"""
        CREATE TEMP TABLE tgt AS
        SELECT tgt_id AS test_id, vehicle_id, tgt_date FROM read_parquet('{frame_glob}')
    """)
    con.execute(f"""
        CREATE TEMP TABLE tgt_r AS
        SELECT t.*, trim(r.test_class_id) AS test_class_id
        FROM tgt t JOIN {results_rel} r ON r.test_id = t.test_id
    """)
    con.execute(f"""
        CREATE TEMP TABLE it AS
        SELECT t.test_id, {disp} AS disp, s.section_name
        FROM tgt_r t
        JOIN {items_rel} i ON i.test_id = t.test_id
        LEFT JOIN cat c ON c.rfr_id = TRY_CAST(trim(i.rfr_id) AS BIGINT)
                       AND c.test_class_id = t.test_class_id
        LEFT JOIN sect s ON s.section_id = c.section_id
                        AND s.test_class_id = t.test_class_id
    """)
    # sect_md, verbatim from severity_collect.py:169-172 (fp = disp IN ('F','P'))
    rows = con.execute("""
        WITH sect_md AS (
          SELECT test_id, section_name, count(*) AS n_md
          FROM it WHERE disp IN ('F','P') AND section_name IS NOT NULL
          GROUP BY test_id, section_name
        )
        SELECT section_name, count(DISTINCT test_id) AS positive_n
        FROM sect_md GROUP BY 1 ORDER BY 1
    """).fetchall()
    n_tgt = con.execute("SELECT count(*) FROM tgt").fetchone()[0]
    print(f"[{label}] targets={n_tgt:,}  distinct sections in sect_md={len(rows)}", flush=True)
    return [{"index": i, "column": f"sect_{i:02d}_n_md",
             "section_name": r[0], "positive_n": r[1]} for i, r in enumerate(rows)]


def main():
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET threads=4")
    con.execute(f"SET temp_directory='{TMP}'")
    con.execute("PRAGMA max_temp_directory_size='8GiB'")
    con.execute("SET preserve_insertion_order=false")

    con.execute(f"""CREATE TABLE cat AS
        SELECT TRY_CAST(trim(rfr_id) AS BIGINT) AS rfr_id,
               trim(test_class_id) AS test_class_id,
               TRY_CAST(trim(test_item_set_section_id) AS INTEGER) AS section_id
        FROM read_csv('{SC.LOOKUP}/item_detail.csv', delim='|', header=true, all_varchar=true)""")
    con.execute(f"""CREATE TABLE sect AS
        SELECT DISTINCT TRY_CAST(trim(test_item_id) AS INTEGER) AS section_id,
               trim(test_class_id) AS test_class_id, trim(item_name) AS section_name
        FROM read_csv('{SC.LOOKUP}/item_group.csv', delim='|', header=true, all_varchar=true)""")

    ev = section_list(con, str(PROG / SC.EVAL_FRAME), (SC.TARGET_YEAR,), "eval2024")
    tr = section_list(con, str(PROG / SC.TRAIN_FRAME), SC.TRAIN_YEARS, "flat4y-train")

    ev_names = [r["section_name"] for r in ev]
    tr_names = [r["section_name"] for r in tr]
    identical = ev_names == tr_names

    # Independent verification against the banked artifact.
    banked = json.load(open(PROG / "out" / "SEVERITY_RESULT.json"))
    comp = banked["seeds"]["101"]["result"]["components"]
    mismatches = []
    for r in ev:
        b = comp.get(r["column"])
        if b is None:
            mismatches.append((r["column"], "absent from SEVERITY_RESULT.json", None))
        elif b["positive_n"] != r["positive_n"]:
            mismatches.append((r["column"], b["positive_n"], r["positive_n"]))

    out = {
        "artifact": "PERSIST_SECT_INDEX — recovered sect_NN -> section_name mapping",
        "why": ("severity_collect.write_labels() assigns the index positionally from "
                "SELECT DISTINCT section_name FROM sect_md ORDER BY 1 and persists no names. "
                "Replayed here with severity_collect's own expressions."),
        "source_script": "scripts/analysis/severity_collect.py:169-221",
        "eval2024": ev,
        "flat4y_train": tr,
        "train_eval_mapping_identical": identical,
        "verification_vs_SEVERITY_RESULT_seed101": {
            "checked": len(ev),
            "mismatches": mismatches,
            "all_match": not mismatches,
        },
    }
    dest = PROG / "out" / "PERSIST_SECT_INDEX.json"
    dest.write_text(json.dumps(out, indent=1))

    print(f"\n{'idx':>4} {'positive_n':>11}  section_name")
    for r in ev:
        print(f"{r['index']:>4} {r['positive_n']:>11,}  {r['section_name']}")
    print(f"\nTRAIN/EVAL mapping identical : {identical}")
    if not identical:
        print(f"  EVAL  ({len(ev_names)}): {ev_names}")
        print(f"  TRAIN ({len(tr_names)}): {tr_names}")
    print(f"positive_n matches banked    : {not mismatches}")
    if mismatches:
        for m in mismatches:
            print("   MISMATCH", m)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
