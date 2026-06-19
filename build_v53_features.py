"""
Build V53 Features: System-Level Features for Bottom 20% Improvement
====================================================================

V53 addresses the gap identified in bottom 20% AUC analysis:
- Premium brands fail on TYRES (not predicted by V51/V52 mechanism features)
- Need system-level features, not just mechanism-level

Feature Sets:
1. System-Level Indices (48 features)
   - 8 systems x (advisory_index, advisory_12m, advisory_36m, failure_index, failure_12m, failure_36m)

2. Mechanism x System Cross-Features (32 features)
   - 4 mechanisms x 8 systems

3. Make x System EB Priors (~8 features per system)
   - make_{system}_fail_rate_eb

4. Recency Features (16 features)
   - 8 systems x 2 (days_since_advisory, days_since_failure)

Usage:
    python build_v53_features.py

Created: 2026-01-16
"""

import yaml
import duckdb
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Set, Optional, Tuple
from math import log
from collections import defaultdict
import re
import warnings
import sys
warnings.filterwarnings('ignore')


# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# Input files
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_set_with_advisory.parquet"
DEFECTS_SUMMARY = PROJECT_ROOT / "defects_summary.parquet"
ADVISORY_TOTALS = PROJECT_ROOT / "advisory_totals.parquet"
RFR_WITH_ANCHORS = PROJECT_ROOT / "prev_test_rfr_with_anchors.parquet"
ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
CYCLE_FIRST_WITH_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
ALL_TESTS = PROJECT_ROOT / "all_tests.parquet"

# Taxonomy
TAXONOMY_FILE = WORK_DIR / "config/system_taxonomy_v53.yaml"

# Output directory
OUTPUT_DIR = WORK_DIR / "v53_features"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Output files
SYSTEM_INDICES_FILE = OUTPUT_DIR / "system_indices.parquet"
MECHANISM_SYSTEM_FILE = OUTPUT_DIR / "mechanism_system_cross.parquet"
MAKE_SYSTEM_PRIORS_FILE = OUTPUT_DIR / "make_system_priors.parquet"
RECENCY_FILE = OUTPUT_DIR / "recency_features.parquet"
COMBINED_FILE = WORK_DIR / "v53_features.parquet"

# Systems to process
SYSTEMS = ['tyres', 'suspension', 'brakes', 'lamps', 'steering', 'structure', 'visibility', 'emissions']
MECHANISMS = ['corrosion', 'wear', 'leak', 'damage']


# =============================================================================
# Taxonomy Loader
# =============================================================================

class SystemTaxonomy:
    """Loads and manages the V53 system taxonomy."""

    def __init__(self, taxonomy_path: Path):
        """Load taxonomy from YAML file."""
        with open(taxonomy_path, 'r') as f:
            self.raw = yaml.safe_load(f)

        self.version = self.raw['version']
        self.systems = self.raw['systems']
        self.mechanisms = self.raw['mechanisms']
        self.severity = self.raw['severity']
        self.eb_config = self.raw['eb_config']

        # Build keyword patterns for efficient matching
        self._build_patterns()

        print(f"  Loaded V53 taxonomy v{self.version}", flush=True)
        print(f"  Systems: {list(self.systems.keys())}", flush=True)
        print(f"  Mechanisms: {list(self.mechanisms.keys())}", flush=True)

    def _build_patterns(self):
        """Compile regex patterns for keyword matching."""
        self.system_patterns = {}
        for system_name, config in self.systems.items():
            keywords = config['component_keywords']
            pattern = r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b'
            self.system_patterns[system_name] = re.compile(pattern, re.IGNORECASE)

        self.mechanism_patterns = {}
        for mech_name, config in self.mechanisms.items():
            keywords = config['keywords']
            pattern = r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b'
            self.mechanism_patterns[mech_name] = re.compile(pattern, re.IGNORECASE)

    def classify_system_from_text(self, text: str) -> List[str]:
        """Classify text into system categories."""
        if not text or pd.isna(text):
            return []
        text = str(text).lower()
        matched = []
        for system_name, pattern in self.system_patterns.items():
            if pattern.search(text):
                matched.append(system_name)
        return matched

    def classify_mechanism_from_text(self, text: str) -> List[str]:
        """Classify text into mechanism categories."""
        if not text or pd.isna(text):
            return []
        text = str(text).lower()
        matched = []
        for mech_name, pattern in self.mechanism_patterns.items():
            if pattern.search(text):
                matched.append(mech_name)
        return matched

    def get_system_config(self, system: str) -> dict:
        """Get configuration for a system."""
        return self.systems.get(system, {})

    def get_mechanism_config(self, mechanism: str) -> dict:
        """Get configuration for a mechanism."""
        return self.mechanisms.get(mechanism, {})


# =============================================================================
# System Mapping
# =============================================================================

def build_system_mapping_from_defects(taxonomy: SystemTaxonomy) -> Dict[str, List[str]]:
    """Build mapping from system name to defects_summary column names."""
    mapping = {}
    for system_name, config in taxonomy.systems.items():
        mapping[system_name] = config.get('defects_columns', [])
    return mapping


# Advisory totals column mapping
SYSTEM_TO_ADVISORY_COLS = {
    'tyres': ['adv_tyres', 'adv_wheels'],
    'suspension': ['adv_suspension'],
    'brakes': ['adv_brakes'],
    'lamps': ['adv_lights'],
    'steering': ['adv_steering'],
    'structure': ['adv_body'],
    'visibility': [],  # No direct column
    'emissions': ['adv_emissions'],
}


# =============================================================================
# Feature 1: System-Level Indices (Vectorized using DuckDB)
# =============================================================================

def build_system_indices(conn, taxonomy: SystemTaxonomy) -> pd.DataFrame:
    """
    Build system-level indices for each test_id.

    Uses a more efficient approach:
    1. For dev/oot sets that already have prev_advisory_* columns, use those
    2. Extend to additional systems using defects_summary
    """
    print("\n[1] Building System-Level Indices...", flush=True)

    # Load target tests with existing features
    print("  Loading target tests...", flush=True)
    target_df = conn.execute(f"""
        SELECT
            test_id,
            vehicle_id,
            test_date,
            is_failure,
            n_prior_tests,
            prev_advisory_brakes,
            prev_advisory_suspension,
            prev_advisory_steering,
            prev_advisory_tyres,
            prev_advisory_total,
            advisory_trend,
            days_since_last_test
        FROM read_parquet('{DEV_SET}')
        UNION ALL
        SELECT
            test_id,
            vehicle_id,
            test_date,
            is_failure,
            n_prior_tests,
            prev_advisory_brakes,
            prev_advisory_suspension,
            prev_advisory_steering,
            prev_advisory_tyres,
            prev_advisory_total,
            advisory_trend,
            days_since_last_test
        FROM read_parquet('{OOT_SET}')
    """).fetchdf()
    target_df['test_date'] = pd.to_datetime(target_df['test_date'])
    print(f"    Loaded {len(target_df):,} target tests", flush=True)

    # Load defects summary for additional systems
    print("  Loading defects summary...", flush=True)
    defects_df = conn.execute(f"""
        SELECT * FROM read_parquet('{DEFECTS_SUMMARY}')
    """).fetchdf()
    print(f"    Loaded {len(defects_df):,} defects records", flush=True)

    # Merge target with defects
    print("  Merging datasets...", flush=True)
    merged = target_df.merge(defects_df, on='test_id', how='left')
    print(f"    Merged: {len(merged):,} records", flush=True)

    # Build system mapping
    system_mapping = build_system_mapping_from_defects(taxonomy)

    # Initialize result
    result = target_df[['test_id']].copy()

    # Process each system
    for system in SYSTEMS:
        print(f"  Processing {system}...", flush=True)
        config = taxonomy.get_system_config(system)
        decay_rate = config.get('decay_rate', 0.8)
        base_weight = config.get('base_weight', 1.0)

        # Get advisory counts from existing columns if available
        if system == 'brakes':
            adv_counts = merged['prev_advisory_brakes'].fillna(0)
        elif system == 'suspension':
            adv_counts = merged['prev_advisory_suspension'].fillna(0)
        elif system == 'steering':
            adv_counts = merged['prev_advisory_steering'].fillna(0)
        elif system == 'tyres':
            adv_counts = merged['prev_advisory_tyres'].fillna(0)
        else:
            # Use defects_summary columns for other systems
            cols = system_mapping.get(system, [])
            cols = [c for c in cols if c in merged.columns]
            if cols:
                adv_counts = merged[cols].sum(axis=1).fillna(0)
            else:
                adv_counts = pd.Series(0, index=merged.index)

        # Compute decay-weighted index
        # Formula: base_weight × (1 + log(1 + count)) × decay^(years_ago)
        # Simplified: use days_since_last_test as proxy for recency
        days_ago = merged['days_since_last_test'].fillna(365)
        years_ago = days_ago / 365.25
        decay_factor = decay_rate ** years_ago.clip(lower=0, upper=10)

        # Advisory index = weight × log-scaled count × decay
        log_count = np.log1p(adv_counts.values.astype(float))
        advisory_index = base_weight * (1 + log_count) * decay_factor.values

        # For count features, use raw counts (12m and 36m are approximations)
        result[f'{system}_advisory_index'] = advisory_index
        result[f'{system}_advisory_count_12m'] = adv_counts.values
        result[f'{system}_advisory_count_36m'] = adv_counts.values * 1.5  # Rough approximation

        # Failure features (based on overall failure, weighted by system advisory)
        is_fail = merged['is_failure'].fillna(0).values
        result[f'{system}_failure_index'] = (adv_counts.values > 0).astype(float) * is_fail * decay_factor.values
        result[f'{system}_failure_count_12m'] = (adv_counts.values > 0).astype(float) * is_fail
        result[f'{system}_failure_count_36m'] = (adv_counts.values > 0).astype(float) * is_fail

        nonzero = (result[f'{system}_advisory_index'] > 0).sum()
        mean_val = result[f'{system}_advisory_index'].mean()
        print(f"    {system}: mean_idx={mean_val:.4f}, non-zero={nonzero:,}", flush=True)

    print(f"  Total features: {len([c for c in result.columns if c != 'test_id'])}", flush=True)
    return result


# =============================================================================
# Feature 2: Mechanism x System Cross-Features (Simplified)
# =============================================================================

def build_mechanism_system_cross(conn, taxonomy: SystemTaxonomy) -> pd.DataFrame:
    """
    Build mechanism x system cross-features.

    Simplified approach: Use existing V52 mechanism features combined with
    system indicators to create cross-features.
    """
    print("\n[2] Building Mechanism x System Cross-Features...", flush=True)

    # Load target tests
    print("  Loading target tests...", flush=True)
    target_df = conn.execute(f"""
        SELECT
            test_id,
            prev_advisory_brakes,
            prev_advisory_suspension,
            prev_advisory_steering,
            prev_advisory_tyres,
            prev_advisory_total
        FROM read_parquet('{DEV_SET}')
        UNION ALL
        SELECT
            test_id,
            prev_advisory_brakes,
            prev_advisory_suspension,
            prev_advisory_steering,
            prev_advisory_tyres,
            prev_advisory_total
        FROM read_parquet('{OOT_SET}')
    """).fetchdf()
    print(f"    Loaded {len(target_df):,} target tests", flush=True)

    # Load defects summary for system indicators
    print("  Loading defects summary...", flush=True)
    defects_df = conn.execute(f"""
        SELECT * FROM read_parquet('{DEFECTS_SUMMARY}')
    """).fetchdf()

    merged = target_df.merge(defects_df, on='test_id', how='left')

    # Build system mapping
    system_mapping = build_system_mapping_from_defects(taxonomy)

    # Initialize result
    result = target_df[['test_id']].copy()

    # For each mechanism x system, create a proxy feature
    # These are simplified approximations - in full implementation would use RFR text
    for mech in MECHANISMS:
        mech_config = taxonomy.get_mechanism_config(mech)
        mech_weight = mech_config.get('weight', 0.7)

        for system in SYSTEMS:
            sys_config = taxonomy.get_system_config(system)
            sys_weight = sys_config.get('base_weight', 1.0)

            # Get system advisory count
            if system == 'brakes':
                sys_count = merged['prev_advisory_brakes'].fillna(0)
            elif system == 'suspension':
                sys_count = merged['prev_advisory_suspension'].fillna(0)
            elif system == 'steering':
                sys_count = merged['prev_advisory_steering'].fillna(0)
            elif system == 'tyres':
                sys_count = merged['prev_advisory_tyres'].fillna(0)
            else:
                cols = system_mapping.get(system, [])
                cols = [c for c in cols if c in merged.columns]
                if cols:
                    sys_count = merged[cols].sum(axis=1).fillna(0)
                else:
                    sys_count = pd.Series(0, index=merged.index)

            # Create cross-feature as weighted system count
            # (In full implementation, would classify RFR text by mechanism)
            combined_weight = (mech_weight * sys_weight) ** 0.5
            cross_idx = combined_weight * np.log1p(sys_count.values.astype(float))

            result[f'{mech}_{system}_index'] = cross_idx

    # Print sample statistics
    for mech in MECHANISMS[:2]:  # Just first 2 for brevity
        for system in SYSTEMS[:2]:
            col = f'{mech}_{system}_index'
            mean_val = result[col].mean()
            nonzero = (result[col] > 0).sum()
            print(f"    {col}: mean={mean_val:.4f}, non-zero={nonzero:,}", flush=True)

    print(f"  Total features: {len(MECHANISMS) * len(SYSTEMS)}", flush=True)
    return result


# =============================================================================
# Feature 3: Make x System EB Priors
# =============================================================================

def build_make_system_priors(conn, taxonomy: SystemTaxonomy) -> pd.DataFrame:
    """
    Build make x system EB priors with hierarchical shrinkage.
    Uses OOF encoding on dev set, frozen priors for OOT.
    """
    print("\n[3] Building Make x System EB Priors...", flush=True)

    # Load dev set
    print("  Loading dev set...", flush=True)
    dev_df = conn.execute(f"""
        SELECT
            d.test_id,
            d.make,
            d.is_failure as target,
            d.prev_advisory_brakes,
            d.prev_advisory_suspension,
            d.prev_advisory_steering,
            d.prev_advisory_tyres
        FROM read_parquet('{DEV_SET}') d
        WHERE d.make IS NOT NULL
    """).fetchdf()
    print(f"    Dev records: {len(dev_df):,}", flush=True)

    # Load OOT set
    print("  Loading OOT set...", flush=True)
    oot_df = conn.execute(f"""
        SELECT
            d.test_id,
            d.make,
            d.is_failure as target,
            d.prev_advisory_brakes,
            d.prev_advisory_suspension,
            d.prev_advisory_steering,
            d.prev_advisory_tyres
        FROM read_parquet('{OOT_SET}') d
        WHERE d.make IS NOT NULL
    """).fetchdf()
    print(f"    OOT records: {len(oot_df):,}", flush=True)

    # EB configuration
    eb_config = taxonomy.eb_config
    k_make_system = eb_config['k_make_system']
    min_samples = eb_config['min_samples_make_system']

    # Create system indicators
    system_col_map = {
        'brakes': 'prev_advisory_brakes',
        'suspension': 'prev_advisory_suspension',
        'steering': 'prev_advisory_steering',
        'tyres': 'prev_advisory_tyres',
    }

    for system in SYSTEMS:
        col = system_col_map.get(system)
        if col and col in dev_df.columns:
            dev_df[f'{system}_has_issue'] = (dev_df[col].fillna(0) > 0).astype(int)
            oot_df[f'{system}_has_issue'] = (oot_df[col].fillna(0) > 0).astype(int)
        else:
            dev_df[f'{system}_has_issue'] = 0
            oot_df[f'{system}_has_issue'] = 0

    # 5-fold OOF encoding for dev set
    print("  Computing OOF priors for dev set (5-fold)...", flush=True)
    n_folds = 5
    np.random.seed(42)
    dev_df['fold'] = np.random.randint(0, n_folds, size=len(dev_df))

    # Initialize result arrays
    dev_priors = {system: np.zeros(len(dev_df)) for system in SYSTEMS}

    for fold in range(n_folds):
        train_mask = dev_df['fold'] != fold
        val_mask = dev_df['fold'] == fold
        train_data = dev_df[train_mask]

        for system in SYSTEMS:
            col = f'{system}_has_issue'

            # Compute global rate from train fold
            system_global = train_data[col].mean() if train_data[col].sum() > 0 else 0.1

            # Compute make x system rates
            make_stats = train_data.groupby('make')[col].agg(['mean', 'count']).reset_index()
            make_stats.columns = ['make', 'rate', 'count']

            # Build make -> EB rate lookup
            make_to_eb = {}
            for _, row in make_stats.iterrows():
                make = row['make']
                raw_rate = row['rate']
                n = row['count']

                if n >= min_samples:
                    eb_rate = (n * raw_rate + k_make_system * system_global) / (n + k_make_system)
                else:
                    eb_rate = system_global

                make_to_eb[make] = eb_rate

            # Apply to validation fold
            val_indices = dev_df[val_mask].index
            for idx in val_indices:
                make = dev_df.loc[idx, 'make']
                dev_priors[system][idx] = make_to_eb.get(make, system_global)

    # Frozen priors for OOT set
    print("  Computing frozen priors for OOT set...", flush=True)
    oot_priors = {system: np.zeros(len(oot_df)) for system in SYSTEMS}

    for system in SYSTEMS:
        col = f'{system}_has_issue'
        system_global = dev_df[col].mean() if dev_df[col].sum() > 0 else 0.1

        make_stats = dev_df.groupby('make')[col].agg(['mean', 'count']).reset_index()
        make_stats.columns = ['make', 'rate', 'count']

        make_to_eb = {}
        for _, row in make_stats.iterrows():
            make = row['make']
            raw_rate = row['rate']
            n = row['count']

            if n >= min_samples:
                eb_rate = (n * raw_rate + k_make_system * system_global) / (n + k_make_system)
            else:
                eb_rate = system_global

            make_to_eb[make] = eb_rate

        for idx in range(len(oot_df)):
            make = oot_df.iloc[idx]['make']
            oot_priors[system][idx] = make_to_eb.get(make, system_global)

    # Build result DataFrame
    print("  Combining dev and OOT priors...", flush=True)

    dev_result = dev_df[['test_id']].copy()
    for system in SYSTEMS:
        dev_result[f'make_{system}_fail_rate_eb'] = dev_priors[system]

    oot_result = oot_df[['test_id']].copy()
    for system in SYSTEMS:
        oot_result[f'make_{system}_fail_rate_eb'] = oot_priors[system]

    result = pd.concat([dev_result, oot_result], ignore_index=True)

    # Print summary
    for system in SYSTEMS:
        col = f'make_{system}_fail_rate_eb'
        mean_val = result[col].mean()
        std_val = result[col].std()
        print(f"    {col}: mean={mean_val:.4f}, std={std_val:.4f}", flush=True)

    print(f"  Total features: {len(SYSTEMS)}", flush=True)
    return result


# =============================================================================
# Feature 4: Recency Features (Simplified)
# =============================================================================

def build_recency_features(conn, taxonomy: SystemTaxonomy) -> pd.DataFrame:
    """
    Build recency features for each system.
    Uses days_since_last_test as base, adjusted by system advisory presence.
    """
    print("\n[4] Building Recency Features...", flush=True)

    # Load target tests
    print("  Loading target tests...", flush=True)
    target_df = conn.execute(f"""
        SELECT
            test_id,
            days_since_last_test,
            prev_advisory_brakes,
            prev_advisory_suspension,
            prev_advisory_steering,
            prev_advisory_tyres
        FROM read_parquet('{DEV_SET}')
        UNION ALL
        SELECT
            test_id,
            days_since_last_test,
            prev_advisory_brakes,
            prev_advisory_suspension,
            prev_advisory_steering,
            prev_advisory_tyres
        FROM read_parquet('{OOT_SET}')
    """).fetchdf()
    print(f"    Loaded {len(target_df):,} target tests", flush=True)

    # Initialize result
    result = target_df[['test_id']].copy()

    # System to column mapping
    system_col_map = {
        'brakes': 'prev_advisory_brakes',
        'suspension': 'prev_advisory_suspension',
        'steering': 'prev_advisory_steering',
        'tyres': 'prev_advisory_tyres',
    }

    days = target_df['days_since_last_test'].fillna(-1)

    for system in SYSTEMS:
        col = system_col_map.get(system)

        if col and col in target_df.columns:
            has_advisory = (target_df[col].fillna(0) > 0)
            # If has advisory, use actual days; otherwise -1 (no prior)
            result[f'days_since_last_{system}_advisory'] = np.where(has_advisory, days, -1)
        else:
            result[f'days_since_last_{system}_advisory'] = -1

        # Failure recency is approximated as days_since_last_test (simplified)
        result[f'days_since_last_{system}_failure'] = days

    # Print summary
    for system in SYSTEMS[:4]:
        col = f'days_since_last_{system}_advisory'
        valid = result[col] >= 0
        if valid.sum() > 0:
            mean_days = result.loc[valid, col].mean()
            print(f"    {system}_advisory: {valid.sum():,} valid, mean={mean_days:.0f} days", flush=True)
        else:
            print(f"    {system}_advisory: no valid records", flush=True)

    print(f"  Total features: {len(SYSTEMS) * 2}", flush=True)
    return result


# =============================================================================
# Main
# =============================================================================

def main():
    start_time = datetime.now()
    print("=" * 70, flush=True)
    print("V53 FEATURE BUILDER", flush=True)
    print("System-Level Features for Bottom 20% Improvement", flush=True)
    print("=" * 70, flush=True)

    # Load taxonomy
    print("\nLoading taxonomy...", flush=True)
    taxonomy = SystemTaxonomy(TAXONOMY_FILE)

    # Connect to DuckDB
    conn = duckdb.connect()
    conn.execute("SET memory_limit='8GB'")

    # Build each feature set
    system_indices = build_system_indices(conn, taxonomy)
    mechanism_system = build_mechanism_system_cross(conn, taxonomy)
    make_system_priors = build_make_system_priors(conn, taxonomy)
    recency_features = build_recency_features(conn, taxonomy)

    # Merge all features
    print("\n[5] Merging all feature sets...", flush=True)
    result = system_indices.merge(mechanism_system, on='test_id', how='outer')
    result = result.merge(make_system_priors, on='test_id', how='outer')
    result = result.merge(recency_features, on='test_id', how='outer')

    # Fill any NaNs
    for col in result.columns:
        if col != 'test_id':
            result[col] = result[col].fillna(0)

    print(f"  Final shape: {result.shape}", flush=True)
    print(f"  Total features: {len(result.columns) - 1}", flush=True)

    # Save individual feature sets
    print("\n[6] Saving feature files...", flush=True)
    system_indices.to_parquet(SYSTEM_INDICES_FILE, index=False)
    print(f"  Saved: {SYSTEM_INDICES_FILE}", flush=True)

    mechanism_system.to_parquet(MECHANISM_SYSTEM_FILE, index=False)
    print(f"  Saved: {MECHANISM_SYSTEM_FILE}", flush=True)

    make_system_priors.to_parquet(MAKE_SYSTEM_PRIORS_FILE, index=False)
    print(f"  Saved: {MAKE_SYSTEM_PRIORS_FILE}", flush=True)

    recency_features.to_parquet(RECENCY_FILE, index=False)
    print(f"  Saved: {RECENCY_FILE}", flush=True)

    # Save combined file
    result.to_parquet(COMBINED_FILE, index=False)
    print(f"  Saved: {COMBINED_FILE}", flush=True)

    # Summary
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70, flush=True)
    print("V53 FEATURE BUILD COMPLETE", flush=True)
    print("=" * 70, flush=True)
    print(f"  Total records: {len(result):,}", flush=True)
    print(f"  Total features: {len(result.columns) - 1}", flush=True)
    print(f"  Elapsed: {elapsed:.1f}s", flush=True)
    print("\nFeature counts by set:", flush=True)
    print(f"  System indices: {len([c for c in system_indices.columns if c != 'test_id'])}", flush=True)
    print(f"  Mechanism x System: {len([c for c in mechanism_system.columns if c != 'test_id'])}", flush=True)
    print(f"  Make x System priors: {len([c for c in make_system_priors.columns if c != 'test_id'])}", flush=True)
    print(f"  Recency: {len([c for c in recency_features.columns if c != 'test_id'])}", flush=True)

    conn.close()


if __name__ == "__main__":
    main()
