"""
Unit tests for risk interpolation functionality.

Tests the interpolation logic that preserves continuous mileage signal
instead of discretizing into coarse bins.
"""
import unittest
from interpolation import (
    interpolate_risk,
    get_mileage_bucket,
    get_age_bucket,
    MILEAGE_ORDER,
    AGE_ORDER,
    MILEAGE_BUCKETS,
    AGE_BUCKETS
)


# Copy of helper functions from main.py for testing without FastAPI
def get_adjacent_mileage_bands(mileage_band: str):
    """Get the current and adjacent mileage bands for interpolation."""
    try:
        idx = MILEAGE_ORDER.index(mileage_band)
        bands = [mileage_band]
        if idx > 0:
            bands.append(MILEAGE_ORDER[idx - 1])
        if idx < len(MILEAGE_ORDER) - 1:
            bands.append(MILEAGE_ORDER[idx + 1])
        return bands
    except ValueError:
        return [mileage_band]


def get_adjacent_age_bands(age_band: str):
    """Get the current and adjacent age bands for interpolation."""
    try:
        idx = AGE_ORDER.index(age_band)
        bands = [age_band]
        if idx > 0:
            bands.append(AGE_ORDER[idx - 1])
        if idx < len(AGE_ORDER) - 1:
            bands.append(AGE_ORDER[idx + 1])
        return bands
    except ValueError:
        return [age_band]


def interpolate_risk_result(
    base_result: dict,
    bucket_data: dict,
    actual_mileage: int,
    actual_age: float,
    mileage_band: str,
    age_band: str
) -> dict:
    """Apply interpolation to risk values using actual mileage and age."""
    result = dict(base_result)

    risk_fields = [k for k in base_result.keys()
                   if k.startswith("Risk_") or k == "Failure_Risk"]

    if not risk_fields:
        return result

    mileage_risks_at_current_age = {}
    for mb in MILEAGE_ORDER:
        key = (age_band, mb)
        if key in bucket_data:
            mileage_risks_at_current_age[mb] = bucket_data[key]

    for field in risk_fields:
        field_by_mileage = {
            mb: data.get(field, 0.0)
            for mb, data in mileage_risks_at_current_age.items()
            if field in data
        }

        if len(field_by_mileage) >= 2:
            interpolated = interpolate_risk(actual_mileage, "mileage", field_by_mileage)
            result[field] = round(interpolated, 6)

    result["interpolated"] = True
    result["actual_mileage"] = actual_mileage
    result["actual_age"] = actual_age

    return result


class TestInterpolationModule(unittest.TestCase):
    """Tests for the core interpolation.py module."""

    def test_get_mileage_bucket(self):
        """Test mileage bucket classification."""
        self.assertEqual(get_mileage_bucket(0), "0-30k")
        self.assertEqual(get_mileage_bucket(29999), "0-30k")
        self.assertEqual(get_mileage_bucket(30000), "30k-60k")
        self.assertEqual(get_mileage_bucket(59999), "30k-60k")
        self.assertEqual(get_mileage_bucket(60000), "60k-100k")
        self.assertEqual(get_mileage_bucket(99999), "60k-100k")
        self.assertEqual(get_mileage_bucket(100000), "100k+")
        self.assertEqual(get_mileage_bucket(200000), "100k+")

    def test_get_age_bucket(self):
        """Test age bucket classification."""
        self.assertEqual(get_age_bucket(0), "0-3")
        self.assertEqual(get_age_bucket(2.9), "0-3")
        self.assertEqual(get_age_bucket(3), "3-5")
        self.assertEqual(get_age_bucket(5.9), "3-5")
        self.assertEqual(get_age_bucket(6), "6-10")
        self.assertEqual(get_age_bucket(10.9), "6-10")
        self.assertEqual(get_age_bucket(11), "10-15")
        self.assertEqual(get_age_bucket(16), "15+")

    def test_interpolate_risk_at_bucket_center(self):
        """At bucket mass center, interpolated value should equal bucket value."""
        bucket_risks = {
            "0-30k": 0.12,
            "30k-60k": 0.18,
            "60k-100k": 0.25,
            "100k+": 0.32,
        }
        # Mass center of 30k-60k is 45000
        result = interpolate_risk(45000, "mileage", bucket_risks)
        self.assertAlmostEqual(result, 0.18, places=2)

    def test_interpolate_risk_smooth_transition(self):
        """Risk should transition smoothly at bucket boundaries."""
        bucket_risks = {
            "0-30k": 0.12,
            "30k-60k": 0.18,
            "60k-100k": 0.25,
            "100k+": 0.32,
        }
        # Values just before and after 60k boundary should be close
        risk_at_59999 = interpolate_risk(59999, "mileage", bucket_risks)
        risk_at_60000 = interpolate_risk(60000, "mileage", bucket_risks)
        risk_at_60001 = interpolate_risk(60001, "mileage", bucket_risks)

        # Should be nearly identical (no cliff)
        self.assertAlmostEqual(risk_at_59999, risk_at_60000, places=3)
        self.assertAlmostEqual(risk_at_60000, risk_at_60001, places=3)

        # Without interpolation, 59999 would be 0.18 and 60000 would be 0.25
        # With interpolation, both should be around 0.21
        self.assertGreater(risk_at_59999, 0.18)  # Higher than raw bucket
        self.assertLess(risk_at_60000, 0.25)     # Lower than raw bucket

    def test_interpolate_risk_monotonic(self):
        """Risk should increase monotonically with mileage."""
        bucket_risks = {
            "0-30k": 0.12,
            "30k-60k": 0.18,
            "60k-100k": 0.25,
            "100k+": 0.32,
        }
        mileages = [20000, 40000, 60000, 80000, 120000]
        risks = [interpolate_risk(m, "mileage", bucket_risks) for m in mileages]

        # Each subsequent risk should be >= previous
        for i in range(1, len(risks)):
            self.assertGreaterEqual(risks[i], risks[i-1],
                f"Risk should increase: {mileages[i-1]}={risks[i-1]:.3f} vs {mileages[i]}={risks[i]:.3f}")

    def test_interpolate_within_bucket_differentiation(self):
        """Two vehicles in same bucket should get different scores."""
        bucket_risks = {
            "0-30k": 0.12,
            "30k-60k": 0.18,
            "60k-100k": 0.25,
        }
        # Both in 30k-60k bucket, but different mileages
        risk_at_35000 = interpolate_risk(35000, "mileage", bucket_risks)
        risk_at_55000 = interpolate_risk(55000, "mileage", bucket_risks)

        # Without interpolation, both would be 0.18
        # With interpolation, they should be different
        self.assertNotAlmostEqual(risk_at_35000, risk_at_55000, places=2)
        self.assertLess(risk_at_35000, risk_at_55000)


class TestMainIntegration(unittest.TestCase):
    """Tests for the integration helper functions."""

    def test_get_adjacent_mileage_bands_middle(self):
        """Middle bands should have both neighbors."""
        bands = get_adjacent_mileage_bands("30k-60k")
        self.assertIn("30k-60k", bands)
        self.assertIn("0-30k", bands)
        self.assertIn("60k-100k", bands)
        self.assertEqual(len(bands), 3)

    def test_get_adjacent_mileage_bands_first(self):
        """First band should only have next neighbor."""
        bands = get_adjacent_mileage_bands("0-30k")
        self.assertIn("0-30k", bands)
        self.assertIn("30k-60k", bands)
        self.assertEqual(len(bands), 2)

    def test_get_adjacent_mileage_bands_last(self):
        """Last band should only have previous neighbor."""
        bands = get_adjacent_mileage_bands("100k+")
        self.assertIn("100k+", bands)
        self.assertIn("60k-100k", bands)
        self.assertEqual(len(bands), 2)

    def test_get_adjacent_age_bands(self):
        """Age bands should work similarly to mileage bands."""
        bands = get_adjacent_age_bands("6-10")
        self.assertIn("6-10", bands)
        self.assertIn("3-5", bands)
        self.assertIn("10-15", bands)

    def test_interpolate_risk_result(self):
        """Full interpolation should modify risk fields."""
        base_result = {
            "model_id": "FORD FIESTA",
            "age_band": "3-5",
            "mileage_band": "30k-60k",
            "Total_Tests": 1000,
            "Total_Failures": 180,
            "Failure_Risk": 0.18,
            "Risk_Brakes": 0.05,
        }
        bucket_data = {
            ("3-5", "0-30k"): {"Failure_Risk": 0.12, "Risk_Brakes": 0.03},
            ("3-5", "30k-60k"): {"Failure_Risk": 0.18, "Risk_Brakes": 0.05},
            ("3-5", "60k-100k"): {"Failure_Risk": 0.25, "Risk_Brakes": 0.07},
        }

        # Test at 35000 miles (lower end of 30k-60k bucket)
        result = interpolate_risk_result(
            base_result=base_result,
            bucket_data=bucket_data,
            actual_mileage=35000,
            actual_age=4,
            mileage_band="30k-60k",
            age_band="3-5"
        )

        # Should be interpolated
        self.assertTrue(result.get("interpolated"))
        self.assertEqual(result["actual_mileage"], 35000)
        self.assertEqual(result["actual_age"], 4)

        # Risk should be lower than bucket center (35000 < 45000)
        self.assertLess(result["Failure_Risk"], 0.18)
        self.assertLess(result["Risk_Brakes"], 0.05)

    def test_interpolate_risk_result_preserves_non_risk_fields(self):
        """Non-risk fields should be preserved unchanged."""
        base_result = {
            "model_id": "FORD FIESTA",
            "age_band": "3-5",
            "mileage_band": "30k-60k",
            "Total_Tests": 1000,
            "Total_Failures": 180,
            "Failure_Risk": 0.18,
        }
        bucket_data = {
            ("3-5", "30k-60k"): {"Failure_Risk": 0.18},
            ("3-5", "60k-100k"): {"Failure_Risk": 0.25},
        }

        result = interpolate_risk_result(
            base_result=base_result,
            bucket_data=bucket_data,
            actual_mileage=50000,
            actual_age=4,
            mileage_band="30k-60k",
            age_band="3-5"
        )

        # Non-risk fields preserved
        self.assertEqual(result["model_id"], "FORD FIESTA")
        self.assertEqual(result["Total_Tests"], 1000)
        self.assertEqual(result["Total_Failures"], 180)


class TestAUCImprovement(unittest.TestCase):
    """Tests demonstrating AUC improvement from interpolation."""

    def test_ranking_preserved_within_bucket(self):
        """
        Key AUC test: vehicles with higher mileage should rank higher risk.

        Without interpolation, all vehicles in 30k-60k get 0.18.
        With interpolation, 55000 miles should rank higher than 35000.
        """
        bucket_risks = {
            "0-30k": 0.12,
            "30k-60k": 0.18,
            "60k-100k": 0.25,
        }

        # Simulate 10 vehicles in the 30k-60k bucket
        mileages = [31000, 35000, 40000, 45000, 50000, 52000, 55000, 57000, 58000, 59000]

        interpolated_risks = [
            interpolate_risk(m, "mileage", bucket_risks) for m in mileages
        ]

        # All should be different (no ties)
        unique_risks = set(round(r, 6) for r in interpolated_risks)
        self.assertEqual(len(unique_risks), len(mileages),
            "Each mileage should produce a unique risk score")

        # Should be monotonically increasing
        for i in range(1, len(interpolated_risks)):
            self.assertGreater(interpolated_risks[i], interpolated_risks[i-1],
                f"Risk should increase with mileage")

    def test_boundary_ranking_correct(self):
        """
        Vehicles near bucket boundaries should rank correctly.

        59999 miles should have similar (slightly lower) risk than 60001 miles,
        not a 7% jump.
        """
        bucket_risks = {
            "30k-60k": 0.18,
            "60k-100k": 0.25,
        }

        risk_59999 = interpolate_risk(59999, "mileage", bucket_risks)
        risk_60001 = interpolate_risk(60001, "mileage", bucket_risks)

        # Should be close (within 1%)
        self.assertAlmostEqual(risk_59999, risk_60001, delta=0.01)

        # 60001 should be slightly higher
        self.assertGreater(risk_60001, risk_59999)

    def test_no_cliff_at_boundary(self):
        """Demonstrate the cliff elimination at 60k boundary."""
        bucket_risks = {
            "0-30k": 0.12,
            "30k-60k": 0.18,
            "60k-100k": 0.25,
            "100k+": 0.32,
        }

        # Without interpolation: 59999->18%, 60000->25% (7% cliff)
        # With interpolation: both should be ~21%
        risk_before = interpolate_risk(59999, "mileage", bucket_risks)
        risk_after = interpolate_risk(60000, "mileage", bucket_risks)

        cliff_size = abs(risk_after - risk_before)

        # Cliff should be < 0.1% (was 7% without interpolation)
        self.assertLess(cliff_size, 0.001,
            f"Cliff at 60k boundary should be eliminated. Got {cliff_size:.4%}")

        print(f"\nCliff elimination test:")
        print(f"  59,999 miles: {risk_before:.4%}")
        print(f"  60,000 miles: {risk_after:.4%}")
        print(f"  Cliff size: {cliff_size:.4%} (was 7% without interpolation)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
