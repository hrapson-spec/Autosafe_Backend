"""Truthfulness and shared-report coverage for outbound email paths."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from email_templates import (  # noqa: E402
    generate_lead_email,
    generate_mot_reminder_confirmation,
    generate_mot_reminder_28d,
    generate_report_email,
)
from main import app  # noqa: E402
from send_mot_reminders import get_due_reminders  # noqa: E402


client = TestClient(app)


def _all_copy(message: dict[str, str]) -> str:
    return "\n".join(message.values()).lower()


def _assert_comparison_truth(message: dict[str, str]) -> None:
    copy = _all_copy(message)
    assert "comparable" in copy
    for misleading in (
        "reliability score",
        "pass probability",
        "mot prediction",
        "predicted failure",
        "looks healthy",
        "no major concerns",
    ):
        assert misleading not in copy


def test_report_email_uses_one_comparable_rate_and_non_diagnostic_copy():
    message = generate_report_email(
        email="owner@example.com",
        registration="AB12CDE",
        vehicle_make="FORD",
        vehicle_model="FIESTA",
        vehicle_year=2018,
        failure_risk=0.27,
        match_scope="exact_band",
        common_faults=[{"component": "Brakes", "risk_level": "12%"}],
    )

    _assert_comparison_truth(message)
    assert "not a diagnosis" in _all_copy(message)


def test_garage_lead_email_does_not_present_the_rate_as_a_vehicle_diagnosis():
    message = generate_lead_email(
        garage_name="Example Garage",
        lead_name=None,
        lead_email="owner@example.com",
        lead_phone=None,
        lead_postcode="SW1A 1AA",
        distance_miles=2.0,
        vehicle_make="FORD",
        vehicle_model="FIESTA",
        vehicle_year=2018,
        failure_risk=0.27,
        match_scope="exact_band",
        top_risks=["brakes"],
        assignment_id="assignment-1",
        risk_percentages={"brakes": 0.12},
    )

    _assert_comparison_truth(message)
    assert "not a diagnosis" in _all_copy(message)


def test_28_day_reminder_keeps_the_saved_rate_non_diagnostic():
    message = generate_mot_reminder_28d(
        email="owner@example.com",
        registration="AB12CDE",
        vehicle_make="FORD",
        vehicle_model="FIESTA",
        vehicle_year=2018,
        mot_expiry_date="2026-08-08",
        failure_risk=0.27,
        match_scope="exact_band",
    )

    _assert_comparison_truth(message)
    assert "not a diagnosis" in _all_copy(message)


def test_reminder_confirmation_keeps_the_saved_rate_non_diagnostic():
    message = generate_mot_reminder_confirmation(
        email="owner@example.com",
        registration="AB12CDE",
        vehicle_make="FORD",
        vehicle_model="FIESTA",
        vehicle_year=2018,
        mot_expiry_date="2026-08-08",
        failure_risk=0.27,
        match_scope="exact_band",
    )

    _assert_comparison_truth(message)
    assert "not a diagnosis" in _all_copy(message)


def test_population_default_emails_call_the_number_a_dataset_wide_reference():
    message = generate_report_email(
        email="owner@example.com",
        registration="AB12CDE",
        vehicle_make="FORD",
        vehicle_model="FIESTA",
        vehicle_year=2018,
        failure_risk=0.28,
        match_scope="population_default",
        common_faults=[],
    )
    copy = _all_copy(message)
    assert "dataset-wide reference mot failure rate" in copy
    assert "comparable-vehicle mot failure rate" not in copy


def test_unknown_year_and_missing_rate_are_not_fabricated_in_garage_email():
    message = generate_lead_email(
        garage_name="Example Garage",
        lead_name=None,
        lead_email="owner@example.com",
        lead_phone=None,
        lead_postcode="SW1A 1AA",
        distance_miles=2.0,
        vehicle_make="FORD",
        vehicle_model="FIESTA",
        vehicle_year=None,
        failure_risk=None,
        top_risks=[],
        assignment_id="assignment-1",
    )
    copy = _all_copy(message)
    assert "0 ford" not in copy
    assert "none ford" not in copy
    assert "failure rate: 0%" not in copy
    assert "estimated job value" not in copy


def test_shared_report_can_be_emailed_without_a_postcode():
    with (
        patch("main.db.is_postgres_available", AsyncMock(return_value=True)),
        patch("main.db.save_report_email_lead", AsyncMock(return_value="lead-1")) as save_lead,
        patch("main.email_configured", return_value=False),
    ):
        response = client.post(
            "/api/email-report",
            json={
                "email": "owner@example.com",
                "registration": "AB12CDE",
                "failure_risk": 0.27,
                "common_faults": [],
            },
        )

    assert response.status_code == 200, response.text
    assert save_lead.await_args.args[0]["postcode"] is None


def test_shared_report_can_set_a_reminder_without_a_postcode():
    with (
        patch("main.db.is_postgres_available", AsyncMock(return_value=True)),
        patch(
            "main.db.save_mot_reminder",
            AsyncMock(return_value={"success": True, "already_subscribed": False}),
        ) as save_reminder,
        patch("main.email_configured", return_value=False),
    ):
        response = client.post(
            "/api/mot-reminder",
            json={
                "email": "owner@example.com",
                "registration": "AB12CDE",
                "failure_risk": 0.27,
            },
        )

    assert response.status_code == 200, response.text
    assert save_reminder.await_args.args[0]["postcode"] is None


def test_reminder_query_preserves_a_real_zero_percent_rate():
    class Connection:
        async def fetch(self, *_args):
            return [{
                "id": "lead-1",
                "email": "owner@example.com",
                "registration": "AB12CDE",
                "vehicle_make": "FORD",
                "vehicle_model": "FIESTA",
                "vehicle_year": 2018,
                "mot_expiry_date": None,
                "failure_risk": 0.0,
                "comparison_scope": "exact_band",
            }]

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    with patch("send_mot_reminders.db.get_pool", AsyncMock(return_value=Pool())):
        reminders = asyncio.run(get_due_reminders())

    assert reminders[0]["failure_risk"] == 0.0
