"""
AutoSafe Model Validation Script

Time-split validation using 2022 data for training and 2023 data for validation.
Computes discrimination and calibration metrics, with segment-level analysis.
"""

import pandas as pd
import numpy as np
import logging
import os
from typing import Dict, Tuple, Optional, Set
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, log_loss
from sklearn.calibration import calibration_curve
from utils import get_age_band, get_mileage_band

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("validation.log"),
        logging.StreamHandler()
    ]
)

class ValidationConfig:
    """Configuration for time-split validation."""
    
    # Training data: 2022
    TRAIN_SOURCE = ("MOT Test Results/2022", "|", "test_result_2022.csv")
    TRAIN_DEFECTS = ("MOT Test Failures/2022", "|", "test_item.csv")
    
    # Validation data: 2023
    VAL_SOURCE = ("MOT Test Results/2023", "|", "test_result.csv")
    VAL_DEFECTS = ("MOT Test Failures/2023", "|", "test_item.csv")
    
    # Smoothing parameters (matching production pipeline)
    K_MAKE = 5      # Shrinkage toward make average
    K_GLOBAL = 10   # Shrinkage toward global average
    
    # Processing
    CHUNK_SIZE = 500_000
    
    # Output
    RESULTS_FILE = "validation_results.json"
    
    # Cycle filter (removes retests for cleaner signal)
    USE_CYCLE_FILTER = True
    CYCLE_FILTER_INDEX = "cycle_first_tests.parquet"


def load_cycle_filter_ids() -> Optional[pd.Index]:
    """Load cycle-first test IDs from the pre-built index."""
    if not ValidationConfig.USE_CYCLE_FILTER:
        return None
    
    index_path = ValidationConfig.CYCLE_FILTER_INDEX
    if not os.path.exists(index_path):
        logging.warning(f"Cycle filter index not found: {index_path}")
        logging.warning("Running without cycle filter - AUC may be lower")
        return None
    
    logging.info(f"Loading cycle filter from {index_path}...")
    df = pd.read_parquet(index_path, columns=['test_id'])
    cycle_ids = pd.Index(df['test_id'].values)
    logging.info(f"Loaded {len(cycle_ids):,} cycle-first test IDs")
    return cycle_ids


def load_year_data(results_source: Tuple, chunk_size: int = 500_000, 
                   cycle_filter_ids: Optional[pd.Index] = None) -> pd.DataFrame:
    """Load test results for a year, computing key features.
    
    Args:
        results_source: Tuple of (folder, separator, filename)
        chunk_size: Number of rows per chunk
        cycle_filter_ids: Optional pd.Index of test IDs to keep (cycle-first tests)
    """
    folder, sep, filename = results_source
    filepath = os.path.join(folder, filename)
    
    if not os.path.exists(filepath):
        logging.error(f"File not found: {filepath}")
        return pd.DataFrame()
    
    logging.info(f"Loading {filepath}...")
    cols = ['test_id', 'test_date', 'first_use_date', 'test_mileage', 'make', 'model', 'test_result']
    
    chunks = []
    total_raw = 0
    total_filtered = 0
    
    for chunk in pd.read_csv(filepath, sep=sep, usecols=cols, chunksize=chunk_size, low_memory=False):
        # Compute features
        chunk['test_id'] = pd.to_numeric(chunk['test_id'], errors='coerce').fillna(0).astype('int64')
        total_raw += len(chunk)
        
        # Apply cycle filter if provided
        if cycle_filter_ids is not None:
            chunk = chunk[chunk['test_id'].isin(cycle_filter_ids)]
            if chunk.empty:
                continue
        total_filtered += len(chunk)
        
        chunk['test_date'] = pd.to_datetime(chunk['test_date'], errors='coerce')
        chunk['first_use_date'] = pd.to_datetime(chunk['first_use_date'], errors='coerce')
        chunk['age_years'] = (chunk['test_date'] - chunk['first_use_date']).dt.days / 365.25
        chunk['age_band'] = chunk['age_years'].apply(get_age_band)
        chunk['test_mileage'] = pd.to_numeric(chunk['test_mileage'], errors='coerce')
        chunk['mileage_band'] = chunk['test_mileage'].apply(get_mileage_band)
        chunk['model_id'] = chunk['make'].astype(str) + " " + chunk['model'].astype(str)
        chunk['is_failure'] = chunk['test_result'].isin(['F', 'FAIL', 'PRS', 'ABA']).astype(int)
        
        # Keep only needed columns
        chunk = chunk[['test_id', 'model_id', 'make', 'age_band', 'mileage_band', 'is_failure']]
        chunks.append(chunk)
    
    result = pd.concat(chunks, ignore_index=True)
    
    if cycle_filter_ids is not None:
        filter_pct = (1 - total_filtered / total_raw) * 100 if total_raw > 0 else 0
        logging.info(f"Loaded {len(result):,} tests ({filter_pct:.1f}% filtered as retests)")
    else:
        logging.info(f"Loaded {len(result):,} tests from {filepath}")
    
    return result


def compute_risk_estimates(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict, float]:
    """
    Compute hierarchical Bayesian risk estimates from training data.
    Returns: (segment_risks_df, make_rates_dict, global_rate)
    """
    logging.info("Computing hierarchical risk estimates...")
    
    # Global rate
    global_total = len(df)
    global_failures = df['is_failure'].sum()
    global_rate = global_failures / global_total if global_total > 0 else 0
    logging.info(f"Global failure rate: {global_rate:.4f} ({global_failures:,}/{global_total:,})")
    
    # Make-level rates (shrunk toward global)
    make_stats = df.groupby('make').agg(
        total=('is_failure', 'count'),
        failures=('is_failure', 'sum')
    ).reset_index()
    
    make_stats['make_rate'] = (
        (make_stats['failures'] + ValidationConfig.K_GLOBAL * global_rate) /
        (make_stats['total'] + ValidationConfig.K_GLOBAL)
    )
    make_rates = dict(zip(make_stats['make'], make_stats['make_rate']))
    
    # Segment-level rates (shrunk toward make rate)
    segment_stats = df.groupby(['model_id', 'age_band', 'mileage_band', 'make']).agg(
        total=('is_failure', 'count'),
        failures=('is_failure', 'sum')
    ).reset_index()
    
    segment_stats['make_rate'] = segment_stats['make'].map(make_rates)
    segment_stats['predicted_risk'] = (
        (segment_stats['failures'] + ValidationConfig.K_MAKE * segment_stats['make_rate']) /
        (segment_stats['total'] + ValidationConfig.K_MAKE)
    )
    
    logging.info(f"Computed risk estimates for {len(segment_stats):,} segments")
    
    return segment_stats, make_rates, global_rate


def evaluate_predictions(y_true: np.ndarray, y_pred: np.ndarray, label: str = "Model") -> Dict:
    """Compute discrimination and calibration metrics."""
    metrics = {}
    
    # Check for valid predictions
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        logging.warning(f"{label}: Not enough variation in labels for metrics")
        return metrics
    
    # Discrimination metrics
    try:
        metrics['auc_roc'] = roc_auc_score(y_true, y_pred)
    except Exception as e:
        logging.warning(f"AUC-ROC failed: {e}")
        metrics['auc_roc'] = None
    
    try:
        metrics['auc_pr'] = average_precision_score(y_true, y_pred)
    except Exception as e:
        logging.warning(f"AUC-PR failed: {e}")
        metrics['auc_pr'] = None
    
    # Calibration metrics
    try:
        metrics['brier_score'] = brier_score_loss(y_true, y_pred)
    except Exception as e:
        logging.warning(f"Brier score failed: {e}")
        metrics['brier_score'] = None
    
    try:
        # Clamp predictions to avoid log(0)
        y_pred_clamped = np.clip(y_pred, 1e-10, 1 - 1e-10)
        metrics['log_loss'] = log_loss(y_true, y_pred_clamped)
    except Exception as e:
        logging.warning(f"Log loss failed: {e}")
        metrics['log_loss'] = None
    
    # Calibration curve
    try:
        prob_true, prob_pred = calibration_curve(y_true, y_pred, n_bins=10, strategy='uniform')
        metrics['calibration_curve'] = {
            'prob_true': prob_true.tolist(),
            'prob_pred': prob_pred.tolist()
        }
    except Exception as e:
        logging.warning(f"Calibration curve failed: {e}")
        metrics['calibration_curve'] = None
    
    return metrics


def print_metrics_table(results: Dict[str, Dict]) -> None:
    """Print a formatted comparison table of metrics."""
    print("\n" + "="*70)
    print("DISCRIMINATION & CALIBRATION METRICS")
    print("="*70)
    print(f"{'Model':<25} {'AUC-ROC':>10} {'AUC-PR':>10} {'Brier':>10} {'Log Loss':>10}")
    print("-"*70)
    
    for model_name, metrics in results.items():
        auc_roc = f"{metrics.get('auc_roc', 0):.4f}" if metrics.get('auc_roc') else "N/A"
        auc_pr = f"{metrics.get('auc_pr', 0):.4f}" if metrics.get('auc_pr') else "N/A"
        brier = f"{metrics.get('brier_score', 0):.4f}" if metrics.get('brier_score') else "N/A"
        logloss = f"{metrics.get('log_loss', 0):.4f}" if metrics.get('log_loss') else "N/A"
        print(f"{model_name:<25} {auc_roc:>10} {auc_pr:>10} {brier:>10} {logloss:>10}")
    
    print("="*70)


def print_segment_analysis(val_df: pd.DataFrame, segment_col: str, col_label: str) -> None:
    """Print segment-level metrics breakdown."""
    print(f"\n{'='*70}")
    print(f"SEGMENT ANALYSIS BY {col_label.upper()}")
    print("="*70)
    print(f"{col_label:<20} {'Count':>12} {'Fail Rate':>10} {'Pred':>10} {'AUC':>8} {'Brier':>8}")
    print("-"*70)
    
    segments = val_df.groupby(segment_col).agg(
        count=('is_failure', 'count'),
        actual_rate=('is_failure', 'mean'),
        pred_rate=('predicted_risk', 'mean')
    ).reset_index()
    
    # Only show segments with sufficient data
    segments = segments[segments['count'] >= 1000].sort_values('count', ascending=False)
    
    for _, row in segments.head(15).iterrows():
        segment_data = val_df[val_df[segment_col] == row[segment_col]]
        
        if len(segment_data) > 100 and len(segment_data['is_failure'].unique()) > 1:
            try:
                auc = roc_auc_score(segment_data['is_failure'], segment_data['predicted_risk'])
                brier = brier_score_loss(segment_data['is_failure'], segment_data['predicted_risk'])
                auc_str = f"{auc:.3f}"
                brier_str = f"{brier:.4f}"
            except:
                auc_str = "N/A"
                brier_str = "N/A"
        else:
            auc_str = "N/A"
            brier_str = "N/A"
        
        print(f"{str(row[segment_col])[:20]:<20} {row['count']:>12,} {row['actual_rate']:>10.3f} {row['pred_rate']:>10.3f} {auc_str:>8} {brier_str:>8}")
    
    print("="*70)


def run_validation():
    """Main validation pipeline."""
    logging.info("="*60)
    logging.info("AUTOSAFE MODEL VALIDATION - TIME-SPLIT BACKTEST")
    logging.info("Training: 2022, Validation: 2023")
    if ValidationConfig.USE_CYCLE_FILTER:
        logging.info("Cycle Filter: ENABLED (removes retests)")
    else:
        logging.info("Cycle Filter: DISABLED")
    logging.info("="*60)
    
    # Load cycle filter IDs (if enabled)
    cycle_filter_ids = load_cycle_filter_ids()
    
    # 1. Load training data (2022)
    logging.info("\n[1/5] Loading training data (2022)...")
    train_df = load_year_data(ValidationConfig.TRAIN_SOURCE, ValidationConfig.CHUNK_SIZE, cycle_filter_ids)
    if train_df.empty:
        logging.error("Failed to load training data")
        return
    
    # 2. Compute risk estimates from training data
    logging.info("\n[2/5] Computing risk estimates from training data...")
    segment_risks, make_rates, global_rate = compute_risk_estimates(train_df)
    
    logging.info(f"Unique makes in training: {len(make_rates)}")
    
    # Free training data memory
    del train_df
    
    # 3. Load validation data (2023)
    logging.info("\n[3/5] Loading validation data (2023)...")
    val_df = load_year_data(ValidationConfig.VAL_SOURCE, ValidationConfig.CHUNK_SIZE, cycle_filter_ids)
    if val_df.empty:
        logging.error("Failed to load validation data")
        return
    
    # 4. Apply predictions to validation data
    logging.info("\n[4/5] Applying predictions to validation data...")
    
    # Prepare segment lookup with just the key columns
    segment_lookup = segment_risks[['model_id', 'age_band', 'mileage_band', 'predicted_risk']].copy()
    
    # Vectorized merge for segment-level predictions
    logging.info("Merging segment-level predictions...")
    val_df = val_df.merge(
        segment_lookup,
        on=['model_id', 'age_band', 'mileage_band'],
        how='left'
    )
    
    # Fill missing segment predictions with make-level rate
    logging.info("Filling missing segments with make-level rates...")
    make_rate_df = pd.DataFrame(list(make_rates.items()), columns=['make', 'make_rate'])
    val_df = val_df.merge(make_rate_df, on='make', how='left')
    val_df['make_rate'] = val_df['make_rate'].fillna(global_rate)
    
    # Fill NaN predictions with make rate, then global rate
    val_df['predicted_risk'] = val_df['predicted_risk'].fillna(val_df['make_rate'])
    
    # Baseline predictions
    val_df['pred_global'] = global_rate
    
    # Age-only baseline (vectorized)
    age_rates = segment_risks.groupby('age_band').apply(
        lambda x: x['failures'].sum() / x['total'].sum()
    ).to_dict()
    val_df['pred_age_only'] = val_df['age_band'].map(age_rates).fillna(global_rate)
    
    # Make-only baseline already computed
    val_df['pred_make_only'] = val_df['make_rate']
    
    logging.info(f"Predictions applied. Coverage: {(val_df['predicted_risk'] != global_rate).mean():.1%}")
    
    # 5. Compute metrics
    logging.info("\n[5/5] Computing validation metrics...")
    
    y_true = val_df['is_failure'].values
    
    results = {}
    results['Full Model'] = evaluate_predictions(y_true, val_df['predicted_risk'].values, "Full Model")
    results['Global Rate Baseline'] = evaluate_predictions(y_true, val_df['pred_global'].values, "Global Rate")
    results['Age-Only Baseline'] = evaluate_predictions(y_true, val_df['pred_age_only'].values, "Age-Only")
    results['Make-Only Baseline'] = evaluate_predictions(y_true, val_df['pred_make_only'].values, "Make-Only")
    
    # Print results
    print_metrics_table(results)
    
    # Segment analysis
    print_segment_analysis(val_df, 'age_band', 'Age Band')
    print_segment_analysis(val_df, 'make', 'Make')
    
    # Summary stats
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)
    print(f"Training samples (2022):   {len(segment_risks):,} segments")
    print(f"Validation samples (2023): {len(val_df):,} tests")
    print(f"Global failure rate:       {global_rate:.4f}")
    print(f"Validation failure rate:   {val_df['is_failure'].mean():.4f}")
    
    # Coverage stats
    coverage = val_df['predicted_risk'].apply(lambda x: x != global_rate).mean()
    print(f"Segment coverage:          {coverage:.1%}")
    
    # Lift calculation
    model_auc = results['Full Model'].get('auc_roc', 0.5)
    baseline_auc = results['Global Rate Baseline'].get('auc_roc', 0.5)
    if model_auc and baseline_auc:
        lift = (model_auc - 0.5) / (baseline_auc - 0.5) if baseline_auc > 0.5 else float('inf')
        print(f"AUC Lift over random:      {model_auc - 0.5:.4f}")
    
    print("="*70)
    
    # Save results to JSON
    import json
    with open(ValidationConfig.RESULTS_FILE, 'w') as f:
        # Convert numpy types for JSON serialization
        serializable_results = {}
        for model_name, metrics in results.items():
            serializable_results[model_name] = {
                k: float(v) if isinstance(v, (np.float32, np.float64)) else v
                for k, v in metrics.items()
                if k != 'calibration_curve'  # Skip array data
            }
        serializable_results['metadata'] = {
            'training_year': 2022,
            'validation_year': 2023,
            'validation_samples': len(val_df),
            'global_rate': float(global_rate),
            'segment_coverage': float(coverage)
        }
        json.dump(serializable_results, f, indent=2)
    
    logging.info(f"Results saved to {ValidationConfig.RESULTS_FILE}")
    
    return results, val_df


if __name__ == "__main__":
    run_validation()
