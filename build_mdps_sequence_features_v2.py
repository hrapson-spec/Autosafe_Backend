"""
Build MDPS Sequence Features V2 (DuckDB-based)
===============================================

Builds sequence features using DuckDB window functions (not Python loops).
Fixes the O(n²) performance issue from v1.

Features (15 total):
- Persistence (4): consecutive prior cycles with advisory (NOT capped)
- Recency (4): cycles since last advisory in each domain (NULL if never)
- Has-Ever Flags (4): binary flags for "ever had advisory in domain"
- Switching (3): domain changes and diversity in last 5 cycles

Input:
  - cycle_advisory_timeseries_dev.parquet
  - cycle_advisory_timeseries_oot.parquet

Output:
  - mdps_sequence_features_dev.parquet
  - mdps_sequence_features_oot.parquet

Join Strategy:
  Output is one row per test_id (UNIQUE).
  Join to training sets via:
    SELECT t.*, f.*
    FROM dev_set t
    LEFT JOIN mdps_sequence_features_dev f ON t.test_id = f.test_id
  Guarantees 1:1 join, no row multiplication.

Created: 2026-01-12
"""

import duckdb
import time
from pathlib import Path


def log(msg):
    print(msg, flush=True)


def build_sequence_features(conn, ts_path: Path, output_path: Path, name: str, timeout_minutes: int = 30):
    """Build sequence features for a single dataset using DuckDB."""
    log(f"\n{'='*60}")
    log(f"Building {name} sequence features")
    log(f"{'='*60}")

    start_time = time.time()
    last_log = start_time

    def log_progress(stage: str):
        nonlocal last_log
        elapsed = time.time() - start_time
        log(f"  [{elapsed:6.1f}s] {stage}")
        last_log = time.time()

    # Load timeseries into temp table
    log_progress(f"Loading {ts_path.name}...")
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE ts AS
        SELECT * FROM read_parquet('{ts_path}')
    """)

    row_count = conn.execute("SELECT COUNT(*) FROM ts").fetchone()[0]
    vehicle_count = conn.execute("SELECT COUNT(DISTINCT vehicle_id) FROM ts").fetchone()[0]
    log_progress(f"Loaded {row_count:,} cycles for {vehicle_count:,} vehicles")

    # Step 1: Compute persistence features using streak detection
    log_progress("Computing persistence features (streak detection)...")

    conn.execute("""
        CREATE OR REPLACE TEMP TABLE with_streaks AS
        WITH
        -- Add binary flags and any_adv
        flagged AS (
            SELECT *,
                CASE WHEN adv_brakes > 0 THEN 1 ELSE 0 END as has_brakes,
                CASE WHEN adv_suspension_steering > 0 THEN 1 ELSE 0 END as has_susp,
                CASE WHEN adv_tyres > 0 THEN 1 ELSE 0 END as has_tyres,
                CASE WHEN adv_brakes > 0 OR adv_suspension_steering > 0 OR adv_tyres > 0 THEN 1 ELSE 0 END as has_any
            FROM ts
        ),
        -- Track last cycle where each domain was 0 (from prior cycles only)
        last_zeros AS (
            SELECT *,
                MAX(CASE WHEN has_brakes = 0 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) as last_brakes_zero,
                MAX(CASE WHEN has_susp = 0 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) as last_susp_zero,
                MAX(CASE WHEN has_tyres = 0 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) as last_tyres_zero,
                MAX(CASE WHEN has_any = 0 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) as last_any_zero
            FROM flagged
        ),
        -- Compute streak length: cycle_index - last_zero (only if current has advisory)
        streak_lengths AS (
            SELECT *,
                CASE WHEN has_brakes = 1 THEN
                    cycle_index - COALESCE(last_brakes_zero, 0)
                ELSE 0 END as brakes_streak,
                CASE WHEN has_susp = 1 THEN
                    cycle_index - COALESCE(last_susp_zero, 0)
                ELSE 0 END as susp_streak,
                CASE WHEN has_tyres = 1 THEN
                    cycle_index - COALESCE(last_tyres_zero, 0)
                ELSE 0 END as tyres_streak,
                CASE WHEN has_any = 1 THEN
                    cycle_index - COALESCE(last_any_zero, 0)
                ELSE 0 END as any_streak
            FROM last_zeros
        )
        -- Get prior cycle's streak length using LAG
        SELECT
            vehicle_id,
            cycle_index,
            test_id,
            adv_brakes,
            adv_suspension_steering,
            adv_tyres,
            has_brakes,
            has_susp,
            has_tyres,
            has_any,
            COALESCE(LAG(brakes_streak, 1) OVER (PARTITION BY vehicle_id ORDER BY cycle_index), 0)::INTEGER as persist_len_brakes_prev,
            COALESCE(LAG(susp_streak, 1) OVER (PARTITION BY vehicle_id ORDER BY cycle_index), 0)::INTEGER as persist_len_susp_steer_prev,
            COALESCE(LAG(tyres_streak, 1) OVER (PARTITION BY vehicle_id ORDER BY cycle_index), 0)::INTEGER as persist_len_tyres_prev,
            COALESCE(LAG(any_streak, 1) OVER (PARTITION BY vehicle_id ORDER BY cycle_index), 0)::INTEGER as persist_len_any_prev
        FROM streak_lengths
    """)

    log_progress("Computing recency features (cycles since last advisory)...")

    # Step 2: Compute recency features
    conn.execute("""
        CREATE OR REPLACE TEMP TABLE with_recency AS
        SELECT
            s.*,
            -- Cycles since last advisory: cycle_index - max(cycle_index where had advisory) over prior rows
            -- Use conditional max with window frame
            COALESCE(
                cycle_index - MAX(CASE WHEN has_brakes = 1 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
                -1  -- Use -1 to indicate "never had advisory" (will convert to NULL or large value)
            )::INTEGER as cycles_since_brakes_prev,
            COALESCE(
                cycle_index - MAX(CASE WHEN has_susp = 1 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
                -1
            )::INTEGER as cycles_since_susp_steer_prev,
            COALESCE(
                cycle_index - MAX(CASE WHEN has_tyres = 1 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
                -1
            )::INTEGER as cycles_since_tyres_prev,
            COALESCE(
                cycle_index - MAX(CASE WHEN has_any = 1 THEN cycle_index END)
                    OVER (PARTITION BY vehicle_id ORDER BY cycle_index
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
                -1
            )::INTEGER as cycles_since_any_adv_prev
        FROM with_streaks s
    """)

    log_progress("Computing switching features (domain changes last 5)...")

    # Step 3: Compute switching features
    conn.execute("""
        CREATE OR REPLACE TEMP TABLE with_switching AS
        WITH
        -- Compute dominant domain for each cycle
        with_dominant AS (
            SELECT *,
                CASE
                    WHEN adv_brakes >= adv_suspension_steering AND adv_brakes >= adv_tyres AND adv_brakes > 0 THEN 1
                    WHEN adv_suspension_steering >= adv_tyres AND adv_suspension_steering > 0 THEN 2
                    WHEN adv_tyres > 0 THEN 3
                    ELSE 0
                END as dominant_domain
            FROM with_recency
        ),
        -- Get lagged domains for last 5 cycles
        with_lags AS (
            SELECT *,
                LAG(dominant_domain, 1) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as dom_1,
                LAG(dominant_domain, 2) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as dom_2,
                LAG(dominant_domain, 3) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as dom_3,
                LAG(dominant_domain, 4) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as dom_4,
                LAG(dominant_domain, 5) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as dom_5
            FROM with_dominant
        )
        -- Count domain changes (only between non-zero domains)
        SELECT *,
            (
                CASE WHEN dom_1 IS NOT NULL AND dom_2 IS NOT NULL AND dom_1 != dom_2 AND dom_1 != 0 AND dom_2 != 0 THEN 1 ELSE 0 END +
                CASE WHEN dom_2 IS NOT NULL AND dom_3 IS NOT NULL AND dom_2 != dom_3 AND dom_2 != 0 AND dom_3 != 0 THEN 1 ELSE 0 END +
                CASE WHEN dom_3 IS NOT NULL AND dom_4 IS NOT NULL AND dom_3 != dom_4 AND dom_3 != 0 AND dom_4 != 0 THEN 1 ELSE 0 END +
                CASE WHEN dom_4 IS NOT NULL AND dom_5 IS NOT NULL AND dom_4 != dom_5 AND dom_4 != 0 AND dom_5 != 0 THEN 1 ELSE 0 END
            )::INTEGER as n_domain_changes_last5_prev,
            -- n_unique_domains_last5_prev: count of distinct non-zero domains in last 5 cycles (0-3)
            (
                (CASE WHEN COALESCE(dom_1,0) = 1 OR COALESCE(dom_2,0) = 1 OR COALESCE(dom_3,0) = 1 OR COALESCE(dom_4,0) = 1 OR COALESCE(dom_5,0) = 1 THEN 1 ELSE 0 END) +
                (CASE WHEN COALESCE(dom_1,0) = 2 OR COALESCE(dom_2,0) = 2 OR COALESCE(dom_3,0) = 2 OR COALESCE(dom_4,0) = 2 OR COALESCE(dom_5,0) = 2 THEN 1 ELSE 0 END) +
                (CASE WHEN COALESCE(dom_1,0) = 3 OR COALESCE(dom_2,0) = 3 OR COALESCE(dom_3,0) = 3 OR COALESCE(dom_4,0) = 3 OR COALESCE(dom_5,0) = 3 THEN 1 ELSE 0 END)
            )::INTEGER as n_unique_domains_last5_prev,
            -- domain_bitmask_last5_prev: bitmask of domains (brakes=1, susp=2, tyres=4), range 0-7
            (
                (CASE WHEN COALESCE(dom_1,0) = 1 OR COALESCE(dom_2,0) = 1 OR COALESCE(dom_3,0) = 1 OR COALESCE(dom_4,0) = 1 OR COALESCE(dom_5,0) = 1 THEN 1 ELSE 0 END) +
                (CASE WHEN COALESCE(dom_1,0) = 2 OR COALESCE(dom_2,0) = 2 OR COALESCE(dom_3,0) = 2 OR COALESCE(dom_4,0) = 2 OR COALESCE(dom_5,0) = 2 THEN 2 ELSE 0 END) +
                (CASE WHEN COALESCE(dom_1,0) = 3 OR COALESCE(dom_2,0) = 3 OR COALESCE(dom_3,0) = 3 OR COALESCE(dom_4,0) = 3 OR COALESCE(dom_5,0) = 3 THEN 4 ELSE 0 END)
            )::INTEGER as domain_bitmask_last5_prev
        FROM with_lags
    """)

    log_progress("Selecting final features...")

    # Step 4: Select final output columns
    # NOTE: Persistence features are NOT capped - they reflect actual consecutive advisory streaks
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE final_features AS
        SELECT
            test_id,
            -- Persistence (4) - NOT capped, reflects actual consecutive advisory streaks
            persist_len_any_prev,
            persist_len_brakes_prev,
            persist_len_susp_steer_prev,
            persist_len_tyres_prev,
            -- Recency (4) - convert -1 to NULL for "never had advisory"
            CASE WHEN cycles_since_any_adv_prev < 0 THEN NULL ELSE cycles_since_any_adv_prev END as cycles_since_any_adv_prev,
            CASE WHEN cycles_since_brakes_prev < 0 THEN NULL ELSE cycles_since_brakes_prev END as cycles_since_brakes_prev,
            CASE WHEN cycles_since_susp_steer_prev < 0 THEN NULL ELSE cycles_since_susp_steer_prev END as cycles_since_susp_steer_prev,
            CASE WHEN cycles_since_tyres_prev < 0 THEN NULL ELSE cycles_since_tyres_prev END as cycles_since_tyres_prev,
            -- Has-Ever Flags (4) - 1 if ever had advisory in domain, 0 otherwise
            CASE WHEN cycles_since_any_adv_prev >= 0 THEN 1 ELSE 0 END::INTEGER as has_ever_any_adv_prev,
            CASE WHEN cycles_since_brakes_prev >= 0 THEN 1 ELSE 0 END::INTEGER as has_ever_brakes_prev,
            CASE WHEN cycles_since_susp_steer_prev >= 0 THEN 1 ELSE 0 END::INTEGER as has_ever_susp_steer_prev,
            CASE WHEN cycles_since_tyres_prev >= 0 THEN 1 ELSE 0 END::INTEGER as has_ever_tyres_prev,
            -- Switching (3)
            n_domain_changes_last5_prev,
            n_unique_domains_last5_prev,
            domain_bitmask_last5_prev
        FROM with_switching
    """)

    # Step 5: Deduplicate by test_id (in case of upstream data quality issues)
    # Note: If same test_id appears for different vehicles, keep first occurrence
    dup_count = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT test_id) FROM final_features").fetchone()[0]
    if dup_count > 0:
        log_progress(f"WARNING: Found {dup_count} duplicate test_ids - deduplicating...")
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE final_features_dedup AS
            SELECT * FROM final_features
            QUALIFY ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY test_id) = 1
        """)
        conn.execute("DROP TABLE final_features")
        conn.execute("ALTER TABLE final_features_dedup RENAME TO final_features")

    # Step 6: Save to parquet
    log_progress(f"Saving to {output_path.name}...")
    conn.execute(f"COPY final_features TO '{output_path}' (FORMAT PARQUET)")

    # Get stats
    final_count = conn.execute("SELECT COUNT(*) FROM final_features").fetchone()[0]
    log_progress(f"Saved {final_count:,} rows")

    # Report feature stats
    log_progress("Feature statistics:")
    for col in ['persist_len_any_prev', 'persist_len_brakes_prev', 'persist_len_susp_steer_prev',
                'persist_len_tyres_prev', 'cycles_since_any_adv_prev', 'cycles_since_brakes_prev',
                'cycles_since_susp_steer_prev', 'cycles_since_tyres_prev',
                'has_ever_any_adv_prev', 'has_ever_brakes_prev', 'has_ever_susp_steer_prev', 'has_ever_tyres_prev',
                'n_domain_changes_last5_prev', 'n_unique_domains_last5_prev', 'domain_bitmask_last5_prev']:
        stats = conn.execute(f"""
            SELECT
                AVG({col})::DECIMAL(10,3) as mean,
                COUNT(*) - COUNT({col}) as null_count,
                SUM(CASE WHEN {col} IS NOT NULL AND {col} != 0 THEN 1 ELSE 0 END) as nonzero
            FROM final_features
        """).fetchone()
        log(f"    {col:35s}: mean={stats[0]}, nulls={stats[1]:,}, nonzero={stats[2]:,}")

    elapsed = time.time() - start_time
    log_progress(f"COMPLETE in {elapsed:.1f}s")

    return final_count


def main():
    log("=" * 70)
    log("BUILDING MDPS SEQUENCE FEATURES V2 (DuckDB)")
    log("=" * 70)

    WORK_DIR = Path.home() / "autosafe_work"

    # Input paths
    TS_DEV = WORK_DIR / "cycle_advisory_timeseries_dev.parquet"
    TS_OOT = WORK_DIR / "cycle_advisory_timeseries_oot.parquet"

    # Output paths
    OUTPUT_DEV = WORK_DIR / "mdps_sequence_features_dev.parquet"
    OUTPUT_OOT = WORK_DIR / "mdps_sequence_features_oot.parquet"

    # Check inputs exist
    for p in [TS_DEV, TS_OOT]:
        if not p.exists():
            log(f"ERROR: {p} not found!")
            return

    # Create DuckDB connection with generous memory
    conn = duckdb.connect(":memory:")
    conn.execute("SET memory_limit='8GB'")
    conn.execute("SET threads=4")

    # Build DEV features
    build_sequence_features(conn, TS_DEV, OUTPUT_DEV, "DEV")

    # Build OOT features
    build_sequence_features(conn, TS_OOT, OUTPUT_OOT, "OOT")

    conn.close()

    log("\n" + "=" * 70)
    log("ALL COMPLETE")
    log("=" * 70)


if __name__ == "__main__":
    main()
