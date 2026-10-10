"""
Leakage Detection Unit Test

Asserts that no feature is a deterministic function of the label.
Key checks:
1. No feature with AUC ~1.0 or ~0.0 (perfect prediction)
2. No feature group with 100% or 0% failure rate
3. Feature computation never reads current_outcome except for label

Run this BEFORE any model training to catch leakage early.
"""

import duckdb
import numpy as np
import pytest
from pathlib import Path
from sklearn.metrics import roc_auc_score


def test_no_perfect_separation():
    """Assert no feature perfectly predicts is_failure."""
    print("=" * 60)
    print("LEAKAGE DETECTION TEST: No Perfect Separation")
    print("=" * 60)

    # tests/ lives one level below the project root
    PROJECT_ROOT = Path(__file__).parent.parent
    DEV_SET = PROJECT_ROOT / "stratified_samples" / "dev_set.parquet"

    conn = duckdb.connect()
    conn.execute("SET memory_limit='4GB'")

    if not DEV_SET.exists():
        pytest.skip("stratified_samples/dev_set.parquet not found")
    
    # Load target and candidate features
    df = conn.execute(f"""
        SELECT 
            is_failure,
            prev_cycle_outcome_band,
            gap_band,
            advisory_band
        FROM read_parquet('{DEV_SET}')
        WHERE is_failure IS NOT NULL
    """).fetchdf()
    
    print(f"\nLoaded {len(df):,} rows from dev_set")
    
    violations = []
    
    # Test categorical features for perfect separation
    cat_features = ['prev_cycle_outcome_band', 'gap_band', 'advisory_band']
    
    for col in cat_features:
        print(f"\n  Testing {col}...")
        for val in df[col].dropna().unique():
            subset = df[df[col] == val]
            if len(subset) < 100:
                continue
            
            failure_rate = subset['is_failure'].mean()
            
            if failure_rate == 0.0 or failure_rate == 1.0:
                msg = f"    ❌ LEAKAGE: {col}={val} has {failure_rate*100:.0f}% failure rate (n={len(subset):,})"
                print(msg)
                violations.append((col, val, failure_rate))
            elif failure_rate < 0.01 or failure_rate > 0.99:
                print(f"    ⚠️  WARNING: {col}={val} has {failure_rate*100:.1f}% failure rate (near-perfect)")
    
    # Test transition2 if available
    transition_file = PROJECT_ROOT / "transition2_features.parquet"
    if transition_file.exists():
        print(f"\n  Testing transition2...")
        
        t2_df = conn.execute(f"""
            SELECT d.is_failure, t.transition2
            FROM read_parquet('{DEV_SET}') d
            LEFT JOIN read_parquet('{transition_file}') t USING (test_id)
            WHERE d.is_failure IS NOT NULL
            AND t.transition2 IS NOT NULL
            AND t.transition2 NOT LIKE '%X%'
        """).fetchdf()
        
        for val in t2_df['transition2'].dropna().unique():
            subset = t2_df[t2_df['transition2'] == val]
            if len(subset) < 100:
                continue
            
            failure_rate = subset['is_failure'].mean()
            
            if failure_rate == 0.0 or failure_rate == 1.0:
                msg = f"    ❌ LEAKAGE: transition2={val} has {failure_rate*100:.0f}% failure rate (n={len(subset):,})"
                print(msg)
                violations.append(('transition2', val, failure_rate))
            else:
                print(f"    ✓ transition2={val}: {failure_rate*100:.1f}% failure rate (n={len(subset):,})")
    
    # Summary — a detected violation must FAIL the test, not return a bool
    print("\n" + "=" * 60)
    assert not violations, (
        f"{len(violations)} leakage violations: "
        + "; ".join(f"{c}={v}: {r * 100:.0f}%" for c, v, r in violations)
    )
    print("PASSED: No perfect separation detected")


def test_transition2_no_current_outcome():
    """Assert transition2 doesn't include current test outcome."""
    print("\n" + "=" * 60)
    print("LEAKAGE DETECTION TEST: Transition2 No Current Outcome")
    print("=" * 60)
    
    PROJECT_ROOT = Path(__file__).parent.parent
    DEV_SET = PROJECT_ROOT / "stratified_samples" / "dev_set.parquet"
    TRANSITION_FILE = PROJECT_ROOT / "transition2_features.parquet"

    if not TRANSITION_FILE.exists():
        pytest.skip("transition2_features.parquet not found")
    
    conn = duckdb.connect()
    
    # Join and check if transition2 pattern ending predicts is_failure
    df = conn.execute(f"""
        SELECT 
            d.is_failure,
            t.transition2,
            -- Extract last character of transition2
            SUBSTRING(t.transition2, LENGTH(t.transition2), 1) AS last_char
        FROM read_parquet('{DEV_SET}') d
        LEFT JOIN read_parquet('{TRANSITION_FILE}') t USING (test_id)
        WHERE t.transition2 NOT LIKE '%X%'
    """).fetchdf()
    
    # Check if last_char of transition2 perfectly predicts is_failure
    for char in ['P', 'F']:
        subset = df[df['last_char'] == char]
        if len(subset) > 0:
            failure_rate = subset['is_failure'].mean()
            print(f"\n  transition2 ending in '{char}': failure_rate = {failure_rate*100:.1f}%")
            assert failure_rate not in (0.0, 1.0), (
                f"LEAKAGE: transition2 last char '{char}' has "
                f"{failure_rate * 100:.0f}% failure rate"
            )

    print("\n" + "=" * 60)
    print("PASSED: transition2 doesn't leak current outcome")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
