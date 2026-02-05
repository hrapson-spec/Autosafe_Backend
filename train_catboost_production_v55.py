"""
AutoSafe CatBoost Production V55 Training Script
=================================================

V55: Temporal Features (Seasonal Patterns)

Adds temporal features derived from test_date to capture seasonal patterns
in MOT failures (e.g., winter lighting/wiper failures).

CHANGES FROM V52:
- ADDED: Temporal Features (3 new features):
  - test_month: Month of test (1-12, categorical)
  - is_winter_test: Oct-Mar = 1, Apr-Sep = 0 (binary)
  - day_of_week: Day of week (0-6 Mon-Sun, categorical)

Rationale:
    MOT failures have known seasonal patterns:
    - Winter months: Higher lighting, wiper, and visibility failures
    - Monday rush: Potentially more hurried inspections
    - Seasonal effects on corrosion visibility

Target: is_failure (binary classification)

Goal: Exceed 0.75 AUC with temporal signal (V52 baseline: 0.7497)

Usage:
  python train_catboost_production_v55.py

Created: 2026-01-16
"""


import duckdb
import pickle
import json
import sys
from pathlib import Path
from datetime import datetime
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from sklearn.linear_model import LogisticRegression
from catboost import CatBoostClassifier, Pool
import numpy as np
import pandas as pd

from hierarchical_make_adjustment import HierarchicalFeatures, ModelHierarchicalFeatures
from station_priors import StationPriors


# ============================================================================
# Configuration
# ============================================================================

ABLATION_MODE = 'degradation'

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"  # Updated with improved advisory coverage
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_set_with_advisory.parquet"  # Updated with improved advisory coverage
HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
# V30: usage_band_hybrid computed in-query from usage_intensity_band + annualized_mileage fallback
ADV_FEATURES_DEV = PROJECT_ROOT / "adv_features_dev.parquet"
ADV_FEATURES_OOT = PROJECT_ROOT / "adv_features_oot.parquet"

# V12d: Extended cycle_history for OOT
HISTORY_EXTENSION_2024 = Path.home() / "autosafe_work/cycle_history_extension_2024.parquet"

# V13: Time-sliced EB priors
SEGMENT_PRIORS = Path.home() / "autosafe_work/time_sliced_eb_priors_dual.parquet"
MAKE_PRIORS = Path.home() / "autosafe_work/time_sliced_make_priors_dual.parquet"
GLOBAL_PRIORS = Path.home() / "autosafe_work/time_sliced_global_priors_dual.parquet"
EB_HIERARCHICAL = PROJECT_ROOT / "eb_hierarchical_features.parquet"

# V9: Neglect features
NEGLECT_FEATURES_FILE = PROJECT_ROOT / "neglect_features.parquet"

# V12: Behavioral history features
BEHAVIORAL_HISTORY_FEATURES = Path.home() / "autosafe_work/behavioral_history_features_v2.parquet"

# V15: Prior Apathy Features
PRIOR_APATHY_FEATURES = Path.home() / "autosafe_work/prior_apathy_features.parquet"

# V31: PONR Features
PONR_FEATURES = Path.home() / "autosafe_work/ponr_features.parquet"

# V32: Split Advisory/Failure Features
ADVISORY_V4_FEATURES = Path.home() / "autosafe_work/advisory_flag_features_v4.parquet"

# V33: Target Denoising - System failure categories (kept for reference)
SYSTEM_FAILURES_9CAT = PROJECT_ROOT / "system_failures_9cat.parquet"
COMPONENT_LABELS_2024 = PROJECT_ROOT / "component_labels_2024.parquet"

# V34: IMD (Index of Multiple Deprivation) features
IMD_FEATURES = Path.home() / "autosafe_work/imd_by_postcode_area.parquet"

# V36: Comprehensive mileage lookup from all sampled files
VALIDATION_SAMPLES = PROJECT_ROOT / "validation_samples"

# V40: Undercarriage Features - NOT USED in V43
# UNDERCARRIAGE_FEATURES = Path.home() / "autosafe_work/undercarriage_features.parquet"

# V46: Negligence Features from ~/autosafe_work (built from advisory_totals)
NEGLIGENCE_FEATURES = Path.home() / "autosafe_work/negligence_features.parquet"

# V51: Mechanical Decay Features (systemic deterioration)
MECHANICAL_DECAY_FEATURES = Path.home() / "autosafe_work/mechanical_decay_features_v49.parquet"

# V52: Text Mining Features (semantic defect signals)
TEXT_MINING_FEATURES = Path.home() / "autosafe_work/text_mining_features_v52.parquet"

# Output directory
WORK_DIR = Path.home() / "autosafe_work/catboost_production_v55"
WORK_DIR.mkdir(parents=True, exist_ok=True)

MODEL_FILE = WORK_DIR / "model.cbm"
CALIBRATOR_FILE = WORK_DIR / "platt_calibrator.pkl"
SEGMENT_HF_FILE = WORK_DIR / "segment_hierarchical_features.pkl"
MODEL_HF_FILE = WORK_DIR / "model_hierarchical_features.pkl"
COHORT_STATS_FILE = WORK_DIR / "cohort_stats.pkl"
RESULTS_FILE = WORK_DIR / "results.json"

# Hierarchy parameters
K_GLOBAL = 10
K_SEGMENT = 100

# V29: Model-specific K overrides for high-volume underperformers
# Lower K = less shrinkage = more model-specific signal
K_MODEL_DEFAULT = 20
K_MODEL_OVERRIDES = {
    'VAUXHALL CORSA': 50,      # 1M+ vehicles, trust model-specific rate
    'FORD TRANSIT': 50,        # 738k vehicles
    'BMW 3 SERIES': 35,        # 560k vehicles
    'RENAULT CLIO': 35,        # 459k vehicles
    'VAUXHALL VIVARO': 50,     # Also high fail variance
    'VAUXHALL INSIGNIA': 35,   # Lowest AUC in top 40
    'MERCEDES-BENZ A': 35,     # Lowest AUC overall (0.587)
}

# V17: Deeper trees + more iterations
PARAMS = {
    'iterations': 2000,
    'learning_rate': 0.02,
    'depth': 6,
    'l2_leaf_reg': 4,
    'border_count': 128,
    'random_strength': 1.0,
    'bagging_temperature': 0.5,
    'eval_metric': 'AUC',
}

# V12d Feature set (from best baseline)
V12D_FEATURE_COLS = [
    # Categorical
    'prev_cycle_outcome_band', 'gap_band', 'make', 'advisory_trend',
    # Numeric
    'test_mileage', 'prev_count_advisory', 'days_since_last_test',
    'prior_fail_rate_smoothed', 'n_prior_tests',
    # Hierarchical
    'make_fail_rate_smoothed', 'segment_fail_rate_smoothed', 'model_fail_rate_smoothed',
    # V4 features
    'days_late', 'prev_adv_brakes', 'prev_adv_suspension',
    'prev_adv_steering', 'prev_adv_tyres',
    # 'annualized_mileage',  # V47: REMOVED - redundant with annualized_mileage_v2
    # V8: Cohort Residuals
    'mileage_cohort_ratio', 'advisory_cohort_delta', 'days_since_pass_ratio',
    # V16: Mileage percentile - V47: REMOVED (ablation showed +0.06pp AUC without it)
    # 'mileage_percentile_for_age',
]

# V13: Hierarchical EB features - V48: REMOVED (unclear provenance, replaced by eb_unified_prior)
# These were from eb_hierarchical_features.parquet which has unclear temporal boundaries
EB_FEATURE_COLS = []  # V48: All removed, replaced by V48_UNIFIED_PRIOR_COLS

# V48 NEW: Unified Hierarchical Prior (replaces scattered EB features)
# Single feature from 5-level hierarchy: Global -> PT -> PT×Age -> Make×Age -> Model×Age
V48_FEATURES_DIR = Path.home() / "autosafe_work/v48_features"
V48_UNIFIED_PRIOR_COLS = [
    'eb_unified_prior',  # Single unified feature (OOF-encoded for DEV, frozen for OOT)
]

# V51 NEW: Mechanical Decay Features (systemic deterioration)
# Captures "about to fail" signals from brake/suspension/structure/steering advisories
V51_MECHANICAL_DECAY_COLS = [
    'mech_decay_brake',             # Brake system sub-index
    'mech_decay_suspension',        # Suspension system sub-index
    'mech_decay_structure',         # Structure system sub-index
    'mech_decay_steering',          # Steering system sub-index
    'mech_decay_index',             # Composite (max-dominated)
    'mech_decay_index_normalized',  # Age-normalized composite
    'mech_risk_driver',             # Categorical: dominant system (BRAKE/SUSP/STRUCT/STEER/MULTI_CRITICAL/CLEAN/NEW_VEHICLE)
]

# V52 NEW: Text Mining Features (semantic defect signals)
# Extracts mechanism-specific signals from advisory text descriptions
V52_TEXT_MINING_COLS = [
    # Original mechanism indices
    'text_corrosion_index',           # Decay-weighted corrosion advisory history
    'text_wear_index',                # Decay-weighted wear advisory history
    'text_leak_index',                # Decay-weighted leak/fluid advisory history
    'text_damage_index',              # Decay-weighted damage/fracture advisory history
    # Log-transformed indices (compressed range)
    'text_corrosion_index_log',       # log(1 + corrosion_index)
    'text_wear_index_log',            # log(1 + wear_index)
    'text_leak_index_log',            # log(1 + leak_index)
    'text_damage_index_log',          # log(1 + damage_index)
    # Binary presence flags (reduce sparsity for tree splits)
    'has_corrosion_history',          # Binary: any corrosion advisory
    'has_wear_history',               # Binary: any wear advisory
    'has_leak_history',               # Binary: any leak advisory
    'has_damage_history',             # Binary: any damage advisory
    'mechanism_count',                # Count of distinct mechanisms (0-4)
    # Severity features
    'max_severity_score',             # Highest severity seen (1-4 scale)
    'severity_escalation_flag',       # Did severity increase between tests?
    # Categorical
    'has_advisory_history',           # Binary: any advisory history (same as has_any_mechanism)
    'dominant_mechanism',             # CORROSION/WEAR/LEAK/DAMAGE/MIXED/CLEAN/NO_HISTORY
]

# V55 NEW: Temporal Features (seasonal patterns)
# Captures seasonal patterns in MOT failures
V55_TEMPORAL_COLS = [
    'test_month',           # 1-12 (categorical)
    'is_winter_test',       # Oct-Mar = 1 (binary)
    'day_of_week',          # 0-6 Mon-Sun (categorical)
]

# V14: Station features
STATION_FEATURE_COLS = [
    'station_fail_rate_smoothed',
    'station_x_prev_outcome_fail_rate',
]

# V15: Prior Apathy Features - REMOVED in V37 Lean (0.024% importance)
APATHY_FEATURE_COLS = []  # was: has_prior_apathy, prior_apathy_rate

# V15: Co-Occurrence Features - SIMPLIFIED in V37 Lean (keep count only)
COOCCUR_FEATURE_COLS = [
    # REMOVED: individual co_occur_* pairs (<0.02% each)
    'multi_system_advisory_count',  # Keep: 1.05% importance
    # REMOVED: has_multi_system_advisory (0.08%) - redundant with count
]

# V16: Cumulative Degradation Features
DEGRADATION_FEATURE_COLS = [
    'n_prior_fails',
    'fails_last_365d',
    'fails_last_730d',
    'fail_rate_trend',
    'recent_fail_intensity',
]

# V27: MDPS
V27_FEATURE_COLS = [
    'mdps_score',
]

# V29: Model-Specific Cohort Features - SIMPLIFIED in V37 Lean
V29_FEATURE_COLS = [
    # REMOVED: vehicle_cohort (0.11%) - captured by other features
    'front_end_advisory_intensity', # Numeric: steering + suspension + tyres (0.20%)
    'brake_system_stress',          # Numeric: brakes advisory count + age factor (0.22%)
    'commercial_wear_proxy',        # Numeric: age-based corrosion risk (1.27%)
]

# V30 NEW: Usage Intensity Band (Hybrid - cycle-to-cycle + annualized fallback)
V30_FEATURE_COLS = [
    'usage_band_hybrid',           # Categorical: 4.63% importance - KEEP
]

# V31: PONR Features - REMOVED in V37 Lean (0.47% total)
V31_FEATURE_COLS = []  # was: ponr_risk_score, has_ponr_pattern

# V32 NEW: Split Advisory vs Failure Features
# Advisory = degradation signal (risk increases over time)
# Failure = repair signal (risk resets after failure)
V32_ADVISORY_COLS = [
    # Brakes
    'has_prior_advisory_brakes',
    # 'miles_since_last_advisory_brakes',  # REMOVED: 1.4% coverage
    'tests_since_last_advisory_brakes',
    'advisory_in_last_1_brakes',
    'advisory_in_last_2_brakes',
    'advisory_streak_len_brakes',
    # Tyres
    'has_prior_advisory_tyres',
    'miles_since_last_advisory_tyres',
    'tests_since_last_advisory_tyres',
    'advisory_in_last_1_tyres',
    'advisory_in_last_2_tyres',
    'advisory_streak_len_tyres',
    # Suspension
    'has_prior_advisory_suspension',
    'miles_since_last_advisory_suspension',
    'tests_since_last_advisory_suspension',
    'advisory_in_last_1_suspension',
    'advisory_in_last_2_suspension',
    'advisory_streak_len_suspension',
]

V32_FAILURE_COLS = [
    # Brakes
    'has_prior_failure_brakes',
    'has_ever_failed_brakes',
    'failure_streak_len_brakes',
    'tests_since_last_failure_brakes',
    # Tyres
    'has_prior_failure_tyres',
    'has_ever_failed_tyres',
    'failure_streak_len_tyres',
    'tests_since_last_failure_tyres',
    # Suspension
    'has_prior_failure_suspension',
    'has_ever_failed_suspension',
    'failure_streak_len_suspension',
    'tests_since_last_failure_suspension',
]

# History controls from V4 (not features, just for reference)
# 'history_tests_observed', 'history_years_observed' - available but not used as features

V32_FEATURE_COLS = V32_ADVISORY_COLS + V32_FAILURE_COLS

# V33: Neglect Scores - RE-ENABLED with optimized weights (+2.21pp AUC)
V33_NEGLECT_COLS = [
    'neglect_score_brakes',
    'neglect_score_tyres',
    'neglect_score_suspension',
]

V33_FEATURE_COLS = V33_NEGLECT_COLS

# V34: External Context Features - SIMPLIFIED in V37 Lean
V34_FEATURE_COLS = [
    'station_strictness_bias',    # 1.16% importance - KEEP
    # REMOVED: area_deprivation_decile (0.02%) - low coverage (31-56%)
]

# V36 NEW: Mileage Block (Trusted Spine Lineage)
# V47: Cleaned - removed redundant features, added explicit indicators
V36_MILEAGE_COLS = [
    # 'miles_since_last_test',      # V47: REMOVED - replaced by has_prev_mileage indicator
    'annualized_mileage_v2',        # Validated annualized from spine (~10-15%)
    'mileage_anomaly_flag',         # Anomaly indicator
    # 'mileage_source_mismatch',    # V47: REMOVED - low value (0.5% flag rate)
]

# V47 NEW: Explicit missingness indicators
V47_MILEAGE_INDICATORS = [
    'has_prev_mileage',             # Binary: 1 if prior mileage found (was miles_since_last_test > 0)
    'mileage_plausible_flag',       # Binary: 1 if mileage is plausible (inverted anomaly flag)
]

# V12: Behavioral features (for full mode)
BEHAVIORAL_FEATURE_COLS = [
    'deferral_propensity_last5',
    'behavioral_risk_score_last5',
    'has_prior_deferral_history',
    'prior_deferral_count_last5',
    'has_prior_pathway_history',
]

# V43: Local Corrosion Index (Geographic Environmental Risk)
# Captures invisible salt/humidity/coastal corrosion patterns by postcode area
V43_GEO_COLS = [
    'local_corrosion_index',   # Area failure rate (computed leakage-free on train)
    'local_corrosion_delta',   # Deviation from global average
]

# V44 NEW: High-Risk Model + Suspension Risk Profile
V44_MODEL_RISK_COLS = [
    'high_risk_model_flag',       # Binary: Top 20 failing models
    'suspension_risk_profile',    # Model-level suspension failure rate (target encoded)
]

# V44.1 NEW: Model-Age Interaction (Shipped V45 Feature)
# Pre-computed from v45_features/model_age_hierarchical.pkl
V45_MODEL_AGE_COLS = [
    'model_age_fail_rate_eb',     # Model+Age hierarchical EB rate (13.4% importance)
    'make_age_fail_rate_eb',      # Make+Age hierarchical EB rate (0.4% importance)
]

# V45 features directory
V45_FEATURES_DIR = Path.home() / "autosafe_work/v45_features"

# V46 NEW: Negligence Features from advisory_totals
V46_NEGLIGENCE_COLS = [
    'historic_negligence_ratio_smoothed',  # EB-smoothed negligence ratio
    'negligence_band',                      # Categorical: clean/low/high/chronic
    'raw_behavioral_count',                 # Raw count of behavioral advisories
]

# V55: Full feature set (V52 + temporal features)
# Changes from V52:
#   +3 added (test_month, is_winter_test, day_of_week)
FEATURE_COLS = (V12D_FEATURE_COLS + EB_FEATURE_COLS + STATION_FEATURE_COLS +
                APATHY_FEATURE_COLS + COOCCUR_FEATURE_COLS + DEGRADATION_FEATURE_COLS +
                V27_FEATURE_COLS + V29_FEATURE_COLS + V30_FEATURE_COLS + V31_FEATURE_COLS +
                V32_FEATURE_COLS + V33_FEATURE_COLS + V34_FEATURE_COLS + V36_MILEAGE_COLS +
                V47_MILEAGE_INDICATORS +
                V43_GEO_COLS + V44_MODEL_RISK_COLS + V45_MODEL_AGE_COLS + V46_NEGLIGENCE_COLS +
                V48_UNIFIED_PRIOR_COLS + V51_MECHANICAL_DECAY_COLS + V52_TEXT_MINING_COLS +
                V55_TEMPORAL_COLS)

print(f"V55 mode: {ABLATION_MODE} ({len(FEATURE_COLS)} features)")

# V55: Categorical features (V52 + temporal categoricals)
CAT_FEATURES = [
    'prev_cycle_outcome_band', 'gap_band', 'make', 'advisory_trend',
    'usage_band_hybrid',      # V30 - 4.63% importance
    'negligence_band',        # V46 - new categorical
    'mech_risk_driver',       # V51 - dominant decay system
    'dominant_mechanism',     # V52 - dominant text mining mechanism
    'test_month',             # V55 - month of test (1-12)
    'day_of_week',            # V55 - day of week (0-6)
]


# ============================================================================
# V29: Vehicle Cohort Assignment
# ============================================================================

# Commercial vehicle keywords (from V25 analysis)
COMMERCIAL_KEYWORDS = [
    'TRANSIT', 'SPRINTER', 'BOXER', 'TRAFIC', 'VIVARO',
    'RELAY', 'MASTER', 'CRAFTER', 'DUCATO', 'DAILY', 'DISPATCH',
    'TRANSPORTER', 'CADDY', 'BERLINGO', 'PARTNER', 'COMBO',
    'PROACE', 'TALENTO', 'MOVANO', 'NV200', 'NV300', 'NV400',
]

# Euro premium makes (from V22)
EURO_PREMIUM_MAKES = [
    'BMW', 'MERCEDES-BENZ', 'MERCEDES', 'AUDI', 'PORSCHE',
    'JAGUAR', 'VOLVO', 'LAND ROVER', 'LEXUS',
]

# Small hatchback models with front-end vulnerability
SMALL_HATCH_MODELS = [
    'CORSA', 'ADAM', 'VIVA',  # Vauxhall small hatches
    'KA', 'KA+',              # Ford small
    '500', 'PANDA',           # Fiat small
    'AYGO', 'YARIS',          # Toyota small
    'I10', 'I20',             # Hyundai small
    'PICANTO', 'RIO',         # Kia small
    'MICRA', 'NOTE',          # Nissan small
    'SWIFT', 'IGNIS',         # Suzuki small
    'TWINGO', 'ZOE',          # Renault small
]

# French compact models
FRENCH_MODELS = ['CLIO', 'MEGANE', 'CAPTUR', 'SCENIC', 'KADJAR',  # Renault
                 '207', '208', '308', '2008', '3008',              # Peugeot
                 'C3', 'C4', 'DS3', 'DS4',                         # Citroen
]


def assign_vehicle_cohort(model_id: str, make: str) -> str:
    """
    Assign vehicle to a cohort based on model and make.

    This is a CATEGORICAL feature - the tree can split on it independently
    and learn different thresholds for each cohort. This avoids the
    multiplicative gating problem from V25.

    Args:
        model_id: Full model identifier (e.g., "VAUXHALL CORSA")
        make: Vehicle make (e.g., "VAUXHALL")

    Returns:
        Cohort string: one of 'commercial_van', 'small_hatch', 'euro_premium',
                       'french_compact', 'standard'
    """
    model_id = str(model_id).upper() if pd.notna(model_id) else ''
    make = str(make).upper() if pd.notna(make) else ''

    # Priority 1: Commercial vehicles (highest failure variance)
    for keyword in COMMERCIAL_KEYWORDS:
        if keyword in model_id:
            return 'commercial_van'

    # Priority 2: Small hatchbacks (front-end vulnerability)
    for model in SMALL_HATCH_MODELS:
        if model in model_id:
            return 'small_hatch'

    # Priority 3: Euro premium (different maintenance patterns)
    if make in EURO_PREMIUM_MAKES:
        return 'euro_premium'

    # Priority 4: French compacts (brake system patterns)
    for model in FRENCH_MODELS:
        if model in model_id:
            return 'french_compact'

    # Default
    return 'standard'


def add_v29_cohort_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add V29 model-specific cohort features.

    Key insight from V25 failure analysis:
    - V25 used multiplicative gating: is_corsa * (steering + suspension + tyres)
    - When is_corsa=0, feature becomes 0 for 99% of vehicles
    - Tree couldn't learn anything from mostly-zero features

    V29 solution:
    1. vehicle_cohort: Categorical feature tree can split on independently
    2. Universal failure signature features: Non-zero for ALL vehicles
       - Tree learns different thresholds per cohort via interaction

    Args:
        df: DataFrame with model_id, make, and advisory columns

    Returns:
        DataFrame with new features added
    """
    print("  Adding V29 cohort features...")
    df = df.copy()

    # ========================================================================
    # Feature 1: Vehicle Cohort (Categorical)
    # ========================================================================
    df['vehicle_cohort'] = df.apply(
        lambda row: assign_vehicle_cohort(row.get('model_id', ''), row.get('make', '')),
        axis=1
    )

    # Stats
    cohort_counts = df['vehicle_cohort'].value_counts()
    print(f"    Cohort distribution:")
    for cohort, count in cohort_counts.items():
        pct = count / len(df) * 100
        print(f"      {cohort}: {count:,} ({pct:.1f}%)")

    # ========================================================================
    # Feature 2: Front-End Advisory Intensity (Universal - NOT gated)
    # ========================================================================
    # This applies to ALL vehicles, not just small hatches
    # Tree will learn that small_hatch cohort has different threshold
    steering = df['prev_adv_steering'].fillna(0)
    suspension = df['prev_adv_suspension'].fillna(0)
    tyres = df['prev_adv_tyres'].fillna(0)

    # Simple sum - tree will learn cohort-specific thresholds
    df['front_end_advisory_intensity'] = steering + suspension + tyres

    pct_nonzero = (df['front_end_advisory_intensity'] > 0).mean() * 100
    mean_intensity = df['front_end_advisory_intensity'].mean()
    print(f"    front_end_advisory_intensity > 0: {pct_nonzero:.1f}%")
    print(f"    front_end_advisory_intensity mean: {mean_intensity:.2f}")

    # ========================================================================
    # Feature 3: Brake System Stress (Universal - NOT gated)
    # ========================================================================
    # Combines brake advisories with age factor (corrosion risk)
    # Transit/Sprinter fail due to age+load-driven corrosion, not mileage
    brakes = df['prev_adv_brakes'].fillna(0)

    # Age proxy from n_prior_tests (more tests = older vehicle)
    n_prior = df['n_prior_tests'].fillna(0)
    age_factor = np.log1p(n_prior) / 3.0  # Normalize: ~0.3 at 1 test, ~0.7 at 5 tests

    # Brake stress = brake advisories + age-based corrosion risk
    df['brake_system_stress'] = brakes + age_factor

    pct_nonzero = (df['brake_system_stress'] > 0).mean() * 100
    mean_stress = df['brake_system_stress'].mean()
    print(f"    brake_system_stress > 0: {pct_nonzero:.1f}%")
    print(f"    brake_system_stress mean: {mean_stress:.2f}")

    # ========================================================================
    # Feature 4: Commercial Wear Proxy (Universal - NOT gated)
    # ========================================================================
    # Age-based corrosion/wear risk that's higher for commercial use patterns
    # Non-zero for all vehicles, but commercial cohort will have different learned threshold

    # Use days_since_last_test as usage intensity proxy
    days_since = df['days_since_last_test'].fillna(365)

    # Mileage intensity
    annual_miles = df['annualized_mileage'].fillna(8000)
    high_mileage_factor = np.log1p(annual_miles / 10000)  # Normalize around 10k/year

    # Time overdue factor (commercial vehicles often defer MOT)
    days_overdue = np.maximum(0, days_since - 365) / 365.0

    # Combined proxy
    df['commercial_wear_proxy'] = high_mileage_factor + days_overdue + age_factor

    pct_nonzero = (df['commercial_wear_proxy'] > 0).mean() * 100
    mean_proxy = df['commercial_wear_proxy'].mean()
    print(f"    commercial_wear_proxy > 0: {pct_nonzero:.1f}%")
    print(f"    commercial_wear_proxy mean: {mean_proxy:.2f}")

    return df


# ============================================================================
# V43: Local Corrosion Index (Geographic Environmental Risk)
# Captures invisible salt/humidity/coastal corrosion by postcode area
# ============================================================================

# V43: Area corrosion rates (computed on 2016 DEV set - will be overwritten at runtime)
# This is a global that gets populated during fit on training data
V43_AREA_CORROSION_RATES = {}
V43_GLOBAL_FAIL_RATE = 0.216  # Global average (default fallback)


def fit_v43_corrosion_index(train_df: pd.DataFrame) -> dict:
    """
    Fit local corrosion index on training data ONLY (leakage-free).
    
    Computes area-level failure rates to capture environmental/geographic risk.
    Coastal and Scottish areas (DD, KY, TR, PL) have 25-30% failure rates.
    London areas (EN, SE, RM) have 14-17% failure rates.
    
    Must be called ONLY on training data to avoid leakage!
    
    Args:
        train_df: Training DataFrame with postcode_area and target columns
        
    Returns:
        Dictionary mapping postcode_area -> failure_rate
    """
    global V43_AREA_CORROSION_RATES, V43_GLOBAL_FAIL_RATE
    
    print("  Fitting V43 local corrosion index (training only)...")
    
    # Global baseline
    V43_GLOBAL_FAIL_RATE = train_df['target'].mean()
    print(f"    Global failure rate: {V43_GLOBAL_FAIL_RATE:.4f}")
    
    # Compute per-area stats (minimum 100 tests for stability)
    area_stats = train_df.groupby('postcode_area').agg({
        'target': ['sum', 'count']
    }).reset_index()
    area_stats.columns = ['postcode_area', 'n_fails', 'n_tests']
    area_stats['fail_rate'] = area_stats['n_fails'] / area_stats['n_tests']
    
    # Filter to areas with sufficient data
    reliable_areas = area_stats[area_stats['n_tests'] >= 100].copy()
    print(f"    Areas with >= 100 tests: {len(reliable_areas)}")
    
    # Apply Bayesian shrinkage (pseudo-count k=20)
    K = 20
    reliable_areas['smoothed_rate'] = (
        (reliable_areas['n_fails'] + K * V43_GLOBAL_FAIL_RATE) /
        (reliable_areas['n_tests'] + K)
    )
    
    # Build lookup dict
    V43_AREA_CORROSION_RATES = dict(zip(
        reliable_areas['postcode_area'],
        reliable_areas['smoothed_rate']
    ))
    
    # Report top/bottom areas
    top_5 = reliable_areas.nlargest(5, 'smoothed_rate')
    bottom_5 = reliable_areas.nsmallest(5, 'smoothed_rate')
    
    print("    Top 5 high-risk areas (coastal/Scottish):")
    for _, row in top_5.iterrows():
        print(f"      {row['postcode_area']}: {row['smoothed_rate']:.3f} (n={int(row['n_tests'])})")
    
    print("    Bottom 5 low-risk areas (urban/London):")
    for _, row in bottom_5.iterrows():
        print(f"      {row['postcode_area']}: {row['smoothed_rate']:.3f} (n={int(row['n_tests'])})")
    
    return V43_AREA_CORROSION_RATES


def add_v43_corrosion_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add V43 local corrosion features to dataframe.
    
    Must call fit_v43_corrosion_index() on training data first!
    
    Features:
    - local_corrosion_index: Area failure rate (smoothed)
    - local_corrosion_delta: Deviation from global average
    
    Args:
        df: DataFrame with postcode_area column
        
    Returns:
        DataFrame with V43 features added
    """
    print("  Adding V43 local corrosion features...")
    
    # Map postcode_area to corrosion index
    df['local_corrosion_index'] = df['postcode_area'].map(V43_AREA_CORROSION_RATES)
    
    # Fill unknown areas with global average
    n_unknown = df['local_corrosion_index'].isna().sum()
    df['local_corrosion_index'] = df['local_corrosion_index'].fillna(V43_GLOBAL_FAIL_RATE)
    
    # Compute delta from global average
    df['local_corrosion_delta'] = df['local_corrosion_index'] - V43_GLOBAL_FAIL_RATE
    
    # Stats
    coverage = (df['postcode_area'].isin(V43_AREA_CORROSION_RATES)).mean() * 100
    mean_index = df['local_corrosion_index'].mean()
    delta_range = (df['local_corrosion_delta'].min(), df['local_corrosion_delta'].max())
    
    print(f"    Coverage: {coverage:.1f}% (unknown areas: {n_unknown:,})")
    print(f"    Mean local_corrosion_index: {mean_index:.4f}")
    print(f"    Delta range: [{delta_range[0]:.4f}, {delta_range[1]:.4f}]")
    
    return df


# ============================================================================
# V44: High-Risk Model Flag + Suspension Risk Profile
# Captures model-specific failure patterns the tree might miss
# ============================================================================

# V44: Top 20 failing models (fail rate > 28%, from 2019 data analysis)
HIGH_RISK_MODELS = [
    'ROVER 75',           # 31.8%
    'RENAULT LAGUNA',     # 31.4%
    'CITROEN XSARA',      # 31.3%
    'PEUGEOT 307 SW',     # 31.2%
    'VAUXHALL VECTRA',    # 31.0%
    'PEUGEOT 206',        # 30.5%
    'CHEVROLET MATIZ',    # 30.5%
    'PEUGEOT 307',        # 30.3%
    'VAUXHALL TIGRA',     # 30.2%
    'RENAULT MODUS',      # 30.1%
    'NISSAN PRIMASTAR',   # 29.9%
    'CITROEN C2',         # 29.9%
    'RENAULT GRAND SCENIC', # 29.6%
    'FORD FOCUS C-MAX',   # 29.5%
    'VOLKSWAGEN BORA',    # 29.3%
    'JAGUAR X TYPE',      # 29.3%
    'FIAT SCUDO',         # 29.1%
    'HYUNDAI COUPE',      # 29.1%
    'MITSUBISHI L200 DOUBLE CAB', # 29.0%
    'MAZDA 5',            # 29.0%
]

# V44: Suspension failure rates by model (computed at runtime)
V44_SUSP_FAIL_RATES = {}
V44_GLOBAL_SUSP_RATE = 0.087  # Default: 8.67% suspension failures


def fit_v44_suspension_profile(train_df: pd.DataFrame) -> dict:
    """
    Fit suspension risk profile on training data ONLY (leakage-free).
    
    Computes model-level suspension failure rates using Bayesian smoothing.
    Uses has_prior_failure_suspension as the proxy (pre-test information, not target).
    
    Args:
        train_df: Training DataFrame with model_id column
        
    Returns:
        Dictionary mapping model_id -> suspension_failure_rate
    """
    global V44_SUSP_FAIL_RATES, V44_GLOBAL_SUSP_RATE
    
    print("  Fitting V44 suspension risk profile (training only)...")
    
    # Use prev_adv_suspension as proxy (pre-test, not leakage)
    if 'prev_adv_suspension' in train_df.columns:
        train_df = train_df.copy()
        train_df['susp_flag'] = (train_df['prev_adv_suspension'] > 0).astype(int)
        susp_col = 'susp_flag'
    else:
        print("    WARNING: No suspension column found, using defaults")
        return {}
    
    # Global baseline
    V44_GLOBAL_SUSP_RATE = train_df[susp_col].mean()
    print(f"    Global suspension advisory rate: {V44_GLOBAL_SUSP_RATE:.4f}")
    
    # Compute per-model stats (minimum 50 tests for stability)
    model_stats = train_df.groupby('model_id').agg({
        susp_col: ['sum', 'count']
    }).reset_index()
    model_stats.columns = ['model_id', 'n_susp', 'n_tests']
    model_stats['susp_rate'] = model_stats['n_susp'] / model_stats['n_tests']
    
    # Filter to models with sufficient data
    reliable_models = model_stats[model_stats['n_tests'] >= 50].copy()
    print(f"    Models with >= 50 tests: {len(reliable_models)}")
    
    # Apply Bayesian shrinkage (pseudo-count k=10)
    K = 10
    reliable_models['smoothed_rate'] = (
        (reliable_models['n_susp'] + K * V44_GLOBAL_SUSP_RATE) /
        (reliable_models['n_tests'] + K)
    )
    
    # Build lookup dict
    V44_SUSP_FAIL_RATES = dict(zip(
        reliable_models['model_id'],
        reliable_models['smoothed_rate']
    ))
    
    # Report top/bottom models
    top_5 = reliable_models.nlargest(5, 'smoothed_rate')
    bottom_5 = reliable_models.nsmallest(5, 'smoothed_rate')
    
    print("    Top 5 suspension-prone models:")
    for _, row in top_5.iterrows():
        print(f"      {row['model_id']}: {row['smoothed_rate']:.3f} (n={int(row['n_tests'])})")
    
    print("    Bottom 5 suspension-safe models:")
    for _, row in bottom_5.iterrows():
        print(f"      {row['model_id']}: {row['smoothed_rate']:.3f} (n={int(row['n_tests'])})")
    
    return V44_SUSP_FAIL_RATES


def add_v44_model_risk_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add V44 model risk features to dataframe.
    
    Must call fit_v44_suspension_profile() on training data first!
    
    Features:
    - high_risk_model_flag: Binary flag for top-20 failing models
    - suspension_risk_profile: Model-level suspension rate (smoothed)
    
    Args:
        df: DataFrame with model_id column
        
    Returns:
        DataFrame with V44 features added
    """
    print("  Adding V44 model risk features...")
    
    # Feature 1: High-risk model flag
    high_risk_set = {m.upper() for m in HIGH_RISK_MODELS}
    df['high_risk_model_flag'] = df['model_id'].apply(
        lambda x: 1 if str(x).upper() in high_risk_set else 0
    )
    
    high_risk_pct = df['high_risk_model_flag'].mean() * 100
    print(f"    high_risk_model_flag=1: {high_risk_pct:.1f}%")
    
    # Feature 2: Suspension risk profile
    df['suspension_risk_profile'] = df['model_id'].map(V44_SUSP_FAIL_RATES)
    
    # Fill unknown models with global average
    n_unknown = df['suspension_risk_profile'].isna().sum()
    df['suspension_risk_profile'] = df['suspension_risk_profile'].fillna(V44_GLOBAL_SUSP_RATE)
    
    # Stats
    coverage = (df['model_id'].isin(V44_SUSP_FAIL_RATES)).mean() * 100
    mean_profile = df['suspension_risk_profile'].mean()
    
    print(f"    suspension_risk_profile coverage: {coverage:.1f}% (unknown: {n_unknown:,})")
    print(f"    suspension_risk_profile mean: {mean_profile:.4f}")

    return df


# ============================================================================
# V44.1: Model-Age Interaction Feature (Shipped V45 Feature)
# ============================================================================

def add_v45_model_age_features(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """
    Add V45 model-age interaction features from pre-computed parquet files.

    Features are pre-computed by v45_features/build_model_age_interaction.py
    using hierarchical EB smoothing: Global -> Make+Age -> Model+Age

    Args:
        df: DataFrame with test_id column
        dataset: 'DEV' or 'OOT' to load correct feature file

    Returns:
        DataFrame with model_age features added
    """
    print("  Adding V44.1 model-age interaction features...")

    suffix = 'dev' if dataset == 'DEV' else 'oot'
    feature_file = V45_FEATURES_DIR / f"model_age_features_{suffix}.parquet"

    if not feature_file.exists():
        print(f"    WARNING: {feature_file} not found, skipping")
        df['model_age_fail_rate_eb'] = 0.25  # Global fallback
        df['make_age_fail_rate_eb'] = 0.25   # Global fallback
        return df

    # Load pre-computed features
    model_age_df = pd.read_parquet(feature_file)

    # Merge on test_id
    n_before = len(df)
    df = df.merge(model_age_df, on='test_id', how='left')
    n_after = len(df)

    if n_after != n_before:
        print(f"    WARNING: Row count changed during merge: {n_before:,} -> {n_after:,}")

    # Fill missing values with global average (~0.25)
    n_missing = df['model_age_fail_rate_eb'].isna().sum()
    df['model_age_fail_rate_eb'] = df['model_age_fail_rate_eb'].fillna(0.25)
    df['make_age_fail_rate_eb'] = df['make_age_fail_rate_eb'].fillna(0.25)

    # Stats
    coverage = (1 - n_missing / len(df)) * 100
    print(f"    model_age_fail_rate_eb coverage: {coverage:.1f}%")
    print(f"    model_age_fail_rate_eb range: [{df['model_age_fail_rate_eb'].min():.4f}, {df['model_age_fail_rate_eb'].max():.4f}]")
    print(f"    make_age_fail_rate_eb range: [{df['make_age_fail_rate_eb'].min():.4f}, {df['make_age_fail_rate_eb'].max():.4f}]")

    return df


# ============================================================================
# V46: Negligence Features from ~/autosafe Pipeline
# ============================================================================

def add_v46_negligence_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add V46 negligence features from ~/autosafe batch pipeline.

    Features are vehicle-level, joined via vehicle_id.

    Features:
    - historic_negligence_ratio_smoothed: EB-smoothed negligence ratio
    - negligence_band: Categorical (clean/low/high/chronic)
    - raw_behavioral_count: Count of behavioral advisories

    Args:
        df: DataFrame with vehicle_id column

    Returns:
        DataFrame with V46 negligence features added
    """
    print("  Adding V46 negligence features...")

    if not NEGLIGENCE_FEATURES.exists():
        print(f"    WARNING: {NEGLIGENCE_FEATURES} not found, using defaults")
        df['historic_negligence_ratio_smoothed'] = 0.05  # Global fallback
        df['negligence_band'] = 'unknown'
        df['raw_behavioral_count'] = 0
        return df

    # Load pre-computed negligence features
    neg_df = pd.read_parquet(NEGLIGENCE_FEATURES)

    # Cast vehicle_id to match (handle int64 vs object mismatch)
    # Real data uses int64, synthetic test data uses string VINs
    if df['vehicle_id'].dtype != neg_df['vehicle_id'].dtype:
        print(f"    Converting vehicle_id types: {df['vehicle_id'].dtype} -> {neg_df['vehicle_id'].dtype}")
        try:
            neg_df['vehicle_id'] = neg_df['vehicle_id'].astype(df['vehicle_id'].dtype)
        except (ValueError, TypeError):
            # If conversion fails, no matches possible - use defaults
            print(f"    WARNING: vehicle_id type conversion failed, using defaults")
            df['historic_negligence_ratio_smoothed'] = 0.05
            df['negligence_band'] = 'unknown'
            df['raw_behavioral_count'] = 0
            return df

    # Join on vehicle_id
    n_before = len(df)
    df = df.merge(
        neg_df[['vehicle_id', 'historic_negligence_ratio_smoothed',
                'negligence_band', 'raw_behavioral_count']],
        on='vehicle_id',
        how='left'
    )
    n_after = len(df)

    if n_after != n_before:
        print(f"    WARNING: Row count changed during merge: {n_before:,} -> {n_after:,}")

    # Fill missing with defaults
    n_missing = df['historic_negligence_ratio_smoothed'].isna().sum()
    df['historic_negligence_ratio_smoothed'] = df['historic_negligence_ratio_smoothed'].fillna(0.05)
    df['negligence_band'] = df['negligence_band'].fillna('unknown')
    df['raw_behavioral_count'] = df['raw_behavioral_count'].fillna(0)

    # Stats
    coverage = (1 - n_missing / len(df)) * 100
    print(f"    Coverage: {coverage:.1f}% (missing: {n_missing:,})")
    print(f"    historic_negligence_ratio_smoothed mean: {df['historic_negligence_ratio_smoothed'].mean():.4f}")
    print(f"    negligence_band distribution:")
    for band, count in df['negligence_band'].value_counts().items():
        pct = count / len(df) * 100
        print(f"      {band}: {count:,} ({pct:.1f}%)")

    return df


# ============================================================================
# V48: Unified Hierarchical Prior from Pre-Computed Parquet
# ============================================================================

def add_v48_unified_prior(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """
    Add V48 unified hierarchical prior from pre-computed parquet.

    The unified prior consolidates 5 levels of hierarchy:
    Global -> Powertrain -> Powertrain×Age -> Make×Age -> Model×Age

    For DEV: Uses OOF-encoded values (5-fold cross-fit, no leakage)
    For OOT: Uses frozen encoder fit on all DEV data

    Args:
        df: DataFrame with test_id column
        dataset: 'DEV' or 'OOT' to select correct feature file

    Returns:
        DataFrame with eb_unified_prior added
    """
    print(f"  Adding V48 unified hierarchical prior ({dataset})...")

    # Select feature file based on dataset
    dataset_lower = dataset.lower()
    feature_file = V48_FEATURES_DIR / f"unified_prior_{dataset_lower}.parquet"

    if not feature_file.exists():
        print(f"    WARNING: {feature_file} not found, using global fallback (0.25)")
        df['eb_unified_prior'] = 0.25
        return df

    # Load pre-computed prior
    prior_df = pd.read_parquet(feature_file)

    # Join on test_id
    n_before = len(df)
    df = df.merge(
        prior_df[['test_id', 'eb_unified_prior']],
        on='test_id',
        how='left'
    )
    n_after = len(df)

    if n_after != n_before:
        print(f"    WARNING: Row count changed during merge: {n_before:,} -> {n_after:,}")

    # Fill missing with global fallback (global fail rate ~0.25)
    n_missing = df['eb_unified_prior'].isna().sum()
    df['eb_unified_prior'] = df['eb_unified_prior'].fillna(0.25)

    # Stats
    coverage = (1 - n_missing / len(df)) * 100
    print(f"    Coverage: {coverage:.1f}% (missing: {n_missing:,})")
    print(f"    eb_unified_prior range: [{df['eb_unified_prior'].min():.4f}, {df['eb_unified_prior'].max():.4f}]")
    print(f"    eb_unified_prior mean: {df['eb_unified_prior'].mean():.4f}")

    return df


# ============================================================================
# V51: Mechanical Decay Features (Systemic Deterioration)
# ============================================================================

def add_v51_mechanical_decay_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add V51 mechanical decay features from pre-computed parquet.

    Features capture systemic deterioration signals from MOT advisories:
    - mech_decay_brake: Brake system sub-index (pipes, hoses, discs)
    - mech_decay_suspension: Suspension sub-index (joints, shocks, springs)
    - mech_decay_structure: Structure sub-index (subframe, sills, anchorages)
    - mech_decay_steering: Steering sub-index (rack, track rods, couplings)
    - mech_decay_index: Max-dominated composite index
    - mech_decay_index_normalized: Age-normalized composite
    - mech_risk_driver: Categorical dominant system

    Formula: Sub-Index = Σ(W_tier × (1 + log(count)) × decay^age_years)
             Composite = max(sub_indices) + 0.25 × Σ(other_sub_indices)

    Args:
        df: DataFrame with test_id column

    Returns:
        DataFrame with V51 mechanical decay features added
    """
    print("  Adding V51 mechanical decay features...")

    if not MECHANICAL_DECAY_FEATURES.exists():
        print(f"    WARNING: {MECHANICAL_DECAY_FEATURES} not found, using defaults")
        df['mech_decay_brake'] = 0.0
        df['mech_decay_suspension'] = 0.0
        df['mech_decay_structure'] = 0.0
        df['mech_decay_steering'] = 0.0
        df['mech_decay_index'] = 0.0
        df['mech_decay_index_normalized'] = 0.0
        df['mech_risk_driver'] = 'CLEAN'
        return df

    # Load pre-computed mechanical decay features
    mech_df = pd.read_parquet(MECHANICAL_DECAY_FEATURES)

    # Join on test_id
    n_before = len(df)
    df = df.merge(
        mech_df[['test_id', 'mech_decay_brake', 'mech_decay_suspension',
                 'mech_decay_structure', 'mech_decay_steering',
                 'mech_decay_index', 'mech_decay_index_normalized', 'mech_risk_driver']],
        on='test_id',
        how='left'
    )
    n_after = len(df)

    if n_after != n_before:
        print(f"    WARNING: Row count changed during merge: {n_before:,} -> {n_after:,}")

    # Fill missing with defaults
    n_missing = df['mech_decay_index'].isna().sum()
    df['mech_decay_brake'] = df['mech_decay_brake'].fillna(0.0)
    df['mech_decay_suspension'] = df['mech_decay_suspension'].fillna(0.0)
    df['mech_decay_structure'] = df['mech_decay_structure'].fillna(0.0)
    df['mech_decay_steering'] = df['mech_decay_steering'].fillna(0.0)
    df['mech_decay_index'] = df['mech_decay_index'].fillna(0.0)
    df['mech_decay_index_normalized'] = df['mech_decay_index_normalized'].fillna(0.0)
    df['mech_risk_driver'] = df['mech_risk_driver'].fillna('CLEAN')

    # Stats
    coverage = (1 - n_missing / len(df)) * 100
    print(f"    Coverage: {coverage:.1f}% (missing: {n_missing:,})")
    print(f"    mech_decay_index range: [{df['mech_decay_index'].min():.4f}, {df['mech_decay_index'].max():.4f}]")
    print(f"    mech_decay_index mean: {df['mech_decay_index'].mean():.4f}")
    nonzero_pct = (df['mech_decay_index'] > 0).mean() * 100
    print(f"    Nonzero indices: {nonzero_pct:.1f}%")
    print(f"    mech_risk_driver distribution:")
    for driver, count in df['mech_risk_driver'].value_counts().head(7).items():
        pct = count / len(df) * 100
        print(f"      {driver}: {count:,} ({pct:.1f}%)")

    return df


# ============================================================================
# V52: Text Mining Features (Semantic Defect Signals)
# ============================================================================

def add_v52_text_mining_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add V52 text mining features from pre-computed parquet.

    Features extract semantic defect signals from advisory text descriptions:
    - text_corrosion_index: Decay-weighted corrosion advisory history
    - text_wear_index: Decay-weighted wear advisory history
    - text_leak_index: Decay-weighted leak/fluid advisory history
    - text_damage_index: Decay-weighted damage/fracture advisory history
    - max_severity_score: Highest severity seen (1-4 scale)
    - severity_escalation_flag: Did severity increase between tests?
    - has_advisory_history: Distinguishes NO_HISTORY from CLEAN
    - dominant_mechanism: Categorical dominant mechanism

    Formula: Index = Σ(weight × (1 + log(count)) × decay^age_years)

    Args:
        df: DataFrame with test_id column

    Returns:
        DataFrame with V52 text mining features added
    """
    print("  Adding V52 text mining features...")

    if not TEXT_MINING_FEATURES.exists():
        print(f"    WARNING: {TEXT_MINING_FEATURES} not found, using defaults")
        # Original indices
        df['text_corrosion_index'] = 0.0
        df['text_wear_index'] = 0.0
        df['text_leak_index'] = 0.0
        df['text_damage_index'] = 0.0
        # Log-transformed indices
        df['text_corrosion_index_log'] = 0.0
        df['text_wear_index_log'] = 0.0
        df['text_leak_index_log'] = 0.0
        df['text_damage_index_log'] = 0.0
        # Binary flags
        df['has_corrosion_history'] = 0
        df['has_wear_history'] = 0
        df['has_leak_history'] = 0
        df['has_damage_history'] = 0
        df['mechanism_count'] = 0
        # Severity
        df['max_severity_score'] = 0
        df['severity_escalation_flag'] = 0
        # Categorical
        df['has_advisory_history'] = 0
        df['dominant_mechanism'] = 'NO_HISTORY'
        return df

    # Load pre-computed text mining features
    text_df = pd.read_parquet(TEXT_MINING_FEATURES)

    # All columns to join
    join_cols = [
        'test_id',
        # Original indices
        'text_corrosion_index', 'text_wear_index', 'text_leak_index', 'text_damage_index',
        # Log-transformed indices
        'text_corrosion_index_log', 'text_wear_index_log', 'text_leak_index_log', 'text_damage_index_log',
        # Binary flags
        'has_corrosion_history', 'has_wear_history', 'has_leak_history', 'has_damage_history',
        'mechanism_count',
        # Severity
        'max_severity_score', 'severity_escalation_flag',
        # Categorical
        'has_advisory_history', 'dominant_mechanism'
    ]

    # Join on test_id
    n_before = len(df)
    df = df.merge(text_df[join_cols], on='test_id', how='left')
    n_after = len(df)

    if n_after != n_before:
        print(f"    WARNING: Row count changed during merge: {n_before:,} -> {n_after:,}")

    # Fill missing with defaults
    n_missing = df['text_corrosion_index'].isna().sum()
    # Original indices
    df['text_corrosion_index'] = df['text_corrosion_index'].fillna(0.0)
    df['text_wear_index'] = df['text_wear_index'].fillna(0.0)
    df['text_leak_index'] = df['text_leak_index'].fillna(0.0)
    df['text_damage_index'] = df['text_damage_index'].fillna(0.0)
    # Log-transformed indices
    df['text_corrosion_index_log'] = df['text_corrosion_index_log'].fillna(0.0)
    df['text_wear_index_log'] = df['text_wear_index_log'].fillna(0.0)
    df['text_leak_index_log'] = df['text_leak_index_log'].fillna(0.0)
    df['text_damage_index_log'] = df['text_damage_index_log'].fillna(0.0)
    # Binary flags
    df['has_corrosion_history'] = df['has_corrosion_history'].fillna(0).astype(int)
    df['has_wear_history'] = df['has_wear_history'].fillna(0).astype(int)
    df['has_leak_history'] = df['has_leak_history'].fillna(0).astype(int)
    df['has_damage_history'] = df['has_damage_history'].fillna(0).astype(int)
    df['mechanism_count'] = df['mechanism_count'].fillna(0).astype(int)
    # Severity
    df['max_severity_score'] = df['max_severity_score'].fillna(0).astype(int)
    df['severity_escalation_flag'] = df['severity_escalation_flag'].fillna(0).astype(int)
    # Categorical
    df['has_advisory_history'] = df['has_advisory_history'].fillna(0).astype(int)
    df['dominant_mechanism'] = df['dominant_mechanism'].fillna('NO_HISTORY')

    # Stats
    coverage = (1 - n_missing / len(df)) * 100
    print(f"    Coverage: {coverage:.1f}% (missing: {n_missing:,})")
    print(f"    text_corrosion_index mean: {df['text_corrosion_index'].mean():.4f}")
    print(f"    text_wear_index mean: {df['text_wear_index'].mean():.4f}")
    # Binary flag stats
    print(f"    has_corrosion_history=1: {(df['has_corrosion_history']==1).sum():,} ({100*(df['has_corrosion_history']==1).mean():.1f}%)")
    print(f"    has_wear_history=1: {(df['has_wear_history']==1).sum():,} ({100*(df['has_wear_history']==1).mean():.1f}%)")
    print(f"    mechanism_count distribution: {df['mechanism_count'].value_counts().sort_index().to_dict()}")
    print(f"    severity_escalation_flag=1: {(df['severity_escalation_flag']==1).sum():,} ({100*(df['severity_escalation_flag']==1).mean():.2f}%)")

    print(f"    dominant_mechanism distribution:")
    for mech, count in df['dominant_mechanism'].value_counts().head(7).items():
        pct = count / len(df) * 100
        print(f"      {mech}: {count:,} ({pct:.1f}%)")

    return df


# ============================================================================
# V36: Mileage Block Functions (Trusted Spine Lineage) - DuckDB Native
# Memory target: 8GB MacBook Air - never materialize > cohort size × 3
# ============================================================================

def add_mileage_block_v36(df: pd.DataFrame, conn) -> pd.DataFrame:
    """
    Add V36 mileage features using trusted spine lineage - DuckDB native.
    
    Memory-safe design:
    - Uses predicate pushdown to avoid loading 114M row spine
    - Two-step filtered retrieval: linkage → needed_ids → mileage
    - All joins computed in DuckDB, only fetchdf() final cohort-sized result
    
    Args:
        df: DataFrame with test_id, test_mileage, days_since_last_test, annualized_mileage
        conn: DuckDB connection
        
    Returns:
        DataFrame with new mileage features added
    """
    print("  Adding V36 mileage block (DuckDB native)...")
    cohort_size = len(df)
    MAX_ALLOWED = cohort_size * 3  # Fail-fast threshold for fanout detection
    
    # ========================================================================
    # Step A: Register target_ids for semi-join pushdown
    # ========================================================================
    target_ids = df[['test_id']].drop_duplicates()
    conn.register('target_ids', target_ids)
    print(f"    Cohort size: {cohort_size:,}")
    
    # ========================================================================
    # Step B: Pull FILTERED linkage (only cohort rows) with predicate pushdown
    # ========================================================================
    linkage = conn.execute(f'''
        SELECT h.test_id, h.prev_cycle_test_id, h.days_since_prev_cycle
        FROM read_parquet('{HISTORY}') h
        WHERE h.test_id IN (SELECT test_id FROM target_ids)
    ''').fetchdf()
    
    linkage_count = len(linkage)
    print(f"    Linkage rows fetched: {linkage_count:,}")
    if linkage_count > MAX_ALLOWED:
        raise RuntimeError(f"Linkage fanout detected: {linkage_count:,} > {MAX_ALLOWED:,} (3× cohort)")
    
    # ========================================================================
    # Step C: Register linkage for next query
    # ========================================================================
    conn.register('linkage_tbl', linkage)
    
    # Count how many prev_cycle_test_id we need to look up
    n_prev_ids = linkage['prev_cycle_test_id'].notna().sum()
    print(f"    Prior test IDs to look up: {n_prev_ids:,}")
    
    # ========================================================================
    # Step D: Pull mileage for needed_ids only (target_ids ∪ prev_cycle_test_id)
    # This is the key optimization - query sampled files with pushdown
    # ========================================================================
    mileage_df = conn.execute(f'''
        WITH needed_ids AS (
            SELECT test_id FROM target_ids
            UNION
            SELECT DISTINCT prev_cycle_test_id AS test_id 
            FROM linkage_tbl 
            WHERE prev_cycle_test_id IS NOT NULL
        )
        SELECT s.test_id, s.test_mileage
        FROM read_parquet('{VALIDATION_SAMPLES}/sampled_*.parquet') s
        WHERE s.test_id IN (SELECT test_id FROM needed_ids)
          AND s.test_mileage IS NOT NULL
    ''').fetchdf()
    
    mileage_count = len(mileage_df)
    print(f"    Mileage rows fetched: {mileage_count:,}")
    if mileage_count > MAX_ALLOWED * 2:  # Allow 2× for union of target + prev
        raise RuntimeError(f"Mileage fanout detected: {mileage_count:,} > {MAX_ALLOWED*2:,}")
    
    # ========================================================================
    # Step E: Register mileage and input df for DuckDB computation
    # ========================================================================
    conn.register('mileage_tbl', mileage_df)
    
    # Only pass columns needed for computation
    df_subset = df[['test_id', 'test_mileage', 'days_since_last_test', 'annualized_mileage']].copy()
    conn.register('df_tbl', df_subset)
    
    # ========================================================================
    # Step F: Compute all features in DuckDB SQL
    # ========================================================================
    result = conn.execute('''
        SELECT 
            d.test_id,
            
            -- miles_since_last_test (NULL if no prior mileage)
            CASE 
                WHEN m_prev.test_mileage IS NOT NULL 
                THEN d.test_mileage - m_prev.test_mileage 
                ELSE NULL 
            END AS miles_since_last_test_raw,
            
            -- annualized_mileage_v2 with guards
            CASE
                WHEN l.days_since_prev_cycle IS NULL THEN NULL
                WHEN l.days_since_prev_cycle <= 0 THEN NULL
                WHEN m_prev.test_mileage IS NULL THEN NULL
                ELSE LEAST(50000.0, GREATEST(0.0, 
                    (d.test_mileage - m_prev.test_mileage) * 365.0 / l.days_since_prev_cycle
                ))
            END AS annualized_mileage_v2_raw,
            
            -- mileage_anomaly_flag: rollback, extreme delta, or zero/negative days
            CASE
                WHEN l.days_since_prev_cycle IS NOT NULL AND l.days_since_prev_cycle <= 0 THEN 1
                WHEN m_prev.test_mileage IS NOT NULL AND (d.test_mileage - m_prev.test_mileage) < -1000 THEN 1
                WHEN m_prev.test_mileage IS NOT NULL AND (d.test_mileage - m_prev.test_mileage) > 100000 THEN 1
                WHEN l.days_since_prev_cycle IS NOT NULL AND l.days_since_prev_cycle > 0 AND m_prev.test_mileage IS NOT NULL
                     AND (d.test_mileage - m_prev.test_mileage) * 365.0 / l.days_since_prev_cycle > 40000 THEN 1
                ELSE 0
            END AS mileage_anomaly_flag,
            
            -- mileage_source_mismatch: compare old annualized to new
            CASE
                WHEN d.annualized_mileage IS NULL THEN 0
                WHEN m_prev.test_mileage IS NULL THEN 0
                WHEN l.days_since_prev_cycle IS NULL OR l.days_since_prev_cycle <= 0 THEN 0
                WHEN ABS(d.annualized_mileage - 
                     LEAST(50000.0, GREATEST(0.0, 
                         (d.test_mileage - m_prev.test_mileage) * 365.0 / l.days_since_prev_cycle
                     ))) > 5000 THEN 1
                ELSE 0
            END AS mileage_source_mismatch,
            
            -- Pass through for fallback defaults
            d.annualized_mileage AS annualized_mileage_fallback
            
        FROM df_tbl d
        LEFT JOIN linkage_tbl l ON d.test_id = l.test_id
        LEFT JOIN mileage_tbl m_prev ON l.prev_cycle_test_id = m_prev.test_id
    ''').fetchdf()
    
    result_count = len(result)
    print(f"    Result rows: {result_count:,}")
    
    # ========================================================================
    # Step G: Apply defaults and compute final features
    # ========================================================================
    # miles_since_last_test: NULL → 0
    result['miles_since_last_test'] = result['miles_since_last_test_raw'].fillna(0).astype(float)
    
    # annualized_mileage_v2: NULL → fallback to old annualized_mileage → 8000
    result['annualized_mileage_v2'] = result['annualized_mileage_v2_raw'].fillna(
        result['annualized_mileage_fallback']
    ).fillna(8000.0).astype(float)
    
    # Ensure integer types for flags
    result['mileage_anomaly_flag'] = result['mileage_anomaly_flag'].fillna(0).astype(int)
    result['mileage_source_mismatch'] = result['mileage_source_mismatch'].fillna(0).astype(int)

    # V47: Add explicit missingness indicators
    result['has_prev_mileage'] = (result['miles_since_last_test_raw'].notna()).astype(int)
    result['mileage_plausible_flag'] = (result['mileage_anomaly_flag'] == 0).astype(int)
    
    # ========================================================================
    # Step H: Compute and log statistics
    # ========================================================================
    v2_from_spine = result['miles_since_last_test_raw'].notna().mean() * 100
    v2_coverage = (result['annualized_mileage_v2'] != 8000).mean() * 100
    mismatch_rate = result['mileage_source_mismatch'].mean() * 100
    anomaly_rate = result['mileage_anomaly_flag'].mean() * 100
    
    print(f"    Prior mileage found (spine lookup): {v2_from_spine:.1f}%")
    print(f"    annualized_mileage_v2 coverage: {v2_coverage:.1f}%")
    print(f"    mileage_source_mismatch=1: {mismatch_rate:.1f}%")
    print(f"    mileage_anomaly_flag=1: {anomaly_rate:.1f}%")

    # V47: Log new indicators
    has_prev_rate = result['has_prev_mileage'].mean() * 100
    plausible_rate = result['mileage_plausible_flag'].mean() * 100
    print(f"    has_prev_mileage=1: {has_prev_rate:.1f}%")
    print(f"    mileage_plausible_flag=1: {plausible_rate:.1f}%")
    
    # ========================================================================
    # Step I: Cleanup registered tables
    # ========================================================================
    conn.unregister('target_ids')
    conn.unregister('linkage_tbl')
    conn.unregister('mileage_tbl')
    conn.unregister('df_tbl')
    
    # ========================================================================
    # Step J: Merge results back to original dataframe
    # ========================================================================
    # V47: Added has_prev_mileage, mileage_plausible_flag
    feature_cols = ['test_id', 'miles_since_last_test', 'annualized_mileage_v2',
                    'mileage_anomaly_flag', 'mileage_source_mismatch',
                    'has_prev_mileage', 'mileage_plausible_flag']
    df = df.merge(result[feature_cols], on='test_id', how='left')
    
    return df




# ============================================================================
# Inherited Functions from V27 (with minimal changes)
# ============================================================================

def build_query_dev(dataset_path: Path, adv_features_path: Path, veterans_only: bool = True,
                    train_years: list = None) -> str:
    """Build feature extraction query for DEV set."""
    where_clauses = []
    if veterans_only:
        where_clauses.append("""
            NOT (
                d.prev_cycle_outcome_band = 'NO_PRIOR_RECORDED'
                AND (d.n_prior_tests IS NULL OR d.n_prior_tests = 0)
            )
        """)
    # Filter to training years (2019-2023)
    if train_years:
        years_str = ', '.join(str(y) for y in train_years)
        where_clauses.append(f"YEAR(d.test_date) IN ({years_str})")
    where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    return f"""
    SELECT
        d.test_id,
        d.vehicle_id,
        d.test_date,
        CAST(d.is_failure AS INT) as target,
        COALESCE(d.model_id, 'UNKNOWN UNKNOWN') as model_id,
        COALESCE(d.prev_cycle_outcome_band, 'NONE') as prev_cycle_outcome_band,
        COALESCE(d.gap_band, 'NONE') as gap_band,
        COALESCE(d.make, 'UNKNOWN') as make,
        COALESCE(d.age_band, 'UNKNOWN') as age_band,
        CAST(COALESCE(d.test_mileage, 50000) AS FLOAT) as test_mileage,
        CAST(COALESCE(h.prev_advisory_total, 0) AS FLOAT) as prev_count_advisory,
        COALESCE(h.advisory_trend, 'UNKNOWN') as advisory_trend,
        CAST(COALESCE(h.days_since_prev_cycle, 730) AS FLOAT) as days_since_last_test,
        CAST(COALESCE(d.prior_fail_rate_smoothed, 0.33) AS FLOAT) as prior_fail_rate_smoothed,
        CAST(COALESCE(d.n_prior_tests, 0) AS FLOAT) as n_prior_tests,
        CAST(COALESCE(h.days_since_prev_cycle, 730) - 365 AS FLOAT) as days_late,
        CAST(COALESCE(a.prev_adv_brakes, 0) AS FLOAT) as prev_adv_brakes,
        CAST(COALESCE(a.prev_adv_suspension, 0) AS FLOAT) as prev_adv_suspension,
        CAST(COALESCE(a.prev_adv_steering, 0) AS FLOAT) as prev_adv_steering,
        CAST(COALESCE(a.prev_adv_tyres, 0) AS FLOAT) as prev_adv_tyres,
        CAST(COALESCE(d.annualized_mileage, 8000) AS FLOAT) as annualized_mileage,
        CASE WHEN h.test_id IS NOT NULL THEN 1 ELSE 0 END as has_cycle_history_link,
        COALESCE(d.postcode_area, 'UNKNOWN') as postcode_area,
        CAST(COALESCE(d.mileage_percentile_for_age, 0.5) AS FLOAT) as mileage_percentile_for_age,
        CAST(COALESCE(d.n_prior_fails, 0) AS FLOAT) as n_prior_fails,
        CAST(COALESCE(d.fails_last_365d, 0) AS FLOAT) as fails_last_365d,
        CAST(COALESCE(d.fails_last_730d, 0) AS FLOAT) as fails_last_730d,
        -- V30 Hybrid: Use cycle-to-cycle if available, else annualized_mileage proxy
        CASE
            WHEN d.usage_intensity_band IN ('low', 'medium', 'high')
                THEN d.usage_intensity_band
            WHEN d.annualized_mileage IS NOT NULL AND d.annualized_mileage > 0 THEN
                CASE
                    WHEN d.annualized_mileage < 5000 THEN 'low'
                    WHEN d.annualized_mileage < 12000 THEN 'medium'
                    ELSE 'high'
                END
            ELSE 'unknown'
        END as usage_band_hybrid,
        -- V31: PONR features
        CAST(COALESCE(p.ponr_risk_score, 0) AS FLOAT) as ponr_risk_score,
        CAST(COALESCE(p.has_ponr_pattern, 0) AS INT) as has_ponr_pattern
    FROM read_parquet('{dataset_path}') d
    LEFT JOIN read_parquet('{HISTORY}') h ON d.test_id = h.test_id
    LEFT JOIN read_parquet('{adv_features_path}') a ON d.test_id = a.test_id
    LEFT JOIN read_parquet('{PONR_FEATURES}') p ON d.test_id = p.test_id
    {where_clause}
    """


def build_query_oot(dataset_path: Path, adv_features_path: Path, veterans_only: bool = True) -> str:
    """Build feature extraction query for OOT set with extended coverage."""
    where_clauses = []
    if veterans_only:
        where_clauses.append("""
            NOT (
                d.prev_cycle_outcome_band = 'NO_PRIOR_RECORDED'
                AND (d.n_prior_tests IS NULL OR d.n_prior_tests = 0)
            )
        """)
    where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    return f"""
    SELECT
        d.test_id,
        d.vehicle_id,
        d.test_date,
        CAST(d.is_failure AS INT) as target,
        COALESCE(d.model_id, 'UNKNOWN UNKNOWN') as model_id,
        COALESCE(d.prev_cycle_outcome_band, 'NONE') as prev_cycle_outcome_band,
        COALESCE(d.gap_band, 'NONE') as gap_band,
        COALESCE(d.make, 'UNKNOWN') as make,
        COALESCE(d.age_band, 'UNKNOWN') as age_band,
        CAST(COALESCE(d.test_mileage, 50000) AS FLOAT) as test_mileage,
        CAST(COALESCE(e.prev_advisory_total, 0) AS FLOAT) as prev_count_advisory,
        COALESCE(e.advisory_trend, 'UNKNOWN') as advisory_trend,
        CAST(COALESCE(e.days_since_prev_cycle, 730) AS FLOAT) as days_since_last_test,
        CAST(COALESCE(d.prior_fail_rate_smoothed, 0.33) AS FLOAT) as prior_fail_rate_smoothed,
        CAST(COALESCE(d.n_prior_tests, 0) AS FLOAT) as n_prior_tests,
        CAST(COALESCE(e.days_since_prev_cycle, 730) - 365 AS FLOAT) as days_late,
        CAST(COALESCE(a.prev_adv_brakes, 0) AS FLOAT) as prev_adv_brakes,
        CAST(COALESCE(a.prev_adv_suspension, 0) AS FLOAT) as prev_adv_suspension,
        CAST(COALESCE(a.prev_adv_steering, 0) AS FLOAT) as prev_adv_steering,
        CAST(COALESCE(a.prev_adv_tyres, 0) AS FLOAT) as prev_adv_tyres,
        CAST(COALESCE(d.annualized_mileage, 8000) AS FLOAT) as annualized_mileage,
        CASE WHEN e.test_id IS NOT NULL THEN 1 ELSE 0 END as has_cycle_history_link,
        COALESCE(d.postcode_area, 'UNKNOWN') as postcode_area,
        CAST(COALESCE(d.mileage_percentile_for_age, 0.5) AS FLOAT) as mileage_percentile_for_age,
        CAST(COALESCE(d.n_prior_fails, 0) AS FLOAT) as n_prior_fails,
        CAST(COALESCE(d.fails_last_365d, 0) AS FLOAT) as fails_last_365d,
        CAST(COALESCE(d.fails_last_730d, 0) AS FLOAT) as fails_last_730d,
        -- V30 Hybrid: Use cycle-to-cycle if available, else annualized_mileage proxy
        CASE
            WHEN d.usage_intensity_band IN ('low', 'medium', 'high')
                THEN d.usage_intensity_band
            WHEN d.annualized_mileage IS NOT NULL AND d.annualized_mileage > 0 THEN
                CASE
                    WHEN d.annualized_mileage < 5000 THEN 'low'
                    WHEN d.annualized_mileage < 12000 THEN 'medium'
                    ELSE 'high'
                END
            ELSE 'unknown'
        END as usage_band_hybrid,
        -- V31: PONR features
        CAST(COALESCE(p.ponr_risk_score, 0) AS FLOAT) as ponr_risk_score,
        CAST(COALESCE(p.has_ponr_pattern, 0) AS INT) as has_ponr_pattern
    FROM read_parquet('{dataset_path}') d
    LEFT JOIN read_parquet('{HISTORY_EXTENSION_2024}') e ON d.test_id = e.test_id
    LEFT JOIN read_parquet('{adv_features_path}') a ON d.test_id = a.test_id
    LEFT JOIN read_parquet('{PONR_FEATURES}') p ON d.test_id = p.test_id
    {where_clause}
    """


def add_eb_features(df: pd.DataFrame, conn, max_asof_date: str) -> pd.DataFrame:
    """Add hierarchical EB features from precomputed parquets."""
    print("  Adding hierarchical EB features...")
    df = df.copy()

    df['join_month'] = pd.to_datetime(df['test_date']).dt.to_period('M').dt.to_timestamp()
    df['join_month'] = df['join_month'].clip(upper=pd.Timestamp(max_asof_date))

    def compute_age_band_key(row):
        if pd.isna(row.get('age_band')) or row['age_band'] == 'UNKNOWN':
            return 'Unknown'
        ab = str(row['age_band'])
        if ab in ['0-2', '0-1', '1-2', '2-3', '0-3']:
            return '0-2'
        elif ab in ['3-5', '3-4', '4-5']:
            return '3-5'
        elif ab in ['6-10', '5-6', '6-7', '7-8', '8-9', '9-10', '5-10']:
            return '6-10'
        elif ab in ['11-15', '10-11', '11-12', '12-13', '13-14', '14-15', '10-15']:
            return '11-15'
        else:
            return '15+'

    def compute_mileage_band_key(mileage):
        if pd.isna(mileage) or mileage < 0:
            return 'Unknown'
        elif mileage < 30000:
            return '0-30k'
        elif mileage < 60000:
            return '30k-60k'
        elif mileage < 100000:
            return '60k-100k'
        else:
            return '100k+'

    df['age_band_key'] = df.apply(compute_age_band_key, axis=1)
    df['mileage_band_key'] = df['test_mileage'].apply(compute_mileage_band_key)

    # Load segment priors
    if SEGMENT_PRIORS.exists():
        segment_priors = conn.execute(f"""
            SELECT asof_month, model_id, age_band, mileage_band,
                   eb_segment_long, eb_segment_short, make_rate_long
            FROM read_parquet('{SEGMENT_PRIORS}')
        """).fetchdf()
        segment_priors['asof_month'] = pd.to_datetime(segment_priors['asof_month'])

        df = df.merge(
            segment_priors,
            left_on=['join_month', 'model_id', 'age_band_key', 'mileage_band_key'],
            right_on=['asof_month', 'model_id', 'age_band', 'mileage_band'],
            how='left',
            suffixes=('', '_seg')
        )
        df['eb_long'] = df['eb_segment_long'].fillna(0.30)
        df['eb_short'] = df['eb_segment_short'].fillna(0.30)
        print(f"    Segment priors coverage: {(df['eb_segment_long'].notna()).mean()*100:.1f}%")
    else:
        df['eb_long'] = 0.30
        df['eb_short'] = 0.30

    # Load make priors
    if MAKE_PRIORS.exists():
        make_priors = conn.execute(f"""
            SELECT asof_month, make, make_rate_long as make_eb_long, make_rate_short as make_eb_short
            FROM read_parquet('{MAKE_PRIORS}')
        """).fetchdf()
        make_priors['asof_month'] = pd.to_datetime(make_priors['asof_month'])

        df = df.merge(make_priors, left_on=['join_month', 'make'],
                      right_on=['asof_month', 'make'], how='left', suffixes=('', '_make'))
        df['eb_long'] = df['eb_long'].fillna(df['make_eb_long']).fillna(0.30)
        df['eb_short'] = df['eb_short'].fillna(df['make_eb_short']).fillna(0.30)

    df['drift_ratio'] = (df['eb_short'] / (df['eb_long'] + 1e-6)).clip(0.5, 2.0)

    # V48: REMOVED - eb_hierarchical_features.parquet has unclear provenance
    # These features (eb_model, eb_model_age, eb_segment_hier, eb_model_vs_segment, eb_reliability_signal)
    # are now replaced by eb_unified_prior loaded separately in add_v48_unified_prior()
    # Old code commented out for reference:
    # if EB_HIERARCHICAL.exists():
    #     hier_eb = conn.execute(f"""
    #         SELECT model_id, age_band, mileage_band,
    #                eb_model, eb_model_age, eb_segment_smoothed as eb_segment_hier, n_segment
    #         FROM read_parquet('{EB_HIERARCHICAL}')
    #     """).fetchdf()
    #     df = df.merge(hier_eb, ...)
    #     etc.

    # Cleanup
    drop_cols = ['join_month', 'age_band_key', 'mileage_band_key', 'asof_month',
                 'age_band_seg', 'mileage_band_seg', 'eb_segment_long', 'eb_segment_short',
                 'make_rate_long', 'asof_month_make', 'make_eb_long', 'make_eb_short',
                 'age_band_hier', 'mileage_band_hier', 'eb_long', 'eb_short', 'drift_ratio']
    df = df.drop(columns=[c for c in drop_cols if c in df.columns], errors='ignore')

    return df


def add_apathy_features(df: pd.DataFrame, conn) -> pd.DataFrame:
    """Add V15 prior apathy features from precomputed parquet."""
    print("  Adding prior apathy features...")

    if not PRIOR_APATHY_FEATURES.exists():
        print(f"    WARNING: {PRIOR_APATHY_FEATURES} not found")
        df['has_prior_apathy'] = 0
        df['prior_apathy_rate'] = 0.0
        return df

    # Register test_ids as a temporary table for semi-join pushdown
    # This avoids loading all 114M rows from the parquet file
    test_ids = df[['test_id']].drop_duplicates()
    conn.register('target_ids', test_ids)

    apathy_df = conn.execute(f"""
        SELECT p.test_id,
               CAST(p.has_prior_apathy AS INTEGER) as has_prior_apathy,
               CAST(p.prior_apathy_rate AS DOUBLE) as prior_apathy_rate
        FROM read_parquet('{PRIOR_APATHY_FEATURES}') p
        WHERE p.test_id IN (SELECT test_id FROM target_ids)
    """).fetchdf()

    conn.unregister('target_ids')

    df = df.merge(apathy_df, on='test_id', how='left')
    df['has_prior_apathy'] = df['has_prior_apathy'].fillna(0).astype(int)
    df['prior_apathy_rate'] = df['prior_apathy_rate'].fillna(0.0).astype(float)

    n_matched = df['has_prior_apathy'].notna().sum()
    pct_apathy = (df['has_prior_apathy'] == 1).mean() * 100
    print(f"    Matched: {n_matched:,} / {len(df):,}")
    print(f"    has_prior_apathy=1: {pct_apathy:.1f}%")

    return df


def add_cooccurrence_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add V15 advisory co-occurrence features."""
    print("  Adding co-occurrence features...")
    df = df.copy()

    df['co_occur_brakes_suspension'] = ((df['prev_adv_brakes'] > 0) & (df['prev_adv_suspension'] > 0)).astype(int)
    df['co_occur_brakes_tyres'] = ((df['prev_adv_brakes'] > 0) & (df['prev_adv_tyres'] > 0)).astype(int)
    df['co_occur_brakes_steering'] = ((df['prev_adv_brakes'] > 0) & (df['prev_adv_steering'] > 0)).astype(int)
    df['co_occur_suspension_tyres'] = ((df['prev_adv_suspension'] > 0) & (df['prev_adv_tyres'] > 0)).astype(int)
    df['co_occur_steering_tyres'] = ((df['prev_adv_steering'] > 0) & (df['prev_adv_tyres'] > 0)).astype(int)
    df['co_occur_suspension_steering'] = ((df['prev_adv_suspension'] > 0) & (df['prev_adv_steering'] > 0)).astype(int)

    df['multi_system_advisory_count'] = (
        (df['prev_adv_brakes'] > 0).astype(int) +
        (df['prev_adv_suspension'] > 0).astype(int) +
        (df['prev_adv_steering'] > 0).astype(int) +
        (df['prev_adv_tyres'] > 0).astype(int)
    )

    df['has_multi_system_advisory'] = (df['multi_system_advisory_count'] >= 2).astype(int)

    pct_multi = (df['has_multi_system_advisory'] == 1).mean() * 100
    print(f"    has_multi_system_advisory=1: {pct_multi:.1f}%")

    return df


def add_degradation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add V16 cumulative degradation features."""
    print("  Adding degradation features...")
    df = df.copy()

    n_prior = df['n_prior_tests'].clip(lower=1)
    lifetime_fail_rate = df['n_prior_fails'] / n_prior

    recent_fail_rate = df['fails_last_365d']

    df['fail_rate_trend'] = recent_fail_rate - lifetime_fail_rate
    df['fail_rate_trend'] = df['fail_rate_trend'].fillna(0).clip(-1, 1)

    n_prior_fails = df['n_prior_fails'].clip(lower=0.1)
    df['recent_fail_intensity'] = df['fails_last_365d'] / n_prior_fails
    df['recent_fail_intensity'] = df['recent_fail_intensity'].fillna(0).clip(0, 3)

    mean_trend = df['fail_rate_trend'].mean()
    mean_intensity = df['recent_fail_intensity'].mean()
    print(f"    fail_rate_trend mean: {mean_trend:.3f}")
    print(f"    recent_fail_intensity mean: {mean_intensity:.3f}")

    return df


def add_advisory_v4_features(df: pd.DataFrame, conn) -> pd.DataFrame:
    """Add V32 split advisory/failure features from precomputed parquet.

    These features separate:
    - Advisory = degradation signal (risk increases over time)
    - Failure = repair signal (risk resets after failure)

    Key logic:
    - miles_since_last_advisory resets to NULL after a failure
    - has_prior_failure = most recent prior event was a failure
    - has_ever_failed = any failure in lifetime
    """
    print("  Adding V32 split advisory/failure features...")

    if not ADVISORY_V4_FEATURES.exists():
        print(f"    WARNING: {ADVISORY_V4_FEATURES} not found")
        # Set all V32 features to defaults
        for col in V32_ADVISORY_COLS:
            df[col] = 0
        for col in V32_FAILURE_COLS:
            df[col] = 0
        return df

    # Register test_ids as a temporary table for semi-join pushdown
    test_ids = df[['test_id']].drop_duplicates()
    conn.register('target_ids', test_ids)

    # Build column list for query
    v4_cols = ['test_id'] + V32_ADVISORY_COLS + V32_FAILURE_COLS
    col_str = ', '.join(v4_cols)

    adv_df = conn.execute(f"""
        SELECT {col_str}
        FROM read_parquet('{ADVISORY_V4_FEATURES}')
        WHERE test_id IN (SELECT test_id FROM target_ids)
    """).fetchdf()

    conn.unregister('target_ids')

    # Merge
    df = df.merge(adv_df, on='test_id', how='left')

    # Fill NULLs with defaults
    for col in V32_ADVISORY_COLS:
        if col.startswith('has_') or col.startswith('advisory_in_'):
            # Boolean flags
            df[col] = df[col].fillna(0).astype(int)
        elif col.endswith('_streak_len') or col.startswith('tests_since_'):
            # Integer counts
            df[col] = df[col].fillna(0).astype(int)
        else:
            # miles_since - keep as float, NULL means no prior advisory
            df[col] = df[col].fillna(0).astype(float)

    for col in V32_FAILURE_COLS:
        df[col] = df[col].fillna(0).astype(int)

    # Stats
    n_matched = adv_df.shape[0]
    pct_matched = n_matched / len(df) * 100
    print(f"    Matched: {n_matched:,} / {len(df):,} ({pct_matched:.1f}%)")

    # Advisory coverage
    for comp in ['brakes', 'tyres', 'suspension']:
        pct_adv = (df[f'has_prior_advisory_{comp}'] == 1).mean() * 100
        pct_fail = (df[f'has_prior_failure_{comp}'] == 1).mean() * 100
        print(f"    {comp}: advisory={pct_adv:.1f}%, failure={pct_fail:.1f}%")

    return df


def add_neglect_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Add V33 component-specific neglect scores with optimized weights.

    Weights learned via logistic regression on DEV set, validated on OOT.
    Result: +2.21pp AUC improvement over hand-picked weights.

    Formula:
        neglect_score_{comp} = (adv_streak * W_adv) + (fail_streak * W_fail) + (tsf * W_tsf)

    Key insight: Suspension repair REDUCES risk (positive TSF weight),
    while brakes/tyres recent failure slightly increases risk.
    """
    print("  Adding V33 neglect scores (optimized weights)...")
    df = df.copy()

    # Optimized weights from logistic regression (2.21pp AUC lift)
    NEGLECT_WEIGHTS = {
        'brakes': {'adv': 0.19, 'fail': 0.55, 'tsf': -0.02},
        'tyres': {'adv': 0.20, 'fail': 0.35, 'tsf': -0.03},
        'suspension': {'adv': 0.36, 'fail': 0.53, 'tsf': 0.06},
    }

    for comp in ['brakes', 'tyres', 'suspension']:
        w = NEGLECT_WEIGHTS[comp]
        adv_streak = df[f'advisory_streak_len_{comp}'].fillna(0)
        fail_streak = df[f'failure_streak_len_{comp}'].fillna(0)
        tests_since_repair = df[f'tests_since_last_failure_{comp}'].fillna(0)

        df[f'neglect_score_{comp}'] = (
            (adv_streak * w['adv']) +
            (fail_streak * w['fail']) +
            (tests_since_repair * w['tsf'])
        )

    # Stats
    for comp in ['brakes', 'tyres', 'suspension']:
        mean_score = df[f'neglect_score_{comp}'].mean()
        pct_positive = (df[f'neglect_score_{comp}'] > 0).mean() * 100
        print(f"    {comp}: mean={mean_score:.3f}, >0: {pct_positive:.1f}%")

    return df


def compute_station_strictness(train_df: pd.DataFrame) -> dict:
    """Compute station strictness bias: Actual Fails / Expected Fails.

    Expected fails = sum of make×age expected fail rates for all tests at station.
    Uses Bayesian shrinkage to handle low-volume stations.

    Returns dict mapping postcode_area -> strictness_bias
    """
    print("  Computing station strictness bias...")

    # Step 1: Compute make×age expected fail rates from training data
    make_age_rates = train_df.groupby(['make', 'age_band'])['target'].mean()

    # Create lookup with fallback
    global_rate = train_df['target'].mean()

    def get_expected_rate(row):
        key = (row['make'], row['age_band'])
        return make_age_rates.get(key, global_rate)

    # Step 2: For each test, get expected fail rate
    train_df = train_df.copy()
    train_df['expected_fail'] = train_df.apply(get_expected_rate, axis=1)

    # Step 3: Aggregate by station (postcode_area)
    station_stats = train_df.groupby('postcode_area').agg({
        'target': 'sum',           # Actual fails
        'expected_fail': 'sum',    # Expected fails
    }).reset_index()
    station_stats.columns = ['postcode_area', 'actual_fails', 'expected_fails']

    # Step 4: Compute strictness with Bayesian shrinkage
    # Formula: (actual + prior_n * prior_rate) / (expected + prior_n)
    # prior_n = 10 provides shrinkage toward 1.0 for low-volume stations
    PRIOR_N = 10
    station_stats['strictness_bias'] = (
        (station_stats['actual_fails'] + PRIOR_N) /
        (station_stats['expected_fails'] + PRIOR_N)
    )

    # Cap extreme values
    station_stats['strictness_bias'] = station_stats['strictness_bias'].clip(0.5, 2.0)

    # Convert to dict
    strictness_dict = dict(zip(
        station_stats['postcode_area'],
        station_stats['strictness_bias']
    ))

    # Stats
    mean_bias = station_stats['strictness_bias'].mean()
    std_bias = station_stats['strictness_bias'].std()
    n_stations = len(strictness_dict)
    print(f"    Stations: {n_stations}")
    print(f"    Mean strictness: {mean_bias:.3f} (std={std_bias:.3f})")
    print(f"    Range: {station_stats['strictness_bias'].min():.3f} - {station_stats['strictness_bias'].max():.3f}")

    return strictness_dict


def add_station_strictness(df: pd.DataFrame, strictness_dict: dict) -> pd.DataFrame:
    """Add station_strictness_bias feature using precomputed dict."""
    df = df.copy()

    # Map postcode_area to strictness (default 1.0 = neutral)
    df['station_strictness_bias'] = df['postcode_area'].map(strictness_dict).fillna(1.0)

    # Stats
    coverage = df['postcode_area'].isin(strictness_dict).mean() * 100
    mean_bias = df['station_strictness_bias'].mean()
    print(f"    Coverage: {coverage:.1f}%")
    print(f"    Mean bias in data: {mean_bias:.3f}")

    return df


def add_imd_features(df: pd.DataFrame, conn) -> pd.DataFrame:
    """Add area_deprivation_decile from IMD data.

    IMD decile: 1 = most deprived, 10 = least deprived
    Higher deprivation areas may have older/less maintained vehicles.
    """
    print("  Adding area_deprivation_decile...")

    if not IMD_FEATURES.exists():
        print(f"    WARNING: {IMD_FEATURES} not found - using default")
        df['area_deprivation_decile'] = 5  # Neutral default
        return df

    # Load IMD mapping
    imd_df = conn.execute(f"""
        SELECT postcode_area, area_deprivation_decile
        FROM read_parquet('{IMD_FEATURES}')
    """).fetchdf()

    # Create lookup dict
    imd_dict = dict(zip(imd_df['postcode_area'], imd_df['area_deprivation_decile']))

    # Map to dataframe (default to 5 = middle decile)
    df = df.copy()
    df['area_deprivation_decile'] = df['postcode_area'].map(imd_dict).fillna(5).astype(int)

    # Stats
    coverage = df['postcode_area'].isin(imd_dict).mean() * 100
    mean_decile = df['area_deprivation_decile'].mean()
    print(f"    Coverage: {coverage:.1f}%")
    print(f"    Mean decile: {mean_decile:.2f}")
    print(f"    Distribution:")
    print(df['area_deprivation_decile'].value_counts().sort_index().to_string(header=False))

    return df


def add_mechanical_target(df: pd.DataFrame, conn, is_oot: bool = False) -> pd.DataFrame:
    """Add is_mechanical_fail target using failure category data.

    Target denoising logic:
    - Passing tests: is_mechanical_fail = 0 (no failure)
    - Failed tests with category data: is_mechanical_fail = 1 if mechanical component failed
    - Failed tests without category data: is_mechanical_fail = original target (fallback)

    Uses different source files for DEV vs OOT:
    - DEV (2019-2023): system_failures_9cat.parquet
    - OOT (2024): component_labels_2024.parquet

    Returns DataFrame with all rows preserved (LEFT JOIN, not INNER).
    """
    if is_oot:
        source = COMPONENT_LABELS_2024
        source_name = "component_labels_2024"
        # OOT has separate fail_steering column
        mechanical_expr = "(c.fail_brakes = 1 OR c.fail_tyres = 1 OR c.fail_suspension = 1 OR c.fail_steering = 1)"
    else:
        source = SYSTEM_FAILURES_9CAT
        source_name = "system_failures_9cat"
        # DEV has combined suspension_steering
        mechanical_expr = "(c.fail_brakes = 1 OR c.fail_tyres_wheels = 1 OR c.fail_suspension_steering = 1)"

    print(f"  Adding mechanical target ({source_name})...")

    if not source.exists():
        print(f"    WARNING: {source} not found - using original target")
        df['is_mechanical_fail'] = df['target']
        return df

    # Register DataFrame for join
    test_ids = df[['test_id']].drop_duplicates()
    conn.register('target_ids', test_ids)

    # Get failure categories for failed tests only
    result = conn.execute(f"""
        SELECT
            c.test_id,
            CASE WHEN {mechanical_expr} THEN 1 ELSE 0 END as has_mechanical_failure
        FROM read_parquet('{source}') c
        WHERE c.test_id IN (SELECT test_id FROM target_ids)
    """).fetchdf()

    conn.unregister('target_ids')

    original_len = len(df)

    # LEFT join to keep all rows
    df = df.merge(result, on='test_id', how='left')

    # Create target:
    # - Pass (target=0): is_mechanical_fail = 0
    # - Fail with category data: use has_mechanical_failure
    # - Fail without category data: fallback to original target
    df['is_mechanical_fail'] = df.apply(
        lambda row: 0 if row['target'] == 0
        else (row['has_mechanical_failure'] if pd.notna(row.get('has_mechanical_failure')) else row['target']),
        axis=1
    ).astype(int)

    # Drop helper column
    df = df.drop(columns=['has_mechanical_failure'], errors='ignore')

    # Stats
    matched_failures = df[(df['target'] == 1) & (df['is_mechanical_fail'].notna())].shape[0]
    total_failures = (df['target'] == 1).sum()
    mechanical_rate = df['is_mechanical_fail'].mean() * 100
    original_fail_rate = df['target'].mean() * 100

    print(f"    Total rows: {original_len:,}")
    print(f"    Failures with category data: {matched_failures:,} / {total_failures:,} ({matched_failures/total_failures*100:.1f}%)")
    print(f"    Original fail rate: {original_fail_rate:.1f}%")
    print(f"    Mechanical fail rate: {mechanical_rate:.1f}%")

    return df


def add_v27_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add V27 Maintenance Debt Persistence Score (MDPS)."""
    print("  Adding V27 MDPS features...")
    df = df.copy()

    W_SUSPENSION_STEERING = 1.3
    W_BRAKES = 1.2
    W_TYRES = 1.0

    ALPHA_PERSISTENCE = 0.5
    BETA_ESCALATION = 1.0

    A_MILEAGE = 0.7
    B_TIME = 0.3

    brakes = df['prev_adv_brakes'].fillna(0)
    suspension = df['prev_adv_suspension'].fillna(0)
    steering = df['prev_adv_steering'].fillna(0)
    tyres = df['prev_adv_tyres'].fillna(0)

    susp_steer = suspension + steering

    trend = df['advisory_trend'].fillna('UNKNOWN').astype(str).str.upper()

    persistence = ((trend == 'STABLE') | (trend == 'WORSENING')).astype(int)
    escalation = (trend == 'WORSENING').astype(int)

    debt_susp_steer = W_SUSPENSION_STEERING * np.log1p(susp_steer) * (1 + ALPHA_PERSISTENCE * persistence + BETA_ESCALATION * escalation)
    debt_brakes = W_BRAKES * np.log1p(brakes) * (1 + ALPHA_PERSISTENCE * persistence + BETA_ESCALATION * escalation)
    debt_tyres = W_TYRES * np.log1p(tyres) * (1 + ALPHA_PERSISTENCE * persistence + BETA_ESCALATION * escalation)

    total_domain_debt = debt_susp_steer + debt_brakes + debt_tyres

    days_since = df['days_since_last_test'].fillna(730)
    months_since = days_since / 30.0

    annual_miles = df['annualized_mileage'].fillna(8000)

    usage_factor = 1.0 + A_MILEAGE * np.log1p(annual_miles / 5000) + B_TIME * np.log1p(months_since)
    usage_factor = usage_factor.clip(lower=1.0)

    df['mdps_score'] = usage_factor * total_domain_debt

    pct_positive = (df['mdps_score'] > 0).mean() * 100
    mean_mdps = df['mdps_score'].mean()
    print(f"    mdps_score > 0: {pct_positive:.1f}%")
    print(f"    mdps_score mean: {mean_mdps:.3f}")

    return df


def compute_cohort_stats(df: pd.DataFrame) -> dict:
    """Compute cohort statistics on training data."""
    print("  Computing cohort statistics...")

    global_mileage_avg = df['test_mileage'].mean()
    global_advisory_avg = df['prev_count_advisory'].mean()

    cohort_stats = df.groupby(['model_id', 'age_band']).agg({
        'test_mileage': ['mean', 'count'],
        'prev_count_advisory': 'mean'
    }).reset_index()
    cohort_stats.columns = ['model_id', 'age_band', 'mileage_avg', 'cohort_size', 'advisory_avg']

    MIN_COHORT_SIZE = 10
    model_stats = df.groupby('model_id').agg({
        'test_mileage': 'mean',
        'prev_count_advisory': 'mean'
    }).reset_index()
    model_stats.columns = ['model_id', 'model_mileage_avg', 'model_advisory_avg']

    cohort_stats = cohort_stats.merge(model_stats, on='model_id', how='left')
    cohort_stats.loc[cohort_stats['cohort_size'] < MIN_COHORT_SIZE, 'mileage_avg'] = \
        cohort_stats.loc[cohort_stats['cohort_size'] < MIN_COHORT_SIZE, 'model_mileage_avg']
    cohort_stats.loc[cohort_stats['cohort_size'] < MIN_COHORT_SIZE, 'advisory_avg'] = \
        cohort_stats.loc[cohort_stats['cohort_size'] < MIN_COHORT_SIZE, 'model_advisory_avg']

    cohort_mileage = {}
    cohort_advisory = {}
    for _, row in cohort_stats.iterrows():
        key = (row['model_id'], row['age_band'])
        cohort_mileage[key] = row['mileage_avg']
        cohort_advisory[key] = row['advisory_avg']

    return {
        'cohort_mileage': cohort_mileage,
        'cohort_advisory': cohort_advisory,
        'global_mileage_avg': global_mileage_avg,
        'global_advisory_avg': global_advisory_avg,
    }


def add_cohort_residuals(df: pd.DataFrame, cohort_stats: dict) -> pd.DataFrame:
    """Add cohort residual features."""
    df = df.copy()
    cohort_mileage = cohort_stats['cohort_mileage']
    cohort_advisory = cohort_stats['cohort_advisory']
    global_mileage = cohort_stats['global_mileage_avg']
    global_advisory = cohort_stats['global_advisory_avg']

    def get_mileage_ratio(row):
        key = (row['model_id'], row['age_band'])
        cohort_avg = cohort_mileage.get(key, global_mileage)
        if cohort_avg == 0:
            cohort_avg = global_mileage
        return row['test_mileage'] / cohort_avg

    df['mileage_cohort_ratio'] = df.apply(get_mileage_ratio, axis=1)

    def get_advisory_delta(row):
        key = (row['model_id'], row['age_band'])
        cohort_avg = cohort_advisory.get(key, global_advisory)
        return row['prev_count_advisory'] - cohort_avg

    df['advisory_cohort_delta'] = df.apply(get_advisory_delta, axis=1)
    df['days_since_pass_ratio'] = df['days_since_last_test'] / 365.0

    return df


def add_neglect_features(df: pd.DataFrame, conn) -> pd.DataFrame:
    """Add V9 neglect features."""
    if not NEGLECT_FEATURES_FILE.exists():
        df['neglect_score'] = 0.0
        df['neglect_score_last3'] = 0.0
        df['has_prior_random'] = 0
        df['has_prior_systemic'] = 0
        return df

    neglect_df = conn.execute(f"""
        SELECT test_id, neglect_score, neglect_score_last3, has_prior_random, has_prior_systemic
        FROM read_parquet('{NEGLECT_FEATURES_FILE}')
    """).fetchdf()

    df = df.merge(neglect_df, on='test_id', how='left')
    df['neglect_score'] = df['neglect_score'].fillna(0).astype(float)
    df['neglect_score_last3'] = df['neglect_score_last3'].fillna(0).astype(float)
    df['has_prior_random'] = df['has_prior_random'].fillna(0).astype(int)
    df['has_prior_systemic'] = df['has_prior_systemic'].fillna(0).astype(int)

    return df


def prepare_dataframe(df, cat_features):
    """Convert dataframe types for CatBoost."""
    df = df.copy()
    df['target'] = df['target'].astype('int8')
    for col in cat_features:
        if col in df.columns:
            df[col] = df[col].astype('str').fillna('UNKNOWN')
    return df


def compute_model_auc(y_true, y_pred, model_ids: pd.Series, target_models: list) -> dict:
    """Compute AUC for specific target models."""
    results = {}
    for model in target_models:
        mask = model_ids.str.contains(model, case=False, na=False)
        n = mask.sum()
        if n >= 50 and y_true[mask].nunique() == 2:
            auc = roc_auc_score(y_true[mask], y_pred[mask])
            results[model] = {'auc': auc, 'n': n}
        else:
            results[model] = {'auc': None, 'n': n}
    return results


def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add V55 temporal features derived from test_date.

    Extracts seasonal patterns from test_date:
    - test_month: 1-12 (categorical)
    - is_winter_test: Oct-Mar = 1 (binary)
    - day_of_week: 0-6 Mon-Sun (categorical)
    """
    print("  Adding V55 temporal features...")
    df = df.copy()

    # Convert test_date if needed
    if not pd.api.types.is_datetime64_any_dtype(df['test_date']):
        df['test_date'] = pd.to_datetime(df['test_date'])

    # Extract temporal features
    df['test_month'] = df['test_date'].dt.month
    df['day_of_week'] = df['test_date'].dt.dayofweek
    df['is_winter_test'] = df['test_date'].dt.month.isin([10, 11, 12, 1, 2, 3]).astype(int)

    # Stats
    winter_pct = df['is_winter_test'].mean() * 100
    print(f"    test_month range: {df['test_month'].min()}-{df['test_month'].max()}")
    print(f"    is_winter_test=1: {winter_pct:.1f}%")
    print(f"    day_of_week mode: {df['day_of_week'].mode().iloc[0]} (0=Mon)")

    return df


def main():
    start_time = datetime.now()
    print("=" * 70)
    print(f"AUTOSAFE CATBOOST V55 TRAINING (mode={ABLATION_MODE})")
    print("V55: Temporal Features (Seasonal Patterns)")
    print("=" * 70)

    conn = duckdb.connect()
    conn.execute("SET memory_limit='8GB'")

    # ========================================================================
    # Load Data
    # ========================================================================
    print("\n[1] Loading data...")

    # Train on 2019-2023 with new advisory data
    TRAIN_YEARS = [2019, 2020, 2021, 2022, 2023]
    query = build_query_dev(DEV_SET, ADV_FEATURES_DEV, veterans_only=True, train_years=TRAIN_YEARS)
    dev_raw = conn.execute(query).fetchdf()
    print(f"  Dev set: {len(dev_raw):,} veterans (years: {TRAIN_YEARS})")

    query = build_query_oot(OOT_SET, ADV_FEATURES_OOT, veterans_only=True)
    oot_raw = conn.execute(query).fetchdf()
    print(f"  OOT set: {len(oot_raw):,} veterans")

    # ========================================================================
    # Hierarchical Features
    # ========================================================================
    print("\n[2] Computing hierarchical features...")

    segment_hf = HierarchicalFeatures(k_global=K_GLOBAL, k_segment=K_SEGMENT)
    segment_hf.fit(dev_raw, target_col='target', make_col='make')
    dev_raw = segment_hf.transform(dev_raw, make_col='make', prev_outcome_col='prev_cycle_outcome_band')
    oot_raw = segment_hf.transform(oot_raw, make_col='make', prev_outcome_col='prev_cycle_outcome_band')
    dev_raw['segment_fail_rate_smoothed'] = dev_raw['make_fail_rate_smoothed']
    oot_raw['segment_fail_rate_smoothed'] = oot_raw['make_fail_rate_smoothed']

    # V29: Use model-level hierarchical features with overrides
    model_hf = ModelHierarchicalFeatures(k_global=K_GLOBAL, k_model=K_MODEL_DEFAULT)
    model_hf.fit(dev_raw, target_col='target', model_col='model_id')
    dev_raw = model_hf.transform(dev_raw, model_col='model_id', prev_outcome_col='prev_cycle_outcome_band')
    oot_raw = model_hf.transform(oot_raw, model_col='model_id', prev_outcome_col='prev_cycle_outcome_band')

    # ========================================================================
    # Hierarchical EB Features
    # ========================================================================
    print("\n[2b] Adding hierarchical EB features...")

    max_asof = conn.execute(f"SELECT MAX(asof_month) FROM read_parquet('{SEGMENT_PRIORS}')").fetchone()[0]
    max_asof_str = max_asof.strftime('%Y-%m-%d') if max_asof else '2023-12-01'

    dev_raw = add_eb_features(dev_raw, conn, max_asof_str)
    oot_raw = add_eb_features(oot_raw, conn, max_asof_str)

    # ========================================================================
    # Cohort Residuals
    # ========================================================================
    print("\n[3] Computing cohort residuals...")
    cohort_stats = compute_cohort_stats(dev_raw)
    dev_raw = add_cohort_residuals(dev_raw, cohort_stats)
    oot_raw = add_cohort_residuals(oot_raw, cohort_stats)

    # ========================================================================
    # Neglect Features
    # ========================================================================
    print("\n[4] Adding neglect features...")
    dev_raw = add_neglect_features(dev_raw, conn)
    oot_raw = add_neglect_features(oot_raw, conn)

    # ========================================================================
    # V15: Prior Apathy Features
    # ========================================================================
    print("\n[5] Adding prior apathy features...")
    dev_raw = add_apathy_features(dev_raw, conn)
    oot_raw = add_apathy_features(oot_raw, conn)

    # ========================================================================
    # V15: Co-Occurrence Features
    # ========================================================================
    print("\n[6] Adding co-occurrence features...")
    dev_raw = add_cooccurrence_features(dev_raw)
    oot_raw = add_cooccurrence_features(oot_raw)

    # ========================================================================
    # V16: Degradation Features
    # ========================================================================
    print("\n[6b] Adding degradation features...")
    dev_raw = add_degradation_features(dev_raw)
    oot_raw = add_degradation_features(oot_raw)

    # ========================================================================
    # V27: MDPS Features
    # ========================================================================
    print("\n[6c] Adding V27 MDPS features...")
    dev_raw = add_v27_features(dev_raw)
    oot_raw = add_v27_features(oot_raw)

    # ========================================================================
    # V29: Model-Specific Cohort Features
    # ========================================================================
    print("\n[6d] Adding V29 cohort features...")
    dev_raw = add_v29_cohort_features(dev_raw)
    oot_raw = add_v29_cohort_features(oot_raw)

    # ========================================================================
    # V36 NEW: Mileage Block (Trusted Spine Lineage) - DuckDB Native
    # ========================================================================
    print("\n[6d.5] Adding V36 mileage block...")
    dev_raw = add_mileage_block_v36(dev_raw, conn)
    oot_raw = add_mileage_block_v36(oot_raw, conn)


    # ========================================================================
    # V43 NEW: Local Corrosion Index (Geographic Environmental Risk)
    # ========================================================================
    print("\n[6d.6] Fitting V43 local corrosion index (DEV only - leakage-free)...")
    fit_v43_corrosion_index(dev_raw)  # Fit on training data only!
    
    print("\n[6d.7] Adding V43 corrosion features to both sets...")
    dev_raw = add_v43_corrosion_features(dev_raw)
    oot_raw = add_v43_corrosion_features(oot_raw)

    # ========================================================================
    # V44 NEW: High-Risk Model Flag + Suspension Risk Profile
    # ========================================================================
    print("\n[6d.8] Fitting V44 suspension risk profile (DEV only - leakage-free)...")
    fit_v44_suspension_profile(dev_raw)  # Fit on training data only!
    
    print("\n[6d.9] Adding V44 model risk features to both sets...")
    dev_raw = add_v44_model_risk_features(dev_raw)
    oot_raw = add_v44_model_risk_features(oot_raw)

    # ========================================================================
    # V44.1 NEW: Model-Age Interaction Feature (Shipped V45 Feature)
    # ========================================================================
    print("\n[6d.10] Adding V44.1 model-age features (from V45 ablation)...")
    dev_raw = add_v45_model_age_features(dev_raw, 'DEV')
    oot_raw = add_v45_model_age_features(oot_raw, 'OOT')

    # ========================================================================
    # V46 NEW: Negligence Features from ~/autosafe Pipeline
    # ========================================================================
    print("\n[6d.11] Adding V46 negligence features...")
    dev_raw = add_v46_negligence_features(dev_raw)
    oot_raw = add_v46_negligence_features(oot_raw)

    # ========================================================================
    # V48 NEW: Unified Hierarchical Prior (replaces eb_segment_hier, eb_model, etc.)
    # ========================================================================
    print("\n[6d.12] Adding V48 unified hierarchical prior...")
    dev_raw = add_v48_unified_prior(dev_raw, 'DEV')
    oot_raw = add_v48_unified_prior(oot_raw, 'OOT')

    # ========================================================================
    # V51 NEW: Mechanical Decay Features (Systemic Deterioration)
    # ========================================================================
    print("\n[6d.13] Adding V51 mechanical decay features...")
    dev_raw = add_v51_mechanical_decay_features(dev_raw)
    oot_raw = add_v51_mechanical_decay_features(oot_raw)

    # ========================================================================
    # V52 NEW: Text Mining Features (Semantic Defect Signals)
    # ========================================================================
    print("\n[6d.14] Adding V52 text mining features...")
    dev_raw = add_v52_text_mining_features(dev_raw)
    oot_raw = add_v52_text_mining_features(oot_raw)

    # ========================================================================
    # V55 NEW: Temporal Features (Seasonal Patterns)
    # ========================================================================
    print("\n[6d.15] Adding V55 temporal features...")
    dev_raw = add_temporal_features(dev_raw)
    oot_raw = add_temporal_features(oot_raw)

    # ========================================================================
    # V30 NEW: Usage Band Hybrid Distribution
    # ========================================================================
    print("\n[6e] V30: usage_band_hybrid distribution (cycle-to-cycle + annualized fallback):")
    print("  DEV set:")
    print(dev_raw['usage_band_hybrid'].value_counts(normalize=True).to_string(header=False))
    print("  OOT set:")
    print(oot_raw['usage_band_hybrid'].value_counts(normalize=True).to_string(header=False))

    # ========================================================================
    # V32 NEW: Split Advisory/Failure Features
    # ========================================================================
    print("\n[6f] Adding V32 split advisory/failure features...")
    dev_raw = add_advisory_v4_features(dev_raw, conn)
    oot_raw = add_advisory_v4_features(oot_raw, conn)

    # ========================================================================
    # V33 NEW: Component-Specific Neglect Scores
    # ========================================================================
    print("\n[6g] Adding V33 neglect scores...")
    dev_raw = add_neglect_scores(dev_raw)
    oot_raw = add_neglect_scores(oot_raw)

    # ========================================================================
    # V31: PONR Feature Distribution
    # ========================================================================
    print("\n[6h] V31: PONR feature distribution:")
    dev_ponr_pct = dev_raw['has_ponr_pattern'].mean() * 100
    oot_ponr_pct = oot_raw['has_ponr_pattern'].mean() * 100
    print(f"  DEV has_ponr_pattern=1: {dev_ponr_pct:.1f}%")
    print(f"  OOT has_ponr_pattern=1: {oot_ponr_pct:.1f}%")
    print(f"  DEV ponr_risk_score mean: {dev_raw['ponr_risk_score'].mean():.3f}")
    print(f"  OOT ponr_risk_score mean: {oot_raw['ponr_risk_score'].mean():.3f}")

    # ========================================================================
    # Station Priors
    # ========================================================================
    print("\n[8] Adding station priors...")
    station_priors = StationPriors()
    station_priors.fit(dev_raw, target_col='target', station_col='postcode_area',
                       outcome_col='prev_cycle_outcome_band')
    dev_raw = station_priors.transform(dev_raw, station_col='postcode_area',
                                        outcome_col='prev_cycle_outcome_band')
    oot_raw = station_priors.transform(oot_raw, station_col='postcode_area',
                                        outcome_col='prev_cycle_outcome_band')

    # ========================================================================
    # V34 NEW: Station Strictness Bias
    # ========================================================================
    print("\n[8b] Computing station strictness bias...")
    strictness_dict = compute_station_strictness(dev_raw)
    print("  Applying to datasets...")
    dev_raw = add_station_strictness(dev_raw, strictness_dict)
    oot_raw = add_station_strictness(oot_raw, strictness_dict)

    # ========================================================================
    # V34 NEW: Area Deprivation (IMD)
    # ========================================================================
    print("\n[8c] Adding area deprivation features...")
    dev_raw = add_imd_features(dev_raw, conn)
    oot_raw = add_imd_features(oot_raw, conn)

    # ========================================================================
    # Prepare for Training
    # ========================================================================
    print("\n[9] Preparing training data...")
    dev_df = prepare_dataframe(dev_raw, CAT_FEATURES)
    oot_df = prepare_dataframe(oot_raw, CAT_FEATURES)

    # Check for missing features
    missing = [f for f in FEATURE_COLS if f not in dev_df.columns]
    if missing:
        print(f"  WARNING: Missing features: {missing}")
        for f in missing:
            dev_df[f] = 0
            oot_df[f] = 0

    X_train = dev_df[FEATURE_COLS]
    y_train = dev_df['target']  # V34: Reverted to all failures (mechanical-only was too sparse)
    X_test = oot_df[FEATURE_COLS]
    y_test = oot_df['target']  # V34: Reverted to all failures

    # Sample weights (2023 emphasized)
    dev_df['test_date'] = pd.to_datetime(dev_raw['test_date'])
    year = dev_df['test_date'].dt.year
    train_weights = np.select(
        [year == 2023, year == 2022, year == 2021],
        [20.0, 6.0, 2.0],
        default=1.0
    )

    print(f"  Training: {len(X_train):,} samples, {len(FEATURE_COLS)} features")
    print(f"  Testing:  {len(X_test):,} samples")
    print(f"  Train fail rate: {y_train.mean()*100:.1f}%")
    print(f"  Test fail rate:  {y_test.mean()*100:.1f}%")

    # ========================================================================
    # Save Prepared Data for Future Experiments
    # ========================================================================
    print("\n[9b] Saving prepared data...")
    prepared_data = {
        'X_train': X_train,
        'y_train': y_train,
        'X_test': X_test,
        'y_test': y_test,
        'cat_features': CAT_FEATURES,
        'feature_cols': FEATURE_COLS,
        'train_weights': train_weights,
    }
    prepared_data_path = Path.home() / "autosafe_work/v55_prepared_data.pkl"
    with open(prepared_data_path, 'wb') as f:
        pickle.dump(prepared_data, f)
    print(f"  Saved: {prepared_data_path}")

    # ========================================================================
    # Train CatBoost
    # ========================================================================
    print(f"\n[10] Training CatBoost (10-seed ensemble)...")
    print(f"     lr={PARAMS['learning_rate']}, depth={PARAMS['depth']}, iterations={PARAMS['iterations']}")

    cat_indices = [FEATURE_COLS.index(f) for f in CAT_FEATURES if f in FEATURE_COLS]
    train_pool = Pool(X_train, y_train, cat_features=cat_indices, weight=train_weights)
    test_pool = Pool(X_test, y_test, cat_features=cat_indices)

    N_SEEDS = 10
    all_preds_train = []
    all_preds_test = []
    all_importances = []
    models = []

    for seed in range(N_SEEDS):
        print(f"  Training seed {seed}...", end=" ", flush=True)
        model = CatBoostClassifier(
            iterations=PARAMS['iterations'],
            learning_rate=PARAMS['learning_rate'],
            depth=PARAMS['depth'],
            l2_leaf_reg=PARAMS['l2_leaf_reg'],
            border_count=PARAMS['border_count'],
            random_strength=PARAMS['random_strength'],
            bagging_temperature=PARAMS['bagging_temperature'],
            random_seed=seed,
            verbose=0,
            cat_features=cat_indices,
            eval_metric=PARAMS['eval_metric'],
        )
        model.fit(train_pool, eval_set=test_pool, early_stopping_rounds=150)

        pred_train = model.predict_proba(X_train)[:, 1]
        pred_test = model.predict_proba(X_test)[:, 1]
        seed_auc = roc_auc_score(y_test, pred_test)
        print(f"AUC={seed_auc:.4f}")

        all_preds_train.append(pred_train)
        all_preds_test.append(pred_test)
        all_importances.append(model.get_feature_importance())
        models.append(model)

    # Ensemble predictions
    y_pred_train = np.mean(all_preds_train, axis=0)
    y_pred_test = np.mean(all_preds_test, axis=0)

    model = models[0]

    # ========================================================================
    # Evaluate
    # ========================================================================
    print("\n[11] Evaluating ensemble...")

    train_auc = roc_auc_score(y_train, y_pred_train)
    test_auc = roc_auc_score(y_test, y_pred_test)
    brier = brier_score_loss(y_test, y_pred_test)
    logloss = log_loss(y_test, y_pred_test)

    print(f"\n  Train AUC: {train_auc:.4f}")
    print(f"  Test AUC:  {test_auc:.4f}")
    print(f"  Brier:     {brier:.4f}")
    print(f"  LogLoss:   {logloss:.4f}")

    # ========================================================================
    # V29: Target Model AUC (Key Metric)
    # ========================================================================
    print("\n[11b] Target Model AUC (V29 focus)...")

    TARGET_MODELS = ['VAUXHALL CORSA', 'FORD TRANSIT', 'BMW 3 SERIES', 'RENAULT CLIO']

    target_auc_results = compute_model_auc(
        y_test, y_pred_test, oot_df['model_id'], TARGET_MODELS
    )

    print("\n  Target Model Results:")
    print(f"  {'Model':<20} {'N':>8} {'AUC':>8} {'vs V27':>8}")
    print("  " + "-" * 46)

    # V27 baseline (from model_opportunity_analysis.csv)
    v27_baselines = {
        'VAUXHALL CORSA': 0.635,
        'FORD TRANSIT': 0.648,
        'BMW 3 SERIES': 0.644,
        'RENAULT CLIO': 0.648,
    }

    for model_name, result in target_auc_results.items():
        n = result['n']
        auc = result['auc']
        baseline = v27_baselines.get(model_name, 0.0)
        if auc is not None:
            delta = (auc - baseline) * 100  # Convert to basis points
            delta_str = f"+{delta:.1f}pp" if delta >= 0 else f"{delta:.1f}pp"
            print(f"  {model_name:<20} {n:>8,} {auc:>8.4f} {delta_str:>8}")
        else:
            print(f"  {model_name:<20} {n:>8,} {'N/A':>8} {'N/A':>8}")

    # ========================================================================
    # Platt Calibration
    # ========================================================================
    print("\n[12] Platt calibration...")
    calibrator = LogisticRegression()
    calibrator.fit(y_pred_train.reshape(-1, 1), y_train)
    y_calibrated = calibrator.predict_proba(y_pred_test.reshape(-1, 1))[:, 1]

    calibrated_brier = brier_score_loss(y_test, y_calibrated)
    print(f"  Calibrated Brier: {calibrated_brier:.4f}")

    # ========================================================================
    # Top-Decile Lift
    # ========================================================================
    print("\n[13] Top-decile lift...")
    n_decile = len(y_test) // 10
    top_indices = np.argsort(y_calibrated)[-n_decile:]
    top_decile_rate = y_test.iloc[top_indices].mean()
    baseline_rate = y_test.mean()
    lift = top_decile_rate / baseline_rate
    print(f"  Baseline failure rate: {baseline_rate*100:.1f}%")
    print(f"  Top-decile failure rate: {top_decile_rate*100:.1f}%")
    print(f"  Lift: {lift:.2f}x")

    # ========================================================================
    # Feature Importance
    # ========================================================================
    print("\n[14] Feature importance...")
    importances = np.mean(all_importances, axis=0)
    feature_importance = dict(zip(FEATURE_COLS, importances))

    # Mark new features
    v29_features = V29_FEATURE_COLS
    v30_features = V30_FEATURE_COLS
    v31_features = V31_FEATURE_COLS
    v32_features = V32_FEATURE_COLS
    v33_features = V33_FEATURE_COLS
    v34_features = V34_FEATURE_COLS
    v36_features = V36_MILEAGE_COLS
    v45_features = V45_MODEL_AGE_COLS

    print("\n  Top 20 features:")
    sorted_imp = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)
    for feat, imp in sorted_imp[:20]:
        if feat in v45_features:
            marker = " *V45 NEW*"
        elif feat in v36_features:
            marker = " *V36*"
        elif feat in v34_features:
            marker = " *V34*"
        elif feat in v33_features:
            marker = " *V33*"
        elif feat in v32_features:
            marker = " *V32*"
        elif feat in v31_features:
            marker = " *V31*"
        elif feat in v30_features:
            marker = " *V30*"
        elif feat in v29_features:
            marker = " *V29*"
        else:
            marker = ""
        print(f"    {feat}: {imp:.2f}%{marker}")

    # V29, V30, V31, V32, V33, V34, V36 feature importance
    v29_importance = sum(feature_importance.get(f, 0) for f in V29_FEATURE_COLS)
    v30_importance = sum(feature_importance.get(f, 0) for f in V30_FEATURE_COLS)
    v31_importance = sum(feature_importance.get(f, 0) for f in V31_FEATURE_COLS)
    v32_importance = sum(feature_importance.get(f, 0) for f in V32_FEATURE_COLS)
    v33_importance = sum(feature_importance.get(f, 0) for f in V33_FEATURE_COLS)
    v34_importance = sum(feature_importance.get(f, 0) for f in V34_FEATURE_COLS)
    v36_importance = sum(feature_importance.get(f, 0) for f in V36_MILEAGE_COLS)
    print(f"\n  V29 feature importance: {v29_importance:.2f}%")
    print(f"  V30 feature importance: {v30_importance:.2f}%")
    print(f"  V31 feature importance: {v31_importance:.2f}%")
    print(f"  V32 feature importance: {v32_importance:.2f}%")
    print(f"  V33 feature importance: {v33_importance:.2f}%")
    print(f"  V34 feature importance: {v34_importance:.2f}%")
    print(f"  V36 feature importance: {v36_importance:.2f}%")

    # ========================================================================
    # Save Artifacts
    # ========================================================================
    print("\n[15] Saving artifacts...")
    model.save_model(str(MODEL_FILE))
    with open(CALIBRATOR_FILE, 'wb') as f:
        pickle.dump(calibrator, f)
    with open(SEGMENT_HF_FILE, 'wb') as f:
        pickle.dump(segment_hf, f)
    with open(MODEL_HF_FILE, 'wb') as f:
        pickle.dump(model_hf, f)
    with open(COHORT_STATS_FILE, 'wb') as f:
        pickle.dump(cohort_stats, f)

    results = {
        'version': 'V40',
        'ablation_mode': ABLATION_MODE,
        'strategy': 'V37 Lean + Undercarriage Stress + Structural Fatigue',
        'target': 'is_failure',
        'train_auc': train_auc,
        'test_auc': test_auc,
        'brier': brier,
        'calibrated_brier': calibrated_brier,
        'logloss': logloss,
        'top_decile_lift': lift,
        'n_features': len(FEATURE_COLS),
        'v29_importance': v29_importance,
        'v30_importance': v30_importance,
        'v31_importance': v31_importance,
        'v32_importance': v32_importance,
        'v33_importance': v33_importance,
        'v34_importance': v34_importance,
        'v36_importance': v36_importance,
        'v43_importance': sum(feature_importance.get(f, 0) for f in V43_GEO_COLS),
        'v44_importance': sum(feature_importance.get(f, 0) for f in V44_MODEL_RISK_COLS),
        'v45_importance': sum(feature_importance.get(f, 0) for f in V45_MODEL_AGE_COLS),
        'target_model_auc': {k: v['auc'] for k, v in target_auc_results.items()},
        'v27_baselines': v27_baselines,
        'hyperparameters': PARAMS,
        'k_model_overrides': K_MODEL_OVERRIDES,
        'feature_importance': feature_importance,
        'created': datetime.now().isoformat(),
    }
    with open(RESULTS_FILE, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"  Saved to: {WORK_DIR}")

    # ========================================================================
    # Summary
    # ========================================================================
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70)
    print(f"V48 TRAINING COMPLETE - Unified EB Prior Hierarchy")
    print("=" * 70)
    print(f"  Test AUC:            {test_auc:.4f} (Goal: >= 0.7356)")
    print(f"  Brier:               {calibrated_brier:.4f}")
    print(f"  Top-decile Lift:     {lift:.2f}x")
    print(f"  Feature count:       {len(FEATURE_COLS)}")
    v43_importance = sum(feature_importance.get(f, 0) for f in V43_GEO_COLS)
    v44_importance = sum(feature_importance.get(f, 0) for f in V44_MODEL_RISK_COLS)
    v45_importance = sum(feature_importance.get(f, 0) for f in V45_MODEL_AGE_COLS)
    v46_importance = sum(feature_importance.get(f, 0) for f in V46_NEGLIGENCE_COLS)
    v48_importance = sum(feature_importance.get(f, 0) for f in V48_UNIFIED_PRIOR_COLS)
    v51_importance = sum(feature_importance.get(f, 0) for f in V51_MECHANICAL_DECAY_COLS)
    v52_importance = sum(feature_importance.get(f, 0) for f in V52_TEXT_MINING_COLS)
    print(f"  V43 Importance:      {v43_importance:.2f}%")
    print(f"  V44 Importance:      {v44_importance:.2f}%")
    print(f"  V45 Importance:      {v45_importance:.2f}% (model_age_fail_rate_eb)")
    print(f"  V46 Importance:      {v46_importance:.2f}% (negligence)")
    print(f"  V48 Importance:      {v48_importance:.2f}% (eb_unified_prior)")
    print(f"  V51 Importance:      {v51_importance:.2f}% (mechanical_decay)")
    print(f"  V52 Importance:      {v52_importance:.2f}% (text_mining)")

    print(f"\n  Target Model Results (BMW 3 SERIES is primary target):")
    for model_name, result in target_auc_results.items():
        if result['auc'] is not None:
            baseline = v27_baselines.get(model_name, 0.0)
            delta = (result['auc'] - baseline) * 100
            if 'BMW' in model_name:
                status = "TARGET" if result['auc'] >= 0.67 else "needs work"
            else:
                status = "IMPROVED" if delta >= 2 else "needs work"
            print(f"    {model_name}: {result['auc']:.4f} ({delta:+.1f}pp) - {status}")

    # Delta vs V47
    v47_baseline = 0.7356
    print(f"\n  vs V47 ({v47_baseline:.4f}): {(test_auc - v47_baseline)*100:+.2f}pp")

    # BMW check
    bmw_auc = target_auc_results.get('BMW 3 SERIES', {}).get('auc', 0)
    bmw_improved = bmw_auc >= 0.67 if bmw_auc else False

    success = test_auc >= 0.7356
    print(f"  SUCCESS: {'YES' if success else 'NO'} (AUC >= 0.7356)")
    print(f"  BMW TARGET: {'MET' if bmw_improved else 'NOT MET'} (Target: >= 0.67, Actual: {bmw_auc:.4f})")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 70)


    conn.close()


if __name__ == "__main__":
    main()
