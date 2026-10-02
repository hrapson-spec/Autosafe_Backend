"""OA-003: explicit bearer-report protections.

Header matrix over every response state on the bearer report routes, landing
page non-regression, the report HTML shell transform, and the safe_log_path
trailing-slash redaction.

Route-level tests run against the real ``main.app`` with only the storage /
build boundaries patched (the same pattern as tests/test_report_routes.py).
SPA-shell tests run from a temporary working directory whose ``static/``
holds a fixture ``index.html`` derived from the repo's root ``index.html``
with the rewrites Vite applies (hashed module/CSS assets), so they pass
whether or not ``npm run build`` has been run, and never depend on it.
"""
import asyncio
import contextlib
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

import database as db  # noqa: E402
import main  # noqa: E402
import report_protection  # noqa: E402
from main import app  # noqa: E402
from test_report_routes import (  # noqa: E402
    VALID_VRM,
    _fixture_assessment,
    _fixture_response,
    _naive_future,
)
from utils import safe_log_path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PROTECTED = {
    "x-robots-tag": "noindex, nofollow",
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
}
# Header set the global middleware has always applied; must survive on every
# response, including bearer ones (apart from Referrer-Policy, overridden).
OTHER_SECURITY_HEADERS = (
    "x-content-type-options",
    "x-frame-options",
    "strict-transport-security",
    "content-security-policy",
)

SYNTHETIC_TOKEN = "synthetic-token-0000000001"


def _fixture_index_html() -> str:
    """Root index.html with the rewrites `vite build` applies, so the
    transform is exercised on the same shape as the shipped shell."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/apple-touch-icon.png"', 'href="/assets/apple-touch-icon-AAAA1111.png"')
    html = html.replace('href="/static/favicon.png"', 'href="/assets/favicon-BBBB2222.png"')
    html = html.replace(
        '<script type="module" src="/index.tsx"></script>',
        '<script type="module" crossorigin src="/assets/index-CCCC3333.js"></script>\n'
        '    <link rel="stylesheet" crossorigin href="/assets/index-DDDD4444.css">',
    )
    assert "/index.tsx" not in html
    return html


@contextlib.contextmanager
def _isolated_static():
    """chdir into a temp dir whose static/ is the repo's static/ (minus
    assets) with a fixture index.html -- serve_spa uses CWD-relative paths."""
    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(ROOT / "static", Path(tmp) / "static", ignore=shutil.ignore_patterns("assets", "index.html"))
        (Path(tmp) / "static" / "index.html").write_text(_fixture_index_html(), encoding="utf-8")
        os.chdir(tmp)
        report_protection._shell_cache.clear()
        try:
            yield Path(tmp)
        finally:
            os.chdir(previous)
            report_protection._shell_cache.clear()


def _assert_protected(testcase, resp, label):
    for name, value in PROTECTED.items():
        testcase.assertEqual(resp.headers.get(name), value, f"{label}: {name}")
        # exactly one value, not a comma-joined duplicate
        testcase.assertEqual(len(resp.headers.get_list(name)), 1, f"{label}: {name} duplicated")
    for name in OTHER_SECURITY_HEADERS:
        testcase.assertTrue(resp.headers.get(name), f"{label}: {name} missing")
    testcase.assertEqual(resp.headers["x-frame-options"], "DENY", label)


def _row(**overrides):
    row = {
        "id": "row-prot-1",
        "report_payload": _fixture_response(token=SYNTHETIC_TOKEN),
        "expires_at": _naive_future(),
        "pseudonymised_at": None,
    }
    row.update(overrides)
    return row


class TestBearerReportHeaderMatrix(unittest.TestCase):
    """Invariant 1: every state, API and SPA shell."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client_no_raise = TestClient(app, raise_server_exceptions=False)

    # -- API -------------------------------------------------------------

    def test_get_persisted_success_200(self):
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=_row())):
            resp = self.client.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}")
        self.assertEqual(resp.status_code, 200)
        _assert_protected(self, resp, "GET 200")

    def test_post_create_success_200(self):
        with patch("report_routes.report_service.build_assessment", new=AsyncMock(return_value=_fixture_assessment())), \
             patch("report_routes.db.save_report", new=AsyncMock(return_value="row-id-123")):
            resp = self.client.post("/api/v2/reports", json={"registration": VALID_VRM, "postcode": "SW1A 1AA"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["report_token"])
        _assert_protected(self, resp, "POST 200")

    def test_post_create_degraded_unsaved_200(self):
        with patch("report_routes.db.save_report", new=AsyncMock(side_effect=db.PostgresUnavailable("down"))):
            resp = self.client.post("/api/v2/reports", json={"registration": VALID_VRM})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["report_token"])
        _assert_protected(self, resp, "POST 200 degraded")

    def test_get_invalid_token_404(self):
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=None)):
            resp = self.client.get("/api/v2/reports/unknown-token-000001")
        self.assertEqual(resp.status_code, 404)
        _assert_protected(self, resp, "GET 404")

    def test_get_bad_shape_token_404(self):
        resp = self.client.get("/api/v2/reports/invalid")
        self.assertEqual(resp.status_code, 404)
        _assert_protected(self, resp, "GET 404 bad shape")

    def test_get_expired_410(self):
        row = _row(expires_at=datetime(2020, 1, 1))
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = self.client.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}")
        self.assertEqual(resp.status_code, 410)
        _assert_protected(self, resp, "GET 410")

    def test_get_storage_unavailable_503(self):
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(side_effect=db.PostgresUnavailable("down"))):
            resp = self.client.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}")
        self.assertEqual(resp.status_code, 503)
        _assert_protected(self, resp, "GET 503")

    def test_post_validation_422(self):
        resp = self.client.post("/api/v2/reports", json={"registration": VALID_VRM, "mileage_user": 45000})
        self.assertEqual(resp.status_code, 422)
        _assert_protected(self, resp, "POST 422")

    def test_post_invalid_registration_400(self):
        resp = self.client.post("/api/v2/reports", json={"registration": "!!!!"})
        self.assertEqual(resp.status_code, 400)
        _assert_protected(self, resp, "POST 400")

    def test_undeclared_query_400(self):
        resp = self.client.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}?bogus=1")
        self.assertEqual(resp.status_code, 400)
        _assert_protected(self, resp, "GET 400")

    def test_method_not_allowed_405(self):
        resp = self.client.put(f"/api/v2/reports/{SYNTHETIC_TOKEN}")
        self.assertEqual(resp.status_code, 405)
        _assert_protected(self, resp, "PUT 405")

    def test_rate_limited_429_get_and_post(self):
        fake_limit = MagicMock()
        fake_limit.error_message = None
        fake_limit.limit = "60 per 1 minute"
        exc = RateLimitExceeded(fake_limit)
        with patch.object(main.limiter, "_check_request_limit", side_effect=exc):
            get_resp = self.client.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}")
            post_resp = self.client.post("/api/v2/reports", json={"registration": VALID_VRM})
        for label, resp in (("GET 429", get_resp), ("POST 429", post_resp)):
            self.assertEqual(resp.status_code, 429, label)
            self.assertEqual(resp.json()["error_code"], "rate_limited", label)
            _assert_protected(self, resp, label)

    def test_guarded_internal_error_500(self):
        broken = AsyncMock(side_effect=RuntimeError("boom-secret-detail"))
        with patch("report_routes.report_service.build_assessment", new=broken):
            resp = self.client.post("/api/v2/reports", json={"registration": VALID_VRM})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["error_code"], "internal_error")
        _assert_protected(self, resp, "POST 500 guarded")

    def test_unhandled_exception_500_api(self):
        """An exception that escapes _guard_internal_errors is answered by
        global_exception_handler, which Starlette runs in
        ServerErrorMiddleware -- outside the @app.middleware layers -- so it
        must apply the headers itself."""
        with patch("report_routes._guard_internal_errors", new=AsyncMock(side_effect=RuntimeError("escaped"))):
            get_resp = self.client_no_raise.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}")
            post_resp = self.client_no_raise.post("/api/v2/reports", json={"registration": VALID_VRM})
        for label, resp in (("GET unhandled 500", get_resp), ("POST unhandled 500", post_resp)):
            self.assertEqual(resp.status_code, 500, label)
            self.assertIn("error_id", resp.json(), label)  # global handler body
            _assert_protected(self, resp, label)

    def test_unhandled_exception_500_spa_shell(self):
        with _isolated_static(), patch("main.report_shell_html", side_effect=RuntimeError("escaped")):
            resp = self.client_no_raise.get(f"/app/report/{SYNTHETIC_TOKEN}")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("error_id", resp.json())
        _assert_protected(self, resp, "SPA unhandled 500")

    def test_unhandled_exception_on_public_route_is_unchanged(self):
        """Public routes' unhandled 500 keeps its pre-OA-003 header set (the
        handler applies nothing there); in particular no bearer protections."""
        with _isolated_static(), patch("seo_pages._not_found_html", side_effect=RuntimeError("escaped")):
            resp = self.client_no_raise.get("/not-a-route-xyz")
        self.assertEqual(resp.status_code, 500)
        for name in PROTECTED:
            self.assertNotIn(name, resp.headers)

    # -- SPA shell -------------------------------------------------------

    def test_spa_report_routes_have_headers(self):
        with _isolated_static():
            for path in (
                f"/app/report/{SYNTHETIC_TOKEN}",   # persisted / restored link
                "/app/report/unsaved",              # inline unsaved
                "/app/report/invalid",              # invalid token (shell is token-agnostic)
                "/app/report/expired-token-0001",
                "/app/report",
                "/app/report/",
                f"/app/report/{SYNTHETIC_TOKEN}?x=1",
            ):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, path)
                self.assertIn("text/html", resp.headers["content-type"], path)
                _assert_protected(self, resp, path)

    def test_case_variant_report_paths_get_headers_and_neutral_shell(self):
        """react-router matches case-insensitively, so /app/Report/<token>
        renders the report client-side; it must get the same controls and
        the same neutral shell as /app/report/<token>."""
        with _isolated_static():
            expected = report_protection.build_report_shell(_fixture_index_html())
            for path in (
                f"/app/Report/{SYNTHETIC_TOKEN}",
                f"/app/REPORT/{SYNTHETIC_TOKEN}",
                "/app/Report/invalid-w4-probe",
                "/app/rEpOrT",
            ):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, path)
                _assert_protected(self, resp, path)
                self.assertEqual(resp.text, expected, path)
                self.assertEqual(report_protection.leftover_homepage_metadata(resp.text), [], path)

    def test_case_variant_paths_that_miss_the_spa_still_get_headers(self):
        """/App/report/x and /api/v2/Reports/x are not served by the SPA
        catch-all or the API router (both case-sensitive), but a browser can
        still be sent there; whatever answers must carry the controls."""
        with _isolated_static():
            for path in (
                f"/App/report/{SYNTHETIC_TOKEN}",
                f"/APP/REPORT/{SYNTHETIC_TOKEN}",
                f"/api/v2/Reports/{SYNTHETIC_TOKEN}",
                f"/API/V2/REPORTS/{SYNTHETIC_TOKEN}",
            ):
                resp = self.client.get(path)
                self.assertIn(resp.status_code, (200, 404), path)
                _assert_protected(self, resp, path)

    def test_spa_report_shell_trailing_slash_api_redirect_is_protected(self):
        resp = self.client.get(f"/api/v2/reports/{SYNTHETIC_TOKEN}/", follow_redirects=False)
        self.assertIn(resp.status_code, (200, 301, 307, 308, 404))
        _assert_protected(self, resp, "API trailing slash")


class TestLandingPagesUnchanged(unittest.TestCase):
    """Invariant 2: public landing pages keep global headers; no bearer
    protections leak onto them."""

    LANDING = (
        "/",
        "/app",
        "/app/",
        "/guides/common-mot-failures",
        "/privacy",
        "/terms",
        "/mot-check/",
        "/sitemap.xml",
        "/robots.txt",
        "/api/version",
        "/app/reportsx",  # not a report route: prefix must not over-match
    )

    def test_landing_headers(self):
        client = TestClient(app, raise_server_exceptions=False)
        with _isolated_static():
            for path in self.LANDING:
                resp = client.get(path)
                self.assertLess(resp.status_code, 500, path)
                self.assertEqual(
                    resp.headers["referrer-policy"], "strict-origin-when-cross-origin", path
                )
                self.assertNotIn("x-robots-tag", resp.headers, path)
                self.assertNotEqual(resp.headers.get("cache-control"), "no-store", path)
                for name in OTHER_SECURITY_HEADERS:
                    self.assertTrue(resp.headers.get(name), f"{path}: {name}")

    def test_app_shell_html_is_untouched(self):
        client = TestClient(app)
        with _isolated_static():
            resp = client.get("/app")
        self.assertEqual(resp.text, _fixture_index_html())

    def test_path_matcher(self):
        yes = ["/app/report/x", "/app/report", "/app/report/", "/api/v2/reports", "/api/v2/reports/x", "//app/report/x",
               "/app/Report/x", "/app/REPORT/x", "/App/report/x", "/api/v2/Reports/x", "/API/V2/REPORTS"]
        no = ["/", "/app", "/app/", "/app/reports", "/app/reportx", "/app/Reportx", "/api/v2/reportsx", "/api/v2/version",
              "/api/version", "/guides/report", "/mot-check/", "/api/reports/x"]
        for p in yes:
            self.assertTrue(report_protection.is_bearer_report_path(p), p)
        for p in no:
            self.assertFalse(report_protection.is_bearer_report_path(p), p)


class TestReportShellTransform(unittest.TestCase):
    """Invariant 3."""

    HEAD_SCRIPT_RE = re.compile(r"<script\b(?![^>]*ld\+json)[^>]*>.*?</script>", re.S | re.I)

    def setUp(self):
        self.source = _fixture_index_html()
        self.shell = report_protection.build_report_shell(self.source)

    def test_homepage_metadata_removed(self):
        self.assertEqual(report_protection.leftover_homepage_metadata(self.shell), [])
        for needle in (
            "rel=\"canonical\"", "https://www.autosafe.one/\"", "name=\"description\"", "og:", "twitter:",
            "application/ld+json", "WebSite", "Organization", "MOT Records",
            "Compare your car with recorded MOT outcomes", "148 million recorded",
        ):
            self.assertNotIn(needle, self.shell, needle)
        # source really did contain them (guards a vacuous pass)
        for needle in ("rel=\"canonical\"", "og:title", "twitter:card", "application/ld+json", "<noscript>"):
            self.assertIn(needle, self.source)

    def test_neutral_title_and_noindex(self):
        self.assertEqual(self.shell.count("<title>AutoSafe report</title>"), 1)
        self.assertEqual(self.shell.count('<meta name="robots" content="noindex, nofollow" />'), 1)
        self.assertEqual(self.shell.lower().count("<title"), 1)

    def test_bundle_scripts_styles_and_analytics_loaders_byte_identical(self):
        # Every non-JSON-LD script (gtag bootstrap, Umami loader + before-send
        # filter, module bundle) and the stylesheet link survive exactly.
        before = self.HEAD_SCRIPT_RE.findall(self.source)
        after = self.HEAD_SCRIPT_RE.findall(self.shell)
        self.assertEqual(before, after)
        self.assertGreaterEqual(len(before), 3)
        self.assertIn('src="/assets/index-CCCC3333.js"', self.shell)
        self.assertIn('href="/assets/index-DDDD4444.css"', self.shell)
        self.assertIn("window.autosafeAnalyticsAllowed = !/^\\/app\\/report\\//", self.shell)
        self.assertIn("autosafeUmamiBeforeSend", self.shell)
        self.assertIn("data-auto-track", self.shell)
        self.assertIn('<div id="root">', self.shell)
        self.assertIn("charset", self.shell)
        self.assertIn('name="viewport"', self.shell)

    def test_served_from_built_shell_and_cached_by_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.html")
            Path(path).write_text(self.source, encoding="utf-8")
            report_protection._shell_cache.clear()
            first = report_protection.report_shell_html(path)
            self.assertIs(first, report_protection.report_shell_html(path))  # cache hit
            Path(path).write_text(self.source.replace("CCCC3333", "EEEE5555"), encoding="utf-8")
            os.utime(path, ns=(1, 1_000_000_000_123))
            second = report_protection.report_shell_html(path)
            self.assertIn("EEEE5555", second)
            self.assertNotIn("EEEE5555", first)

    def test_missing_index_returns_none(self):
        self.assertIsNone(report_protection.report_shell_html("/nonexistent/static/index.html"))

    def test_report_route_serves_transformed_shell_and_other_app_routes_do_not(self):
        client = TestClient(app)
        with _isolated_static():
            report = client.get(f"/app/report/{SYNTHETIC_TOKEN}").text
            home = client.get("/app").text
            unsaved = client.get("/app/report/unsaved").text
        self.assertEqual(report, report_protection.build_report_shell(_fixture_index_html()))
        self.assertEqual(unsaved, report)
        self.assertEqual(home, _fixture_index_html())
        self.assertNotIn(SYNTHETIC_TOKEN, report)  # shell never echoes the token

    def test_built_shell_if_present_transforms_cleanly(self):
        built = ROOT / "static" / "index.html"
        if not built.exists():
            self.skipTest("static/index.html not built")
        html = report_protection.build_report_shell(built.read_text(encoding="utf-8"))
        self.assertEqual(report_protection.leftover_homepage_metadata(html), [])
        self.assertIn('<meta name="robots" content="noindex, nofollow" />', html)
        self.assertRegex(html, r'<script type="module" crossorigin src="/assets/index-[^"]+\.js">')


class TestLogRedaction(unittest.TestCase):
    def test_safe_log_path_redacts_token_with_and_without_trailing_slash(self):
        self.assertEqual(safe_log_path("/api/v2/reports/secrettoken123"), "/api/v2/reports/{token}")
        self.assertEqual(safe_log_path("/app/report/secrettoken123"), "/app/report/{token}")
        self.assertEqual(safe_log_path("/api/v2/reports/secrettoken123/"), "/api/v2/reports/{token}")
        self.assertEqual(safe_log_path("/app/report/secrettoken123/"), "/app/report/{token}")
        self.assertEqual(safe_log_path("/api/v2/reports"), "/api/v2/reports")

    def test_safe_log_path_is_case_insensitive_and_covers_doubled_slash_and_extra_segments(self):
        cases = {
            "/app/Report/secrettoken123": "/app/Report/{token}",
            "/App/REPORT/secrettoken123/": "/App/REPORT/{token}",
            "/api/v2/Reports/secrettoken123": "/api/v2/Reports/{token}",
            "/app/report//secrettoken123": "/app/report/{token}",
            "/app/report/secrettoken123/extra": "/app/report/{token}",
            "/api/v2/reports/secrettoken123/extra/more": "/api/v2/reports/{token}",
            "//app/report/secrettoken123": "//app/report/{token}",
        }
        for raw, expected in cases.items():
            self.assertEqual(safe_log_path(raw), expected, raw)
            self.assertNotIn("secrettoken123", safe_log_path(raw), raw)

    def test_safe_log_path_leaves_non_report_paths_alone(self):
        for p in ("/", "/app", "/app/report", "/app/report/", "/app/reports/x", "/app/reportx/tok",
                  "/api/v2/reports/", "/api/version", "/guides/mot-cost"):
            self.assertEqual(safe_log_path(p), p, p)


class TestClientRegexCaseFlags(unittest.TestCase):
    """Every client-side bearer-route gate must be case-insensitive. The
    behavioural versions run in vitest (utils/analytics.test.ts) against the
    real source text; this is the source-level guard that no gate regresses
    to a case-sensitive literal."""

    LITERAL = re.compile(r"/\^\\/app\\/report\\//([a-z]*)")

    def test_every_report_route_regex_has_the_i_flag(self):
        expected = {
            "index.html": 2,        # autosafeAnalyticsAllowed + inline before-send
            "static/umami.js": 2,   # loader gate + before-send
            "static/consent.js": 1,  # standalone/SEO pages' mirror of the gate
            "utils/analytics.ts": 1,
        }
        for rel, count in expected.items():
            text = (ROOT / rel).read_text(encoding="utf-8")
            flags = self.LITERAL.findall(text)
            self.assertEqual(len(flags), count, f"{rel}: report-route regex literals")
            for f in flags:
                self.assertIn("i", f, f"{rel}: regex literal lacks the i flag")

    def test_no_unreviewed_report_route_regex_elsewhere_in_client_sources(self):
        for rel in ("App.tsx", "index.tsx"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertEqual(self.LITERAL.findall(text), [], rel)
        for path in [*sorted((ROOT / "components").glob("*.tsx")), *sorted((ROOT / "services").glob("*.ts")),
                     *sorted((ROOT / "hooks").glob("*.ts*"))]:
            if ".test." in path.name:
                continue
            self.assertEqual(self.LITERAL.findall(path.read_text(encoding="utf-8")), [], path.name)


if __name__ == "__main__":
    unittest.main()
