#!/usr/bin/env python3
"""Severity/burden outcome study -- DATA COLLECTION. Per PREREG_SEVERITY_2026_08_15.md.

Written and committed BEFORE any result exists. This script computes NO comparisons and
NO metrics: it emits one small per-target label table plus Gate-0 diagnostics. All
statistics live in severity_analyze.py.

Severity semantics are the rule of record (factory/severity.py:98-110), read ONLY from
the raw lake columns rfr_type_code and dangerous_mark. The derived lake columns
rfr_class / is_fail_item / is_advisory / is_dangerous are known-wrong (F-22) and gate
G0.9 asserts this file never references them.

Recorded deviation from severity_expr (PREREG §3): severity_expr tests dangerous_mark
BEFORE disposition, so a D-marked ADVISORY grades 'dangerous'. n_dangerous here is
FAIL-GATED, and n_major_or_dangerous is the F+P count, not n_major + n_dangerous.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent.parent))  # ~/autosafe-v58 for pipeline.*

from factory import severity as sev  # noqa: E402
from pipeline.lake.rfr_mapping import project_category  # noqa: E402  (SECTION half only)

LAKE = "/Users/henrirapson/autosafe/autosafe_lake"
LOOKUP = "/Users/henrirapson/autosafe_raw/lookup"
EVAL_FRAME = "out/frames_eval/recipe=eval2024/rung=all/frame/part_*.parquet"
TARGET_YEAR = 2024
#: Stage 2 generalisation: the same builder serves the flat4y training window.
TRAIN_FRAME = "out/frames/recipe=flat4y/rung=r1m/frame/part_*.parquet"
TRAIN_YEARS = (2020, 2021, 2022, 2023)

# Columns the lake derives and gets wrong (F-22). Never read. Asserted by G0.9.
FORBIDDEN_COLUMNS = ("rfr_class", "is_fail_item", "is_advisory", "is_dangerous")


class RecordingConnection:
    """Wraps duckdb so G0.9 can assert on the SQL that ACTUALLY RAN.

    Scanning this file's own source instead self-matches on the FORBIDDEN_COLUMNS
    declaration (found on first execution, 2026-08-15) -- the same self-match trap the
    house hit with pgrep watchdog patterns.
    """

    def __init__(self, con):
        self._con = con
        self.sql = []

    def execute(self, q, *a, **k):
        self.sql.append(q)
        return self._con.execute(q, *a, **k)

    def __getattr__(self, name):
        return getattr(self._con, name)


def connect(memory_limit: str, threads: int, max_temp: str, tmpdir: str):
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET threads={threads}")
    # per-PID temp dir: shared duckdb spill dirs corrupt each other (lake defect #17)
    con.execute(f"SET temp_directory='{tmpdir}'")
    con.execute(f"PRAGMA max_temp_directory_size='{max_temp}'")
    con.execute("SET preserve_insertion_order=false")
    return RecordingConnection(con)


def _year_glob(dataset: str, years) -> str:
    """duckdb list-of-paths so one query spans several lake partitions."""
    paths = ", ".join(f"'{LAKE}/{dataset}/test_year={int(y)}/*.parquet'" for y in years)
    return f"read_parquet([{paths}])"


def build(con, eval_frame: str, years) -> dict:
    results_rel = _year_glob("results", years)
    items_rel = _year_glob("items", years)
    era = sev.era_expr("t.tgt_date")
    disp = sev.disposition_expr("i.rfr_type_code", era)
    dmark = "trim(coalesce(i.dangerous_mark, '')) = 'D'"

    # --- targets ---------------------------------------------------------
    con.execute(f"""
        CREATE TEMP TABLE tgt AS
        SELECT tgt_id AS test_id, vehicle_id, tgt_date, tgt_outcome,
               CAST(y_final AS TINYINT) AS y_final,
               CAST(y_initial AS TINYINT) AS y_initial
        FROM read_parquet('{eval_frame}')
    """)

    # --- results side: test class + type (class-scoping the catalogue join) ---
    con.execute(f"""
        CREATE TEMP TABLE tgt_r AS
        SELECT t.*, trim(r.test_class_id) AS test_class_id, r.test_type,
               r.outcome AS lake_outcome
        FROM tgt t
        JOIN {results_rel} r
          ON r.test_id = t.test_id
    """)

    # --- DVSA catalogue, class-scoped ------------------------------------
    con.execute(f"""
        CREATE TEMP TABLE cat AS
        SELECT TRY_CAST(trim(rfr_id) AS BIGINT) AS rfr_id,
               trim(test_class_id)              AS test_class_id,
               TRY_CAST(trim(test_item_set_section_id) AS INTEGER) AS section_id
        FROM read_csv('{LOOKUP}/item_detail.csv', delim='|', header=true, all_varchar=true)
    """)
    con.execute(f"""
        CREATE TEMP TABLE sect AS
        SELECT DISTINCT
               TRY_CAST(trim(test_item_id) AS INTEGER) AS section_id,
               trim(test_class_id)                     AS test_class_id,
               trim(item_name)                         AS section_name
        FROM read_csv('{LOOKUP}/item_group.csv', delim='|', header=true, all_varchar=true)
    """)

    # --- target items, decoded -------------------------------------------
    con.execute(f"""
        CREATE TEMP TABLE it AS
        SELECT t.test_id, t.test_class_id,
               TRY_CAST(trim(i.rfr_id) AS BIGINT) AS rfr_id,
               {disp}   AS disp,
               {era}    AS era,
               {dmark}  AS is_d,
               s.section_name
        FROM tgt_r t
        JOIN {items_rel} i
          ON i.test_id = t.test_id
        LEFT JOIN cat c
          ON c.rfr_id = TRY_CAST(trim(i.rfr_id) AS BIGINT)
         AND c.test_class_id = t.test_class_id
        LEFT JOIN sect s
          ON s.section_id = c.section_id
         AND s.test_class_id = t.test_class_id
    """)

    fp = "disp IN ('F','P')"
    con.execute(f"""
        CREATE TEMP TABLE agg AS
        SELECT test_id,
          count(*)                                                    AS n_items,
          count(*) FILTER (WHERE disp IS NULL)                        AS n_unknown_disposition,
          count(*) FILTER (WHERE era <> 'post_2018')                  AS n_not_post2018,
          count(*) FILTER (WHERE section_name IS NULL)                AS n_catalogue_miss,
          count(*) FILTER (WHERE {fp})                                AS n_major_or_dangerous,
          count(*) FILTER (WHERE {fp} AND is_d)                       AS n_dangerous,
          count(*) FILTER (WHERE disp = 'M')                          AS n_minor,
          count(*) FILTER (WHERE disp = 'A')                          AS n_advisory,
          count(*) FILTER (WHERE disp = 'P')                          AS n_prs,
          count(*) FILTER (WHERE disp = 'A' AND is_d)                 AS n_dangerous_advisory,
          count(*) FILTER (WHERE disp = 'M' AND is_d)                 AS n_dangerous_minor,
          count(*) FILTER (WHERE {fp} AND rfr_id < 10000)             AS n_legacy_fail_items,
          count(DISTINCT section_name) FILTER (WHERE {fp})            AS n_sections_with_md
        FROM it GROUP BY test_id
    """)

    # --- per-section major/dangerous counts (long) ------------------------
    con.execute(f"""
        CREATE TEMP TABLE sect_md AS
        SELECT test_id, section_name, count(*) AS n_md
        FROM it WHERE {fp} AND section_name IS NOT NULL
        GROUP BY test_id, section_name
    """)

    # --- Gate 0 diagnostics (counts only; no comparisons) -----------------
    g = {}
    g["n_eval_rows"] = con.execute("SELECT count(*) FROM tgt").fetchone()[0]
    g["n_eval_rows_joined_results"] = con.execute("SELECT count(*) FROM tgt_r").fetchone()[0]
    g["n_eval_rows_with_items"] = con.execute("SELECT count(*) FROM agg").fetchone()[0]
    g["n_target_item_rows"] = con.execute("SELECT count(*) FROM it").fetchone()[0]
    g["G0.3_unknown_disposition"] = con.execute(
        "SELECT coalesce(sum(n_unknown_disposition),0) FROM agg").fetchone()[0]
    g["G0.4_items_not_post2018"] = con.execute(
        "SELECT coalesce(sum(n_not_post2018),0) FROM agg").fetchone()[0]
    g["G0.5_d_marked_non_failure_items"] = con.execute(
        "SELECT coalesce(sum(n_dangerous_advisory + n_dangerous_minor),0) FROM agg").fetchone()[0]
    g["G0.6_catalogue_miss_items"] = con.execute(
        "SELECT coalesce(sum(n_catalogue_miss),0) FROM agg").fetchone()[0]
    g["G0.7_legacy_rfr_fail_items"] = con.execute(
        "SELECT coalesce(sum(n_legacy_fail_items),0) FROM agg").fetchone()[0]
    g["target_class_mix"] = dict(con.execute(
        "SELECT test_class_id, count(*) FROM tgt_r GROUP BY 1 ORDER BY 2 DESC").fetchall())
    g["target_test_type_mix"] = dict(con.execute(
        "SELECT test_type, count(*) FROM tgt_r GROUP BY 1 ORDER BY 2 DESC").fetchall())
    g["target_outcome_mix"] = dict(con.execute(
        "SELECT tgt_outcome, count(*) FROM tgt_r GROUP BY 1 ORDER BY 2 DESC").fetchall())
    g["frame_vs_lake_outcome_disagreement"] = con.execute(
        "SELECT count(*) FROM tgt_r WHERE tgt_outcome <> lake_outcome").fetchone()[0]
    g["disposition_mix"] = dict(con.execute(
        "SELECT coalesce(disp,'(unknown)'), count(*) FROM it GROUP BY 1 ORDER BY 2 DESC").fetchall())
    g["sections_present"] = con.execute(
        "SELECT count(DISTINCT section_name) FROM sect_md").fetchone()[0]
    return g


def write_labels(con, out_path: Path) -> list:
    sections = [r[0] for r in con.execute(
        "SELECT DISTINCT section_name FROM sect_md ORDER BY 1").fetchall()]

    # wide per-section major/dangerous counts, zero-filled for absent sections
    cols = []
    for i, s in enumerate(sections):
        safe = s.replace("'", "''")
        cols.append(f"coalesce(max(CASE WHEN section_name = '{safe}' THEN n_md END), 0) "
                    f"AS sect_{i:02d}_n_md")
    pivot = f"SELECT test_id, {', '.join(cols)} FROM sect_md GROUP BY test_id" if cols else \
            "SELECT test_id FROM sect_md WHERE false"
    con.execute(f"CREATE TEMP TABLE sect_wide AS {pivot}")

    sect_cols = ", ".join(
        f"coalesce(w.sect_{i:02d}_n_md, 0) AS sect_{i:02d}_n_md" for i in range(len(sections)))
    sect_sel = (", " + sect_cols) if sections else ""

    con.execute(f"""
        CREATE TEMP TABLE labels AS
        SELECT
          t.test_id, t.vehicle_id, t.tgt_date, t.tgt_outcome, t.test_class_id, t.test_type,
          t.y_final, t.y_initial,
          coalesce(a.n_items, 0)                AS n_items,
          coalesce(a.n_major_or_dangerous, 0)   AS n_major_or_dangerous,
          coalesce(a.n_dangerous, 0)            AS n_dangerous,
          coalesce(a.n_major_or_dangerous, 0)
            - coalesce(a.n_dangerous, 0)        AS n_major,
          coalesce(a.n_minor, 0)                AS n_minor,
          coalesce(a.n_advisory, 0)             AS n_advisory,
          coalesce(a.n_prs, 0)                  AS n_prs,
          coalesce(a.n_dangerous_advisory, 0)   AS n_dangerous_advisory,
          coalesce(a.n_catalogue_miss, 0)       AS n_catalogue_miss,
          coalesce(a.n_legacy_fail_items, 0)    AS n_legacy_fail_items,
          coalesce(a.n_sections_with_md, 0)     AS n_sections_with_md
          {sect_sel}
        FROM tgt_r t
        LEFT JOIN agg a       ON a.test_id = t.test_id
        LEFT JOIN sect_wide w ON w.test_id = t.test_id
    """)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.parquet")
    con.execute(f"COPY labels TO '{tmp}' (FORMAT PARQUET, COMPRESSION zstd)")
    os.replace(tmp, out_path)
    return sections


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-frame", default=EVAL_FRAME)
    ap.add_argument("--years", default=str(TARGET_YEAR),
                    help="comma list of lake test_year partitions the targets fall in")
    ap.add_argument("--out", default="out/TARGET_SEVERITY_LABELS.parquet")
    ap.add_argument("--diag", default="out/SEVERITY_COLLECT_DIAG.json")
    ap.add_argument("--memory-limit", default="2500MB")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--max-temp", default="4GiB")
    a = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix=f"sevcollect_{os.getpid()}_")
    try:
        con = connect(a.memory_limit, a.threads, a.max_temp, tmpdir)
        years = [int(y) for y in str(a.years).split(",") if str(y).strip()]
        g = build(con, str(ROOT / a.eval_frame) if not a.eval_frame.startswith("/")
                  else a.eval_frame, years)
        g["target_years"] = years
        sections = write_labels(con, ROOT / a.out)

        # G0.9: no executed statement may reference a known-wrong derived column.
        executed = "\n".join(con.sql)
        hits = sorted({c for c in FORBIDDEN_COLUMNS
                       if re.search(rf"\b{re.escape(c)}\b", executed)})
        if hits:
            print(f"G0.9 FAIL: executed SQL references known-wrong columns {hits}")
            return 2
        g["G0.9_forbidden_column_refs"] = 0
        g["G0.9_statements_scanned"] = len(con.sql)
        g["sections"] = sections
        g["section_to_category_secondary"] = {s: project_category(s) for s in sections}
        g["out"] = a.out
        (ROOT / a.diag).write_text(json.dumps(g, indent=1, default=str))
        print(json.dumps(g, indent=1, default=str))
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
