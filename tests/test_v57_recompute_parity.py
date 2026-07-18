"""Train-vs-serve equivalence for the v57 in-window history recompute.

The decisive Stage-1 gate in miniature: for synthetic vehicles we build the
training-side inputs (lake / spine / component-failure parquets + target
rows) AND the equivalent serving-side ``VehicleHistory`` objects, then
assert that ``v57_history_recompute.build_history_features`` emits exactly
what ``feature_engineering_v57.engineer_features`` emits, feature by
feature, for every feature the recompute owns.

A mismatch here is precisely the defect class GF-17 catalogued (frame
drift, default drift, vocab drift) — it must fail loudly, not average out.
"""

import os
import sys
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import v57_history_recompute as rec  # noqa: E402
from dvsa_client import MOTTest, VehicleHistory  # noqa: E402
from feature_engineering_v57 import engineer_features  # noqa: E402

# Features whose training-side values the recompute owns. Names are the
# renamed v57 contract names; serving emits the same names via fe_v57.
RECOMPUTED_FEATURES = [
    "n_prior_tests_observed", "n_prior_fails_observed", "prior_fail_rate_observed",
    "fails_last_365d_observed", "fails_last_730d_observed",
    "prior_advisory_count_observed",
    "prev_cycle_outcome_band", "days_since_last_test", "gap_band",
    "days_since_pass_ratio", "days_late", "mdps_score", "advisory_trend",
    "test_month", "day_of_week", "is_winter_test",
    "test_mileage", "has_prev_mileage", "mileage_anomaly_flag",
    "mileage_plausible_flag", "annualized_mileage_v2", "usage_band_hybrid",
    "prev_adv_brakes", "prev_adv_tyres", "prev_adv_suspension", "prev_adv_steering",
    "multi_system_advisory_count", "front_end_advisory_intensity",
    "brake_system_stress", "commercial_wear_proxy",
    "fail_rate_trend", "recent_fail_intensity", "has_advisory_history",
    "max_severity_score", "severity_escalation_flag",
    "mech_decay_brake", "mech_decay_suspension", "mech_decay_structure",
    "mech_decay_steering", "mech_decay_index", "mech_decay_index_normalized",
    "mech_risk_driver",
    "historic_negligence_ratio_smoothed", "negligence_band", "raw_behavioral_count",
    "has_prior_advisory_brakes_observed", "tests_since_last_advisory_brakes_observed",
    "advisory_in_last_1_brakes_observed", "advisory_in_last_2_brakes_observed",
    "advisory_streak_len_brakes_observed",
    "has_prior_advisory_tyres_observed", "tests_since_last_advisory_tyres_observed",
    "advisory_in_last_1_tyres_observed", "advisory_in_last_2_tyres_observed",
    "advisory_streak_len_tyres_observed", "miles_since_last_advisory_tyres_observed",
    "has_prior_advisory_suspension_observed",
    "tests_since_last_advisory_suspension_observed",
    "advisory_in_last_1_suspension_observed", "advisory_in_last_2_suspension_observed",
    "advisory_streak_len_suspension_observed",
    "miles_since_last_advisory_suspension_observed",
    "has_prior_failure_brakes_observed", "has_observed_failure_brakes",
    "failure_streak_len_brakes_observed", "tests_since_last_failure_brakes_observed",
    "has_prior_failure_tyres_observed", "has_observed_failure_tyres",
    "failure_streak_len_tyres_observed", "tests_since_last_failure_tyres_observed",
    "has_prior_failure_suspension_observed", "has_observed_failure_suspension",
    "failure_streak_len_suspension_observed",
    "tests_since_last_failure_suspension_observed",
    "window_days_available", "history_years_observed", "has_prior_test_observed",
    "has_left_truncated_history", "first_observed_test_is_not_true_first",
]

BRAKE_ADV = {"type": "ADVISORY", "text": "brake pads worn"}
BRAKE_FAIL = {"type": "FAIL", "text": "brake disc fractured"}
TYRE_ADV = {"type": "ADVISORY", "text": "tyre tread low"}


class _Veh:
    """One synthetic vehicle described once, projected to both paths."""

    def __init__(self, vehicle_id, first_use, target_test_id, target_date, events):
        # events: list of dicts {test_id, date, result, odo, defects}
        self.vehicle_id = vehicle_id
        self.first_use = first_use
        self.target_test_id = target_test_id
        self.target_date = target_date
        self.events = events

    def n_advisories(self, ev):
        return sum(1 for d in ev["defects"] if d["type"] == "ADVISORY")

    def n_adv_comp(self, ev, keyword):
        return sum(
            1 for d in ev["defects"] if d["type"] == "ADVISORY" and keyword in d["text"]
        )

    def serving_history(self):
        return VehicleHistory(
            registration=f"V{self.vehicle_id}",
            make="FORD",
            model="FOCUS",
            fuel_type=None,
            colour=None,
            registration_date=datetime.fromisoformat(self.first_use),
            manufacture_date=datetime.fromisoformat(self.first_use),
            engine_size=1600,
            mot_tests=[
                MOTTest(
                    test_date=datetime.fromisoformat(ev["date"]),
                    test_result=ev["result"],
                    # PASSED tests carry expiry ~= test date + 1 year; the
                    # training side approximates expiry as prior_date + 365
                    # (DVSA's anniversary-preservation rule can add up to
                    # ~30 days — documented Stage-1 residual).
                    expiry_date=(
                        datetime.fromisoformat(ev["date"]) + timedelta(days=365)
                        if ev["result"] == "PASSED" else None
                    ),
                    odometer_value=ev["odo"],
                    odometer_unit="mi",
                    test_number=str(ev["test_id"]),
                    defects=ev["defects"],
                )
                for ev in self.events
            ],
        )

    def lake_rows(self):
        """Cycle-chain rows: each event's prev_advisory_* describes the
        event before it (the lake's lagged-column convention)."""
        rows = []
        prev = None
        for ev in self.events:
            rows.append({
                "test_id": ev["test_id"],
                "vehicle_id": self.vehicle_id,
                "test_date": ev["date"],
                "outcome": "FAIL" if ev["result"] == "FAILED" else "PASS",
                "prev_advisory_total": float(self.n_advisories(prev)) if prev else 0.0,
                "prev_advisory_known": 1 if prev else 0,
                "prev_advisory_brakes": self.n_adv_comp(prev, "brake") if prev else 0,
                "prev_advisory_tyres": self.n_adv_comp(prev, "tyre") if prev else 0,
                "prev_advisory_suspension": self.n_adv_comp(prev, "suspension") if prev else 0,
            })
            prev = ev
        return rows

    def target_row(self):
        last = self.events[-1] if self.events else None
        return {
            "test_id": self.target_test_id,
            "vehicle_id": self.vehicle_id,
            "test_date": self.target_date,
            "first_use_date": self.first_use,
            "prev_advisory_total": float(self.n_advisories(last)) if last else 0.0,
            "prev_advisory_known": 1 if last else 0,
            "prev_advisory_brakes": self.n_adv_comp(last, "brake") if last else 0,
            "prev_advisory_tyres": self.n_adv_comp(last, "tyre") if last else 0,
            "prev_advisory_suspension": self.n_adv_comp(last, "suspension") if last else 0,
        }

    def spine_rows(self):
        return [
            {"test_id": ev["test_id"], "vehicle_id": self.vehicle_id,
             "test_date": ev["date"], "is_failure": ev["result"] == "FAILED",
             "test_mileage": ev["odo"]}
            for ev in self.events
        ]

    def component_failure_rows(self):
        rows = []
        for ev in self.events:
            if ev["result"] != "FAILED":
                continue
            fails = [d for d in ev["defects"] if d["type"] == "FAIL"]
            rows.append({
                "test_id": ev["test_id"],
                "fail_brakes": int(any("brake" in d["text"] for d in fails)),
                "fail_tyres": int(any("tyre" in d["text"] for d in fails)),
                "fail_suspension": int(any("suspension" in d["text"] for d in fails)),
            })
        return rows


VEHICLES = [
    # Rich truncated history: 3 in-window events, brake failure + advisories.
    _Veh(100, "2014-03-01", 1000, "2023-06-25", [
        {"test_id": 101, "date": "2020-06-01", "result": "FAILED", "odo": 30000,
         "defects": [BRAKE_ADV, BRAKE_ADV, BRAKE_FAIL]},
        {"test_id": 102, "date": "2021-06-10", "result": "PASSED", "odo": 38000,
         "defects": [TYRE_ADV]},
        {"test_id": 103, "date": "2022-06-20", "result": "PASSED", "odo": 46000,
         "defects": []},
    ]),
    # Rookie: no observed history, young vehicle.
    _Veh(200, "2021-05-01", 2000, "2024-05-20", []),
    # Single observed prior, old vehicle (left-truncated).
    _Veh(300, "2012-01-01", 3000, "2023-03-10", [
        {"test_id": 301, "date": "2022-03-15", "result": "PASSED", "odo": 20000,
         "defects": []},
    ]),
    # Latest prior FAILED: no expiry at serve -> days_late must be 0 on
    # both paths even though the gap exceeds a year.
    _Veh(400, "2015-07-01", 4000, "2024-02-01", [
        {"test_id": 401, "date": "2021-09-01", "result": "PASSED", "odo": 51000,
         "defects": []},
        {"test_id": 402, "date": "2022-09-15", "result": "FAILED", "odo": 60000,
         "defects": [BRAKE_FAIL]},
    ]),
]


@pytest.fixture(scope="module")
def recomputed(tmp_path_factory, monkeypatch_module=None):
    tmp = tmp_path_factory.mktemp("v57_fixture")
    lake = pd.DataFrame(
        [row for v in VEHICLES for row in v.lake_rows()]
        # the targets themselves are lake events too (cycle-first tests)
        + [
            {**v.target_row(), "outcome": "PASS"}
            for v in VEHICLES
        ]
    )
    lake["test_date"] = pd.to_datetime(lake["test_date"])
    lake.drop(columns=["first_use_date"], errors="ignore").to_parquet(tmp / "lake.parquet")

    spine = pd.DataFrame([row for v in VEHICLES for row in v.spine_rows()])
    if len(spine):
        spine["test_date"] = pd.to_datetime(spine["test_date"])
    spine.to_parquet(tmp / "sampled_fixture.parquet")

    comp = pd.DataFrame(
        [row for v in VEHICLES for row in v.component_failure_rows()]
    )
    if comp.empty:
        comp = pd.DataFrame({"test_id": pd.Series(dtype="int64"),
                             "fail_brakes": pd.Series(dtype="int64"),
                             "fail_tyres": pd.Series(dtype="int64"),
                             "fail_suspension": pd.Series(dtype="int64")})
    comp.to_parquet(tmp / "component_failures.parquet")

    ext = pd.DataFrame({
        "test_id": pd.Series(dtype="int64"), "vehicle_id": pd.Series(dtype="int64"),
        "test_date": pd.Series(dtype="datetime64[ns]"),
        "prev_advisory_total": pd.Series(dtype="float64"),
        "prev_advisory_known": pd.Series(dtype="int64"),
        "prev_advisory_brakes": pd.Series(dtype="int64"),
        "prev_advisory_tyres": pd.Series(dtype="int64"),
        "prev_advisory_suspension": pd.Series(dtype="int64"),
    })
    ext.to_parquet(tmp / "extension.parquet")

    targets = pd.DataFrame([v.target_row() for v in VEHICLES])
    targets["test_date"] = pd.to_datetime(targets["test_date"])
    targets["first_use_date"] = pd.to_datetime(targets["first_use_date"])

    real = (rec.LAKE, rec.SPINE_FILES, rec.HISTORY_EXTENSION_2024, rec.COMPONENT_FAILURES)
    rec.LAKE = tmp / "lake.parquet"
    rec.SPINE_FILES = [tmp / "sampled_fixture.parquet"]
    rec.HISTORY_EXTENSION_2024 = tmp / "extension.parquet"
    rec.COMPONENT_FAILURES = tmp / "component_failures.parquet"
    try:
        conn = duckdb.connect()
        out = rec.build_history_features(conn, targets, label="fixture")
    finally:
        rec.LAKE, rec.SPINE_FILES, rec.HISTORY_EXTENSION_2024, rec.COMPONENT_FAILURES = real
    return out.set_index("test_id")


@pytest.mark.parametrize("veh", VEHICLES, ids=lambda v: f"vehicle_{v.vehicle_id}")
def test_recompute_matches_serving_feature_engineering(recomputed, veh):
    serving = engineer_features(
        veh.serving_history(),
        postcode="SW1A 1AA",
        prediction_date=datetime.fromisoformat(veh.target_date),
    )
    train = recomputed.loc[veh.target_test_id]

    mismatches = []
    for name in RECOMPUTED_FEATURES:
        s, tr = serving[name], train[name]
        if isinstance(s, str) or isinstance(tr, str):
            # categorical / stringly-typed (test_month and day_of_week are
            # str at serve, int pre-cast in the matrix): compare normalized
            if _norm(s) != _norm(tr):
                mismatches.append((name, s, tr))
        else:
            if not float(s) == pytest.approx(float(tr), rel=1e-9, abs=1e-9):
                mismatches.append((name, s, tr))
    assert not mismatches, f"train/serve drift: {mismatches}"


def _norm(v):
    """Int-like values compare by their integer string ('6' == 6 == 6.0)."""
    if not isinstance(v, str):
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)
        except (TypeError, ValueError):
            return str(v)
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        return v
