"""
AutoSafe Report Contract Tests
===============================

Tests for the versioned v2 report contract (report_contract.py): request/
response boundary validation, enum wire-format stability, and the
null-means-unknown / no-fabricated-defaults guarantees the contract makes.
"""
import json
import os
import sys
import unittest

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel, ValidationError

from confidence import classify_confidence
from report_contract import (
    ERROR_CODE_STATUS,
    POPULATION_DEFAULT_FAILURE_RISK,
    REPORT_CONTRACT_VERSION,
    REPORT_TTL_DAYS,
    SHARE_URL_PATH,
    ComponentRiskItem,
    ConfidenceLevel,
    ErrorCode,
    ErrorEnvelope,
    MatchScope,
    MileageSource,
    PredictionSource,
    ReportComponents,
    ReportCreateRequest,
    ReportEvidence,
    ReportMileage,
    ReportMot,
    ReportPersistence,
    ReportResponse,
    ReportRisk,
    ReportVehicle,
    VehicleDataSource,
)


def _full_response_kwargs():
    """A minimal, fully-degraded ReportResponse kwargs dict.

    Population default scope, every Optional left None, components
    unavailable, nothing persisted/shareable, prediction source
    unavailable. Used as the base fixture for the "honest failure" tests;
    other tests override individual fields as needed.
    """
    return dict(
        created_at="2026-07-10T00:00:00Z",
        registration="AB12CDE",
        vehicle=ReportVehicle(make="FORD", model="FIESTA", year=None),
        mot=ReportMot(),
        mileage=ReportMileage(effective_value=None, source=MileageSource.MISSING),
        evidence=ReportEvidence(
            match_scope=MatchScope.POPULATION_DEFAULT,
            age_band=None,
            mileage_band=None,
            total_tests=None,
            total_failures=None,
        ),
        risk=ReportRisk(failure_risk=POPULATION_DEFAULT_FAILURE_RISK, confidence=ConfidenceLevel.VERY_LOW),
        components=ReportComponents(available=False),
        persistence=ReportPersistence(saved=False, share_available=False),
        prediction_source=PredictionSource.UNAVAILABLE,
        vehicle_data_source=VehicleDataSource.DEMO,
    )


class TestExtraForbid(unittest.TestCase):
    """model_config = ConfigDict(extra="forbid") must reject unknown fields."""

    def test_unknown_field_on_request_rejected(self):
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="AB12CDE", bogus_field="nope")

    def test_unknown_field_on_response_rejected(self):
        kwargs = _full_response_kwargs()
        kwargs["bogus_field"] = "nope"
        with self.assertRaises(ValidationError):
            ReportResponse(**kwargs)


class TestRequestBoundaryValidation(unittest.TestCase):

    def test_registration_length_1_rejected(self):
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="A")

    def test_registration_length_2_accepted(self):
        req = ReportCreateRequest(registration="AB")
        self.assertEqual(req.registration, "AB")

    def test_registration_length_12_accepted(self):
        req = ReportCreateRequest(registration="A" * 12)
        self.assertEqual(len(req.registration), 12)

    def test_registration_length_13_rejected(self):
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="A" * 13)

    def test_mileage_user_negative_rejected(self):
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="AB12CDE", mileage_user=-1)

    def test_mileage_user_over_max_rejected(self):
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="AB12CDE", mileage_user=500001)

    def test_mileage_user_zero_accepted(self):
        req = ReportCreateRequest(registration="AB12CDE", mileage_user=0)
        self.assertEqual(req.mileage_user, 0)

    def test_mileage_user_max_accepted(self):
        req = ReportCreateRequest(registration="AB12CDE", mileage_user=500000)
        self.assertEqual(req.mileage_user, 500000)


class TestEnumRoundTrip(unittest.TestCase):
    """Every enum round-trips through model_dump_json -> model_validate_json,
    and the wire value is the literal string the contract pins."""

    def _roundtrip(self, enum_cls):
        class Wrapper(BaseModel):
            value: enum_cls

        for member in enum_cls:
            dumped = Wrapper(value=member).model_dump_json()
            restored = Wrapper.model_validate_json(dumped)
            self.assertEqual(restored.value, member)
            self.assertEqual(json.loads(dumped)["value"], member.value)

    def test_mileage_source_roundtrip(self):
        self._roundtrip(MileageSource)

    def test_match_scope_roundtrip(self):
        self._roundtrip(MatchScope)

    def test_confidence_level_roundtrip(self):
        self._roundtrip(ConfidenceLevel)

    def test_prediction_source_roundtrip(self):
        self._roundtrip(PredictionSource)

    def test_vehicle_data_source_roundtrip(self):
        self._roundtrip(VehicleDataSource)

    def test_error_code_roundtrip(self):
        self._roundtrip(ErrorCode)


class TestEnumValuesExact(unittest.TestCase):
    """Spec pins exact wire values; guard against accidental renames."""

    def test_mileage_source_values(self):
        self.assertEqual(
            {m.value for m in MileageSource},
            {"user_entered", "observed_mot", "estimated", "missing"},
        )

    def test_match_scope_values(self):
        self.assertEqual(
            {m.value for m in MatchScope},
            {"exact_band", "age_band_only", "model_average", "population_default", "unavailable"},
        )

    def test_confidence_level_values(self):
        self.assertEqual(
            {m.value for m in ConfidenceLevel},
            {"High", "Medium", "Low", "Very Low"},
        )

    def test_prediction_source_values(self):
        self.assertEqual(
            {m.value for m in PredictionSource},
            {"postgres", "sqlite", "unavailable"},
        )

    def test_vehicle_data_source_values(self):
        self.assertEqual({m.value for m in VehicleDataSource}, {"dvsa", "demo"})

    def test_error_code_values(self):
        self.assertEqual(
            {m.value for m in ErrorCode},
            {
                "invalid_registration", "vehicle_not_found", "dvsa_unavailable",
                "rate_limited", "internal_error", "report_not_found",
                "report_expired", "storage_unavailable",
            },
        )


class TestDegradedReportResponse(unittest.TestCase):
    """The contract must be able to express honest failure, not just the
    happy path."""

    def test_fully_degraded_response_validates(self):
        resp = ReportResponse(**_full_response_kwargs())

        self.assertEqual(resp.evidence.match_scope, MatchScope.POPULATION_DEFAULT)
        self.assertIsNone(resp.evidence.total_tests)
        self.assertIsNone(resp.evidence.total_failures)
        self.assertIsNone(resp.evidence.age_band)
        self.assertIsNone(resp.evidence.mileage_band)
        self.assertIsNone(resp.vehicle.year)
        self.assertIsNone(resp.mileage.effective_value)
        self.assertFalse(resp.components.available)
        self.assertIsNone(resp.components.items)
        self.assertFalse(resp.persistence.saved)
        self.assertFalse(resp.persistence.share_available)
        self.assertEqual(resp.prediction_source, PredictionSource.UNAVAILABLE)
        self.assertIsNone(resp.report_id)
        self.assertIsNone(resp.report_token)
        self.assertIsNone(resp.share_url)
        self.assertIsNone(resp.repair_estimate)

    def test_degraded_response_serializes_nulls_not_zeros(self):
        """Rendering a missing count as 0 is a contract violation -- prove
        the wire format actually preserves null rather than coercing it."""
        resp = ReportResponse(**_full_response_kwargs())
        payload = json.loads(resp.model_dump_json())
        self.assertIsNone(payload["evidence"]["total_tests"])
        self.assertIsNone(payload["evidence"]["total_failures"])
        self.assertNotEqual(payload["evidence"]["total_tests"], 0)
        self.assertNotEqual(payload["evidence"]["total_failures"], 0)


class TestErrorCodeStatus(unittest.TestCase):

    def test_covers_every_error_code_exactly_once(self):
        self.assertEqual(set(ERROR_CODE_STATUS.keys()), set(ErrorCode))

    def test_statuses_in_allowed_set(self):
        allowed = {400, 404, 410, 429, 500, 503}
        for code, status in ERROR_CODE_STATUS.items():
            self.assertIn(status, allowed, f"{code} has unexpected status {status}")

    def test_error_envelope_uses_error_code(self):
        env = ErrorEnvelope(
            error_code=ErrorCode.REPORT_EXPIRED,
            message="Report expired",
            correlation_id="corr-123",
        )
        self.assertEqual(ERROR_CODE_STATUS[env.error_code], 410)


class TestConfidenceLevelMatchesConfidenceModule(unittest.TestCase):
    """ConfidenceLevel values must match confidence.classify_confidence
    outputs exactly -- assert against real calls, not hardcoded strings."""

    def test_high(self):
        self.assertEqual(classify_confidence(1000), ConfidenceLevel.HIGH.value)

    def test_medium(self):
        self.assertEqual(classify_confidence(100), ConfidenceLevel.MEDIUM.value)

    def test_low(self):
        self.assertEqual(classify_confidence(20), ConfidenceLevel.LOW.value)

    def test_very_low(self):
        self.assertEqual(classify_confidence(5), ConfidenceLevel.VERY_LOW.value)

    def test_all_confidence_level_members_are_reachable(self):
        """Every ConfidenceLevel member must be producible by
        classify_confidence -- an unreachable member would be a latent
        contract/behaviour mismatch."""
        reachable = {
            classify_confidence(1000),
            classify_confidence(100),
            classify_confidence(20),
            classify_confidence(0),
        }
        self.assertEqual(reachable, {m.value for m in ConfidenceLevel})


class TestFailureRiskBounds(unittest.TestCase):

    def test_failure_risk_below_zero_rejected(self):
        with self.assertRaises(ValidationError):
            ReportRisk(failure_risk=-0.01, confidence=ConfidenceLevel.HIGH)

    def test_failure_risk_above_one_rejected(self):
        with self.assertRaises(ValidationError):
            ReportRisk(failure_risk=1.01, confidence=ConfidenceLevel.HIGH)

    def test_failure_risk_boundaries_accepted(self):
        ReportRisk(failure_risk=0.0, confidence=ConfidenceLevel.HIGH)
        ReportRisk(failure_risk=1.0, confidence=ConfidenceLevel.HIGH)

    def test_component_risk_item_bounds_rejected(self):
        with self.assertRaises(ValidationError):
            ComponentRiskItem(key="brakes", label="Brakes", risk=-0.1)
        with self.assertRaises(ValidationError):
            ComponentRiskItem(key="brakes", label="Brakes", risk=1.1)


class TestContractConstants(unittest.TestCase):

    def test_contract_version(self):
        self.assertEqual(REPORT_CONTRACT_VERSION, "2.0")

    def test_ttl_days(self):
        self.assertEqual(REPORT_TTL_DAYS, 90)

    def test_population_default_failure_risk(self):
        self.assertEqual(POPULATION_DEFAULT_FAILURE_RISK, 0.28)

    def test_share_url_path_has_token_placeholder(self):
        self.assertIn("{token}", SHARE_URL_PATH)
        self.assertEqual(SHARE_URL_PATH.format(token="abc123"), "/app/report/abc123")

    def test_response_defaults_to_current_contract_version(self):
        resp = ReportResponse(**_full_response_kwargs())
        self.assertEqual(resp.contract_version, REPORT_CONTRACT_VERSION)


if __name__ == '__main__':
    unittest.main()
