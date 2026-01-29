"""
Tests for the confidence interval module.
"""
import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from confidence import wilson_interval, classify_confidence


class TestWilsonInterval(unittest.TestCase):
    
    def test_basic_calculation(self):
        """Test basic Wilson interval calculation."""
        lower, upper = wilson_interval(25, 100)
        # Expected: approximately 0.17-0.34 for 25/100
        self.assertGreater(lower, 0.15)
        self.assertLess(lower, 0.20)
        self.assertGreater(upper, 0.30)
        self.assertLess(upper, 0.38)
    
    def test_zero_total(self):
        """Test handling of zero total trials."""
        lower, upper = wilson_interval(0, 0)
        self.assertEqual(lower, 0.0)
        self.assertEqual(upper, 0.0)
    
    def test_zero_successes(self):
        """Test when there are no successes."""
        lower, upper = wilson_interval(0, 100)
        self.assertEqual(lower, 0.0)
        self.assertGreater(upper, 0.0)
        self.assertLess(upper, 0.1)
    
    def test_all_successes(self):
        """Test when all trials are successes."""
        lower, upper = wilson_interval(100, 100)
        self.assertGreater(lower, 0.9)
        self.assertAlmostEqual(upper, 1.0, places=5)  # Use almost equal for float comparison
    
    def test_bounds(self):
        """Test that bounds are always in [0, 1]."""
        test_cases = [
            (0, 10),
            (5, 10),
            (10, 10),
            (1, 1000),
            (999, 1000)
        ]
        for successes, total in test_cases:
            lower, upper = wilson_interval(successes, total)
            self.assertGreaterEqual(lower, 0.0, f"Lower bound < 0 for {successes}/{total}")
            self.assertLessEqual(upper, 1.0, f"Upper bound > 1 for {successes}/{total}")
            self.assertLessEqual(lower, upper, f"Lower > Upper for {successes}/{total}")


class TestClassifyConfidence(unittest.TestCase):
    
    def test_high_confidence(self):
        """Test high confidence classification."""
        self.assertEqual(classify_confidence(10000), "High")
        self.assertEqual(classify_confidence(50000), "High")
    
    def test_good_confidence(self):
        """Test good confidence classification."""
        self.assertEqual(classify_confidence(1000), "Good")
        self.assertEqual(classify_confidence(5000), "Good")
        self.assertEqual(classify_confidence(9999), "Good")
    
    def test_limited_confidence(self):
        """Test limited confidence classification."""
        self.assertEqual(classify_confidence(0), "Limited")
        self.assertEqual(classify_confidence(100), "Limited")
        self.assertEqual(classify_confidence(999), "Limited")


if __name__ == '__main__':
    unittest.main()
