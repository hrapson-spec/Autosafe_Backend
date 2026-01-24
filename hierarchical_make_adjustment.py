"""
Hierarchical Make Adjustment Features
======================================

Computes hierarchical Bayesian smoothed rates for use as CatBoost features.
Implements two-level shrinkage from calculate_risk_duckdb.py methodology.

Classes:
1. HierarchicalFeatures: Original Make+Age+Mileage segment hierarchy
2. ModelHierarchicalFeatures: V6 Model-level hierarchy (Global → Make → Model)
3. RegimeAwareHierarchicalFeatures: V9 Regime-aware hierarchy (Global → Regime → Make → Segment)

Features computed:
1. make_fail_rate_smoothed: Make-level smoothed failure rate
2. segment_fail_rate_smoothed: Segment-level (make+age+mileage) smoothed rate
3. model_fail_rate_smoothed: Model-level (FORD FOCUS vs FORD TRANSIT) smoothed rate
4. regime_fail_rate: Regime-level (Car/Motorcycle/Commercial) rate (V9)
4. make_x_age_band: Interaction feature (categorical)
5. make_x_prev_outcome: Interaction feature (categorical)

Shrinkage Parameters:
- K_GLOBAL = 10: Shrinkage toward global average for make-level estimates
- K_MAKE/K_SEGMENT = 5: Shrinkage toward make average for segment-level estimates
- K_MODEL = 20: Shrinkage toward make average for model-level estimates (V6)
- K_REGIME = 10: Shrinkage toward regime average for make-level estimates (V9)

Usage:
    # Original (V2-V5):
    from hierarchical_make_adjustment import HierarchicalFeatures
    hf = HierarchicalFeatures()

    # V6 Model-level:
    from hierarchical_make_adjustment import ModelHierarchicalFeatures
    mhf = ModelHierarchicalFeatures(k_model=20, min_model_count=50)
    mhf.fit(train_df, model_col='model_id')
    train_df = mhf.transform(train_df, model_col='model_id')

    # V9 Regime-aware:
    from hierarchical_make_adjustment import RegimeAwareHierarchicalFeatures
    rhf = RegimeAwareHierarchicalFeatures()
    rhf.fit(train_df, target_col='is_failure', make_col='make')
    train_df = rhf.transform(train_df, make_col='make')

Created: 2026-01-04
Updated: 2026-01-05 - Added ModelHierarchicalFeatures for V6
Updated: 2026-01-08 - Added RegimeAwareHierarchicalFeatures for V9
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional, Set
import pickle
from pathlib import Path

from regime_definitions import infer_regime, infer_powertrain, REGIMES, POWERTRAINS


class HierarchicalFeatures:
    """Compute and apply hierarchical Bayesian smoothed features."""

    # Default shrinkage parameters (from calculate_risk_duckdb.py)
    DEFAULT_K_GLOBAL = 10  # Shrinkage toward global average
    DEFAULT_K_SEGMENT = 5  # Shrinkage toward make average

    def __init__(self, k_global: int = None, k_segment: int = None):
        """
        Initialize with configurable shrinkage parameters.

        Args:
            k_global: Shrinkage toward global average (default 10)
            k_segment: Shrinkage toward make average for segments (default 5)
                       Higher K = more shrinkage toward parent mean
                       K=100 "weakens" segment specificity, forces model to use other features
        """
        self.K_GLOBAL = k_global if k_global is not None else self.DEFAULT_K_GLOBAL
        self.K_MAKE = k_segment if k_segment is not None else self.DEFAULT_K_SEGMENT
        self.global_fail_rate: float = None
        self.make_rates: Dict[str, float] = None
        self.segment_rates: Dict[Tuple[str, str, str], float] = None
        self.fitted = False

    def fit(self, df: pd.DataFrame,
            target_col: str = 'target',
            make_col: str = 'make',
            age_band_col: str = 'age_band',
            mileage_band_col: str = 'mileage_band') -> 'HierarchicalFeatures':
        """
        Fit hierarchical rates from training data.

        Args:
            df: Training dataframe with target and grouping columns
            target_col: Name of binary target column (0/1 or boolean)
            make_col: Name of make column
            age_band_col: Name of age band column (will be created if missing)
            mileage_band_col: Name of mileage band column (will be created if missing)

        Returns:
            self (fitted)
        """
        df = df.copy()

        # Create age_band if not present (from test_mileage and days_since_last_test proxy)
        if age_band_col not in df.columns:
            df[age_band_col] = self._compute_age_band(df)

        # Create mileage_band if not present
        if mileage_band_col not in df.columns:
            df[mileage_band_col] = self._compute_mileage_band(df)

        # Step 1: Global failure rate
        total_tests = len(df)
        total_failures = df[target_col].sum()
        self.global_fail_rate = total_failures / total_tests if total_tests > 0 else 0.0

        # Step 2: Make-level smoothed rates
        make_stats = df.groupby(make_col).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        make_stats.columns = [make_col, 'failures', 'tests']

        # Apply shrinkage toward global
        make_stats['smoothed_rate'] = (
            (make_stats['failures'] + self.K_GLOBAL * self.global_fail_rate) /
            (make_stats['tests'] + self.K_GLOBAL)
        )

        self.make_rates = dict(zip(make_stats[make_col], make_stats['smoothed_rate']))

        # Step 3: Segment-level smoothed rates (make + age_band + mileage_band)
        segment_stats = df.groupby([make_col, age_band_col, mileage_band_col]).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        segment_stats.columns = [make_col, age_band_col, mileage_band_col, 'failures', 'tests']

        # Get make rate for each segment
        segment_stats['make_rate'] = segment_stats[make_col].map(self.make_rates)

        # Apply shrinkage toward make rate
        segment_stats['smoothed_rate'] = (
            (segment_stats['failures'] + self.K_MAKE * segment_stats['make_rate']) /
            (segment_stats['tests'] + self.K_MAKE)
        )

        # Create lookup dictionary
        self.segment_rates = {}
        for _, row in segment_stats.iterrows():
            key = (row[make_col], row[age_band_col], row[mileage_band_col])
            self.segment_rates[key] = row['smoothed_rate']

        self.fitted = True

        print(f"[HierarchicalFeatures] Fitted on {total_tests:,} samples")
        print(f"  K_GLOBAL={self.K_GLOBAL}, K_SEGMENT={self.K_MAKE}")
        print(f"  Global failure rate: {self.global_fail_rate:.4f}")
        print(f"  Make-level rates: {len(self.make_rates)} makes")
        print(f"  Segment-level rates: {len(self.segment_rates)} segments")

        return self

    def transform(self, df: pd.DataFrame,
                  make_col: str = 'make',
                  age_band_col: str = 'age_band',
                  mileage_band_col: str = 'mileage_band',
                  prev_outcome_col: str = 'prev_cycle_outcome_band') -> pd.DataFrame:
        """
        Add hierarchical features to dataframe.

        Args:
            df: Dataframe to transform
            make_col: Name of make column
            age_band_col: Name of age band column
            mileage_band_col: Name of mileage band column
            prev_outcome_col: Name of previous outcome column (for interaction)

        Returns:
            DataFrame with added features
        """
        if not self.fitted:
            raise ValueError("HierarchicalFeatures not fitted. Call fit() first.")

        df = df.copy()

        # Create age_band if not present
        if age_band_col not in df.columns:
            df[age_band_col] = self._compute_age_band(df)

        # Create mileage_band if not present
        if mileage_band_col not in df.columns:
            df[mileage_band_col] = self._compute_mileage_band(df)

        # Feature 1: Make-level smoothed rate
        df['make_fail_rate_smoothed'] = df[make_col].map(self.make_rates)
        # Fallback to global rate for unseen makes
        df['make_fail_rate_smoothed'] = df['make_fail_rate_smoothed'].fillna(self.global_fail_rate)

        # Feature 2: Segment-level smoothed rate
        def get_segment_rate(row):
            key = (row[make_col], row[age_band_col], row[mileage_band_col])
            if key in self.segment_rates:
                return self.segment_rates[key]
            # Fallback to make rate
            return self.make_rates.get(row[make_col], self.global_fail_rate)

        df['segment_fail_rate_smoothed'] = df.apply(get_segment_rate, axis=1)

        # Feature 3: Make x Age Band interaction
        df['make_x_age_band'] = df[make_col].astype(str) + '_' + df[age_band_col].astype(str)

        # Feature 4: Make x Previous Outcome interaction
        if prev_outcome_col in df.columns:
            df['make_x_prev_outcome'] = df[make_col].astype(str) + '_' + df[prev_outcome_col].astype(str)
        else:
            df['make_x_prev_outcome'] = df[make_col].astype(str) + '_UNKNOWN'

        return df

    def fit_transform(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """Fit and transform in one step."""
        self.fit(df, **kwargs)
        return self.transform(df, **kwargs)

    def _compute_age_band(self, df: pd.DataFrame) -> pd.Series:
        """Compute age_band from days_since_last_test or n_prior_tests proxy."""
        # Use n_prior_tests as proxy for vehicle age (more tests = older vehicle)
        if 'n_prior_tests' in df.columns:
            n_tests = df['n_prior_tests'].fillna(0)
            return pd.cut(
                n_tests,
                bins=[-1, 0, 2, 5, 10, float('inf')],
                labels=['0-2', '3-5', '6-10', '11-15', '15+']
            ).astype(str)
        else:
            return pd.Series(['Unknown'] * len(df), index=df.index)

    def _compute_mileage_band(self, df: pd.DataFrame) -> pd.Series:
        """Compute mileage_band from test_mileage."""
        if 'test_mileage' in df.columns:
            mileage = df['test_mileage'].fillna(50000)
            return pd.cut(
                mileage,
                bins=[-1, 30000, 60000, 100000, float('inf')],
                labels=['0-30k', '30k-60k', '60k-100k', '100k+']
            ).astype(str)
        else:
            return pd.Series(['Unknown'] * len(df), index=df.index)

    def save(self, path: Path):
        """Save fitted rates to file."""
        if not self.fitted:
            raise ValueError("Cannot save unfitted HierarchicalFeatures")

        with open(path, 'wb') as f:
            pickle.dump({
                'global_fail_rate': self.global_fail_rate,
                'make_rates': self.make_rates,
                'segment_rates': self.segment_rates,
                'K_GLOBAL': self.K_GLOBAL,
                'K_MAKE': self.K_MAKE,
            }, f)
        print(f"[HierarchicalFeatures] Saved to {path}")

    @classmethod
    def load(cls, path: Path) -> 'HierarchicalFeatures':
        """Load fitted rates from file."""
        with open(path, 'rb') as f:
            data = pickle.load(f)

        hf = cls()
        hf.global_fail_rate = data['global_fail_rate']
        hf.make_rates = data['make_rates']
        hf.segment_rates = data['segment_rates']
        hf.fitted = True

        print(f"[HierarchicalFeatures] Loaded from {path}")
        return hf

    def get_summary(self) -> dict:
        """Get summary statistics for audit/documentation."""
        if not self.fitted:
            return {'fitted': False}

        return {
            'fitted': True,
            'global_fail_rate': self.global_fail_rate,
            'n_makes': len(self.make_rates),
            'n_segments': len(self.segment_rates),
            'K_GLOBAL': self.K_GLOBAL,
            'K_MAKE': self.K_MAKE,
            'make_rate_range': (
                min(self.make_rates.values()),
                max(self.make_rates.values())
            ),
            'segment_rate_range': (
                min(self.segment_rates.values()),
                max(self.segment_rates.values())
            ),
        }


# Convenience functions for backward compatibility
def compute_make_intercepts(df: pd.DataFrame,
                            target_col: str = 'target',
                            make_col: str = 'make',
                            k_global: float = 10) -> Dict[str, float]:
    """
    Compute make-level smoothed failure rates.

    Standalone function for simple use cases.
    """
    total = len(df)
    global_rate = df[target_col].sum() / total if total > 0 else 0.0

    make_stats = df.groupby(make_col).agg({
        target_col: ['sum', 'count']
    }).reset_index()
    make_stats.columns = [make_col, 'failures', 'tests']

    make_stats['rate'] = (
        (make_stats['failures'] + k_global * global_rate) /
        (make_stats['tests'] + k_global)
    )

    return dict(zip(make_stats[make_col], make_stats['rate']))


def add_interaction_features(df: pd.DataFrame,
                             make_col: str = 'make',
                             age_band_col: str = 'age_band',
                             prev_outcome_col: str = 'prev_cycle_outcome_band') -> pd.DataFrame:
    """
    Add interaction feature columns to dataframe.

    Standalone function for simple use cases.
    """
    df = df.copy()
    df['make_x_age_band'] = df[make_col].astype(str) + '_' + df[age_band_col].astype(str)
    if prev_outcome_col in df.columns:
        df['make_x_prev_outcome'] = df[make_col].astype(str) + '_' + df[prev_outcome_col].astype(str)
    return df


class ModelHierarchicalFeatures:
    """
    V6 Model-Level Hierarchical Features.

    Computes: Global → Make → Model hierarchy
    Replaces segment_fail_rate_smoothed with model_fail_rate_smoothed.

    The key insight: model_id (e.g., "FORD FOCUS") distinguishes between
    city cars (FORD KA) and commercial vehicles (FORD TRANSIT) within the same make.
    """

    DEFAULT_K_GLOBAL = 10
    DEFAULT_K_MODEL = 20
    DEFAULT_MIN_MODEL_COUNT = 50

    def __init__(self, k_global: int = None, k_model: int = None, min_model_count: int = None):
        """
        Initialize with configurable shrinkage parameters.

        Args:
            k_global: Shrinkage toward global average for make (default 10)
            k_model: Shrinkage toward make average for model (default 20)
            min_model_count: Minimum samples for model-specific rate (default 50)
                            Models with fewer samples fallback to make rate
        """
        self.K_GLOBAL = k_global if k_global is not None else self.DEFAULT_K_GLOBAL
        self.K_MODEL = k_model if k_model is not None else self.DEFAULT_K_MODEL
        self.MIN_MODEL_COUNT = min_model_count if min_model_count is not None else self.DEFAULT_MIN_MODEL_COUNT

        self.global_fail_rate: float = None
        self.make_rates: Dict[str, float] = None
        self.model_rates: Dict[str, float] = None
        self.model_counts: Dict[str, int] = None
        self.model_to_make: Dict[str, str] = None
        self.fitted = False

    def fit(self, df: pd.DataFrame,
            target_col: str = 'target',
            model_col: str = 'model_id') -> 'ModelHierarchicalFeatures':
        """
        Fit hierarchical rates from training data.

        Args:
            df: Training dataframe with target and model columns
            target_col: Name of binary target column (0/1 or boolean)
            model_col: Name of model column (format: "MAKE MODEL", e.g., "FORD FOCUS")

        Returns:
            self (fitted)
        """
        df = df.copy()

        # Extract make from model_id (first word)
        df['_make'] = df[model_col].str.split().str[0]

        # Step 1: Global failure rate
        total_tests = len(df)
        total_failures = df[target_col].sum()
        self.global_fail_rate = total_failures / total_tests if total_tests > 0 else 0.0

        # Step 2: Make-level smoothed rates (Global → Make)
        make_stats = df.groupby('_make').agg({
            target_col: ['sum', 'count']
        }).reset_index()
        make_stats.columns = ['make', 'failures', 'tests']

        make_stats['smoothed_rate'] = (
            (make_stats['failures'] + self.K_GLOBAL * self.global_fail_rate) /
            (make_stats['tests'] + self.K_GLOBAL)
        )

        self.make_rates = dict(zip(make_stats['make'], make_stats['smoothed_rate']))

        # Step 3: Model-level smoothed rates (Make → Model)
        model_stats = df.groupby([model_col, '_make']).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        model_stats.columns = [model_col, 'make', 'failures', 'tests']

        # Get make rate for each model
        model_stats['make_rate'] = model_stats['make'].map(self.make_rates)

        # Apply shrinkage toward make rate
        model_stats['smoothed_rate'] = (
            (model_stats['failures'] + self.K_MODEL * model_stats['make_rate']) /
            (model_stats['tests'] + self.K_MODEL)
        )

        # Store model rates, counts, and model→make mapping
        self.model_rates = dict(zip(model_stats[model_col], model_stats['smoothed_rate']))
        self.model_counts = dict(zip(model_stats[model_col], model_stats['tests']))
        self.model_to_make = dict(zip(model_stats[model_col], model_stats['make']))

        # Count rare models
        rare_models = sum(1 for c in self.model_counts.values() if c < self.MIN_MODEL_COUNT)

        self.fitted = True

        print(f"[ModelHierarchicalFeatures] Fitted on {total_tests:,} samples")
        print(f"  K_GLOBAL={self.K_GLOBAL}, K_MODEL={self.K_MODEL}, MIN_COUNT={self.MIN_MODEL_COUNT}")
        print(f"  Global failure rate: {self.global_fail_rate:.4f}")
        print(f"  Make-level rates: {len(self.make_rates)} makes")
        print(f"  Model-level rates: {len(self.model_rates)} models ({rare_models} rare, will fallback)")

        return self

    def transform(self, df: pd.DataFrame,
                  model_col: str = 'model_id',
                  prev_outcome_col: str = 'prev_cycle_outcome_band') -> pd.DataFrame:
        """
        Add model-level hierarchical features to dataframe.

        Args:
            df: Dataframe to transform
            model_col: Name of model column
            prev_outcome_col: Name of previous outcome column (for interaction)

        Returns:
            DataFrame with added features
        """
        if not self.fitted:
            raise ValueError("ModelHierarchicalFeatures not fitted. Call fit() first.")

        df = df.copy()

        # Extract make from model_id
        df['_make'] = df[model_col].str.split().str[0]

        # Feature 1: Make-level smoothed rate
        df['make_fail_rate_smoothed'] = df['_make'].map(self.make_rates)
        df['make_fail_rate_smoothed'] = df['make_fail_rate_smoothed'].fillna(self.global_fail_rate)

        # Feature 2: Model-level smoothed rate
        # V6.1: Removed MIN_MODEL_COUNT gate - trust Bayesian smoothing
        # K=20 naturally handles variance: rare models shrink to make rate,
        # common models express their true rate
        def get_model_rate(row):
            model = row[model_col]
            make = row['_make']

            # Use smoothed rate if model was seen in training
            if model in self.model_rates:
                return self.model_rates[model]

            # Fallback only for truly unseen models (not in training set)
            return self.make_rates.get(make, self.global_fail_rate)

        df['model_fail_rate_smoothed'] = df.apply(get_model_rate, axis=1)

        # Feature 3: Model x Previous Outcome interaction (optional)
        if prev_outcome_col in df.columns:
            df['model_x_prev_outcome'] = df[model_col].astype(str) + '_' + df[prev_outcome_col].astype(str)
        else:
            df['model_x_prev_outcome'] = df[model_col].astype(str) + '_UNKNOWN'

        # Clean up temp column
        df = df.drop(columns=['_make'])

        return df

    def fit_transform(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """Fit and transform in one step."""
        self.fit(df, **kwargs)
        return self.transform(df, **kwargs)

    def save(self, path: Path):
        """Save fitted rates to file."""
        if not self.fitted:
            raise ValueError("Cannot save unfitted ModelHierarchicalFeatures")

        with open(path, 'wb') as f:
            pickle.dump({
                'global_fail_rate': self.global_fail_rate,
                'make_rates': self.make_rates,
                'model_rates': self.model_rates,
                'model_counts': self.model_counts,
                'model_to_make': self.model_to_make,
                'K_GLOBAL': self.K_GLOBAL,
                'K_MODEL': self.K_MODEL,
                'MIN_MODEL_COUNT': self.MIN_MODEL_COUNT,
            }, f)
        print(f"[ModelHierarchicalFeatures] Saved to {path}")

    @classmethod
    def load(cls, path: Path) -> 'ModelHierarchicalFeatures':
        """Load fitted rates from file."""
        with open(path, 'rb') as f:
            data = pickle.load(f)

        mhf = cls()
        mhf.global_fail_rate = data['global_fail_rate']
        mhf.make_rates = data['make_rates']
        mhf.model_rates = data['model_rates']
        mhf.model_counts = data['model_counts']
        mhf.model_to_make = data['model_to_make']
        mhf.K_GLOBAL = data['K_GLOBAL']
        mhf.K_MODEL = data['K_MODEL']
        mhf.MIN_MODEL_COUNT = data['MIN_MODEL_COUNT']
        mhf.fitted = True

        print(f"[ModelHierarchicalFeatures] Loaded from {path}")
        return mhf

    def get_summary(self) -> dict:
        """Get summary statistics for audit/documentation."""
        if not self.fitted:
            return {'fitted': False}

        rare_models = sum(1 for c in self.model_counts.values() if c < self.MIN_MODEL_COUNT)
        valid_models = len(self.model_counts) - rare_models

        return {
            'fitted': True,
            'global_fail_rate': self.global_fail_rate,
            'n_makes': len(self.make_rates),
            'n_models': len(self.model_rates),
            'n_valid_models': valid_models,
            'n_rare_models': rare_models,
            'K_GLOBAL': self.K_GLOBAL,
            'K_MODEL': self.K_MODEL,
            'MIN_MODEL_COUNT': self.MIN_MODEL_COUNT,
            'make_rate_range': (
                min(self.make_rates.values()),
                max(self.make_rates.values())
            ),
            'model_rate_range': (
                min(self.model_rates.values()),
                max(self.model_rates.values())
            ),
        }


class RegimeAwareHierarchicalFeatures:
    """
    V9/V10 Regime and Powertrain-Aware Hierarchical Bayesian Smoothing.

    Hierarchy: Global → Regime → Powertrain → Make → Segment

    Makes shrink toward their POWERTRAIN average within regime, which shrinks
    toward regime, which shrinks toward global. This provides proper calibration
    for HEV/PHEV/BEV vehicles which have different failure patterns than ICE.

    Analysis showed:
    - ICE: median AUC 0.6478
    - HEV: median AUC 0.6035 (-4.4pp from ICE)
    - BEV: median AUC 0.5857 (-6.2pp from ICE)

    The powertrain tier ensures TOYOTA YARIS HEV shrinks toward Car+HEV rate,
    not just Car rate.
    """

    DEFAULT_K_GLOBAL = 10      # Shrinkage: Global -> Regime
    DEFAULT_K_REGIME = 10      # Shrinkage: Regime -> Powertrain
    DEFAULT_K_POWERTRAIN = 10  # Shrinkage: Powertrain -> Make
    DEFAULT_K_SEGMENT = 5      # Shrinkage: Make -> Segment

    def __init__(self, k_global: int = None, k_regime: int = None,
                 k_powertrain: int = None, k_segment: int = None):
        """
        Initialize with configurable shrinkage parameters.

        Args:
            k_global: Shrinkage toward global for regime rates (default 10)
            k_regime: Shrinkage toward regime for powertrain rates (default 10)
            k_powertrain: Shrinkage toward powertrain for make rates (default 10)
            k_segment: Shrinkage toward make for segment rates (default 5)
        """
        self.K_GLOBAL = k_global if k_global is not None else self.DEFAULT_K_GLOBAL
        self.K_REGIME = k_regime if k_regime is not None else self.DEFAULT_K_REGIME
        self.K_POWERTRAIN = k_powertrain if k_powertrain is not None else self.DEFAULT_K_POWERTRAIN
        self.K_SEGMENT = k_segment if k_segment is not None else self.DEFAULT_K_SEGMENT

        # Fitted parameters
        self.global_fail_rate: float = None
        self.regime_rates: Dict[str, float] = None
        self.powertrain_rates: Dict[Tuple[str, str], float] = None  # (regime, powertrain) -> rate
        self.make_rates: Dict[str, float] = None
        self.make_to_regime: Dict[str, str] = None
        self.make_to_powertrain: Dict[str, str] = None
        self.segment_rates: Dict[Tuple[str, str, str], float] = None
        self.fitted = False

    def fit(self, df: pd.DataFrame,
            target_col: str = 'target',
            make_col: str = 'make',
            model_col: str = 'model_id',
            age_band_col: str = 'age_band',
            mileage_band_col: str = 'mileage_band') -> 'RegimeAwareHierarchicalFeatures':
        """
        Fit regime and powertrain-aware hierarchical rates from training data.

        Args:
            df: Training dataframe with target and grouping columns
            target_col: Name of binary target column (0/1 or boolean)
            make_col: Name of make column
            model_col: Name of model column (for powertrain inference)
            age_band_col: Name of age band column (will be created if missing)
            mileage_band_col: Name of mileage band column (will be created if missing)

        Returns:
            self (fitted)
        """
        df = df.copy()

        # Create age_band if not present
        if age_band_col not in df.columns:
            df[age_band_col] = self._compute_age_band(df)

        # Create mileage_band if not present
        if mileage_band_col not in df.columns:
            df[mileage_band_col] = self._compute_mileage_band(df)

        # Infer regime for each make
        df['_regime'] = df[make_col].apply(infer_regime)

        # Infer powertrain from model_id (or default to ICE if no model_col)
        if model_col in df.columns:
            df['_powertrain'] = df[model_col].apply(infer_powertrain)
        else:
            df['_powertrain'] = 'ICE'

        # =================================================================
        # Step 1: Global failure rate
        # =================================================================
        total_tests = len(df)
        total_failures = df[target_col].sum()
        self.global_fail_rate = total_failures / total_tests if total_tests > 0 else 0.0

        # =================================================================
        # Step 2: Regime-level smoothed rates (Global → Regime)
        # =================================================================
        regime_stats = df.groupby('_regime').agg({
            target_col: ['sum', 'count']
        }).reset_index()
        regime_stats.columns = ['regime', 'failures', 'tests']

        # Apply shrinkage toward global
        regime_stats['smoothed_rate'] = (
            (regime_stats['failures'] + self.K_GLOBAL * self.global_fail_rate) /
            (regime_stats['tests'] + self.K_GLOBAL)
        )

        self.regime_rates = dict(zip(regime_stats['regime'], regime_stats['smoothed_rate']))

        # Ensure all regimes have a rate (use global for missing)
        for regime in REGIMES:
            if regime not in self.regime_rates:
                self.regime_rates[regime] = self.global_fail_rate

        # =================================================================
        # Step 2b: Powertrain-level smoothed rates (Regime → Powertrain)
        # =================================================================
        pt_stats = df.groupby(['_regime', '_powertrain']).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        pt_stats.columns = ['regime', 'powertrain', 'failures', 'tests']

        # Get regime rate for each powertrain
        pt_stats['regime_rate'] = pt_stats['regime'].map(self.regime_rates)

        # Apply shrinkage toward regime rate
        pt_stats['smoothed_rate'] = (
            (pt_stats['failures'] + self.K_REGIME * pt_stats['regime_rate']) /
            (pt_stats['tests'] + self.K_REGIME)
        )

        self.powertrain_rates = {}
        for _, row in pt_stats.iterrows():
            key = (row['regime'], row['powertrain'])
            self.powertrain_rates[key] = row['smoothed_rate']

        # Ensure all regime+powertrain combos have a rate
        for regime in REGIMES:
            for pt in POWERTRAINS:
                key = (regime, pt)
                if key not in self.powertrain_rates:
                    self.powertrain_rates[key] = self.regime_rates.get(regime, self.global_fail_rate)

        # =================================================================
        # Step 3: Make-level smoothed rates (Powertrain → Make)
        # =================================================================
        make_stats = df.groupby([make_col, '_regime', '_powertrain']).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        make_stats.columns = [make_col, 'regime', 'powertrain', 'failures', 'tests']

        # Get powertrain rate for each make (fallback to regime rate)
        def get_pt_rate(row):
            key = (row['regime'], row['powertrain'])
            return self.powertrain_rates.get(key, self.regime_rates.get(row['regime'], self.global_fail_rate))

        make_stats['pt_rate'] = make_stats.apply(get_pt_rate, axis=1)

        # Apply shrinkage toward POWERTRAIN rate (not regime directly)
        make_stats['smoothed_rate'] = (
            (make_stats['failures'] + self.K_POWERTRAIN * make_stats['pt_rate']) /
            (make_stats['tests'] + self.K_POWERTRAIN)
        )

        self.make_rates = dict(zip(make_stats[make_col], make_stats['smoothed_rate']))
        self.make_to_regime = dict(zip(make_stats[make_col], make_stats['regime']))
        self.make_to_powertrain = dict(zip(make_stats[make_col], make_stats['powertrain']))

        # =================================================================
        # Step 4: Segment-level smoothed rates (Make → Segment)
        # =================================================================
        segment_stats = df.groupby([make_col, age_band_col, mileage_band_col]).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        segment_stats.columns = [make_col, age_band_col, mileage_band_col, 'failures', 'tests']

        # Get make rate for each segment
        segment_stats['make_rate'] = segment_stats[make_col].map(self.make_rates)

        # Apply shrinkage toward make rate
        segment_stats['smoothed_rate'] = (
            (segment_stats['failures'] + self.K_SEGMENT * segment_stats['make_rate']) /
            (segment_stats['tests'] + self.K_SEGMENT)
        )

        # Create lookup dictionary
        self.segment_rates = {}
        for _, row in segment_stats.iterrows():
            key = (row[make_col], row[age_band_col], row[mileage_band_col])
            self.segment_rates[key] = row['smoothed_rate']

        self.fitted = True

        # Print diagnostic information
        print(f"[RegimeAwareHierarchicalFeatures] Fitted on {total_tests:,} samples")
        print(f"  K_GLOBAL={self.K_GLOBAL}, K_REGIME={self.K_REGIME}, K_POWERTRAIN={self.K_POWERTRAIN}, K_SEGMENT={self.K_SEGMENT}")
        print(f"  Global failure rate: {self.global_fail_rate:.4f}")
        print(f"\n  Regime Statistics:")
        for regime in REGIMES:
            regime_df = df[df['_regime'] == regime]
            if len(regime_df) > 0:
                raw_rate = regime_df[target_col].mean()
                smoothed = self.regime_rates.get(regime, 0)
                n_makes = len(make_stats[make_stats['regime'] == regime])
                print(f"    {regime:<12}: {len(regime_df):>10,} samples, "
                      f"{raw_rate*100:.1f}% raw -> {smoothed*100:.1f}% smoothed, "
                      f"{n_makes} makes")
        print(f"\n  Powertrain Statistics:")
        for (regime, pt), rate in sorted(self.powertrain_rates.items()):
            pt_df = df[(df['_regime'] == regime) & (df['_powertrain'] == pt)]
            if len(pt_df) > 0:
                raw_rate = pt_df[target_col].mean()
                print(f"    {regime}+{pt:<4}: {len(pt_df):>8,} samples, "
                      f"{raw_rate*100:.1f}% raw -> {rate*100:.1f}% smoothed")
        print(f"\n  Make-level rates: {len(self.make_rates)} makes")
        print(f"  Segment-level rates: {len(self.segment_rates)} segments")

        return self

    def transform(self, df: pd.DataFrame,
                  make_col: str = 'make',
                  model_col: str = 'model_id',
                  age_band_col: str = 'age_band',
                  mileage_band_col: str = 'mileage_band',
                  prev_outcome_col: str = 'prev_cycle_outcome_band') -> pd.DataFrame:
        """
        Add regime and powertrain-aware hierarchical features to dataframe.

        Args:
            df: Dataframe to transform
            make_col: Name of make column
            model_col: Name of model column (for powertrain inference)
            age_band_col: Name of age band column
            mileage_band_col: Name of mileage band column
            prev_outcome_col: Name of previous outcome column (for interaction)

        Returns:
            DataFrame with added features:
            - regime_fail_rate: Regime-level smoothed rate
            - powertrain_fail_rate: Powertrain-level smoothed rate (NEW)
            - make_fail_rate_smoothed: Make-level smoothed rate (shrunk toward powertrain)
            - segment_fail_rate_smoothed: Segment-level smoothed rate
            - make_x_age_band: Interaction feature
            - make_x_prev_outcome: Interaction feature
        """
        if not self.fitted:
            raise ValueError("RegimeAwareHierarchicalFeatures not fitted. Call fit() first.")

        df = df.copy()

        # Create age_band if not present
        if age_band_col not in df.columns:
            df[age_band_col] = self._compute_age_band(df)

        # Create mileage_band if not present
        if mileage_band_col not in df.columns:
            df[mileage_band_col] = self._compute_mileage_band(df)

        # Infer regime and powertrain
        df['_regime'] = df[make_col].apply(infer_regime)
        if model_col in df.columns:
            df['_powertrain'] = df[model_col].apply(infer_powertrain)
        else:
            df['_powertrain'] = 'ICE'

        # Feature 0: Regime-level rate
        df['regime_fail_rate'] = df['_regime'].map(self.regime_rates)
        df['regime_fail_rate'] = df['regime_fail_rate'].fillna(self.global_fail_rate)

        # Feature 0b: Powertrain-level rate (NEW)
        def get_pt_rate(row):
            key = (row['_regime'], row['_powertrain'])
            return self.powertrain_rates.get(key, self.regime_rates.get(row['_regime'], self.global_fail_rate))

        df['powertrain_fail_rate'] = df.apply(get_pt_rate, axis=1)

        # Feature 1: Make-level smoothed rate
        df['make_fail_rate_smoothed'] = df[make_col].map(self.make_rates)
        # Fallback: powertrain rate for unseen makes
        df['make_fail_rate_smoothed'] = df.apply(
            lambda row: row['make_fail_rate_smoothed'] if pd.notna(row['make_fail_rate_smoothed'])
            else self.powertrain_rates.get((row['_regime'], row['_powertrain']),
                                           self.regime_rates.get(row['_regime'], self.global_fail_rate)),
            axis=1
        )

        # Feature 2: Segment-level smoothed rate
        def get_segment_rate(row):
            key = (row[make_col], row[age_band_col], row[mileage_band_col])
            if key in self.segment_rates:
                return self.segment_rates[key]
            # Fallback to make rate
            if row[make_col] in self.make_rates:
                return self.make_rates[row[make_col]]
            # Fallback to powertrain rate
            pt_key = (row['_regime'], row['_powertrain'])
            if pt_key in self.powertrain_rates:
                return self.powertrain_rates[pt_key]
            # Final fallback to regime rate
            return self.regime_rates.get(row['_regime'], self.global_fail_rate)

        df['segment_fail_rate_smoothed'] = df.apply(get_segment_rate, axis=1)

        # Feature 3: Make x Age Band interaction
        df['make_x_age_band'] = df[make_col].astype(str) + '_' + df[age_band_col].astype(str)

        # Feature 4: Make x Previous Outcome interaction
        if prev_outcome_col in df.columns:
            df['make_x_prev_outcome'] = df[make_col].astype(str) + '_' + df[prev_outcome_col].astype(str)
        else:
            df['make_x_prev_outcome'] = df[make_col].astype(str) + '_UNKNOWN'

        # Clean up temp columns
        df = df.drop(columns=['_regime', '_powertrain'])

        return df

    def fit_transform(self, df: pd.DataFrame,
                       target_col: str = 'target',
                       make_col: str = 'make',
                       model_col: str = 'model_id',
                       age_band_col: str = 'age_band',
                       mileage_band_col: str = 'mileage_band',
                       prev_outcome_col: str = 'prev_cycle_outcome_band') -> pd.DataFrame:
        """Fit and transform in one step."""
        self.fit(df, target_col=target_col, make_col=make_col, model_col=model_col,
                 age_band_col=age_band_col, mileage_band_col=mileage_band_col)
        return self.transform(df, make_col=make_col, model_col=model_col,
                              age_band_col=age_band_col, mileage_band_col=mileage_band_col,
                              prev_outcome_col=prev_outcome_col)

    def _compute_age_band(self, df: pd.DataFrame) -> pd.Series:
        """Compute age_band from n_prior_tests proxy."""
        if 'n_prior_tests' in df.columns:
            n_tests = df['n_prior_tests'].fillna(0)
            return pd.cut(
                n_tests,
                bins=[-1, 0, 2, 5, 10, float('inf')],
                labels=['0-2', '3-5', '6-10', '11-15', '15+']
            ).astype(str)
        else:
            return pd.Series(['Unknown'] * len(df), index=df.index)

    def _compute_mileage_band(self, df: pd.DataFrame) -> pd.Series:
        """Compute mileage_band from test_mileage."""
        if 'test_mileage' in df.columns:
            mileage = df['test_mileage'].fillna(50000)
            return pd.cut(
                mileage,
                bins=[-1, 30000, 60000, 100000, float('inf')],
                labels=['0-30k', '30k-60k', '60k-100k', '100k+']
            ).astype(str)
        else:
            return pd.Series(['Unknown'] * len(df), index=df.index)

    def save(self, path: Path):
        """Save fitted rates to file."""
        if not self.fitted:
            raise ValueError("Cannot save unfitted RegimeAwareHierarchicalFeatures")

        with open(path, 'wb') as f:
            pickle.dump({
                'global_fail_rate': self.global_fail_rate,
                'regime_rates': self.regime_rates,
                'powertrain_rates': self.powertrain_rates,
                'make_rates': self.make_rates,
                'make_to_regime': self.make_to_regime,
                'make_to_powertrain': self.make_to_powertrain,
                'segment_rates': self.segment_rates,
                'K_GLOBAL': self.K_GLOBAL,
                'K_REGIME': self.K_REGIME,
                'K_POWERTRAIN': self.K_POWERTRAIN,
                'K_SEGMENT': self.K_SEGMENT,
            }, f)
        print(f"[RegimeAwareHierarchicalFeatures] Saved to {path}")

    @classmethod
    def load(cls, path: Path) -> 'RegimeAwareHierarchicalFeatures':
        """Load fitted rates from file."""
        with open(path, 'rb') as f:
            data = pickle.load(f)

        rhf = cls()
        rhf.global_fail_rate = data['global_fail_rate']
        rhf.regime_rates = data['regime_rates']
        # Backward compatibility: powertrain_rates may not exist in older files
        rhf.powertrain_rates = data.get('powertrain_rates', {})
        rhf.make_rates = data['make_rates']
        rhf.make_to_regime = data['make_to_regime']
        rhf.make_to_powertrain = data.get('make_to_powertrain', {})
        rhf.segment_rates = data['segment_rates']
        rhf.K_GLOBAL = data['K_GLOBAL']
        rhf.K_REGIME = data['K_REGIME']
        rhf.K_POWERTRAIN = data.get('K_POWERTRAIN', cls.DEFAULT_K_POWERTRAIN)
        rhf.K_SEGMENT = data['K_SEGMENT']
        rhf.fitted = True

        print(f"[RegimeAwareHierarchicalFeatures] Loaded from {path}")
        return rhf

    def get_summary(self) -> dict:
        """Get summary statistics for audit/documentation."""
        if not self.fitted:
            return {'fitted': False}

        # Count makes per regime
        regime_make_counts = {}
        for regime in REGIMES:
            regime_make_counts[regime] = sum(
                1 for m, r in self.make_to_regime.items() if r == regime
            )

        # Powertrain rates summary
        powertrain_summary = {}
        if self.powertrain_rates:
            for (regime, pt), rate in self.powertrain_rates.items():
                powertrain_summary[f"{regime}+{pt}"] = rate

        return {
            'fitted': True,
            'global_fail_rate': self.global_fail_rate,
            'regime_rates': self.regime_rates,
            'powertrain_rates': powertrain_summary,
            'regime_make_counts': regime_make_counts,
            'n_makes': len(self.make_rates),
            'n_powertrain_combos': len(self.powertrain_rates) if self.powertrain_rates else 0,
            'n_segments': len(self.segment_rates),
            'K_GLOBAL': self.K_GLOBAL,
            'K_REGIME': self.K_REGIME,
            'K_POWERTRAIN': self.K_POWERTRAIN,
            'K_SEGMENT': self.K_SEGMENT,
            'make_rate_range': (
                min(self.make_rates.values()),
                max(self.make_rates.values())
            ),
            'segment_rate_range': (
                min(self.segment_rates.values()),
                max(self.segment_rates.values())
            ),
        }
