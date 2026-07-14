"""
Write-path provenance invariant: report_service.build_assessment may only
ever produce mileage.source in {observed_mot, missing} for a freshly
resolved report (user-entered priority and the age*8000 estimate were
ratified-but-rejected fabrication paths, removed for Release 1). Old
persisted 2.0 payloads that still carry a now-write-deprecated source
(estimated / user_entered) must keep replaying, per report_contract.py's
additive-optional guarantee -- the write path no longer PRODUCES that
source, but the contract must still READ it.
"""
import asyncio
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report_service  # noqa: E402
from report_contract import MileageSource, ReportResponse  # noqa: E402
from report_service import build_assessment  # noqa: E402

from report_test_helpers import make_history  # noqa: E402

FIXED_NOW = datetime(2026, 1, 1)


def run(coro):
    return asyncio.run(coro)


def _identity(year=2022):
    return {
        'registration': 'AB12CDE',
        'make': 'TESTMAKE',
        'model': 'TESTMODEL',
        'year': year,
        'fuel_type': 'PETROL',
        'colour': 'BLUE',
    }


# label -> history, covering every resolve_odometer outcome family.
_HISTORY_MATRIX = {
    'miles_reading': make_history([('2025-06-01', 45000, 'mi')]),
    'km_reading': make_history([('2025-06-01', 18000, 'km')]),
    'rollback': make_history([
        ('2026-01-01', 45000, 'mi'),
        ('2025-01-01', 50000, 'mi'),
    ]),
    'implausible_rate': make_history([
        ('2023-07-01', 20000, 'mi'),
        ('2023-06-01', 10000, 'mi'),
    ]),
    'no_readings': make_history([]),
    'no_history': None,
}


@pytest.mark.parametrize("label,history", list(_HISTORY_MATRIX.items()))
def test_new_reports_never_emit_estimated_or_user_entered(monkeypatch, label, history):
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=None), raising=False)

    assessment = run(build_assessment(_identity(), history, now=FIXED_NOW))

    assert assessment.mileage.source in {MileageSource.OBSERVED_MOT, MileageSource.MISSING}, label
    assert assessment.mileage.source != MileageSource.ESTIMATED, label
    assert assessment.mileage.source != MileageSource.USER_ENTERED, label


def _old_shape_payload():
    """A complete, valid v2 report payload shaped exactly as it would have
    been persisted before Release 1: mileage.source == 'estimated' (a
    real, once-legitimate write outcome now retired from the write path)
    and no original_value/original_unit keys at all on mileage (those
    fields did not exist yet)."""
    return {
        'contract_version': '2.0',
        'report_id': '11111111-1111-1111-1111-111111111111',
        'report_token': 'tok_abc123',
        'share_url': 'https://www.autosafe.one/app/report/tok_abc123',
        'created_at': '2026-01-01T00:00:00Z',
        'expires_at': '2026-04-01T00:00:00Z',
        'registration': 'AB12CDE',
        'vehicle': {'make': 'FORD', 'model': 'FIESTA', 'year': 2018, 'fuel_type': 'PETROL', 'colour': 'BLUE'},
        'mot': {'expiry_date': '2026-01-01', 'last_test_date': '2025-01-01', 'last_result': 'PASSED'},
        'mileage': {
            'effective_value': 64000,
            'source': 'estimated',
            'observed_at': None,
            'unit_converted': False,
            'anomaly': False,
            # Old (pre-Release-1) shape: no original_value / original_unit keys.
        },
        'evidence': {
            'match_scope': 'exact_band', 'age_band': '6-10', 'mileage_band': '60k-100k',
            'total_tests': 500, 'total_failures': 100,
        },
        'risk': {'failure_risk': 0.2, 'confidence': 'High'},
        'components': {'available': False, 'items': None},
        'repair_estimate': None,
        'persistence': {'saved': True, 'share_available': True},
        'prediction_source': 'sqlite',
        'vehicle_data_source': 'demo',
        'note': None,
    }


def test_persisted_estimated_payload_still_replays():
    payload = _old_shape_payload()
    restored = ReportResponse.model_validate(payload)

    assert restored.mileage.source == MileageSource.ESTIMATED
    assert restored.mileage.effective_value == 64000
    assert restored.mileage.original_value is None
    assert restored.mileage.original_unit is None
