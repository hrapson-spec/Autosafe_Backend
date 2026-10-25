"""Garage API settings (environment driven, no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass

LONDON = "Europe/London"


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    # Public origin used to build sign-in links (the API serves the confirm page).
    base_url: str = "https://garage.autosafe.one"
    app_url: str = "https://garage.autosafe.one/"
    allowed_origins: tuple = ("https://garage.autosafe.one",)
    cookie_name: str = "__Host-garage_session"
    link_ttl_minutes: int = 15
    session_ttl_days: int = 14
    invite_ttl_days: int = 14
    preview_ttl_minutes: int = 60
    max_import_rows: int = 200
    fair_use_assessments_per_day: int = 50
    dvsa_max_rps: int = 5
    login_links_per_hour: int = 5
    outcome_check_days: tuple = (1, 3, 7, 14)
    outcome_hour_london: int = 7
    job_max_attempts: int = 3
    job_backoff_seconds: int = 120
    job_visibility_timeout_seconds: int = 600
    retention_months: int = 12
    # Sanity window for appointment dates, relative to today (London).
    max_days_ahead: int = 366
    max_days_back: int = 30
    outcome_window_days: int = 14

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        base = e("GARAGE_BASE_URL", "https://garage.autosafe.one").rstrip("/")
        origins = tuple(o.strip() for o in e("GARAGE_ALLOWED_ORIGINS", base).split(",") if o.strip())
        return cls(
            database_url=e("GARAGE_DATABASE_URL") or e("DATABASE_URL", ""),
            base_url=base,
            app_url=e("GARAGE_APP_URL", base + "/"),
            allowed_origins=origins,
        )
