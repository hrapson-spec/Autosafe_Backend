"""
Validate Mechanical Decay Index Features (V49)
===============================================

Performs the orthogonality check and validation for mechanical_decay_index.

Key validations:
1. Orthogonality Check: Plot index vs vehicle_age, verify variance within age bands
2. Distribution Analysis: Check for reasonable value distributions
3. Coverage Analysis: Verify feature coverage across dev/OOT sets
4. Correlation Analysis: Check correlation with age and existing features

Created: 2026-01-16
"""

import duckdb
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from scipy import stats


# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# Input files
MECHANICAL_DECAY_FEATURES = WORK_DIR / "mechanical_decay_features_v49.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

# Output
OUTPUT_DIR = WORK_DIR / "validation_mechanical_decay_v49"


def main():
    print("=" * 70)
    print("VALIDATE MECHANICAL DECAY INDEX (V49)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Load data
    print("\n[Step 1] Loading data...")

    con = duckdb.connect()

    # Load mechanical decay features
    features_df = con.execute(f"""
        SELECT * FROM read_parquet('{MECHANICAL_DECAY_FEATURES}')
    """).df()
    print(f"  Mechanical decay features: {len(features_df):,} rows")

    # Load dev and OOT sets with computed vehicle_age
    dev_df = con.execute(f"""
        SELECT test_id, vehicle_id,
               DATEDIFF('year', first_use_date, test_date) as vehicle_age,
               is_failure
        FROM read_parquet('{DEV_SET}')
    """).df()

    oot_df = con.execute(f"""
        SELECT test_id, vehicle_id,
               DATEDIFF('year', first_use_date, test_date) as vehicle_age,
               is_failure
        FROM read_parquet('{OOT_SET}')
    """).df()

    print(f"  Dev set: {len(dev_df):,} rows")
    print(f"  OOT set: {len(oot_df):,} rows")

    # Merge features with dev/OOT
    dev_merged = dev_df.merge(features_df, on='test_id', how='left', suffixes=('', '_feat'))
    oot_merged = oot_df.merge(features_df, on='test_id', how='left', suffixes=('', '_feat'))

    print(f"  Dev merged: {len(dev_merged):,} rows")
    print(f"  OOT merged: {len(oot_merged):,} rows")

    # =========================================================================
    # Validation 1: Orthogonality Check
    # =========================================================================
    print("\n[Validation 1] Orthogonality Check (Index vs Age)")

    # Combine for analysis
    combined_df = pd.concat([
        dev_merged.assign(split='dev'),
        oot_merged.assign(split='oot')
    ])

    # Filter to valid data
    valid_df = combined_df[
        combined_df['mech_decay_index'].notna() &
        combined_df['vehicle_age'].notna() &
        (combined_df['vehicle_age'] >= 0) &
        (combined_df['vehicle_age'] <= 30)
    ].copy()

    print(f"  Valid rows for analysis: {len(valid_df):,}")

    # Compute correlation
    age_corr, age_pval = stats.pearsonr(
        valid_df['vehicle_age'],
        valid_df['mech_decay_index']
    )
    print(f"  Pearson correlation (index vs age): {age_corr:.4f} (p={age_pval:.2e})")

    # Compute variance within age bands
    age_stats = valid_df.groupby('vehicle_age').agg({
        'mech_decay_index': ['mean', 'std', 'count', 'min', 'max']
    }).round(4)
    age_stats.columns = ['mean', 'std', 'count', 'min', 'max']
    age_stats['cv'] = age_stats['std'] / age_stats['mean'].replace(0, np.nan)  # Coefficient of variation

    print("\n  Variance within age bands:")
    print(age_stats.head(15).to_string())

    # Check for "good" orthogonality: high within-age variance
    avg_cv = age_stats['cv'].mean()
    print(f"\n  Average coefficient of variation within age bands: {avg_cv:.4f}")

    if abs(age_corr) > 0.8:
        print("  WARNING: High correlation with age - consider using normalized index!")
    elif abs(age_corr) > 0.5:
        print("  CAUTION: Moderate correlation with age - review carefully")
    else:
        print("  GOOD: Low correlation with age - orthogonality check passed")

    # =========================================================================
    # Validation 2: Plot Index vs Age
    # =========================================================================
    print("\n[Validation 2] Creating visualizations...")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Plot 1: Scatter plot (sampled for performance)
    ax1 = axes[0, 0]
    sample = valid_df.sample(min(50000, len(valid_df)), random_state=42)
    ax1.scatter(sample['vehicle_age'], sample['mech_decay_index'], alpha=0.1, s=1)
    ax1.set_xlabel('Vehicle Age (years)')
    ax1.set_ylabel('Mechanical Decay Index')
    ax1.set_title(f'Index vs Age (r={age_corr:.3f})')

    # Add age band means
    means = age_stats['mean'].reset_index()
    ax1.plot(means['vehicle_age'], means['mean'], 'r-', linewidth=2, label='Age band mean')
    ax1.legend()

    # Plot 2: Box plot by age band
    ax2 = axes[0, 1]
    age_bins = [0, 3, 5, 7, 10, 15, 20, 30]
    valid_df['age_band'] = pd.cut(valid_df['vehicle_age'], bins=age_bins, labels=[
        '0-3', '3-5', '5-7', '7-10', '10-15', '15-20', '20+'
    ])
    valid_df.boxplot(column='mech_decay_index', by='age_band', ax=ax2)
    ax2.set_xlabel('Age Band')
    ax2.set_ylabel('Mechanical Decay Index')
    ax2.set_title('Index Distribution by Age Band')
    plt.suptitle('')  # Remove automatic title

    # Plot 3: Risk driver distribution
    ax3 = axes[1, 0]
    driver_counts = valid_df['mech_risk_driver'].value_counts()
    driver_counts.plot(kind='bar', ax=ax3, color='steelblue')
    ax3.set_xlabel('Risk Driver')
    ax3.set_ylabel('Count')
    ax3.set_title('Risk Driver Distribution')
    ax3.tick_params(axis='x', rotation=45)

    # Plot 4: Sub-index distributions
    ax4 = axes[1, 1]
    sub_cols = ['mech_decay_brake', 'mech_decay_suspension',
                'mech_decay_structure', 'mech_decay_steering']
    for col in sub_cols:
        nonzero = valid_df[valid_df[col] > 0][col]
        if len(nonzero) > 0:
            ax4.hist(nonzero, bins=50, alpha=0.5, label=col.replace('mech_decay_', ''))
    ax4.set_xlabel('Sub-Index Value (nonzero only)')
    ax4.set_ylabel('Count')
    ax4.set_title('Sub-Index Distributions')
    ax4.legend()
    ax4.set_yscale('log')

    plt.tight_layout()
    plot_path = OUTPUT_DIR / 'orthogonality_check.png'
    plt.savefig(plot_path, dpi=150)
    print(f"  Saved: {plot_path}")
    plt.close()

    # =========================================================================
    # Validation 3: Predictive Power Check
    # =========================================================================
    print("\n[Validation 3] Predictive Power Analysis")

    # Split by failure outcome
    valid_df['is_failure'] = valid_df['is_failure'].astype(int)

    fail_stats = valid_df.groupby('is_failure').agg({
        'mech_decay_index': ['mean', 'std', 'median'],
        'mech_decay_index_normalized': ['mean', 'std', 'median']
    }).round(4)

    print("\n  Index by failure outcome:")
    print(fail_stats.to_string())

    # Compute AUC approximation using index
    from sklearn.metrics import roc_auc_score
    valid_with_target = valid_df[valid_df['is_failure'].notna()].copy()

    if len(valid_with_target) > 0:
        auc_raw = roc_auc_score(valid_with_target['is_failure'], valid_with_target['mech_decay_index'])
        auc_norm = roc_auc_score(valid_with_target['is_failure'], valid_with_target['mech_decay_index_normalized'])
        print(f"\n  Standalone AUC (raw index): {auc_raw:.4f}")
        print(f"  Standalone AUC (normalized index): {auc_norm:.4f}")

    # =========================================================================
    # Validation 4: Coverage Analysis
    # =========================================================================
    print("\n[Validation 4] Coverage Analysis")

    coverage_stats = valid_df.groupby('split').agg({
        'mech_decay_index': ['count', lambda x: (x == 0).mean()],
        'mech_risk_driver': lambda x: (x == 'CLEAN').mean()
    })
    coverage_stats.columns = ['total_count', 'zero_pct', 'clean_pct']

    print("\n  Coverage by split:")
    print(coverage_stats.round(4).to_string())

    # =========================================================================
    # Save detailed report
    # =========================================================================
    print("\n[Step 5] Saving detailed report...")

    report = {
        'timestamp': datetime.now().isoformat(),
        'total_rows': int(len(features_df)),
        'age_correlation': float(age_corr),
        'age_correlation_pval': float(age_pval),
        'avg_cv_within_age': float(avg_cv),
        'orthogonality_passed': bool(abs(age_corr) < 0.5),
        'auc_raw': float(auc_raw) if 'auc_raw' in dir() else None,
        'auc_normalized': float(auc_norm) if 'auc_norm' in dir() else None,
    }

    import json
    report_path = OUTPUT_DIR / 'validation_report.json'
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"  Saved: {report_path}")

    # Save age band stats
    age_stats_path = OUTPUT_DIR / 'age_band_stats.csv'
    age_stats.to_csv(age_stats_path)
    print(f"  Saved: {age_stats_path}")

    print("\n" + "=" * 70)
    print(f"VALIDATION COMPLETE: {datetime.now().isoformat()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
