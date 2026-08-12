"""Tripwire: the API must engineer features through the artifact-loaded path.

A local refactor once switched main.py to the bare
feature_engineering_v55.engineer_features(), which silently bypasses the
loaded cohort/EB artifacts (model_age_fail_rate_eb alone carries 13.4%
importance) and degrades every hierarchical prior to its global default —
with no error and no visible failure. Caught in the GF-17 Phase A review
(2026-06-11). These source-level assertions make that regression loud.

Also pins feature_engineering_v55.engineer_features' OWN mileage
fallback/anomaly behaviour (test_mileage=0 when missing;
previous-reading substitution on an implausible annualised rate). This is
the TRAINED MODEL's serving contract, deliberately distinct from
report_service.resolve_odometer's honest-UNAVAILABLE Release-1 behaviour
-- retraining the model is the only way this may change, so these pins
guard it against an accidental "helpful" edit.
"""
import re
from datetime import datetime, timedelta
from pathlib import Path

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dvsa_client import MOTTest, VehicleHistory  # noqa: E402
from feature_engineering_v55 import engineer_features  # noqa: E402

MAIN = (Path(__file__).resolve().parent.parent / "main.py").read_text()


def test_main_imports_artifact_loaded_path():
    assert "from model_v55 import engineer_features_with_stats" in MAIN


def test_main_does_not_import_bare_engineer_features():
    assert not re.search(
        r"from\s+feature_engineering_v55\s+import\s+[^\n]*\bengineer_features\b",
        MAIN), ("main.py imports the bare engineer_features — this bypasses "
                "loaded cohort/EB artifacts; use model_v55."
                "engineer_features_with_stats")


def test_no_bare_engineer_features_calls():
    # Every scoring call site must use the _with_stats variant. Comments are
    # excluded so prose mentioning the call form cannot trip this.
    code_only = "\n".join(line.split("#", 1)[0] for line in MAIN.splitlines())
    bare_calls = re.findall(r"(?<![\w.])engineer_features\(", code_only)
    assert not bare_calls, (
        f"{len(bare_calls)} bare engineer_features() call(s) in main.py — "
        "use engineer_features_with_stats")


# ---------------------------------------------------------------------------
# V55 serving-contract mileage pins: guard the TRAINED model's own
# fallback/anomaly behaviour, distinct from report_service.resolve_odometer.
# ---------------------------------------------------------------------------

def _vehicle(tests):
    return VehicleHistory(
        registration='TESTV55', make='FORD', model='FIESTA',
        fuel_type='PETROL', colour='BLUE',
        registration_date=datetime(2015, 3, 15),
        manufacture_date=datetime(2015, 3, 15),
        engine_size=1200, mot_tests=tests)


def _mot(test_date, odometer_value, odometer_unit='mi'):
    return MOTTest(
        test_date=test_date, test_result='PASSED',
        expiry_date=test_date + timedelta(days=365),
        odometer_value=odometer_value, odometer_unit=odometer_unit,
        test_number=f'T{odometer_value}', defects=[])


def test_v55_missing_mileage_feature_is_zero_not_50000():
    """No test history at all -> the trained model's own missing-mileage
    fallback is test_mileage=0 (feature_engineering_v55.py's documented
    'else' branch), never a fabricated 50000 or any other placeholder."""
    history = _vehicle([])
    feats = engineer_features(history, 'SW1A 1AA', datetime(2026, 6, 10))

    assert feats['test_mileage'] == 0
    assert feats['test_mileage'] != 50000
    assert feats['has_prev_mileage'] == 0
    assert feats['mileage_anomaly_flag'] == 0


def test_v55_anomaly_substitutes_previous_reading():
    """10,000 miles in 30 days -> annualized ~=121,667mi/yr, over the
    trained model's own 50,000mi/yr implausibility threshold. This is the
    MODEL's serving contract (feature_engineering_v55.py:256-260):
    unlike report_service.resolve_odometer's honest UNAVAILABLE, the
    engineered feature substitutes the previous reading and sets
    mileage_anomaly_flag=1 -- pinned here so it cannot silently change
    outside of a deliberate, reviewed model retrain."""
    newest = datetime(2023, 7, 1)
    previous = datetime(2023, 6, 1)  # 30 days earlier
    history = _vehicle([
        _mot(newest, 20000),
        _mot(previous, 10000),
    ])
    feats = engineer_features(history, 'SW1A 1AA', datetime(2026, 6, 10))

    assert feats['test_mileage'] == 10000  # previous reading substituted
    assert feats['mileage_anomaly_flag'] == 1
    assert feats['mileage_plausible_flag'] == 0
    assert feats['has_prev_mileage'] == 1


class TestD13ServingChronology:
    """Serving analogue of decision D13: same-day ties must be deterministic
    and follow true chronology when DVSA provides it."""

    def _t(self, day, result, hhmm=None, num="1"):
        from datetime import datetime
        from dvsa_client import MOTTest
        completed = datetime(2025, 7, day, *divmod(hhmm, 100)) if hhmm else None
        return MOTTest(test_date=datetime(2025, 7, day), test_result=result,
                       expiry_date=None, odometer_value=10000, odometer_unit="mi",
                       test_number=num, defects=[], completed_at=completed)

    def test_timestamp_orders_same_day_pair_regardless_of_array_order(self):
        from dvsa_client import mot_chronology_key
        fail = self._t(2, "FAILED", hhmm=930, num="2")
        rt = self._t(2, "PASSED", hhmm=1445, num="1")
        for arr in ([rt, fail], [fail, rt]):
            newest_first = sorted(arr, key=mot_chronology_key, reverse=True)
            assert newest_first[0] is rt and newest_first[1] is fail

    def test_outcome_fallback_when_no_timestamps(self):
        # No time-of-day: the FAILED test precedes the PASSED resolution, so
        # newest-first puts PASSED at index 0 -- for either array order.
        from dvsa_client import mot_chronology_key
        fail = self._t(2, "FAILED", num="9")
        rt = self._t(2, "PASSED", num="3")
        for arr in ([rt, fail], [fail, rt]):
            newest_first = sorted(arr, key=mot_chronology_key, reverse=True)
            assert newest_first[0] is rt

    def test_client_parses_full_timestamp(self):
        from datetime import datetime
        from dvsa_client import DVSAClient
        client = DVSAClient.__new__(DVSAClient)
        assert client._parse_datetime('2025-07-02T10:30:00.000Z') == \
            datetime(2025, 7, 2, 10, 30, 0)
        assert client._parse_datetime('2025-07-02T10:30:00Z') == \
            datetime(2025, 7, 2, 10, 30, 0)
        assert client._parse_datetime('2025-07-02') is None   # no intra-day info
        assert client._parse_datetime(None) is None
        # the truncation contract of _parse_date is untouched
        assert client._parse_date('2025-07-02T10:30:00.000Z') == datetime(2025, 7, 2)
