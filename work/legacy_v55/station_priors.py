"""
Station-Level Shrinkage Priors
==============================

Computes empirical Bayes smoothed failure rates at the station (postcode_area) level.
Uses global→station shrinkage to handle sparse stations.

Features computed:
1. station_fail_rate_smoothed: Station-level smoothed failure rate
2. station_x_prev_outcome_fail_rate: Station × previous outcome interaction

Shrinkage Parameters:
- K_STATION = 100: Shrinkage toward global average for station-level estimates
- K_INTERACTION = 50: Shrinkage for station × outcome interactions

Usage:
    from station_priors import StationPriors

    sp = StationPriors()
    sp.fit(train_df, target_col='target', station_col='postcode_area')
    train_df = sp.transform(train_df)
    val_df = sp.transform(val_df)  # Uses frozen mappings (no leakage)

Created: 2026-01-04
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional
import pickle
from pathlib import Path


class StationPriors:
    """Compute and apply station-level Bayesian smoothed features."""

    # Shrinkage parameters
    K_STATION = 100       # Shrinkage for station-level rates
    K_INTERACTION = 50    # Shrinkage for station × outcome interactions

    def __init__(self):
        self.global_fail_rate: float = None
        self.station_rates: Dict[str, float] = None
        self.station_x_outcome_rates: Dict[Tuple[str, str], float] = None
        self.fitted = False

    def fit(self, df: pd.DataFrame,
            target_col: str = 'target',
            station_col: str = 'postcode_area',
            outcome_col: str = 'prev_cycle_outcome_band') -> 'StationPriors':
        """
        Fit station priors from training data only.

        Args:
            df: Training dataframe
            target_col: Binary target column (0/1)
            station_col: Station identifier column (postcode_area)
            outcome_col: Previous outcome column for interaction

        Returns:
            self (fitted)
        """
        df = df.copy()

        # Step 1: Global failure rate
        total_tests = len(df)
        total_failures = df[target_col].sum()
        self.global_fail_rate = total_failures / total_tests if total_tests > 0 else 0.0

        # Step 2: Station-level smoothed rates
        station_stats = df.groupby(station_col).agg({
            target_col: ['sum', 'count']
        }).reset_index()
        station_stats.columns = [station_col, 'failures', 'tests']

        # Apply shrinkage toward global
        station_stats['smoothed_rate'] = (
            (station_stats['failures'] + self.K_STATION * self.global_fail_rate) /
            (station_stats['tests'] + self.K_STATION)
        )

        self.station_rates = dict(zip(station_stats[station_col], station_stats['smoothed_rate']))

        # Step 3: Station × Previous Outcome interaction rates
        if outcome_col in df.columns:
            interaction_stats = df.groupby([station_col, outcome_col]).agg({
                target_col: ['sum', 'count']
            }).reset_index()
            interaction_stats.columns = [station_col, outcome_col, 'failures', 'tests']

            # Get station rate for shrinkage target
            interaction_stats['station_rate'] = interaction_stats[station_col].map(self.station_rates)

            # Apply shrinkage toward station rate
            interaction_stats['smoothed_rate'] = (
                (interaction_stats['failures'] + self.K_INTERACTION * interaction_stats['station_rate']) /
                (interaction_stats['tests'] + self.K_INTERACTION)
            )

            self.station_x_outcome_rates = {}
            for _, row in interaction_stats.iterrows():
                key = (row[station_col], row[outcome_col])
                self.station_x_outcome_rates[key] = row['smoothed_rate']
        else:
            self.station_x_outcome_rates = {}

        self.fitted = True

        # Report statistics
        print(f"[StationPriors] Fitted on {total_tests:,} samples")
        print(f"  Global failure rate: {self.global_fail_rate:.4f}")
        print(f"  Station-level rates: {len(self.station_rates)} stations")
        print(f"  Station × outcome rates: {len(self.station_x_outcome_rates)} combinations")

        # Show station rate range
        rates = list(self.station_rates.values())
        print(f"  Station rate range: [{min(rates):.4f}, {max(rates):.4f}]")

        return self

    def transform(self, df: pd.DataFrame,
                  station_col: str = 'postcode_area',
                  outcome_col: str = 'prev_cycle_outcome_band') -> pd.DataFrame:
        """
        Add station prior features to dataframe.
        Uses frozen mappings from fit() - unknown stations get global mean.

        Args:
            df: Dataframe to transform
            station_col: Station identifier column
            outcome_col: Previous outcome column

        Returns:
            DataFrame with added features
        """
        if not self.fitted:
            raise ValueError("StationPriors not fitted. Call fit() first.")

        df = df.copy()

        # Feature 1: Station-level smoothed rate
        df['station_fail_rate_smoothed'] = df[station_col].map(self.station_rates)
        # Fallback to global rate for unknown stations
        unknown_mask = df['station_fail_rate_smoothed'].isna()
        n_unknown = unknown_mask.sum()
        if n_unknown > 0:
            print(f"  [StationPriors] {n_unknown} rows with unknown station → global mean")
        df['station_fail_rate_smoothed'] = df['station_fail_rate_smoothed'].fillna(self.global_fail_rate)

        # Feature 2: Station × Previous Outcome rate
        if outcome_col in df.columns and self.station_x_outcome_rates:
            def get_interaction_rate(row):
                key = (row[station_col], row[outcome_col])
                if key in self.station_x_outcome_rates:
                    return self.station_x_outcome_rates[key]
                # Fallback to station rate
                return self.station_rates.get(row[station_col], self.global_fail_rate)

            df['station_x_prev_outcome_fail_rate'] = df.apply(get_interaction_rate, axis=1)
        else:
            # No outcome column - just duplicate station rate
            df['station_x_prev_outcome_fail_rate'] = df['station_fail_rate_smoothed']

        return df

    def fit_transform(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """Fit and transform in one step."""
        self.fit(df, **kwargs)
        return self.transform(df, **kwargs)

    def save(self, path: Path):
        """Save fitted priors to file."""
        if not self.fitted:
            raise ValueError("Cannot save unfitted StationPriors")

        with open(path, 'wb') as f:
            pickle.dump({
                'global_fail_rate': self.global_fail_rate,
                'station_rates': self.station_rates,
                'station_x_outcome_rates': self.station_x_outcome_rates,
                'K_STATION': self.K_STATION,
                'K_INTERACTION': self.K_INTERACTION,
            }, f)
        print(f"[StationPriors] Saved to {path}")

    @classmethod
    def load(cls, path: Path) -> 'StationPriors':
        """Load fitted priors from file."""
        with open(path, 'rb') as f:
            data = pickle.load(f)

        sp = cls()
        sp.global_fail_rate = data['global_fail_rate']
        sp.station_rates = data['station_rates']
        sp.station_x_outcome_rates = data['station_x_outcome_rates']
        sp.fitted = True

        print(f"[StationPriors] Loaded from {path}")
        return sp

    def get_summary(self) -> dict:
        """Get summary statistics for audit/documentation."""
        if not self.fitted:
            return {'fitted': False}

        rates = list(self.station_rates.values())
        return {
            'fitted': True,
            'global_fail_rate': self.global_fail_rate,
            'n_stations': len(self.station_rates),
            'n_interactions': len(self.station_x_outcome_rates),
            'K_STATION': self.K_STATION,
            'K_INTERACTION': self.K_INTERACTION,
            'station_rate_range': (min(rates), max(rates)),
            'station_rate_std': float(np.std(rates)),
        }

    def leakage_check(self, train_df: pd.DataFrame, val_df: pd.DataFrame,
                      station_col: str = 'postcode_area') -> dict:
        """
        Verify no leakage from validation to training.

        Checks:
        1. All station rates computed from train only
        2. No val-only stations leaked into rates
        3. Unknown stations in val get global mean
        """
        train_stations = set(train_df[station_col].unique())
        val_stations = set(val_df[station_col].unique())
        fitted_stations = set(self.station_rates.keys())

        # Stations only in validation (should not be in fitted rates)
        val_only = val_stations - train_stations
        leaked = val_only & fitted_stations

        # Stations in validation that need fallback
        unknown_in_val = val_stations - fitted_stations

        result = {
            'train_stations': len(train_stations),
            'val_stations': len(val_stations),
            'fitted_stations': len(fitted_stations),
            'val_only_stations': len(val_only),
            'leaked_stations': len(leaked),
            'unknown_in_val': len(unknown_in_val),
            'leakage_detected': len(leaked) > 0,
        }

        if result['leakage_detected']:
            print(f"[LEAKAGE WARNING] {len(leaked)} validation-only stations found in fitted rates!")
        else:
            print(f"[StationPriors] Leakage check PASSED")
            print(f"  Train stations: {len(train_stations)}")
            print(f"  Val stations: {len(val_stations)}")
            print(f"  Unknown in val (→ global mean): {len(unknown_in_val)}")

        return result
