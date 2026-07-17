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

import pytest

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel, ValidationError

from confidence import classify_confidence
from report_contract import (
    DATASET_TOTAL_FAILURES,
    DATASET_TOTAL_TESTS,
    ERROR_CODE_STATUS,
    POPULATION_DEFAULT_FAILURE_RISK,
    REPORT_CONTRACT_VERSION,
    REPORT_TTL_DAYS,
    SHARE_URL_PATH,
    CohortMatchLevel,
    ComponentRiskItem,
    ConfidenceLevel,
    ErrorCode,
    ErrorEnvelope,
    LookupPredictionSource,
    MatchScope,
    MileageSource,
    OdometerStatus,
    OdometerUnavailableReason,
    PredictionSource,
    ResultKind,
    ReportComponents,
    ReportCreateRequest,
    ReportEvidence,
    ReportMileage,
    ReportMot,
    ReportPersistence,
    ReportRepairEstimate,
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
        prediction_source=PredictionSource.DATASET_REFERENCE,
        vehicle_data_source=VehicleDataSource.DEMO,
    )


@pytest.fixture
def valid_report_dict():
    payload = ReportResponse(**_full_response_kwargs()).model_dump(mode="json")
    payload["result_kind"] = "comparison"
    return payload


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


class TestResultSemanticContract:

    def test_result_kind_values(self):
        assert {member.value for member in ResultKind} == {
            "comparison",
            "vehicle_prediction",
        }

    def test_result_kind_defaults_to_comparison(self, valid_report_dict):
        valid_report_dict.pop("result_kind", None)
        report = ReportResponse.model_validate(valid_report_dict)
        assert report.result_kind == ResultKind.COMPARISON

    def test_vehicle_prediction_requires_model_source(self, valid_report_dict):
        valid_report_dict["result_kind"] = "vehicle_prediction"
        with pytest.raises(ValidationError, match="vehicle prediction requires model_v55"):
            ReportResponse.model_validate(valid_report_dict)

    def test_vehicle_prediction_accepts_model_v55(self, valid_report_dict):
        valid_report_dict.update(result_kind="vehicle_prediction", prediction_source="model_v55")
        # A prediction must also claim the model_prediction evidence scope
        # (see TestModelPredictionScope for the full biconditional).
        valid_report_dict["evidence"] = dict(
            match_scope="model_prediction",
            age_band=None,
            mileage_band=None,
            total_tests=None,
            total_failures=None,
        )
        report = ReportResponse.model_validate(valid_report_dict)
        assert report.result_kind == ResultKind.VEHICLE_PREDICTION

    def test_model_v55_prediction_source_requires_vehicle_prediction(self, valid_report_dict):
        valid_report_dict["prediction_source"] = "model_v55"
        with pytest.raises(
            ValidationError,
            match="model_v55 prediction source requires vehicle_prediction",
        ):
            ReportResponse.model_validate(valid_report_dict)


class TestModelPredictionScope:
    """model_prediction evidence scope: valid only on vehicle_prediction
    reports, and carries no cohort bands or counts (a per-vehicle model
    output is not a cohort match)."""

    def _prediction_evidence(self, **overrides):
        fields = dict(
            match_scope=MatchScope.MODEL_PREDICTION,
            age_band=None,
            mileage_band=None,
            total_tests=None,
            total_failures=None,
        )
        fields.update(overrides)
        return fields

    def test_match_scope_has_model_prediction_member(self):
        assert MatchScope.MODEL_PREDICTION.value == "model_prediction"

    def test_all_null_prediction_evidence_validates(self):
        evidence = ReportEvidence(**self._prediction_evidence())
        assert evidence.match_scope == MatchScope.MODEL_PREDICTION

    def test_prediction_evidence_rejects_bands(self):
        for overrides in (
            dict(age_band="3-5"),
            dict(mileage_band="30k-60k"),
            dict(age_band="3-5", mileage_band="30k-60k"),
        ):
            with pytest.raises(ValidationError):
                ReportEvidence(**self._prediction_evidence(**overrides))

    def test_prediction_evidence_rejects_cohort_counts(self):
        with pytest.raises(ValidationError, match="carries no cohort counts"):
            ReportEvidence(**self._prediction_evidence(total_tests=100))
        with pytest.raises(ValidationError):
            ReportEvidence(**self._prediction_evidence(total_tests=100, total_failures=20))

    def _prediction_response_dict(self, valid_report_dict):
        valid_report_dict.update(
            result_kind="vehicle_prediction",
            prediction_source="model_v55",
        )
        valid_report_dict["evidence"] = dict(
            match_scope="model_prediction",
            age_band=None,
            mileage_band=None,
            total_tests=None,
            total_failures=None,
        )
        valid_report_dict["risk"] = dict(failure_risk=0.31, confidence="Medium")
        return valid_report_dict

    def test_full_prediction_response_validates(self, valid_report_dict):
        report = ReportResponse.model_validate(self._prediction_response_dict(valid_report_dict))
        assert report.result_kind == ResultKind.VEHICLE_PREDICTION
        assert report.evidence.match_scope == MatchScope.MODEL_PREDICTION
        assert report.note is None

    def test_prediction_response_validates_with_components_and_repair(self, valid_report_dict):
        payload = self._prediction_response_dict(valid_report_dict)
        payload["components"] = dict(
            available=True,
            items=[dict(key="brakes", label="Brakes", risk=0.18)],
        )
        payload["repair_estimate"] = dict(expected=120, range_low=80, range_high=200)
        report = ReportResponse.model_validate(payload)
        assert report.components.available is True
        assert report.repair_estimate.expected == 120

    def test_vehicle_prediction_requires_model_prediction_scope(self, valid_report_dict):
        payload = self._prediction_response_dict(valid_report_dict)
        payload["evidence"] = dict(
            match_scope="exact_band",
            age_band="3-5",
            mileage_band="30k-60k",
            total_tests=100,
            total_failures=20,
        )
        with pytest.raises(ValidationError, match="model_prediction evidence scope"):
            ReportResponse.model_validate(payload)

    def test_comparison_cannot_claim_model_prediction_scope(self, valid_report_dict):
        valid_report_dict["evidence"] = dict(
            match_scope="model_prediction",
            age_band=None,
            mileage_band=None,
            total_tests=None,
            total_failures=None,
        )
        with pytest.raises(ValidationError, match="model_prediction evidence scope"):
            ReportResponse.model_validate(valid_report_dict)


class TestRequestBoundaryValidation(unittest.TestCase):

    def test_registration_length_1_accepted_at_contract_level(self):
        # Deliberate: a 1-char value must pass pydantic so the ROUTE's VRN
        # regex rejects it with the typed 400 invalid_registration envelope
        # instead of a generic 422 (staging acceptance check 4h). Empty
        # string still fails min_length here.
        req = ReportCreateRequest(registration="A")
        self.assertEqual(req.registration, "A")
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="")

    def test_registration_length_2_accepted(self):
        req = ReportCreateRequest(registration="AB")
        self.assertEqual(req.registration, "AB")

    def test_registration_length_12_accepted(self):
        req = ReportCreateRequest(registration="A" * 12)
        self.assertEqual(len(req.registration), 12)

    def test_registration_length_13_rejected(self):
        with self.assertRaises(ValidationError):
            ReportCreateRequest(registration="A" * 13)

    def test_empty_or_whitespace_idempotency_key_is_rejected(self):
        for key in ("", "   "):
            with self.subTest(key=repr(key)), self.assertRaises(ValidationError):
                ReportCreateRequest(registration="AB12CDE", idempotency_key=key)


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
        # user_entered/estimated members are RETAINED (already-persisted 2.0
        # payloads replay through ReportResponse.model_validate) but are
        # write-deprecated as of Release 1: resolve_mileage() can now only
        # ever produce observed_mot or missing for a freshly built report.
        self.assertEqual(
            {m.value for m in MileageSource},
            {"user_entered", "observed_mot", "estimated", "missing"},
        )

    def test_odometer_status_values(self):
        self.assertEqual(
            {m.value for m in OdometerStatus},
            {"available", "unavailable"},
        )

    def test_odometer_unavailable_reason_values(self):
        self.assertEqual(
            {m.value for m in OdometerUnavailableReason},
            {"no_reading", "rollback", "implausible_increase", "unknown_unit"},
        )

    def test_lookup_prediction_source_values(self):
        self.assertEqual(
            {m.value for m in LookupPredictionSource},
            {"population_exact", "population_broad", "population_global", "unavailable"},
        )

    def test_cohort_match_level_values(self):
        self.assertEqual(
            {m.value for m in CohortMatchLevel},
            {"exact_band", "age_band_only", "model_average", "dataset"},
        )

    def test_match_scope_values(self):
        self.assertEqual(
            {m.value for m in MatchScope},
            {"exact_band", "age_band_only", "model_average", "population_default",
             "unavailable", "model_prediction"},
        )

    def test_confidence_level_values(self):
        self.assertEqual(
            {m.value for m in ConfidenceLevel},
            {"High", "Medium", "Low", "Very Low"},
        )

    def test_prediction_source_values(self):
        self.assertEqual(
            {m.value for m in PredictionSource},
            {"postgres", "sqlite", "dataset_reference", "model_v55", "unavailable"},
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
                "idempotency_conflict",
                # FLAGGED ADDITIVE CHANGE: added for report_routes.py's
                # shared undeclared-query-parameter guard on both v2
                # routes (see report_contract.ErrorCode's own comment).
                "undeclared_parameter",
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
        self.assertEqual(resp.prediction_source, PredictionSource.DATASET_REFERENCE)
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
        allowed = {400, 404, 409, 410, 429, 500, 503}
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


class TestContractConsistency(unittest.TestCase):

    def test_evidence_bands_describe_the_matched_scope_only(self):
        invalid = (
            dict(match_scope=MatchScope.EXACT_BAND, age_band="3-5", mileage_band=None),
            dict(match_scope=MatchScope.AGE_BAND_ONLY, age_band="3-5", mileage_band="30k-60k"),
            dict(match_scope=MatchScope.AGE_BAND_ONLY, age_band=None, mileage_band=None),
            dict(match_scope=MatchScope.MODEL_AVERAGE, age_band="3-5", mileage_band=None),
            dict(match_scope=MatchScope.POPULATION_DEFAULT, age_band=None, mileage_band="30k-60k"),
            dict(match_scope=MatchScope.UNAVAILABLE, age_band="Unknown", mileage_band=None),
        )
        for fields in invalid:
            with self.assertRaises(ValidationError):
                ReportEvidence(total_tests=100 if fields["match_scope"] in {MatchScope.EXACT_BAND, MatchScope.AGE_BAND_ONLY, MatchScope.MODEL_AVERAGE} else None,
                               total_failures=20 if fields["match_scope"] in {MatchScope.EXACT_BAND, MatchScope.AGE_BAND_ONLY, MatchScope.MODEL_AVERAGE} else None,
                               **fields)

        ReportEvidence(match_scope=MatchScope.EXACT_BAND, age_band="3-5", mileage_band="30k-60k", total_tests=100, total_failures=20)
        ReportEvidence(match_scope=MatchScope.AGE_BAND_ONLY, age_band="3-5", mileage_band=None, total_tests=100, total_failures=20)
        ReportEvidence(match_scope=MatchScope.MODEL_AVERAGE, age_band=None, mileage_band=None, total_tests=100, total_failures=20)
        ReportEvidence(match_scope=MatchScope.POPULATION_DEFAULT, age_band=None, mileage_band=None, total_tests=None, total_failures=None)
        ReportEvidence(match_scope=MatchScope.UNAVAILABLE, age_band=None, mileage_band=None, total_tests=None, total_failures=None)

    def test_evidence_counts_are_nonnegative_integral_and_consistent(self):
        for kwargs in (
            dict(total_tests=-1, total_failures=0),
            dict(total_tests=10, total_failures=11),
            dict(total_tests=None, total_failures=1),
            dict(total_tests=0, total_failures=0),
        ):
            with self.assertRaises(ValidationError):
                ReportEvidence(
                    match_scope=MatchScope.EXACT_BAND,
                    age_band="3-5",
                    mileage_band="30k-60k",
                    **kwargs,
                )

    def test_component_available_flag_agrees_with_items(self):
        item = ComponentRiskItem(key="brakes", label="Brakes", risk=0.1)
        with self.assertRaises(ValidationError):
            ReportComponents(available=True, items=None)
        with self.assertRaises(ValidationError):
            ReportComponents(available=False, items=[item])

    def test_missing_mileage_has_no_effective_value(self):
        with self.assertRaises(ValidationError):
            ReportMileage(effective_value=50000, source=MileageSource.MISSING)

    def test_unit_converted_requires_km_original_unit(self):
        # unit_converted True + an explicitly wrong original_unit is rejected.
        with self.assertRaises(ValidationError):
            ReportMileage(
                effective_value=45000, source=MileageSource.OBSERVED_MOT,
                unit_converted=True, original_value=45000, original_unit='mi',
            )
        # unit_converted True + the correct 'km' original_unit validates.
        mileage = ReportMileage(
            effective_value=11185, source=MileageSource.OBSERVED_MOT,
            unit_converted=True, original_value=18000, original_unit='km',
        )
        self.assertEqual(mileage.original_unit, 'km')

    def test_unit_converted_tolerates_missing_original_unit_for_old_payloads(self):
        """A 2.0 payload persisted before original_value/original_unit
        existed can have unit_converted=True with original_unit absent
        entirely (defaults to None) -- that must still validate per the
        additive-optional contract guarantee. None means "unknown", not
        "known and not km"; only an explicitly wrong unit is rejected."""
        mileage = ReportMileage(effective_value=62137, source=MileageSource.OBSERVED_MOT, unit_converted=True)
        self.assertIsNone(mileage.original_unit)
        self.assertIsNone(mileage.original_value)

    def test_repair_estimate_range_is_ordered_and_nonnegative(self):
        with self.assertRaises(ValidationError):
            ReportRepairEstimate(expected=100, range_low=200, range_high=50)
        with self.assertRaises(ValidationError):
            ReportRepairEstimate(expected=-1, range_low=-2, range_high=0)

    def test_repair_estimate_requires_supported_component_evidence(self):
        kwargs = _full_response_kwargs()
        kwargs["repair_estimate"] = ReportRepairEstimate(
            expected=200,
            range_low=100,
            range_high=300,
        )
        with self.assertRaises(ValidationError):
            ReportResponse(**kwargs)

    def test_shareable_response_requires_a_saved_bearer_link(self):
        kwargs = _full_response_kwargs()
        kwargs["persistence"] = ReportPersistence(saved=False, share_available=True)
        with self.assertRaises(ValidationError):
            ReportResponse(**kwargs)

        kwargs = _full_response_kwargs()
        kwargs["persistence"] = ReportPersistence(saved=True, share_available=True)
        with self.assertRaises(ValidationError):
            ReportResponse(**kwargs)

    def test_unsaved_response_cannot_expose_share_credentials(self):
        kwargs = _full_response_kwargs()
        kwargs["report_token"] = "secret-token"
        kwargs["share_url"] = "/app/report/secret-token"
        with self.assertRaises(ValidationError):
            ReportResponse(**kwargs)

    def test_unsaved_response_cannot_claim_a_report_expiry(self):
        kwargs = _full_response_kwargs()
        kwargs["expires_at"] = "2026-10-09T00:00:00Z"
        with self.assertRaises(ValidationError):
            ReportResponse(**kwargs)


class TestContractConstants(unittest.TestCase):

    def test_contract_version(self):
        self.assertEqual(REPORT_CONTRACT_VERSION, "2.0")

    def test_ttl_days(self):
        self.assertEqual(REPORT_TTL_DAYS, 90)

    def test_population_default_failure_risk(self):
        self.assertEqual(DATASET_TOTAL_TESTS, 148_509_908)
        self.assertEqual(DATASET_TOTAL_FAILURES, 39_969_903)
        self.assertEqual(
            POPULATION_DEFAULT_FAILURE_RISK,
            DATASET_TOTAL_FAILURES / DATASET_TOTAL_TESTS,
        )

    def test_share_url_path_has_token_placeholder(self):
        self.assertIn("{token}", SHARE_URL_PATH)
        self.assertEqual(SHARE_URL_PATH.format(token="abc123"), "/app/report/abc123")

    def test_response_defaults_to_current_contract_version(self):
        resp = ReportResponse(**_full_response_kwargs())
        self.assertEqual(resp.contract_version, REPORT_CONTRACT_VERSION)


if __name__ == '__main__':
    unittest.main()
