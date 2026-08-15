#!/usr/bin/env python3
"""Prior major/dangerous BURDEN block. Per PREREG_B3_BURDEN_2026_08_15.md.

Strictly as-of: priors are the vehicle's own INITIAL (NT) tests with test_date STRICTLY
less than the target date, and only those priors' OWN items contribute. The house has been
burned here before -- a tyre-programme arm read +0.0121 and was truly +0.00063 (95% leak)
because a prior episode was selected as-of but its full retest summary was used.

Era-safe: every column uses the {F,P} fail-bearing basis, which is defined in BOTH eras.
Nothing here uses the dangerous/major split, which is post-2018 only.

NULL, not zero, when a vehicle has no priors: a zero would assert "no prior burden", which
is not what an absent history means.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent.parent))

from factory import severity as sev  # noqa: E402

LAKE = "/Users/henrirapson/autosafe/autosafe_lake"
LOOKUP = "/Users/henrirapson/autosafe_raw/lookup"
#: results 2005-2014 are PARKED, so priors are left-censored at 2015. The substrate already
#: carries b1_left_censor_flag for the same reason; this block inherits that censoring.
PRIOR_YEAR_FLOOR = 2015
PRIOR_YEARS = list(range(PRIOR_YEAR_FLOOR, 2026))

BURDEN_COLUMNS = [
    "bd_n_prior_initials", "bd_md_last", "bd_md_mean", "bd_md_max", "bd_md_sum",
    "bd_md_last3_mean", "bd_md_trend", "bd_sec_last", "bd_sec_mean", "bd_sec_max",
    "bd_sec_persistence", "bd_sec_repeat_last2",
]


def rel(dataset, years):
    p = ", ".join(f"'{LAKE}/{dataset}/test_year={y}/*.parquet'" for y in years)
    return f"read_parquet([{p}])"


def build(con, frame_glob: str) -> dict:
    era = sev.era_expr("p.test_date")  # pitem aliases the prior table as p
    fp = f"{sev.disposition_expr('i.rfr_type_code', era)} IN ('F','P')"

    con.execute(f"""CREATE TEMP TABLE tgt AS
        SELECT tgt_id, vehicle_id, tgt_date FROM read_parquet('{frame_glob}')""")

    # prior INITIAL tests only, and only for vehicles we actually need
    con.execute(f"""CREATE TEMP TABLE prior AS
        SELECT r.test_id, r.vehicle_id, r.test_date
        FROM {rel('results', PRIOR_YEARS)} r
        SEMI JOIN (SELECT DISTINCT vehicle_id FROM tgt) v USING (vehicle_id)
        WHERE r.test_type = 'NT' AND r.outcome IN ('PASS','FAIL','PRS')""")

    # catalogue: rfr_id -> normalised section name (case-collapsed so the legacy 5xxx and
    # modern 2xxxx trees resolve consistently)
    con.execute(f"""CREATE TEMP TABLE sect AS
        SELECT DISTINCT TRY_CAST(trim(d.rfr_id) AS BIGINT) AS rfr_id,
               lower(regexp_replace(trim(g.item_name), '\\s+', ' ', 'g')) AS section
        FROM read_csv('{LOOKUP}/item_detail.csv', delim='|', header=true, all_varchar=true) d
        JOIN read_csv('{LOOKUP}/item_group.csv', delim='|', header=true, all_varchar=true) g
          ON trim(g.test_item_id) = trim(d.test_item_set_section_id)
         AND trim(g.test_class_id) = trim(d.test_class_id)""")

    # Items are expanded ONCE PER PRIOR TEST here. Expanding them per (target, prior) pair
    # instead multiplies a shared prior by its target count and OOMs the temp dir.
    con.execute(f"""CREATE TEMP TABLE pitem AS
        SELECT p.test_id, p.vehicle_id, p.test_date, s.section
        FROM prior p
        JOIN {rel('items', PRIOR_YEARS)} i ON i.test_id = p.test_id
        LEFT JOIN sect s ON s.rfr_id = TRY_CAST(trim(i.rfr_id) AS BIGINT)
        WHERE {fp}""")

    con.execute("""CREATE TEMP TABLE pburden AS
        SELECT p.test_id, p.vehicle_id, p.test_date,
               coalesce(x.md, 0) AS md, coalesce(x.nsec, 0) AS nsec
        FROM prior p
        LEFT JOIN (SELECT test_id, count(*) AS md,
                          count(DISTINCT section) AS nsec
                   FROM pitem GROUP BY test_id) x ON x.test_id = p.test_id""")

    #: one row per (prior test, failing section) -- the collapse that makes tsec cheap
    con.execute("""CREATE TEMP TABLE psec AS
        SELECT DISTINCT test_id, section FROM pitem WHERE section IS NOT NULL""")

    # per (target, prior) pairs -- STRICT date inequality, and never the target itself
    con.execute("""CREATE TEMP TABLE tp AS
        SELECT t.tgt_id, b.test_id AS prior_id, b.test_date, b.md, b.nsec,
               row_number() OVER (PARTITION BY t.tgt_id ORDER BY b.test_date DESC,
                                  b.test_id DESC) AS rn
        FROM tgt t JOIN pburden b
          ON b.vehicle_id = t.vehicle_id
         AND b.test_date < t.tgt_date
         AND b.test_id  <> t.tgt_id""")

    # section-level persistence
    con.execute("""CREATE TEMP TABLE tsec AS
        SELECT tp.tgt_id, ps.section, count(DISTINCT tp.prior_id) AS n_hits
        FROM tp JOIN psec ps ON ps.test_id = tp.prior_id
        GROUP BY 1,2""")

    con.execute("""CREATE TEMP TABLE agg AS
        SELECT tgt_id,
          count(*)                                       AS n_prior,
          max(md) FILTER (WHERE rn=1)                    AS md_last,
          avg(md)                                        AS md_mean,
          max(md)                                        AS md_max,
          sum(md)                                        AS md_sum,
          avg(md) FILTER (WHERE rn<=3)                   AS md_last3_mean,
          avg(md) FILTER (WHERE rn>3)                    AS md_earlier_mean,
          max(nsec) FILTER (WHERE rn=1)                  AS sec_last,
          avg(nsec)                                      AS sec_mean,
          max(nsec)                                      AS sec_max
        FROM tp GROUP BY tgt_id""")

    con.execute("""CREATE TEMP TABLE persist AS
        SELECT tgt_id, max(n_hits) AS max_sec_hits FROM tsec GROUP BY tgt_id""")

    # do the two most recent priors share a failing section?
    con.execute("""CREATE TEMP TABLE repeat2 AS
        WITH ls AS (SELECT tp.tgt_id, tp.rn, ps.section
                    FROM tp JOIN psec ps ON ps.test_id = tp.prior_id
                    WHERE tp.rn <= 2)
        SELECT tgt_id, 1 AS repeat_last2 FROM (
          SELECT a.tgt_id FROM (SELECT DISTINCT tgt_id, section FROM ls WHERE rn=1) a
          JOIN (SELECT DISTINCT tgt_id, section FROM ls WHERE rn=2) b USING (tgt_id, section)
        ) GROUP BY tgt_id""")

    con.execute("""CREATE TEMP TABLE burden AS
        SELECT t.tgt_id,
          coalesce(a.n_prior, 0)                         AS bd_n_prior_initials,
          a.md_last                                      AS bd_md_last,
          a.md_mean                                      AS bd_md_mean,
          a.md_max                                       AS bd_md_max,
          a.md_sum                                       AS bd_md_sum,
          a.md_last3_mean                                AS bd_md_last3_mean,
          CASE WHEN a.md_earlier_mean IS NULL THEN NULL
               ELSE a.md_last3_mean - a.md_earlier_mean END AS bd_md_trend,
          a.sec_last                                     AS bd_sec_last,
          a.sec_mean                                     AS bd_sec_mean,
          a.sec_max                                      AS bd_sec_max,
          CASE WHEN a.n_prior IS NULL OR a.n_prior = 0 THEN NULL
               ELSE p.max_sec_hits::DOUBLE / a.n_prior END AS bd_sec_persistence,
          CASE WHEN a.n_prior IS NULL OR a.n_prior < 2 THEN NULL
               ELSE coalesce(r2.repeat_last2, 0) END      AS bd_sec_repeat_last2
        FROM tgt t
        LEFT JOIN agg a     ON a.tgt_id = t.tgt_id
        LEFT JOIN persist p ON p.tgt_id = t.tgt_id
        LEFT JOIN repeat2 r2 ON r2.tgt_id = t.tgt_id""")

    g = {}
    g["n_targets"] = con.execute("SELECT count(*) FROM tgt").fetchone()[0]
    g["n_with_priors"] = con.execute(
        "SELECT count(*) FROM burden WHERE bd_n_prior_initials > 0").fetchone()[0]
    g["n_prior_pairs"] = con.execute("SELECT count(*) FROM tp").fetchone()[0]
    # LEAKAGE GATES -- any violation is fatal
    g["G_strict_date_violations"] = con.execute("""
        SELECT count(*) FROM tp JOIN tgt USING (tgt_id) WHERE test_date >= tgt_date"""
    ).fetchone()[0]
    g["G_self_reference_violations"] = con.execute(
        "SELECT count(*) FROM tp WHERE prior_id = tgt_id").fetchone()[0]
    g["prior_year_floor"] = PRIOR_YEAR_FLOOR
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--diag", required=True)
    ap.add_argument("--memory-limit", default="3GB")
    ap.add_argument("--threads", type=int, default=3)
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix=f"burden_{os.getpid()}_")
    try:
        con = duckdb.connect()
        con.execute(f"SET memory_limit='{a.memory_limit}'")
        con.execute(f"SET threads={a.threads}")
        con.execute(f"SET temp_directory='{tmp}'")
        con.execute("PRAGMA max_temp_directory_size='6GiB'")
        con.execute("SET preserve_insertion_order=false")
        g = build(con, str(ROOT / a.frame) if not a.frame.startswith("/") else a.frame)
        if g["G_strict_date_violations"] or g["G_self_reference_violations"]:
            print("LEAKAGE GATE FAILED — nothing written:", json.dumps(g, indent=1))
            return 2
        dst = ROOT / a.out
        dst.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY (SELECT * FROM burden) TO '{dst}' "
                    f"(FORMAT PARQUET, COMPRESSION zstd)")
        g["columns"] = BURDEN_COLUMNS
        g["out"] = a.out
        (ROOT / a.diag).write_text(json.dumps(g, indent=1, default=str))
        print(json.dumps(g, indent=1, default=str))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
