"""
Tests for the new odometer/lookup contract models in report_contract.py:

- OdometerReading: status <-> field-presence validator (AVAILABLE requires
  every reading detail present + source pinned to OBSERVED_MOT;
  UNAVAILABLE requires every reading detail absent + a reason).
- LookupCohort: zero tests are not evidence (mirrors ReportEvidence's own
  rule at report_contract.py:227).
- RiskLookupResponse: prediction_source <-> cohort/rate shape validator
  (the future /api/risk response; T3 wires the route, this module defines
  and fully tests the model now).
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError  # noqa: E402

from report_contract import (  # noqa: E402
    DATASET_TOTAL_FAILURES,
    DATASET_TOTAL_TESTS,
    CohortMatchLevel,
    LookupCohort,
    LookupPredictionSource,
    MileageSource,
    OdometerReading,
    OdometerStatus,
    OdometerUnavailableReason,
    RiskLookupResponse,
)


def _cohort(**overrides):
    defaults = dict(
        match_level=CohortMatchLevel.EXACT_BAND,
        age_band='3-5',
        mileage_band='30k-60k',
        total_tests=500,
        total_failures=100,
    )
    defaults.update(overrides)
    return LookupCohort(**defaults)


def _lookup_kwargs(**overrides):
    defaults = dict(
        vehicle='FORD FIESTA',
        year=2018,
        mileage=None,
        failure_risk=0.2,
        confidence_level='High',
        risk_brakes=0.1, risk_suspension=0.1, risk_tyres=0.1, risk_steering=0.1,
        risk_visibility=0.1, risk_lamps=0.1, risk_body=0.1,
        repair_cost_estimate=None,
        prediction_source=LookupPredictionSource.POPULATION_EXACT,
        cohort=_cohort(),
        note=None,
    )
    defaults.update(overrides)
    return defaults


_ALL_COMPONENT_KEYS = (
    'risk_brakes', 'risk_suspension', 'risk_tyres', 'risk_steering',
    'risk_visibility', 'risk_lamps', 'risk_body',
)


class TestRiskLookupResponseValidator(unittest.TestCase):

    def test_unavailable_forbids_rate_and_cohort(self):
        with self.assertRaises(ValidationError):
            RiskLookupResponse(**_lookup_kwargs(prediction_source=LookupPredictionSource.UNAVAILABLE))

        # A cohort-free, rate-free unavailable lookup, on the other hand, is
        # exactly the honest shape and must validate.
        nulled_components = {key: None for key in _ALL_COMPONENT_KEYS}
        RiskLookupResponse(**_lookup_kwargs(
            prediction_source=LookupPredictionSource.UNAVAILABLE,
            failure_risk=None, confidence_level=None, cohort=None,
            repair_cost_estimate=None,
            **nulled_components,
        ))

    def test_population_global_pins_dataset_totals(self):
        wrong_cohort = _cohort(
            match_level=CohortMatchLevel.DATASET, age_band=None, mileage_band=None,
            total_tests=1000, total_failures=200,
        )
        with self.assertRaises(ValidationError):
            RiskLookupResponse(**_lookup_kwargs(
                prediction_source=LookupPredictionSource.POPULATION_GLOBAL, cohort=wrong_cohort,
            ))

        right_cohort = _cohort(
            match_level=CohortMatchLevel.DATASET, age_band=None, mileage_band=None,
            total_tests=DATASET_TOTAL_TESTS, total_failures=DATASET_TOTAL_FAILURES,
        )
        resp = RiskLookupResponse(**_lookup_kwargs(
            prediction_source=LookupPredictionSource.POPULATION_GLOBAL, cohort=right_cohort,
        ))
        self.assertEqual(resp.cohort.total_tests, DATASET_TOTAL_TESTS)
        self.assertEqual(resp.cohort.total_failures, DATASET_TOTAL_FAILURES)

    def test_exact_requires_both_bands(self):
        missing_mileage_band = _cohort(
            match_level=CohortMatchLevel.EXACT_BAND, age_band='3-5', mileage_band=None,
        )
        with self.assertRaises(ValidationError):
            RiskLookupResponse(**_lookup_kwargs(
                prediction_source=LookupPredictionSource.POPULATION_EXACT, cohort=missing_mileage_band,
            ))

        missing_age_band = _cohort(
            match_level=CohortMatchLevel.EXACT_BAND, age_band=None, mileage_band='30k-60k',
        )
        with self.assertRaises(ValidationError):
            RiskLookupResponse(**_lookup_kwargs(
                prediction_source=LookupPredictionSource.POPULATION_EXACT, cohort=missing_age_band,
            ))

        wrong_level = _cohort(
            match_level=CohortMatchLevel.AGE_BAND_ONLY, age_band='3-5', mileage_band=None,
        )
        with self.assertRaises(ValidationError):
            RiskLookupResponse(**_lookup_kwargs(
                prediction_source=LookupPredictionSource.POPULATION_EXACT, cohort=wrong_level,
            ))

        both_bands = _cohort(
            match_level=CohortMatchLevel.EXACT_BAND, age_band='3-5', mileage_band='30k-60k',
        )
        RiskLookupResponse(**_lookup_kwargs(
            prediction_source=LookupPredictionSource.POPULATION_EXACT, cohort=both_bands,
        ))

    def test_broad_levels(self):
        age_only = _cohort(match_level=CohortMatchLevel.AGE_BAND_ONLY, age_band='3-5', mileage_band=None)
        RiskLookupResponse(**_lookup_kwargs(
            prediction_source=LookupPredictionSource.POPULATION_BROAD, cohort=age_only,
        ))

        model_avg = _cohort(match_level=CohortMatchLevel.MODEL_AVERAGE, age_band=None, mileage_band=None)
        RiskLookupResponse(**_lookup_kwargs(
            prediction_source=LookupPredictionSource.POPULATION_BROAD, cohort=model_avg,
        ))

        dataset_level = _cohort(
            match_level=CohortMatchLevel.DATASET, age_band=None, mileage_band=None,
            total_tests=DATASET_TOTAL_TESTS, total_failures=DATASET_TOTAL_FAILURES,
        )
        with self.assertRaises(ValidationError):
            RiskLookupResponse(**_lookup_kwargs(
                prediction_source=LookupPredictionSource.POPULATION_BROAD, cohort=dataset_level,
            ))


class TestLookupCohortZeroTests(unittest.TestCase):

    def test_zero_tests_rejected(self):
        with self.assertRaises(ValidationError):
            LookupCohort(
                match_level=CohortMatchLevel.MODEL_AVERAGE, age_band=None, mileage_band=None,
                total_tests=0, total_failures=None,
            )


class TestOdometerReadingValidator(unittest.TestCase):

    def _available_kwargs(self):
        return dict(
            value_miles=45000, recorded_at='2025-06-01', original_value=45000,
            original_unit='mi', source=MileageSource.OBSERVED_MOT,
            status=OdometerStatus.AVAILABLE, unavailable_reason=None,
        )

    def test_odometer_available_requires_all_fields(self):
        base = self._available_kwargs()
        for missing_field in ('value_miles', 'recorded_at', 'original_value', 'original_unit', 'source'):
            kwargs = dict(base)
            kwargs[missing_field] = None
            with self.assertRaises(ValidationError, msg=missing_field):
                OdometerReading(**kwargs)

        # Wrong source (not OBSERVED_MOT) must fail even with everything else present.
        wrong_source = dict(base)
        wrong_source['source'] = MileageSource.ESTIMATED
        with self.assertRaises(ValidationError):
            OdometerReading(**wrong_source)

        # unavailable_reason present alongside AVAILABLE must fail.
        stray_reason = dict(base)
        stray_reason['unavailable_reason'] = OdometerUnavailableReason.NO_READING
        with self.assertRaises(ValidationError):
            OdometerReading(**stray_reason)

        # The fully-populated case must validate.
        OdometerReading(**base)

    def test_odometer_unavailable_requires_reason(self):
        with self.assertRaises(ValidationError):
            OdometerReading(
                value_miles=None, recorded_at=None, original_value=None, original_unit=None,
                source=None, status=OdometerStatus.UNAVAILABLE, unavailable_reason=None,
            )

        # Any stray detail field alongside UNAVAILABLE must also fail.
        with self.assertRaises(ValidationError):
            OdometerReading(
                value_miles=45000, recorded_at=None, original_value=None, original_unit=None,
                source=None, status=OdometerStatus.UNAVAILABLE,
                unavailable_reason=OdometerUnavailableReason.NO_READING,
            )

        # The fully-honest unavailable shape must validate.
        OdometerReading(
            value_miles=None, recorded_at=None, original_value=None, original_unit=None,
            source=None, status=OdometerStatus.UNAVAILABLE,
            unavailable_reason=OdometerUnavailableReason.NO_READING,
        )


if __name__ == '__main__':
    unittest.main()
