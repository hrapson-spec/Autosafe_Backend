"""
prediction_service tests
========================

The typed V55 prediction boundary: turns resolved vehicle identity + DVSA
history into a vehicle_prediction Assessment, or raises exactly one of the
typed operational failures the route is allowed to catch. Model functions
are mocked at the prediction_service import site; no real model artifacts
are loaded here.
"""
import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_test_helpers import make_history  # noqa: E402

import prediction_service  # noqa: E402
import report_service  # noqa: E402
from prediction_service import (  # noqa: E402
    FeatureEngineeringFailed,
    InferenceFailed,
    ModelUnavailable,
    PredictionDisabled,
    PredictionUnavailable,
    build_v55_assessment,
)
from report_contract import (  # noqa: E402
    ConfidenceLevel,
    MatchScope,
    PredictionSource,
    ResultKind,
)


IDENTITY = {
    'registration': 'AB12CDE',
    'make': 'FORD',
    'model': 'FIESTA',
    'year': 2018,
    'fuel_type': 'PETROL',
    'colour': 'BLUE',
}

COMPONENT_KEYS = ('brakes', 'suspension', 'tyres', 'steering', 'visibility', 'lamps', 'body')


def _history():
    return make_history([('2025-03-01', 41000, 'mi'), ('2024-03-01', 36000, 'mi')])


def _prediction(failure_risk=0.31, confidence='Medium', components=None):
    if components is None:
        components = {
            'brakes': 0.18, 'suspension': 0.07, 'tyres': 0.09, 'steering': 0.02,
            'visibility': 0.03, 'lamps': 0.05, 'body': 0.02,
        }
    return {
        'failure_risk': failure_risk,
        'raw_probability': 0.29,
        'confidence_level': confidence,
        'risk_components': components,
    }


class PredictionServiceCase(unittest.TestCase):
    """Base: kill-switch on, model loaded, both model functions mocked."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'PREDICTIONS_ENABLED': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)

        self.loaded = patch('prediction_service.model_v55.is_model_loaded', return_value=True)
        self.loaded.start()
        self.addCleanup(self.loaded.stop)

        self.engineer = patch(
            'prediction_service.model_v55.engineer_features_with_stats',
            return_value={'feature': 1},
        )
        self.mock_engineer = self.engineer.start()
        self.addCleanup(self.engineer.stop)

        self.predict = patch(
            'prediction_service.model_v55.predict_risk',
            return_value=_prediction(),
        )
        self.mock_predict = self.predict.start()
        self.addCleanup(self.predict.stop)


class TestTypedFailures(PredictionServiceCase):

    def test_kill_switch_disabled_raises_and_never_touches_the_model(self):
        with patch.dict(os.environ, {'PREDICTIONS_ENABLED': 'false'}):
            with self.assertRaises(PredictionDisabled):
                build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        self.mock_engineer.assert_not_called()
        self.mock_predict.assert_not_called()

    def test_model_not_loaded_raises_model_unavailable(self):
        with patch('prediction_service.model_v55.is_model_loaded', return_value=False):
            with self.assertRaises(ModelUnavailable):
                build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        self.mock_predict.assert_not_called()

    def test_feature_engineering_exception_is_typed(self):
        self.mock_engineer.side_effect = KeyError('missing feature input')
        with self.assertRaises(FeatureEngineeringFailed):
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        self.mock_predict.assert_not_called()

    def test_inference_exception_is_typed(self):
        self.mock_predict.side_effect = RuntimeError('catboost blew up')
        with self.assertRaises(InferenceFailed):
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')

    def test_all_typed_failures_share_the_catchable_base(self):
        for exc in (PredictionDisabled, ModelUnavailable, FeatureEngineeringFailed, InferenceFailed):
            self.assertTrue(issubclass(exc, PredictionUnavailable))

    def test_invalid_overall_probability_is_inference_failure(self):
        for bad in (float('nan'), float('inf'), -0.1, 1.1, None, 'high', True):
            self.mock_predict.return_value = _prediction(failure_risk=bad)
            with self.assertRaises(InferenceFailed, msg=f'failure_risk={bad!r}'):
                build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')

    def test_unknown_confidence_string_is_inference_failure(self):
        self.mock_predict.return_value = _prediction(confidence='Extremely High')
        with self.assertRaises(InferenceFailed):
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')


class TestSuccess(PredictionServiceCase):

    def test_success_shape(self):
        assessment = build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')

        self.assertEqual(assessment.result_kind, ResultKind.VEHICLE_PREDICTION)
        self.assertEqual(assessment.prediction_source, PredictionSource.MODEL_V55)
        self.assertEqual(assessment.evidence.match_scope, MatchScope.MODEL_PREDICTION)
        self.assertIsNone(assessment.evidence.age_band)
        self.assertIsNone(assessment.evidence.mileage_band)
        self.assertIsNone(assessment.evidence.total_tests)
        self.assertIsNone(assessment.evidence.total_failures)
        self.assertIsNone(assessment.note)
        self.assertEqual(assessment.risk.failure_risk, 0.31)
        self.assertEqual(assessment.risk.confidence, ConfidenceLevel.MEDIUM)

    def test_each_model_function_called_exactly_once(self):
        build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        self.assertEqual(self.mock_engineer.call_count, 1)
        self.assertEqual(self.mock_predict.call_count, 1)
        # predict_risk receives exactly what feature engineering produced.
        self.mock_predict.assert_called_once_with({'feature': 1})

    def test_confidence_levels_map_directly(self):
        for level, expected in (
            ('High', ConfidenceLevel.HIGH),
            ('Medium', ConfidenceLevel.MEDIUM),
            ('Low', ConfidenceLevel.LOW),
        ):
            self.mock_predict.return_value = _prediction(confidence=level)
            assessment = build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
            self.assertEqual(assessment.risk.confidence, expected)

    def test_components_carry_the_shared_keys_labels_and_order(self):
        assessment = build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        self.assertTrue(assessment.components.available)
        items = assessment.components.items
        self.assertEqual([item.key for item in items], list(COMPONENT_KEYS))
        expected_labels = [label for _, label, _ in report_service._COMPONENT_FIELDS]
        self.assertEqual([item.label for item in items], expected_labels)
        self.assertEqual(items[0].risk, 0.18)

    def test_repair_estimate_derives_from_the_predicted_components(self):
        assessment = build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        estimate = assessment.repair_estimate
        self.assertIsNotNone(estimate)
        self.assertTrue(estimate.range_low <= estimate.expected <= estimate.range_high)

    def test_repair_estimate_input_uses_the_repair_cost_keys(self):
        with patch('prediction_service.repair_costs.calculate_expected_repair_cost') as mock_cost:
            mock_cost.return_value = {'cost_min': 80, 'cost_mid': 120, 'cost_max': 200}
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        risk_data = mock_cost.call_args.args[0]
        self.assertEqual(risk_data['Failure_Risk'], 0.31)
        self.assertEqual(risk_data['Risk_Brakes'], 0.18)
        self.assertEqual(risk_data['Risk_Lamps_Reflectors_And_Electrical_Equipment'], 0.05)
        self.assertEqual(risk_data['Risk_Body_Chassis_Structure'], 0.02)

    def test_vehicle_context_matches_the_shared_helper(self):
        history = _history()
        assessment = build_v55_assessment(IDENTITY, history, postcode='SW1A 1AA')
        vehicle, mot, mileage = report_service.build_vehicle_context(IDENTITY, history)
        self.assertEqual(assessment.vehicle, vehicle)
        self.assertEqual(assessment.mot, mot)
        self.assertEqual(assessment.mileage, mileage)


class TestComponentSuppression(PredictionServiceCase):
    """Incomplete or invalid component data suppresses components and the
    repair estimate WITHOUT discarding a valid overall prediction."""

    def _assert_suppressed_but_valid(self, assessment):
        self.assertEqual(assessment.result_kind, ResultKind.VEHICLE_PREDICTION)
        self.assertEqual(assessment.risk.failure_risk, 0.31)
        self.assertFalse(assessment.components.available)
        self.assertIsNone(assessment.components.items)
        self.assertIsNone(assessment.repair_estimate)

    def test_missing_component_key(self):
        components = {k: 0.05 for k in COMPONENT_KEYS if k != 'tyres'}
        self.mock_predict.return_value = _prediction(components=components)
        self._assert_suppressed_but_valid(
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        )

    def test_non_finite_component_value(self):
        components = {k: 0.05 for k in COMPONENT_KEYS}
        components['brakes'] = math.nan
        self.mock_predict.return_value = _prediction(components=components)
        self._assert_suppressed_but_valid(
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        )

    def test_out_of_range_component_value(self):
        components = {k: 0.05 for k in COMPONENT_KEYS}
        components['body'] = 1.2
        self.mock_predict.return_value = _prediction(components=components)
        self._assert_suppressed_but_valid(
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        )

    def test_components_not_a_dict(self):
        self.mock_predict.return_value = _prediction(components=[])
        self._assert_suppressed_but_valid(
            build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        )

    def test_repair_cost_none_keeps_components_but_drops_estimate(self):
        with patch('prediction_service.repair_costs.calculate_expected_repair_cost', return_value=None):
            assessment = build_v55_assessment(IDENTITY, _history(), postcode='SW1A 1AA')
        self.assertTrue(assessment.components.available)
        self.assertIsNone(assessment.repair_estimate)


if __name__ == '__main__':
    unittest.main()
