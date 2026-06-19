"""
Build Mechanical Decay Index Features (V49)
============================================

Computes the mechanical_decay_index feature that captures systemic deterioration risk.

Formula:
    Sub-Index = Σ(W_tier × (1 + log(count)) × decay^age_years)
    Composite = max(sub_indices) + 0.25 × Σ(other_sub_indices)

Systems tracked:
    - brake: Pipe corrosion, hose failure, disc pitting
    - suspension: Ball joint play, shock absorber leaking, coil spring corrosion
    - structure: Subframe corrosion, sill corrosion, seatbelt anchorage
    - steering: Rack play, track rod wear, column play

Output columns:
    - mech_decay_brake: Float sub-index
    - mech_decay_suspension: Float sub-index
    - mech_decay_structure: Float sub-index
    - mech_decay_steering: Float sub-index
    - mech_decay_index: Float composite
    - mech_decay_index_normalized: Float (if orthogonality check needed)
    - mech_risk_driver: String categorical

Created: 2026-01-16
Version: 1.0
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


# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# Input files
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
CYCLE_FIRST_TESTS = WORK_DIR / "cycle_first_tests.parquet"
TEST_ITEMS_LAKE = WORK_DIR / "test_items_lake"
RFR_2017_ADVISORIES = PROJECT_ROOT / "rfr_2017_advisories.parquet"
ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"

# Taxonomy
TAXONOMY_FILE = WORK_DIR / "config/mechanical_decay_taxonomy.yaml"

# Output
OUTPUT_FILE = WORK_DIR / "mechanical_decay_features_v49.parquet"

# Processing parameters
COMPOSITE_ALPHA = 0.25  # Weight for secondary systems in max-dominated composite


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class TierConfig:
    """Configuration for a severity tier."""
    weight: float
    decay_rate: float
    description: str


@dataclass
class DefectConfig:
    """Configuration for a defect type."""
    tier: str
    description: str
    codes: Set[int]
    keywords: List[str]
    sections: List[int]


@dataclass
class SystemConfig:
    """Configuration for a vehicle system."""
    description: str
    defects: Dict[str, DefectConfig]


# =============================================================================
# Taxonomy Loader
# =============================================================================

class MechanicalDecayTaxonomy:
    """Loads and manages the mechanical decay taxonomy."""

    def __init__(self, taxonomy_path: Path):
        """Load taxonomy from YAML file."""
        with open(taxonomy_path, 'r') as f:
            self.raw = yaml.safe_load(f)

        self.version = self.raw['version']
        self.parameters = self.raw['parameters']

        # Load tiers
        self.tiers: Dict[str, TierConfig] = {}
        for tier_name, tier_data in self.raw['tiers'].items():
            self.tiers[tier_name] = TierConfig(
                weight=tier_data['weight'],
                decay_rate=tier_data['decay_rate'],
                description=tier_data['description']
            )

        # Load systems and defects
        self.systems: Dict[str, SystemConfig] = {}
        self.code_to_defect: Dict[int, Tuple[str, str, str]] = {}  # code -> (system, defect, tier)

        for system_name, system_data in self.raw['systems'].items():
            defects = {}
            for defect_name, defect_data in system_data['defects'].items():
                codes = set(defect_data.get('codes', []))
                defect = DefectConfig(
                    tier=defect_data['tier'],
                    description=defect_data['description'],
                    codes=codes,
                    keywords=defect_data.get('keywords', []),
                    sections=defect_data.get('sections', [])
                )
                defects[defect_name] = defect

                # Build code -> defect mapping
                for code in codes:
                    self.code_to_defect[code] = (system_name, defect_name, defect_data['tier'])

            self.systems[system_name] = SystemConfig(
                description=system_data['description'],
                defects=defects
            )

        print(f"  Loaded taxonomy v{self.version}")
        print(f"  Systems: {list(self.systems.keys())}")
        print(f"  Total codes mapped: {len(self.code_to_defect)}")

    def classify_rfr(self, rfr_id: int) -> Optional[Tuple[str, str, str]]:
        """Classify an RFR code to (system, defect, tier) or None if not mapped."""
        return self.code_to_defect.get(rfr_id)

    def get_tier_config(self, tier_name: str) -> TierConfig:
        """Get tier configuration."""
        return self.tiers[tier_name]


# =============================================================================
# Feature Computation
# =============================================================================

def compute_decay_contribution(
    weight: float,
    count: int,
    decay_rate: float,
    age_years: float,
    max_age: float = 20.0
) -> float:
    """
    Compute decay-weighted contribution for a defect.

    Formula: W_tier × (1 + log(count)) × decay^age_years

    Args:
        weight: Tier weight (1.0 for critical, 0.5 for moderate, 0.2 for minor)
        count: Number of occurrences of this defect type
        decay_rate: Per-year decay rate (0.9 for critical, 0.7 for moderate, 0.5 for minor)
        age_years: Years since the advisory
        max_age: Cap on age for decay calculation

    Returns:
        Contribution to the system sub-index
    """
    if count == 0:
        return 0.0

    # Cap age to prevent extreme decay
    age_years = min(age_years, max_age)

    # Diminishing returns for multiple occurrences
    count_factor = 1 + log(count)

    # Time decay
    decay_factor = decay_rate ** age_years

    return weight * count_factor * decay_factor


def compute_composite_index(sub_indices: Dict[str, float], alpha: float = 0.25) -> float:
    """
    Compute max-dominated composite index.

    Formula: max(sub_indices) + alpha × Σ(other_sub_indices)

    Args:
        sub_indices: Dict mapping system name to sub-index value
        alpha: Weight for secondary systems

    Returns:
        Composite mechanical decay index
    """
    if not sub_indices:
        return 0.0

    values = list(sub_indices.values())
    max_val = max(values)
    sum_others = sum(values) - max_val

    return max_val + alpha * sum_others


def determine_risk_driver(
    sub_indices: Dict[str, float],
    tier_counts: Dict[str, Dict[str, int]],
    is_new_vehicle: bool = False
) -> str:
    """
    Determine the risk driver category for explainability.

    Returns one of:
        - BRAKE, SUSP, STRUCT, STEER: System with highest sub-index
        - MULTI_CRITICAL: Multiple systems have critical-tier advisories
        - CLEAN: No relevant advisories
        - NEW_VEHICLE: First MOT, no history
    """
    if is_new_vehicle:
        return 'NEW_VEHICLE'

    # Check if all sub-indices are zero
    total = sum(sub_indices.values())
    if total == 0:
        return 'CLEAN'

    # Count systems with critical-tier advisories
    critical_systems = []
    for system, tiers in tier_counts.items():
        if tiers.get('critical', 0) > 0:
            critical_systems.append(system)

    if len(critical_systems) > 1:
        return 'MULTI_CRITICAL'

    # Find dominant system
    max_system = max(sub_indices.keys(), key=lambda k: sub_indices[k])

    system_to_driver = {
        'brake': 'BRAKE',
        'suspension': 'SUSP',
        'structure': 'STRUCT',
        'steering': 'STEER'
    }

    return system_to_driver.get(max_system, 'CLEAN')


# =============================================================================
# Main Build Pipeline
# =============================================================================

def main():
    print("=" * 70)
    print("BUILD MECHANICAL DECAY INDEX FEATURES (V49)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    # Load taxonomy
    print("\n[Step 1] Loading taxonomy...")
    taxonomy = MechanicalDecayTaxonomy(TAXONOMY_FILE)

    # Connect to DuckDB
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")
    con.execute("SET threads=4")

    # =========================================================================
    # Step 2: Build target test list
    # =========================================================================
    print("\n[Step 2] Building target test list...")

    con.execute(f"""
        CREATE TEMP TABLE target_tests AS
        SELECT DISTINCT test_id, vehicle_id
        FROM (
            SELECT test_id, vehicle_id FROM read_parquet('{DEV_SET}')
            UNION ALL
            SELECT test_id, vehicle_id FROM read_parquet('{OOT_SET}')
        )
    """)

    n_tests = con.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    n_vehicles = con.execute("SELECT COUNT(DISTINCT vehicle_id) FROM target_tests").fetchone()[0]
    print(f"  Target tests: {n_tests:,}")
    print(f"  Target vehicles: {n_vehicles:,}")

    # =========================================================================
    # Step 3: Load vehicle history with test dates
    # =========================================================================
    print("\n[Step 3] Loading vehicle history...")

    # First, compute vehicle_age from first_use_date and test_date
    con.execute(f"""
        CREATE TEMP TABLE target_ages AS
        SELECT DISTINCT test_id, vehicle_id,
            DATEDIFF('year', first_use_date, test_date) as vehicle_age
        FROM (
            SELECT test_id, vehicle_id, first_use_date, test_date FROM read_parquet('{DEV_SET}')
            UNION ALL
            SELECT test_id, vehicle_id, first_use_date, test_date FROM read_parquet('{OOT_SET}')
        )
    """)

    # Then load vehicle history with computed vehicle_age
    con.execute(f"""
        CREATE TEMP TABLE vehicle_history AS
        SELECT
            c.vehicle_id,
            c.test_id,
            c.test_date,
            ta.vehicle_age,
            ROW_NUMBER() OVER (PARTITION BY c.vehicle_id ORDER BY c.test_date, c.test_id) as test_seq
        FROM read_parquet('{CYCLE_FIRST_TESTS}') c
        SEMI JOIN target_tests tt ON c.vehicle_id = tt.vehicle_id
        LEFT JOIN target_ages ta ON c.test_id = ta.test_id
    """)

    hist_count = con.execute("SELECT COUNT(*) FROM vehicle_history").fetchone()[0]
    print(f"  Vehicle history rows: {hist_count:,}")

    # =========================================================================
    # Step 4: Load RFR codes from test_items_lake
    # =========================================================================
    print("\n[Step 4] Loading RFR advisory data...")

    # Use test_items_lake (has multi-year data)
    con.execute(f"""
        CREATE TEMP TABLE rfr_data AS
        SELECT
            ti.test_id,
            ti.rfr_id,
            ti.rfr_type_code
        FROM read_parquet('{TEST_ITEMS_LAKE}/**/*.parquet') ti
        WHERE ti.rfr_type_code = 'A'  -- Advisories only (not failures)
    """)

    rfr_count = con.execute("SELECT COUNT(*) FROM rfr_data").fetchone()[0]
    print(f"  RFR advisory rows loaded: {rfr_count:,}")

    # =========================================================================
    # Step 5: Join history with RFR data and classify
    # =========================================================================
    print("\n[Step 5] Joining history with RFR data...")

    # Get the codes we care about from taxonomy
    all_codes = list(taxonomy.code_to_defect.keys())
    if not all_codes:
        print("  WARNING: No codes in taxonomy!")
    else:
        print(f"  Taxonomy codes: {len(all_codes)}")

    # Create a temp table with taxonomy codes for filtering
    codes_df = pd.DataFrame({
        'rfr_id': list(taxonomy.code_to_defect.keys()),
        'system': [v[0] for v in taxonomy.code_to_defect.values()],
        'defect': [v[1] for v in taxonomy.code_to_defect.values()],
        'tier': [v[2] for v in taxonomy.code_to_defect.values()]
    })
    con.register('taxonomy_codes', codes_df)

    # Join to get classified advisories
    con.execute("""
        CREATE TEMP TABLE classified_advisories AS
        SELECT
            vh.vehicle_id,
            vh.test_id as target_test_id,
            vh.test_date as target_date,
            vh.vehicle_age,
            vh.test_seq as target_seq,
            hist.test_id as adv_test_id,
            hist.test_date as adv_date,
            hist.test_seq as adv_seq,
            tc.system,
            tc.defect,
            tc.tier,
            rfr.rfr_id,
            -- Calculate age in years between advisory and target test
            DATEDIFF('day', hist.test_date, vh.test_date) / 365.25 as age_years
        FROM target_tests tt
        JOIN vehicle_history vh ON tt.vehicle_id = vh.vehicle_id AND tt.test_id = vh.test_id
        JOIN vehicle_history hist ON vh.vehicle_id = hist.vehicle_id
            AND hist.test_seq < vh.test_seq  -- Strictly prior tests
        JOIN rfr_data rfr ON hist.test_id = rfr.test_id
        JOIN taxonomy_codes tc ON rfr.rfr_id = tc.rfr_id
    """)

    classified_count = con.execute("SELECT COUNT(*) FROM classified_advisories").fetchone()[0]
    print(f"  Classified advisory rows: {classified_count:,}")

    # Show distribution by system
    system_dist = con.execute("""
        SELECT system, tier, COUNT(*) as cnt
        FROM classified_advisories
        GROUP BY system, tier
        ORDER BY system, tier
    """).df()
    print("\n  Advisory distribution by system/tier:")
    print(system_dist.to_string(index=False))

    # =========================================================================
    # Step 6: Aggregate to target test level
    # =========================================================================
    print("\n[Step 6] Aggregating features to test level...")

    # Get tier weights and decay rates from taxonomy
    tier_params = {
        name: (cfg.weight, cfg.decay_rate)
        for name, cfg in taxonomy.tiers.items()
    }

    # Aggregate by (target_test_id, system, tier) with age-weighted contributions
    # We'll compute the contribution for each advisory and sum by system
    agg_df = con.execute("""
        SELECT
            target_test_id as test_id,
            vehicle_id,
            vehicle_age,
            system,
            tier,
            COUNT(*) as adv_count,
            AVG(age_years) as avg_age_years,
            MIN(age_years) as min_age_years
        FROM classified_advisories
        GROUP BY target_test_id, vehicle_id, vehicle_age, system, tier
    """).df()

    print(f"  Aggregated rows: {len(agg_df):,}")

    # =========================================================================
    # Step 7: Compute sub-indices and composite
    # =========================================================================
    print("\n[Step 7] Computing decay indices...")

    # Process in Python for flexibility
    results = []

    # Get all target tests (including those with no advisories)
    all_targets = con.execute("""
        SELECT tt.test_id, tt.vehicle_id, vh.vehicle_age, vh.test_seq
        FROM target_tests tt
        JOIN vehicle_history vh ON tt.test_id = vh.test_id AND tt.vehicle_id = vh.vehicle_id
    """).df()

    print(f"  Processing {len(all_targets):,} target tests...")

    # Group aggregated data by test_id
    if len(agg_df) > 0:
        grouped = agg_df.groupby('test_id')
    else:
        grouped = {}

    for idx, row in all_targets.iterrows():
        test_id = row['test_id']
        vehicle_id = row['vehicle_id']
        vehicle_age = row['vehicle_age']
        test_seq = row['test_seq']

        is_new_vehicle = (test_seq == 1 and (vehicle_age is None or vehicle_age <= 3))

        # Initialize sub-indices
        sub_indices = {'brake': 0.0, 'suspension': 0.0, 'structure': 0.0, 'steering': 0.0}
        tier_counts = {s: {'critical': 0, 'moderate': 0, 'minor': 0} for s in sub_indices.keys()}

        # Get advisories for this test
        if test_id in grouped.groups:
            test_data = grouped.get_group(test_id)

            for _, adv_row in test_data.iterrows():
                system = adv_row['system']
                tier = adv_row['tier']
                count = adv_row['adv_count']
                avg_age = adv_row['avg_age_years'] if pd.notna(adv_row['avg_age_years']) else 0.0

                if system not in sub_indices:
                    continue

                # Get tier parameters
                weight, decay_rate = tier_params.get(tier, (0.0, 1.0))

                # Compute contribution
                contribution = compute_decay_contribution(
                    weight=weight,
                    count=int(count),
                    decay_rate=decay_rate,
                    age_years=avg_age
                )

                sub_indices[system] += contribution
                tier_counts[system][tier] += int(count)

        # Compute composite
        composite = compute_composite_index(sub_indices, alpha=COMPOSITE_ALPHA)

        # Determine risk driver
        risk_driver = determine_risk_driver(sub_indices, tier_counts, is_new_vehicle)

        results.append({
            'test_id': test_id,
            'vehicle_id': vehicle_id,
            'vehicle_age': vehicle_age,
            'mech_decay_brake': sub_indices['brake'],
            'mech_decay_suspension': sub_indices['suspension'],
            'mech_decay_structure': sub_indices['structure'],
            'mech_decay_steering': sub_indices['steering'],
            'mech_decay_index': composite,
            'mech_risk_driver': risk_driver
        })

        if (idx + 1) % 100000 == 0:
            print(f"    Processed {idx + 1:,} tests...")

    results_df = pd.DataFrame(results)
    print(f"  Computed features for {len(results_df):,} tests")

    # =========================================================================
    # Step 8: Compute age-normalized index (for orthogonality check)
    # =========================================================================
    print("\n[Step 8] Computing age-normalized index...")

    # Group by vehicle_age and compute mean index
    age_means = results_df.groupby('vehicle_age')['mech_decay_index'].mean().to_dict()

    def normalize_by_age(row):
        age = row['vehicle_age']
        idx = row['mech_decay_index']
        if age in age_means and age_means[age] > 0:
            return idx / age_means[age]
        return idx  # No normalization if age not found or mean is 0

    results_df['mech_decay_index_normalized'] = results_df.apply(normalize_by_age, axis=1)

    # =========================================================================
    # Step 9: Summary statistics
    # =========================================================================
    print("\n[Step 9] Summary statistics:")

    print("\n  Sub-index statistics:")
    for col in ['mech_decay_brake', 'mech_decay_suspension', 'mech_decay_structure',
                'mech_decay_steering', 'mech_decay_index']:
        stats = results_df[col].describe()
        print(f"    {col}:")
        print(f"      mean={stats['mean']:.4f}, std={stats['std']:.4f}, "
              f"max={stats['max']:.4f}, nonzero={100*(results_df[col] > 0).mean():.1f}%")

    print("\n  Risk driver distribution:")
    driver_dist = results_df['mech_risk_driver'].value_counts()
    for driver, count in driver_dist.items():
        print(f"    {driver}: {count:,} ({100*count/len(results_df):.1f}%)")

    # =========================================================================
    # Step 10: Save output
    # =========================================================================
    print(f"\n[Step 10] Saving to {OUTPUT_FILE}...")

    # Select final columns
    output_cols = [
        'test_id',
        'mech_decay_brake',
        'mech_decay_suspension',
        'mech_decay_structure',
        'mech_decay_steering',
        'mech_decay_index',
        'mech_decay_index_normalized',
        'mech_risk_driver'
    ]

    output_df = results_df[output_cols]
    output_df.to_parquet(OUTPUT_FILE, index=False)

    print(f"  Saved {len(output_df):,} rows")
    print(f"  File size: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")

    print("\n" + "=" * 70)
    print(f"COMPLETE: {datetime.now().isoformat()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
