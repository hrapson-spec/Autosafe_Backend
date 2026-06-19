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
    NEUTRAL_RATIO,
    cohort_ratio,
    prior_odometer,
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


if __name__ == "__main__":
    unittest.main()
