"""Unit tests for the honest, point-in-time mileage representations.

These lock down the leakage-free contract and the train/serve consistency that
``mileage_honest`` exists to guarantee.
"""

import math
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mileage_honest import (
    DEFAULT_ANNUAL_MILES,
    NEUTRAL_RATIO,
    cohort_ratio,
    prior_odometer,
    projected_odometer,
)


class TestPriorOdometer(unittest.TestCase):
    def test_reconstructs_previous_reading(self):
        # Scored test at 62k, +12k since the previous test -> previous was 50k.
        self.assertEqual(prior_odometer(62000, 12000), 50000)

    def test_falls_back_when_delta_missing(self):
        # First-test vehicle: no prior delta -> the only reading we have.
        self.assertEqual(prior_odometer(30000, None), 30000)
        self.assertEqual(prior_odometer(30000, float("nan")), 30000)

    def test_falls_back_on_negative_delta(self):
        # Clocking / rollback anomaly: don't invent a larger prior reading.
        self.assertEqual(prior_odometer(40000, -5000), 40000)

    def test_is_strictly_below_contemporaneous_level(self):
        # The whole point: the honest level is the PRIOR reading, never the
        # scored-test reading, whenever miles were added since.
        self.assertLess(prior_odometer(62000, 12000), 62000)


class TestProjectedOdometer(unittest.TestCase):
    def test_zero_horizon_returns_last_reading(self):
        self.assertAlmostEqual(projected_odometer(50000, 9000, 0), 50000)

    def test_projects_forward_at_rate(self):
        # 50k + 10k/yr for 365 days = 60k.
        self.assertAlmostEqual(projected_odometer(50000, 10000, 365), 60000)

    def test_never_moves_backwards(self):
        # Missing/negative rate falls back to the UK default; negative horizon
        # is clamped to zero. Projection is monotonic non-decreasing.
        self.assertAlmostEqual(
            projected_odometer(50000, None, 365), 50000 + DEFAULT_ANNUAL_MILES
        )
        self.assertAlmostEqual(projected_odometer(50000, -3, 365), 50000 + DEFAULT_ANNUAL_MILES)
        self.assertAlmostEqual(projected_odometer(50000, 10000, -10), 50000)

    def test_monotonic_in_horizon_and_rate(self):
        base = projected_odometer(50000, 8000, 200)
        self.assertGreater(projected_odometer(50000, 8000, 400), base)
        self.assertGreater(projected_odometer(50000, 16000, 200), base)


class TestCohortRatio(unittest.TestCase):
    def test_basic_ratio(self):
        self.assertAlmostEqual(cohort_ratio(12000, 8000), 1.5)

    def test_falls_back_to_global_mean(self):
        # No cohort mean -> use the global mean.
        self.assertAlmostEqual(cohort_ratio(10000, None, 8000), 1.25)

    def test_neutral_when_no_denominator(self):
        self.assertEqual(cohort_ratio(10000, None, None), NEUTRAL_RATIO)
        self.assertEqual(cohort_ratio(10000, 0, 0), NEUTRAL_RATIO)

    def test_neutral_when_value_missing(self):
        self.assertEqual(cohort_ratio(None, 8000), NEUTRAL_RATIO)
        self.assertEqual(cohort_ratio(float("nan"), 8000), NEUTRAL_RATIO)

    def test_above_one_means_driven_harder_than_peers(self):
        self.assertGreater(cohort_ratio(20000, 10000), 1.0)
        self.assertLess(cohort_ratio(5000, 10000), 1.0)


class TestTrainServeConsistency(unittest.TestCase):
    """The same vehicle scored in 'training' vs 'serving' framing must yield
    the same honest level."""

    def test_level_matches_across_framings(self):
        # Serving: most recent completed test reads 50k (that IS the level).
        serving_level = 50000.0
        # Training: the scored test reads 62k, +12k since the previous test.
        # The honest training level must reconstruct the previous 50k reading.
        training_level = prior_odometer(62000, 12000)
        self.assertEqual(training_level, serving_level)

    def test_projection_reduces_to_level_at_serve_instant(self):
        # If we score exactly on the last completed test date, projected == level.
        level = prior_odometer(62000, 12000)
        self.assertAlmostEqual(projected_odometer(level, 9000, 0), level)


if __name__ == "__main__":
    unittest.main()
