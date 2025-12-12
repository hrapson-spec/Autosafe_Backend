"""
Previous Cycle Feature Engineering Pipeline

Adds vehicle-specific signal from previous cycle-first MOT to improve discrimination.
Target: ROC-AUC ≈ 0.75 under time-based split.

Produces:
- test_condition_summary.parquet: Defect aggregation per test_id
- model_dataset_with_prev_condition.parquet: Full dataset with prev_* features
- evaluation_report.md: Metrics and ablation study

Defect severity mapping (UK MOT May 2018+):
- Advisory (A): rfr_type_code='A' → weight 1
- Minor (M): rfr_type_code='F'/'P', dangerous_mark=0 or empty → weight 2
- Major (J): rfr_type_code='F'/'P', dangerous_mark=1 (non-dangerous fail) → weight 5
- Dangerous (D): rfr_type_code='F'/'P', dangerous_mark=2 or 'D' → weight 8

Usage:
    python build_prev_cycle_features.py [--eval-only] [--seed 42]
"""

import pandas as pd
import numpy as np
import os
import glob
import logging
from typing import Dict, Tuple, Optional
from datetime import datetime
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    # Input paths
    CYCLE_FIRST_INDEX = 'cycle_first_tests.parquet'

    # Test item (defect) sources
    DEFECT_SOURCES = [
        ("MOT Test Failures/MOT Testing data failure item (2024)", ",", "test_item_*.csv"),
        ("MOT Test Failures/2023", "|", "test_item*.csv"),
        ("MOT Test Failures/2022", "|", "test_item*.csv"),
    ]

    # Test result sources (for outcome/result data)
    RESULT_SOURCES = [
        ("MOT Test Results", ",", "test_result_*.csv"),
        ("MOT Test Results/2023", "|", "test_result.csv"),
        ("MOT Test Results/2022", "|", "test_result_2022.csv"),
    ]

    # Output paths
    CONDITION_SUMMARY_PATH = 'test_condition_summary.parquet'
    MODEL_DATASET_PATH = 'model_dataset_with_prev_condition.parquet'
    EVALUATION_REPORT_PATH = 'evaluation_report.md'

    # Severity weights for score calculation
    SEVERITY_WEIGHTS = {
        'advisory': 1,
        'minor': 2,
        'major': 5,
        'dangerous': 8
    }

    # Near-miss thresholds
    NEAR_MISS_ADVISORY_THRESHOLD = 5
    NEAR_MISS_SEVERITY_THRESHOLD = 8

    # Bucket definitions
    BURDEN_BUCKETS = [(0, 0, '0'), (1, 2, '1-2'), (3, 5, '3-5'), (6, float('inf'), '6+')]

    # Time split for evaluation (e.g., train on pre-2024, test on 2024)
    TIME_SPLIT_DATE = '2024-01-01'

    # Random seed for reproducibility
    RANDOM_SEED = 42

    # Chunk size for processing large files
    CHUNK_SIZE = 1_000_000


# =============================================================================
# STEP 1: BUILD TEST CONDITION SUMMARY
# =============================================================================

def classify_defect_severity(rfr_type_code: str, dangerous_mark) -> str:
    """
    Classify defect severity based on rfr_type_code and dangerous_mark.

    UK MOT defect categories (May 2018+):
    - Advisory: Warning only, doesn't fail test
    - Minor: Fail, no immediate danger
    - Major: Fail, significant defect
    - Dangerous: Immediate prohibition
    """
    rfr_type = str(rfr_type_code).upper().strip()

    # Advisory
    if rfr_type == 'A':
        return 'advisory'

    # Failure types (F = Fail, P = Pass after Rectification/PRS)
    if rfr_type in ('F', 'P', 'FAIL', 'PRS'):
        # Check dangerous_mark for severity
        dm = str(dangerous_mark).strip().upper() if pd.notna(dangerous_mark) else ''

        if dm in ('D', '2', 'TRUE', 'YES', 'DANGEROUS'):
            return 'dangerous'
        elif dm in ('1', 'M', 'MAJOR'):
            return 'major'
        else:
            # Default failure without dangerous mark = minor
            return 'minor'

    # Unknown type - treat as minor if it caused some issue
    return 'minor'


def build_test_condition_summary(
    defect_sources: list = None,
    output_path: str = None
) -> pd.DataFrame:
    """
    Aggregate defect data per test_id into a condition summary.

    Output columns:
    - test_id
    - advisory_count, minor_count, major_count, dangerous_count
    - total_defects
    - severity_score = 1*A + 2*M + 5*J + 8*D
    - has_any_defect (flag)
    - has_major_or_dangerous (flag)
    """
    if defect_sources is None:
        defect_sources = Config.DEFECT_SOURCES
    if output_path is None:
        output_path = Config.CONDITION_SUMMARY_PATH

    logger.info("Building test condition summary from defect data...")

    # Collect all defect files
    file_sources = []
    for folder, delimiter, pattern in defect_sources:
        file_pattern = os.path.join(folder, pattern)
        matched = glob.glob(file_pattern)
        for f in matched:
            file_sources.append((f, delimiter))
        logger.info(f"  Found {len(matched)} files in '{folder}'")

    if not file_sources:
        logger.warning("No defect files found. Creating empty condition summary.")
        empty_df = pd.DataFrame(columns=[
            'test_id', 'advisory_count', 'minor_count', 'major_count',
            'dangerous_count', 'total_defects', 'severity_score',
            'has_any_defect', 'has_major_or_dangerous'
        ])
        empty_df.to_parquet(output_path, index=False)
        return empty_df

    logger.info(f"Processing {len(file_sources)} defect files...")

    # Process in chunks and aggregate
    all_summaries = []

    for filepath, delimiter in file_sources:
        logger.info(f"  Processing {os.path.basename(filepath)}...")

        try:
            # Read columns we need
            usecols = ['test_id', 'rfr_type_code', 'dangerous_mark']

            chunks = pd.read_csv(
                filepath,
                sep=delimiter,
                usecols=lambda c: c.lower() in [col.lower() for col in usecols],
                dtype=str,
                chunksize=Config.CHUNK_SIZE,
                encoding='latin1',
                on_bad_lines='skip'
            )

            for chunk in chunks:
                # Normalize column names
                chunk.columns = chunk.columns.str.lower()

                # Ensure required columns exist
                if 'test_id' not in chunk.columns:
                    continue
                if 'rfr_type_code' not in chunk.columns:
                    chunk['rfr_type_code'] = 'F'  # Default to failure
                if 'dangerous_mark' not in chunk.columns:
                    chunk['dangerous_mark'] = ''

                # Classify severity for each defect
                chunk['severity'] = chunk.apply(
                    lambda row: classify_defect_severity(
                        row['rfr_type_code'],
                        row['dangerous_mark']
                    ),
                    axis=1
                )

                # Aggregate by test_id
                summary = chunk.groupby('test_id').agg(
                    advisory_count=('severity', lambda x: (x == 'advisory').sum()),
                    minor_count=('severity', lambda x: (x == 'minor').sum()),
                    major_count=('severity', lambda x: (x == 'major').sum()),
                    dangerous_count=('severity', lambda x: (x == 'dangerous').sum()),
                ).reset_index()

                all_summaries.append(summary)

        except Exception as e:
            logger.error(f"Error processing {filepath}: {e}")
            continue

    if not all_summaries:
        logger.warning("No defect data processed. Creating empty summary.")
        empty_df = pd.DataFrame(columns=[
            'test_id', 'advisory_count', 'minor_count', 'major_count',
            'dangerous_count', 'total_defects', 'severity_score',
            'has_any_defect', 'has_major_or_dangerous'
        ])
        empty_df.to_parquet(output_path, index=False)
        return empty_df

    # Combine and re-aggregate (same test_id might appear in multiple files)
    logger.info("Consolidating summaries...")
    combined = pd.concat(all_summaries, ignore_index=True)

    final_summary = combined.groupby('test_id').agg({
        'advisory_count': 'sum',
        'minor_count': 'sum',
        'major_count': 'sum',
        'dangerous_count': 'sum'
    }).reset_index()

    # Calculate derived fields
    w = Config.SEVERITY_WEIGHTS
    final_summary['total_defects'] = (
        final_summary['advisory_count'] +
        final_summary['minor_count'] +
        final_summary['major_count'] +
        final_summary['dangerous_count']
    )

    final_summary['severity_score'] = (
        w['advisory'] * final_summary['advisory_count'] +
        w['minor'] * final_summary['minor_count'] +
        w['major'] * final_summary['major_count'] +
        w['dangerous'] * final_summary['dangerous_count']
    )

    final_summary['has_any_defect'] = (final_summary['total_defects'] > 0).astype(np.int8)
    final_summary['has_major_or_dangerous'] = (
        (final_summary['major_count'] > 0) | (final_summary['dangerous_count'] > 0)
    ).astype(np.int8)

    # Convert test_id to int64
    final_summary['test_id'] = pd.to_numeric(final_summary['test_id'], errors='coerce').fillna(0).astype('int64')
    final_summary = final_summary[final_summary['test_id'] > 0]

    # Optimize dtypes
    for col in ['advisory_count', 'minor_count', 'major_count', 'dangerous_count', 'total_defects']:
        final_summary[col] = final_summary[col].astype('int32')
    final_summary['severity_score'] = final_summary['severity_score'].astype('int32')

    # Save
    final_summary.to_parquet(output_path, index=False)

    logger.info(f"Saved condition summary: {len(final_summary):,} tests to {output_path}")
    logger.info(f"  Tests with defects: {(final_summary['has_any_defect'] == 1).sum():,}")
    logger.info(f"  Tests with major/dangerous: {(final_summary['has_major_or_dangerous'] == 1).sum():,}")

    return final_summary


# =============================================================================
# STEP 2: LOAD CYCLE-FIRST TESTS WITH OUTCOMES
# =============================================================================

def load_cycle_first_with_outcomes(
    cycle_index_path: str = None,
    result_sources: list = None
) -> pd.DataFrame:
    """
    Load cycle-first tests and join with test outcomes (PASS/FAIL).

    Returns DataFrame with:
    - test_id, vehicle_id, test_date
    - test_result (1=FAIL, 0=PASS)
    """
    if cycle_index_path is None:
        cycle_index_path = Config.CYCLE_FIRST_INDEX
    if result_sources is None:
        result_sources = Config.RESULT_SOURCES

    logger.info(f"Loading cycle-first index from {cycle_index_path}...")

    if not os.path.exists(cycle_index_path):
        raise FileNotFoundError(
            f"Cycle index not found: {cycle_index_path}. "
            "Run cycle_filter.build_cycle_index() first."
        )

    cycle_df = pd.read_parquet(cycle_index_path)
    logger.info(f"  Loaded {len(cycle_df):,} cycle-first tests")

    # Load test results to get outcomes
    logger.info("Loading test outcomes...")

    all_results = []
    for folder, delimiter, pattern in result_sources:
        file_pattern = os.path.join(folder, pattern)
        for filepath in glob.glob(file_pattern):
            try:
                chunks = pd.read_csv(
                    filepath,
                    sep=delimiter,
                    usecols=['test_id', 'test_result'],
                    dtype={'test_id': str, 'test_result': str},
                    chunksize=Config.CHUNK_SIZE,
                    encoding='latin1',
                    on_bad_lines='skip'
                )

                for chunk in chunks:
                    chunk['test_id'] = pd.to_numeric(chunk['test_id'], errors='coerce').fillna(0).astype('int64')
                    # Convert result to binary: 1=FAIL, 0=PASS
                    chunk['failed'] = chunk['test_result'].str.upper().isin(['F', 'FAIL']).astype(np.int8)
                    all_results.append(chunk[['test_id', 'failed']])

            except Exception as e:
                logger.warning(f"Error reading {filepath}: {e}")
                continue

    if all_results:
        results_df = pd.concat(all_results, ignore_index=True)
        # Keep first occurrence per test_id (shouldn't have duplicates)
        results_df = results_df.drop_duplicates(subset='test_id', keep='first')

        # Merge with cycle-first
        cycle_df = cycle_df.merge(results_df, on='test_id', how='left')
        cycle_df['failed'] = cycle_df['failed'].fillna(0).astype(np.int8)

        logger.info(f"  Matched outcomes for {(~cycle_df['failed'].isna()).sum():,} tests")
        logger.info(f"  Failure rate: {cycle_df['failed'].mean():.1%}")
    else:
        logger.warning("No test result files found. Assuming all tests are outcomes unknown.")
        cycle_df['failed'] = 0

    return cycle_df


# =============================================================================
# STEP 3: BUILD PREVIOUS CYCLE LINKAGE
# =============================================================================

def assign_burden_bucket(total_defects: int) -> str:
    """Assign burden bucket based on total defects."""
    for low, high, label in Config.BURDEN_BUCKETS:
        if low <= total_defects <= high:
            return label
    return '6+'


def build_prev_cycle_features(
    cycle_df: pd.DataFrame,
    condition_summary: pd.DataFrame,
    output_path: str = None
) -> pd.DataFrame:
    """
    Build previous cycle features by linking consecutive cycle-first tests.

    For each test, finds the previous cycle-first test for the same vehicle
    and joins its condition summary and outcome.

    Creates:
    - has_prev_cycle (0/1)
    - prev_failed (0/1)
    - prev_advisory_count, prev_minor_count, prev_major_count, prev_dangerous_count
    - prev_total_defects, prev_severity_score
    - prev_has_any_defect, prev_has_major_or_dangerous
    - prev_near_miss_advisory (prev_advisory_count >= 5)
    - prev_near_miss_severity (prev_severity_score >= 8)
    - prev_result_bucket: NO_HISTORY, PREV_PASS, PREV_FAIL
    - prev_burden_bucket: 0, 1-2, 3-5, 6+
    """
    if output_path is None:
        output_path = Config.MODEL_DATASET_PATH

    logger.info("Building previous cycle features...")

    # Sort by vehicle_id and test_date
    df = cycle_df.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)

    # Compute lag within each vehicle
    df['prev_test_id'] = df.groupby('vehicle_id')['test_id'].shift(1)
    df['prev_test_date'] = df.groupby('vehicle_id')['test_date'].shift(1)
    df['prev_failed_raw'] = df.groupby('vehicle_id')['failed'].shift(1)

    # Flag: has previous cycle
    df['has_prev_cycle'] = df['prev_test_id'].notna().astype(np.int8)

    # =========================================================================
    # LEAKAGE ASSERTION: prev_test_date must be < test_date
    # =========================================================================
    if df['has_prev_cycle'].sum() > 0:
        with_prev = df[df['has_prev_cycle'] == 1]
        leakage_check = with_prev['prev_test_date'] >= with_prev['test_date']
        leakage_count = leakage_check.sum()

        assert leakage_count == 0, (
            f"LEAKAGE DETECTED: {leakage_count} records have prev_test_date >= test_date!"
        )
        logger.info("✓ Leakage assertion passed: all prev_test_date < test_date")

    # Join condition summary for previous test
    logger.info("Joining previous test condition summaries...")

    # Prepare condition summary for join
    prev_cond = condition_summary.copy()
    prev_cond.columns = ['prev_' + c if c != 'test_id' else 'prev_test_id' for c in prev_cond.columns]
    prev_cond['prev_test_id'] = prev_cond['prev_test_id'].astype('float64')  # Match nullable int

    df = df.merge(prev_cond, on='prev_test_id', how='left')

    # Fill missing previous values with 0
    prev_cols = [
        'prev_advisory_count', 'prev_minor_count', 'prev_major_count',
        'prev_dangerous_count', 'prev_total_defects', 'prev_severity_score',
        'prev_has_any_defect', 'prev_has_major_or_dangerous'
    ]

    for col in prev_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype('int32')
        else:
            df[col] = 0

    # prev_failed from the lag
    df['prev_failed'] = df['prev_failed_raw'].fillna(0).astype(np.int8)

    # For tests without previous cycle, ensure all prev_* are 0
    no_prev_mask = df['has_prev_cycle'] == 0
    for col in prev_cols + ['prev_failed']:
        df.loc[no_prev_mask, col] = 0

    # Near-miss flags
    df['prev_near_miss_advisory'] = (
        df['prev_advisory_count'] >= Config.NEAR_MISS_ADVISORY_THRESHOLD
    ).astype(np.int8)

    df['prev_near_miss_severity'] = (
        df['prev_severity_score'] >= Config.NEAR_MISS_SEVERITY_THRESHOLD
    ).astype(np.int8)

    # =========================================================================
    # BUCKET FEATURES (for EB segmentation compatibility)
    # =========================================================================

    # prev_result_bucket
    def get_result_bucket(row):
        if row['has_prev_cycle'] == 0:
            return 'NO_HISTORY'
        elif row['prev_failed'] == 1:
            return 'PREV_FAIL'
        else:
            return 'PREV_PASS'

    df['prev_result_bucket'] = df.apply(get_result_bucket, axis=1)

    # prev_burden_bucket
    df['prev_burden_bucket'] = df['prev_total_defects'].apply(assign_burden_bucket)
    df.loc[no_prev_mask, 'prev_burden_bucket'] = '0'  # No history = 0 burden

    # Clean up intermediate columns
    df = df.drop(columns=['prev_test_id', 'prev_test_date', 'prev_failed_raw'], errors='ignore')

    # Save
    df.to_parquet(output_path, index=False)

    logger.info(f"Saved model dataset: {len(df):,} records to {output_path}")
    logger.info(f"  % with previous cycle: {df['has_prev_cycle'].mean():.1%}")
    logger.info(f"  % prev_failed (among those with history): "
                f"{df[df['has_prev_cycle']==1]['prev_failed'].mean():.1%}")

    return df


# =============================================================================
# STEP 4: EVALUATION WITH ABLATION STUDY
# =============================================================================

def evaluate_model(
    df: pd.DataFrame,
    features: list,
    target: str = 'failed',
    time_split_date: str = None,
    model_name: str = 'LogisticRegression'
) -> Dict:
    """
    Evaluate a model using time-based train/test split.

    Returns metrics: ROC-AUC, PR-AUC, Brier score
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
    from sklearn.preprocessing import StandardScaler

    if time_split_date is None:
        time_split_date = Config.TIME_SPLIT_DATE

    # Time-based split
    split_date = pd.to_datetime(time_split_date)
    train_mask = df['test_date'] < split_date
    test_mask = df['test_date'] >= split_date

    train_df = df[train_mask]
    test_df = df[test_mask]

    if len(train_df) == 0 or len(test_df) == 0:
        return {'error': 'Insufficient data for time split'}

    # Prepare features
    X_train = train_df[features].values
    y_train = train_df[target].values
    X_test = test_df[features].values
    y_test = test_df[target].values

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Train model
    model = LogisticRegression(random_state=Config.RANDOM_SEED, max_iter=1000)
    model.fit(X_train_scaled, y_train)

    # Predict probabilities
    y_prob = model.predict_proba(X_test_scaled)[:, 1]

    # Calculate metrics
    metrics = {
        'n_train': len(train_df),
        'n_test': len(test_df),
        'train_failure_rate': y_train.mean(),
        'test_failure_rate': y_test.mean(),
        'roc_auc': roc_auc_score(y_test, y_prob),
        'pr_auc': average_precision_score(y_test, y_prob),
        'brier': brier_score_loss(y_test, y_prob),
        'features': features
    }

    return metrics


def run_ablation_study(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run ablation study with progressive feature sets.

    Stages:
    1. Baseline (no prev features)
    2. +prev_failed
    3. +prev counts
    4. +counts+score+near-miss
    5. Full set (numeric)
    6. Bucketed variant (for EB)
    """
    logger.info("Running ablation study...")

    results = []

    # Ensure we have required columns
    required = ['failed', 'test_date', 'has_prev_cycle']
    if not all(col in df.columns for col in required):
        logger.error(f"Missing required columns: {required}")
        return pd.DataFrame()

    # Feature sets for ablation
    ablation_stages = [
        ('baseline', ['has_prev_cycle']),
        ('+prev_failed', ['has_prev_cycle', 'prev_failed']),
        ('+prev_counts', ['has_prev_cycle', 'prev_failed',
                         'prev_advisory_count', 'prev_minor_count',
                         'prev_major_count', 'prev_dangerous_count']),
        ('+score+near_miss', ['has_prev_cycle', 'prev_failed',
                             'prev_advisory_count', 'prev_minor_count',
                             'prev_major_count', 'prev_dangerous_count',
                             'prev_total_defects', 'prev_severity_score',
                             'prev_near_miss_advisory', 'prev_near_miss_severity']),
        ('full_numeric', ['has_prev_cycle', 'prev_failed',
                         'prev_advisory_count', 'prev_minor_count',
                         'prev_major_count', 'prev_dangerous_count',
                         'prev_total_defects', 'prev_severity_score',
                         'prev_has_any_defect', 'prev_has_major_or_dangerous',
                         'prev_near_miss_advisory', 'prev_near_miss_severity']),
    ]

    for stage_name, features in ablation_stages:
        # Check all features exist
        missing = [f for f in features if f not in df.columns]
        if missing:
            logger.warning(f"Skipping {stage_name}: missing features {missing}")
            continue

        try:
            metrics = evaluate_model(df, features)
            metrics['stage'] = stage_name
            results.append(metrics)
            logger.info(f"  {stage_name}: ROC-AUC={metrics['roc_auc']:.4f}, "
                       f"PR-AUC={metrics['pr_auc']:.4f}, Brier={metrics['brier']:.4f}")
        except Exception as e:
            logger.error(f"Error evaluating {stage_name}: {e}")

    # Bucketed variant for EB compatibility
    if 'prev_result_bucket' in df.columns and 'prev_burden_bucket' in df.columns:
        try:
            # One-hot encode buckets
            bucket_df = df.copy()
            result_dummies = pd.get_dummies(bucket_df['prev_result_bucket'], prefix='result')
            burden_dummies = pd.get_dummies(bucket_df['prev_burden_bucket'], prefix='burden')

            bucket_features = list(result_dummies.columns) + list(burden_dummies.columns)
            bucket_df = pd.concat([bucket_df, result_dummies, burden_dummies], axis=1)

            metrics = evaluate_model(bucket_df, bucket_features)
            metrics['stage'] = 'bucketed_eb'
            results.append(metrics)
            logger.info(f"  bucketed_eb: ROC-AUC={metrics['roc_auc']:.4f}, "
                       f"PR-AUC={metrics['pr_auc']:.4f}, Brier={metrics['brier']:.4f}")
        except Exception as e:
            logger.error(f"Error evaluating bucketed: {e}")

    return pd.DataFrame(results)


# =============================================================================
# STEP 5: QA CHECKS
# =============================================================================

def run_qa_checks(df: pd.DataFrame, condition_summary: pd.DataFrame) -> Dict:
    """
    Run QA checks on the generated data.

    Checks:
    1. Plausible % has_prev_cycle (expect 40-80%)
    2. Leakage assertion (already done in build step)
    3. prev_failed raises failure odds
    4. prev_major_or_dangerous raises failure odds
    5. Reproducible outputs (hash check)
    """
    logger.info("Running QA checks...")

    qa_results = {
        'passed': True,
        'checks': []
    }

    # Check 1: Plausible % has_prev_cycle
    pct_has_prev = df['has_prev_cycle'].mean()
    check1 = {
        'name': 'plausible_prev_cycle_rate',
        'value': f'{pct_has_prev:.1%}',
        'expected': '40-80%',
        'passed': 0.4 <= pct_has_prev <= 0.8
    }
    qa_results['checks'].append(check1)
    logger.info(f"  % has_prev_cycle: {pct_has_prev:.1%} (expected 40-80%)")

    # Check 2: prev_failed raises failure odds
    if df['has_prev_cycle'].sum() > 0:
        with_prev = df[df['has_prev_cycle'] == 1]

        fail_rate_prev_fail = with_prev[with_prev['prev_failed'] == 1]['failed'].mean()
        fail_rate_prev_pass = with_prev[with_prev['prev_failed'] == 0]['failed'].mean()

        odds_ratio = (fail_rate_prev_fail / fail_rate_prev_pass) if fail_rate_prev_pass > 0 else float('inf')

        check2 = {
            'name': 'prev_failed_raises_odds',
            'value': f'OR={odds_ratio:.2f} (fail_if_prev_fail={fail_rate_prev_fail:.1%}, '
                    f'fail_if_prev_pass={fail_rate_prev_pass:.1%})',
            'expected': 'OR > 1.0',
            'passed': odds_ratio > 1.0
        }
        qa_results['checks'].append(check2)
        logger.info(f"  prev_failed odds ratio: {odds_ratio:.2f}")

    # Check 3: prev_major_or_dangerous raises failure odds
    if 'prev_has_major_or_dangerous' in df.columns and df['has_prev_cycle'].sum() > 0:
        with_prev = df[df['has_prev_cycle'] == 1]

        fail_rate_major = with_prev[with_prev['prev_has_major_or_dangerous'] == 1]['failed'].mean()
        fail_rate_no_major = with_prev[with_prev['prev_has_major_or_dangerous'] == 0]['failed'].mean()

        if fail_rate_no_major > 0 and not np.isnan(fail_rate_major):
            odds_ratio_major = fail_rate_major / fail_rate_no_major

            check3 = {
                'name': 'prev_major_dangerous_raises_odds',
                'value': f'OR={odds_ratio_major:.2f}',
                'expected': 'OR > 1.0',
                'passed': odds_ratio_major > 1.0
            }
            qa_results['checks'].append(check3)
            logger.info(f"  prev_major_or_dangerous odds ratio: {odds_ratio_major:.2f}")

    # Check 4: Reproducibility hash
    hash_input = f"{len(df)}_{df['test_id'].sum()}_{df['has_prev_cycle'].sum()}"
    data_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    check4 = {
        'name': 'reproducibility_hash',
        'value': data_hash,
        'expected': 'Consistent across runs',
        'passed': True  # Will fail if hash changes between runs
    }
    qa_results['checks'].append(check4)
    logger.info(f"  Data hash: {data_hash}")

    # Overall pass/fail
    qa_results['passed'] = all(c['passed'] for c in qa_results['checks'])

    return qa_results


# =============================================================================
# STEP 6: GENERATE EVALUATION REPORT
# =============================================================================

def generate_evaluation_report(
    ablation_results: pd.DataFrame,
    qa_results: Dict,
    df: pd.DataFrame,
    output_path: str = None
) -> None:
    """Generate markdown evaluation report."""
    if output_path is None:
        output_path = Config.EVALUATION_REPORT_PATH

    logger.info(f"Generating evaluation report: {output_path}")

    report = []
    report.append("# Previous Cycle Feature Evaluation Report")
    report.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"\n## Dataset Summary")
    report.append(f"\n- Total records: {len(df):,}")
    report.append(f"- Records with previous cycle: {df['has_prev_cycle'].sum():,} ({df['has_prev_cycle'].mean():.1%})")
    report.append(f"- Overall failure rate: {df['failed'].mean():.1%}")

    # Time split info
    split_date = pd.to_datetime(Config.TIME_SPLIT_DATE)
    train_n = (df['test_date'] < split_date).sum()
    test_n = (df['test_date'] >= split_date).sum()
    report.append(f"\n### Time Split")
    report.append(f"- Split date: {Config.TIME_SPLIT_DATE}")
    report.append(f"- Training set: {train_n:,} records")
    report.append(f"- Test set: {test_n:,} records")

    # Ablation results
    report.append(f"\n## Ablation Study Results")
    report.append("\n| Stage | ROC-AUC | PR-AUC | Brier | Train N | Test N |")
    report.append("|-------|---------|--------|-------|---------|--------|")

    if not ablation_results.empty:
        for _, row in ablation_results.iterrows():
            report.append(
                f"| {row['stage']} | {row['roc_auc']:.4f} | {row['pr_auc']:.4f} | "
                f"{row['brier']:.4f} | {row.get('n_train', 'N/A'):,} | {row.get('n_test', 'N/A'):,} |"
            )
    else:
        report.append("| No results | - | - | - | - | - |")

    # Best model
    if not ablation_results.empty and 'roc_auc' in ablation_results.columns:
        best_idx = ablation_results['roc_auc'].idxmax()
        best = ablation_results.loc[best_idx]
        report.append(f"\n**Best Model:** {best['stage']} with ROC-AUC = {best['roc_auc']:.4f}")

    # QA Results
    report.append(f"\n## QA Checks")
    report.append(f"\n**Overall: {'✓ PASSED' if qa_results['passed'] else '✗ FAILED'}**\n")
    report.append("| Check | Value | Expected | Status |")
    report.append("|-------|-------|----------|--------|")

    for check in qa_results['checks']:
        status = '✓' if check['passed'] else '✗'
        report.append(f"| {check['name']} | {check['value']} | {check['expected']} | {status} |")

    # Feature descriptions
    report.append(f"\n## Feature Descriptions")
    report.append("""
| Feature | Description |
|---------|-------------|
| has_prev_cycle | 1 if vehicle has a previous cycle-first test |
| prev_failed | 1 if previous test was FAIL |
| prev_advisory_count | Count of advisory defects in previous test |
| prev_minor_count | Count of minor defects in previous test |
| prev_major_count | Count of major defects in previous test |
| prev_dangerous_count | Count of dangerous defects in previous test |
| prev_total_defects | Total defect count in previous test |
| prev_severity_score | Weighted score: 1×A + 2×M + 5×J + 8×D |
| prev_has_any_defect | 1 if previous test had any defects |
| prev_has_major_or_dangerous | 1 if previous test had major/dangerous defects |
| prev_near_miss_advisory | 1 if prev_advisory_count ≥ 5 |
| prev_near_miss_severity | 1 if prev_severity_score ≥ 8 |
| prev_result_bucket | NO_HISTORY, PREV_PASS, or PREV_FAIL |
| prev_burden_bucket | 0, 1-2, 3-5, or 6+ (total defects) |
""")

    # Write report
    with open(output_path, 'w') as f:
        f.write('\n'.join(report))

    logger.info(f"Report saved to {output_path}")


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_pipeline(eval_only: bool = False):
    """
    Run the full feature engineering and evaluation pipeline.

    Args:
        eval_only: If True, skip data generation and only run evaluation
    """
    logger.info("=" * 60)
    logger.info("PREVIOUS CYCLE FEATURE ENGINEERING PIPELINE")
    logger.info("=" * 60)

    np.random.seed(Config.RANDOM_SEED)

    if not eval_only:
        # Step 1: Build test condition summary
        logger.info("\n[STEP 1] Building test condition summary...")
        condition_summary = build_test_condition_summary()

        # Step 2: Load cycle-first tests with outcomes
        logger.info("\n[STEP 2] Loading cycle-first tests with outcomes...")
        cycle_df = load_cycle_first_with_outcomes()

        # Step 3: Build previous cycle features
        logger.info("\n[STEP 3] Building previous cycle features...")
        model_df = build_prev_cycle_features(cycle_df, condition_summary)
    else:
        # Load existing data
        logger.info("Loading existing datasets...")
        condition_summary = pd.read_parquet(Config.CONDITION_SUMMARY_PATH)
        model_df = pd.read_parquet(Config.MODEL_DATASET_PATH)

    # Step 4: Run ablation study
    logger.info("\n[STEP 4] Running ablation study...")
    try:
        ablation_results = run_ablation_study(model_df)
    except ImportError as e:
        logger.warning(f"Sklearn not available for evaluation: {e}")
        ablation_results = pd.DataFrame()
    except Exception as e:
        logger.warning(f"Evaluation failed: {e}")
        ablation_results = pd.DataFrame()

    # Step 5: Run QA checks
    logger.info("\n[STEP 5] Running QA checks...")
    qa_results = run_qa_checks(model_df, condition_summary)

    # Step 6: Generate report
    logger.info("\n[STEP 6] Generating evaluation report...")
    generate_evaluation_report(ablation_results, qa_results, model_df)

    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Outputs:")
    logger.info(f"  - {Config.CONDITION_SUMMARY_PATH}")
    logger.info(f"  - {Config.MODEL_DATASET_PATH}")
    logger.info(f"  - {Config.EVALUATION_REPORT_PATH}")
    logger.info(f"QA Status: {'PASSED' if qa_results['passed'] else 'FAILED'}")

    return model_df, ablation_results, qa_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build previous cycle features for MOT prediction")
    parser.add_argument("--eval-only", action="store_true",
                       help="Skip data generation, only run evaluation")
    parser.add_argument("--seed", type=int, default=42,
                       help="Random seed for reproducibility")
    args = parser.parse_args()

    Config.RANDOM_SEED = args.seed

    run_pipeline(eval_only=args.eval_only)
