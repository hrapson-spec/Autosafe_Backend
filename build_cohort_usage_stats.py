"""
Build Cohort Usage Statistics
=============================

Computes hierarchical mean/std/count of mpd_last_interval for cohorts:
- (model_id, age_band) → finest level
- (make, age_band) → fallback
- (make) → fallback
- global → ultimate fallback

With minimum-n thresholds to ensure statistical reliability.

Created: 2026-01-05
"""

import duckdb
import pickle
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple, Optional

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"

OUTPUT_DIR = Path.home() / "autosafe_work"

# Minimum sample thresholds for each level
MIN_N_MODEL_AGE = 30
MIN_N_MAKE_AGE = 100
MIN_N_MAKE = 500


# =============================================================================
# Cohort Statistics Computation
# =============================================================================

def compute_cohort_usage_stats(
    df: pd.DataFrame,
    mpd_col: str = 'mpd_last_interval',
    model_col: str = 'model_id',
    make_col: str = 'make',
    age_col: str = 'age_band'
) -> Dict[str, Any]:
    """
    Compute hierarchical cohort statistics for mpd.

    Returns dict with structure:
    {
        'model_age': {('FORD FOCUS', '3-7y'): {'mean': X, 'std': Y, 'n': Z}, ...},
        'make_age': {('FORD', '3-7y'): {'mean': X, 'std': Y, 'n': Z}, ...},
        'make': {'FORD': {'mean': X, 'std': Y, 'n': Z}, ...},
        'global': {'mean': X, 'std': Y, 'n': Z}
    }
    """
    # Filter to valid mpd only
    valid_df = df[df[mpd_col].notna()].copy()
    print(f"  Computing cohort stats on {len(valid_df):,} records with valid mpd")

    stats = {}

    # Global level
    stats['global'] = {
        'mean': float(valid_df[mpd_col].mean()),
        'std': float(valid_df[mpd_col].std()),
        'n': int(len(valid_df))
    }
    print(f"  Global: mean={stats['global']['mean']:.2f}, std={stats['global']['std']:.2f}, n={stats['global']['n']:,}")

    # Make level
    make_stats = valid_df.groupby(make_col)[mpd_col].agg(['mean', 'std', 'count'])
    stats['make'] = {}
    for make, row in make_stats.iterrows():
        stats['make'][make] = {
            'mean': float(row['mean']),
            'std': float(row['std']) if pd.notna(row['std']) else stats['global']['std'],
            'n': int(row['count'])
        }
    print(f"  Make level: {len(stats['make'])} makes")

    # Make + Age level
    make_age_stats = valid_df.groupby([make_col, age_col])[mpd_col].agg(['mean', 'std', 'count'])
    stats['make_age'] = {}
    for (make, age), row in make_age_stats.iterrows():
        # Use make-level std as fallback if std is NaN
        fallback_std = stats['make'].get(make, stats['global'])['std']
        stats['make_age'][(make, age)] = {
            'mean': float(row['mean']),
            'std': float(row['std']) if pd.notna(row['std']) else fallback_std,
            'n': int(row['count'])
        }
    print(f"  Make+Age level: {len(stats['make_age'])} cohorts")

    # Model + Age level (finest)
    model_age_stats = valid_df.groupby([model_col, age_col])[mpd_col].agg(['mean', 'std', 'count'])
    stats['model_age'] = {}
    for (model, age), row in model_age_stats.iterrows():
        # Extract make from model_id (format: "MAKE MODEL")
        make = model.split()[0] if ' ' in str(model) else str(model)
        # Use make_age or make level std as fallback
        fallback_std = stats['make_age'].get((make, age), stats['make'].get(make, stats['global']))['std']
        stats['model_age'][(model, age)] = {
            'mean': float(row['mean']),
            'std': float(row['std']) if pd.notna(row['std']) else fallback_std,
            'n': int(row['count'])
        }
    print(f"  Model+Age level: {len(stats['model_age'])} cohorts")

    # Add thresholds to stats for reference
    stats['thresholds'] = {
        'min_n_model_age': MIN_N_MODEL_AGE,
        'min_n_make_age': MIN_N_MAKE_AGE,
        'min_n_make': MIN_N_MAKE
    }

    return stats


def compute_recent_usage_z(
    mpd: float,
    model_id: str,
    age_band: str,
    make: str,
    cohort_stats: Dict[str, Any]
) -> Tuple[Optional[float], Optional[int], Optional[str]]:
    """
    Compute z-score of mpd relative to hierarchical cohort.

    Returns:
        (z_score_clipped, cohort_n, cohort_level)
    """
    if mpd is None or pd.isna(mpd):
        return None, None, None

    thresholds = cohort_stats.get('thresholds', {
        'min_n_model_age': MIN_N_MODEL_AGE,
        'min_n_make_age': MIN_N_MAKE_AGE,
        'min_n_make': MIN_N_MAKE
    })

    # Try model_age level
    key = (model_id, age_band)
    if key in cohort_stats['model_age']:
        s = cohort_stats['model_age'][key]
        if s['n'] >= thresholds['min_n_model_age']:
            z = (mpd - s['mean']) / max(s['std'], 1e-6)
            return float(np.clip(z, -5, 5)), s['n'], 'model_age'

    # Fallback to make_age level
    key = (make, age_band)
    if key in cohort_stats['make_age']:
        s = cohort_stats['make_age'][key]
        if s['n'] >= thresholds['min_n_make_age']:
            z = (mpd - s['mean']) / max(s['std'], 1e-6)
            return float(np.clip(z, -5, 5)), s['n'], 'make_age'

    # Fallback to make level
    if make in cohort_stats['make']:
        s = cohort_stats['make'][make]
        if s['n'] >= thresholds['min_n_make']:
            z = (mpd - s['mean']) / max(s['std'], 1e-6)
            return float(np.clip(z, -5, 5)), s['n'], 'make'

    # Ultimate fallback to global
    s = cohort_stats['global']
    z = (mpd - s['mean']) / max(s['std'], 1e-6)
    return float(np.clip(z, -5, 5)), s['n'], 'global'


def compute_recent_usage_z_vectorized(
    mpd: pd.Series,
    model_id: pd.Series,
    age_band: pd.Series,
    make: pd.Series,
    cohort_stats: Dict[str, Any]
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Vectorized z-score computation.

    Returns:
        (z_scores, cohort_ns, cohort_levels)
    """
    n = len(mpd)
    z_scores = pd.Series(index=mpd.index, dtype=float)
    z_scores[:] = np.nan
    cohort_ns = pd.Series(index=mpd.index, dtype=float)
    cohort_ns[:] = np.nan
    cohort_levels = pd.Series(index=mpd.index, dtype=object)
    cohort_levels[:] = None

    thresholds = cohort_stats.get('thresholds', {
        'min_n_model_age': MIN_N_MODEL_AGE,
        'min_n_make_age': MIN_N_MAKE_AGE,
        'min_n_make': MIN_N_MAKE
    })

    # Process each row
    for idx in mpd.index:
        m = mpd.loc[idx]
        if pd.isna(m):
            continue

        model = model_id.loc[idx]
        age = age_band.loc[idx]
        mk = make.loc[idx]

        z, cohort_n, level = compute_recent_usage_z(
            m, model, age, mk, cohort_stats
        )

        z_scores.loc[idx] = z
        cohort_ns.loc[idx] = cohort_n
        cohort_levels.loc[idx] = level

    return z_scores, cohort_ns, cohort_levels


# =============================================================================
# Build and Save Statistics
# =============================================================================

def build_cohort_usage_stats_from_data(
    usage_features_path: Path,
    main_data_path: Path,
    output_path: Path,
    cutoff_year: int = 2024
):
    """
    Build cohort usage stats from training data only (before cutoff_year).

    Args:
        usage_features_path: Path to usage_features_dev.parquet
        main_data_path: Path to dev_set.parquet (for make, model_id, age_band)
        output_path: Where to save cohort_usage_stats.pkl
        cutoff_year: Only use data before this year (for leakage safety)
    """
    print("=" * 60)
    print("BUILDING COHORT USAGE STATISTICS")
    print("=" * 60)

    con = duckdb.connect()

    # Join usage features with main data to get model_id, make, age_band
    query = f"""
    SELECT
        u.test_id,
        u.mpd_last_interval,
        d.model_id,
        d.make,
        d.age_band,
        YEAR(d.test_date) as test_year
    FROM read_parquet('{usage_features_path}') u
    JOIN read_parquet('{main_data_path}') d ON u.test_id = d.test_id
    WHERE YEAR(d.test_date) < {cutoff_year}
      AND u.mpd_last_interval IS NOT NULL
    """

    print(f"\n[1] Loading data (years < {cutoff_year})...")
    df = con.execute(query).df()
    con.close()

    print(f"    Loaded {len(df):,} records with valid mpd")
    print(f"    Years: {df['test_year'].min()} - {df['test_year'].max()}")

    # Compute stats
    print("\n[2] Computing hierarchical cohort statistics...")
    stats = compute_cohort_usage_stats(
        df,
        mpd_col='mpd_last_interval',
        model_col='model_id',
        make_col='make',
        age_col='age_band'
    )

    # Save
    print(f"\n[3] Saving to {output_path}...")
    with open(output_path, 'wb') as f:
        pickle.dump(stats, f)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)

    return stats


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    # First, ensure usage features are built
    usage_dev_path = OUTPUT_DIR / "usage_features_dev.parquet"

    if not usage_dev_path.exists():
        print("Usage features not found. Building them first...")
        from build_usage_features import compute_usage_features_quick
        compute_usage_features_quick()

    # Build cohort stats
    stats = build_cohort_usage_stats_from_data(
        usage_features_path=usage_dev_path,
        main_data_path=DEV_SET,
        output_path=OUTPUT_DIR / "cohort_usage_stats.pkl",
        cutoff_year=2024
    )

    # Quick validation
    print("\n[Validation] Sample lookups:")
    test_cases = [
        (25.0, 'FORD FOCUS', '3-7y', 'FORD'),
        (50.0, 'BMW 3 SERIES', '7-10y', 'BMW'),
        (10.0, 'UNKNOWN MODEL', 'UNKNOWN', 'UNKNOWN'),
    ]

    for mpd, model, age, make in test_cases:
        z, n, level = compute_recent_usage_z(mpd, model, age, make, stats)
        print(f"  mpd={mpd}, model={model}, age={age} -> z={z:.2f}, n={n}, level={level}")
