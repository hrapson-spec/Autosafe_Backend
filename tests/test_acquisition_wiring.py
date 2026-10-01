"""OA-005 source-level guards: collection stays OFF, public pages are wired
to the landing script correctly, the privacy notice says what D-005 requires,
and nothing stores anything on the user's device."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDES = sorted((ROOT / "static" / "guides").glob("*.html"))


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_collection_is_off_in_this_branch():
    ts = read(ROOT / "utils" / "acquisitionEvents.ts")
    assert re.search(r"export const ACQUISITION_COLLECTOR_ENABLED: boolean = false;", ts)
    assert "var ENABLED = false;" in read(ROOT / "static" / "acquisition-landing.js")
    for name in ("Dockerfile", "docker-compose.staging.yml"):
        assert "ACQUISITION_INGEST_ENABLED" not in read(ROOT / name), name
    env_example = ROOT / ".env.example"
    if env_example.exists():
        assert not re.search(r"^ACQUISITION_INGEST_ENABLED=(1|true)", read(env_example), re.M | re.I)


def test_landing_script_is_on_every_public_template_and_guide_but_not_legal_pages():
    base = read(ROOT / "templates" / "seo_base.html")
    assert base.count('<script src="/static/acquisition-landing.js" defer></script>') == 1
    assert len(GUIDES) >= 9
    for page in GUIDES:
        assert read(page).count('<script src="/static/acquisition-landing.js" defer></script>') == 1, page.name
    for legal in ("privacy.html", "terms.html"):
        assert "acquisition-landing" not in read(ROOT / "static" / legal)
    index = read(ROOT / "index.html")
    assert "acquisition-landing" not in index  # the SPA uses utils/acquisitionLanding.ts


def test_every_seo_template_extends_the_base_that_loads_the_script():
    for template in sorted((ROOT / "templates").glob("seo_*.html")):
        text = read(template)
        assert template.name == "seo_base.html" or '{% extends "seo_base.html" %}' in text, template.name


def test_cta_markers_only_sit_on_links_into_the_app():
    for page in [ROOT / "templates" / "seo_base.html", *sorted((ROOT / "templates").glob("seo_*.html")), *GUIDES]:
        for tag in re.findall(r"<a\b[^>]*data-acq-cta[^>]*>", read(page)):
            assert re.search(r'href="(/|/app)"', tag), (page.name, tag)


def test_landing_assets_use_no_storage_cookie_or_beacon():
    for path in (ROOT / "static" / "acquisition-landing.js", ROOT / "utils" / "acquisitionLanding.ts"):
        code = re.sub(r"/\*[\s\S]*?\*/", "", read(path))
        code = re.sub(r"(^|[^:])//.*$", r"\1", code, flags=re.M)
        for token in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "sendBeacon",
                      "XMLHttpRequest", "new Image("):
            assert token not in code, (path.name, token)
    # The public script reads the page's own query in exactly one place: the paid-click test (D-006),
    # which only compares keys/values and never forwards them.
    js = re.sub(r"/\*[\s\S]*?\*/", "", read(ROOT / "static" / "acquisition-landing.js"))
    assert js.count("window.location.search") == 1
    assert "hasPaidMarker(window.location.search)" in js


def test_landing_script_never_reads_the_referrer_beyond_its_origin():
    js = re.sub(r"/\*[\s\S]*?\*/", "", read(ROOT / "static" / "acquisition-landing.js"))
    assert js.count("document.referrer") == 1
    assert "referrer.pathname" not in js and "referrer.search" not in js
    assert ".hostname" in js


NOTICE_FACTS = [
    "First-party measurement of visits and check outcomes",
    "Legitimate interests (UK GDPR Article 6(1)(f))",
    "deleted after 90 days",
    "kept for 25 months",
    "Global Privacy Control",
    "autosafehq@gmail.com",
    "no cookies, local storage, session storage or IndexedDB",
    "We do not store the User-Agent",
    "your IP address (it is used in memory only",
    "Only the site operator can access them",
    "no new service provider",
]


def test_privacy_notice_covers_d005_in_static_and_react_pages():
    for path in (ROOT / "static" / "privacy.html", ROOT / "components" / "PrivacyPage.tsx"):
        text = " ".join(re.sub(r"<[^>]+>", " ", read(path)).split())
        for fact in NOTICE_FACTS:
            assert fact.lower() in text.lower(), (path.name, fact)
        for table_fact in ("First-party measurement events", "Raw events deleted after 90 days"):
            assert table_fact in text, (path.name, table_fact)


def test_privacy_notice_makes_no_claim_the_collector_does_not_meet():
    text = " ".join(re.sub(r"<[^>]+>", " ", read(ROOT / "static" / "privacy.html")).split())
    section = text.split("First-party measurement of visits and check outcomes", 1)[1].split("Changes to this notice", 1)[0]
    for banned in ("anonymous", "cannot be linked", "will never", "no personal data"):
        assert banned not in section.lower(), banned


def test_enable_gate_documents_the_notice_as_a_gate_item():
    collector = read(ROOT / "docs" / "acquisition" / "COLLECTOR.md")
    evidence = read(ROOT / "docs" / "acquisition" / "OA-005_EVIDENCE.md")
    assert "privacy notice" in collector.lower() and "enable gate" in collector.lower()
    assert "notice text is part of the enable gate" in evidence.lower()
