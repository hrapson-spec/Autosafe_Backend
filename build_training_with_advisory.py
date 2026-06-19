"""
Build Training Dataset with Advisory Features

Joins dev_set.parquet with cycle_first_with_history_backfilled.parquet
to add improved advisory coverage for model training.
"""

import duckdb
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
HISTORY = PROJECT_ROOT / "cycle_first_with_history_backfilled.parquet"
OUTPUT_DEV = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"
OUTPUT_OOT = PROJECT_ROOT / "stratified_samples/oot_set_with_advisory.parquet"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def build_enhanced_dataset(input_path: Path, output_path: Path, conn, name: str):
    """Merge a dataset with advisory features from backfilled history."""
    log(f"\n[{name}] Building enhanced dataset...")

    # Get original column list (excluding old advisory columns we'll replace)
    cols = conn.execute(f"""
        SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('{input_path}'))
    """).fetchall()

    old_adv_cols = {
        'prev_adv_brakes', 'prev_adv_suspension', 'prev_adv_steering', 'prev_adv_tyres',
        'prev_adv_lights', 'prev_adv_body', 'prev_adv_emissions', 'prev_adv_wheels',
        'advisory_trend', 'advisory_trajectory_trend', 'advisory_delta'
    }
    keep_cols = [c[0] for c in cols if c[0] not in old_adv_cols]
    col_list = ', '.join([f'd.{c}' for c in keep_cols])

    # Build query with new advisory columns from history
    query = f"""
        SELECT
            {col_list},
            -- New advisory columns from backfilled history
            COALESCE(h.prev_advisory_brakes, 0) as prev_advisory_brakes,
            COALESCE(h.prev_advisory_suspension, 0) as prev_advisory_suspension,
            COALESCE(h.prev_advisory_steering, 0) as prev_advisory_steering,
            COALESCE(h.prev_advisory_tyres, 0) as prev_advisory_tyres,
            COALESCE(h.prev_advisory_total, 0) as prev_advisory_total,
            COALESCE(h.prev_advisory_known, 0) as prev_advisory_known,
            COALESCE(h.prev_has_safety_advisory, 0) as prev_has_safety_advisory,
            -- Advisory trend from history
            COALESCE(h.advisory_trend, 'UNKNOWN') as advisory_trend
        FROM read_parquet('{input_path}') d
        LEFT JOIN read_parquet('{HISTORY}') h ON d.test_id = h.test_id
    """

    # Execute and save
    conn.execute(f"""
        COPY ({query}) TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    # Verify
    n_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{output_path}')").fetchone()[0]
    log(f"  Rows: {n_rows:,}")

    # Check advisory coverage by year
    coverage = conn.execute(f"""
        SELECT YEAR(test_date) as yr,
               COUNT(*) as total,
               SUM(CASE WHEN prev_advisory_known = 1 THEN 1 ELSE 0 END) as known,
               ROUND(100.0 * SUM(CASE WHEN prev_advisory_known = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM read_parquet('{output_path}')
        GROUP BY 1 ORDER BY 1
    """).fetchall()

    log(f"\n  Advisory coverage by year:")
    log(f"  {'Year':<6} {'Total':<10} {'Known':<10} {'%':>6}")
    for yr, total, known, pct in coverage:
        log(f"  {yr:<6} {total:>10,} {known:>10,} {pct:>6}%")

    # Check advisory_trend distribution for 2021+
    trend_dist = conn.execute(f"""
        SELECT advisory_trend, COUNT(*) as cnt
        FROM read_parquet('{output_path}')
        WHERE YEAR(test_date) >= 2021
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()

    log(f"\n  Advisory trend distribution (2021+):")
    for trend, cnt in trend_dist:
        log(f"    {trend}: {cnt:,}")

    return n_rows


def main():
    start = datetime.now()
    log("=" * 60)
    log("BUILD TRAINING DATA WITH ADVISORY FEATURES")
    log("=" * 60)

    conn = duckdb.connect()
    conn.execute("SET memory_limit='4GB'")
    conn.execute("SET threads=2")

    # Build enhanced dev set
    build_enhanced_dataset(DEV_SET, OUTPUT_DEV, conn, "DEV")

    # Build enhanced OOT set
    build_enhanced_dataset(OOT_SET, OUTPUT_OOT, conn, "OOT")

    conn.close()

    elapsed = (datetime.now() - start).total_seconds()
    log(f"\n[Done] Elapsed: {elapsed:.1f}s")
    log(f"DEV: {OUTPUT_DEV}")
    log(f"OOT: {OUTPUT_OOT}")
    log("=" * 60)


if __name__ == "__main__":
    main()
