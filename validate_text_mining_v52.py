"""
Validate Text Mining Features (V52)
====================================

Performs validation for text mining semantic features.

Key validations:
1. Orthogonality Check: Correlation with V49 mechanical_decay_index
2. Distribution Analysis: Check for reasonable value distributions
3. Standalone AUC: Predictive power of new features
4. Coverage Analysis: Verify feature coverage across dev/OOT sets

Created: 2026-01-16
"""

import duckdb
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from scipy import stats
from sklearn.metrics import roc_auc_score
import json


# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# Input files
TEXT_MINING_FEATURES = WORK_DIR / "text_mining_features_v52.parquet"
MECHANICAL_DECAY_FEATURES = WORK_DIR / "mechanical_decay_features_v49.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

# Output
OUTPUT_DIR = WORK_DIR / "validation_text_mining_v52"


def main():
    print("=" * 70)
    print("VALIDATE TEXT MINING FEATURES (V52)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Load data
    print("\n[Step 1] Loading data...")

    con = duckdb.connect()

    # Load text mining features
    v52_df = con.execute(f"""
        SELECT * FROM read_parquet('{TEXT_MINING_FEATURES}')
    """).df()
    print(f"  Text mining features (V52): {len(v52_df):,} rows")

    # Load mechanical decay features (V49)
    v49_df = con.execute(f"""
        SELECT test_id, mech_decay_index, mech_decay_index_normalized, mech_risk_driver
        FROM read_parquet('{MECHANICAL_DECAY_FEATURES}')
    """).df()
    print(f"  Mechanical decay features (V49): {len(v49_df):,} rows")

    # Load dev and OOT sets with target
    dev_df = con.execute(f"""
        SELECT test_id, vehicle_id, is_failure
        FROM read_parquet('{DEV_SET}')
    """).df()

    oot_df = con.execute(f"""
        SELECT test_id, vehicle_id, is_failure
        FROM read_parquet('{OOT_SET}')
    """).df()

    print(f"  Dev set: {len(dev_df):,} rows")
    print(f"  OOT set: {len(oot_df):,} rows")

    # Merge all features
    combined = dev_df.assign(split='dev')
    combined = pd.concat([combined, oot_df.assign(split='oot')])
    combined = combined.merge(v52_df, on='test_id', how='left')
    combined = combined.merge(v49_df, on='test_id', how='left')

    print(f"  Combined: {len(combined):,} rows")

    # =========================================================================
    # Validation 1: Orthogonality with V49
    # =========================================================================
    print("\n[Validation 1] Orthogonality Check (V52 vs V49)")

    # Filter to valid data
    valid_df = combined[
        combined['text_corrosion_index'].notna() &
        combined['mech_decay_index'].notna()
    ].copy()

    print(f"  Valid rows for analysis: {len(valid_df):,}")

    # Compute correlations between V52 features and V49 index
    v52_cols = ['text_corrosion_index', 'text_wear_index', 'text_leak_index', 'text_damage_index']

    correlations = {}
    for col in v52_cols:
        corr, pval = stats.pearsonr(valid_df[col], valid_df['mech_decay_index'])
        correlations[col] = {'correlation': corr, 'pval': pval}
        status = "HIGH" if abs(corr) > 0.7 else "MODERATE" if abs(corr) > 0.3 else "LOW"
        print(f"  {col} vs mech_decay_index: r={corr:.4f} (p={pval:.2e}) [{status}]")

    # Combined text index
    valid_df['text_combined_index'] = (
        valid_df['text_corrosion_index'] +
        valid_df['text_wear_index'] +
        valid_df['text_leak_index'] +
        valid_df['text_damage_index']
    )

    combined_corr, combined_pval = stats.pearsonr(
        valid_df['text_combined_index'],
        valid_df['mech_decay_index']
    )
    print(f"\n  Combined text index vs mech_decay_index: r={combined_corr:.4f}")

    # =========================================================================
    # Validation 2: Standalone AUC
    # =========================================================================
    print("\n[Validation 2] Standalone AUC Analysis")

    valid_with_target = valid_df[valid_df['is_failure'].notna()].copy()
    valid_with_target['is_failure'] = valid_with_target['is_failure'].astype(int)

    if len(valid_with_target) > 0:
        aucs = {}

        # Individual mechanism indices
        for col in v52_cols:
            auc = roc_auc_score(valid_with_target['is_failure'], valid_with_target[col])
            aucs[col] = auc
            print(f"  {col}: AUC = {auc:.4f}")

        # Combined text index
        auc_combined = roc_auc_score(valid_with_target['is_failure'], valid_with_target['text_combined_index'])
        aucs['text_combined_index'] = auc_combined
        print(f"  text_combined_index: AUC = {auc_combined:.4f}")

        # Max severity score
        auc_severity = roc_auc_score(valid_with_target['is_failure'], valid_with_target['max_severity_score'])
        aucs['max_severity_score'] = auc_severity
        print(f"  max_severity_score: AUC = {auc_severity:.4f}")

        # V49 for comparison
        auc_v49 = roc_auc_score(valid_with_target['is_failure'], valid_with_target['mech_decay_index'])
        aucs['mech_decay_index_v49'] = auc_v49
        print(f"  mech_decay_index (V49): AUC = {auc_v49:.4f}")

    # =========================================================================
    # Validation 3: Distribution Analysis
    # =========================================================================
    print("\n[Validation 3] Feature Distributions")

    for col in v52_cols:
        nonzero = (valid_df[col] > 0).sum()
        pct_nonzero = 100 * nonzero / len(valid_df)
        mean_val = valid_df[col].mean()
        std_val = valid_df[col].std()
        p99 = valid_df[col].quantile(0.99)
        print(f"  {col}:")
        print(f"    Nonzero: {nonzero:,} ({pct_nonzero:.1f}%)")
        print(f"    Mean: {mean_val:.4f}, Std: {std_val:.4f}, P99: {p99:.4f}")

    print(f"\n  dominant_mechanism distribution:")
    for mech, count in valid_df['dominant_mechanism'].value_counts().items():
        pct = 100 * count / len(valid_df)
        print(f"    {mech}: {count:,} ({pct:.1f}%)")

    # =========================================================================
    # Validation 4: Coverage by Split
    # =========================================================================
    print("\n[Validation 4] Coverage by Split")

    for split in ['dev', 'oot']:
        split_df = valid_df[valid_df['split'] == split]
        has_history = (split_df['has_advisory_history'] == 1).sum()
        pct = 100 * has_history / len(split_df)
        print(f"  {split}: {has_history:,} / {len(split_df):,} ({pct:.1f}%) with advisory history")

    # =========================================================================
    # Validation 5: Visualizations
    # =========================================================================
    print("\n[Validation 5] Creating visualizations...")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Plot 1: V52 indices vs V49 index
    ax1 = axes[0, 0]
    sample = valid_df.sample(min(50000, len(valid_df)), random_state=42)
    ax1.scatter(sample['mech_decay_index'], sample['text_combined_index'], alpha=0.1, s=1)
    ax1.set_xlabel('V49 Mechanical Decay Index')
    ax1.set_ylabel('V52 Combined Text Index')
    ax1.set_title(f'V52 vs V49 (r={combined_corr:.3f})')

    # Plot 2: Mechanism index distributions
    ax2 = axes[0, 1]
    for col in v52_cols:
        nonzero = valid_df[valid_df[col] > 0][col]
        if len(nonzero) > 0:
            ax2.hist(nonzero, bins=50, alpha=0.5, label=col.replace('text_', '').replace('_index', ''))
    ax2.set_xlabel('Index Value (nonzero only)')
    ax2.set_ylabel('Count')
    ax2.set_title('V52 Mechanism Index Distributions')
    ax2.legend()
    ax2.set_yscale('log')

    # Plot 3: AUC comparison bar chart
    ax3 = axes[1, 0]
    if 'aucs' in dir():
        auc_items = list(aucs.items())
        auc_names = [x[0].replace('text_', '').replace('_index', '') for x in auc_items]
        auc_vals = [x[1] for x in auc_items]
        bars = ax3.bar(range(len(auc_vals)), auc_vals, color='steelblue')
        ax3.set_xticks(range(len(auc_vals)))
        ax3.set_xticklabels(auc_names, rotation=45, ha='right')
        ax3.set_ylabel('Standalone AUC')
        ax3.set_title('Feature Standalone AUC')
        ax3.axhline(y=0.5, color='r', linestyle='--', label='Random')
        for bar, val in zip(bars, auc_vals):
            ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
                    f'{val:.3f}', ha='center', va='bottom', fontsize=8)

    # Plot 4: Dominant mechanism distribution
    ax4 = axes[1, 1]
    mech_counts = valid_df['dominant_mechanism'].value_counts()
    ax4.pie(mech_counts.values, labels=mech_counts.index, autopct='%1.1f%%', startangle=90)
    ax4.set_title('Dominant Mechanism Distribution')

    plt.tight_layout()
    plot_path = OUTPUT_DIR / 'v52_validation.png'
    plt.savefig(plot_path, dpi=150)
    print(f"  Saved: {plot_path}")
    plt.close()

    # =========================================================================
    # Save Report
    # =========================================================================
    print("\n[Step 6] Saving detailed report...")

    report = {
        'timestamp': datetime.now().isoformat(),
        'total_rows': int(len(v52_df)),
        'correlations_with_v49': {k: float(v['correlation']) for k, v in correlations.items()},
        'combined_correlation_with_v49': float(combined_corr),
        'orthogonality_passed': bool(abs(combined_corr) < 0.5),
        'aucs': {k: float(v) for k, v in aucs.items()} if 'aucs' in dir() else None,
        'coverage': {
            'has_advisory_history_pct': float(100 * (valid_df['has_advisory_history'] == 1).sum() / len(valid_df))
        }
    }

    report_path = OUTPUT_DIR / 'validation_report.json'
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"  Saved: {report_path}")

    print("\n" + "=" * 70)
    print(f"VALIDATION COMPLETE: {datetime.now().isoformat()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
