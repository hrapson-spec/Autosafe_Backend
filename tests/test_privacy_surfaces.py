"""Source-level guards for browser/log transport privacy boundaries."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_public_sources_never_generate_registration_query_links():
    sources = [ROOT / "index.html", ROOT / "App.tsx", *sorted((ROOT / "templates").glob("*.html"))]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "?reg=" not in joined
    assert 'name="reg"' not in joined
    assert "searchParams.get('reg')" not in joined


def test_canonical_host_redirect_does_not_propagate_identifier_queries():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'new_url += f"?{request.url.query}"' not in source
    assert "_SENSITIVE_QUERY_KEYS" in source
    assert "request.query_params.multi_items()" in source


def test_bearer_report_routes_disable_automatic_analytics():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "!/^\\/app\\/report\\//" in index
    assert "if (!window.autosafeAnalyticsAllowed) return" in index
    assert "if (window.autosafeAnalyticsAllowed)" in index
    assert "data-auto-track', 'false'" in index
    assert "send_page_view: false" in index
    assert "G-8PV2HG1QDC" not in index
    assert '<script defer src="https://umami' not in index


def test_standalone_static_pages_use_the_shared_consent_gate():
    pages = [
        ROOT / "static" / "privacy.html",
        ROOT / "static" / "terms.html",
        *sorted((ROOT / "static" / "guides").glob("*.html")),
    ]
    for page in pages:
        source = page.read_text(encoding="utf-8")
        assert '<script src="/static/consent.js"></script>' in source
        assert '<script async src="https://www.googletagmanager.com/' not in source
        assert "gtag('config'" not in source

    consent = (ROOT / "static" / "consent.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('autosafe_consent')" in consent
    assert "if (!analyticsAllowed || loaded) return" in consent
    assert "['reg', 'registration', 'vrm', 'postcode']" in consent
    assert "!/^\\/app\\/report\\//" in consent


def test_public_pages_do_not_contact_remote_font_hosts_before_consent():
    sources = [
        ROOT / "index.html",
        ROOT / "seo_pages.py",
        ROOT / "templates" / "seo_base.html",
        *sorted((ROOT / "static" / "guides").glob("*.html")),
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "fonts.googleapis.com" not in joined
    assert "fonts.gstatic.com" not in joined

    csp_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in csp_source
    assert "fonts.gstatic.com" not in csp_source


def test_runtime_access_log_is_disabled_and_email_logs_exclude_content():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    email_service = (ROOT / "email_service.py").read_text(encoding="utf-8")
    assert "--no-access-log" in dockerfile
    assert "Would send email to {_mask_email(to_email)}: {subject}" not in email_service
    assert "HTML Content" not in email_service
    assert "{response.text}" not in email_service


def test_runtime_logs_do_not_interpolate_registration_postcode_or_exception_text():
    runtime_sources = [
        ROOT / "main.py",
        ROOT / "database.py",
        ROOT / "report_routes.py",
        ROOT / "report_service.py",
        ROOT / "send_mot_reminders.py",
        ROOT / "lead_matcher.py",
        ROOT / "postcode_service.py",
        ROOT / "dvla_client.py",
        ROOT / "build_db.py",
        ROOT / "model_v55.py",
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources)
    assert 'logger.info(f"Demo mode: generating mock data for {registration}")' not in joined
    assert "exc_info=True" not in joined

    for line in joined.splitlines():
        if "logger." in line:
            assert "{lead_postcode}" not in line
            assert "{postcode}" not in line
            assert ": {e}" not in line
            assert ": {exc}" not in line


def test_dvsa_logging_uses_shared_keyed_vrm_digest():
    source = (ROOT / "dvsa_client.py").read_text(encoding="utf-8")
    assert "hash_vrm(vrm)" in source
    assert "hashlib.sha256(vrm.encode())" not in source


def test_runtime_error_responses_do_not_echo_exception_text():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    dvsa_source = (ROOT / "dvsa_client.py").read_text(encoding="utf-8")
    dvla_source = (ROOT / "dvla_client.py").read_text(encoding="utf-8")

    assert "detail=str(e)" not in main_source
    assert '"error": str(e)' not in main_source
    assert "note=f\"DVSA API unavailable: {str(e)}\"" not in main_source
    assert "note=f\"Feature engineering error: {str(e)}\"" not in main_source
    assert "note=f\"Model prediction error: {str(e)}\"" not in main_source
    assert "DVSA API connection error: {str(e)}" not in dvsa_source
    assert "OAuth token request failed: {str(e)}" not in dvsa_source
    assert "DVLA API connection error: {e}" not in dvla_source
    assert "error_data['message']" not in dvla_source


def test_backlog_verification_covers_every_field_the_migration_removes():
    source = (ROOT / "migrations" / "pseudonymize_backlog.py").read_text(encoding="utf-8")
    verification = source.split("async def run_verification", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# Orchestration",
        1,
    )[0]
    for field in ("registration", "postcode", "report_payload", "report_token"):
        assert f"{field} IS NOT NULL" in verification
    assert '"stale_sensitive_fields"' in verification
    assert '"pseudonymised_with_sensitive_fields"' in verification


def test_public_record_count_is_derived_from_primary_dataset_metadata():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "DATASET_TOTAL_TESTS // 1_000_000" in source
    assert '"142M+"' not in source


def test_public_privacy_notice_does_not_claim_unimplemented_outcome_linkage():
    sources = [
        (ROOT / "components" / "PrivacyPage.tsx").read_text(encoding="utf-8"),
        (ROOT / "static" / "privacy.html").read_text(encoding="utf-8"),
    ]
    for source in sources:
        lowered = source.lower()
        assert "we do not currently link reports to later mot outcomes" in lowered
        assert "we use the record to operate and monitor the service and to evaluate" not in lowered
        assert "rights are not absolute" in lowered
        assert "cannot recall details already sent" in lowered
        assert "partner garages" in lowered and "responsible for their own subsequent handling" in lowered


def test_release_does_not_ship_unreviewed_outbound_publishing_agents():
    """Retired marketing agents must not bypass the public-copy/privacy gates."""
    assert not (ROOT / "agents" / "reddit_agent.py").exists()
    assert not (ROOT / "agents" / "data_story_publisher.py").exists()
    assert not any((ROOT / "data_stories").glob("*.py"))
    assert not (ROOT / "config" / "media_contacts.json").exists()


def test_public_privacy_notice_discloses_conditional_prediction_processing():
    """V55 integration: the automated-decision-making notice must describe
    BOTH states (per-vehicle predicted chance when available, labelled
    comparison otherwise) and deny diagnosis/outcome determination for both."""
    sources = [
        (ROOT / "components" / "PrivacyPage.tsx").read_text(encoding="utf-8"),
        (ROOT / "static" / "privacy.html").read_text(encoding="utf-8"),
    ]
    for source in sources:
        lowered = source.lower()
        assert "predicted chance of failing its next mot" in lowered
        assert "closest supported group" in lowered
        assert "neither result diagnoses this vehicle" in lowered
