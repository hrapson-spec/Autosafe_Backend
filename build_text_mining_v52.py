"""
Build Text Mining Features (V52)
================================

Extracts semantic defect signals from MOT advisory text descriptions.
Complements V49 mechanical_decay_index with mechanism-specific decay indices.

Features:
- text_corrosion_index: Decay-weighted corrosion advisory history
- text_wear_index: Decay-weighted wear advisory history
- text_leak_index: Decay-weighted leak/fluid advisory history
- text_damage_index: Decay-weighted damage/fracture advisory history
- max_severity_score: Highest severity seen (1-4 scale)
- severity_escalation_flag: Did severity increase between tests?
- has_advisory_history: Distinguishes NO_HISTORY from CLEAN
- dominant_mechanism: CORROSION/WEAR/LEAK/DAMAGE/MIXED/CLEAN/NO_HISTORY

Created: 2026-01-16
"""

import duckdb
import pandas as pd
import numpy as np
import yaml
import re
from pathlib import Path
from datetime import datetime
from math import log
from collections import defaultdict


# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# Input files
ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
TEST_ITEMS_LAKE = WORK_DIR / "test_items_lake"
TAXONOMY_FILE = WORK_DIR / "config/text_mining_taxonomy_v52.yaml"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
CYCLE_FIRST_TESTS = WORK_DIR / "cycle_first_tests.parquet"  # Full test history

# Output
OUTPUT_FILE = WORK_DIR / "text_mining_features_v52.parquet"


# =============================================================================
# Load Taxonomy
# =============================================================================

def load_taxonomy():
    """Load the text mining taxonomy from YAML."""
    with open(TAXONOMY_FILE, 'r') as f:
        return yaml.safe_load(f)


# =============================================================================
# Build RFR Text Classifier
# =============================================================================

class RFRTextClassifier:
    """
    Classifies RFR descriptions into mechanisms and severity levels.
    """

    def __init__(self, taxonomy: dict):
        self.taxonomy = taxonomy
        self.mechanisms = taxonomy['mechanisms']
        self.severity_map = taxonomy['severity']
        self.default_severity = taxonomy.get('default_severity', 2)

        # Compile keyword patterns for efficient matching
        self.mechanism_patterns = {}
        for mech_name, mech_config in self.mechanisms.items():
            keywords = mech_config['keywords']
            # Create pattern that matches any keyword as whole word
            pattern = r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b'
            self.mechanism_patterns[mech_name] = re.compile(pattern, re.IGNORECASE)

        self.severity_patterns = {}
        for level, config in self.severity_map.items():
            keywords = config['keywords']
            pattern = r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b'
            self.severity_patterns[int(level)] = re.compile(pattern, re.IGNORECASE)

    def classify_mechanism(self, text: str) -> list:
        """
        Classify text into mechanism categories.
        Returns list of matched mechanisms (can be multiple).
        """
        if not text or pd.isna(text):
            return []

        text = str(text).lower()
        matched = []

        for mech_name, pattern in self.mechanism_patterns.items():
            if pattern.search(text):
                matched.append(mech_name)

        return matched

    def extract_severity(self, text: str, deficiency_category: str = None) -> int:
        """
        Extract severity level (1-4) from text.
        Falls back to deficiency_category if text has no severity keywords.
        """
        if text and not pd.isna(text):
            text = str(text).lower()
            # Check severity patterns from highest to lowest
            for level in [4, 3, 2, 1]:
                if level in self.severity_patterns:
                    if self.severity_patterns[level].search(text):
                        return level

        # Fall back to deficiency category
        if deficiency_category:
            cat = str(deficiency_category).lower()
            if 'dangerous' in cat:
                return 4
            elif 'major' in cat:
                return 3
            elif 'minor' in cat:
                return 1

        return self.default_severity

    def get_mechanism_config(self, mechanism: str) -> dict:
        """Get weight and decay rate for a mechanism."""
        if mechanism in self.mechanisms:
            return {
                'weight': self.mechanisms[mechanism]['weight'],
                'decay_rate': self.mechanisms[mechanism]['decay_rate']
            }
        return {'weight': 0.5, 'decay_rate': 0.7}  # Default


# =============================================================================
# Build RFR Code -> Text Lookup
# =============================================================================

def build_rfr_lookup(con) -> pd.DataFrame:
    """
    Build RFR code to text description lookup from item_detail.csv.
    Returns DataFrame with rfr_id, combined_text, deficiency_category.
    """
    print("  Loading RFR text descriptions from item_detail.csv...")

    # Load item_detail
    item_detail = con.execute(f"""
        SELECT DISTINCT
            rfr_id,
            rfr_desc,
            rfr_insp_manual_desc,
            rfr_advisory_text,
            rfr_deficiency_category
        FROM read_csv('{ITEM_DETAIL}', delim='|', header=true)
    """).df()

    print(f"    Loaded {len(item_detail):,} unique RFR entries")

    # Combine text fields for comprehensive matching
    item_detail['combined_text'] = (
        item_detail['rfr_desc'].fillna('') + ' ' +
        item_detail['rfr_insp_manual_desc'].fillna('') + ' ' +
        item_detail['rfr_advisory_text'].fillna('')
    ).str.strip()

    # Dedupe by rfr_id (take first text version)
    rfr_lookup = item_detail.groupby('rfr_id').agg({
        'combined_text': 'first',
        'rfr_deficiency_category': 'first'
    }).reset_index()

    print(f"    Created lookup for {len(rfr_lookup):,} unique RFR codes")

    return rfr_lookup


# =============================================================================
# Pre-classify all RFR codes
# =============================================================================

def preclassify_rfr_codes(rfr_lookup: pd.DataFrame, classifier: RFRTextClassifier) -> dict:
    """
    Pre-classify all RFR codes to avoid repeated text processing.
    Returns dict: rfr_id -> {'mechanisms': [...], 'severity': int, 'weight': float, 'decay': float}
    """
    print("  Pre-classifying RFR codes...")

    rfr_classifications = {}

    for _, row in rfr_lookup.iterrows():
        rfr_id = int(row['rfr_id'])
        text = row['combined_text']
        deficiency = row['rfr_deficiency_category']

        mechanisms = classifier.classify_mechanism(text)
        severity = classifier.extract_severity(text, deficiency)

        # Get max weight/decay from matched mechanisms
        if mechanisms:
            max_weight = max(classifier.get_mechanism_config(m)['weight'] for m in mechanisms)
            # Use weighted average for decay
            total_weight = sum(classifier.get_mechanism_config(m)['weight'] for m in mechanisms)
            avg_decay = sum(
                classifier.get_mechanism_config(m)['weight'] * classifier.get_mechanism_config(m)['decay_rate']
                for m in mechanisms
            ) / total_weight if total_weight > 0 else 0.7
        else:
            max_weight = 0.5
            avg_decay = 0.7

        rfr_classifications[rfr_id] = {
            'mechanisms': mechanisms,
            'severity': severity,
            'weight': max_weight,
            'decay': avg_decay
        }

    # Stats
    classified = sum(1 for v in rfr_classifications.values() if v['mechanisms'])
    print(f"    Classified {classified:,} / {len(rfr_classifications):,} codes with mechanisms")

    mech_counts = defaultdict(int)
    for v in rfr_classifications.values():
        for m in v['mechanisms']:
            mech_counts[m] += 1

    print("    Mechanism distribution:")
    for mech, count in sorted(mech_counts.items(), key=lambda x: -x[1]):
        print(f"      {mech}: {count:,} codes")

    return rfr_classifications


# =============================================================================
# Compute Features per Vehicle
# =============================================================================

def compute_decay_contribution(weight: float, count: int, decay_rate: float, age_years: float, max_age: float = 20.0) -> float:
    """
    Compute decay-weighted contribution.
    Formula: weight * (1 + log(count)) * (decay_rate ^ age_years)
    """
    if count == 0:
        return 0.0
    age_years = min(age_years, max_age)
    count_factor = 1 + log(count)
    decay_factor = decay_rate ** age_years
    return weight * count_factor * decay_factor


def compute_text_features_for_vehicle(
    advisories: list,
    rfr_classifications: dict,
    as_of_date: datetime,
    taxonomy: dict
) -> dict:
    """
    Compute text mining features for a single vehicle's advisory history.

    Args:
        advisories: List of (rfr_id, test_date) tuples from prior tests
        rfr_classifications: Pre-computed RFR code classifications
        as_of_date: Reference date for decay calculation
        taxonomy: Full taxonomy config

    Returns:
        Dict of feature values
    """
    # Initialize accumulators
    mechanism_counts = defaultdict(int)
    mechanism_contributions = defaultdict(float)
    severity_history = []

    mechanisms_config = taxonomy['mechanisms']

    for rfr_id, test_date in advisories:
        if rfr_id not in rfr_classifications:
            continue

        classification = rfr_classifications[rfr_id]
        mechanisms = classification['mechanisms']
        severity = classification['severity']

        if not mechanisms:
            continue

        # Calculate age
        if isinstance(test_date, str):
            test_date = datetime.strptime(test_date[:10], '%Y-%m-%d')
        elif hasattr(test_date, 'date'):
            test_date = test_date

        age_years = (as_of_date - test_date).days / 365.25
        age_years = max(0, age_years)

        # Accumulate per mechanism
        for mech in mechanisms:
            mechanism_counts[mech] += 1
            config = mechanisms_config.get(mech, {'weight': 0.5, 'decay_rate': 0.7})
            weight = config['weight']
            decay_rate = config['decay_rate']

            contribution = compute_decay_contribution(weight, 1, decay_rate, age_years)
            mechanism_contributions[mech] += contribution

        severity_history.append((severity, age_years))

    # Compute final features
    has_history = len(advisories) > 0
    has_mechanism = sum(mechanism_counts.values()) > 0

    # Mechanism indices
    text_corrosion_index = mechanism_contributions.get('corrosion', 0.0)
    text_wear_index = mechanism_contributions.get('wear', 0.0)
    text_leak_index = mechanism_contributions.get('leak', 0.0)
    text_damage_index = mechanism_contributions.get('damage', 0.0)

    # Severity features
    if severity_history:
        max_severity_score = max(s for s, _ in severity_history)
        # Check for escalation (later advisory has higher severity than earlier)
        sorted_history = sorted(severity_history, key=lambda x: x[1], reverse=True)  # oldest first
        severity_escalation = False
        for i in range(1, len(sorted_history)):
            if sorted_history[i][0] > sorted_history[i-1][0]:
                severity_escalation = True
                break
    else:
        max_severity_score = 0
        severity_escalation = False

    # Dominant mechanism
    if not has_history:
        dominant_mechanism = 'NO_HISTORY'
    elif not has_mechanism:
        dominant_mechanism = 'CLEAN'
    else:
        # Find dominant
        sorted_mechs = sorted(mechanism_contributions.items(), key=lambda x: -x[1])
        if len(sorted_mechs) == 1:
            dominant_mechanism = sorted_mechs[0][0].upper()
        else:
            top_score = sorted_mechs[0][1]
            second_score = sorted_mechs[1][1]
            mixed_threshold = taxonomy['output'].get('mixed_threshold', 0.8)
            if second_score > mixed_threshold * top_score:
                dominant_mechanism = 'MIXED'
            else:
                dominant_mechanism = sorted_mechs[0][0].upper()

    return {
        'text_corrosion_index': text_corrosion_index,
        'text_wear_index': text_wear_index,
        'text_leak_index': text_leak_index,
        'text_damage_index': text_damage_index,
        'max_severity_score': max_severity_score,
        'severity_escalation_flag': int(severity_escalation),
        'has_advisory_history': int(has_history),
        'dominant_mechanism': dominant_mechanism
    }


# =============================================================================
# Main Pipeline
# =============================================================================

def main():
    print("=" * 70)
    print("BUILD TEXT MINING FEATURES (V52)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    # Load taxonomy
    print("\n[Step 1] Loading taxonomy...")
    taxonomy = load_taxonomy()
    classifier = RFRTextClassifier(taxonomy)
    print(f"  Loaded {len(taxonomy['mechanisms'])} mechanisms")

    # Connect to DuckDB
    con = duckdb.connect()

    # Build RFR lookup
    print("\n[Step 2] Building RFR code lookup...")
    rfr_lookup = build_rfr_lookup(con)

    # Pre-classify all RFR codes
    print("\n[Step 3] Pre-classifying RFR codes...")
    rfr_classifications = preclassify_rfr_codes(rfr_lookup, classifier)

    # Create mechanism classification table for SQL
    mech_rows = []
    for rfr_id, classification in rfr_classifications.items():
        mechanisms = classification['mechanisms']
        severity = classification['severity']
        if mechanisms:
            for mech in mechanisms:
                config = taxonomy['mechanisms'][mech]
                mech_rows.append({
                    'rfr_id': rfr_id,
                    'mechanism': mech,
                    'severity': severity,
                    'weight': config['weight'],
                    'decay_rate': config['decay_rate']
                })
        else:
            # No mechanism detected - still record severity
            mech_rows.append({
                'rfr_id': rfr_id,
                'mechanism': 'unknown',
                'severity': severity,
                'weight': 0.5,
                'decay_rate': 0.7
            })

    mech_df = pd.DataFrame(mech_rows)
    con.register('mechanism_lookup', mech_df)
    print(f"  Created mechanism lookup with {len(mech_df):,} rows")

    # Load target tests (dev + OOT)
    print("\n[Step 4] Loading target tests...")
    con.execute(f"""
        CREATE TEMP TABLE target_tests AS
        SELECT test_id, vehicle_id, test_date
        FROM (
            SELECT test_id, vehicle_id, test_date FROM read_parquet('{DEV_SET}')
            UNION ALL
            SELECT test_id, vehicle_id, test_date FROM read_parquet('{OOT_SET}')
        )
    """)
    target_count = con.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    print(f"  Loaded {target_count:,} target tests")

    # Build vehicle history using FULL test history (following V49 pattern)
    # This loads all tests for vehicles that appear in dev/OOT sets
    print("\n[Step 5] Building vehicle history from cycle_first_tests...")
    con.execute(f"""
        CREATE TEMP TABLE vehicle_history AS
        SELECT
            c.vehicle_id,
            c.test_id,
            c.test_date,
            ROW_NUMBER() OVER (PARTITION BY c.vehicle_id ORDER BY c.test_date, c.test_id) as test_seq
        FROM read_parquet('{CYCLE_FIRST_TESTS}') c
        SEMI JOIN target_tests tt ON c.vehicle_id = tt.vehicle_id
    """)
    hist_count = con.execute("SELECT COUNT(*) FROM vehicle_history").fetchone()[0]
    print(f"  Vehicle history rows: {hist_count:,}")

    # Load RFR advisories
    print("\n[Step 6] Loading RFR advisory data...")
    con.execute(f"""
        CREATE TEMP TABLE rfr_data AS
        SELECT
            ti.test_id,
            ti.rfr_id,
            ti.rfr_type_code
        FROM read_parquet('{TEST_ITEMS_LAKE}/**/*.parquet') ti
        WHERE ti.rfr_type_code = 'A'  -- Advisories only
    """)
    rfr_count = con.execute("SELECT COUNT(*) FROM rfr_data").fetchone()[0]
    print(f"  RFR advisory rows loaded: {rfr_count:,}")

    # Join history with RFR and mechanism classification
    print("\n[Step 7] Joining history with RFR data and mechanism classifications...")
    con.execute("""
        CREATE TEMP TABLE classified_advisories AS
        SELECT
            vh.vehicle_id,
            vh.test_id as target_test_id,
            vh.test_date as target_date,
            hist.test_id as adv_test_id,
            hist.test_date as adv_date,
            ml.mechanism,
            ml.severity,
            ml.weight,
            ml.decay_rate,
            -- Calculate age in years between advisory and target test
            DATEDIFF('day', hist.test_date, vh.test_date) / 365.25 as age_years
        FROM target_tests tt
        JOIN vehicle_history vh ON tt.vehicle_id = vh.vehicle_id AND tt.test_id = vh.test_id
        JOIN vehicle_history hist ON vh.vehicle_id = hist.vehicle_id
            AND hist.test_seq < vh.test_seq  -- Strictly prior tests
        JOIN rfr_data rfr ON hist.test_id = rfr.test_id
        JOIN mechanism_lookup ml ON rfr.rfr_id = ml.rfr_id
        WHERE ml.mechanism != 'unknown'  -- Only include classified mechanisms
    """)
    classified_count = con.execute("SELECT COUNT(*) FROM classified_advisories").fetchone()[0]
    print(f"  Classified advisory rows: {classified_count:,}")

    # Show distribution by mechanism
    mech_dist = con.execute("""
        SELECT mechanism, COUNT(*) as cnt
        FROM classified_advisories
        GROUP BY mechanism
        ORDER BY cnt DESC
    """).df()
    print("\n  Advisory distribution by mechanism:")
    print(mech_dist.to_string(index=False))

    # Aggregate to test level with decay-weighted contributions
    print("\n[Step 8] Aggregating features to test level...")

    # Compute decay-weighted contributions per mechanism
    agg_df = con.execute("""
        SELECT
            target_test_id as test_id,
            mechanism,
            SUM(weight * (1 + LN(1)) * POWER(decay_rate, GREATEST(0, LEAST(age_years, 20)))) as index_value,
            MAX(severity) as max_severity,
            COUNT(*) as adv_count
        FROM classified_advisories
        GROUP BY target_test_id, mechanism
    """).df()
    print(f"  Aggregated to {len(agg_df):,} (test, mechanism) pairs")

    # Pivot to wide format
    print("\n[Step 9] Pivoting to wide format...")

    # Get all target test_ids
    all_test_ids = con.execute("SELECT DISTINCT test_id FROM target_tests").df()['test_id'].tolist()

    # Pivot mechanism indices
    pivot_df = agg_df.pivot(index='test_id', columns='mechanism', values='index_value').reset_index()
    pivot_df = pivot_df.fillna(0)

    # Rename columns
    rename_map = {
        'corrosion': 'text_corrosion_index',
        'wear': 'text_wear_index',
        'leak': 'text_leak_index',
        'damage': 'text_damage_index'
    }
    for old_name, new_name in rename_map.items():
        if old_name in pivot_df.columns:
            pivot_df = pivot_df.rename(columns={old_name: new_name})
        else:
            pivot_df[new_name] = 0.0

    # Ensure all mechanism columns exist
    for col in ['text_corrosion_index', 'text_wear_index', 'text_leak_index', 'text_damage_index']:
        if col not in pivot_df.columns:
            pivot_df[col] = 0.0

    # Get max severity per test
    severity_df = agg_df.groupby('test_id').agg({
        'max_severity': 'max',
        'adv_count': 'sum'
    }).reset_index()
    severity_df = severity_df.rename(columns={'max_severity': 'max_severity_score'})

    # Merge
    result_df = pivot_df.merge(severity_df[['test_id', 'max_severity_score']], on='test_id', how='left')

    # Add missing tests (no advisory history)
    result_df = pd.DataFrame({'test_id': all_test_ids}).merge(result_df, on='test_id', how='left')

    # Fill defaults
    for col in ['text_corrosion_index', 'text_wear_index', 'text_leak_index', 'text_damage_index']:
        result_df[col] = result_df[col].fillna(0.0)
    result_df['max_severity_score'] = result_df['max_severity_score'].fillna(0).astype(int)

    # Compute has_advisory_history and dominant_mechanism
    result_df['has_advisory_history'] = (
        (result_df['text_corrosion_index'] > 0) |
        (result_df['text_wear_index'] > 0) |
        (result_df['text_leak_index'] > 0) |
        (result_df['text_damage_index'] > 0)
    ).astype(int)

    # Dominant mechanism
    def get_dominant_mechanism(row):
        if row['has_advisory_history'] == 0:
            # Check if vehicle has ANY prior tests
            return 'NO_HISTORY'  # Simplified - will refine if needed

        indices = {
            'CORROSION': row['text_corrosion_index'],
            'WEAR': row['text_wear_index'],
            'LEAK': row['text_leak_index'],
            'DAMAGE': row['text_damage_index']
        }

        max_val = max(indices.values())
        if max_val == 0:
            return 'CLEAN'

        sorted_indices = sorted(indices.items(), key=lambda x: -x[1])
        top_mech, top_val = sorted_indices[0]
        second_val = sorted_indices[1][1] if len(sorted_indices) > 1 else 0

        mixed_threshold = 0.8
        if second_val > mixed_threshold * top_val and second_val > 0:
            return 'MIXED'
        return top_mech

    result_df['dominant_mechanism'] = result_df.apply(get_dominant_mechanism, axis=1)

    # ==========================================================================
    # NEW: Binary presence flags (reduce sparsity for tree splits)
    # ==========================================================================
    print("\n[Step 9b] Adding binary presence flags...")
    result_df['has_corrosion_history'] = (result_df['text_corrosion_index'] > 0).astype(int)
    result_df['has_wear_history'] = (result_df['text_wear_index'] > 0).astype(int)
    result_df['has_leak_history'] = (result_df['text_leak_index'] > 0).astype(int)
    result_df['has_damage_history'] = (result_df['text_damage_index'] > 0).astype(int)
    # Note: has_any_mechanism_history == has_advisory_history, so we use has_advisory_history
    result_df['mechanism_count'] = (
        result_df['has_corrosion_history'] +
        result_df['has_wear_history'] +
        result_df['has_leak_history'] +
        result_df['has_damage_history']
    )
    print(f"    has_corrosion_history=1: {result_df['has_corrosion_history'].sum():,} ({100*result_df['has_corrosion_history'].mean():.1f}%)")
    print(f"    has_wear_history=1: {result_df['has_wear_history'].sum():,} ({100*result_df['has_wear_history'].mean():.1f}%)")
    print(f"    has_leak_history=1: {result_df['has_leak_history'].sum():,} ({100*result_df['has_leak_history'].mean():.1f}%)")
    print(f"    has_damage_history=1: {result_df['has_damage_history'].sum():,} ({100*result_df['has_damage_history'].mean():.1f}%)")
    print(f"    mechanism_count distribution: {result_df['mechanism_count'].value_counts().sort_index().to_dict()}")

    # ==========================================================================
    # NEW: Log-transformed indices (compress range while preserving signal)
    # ==========================================================================
    print("\n[Step 9c] Adding log-transformed indices...")
    result_df['text_corrosion_index_log'] = np.log1p(result_df['text_corrosion_index'])
    result_df['text_wear_index_log'] = np.log1p(result_df['text_wear_index'])
    result_df['text_leak_index_log'] = np.log1p(result_df['text_leak_index'])
    result_df['text_damage_index_log'] = np.log1p(result_df['text_damage_index'])
    print(f"    text_corrosion_index_log max: {result_df['text_corrosion_index_log'].max():.4f}")
    print(f"    text_wear_index_log max: {result_df['text_wear_index_log'].max():.4f}")

    # ==========================================================================
    # Severity escalation: Compare current vs prior test max_severity
    # ==========================================================================
    print("\n[Step 9d] Computing severity escalation flag...")

    # Get max_severity per test from classified advisories (for prior tests)
    # First, aggregate severity by prior test (adv_test_id)
    prior_severity_df = con.execute("""
        SELECT
            target_test_id as test_id,
            adv_test_id,
            MAX(severity) as prior_max_severity,
            age_years
        FROM classified_advisories
        GROUP BY target_test_id, adv_test_id, age_years
    """).df()

    if len(prior_severity_df) > 0:
        # Get the most recent prior test for each target test
        # (smallest age_years = most recent prior test)
        most_recent_prior = prior_severity_df.loc[
            prior_severity_df.groupby('test_id')['age_years'].idxmin()
        ][['test_id', 'prior_max_severity']]

        # Get the second most recent prior test (to compare for escalation)
        # Sort by test_id and age_years, then take the second row per group
        prior_severity_df_sorted = prior_severity_df.sort_values(['test_id', 'age_years'])
        second_prior = prior_severity_df_sorted.groupby('test_id').nth(1)
        if len(second_prior) > 0:
            second_prior = second_prior.reset_index()[['test_id', 'prior_max_severity']]
            second_prior = second_prior.rename(columns={'prior_max_severity': 'second_prior_severity'})

            # Merge to compare
            escalation_df = most_recent_prior.merge(second_prior, on='test_id', how='left')
            escalation_df['severity_escalation_flag'] = (
                escalation_df['prior_max_severity'] > escalation_df['second_prior_severity'].fillna(0)
            ).astype(int)

            # Merge back to result
            result_df = result_df.merge(
                escalation_df[['test_id', 'severity_escalation_flag']],
                on='test_id',
                how='left',
                suffixes=('_drop', '')
            )
            if 'severity_escalation_flag_drop' in result_df.columns:
                result_df = result_df.drop(columns=['severity_escalation_flag_drop'])
            result_df['severity_escalation_flag'] = result_df['severity_escalation_flag'].fillna(0).astype(int)
        else:
            result_df['severity_escalation_flag'] = 0
    else:
        result_df['severity_escalation_flag'] = 0

    escalation_count = result_df['severity_escalation_flag'].sum()
    print(f"    severity_escalation_flag=1: {escalation_count:,} ({100*escalation_count/len(result_df):.2f}%)")

    print(f"\n  Total rows: {len(result_df):,}")

    # Reorder columns - include all new features
    cols = [
        'test_id',
        # Original mechanism indices
        'text_corrosion_index', 'text_wear_index', 'text_leak_index', 'text_damage_index',
        # Log-transformed indices
        'text_corrosion_index_log', 'text_wear_index_log', 'text_leak_index_log', 'text_damage_index_log',
        # Binary presence flags
        'has_corrosion_history', 'has_wear_history', 'has_leak_history', 'has_damage_history',
        'mechanism_count',
        # Severity features
        'max_severity_score', 'severity_escalation_flag',
        # Categorical
        'has_advisory_history', 'dominant_mechanism'
    ]
    result_df = result_df[cols]

    # Stats
    print("\n[Step 8] Feature statistics:")
    print(f"  Total rows: {len(result_df):,}")

    for col in ['text_corrosion_index', 'text_wear_index', 'text_leak_index', 'text_damage_index']:
        nonzero = (result_df[col] > 0).sum()
        mean_val = result_df[col].mean()
        print(f"  {col}: nonzero={nonzero:,} ({100*nonzero/len(result_df):.1f}%), mean={mean_val:.4f}")

    print(f"\n  max_severity_score distribution:")
    print(result_df['max_severity_score'].value_counts().sort_index().to_string())

    print(f"\n  dominant_mechanism distribution:")
    for mech, count in result_df['dominant_mechanism'].value_counts().items():
        pct = 100 * count / len(result_df)
        print(f"    {mech}: {count:,} ({pct:.1f}%)")

    # Save
    print(f"\n[Step 9] Saving to {OUTPUT_FILE}...")
    result_df.to_parquet(OUTPUT_FILE, index=False)
    print(f"  Saved {len(result_df):,} rows")

    print("\n" + "=" * 70)
    print(f"COMPLETE: {datetime.now().isoformat()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
