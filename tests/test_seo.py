"""
SEO Regression Tests
====================

Validates SEO landing pages return correct status codes, contain required
elements (canonical tags, structured data, breadcrumbs), and don't regress.

Run with: pytest tests/test_seo.py -v
"""
import json
import os
import re
import sqlite3
import sys
import unittest
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from report_contract import DATASET_ARTIFACT_REVISION
import page_revisions
import seo_pages
from seo_pages import (
    _align_component_rates,
    _model_totals_from_groups,
    _query_model_age_bands,
    _query_model_overall,
    _summarise_models,
)

# Enter context manager so lifespan (including SEO data init) runs before tests
client = TestClient(app)
client.__enter__()


def teardown_module():
    """Clean up TestClient context after all tests."""
    try:
        client.__exit__(None, None, None)
    except Exception:
        pass


class TestSeoPages(unittest.TestCase):
    """Core SEO page availability and structure."""

    def test_seo_index_returns_200(self):
        r = client.get("/mot-check/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_seo_index_contains_make_links(self):
        r = client.get("/mot-check/")
        # Should have at least some make links
        self.assertIn("/mot-check/ford/", r.text.lower())
        self.assertIn("/mot-check/vauxhall/", r.text.lower())

    def test_seo_make_page_ford(self):
        r = client.get("/mot-check/ford/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ford", r.text)

    def test_make_summary_weights_rates_by_test_count(self):
        summary = _summarise_models([
            {"total_tests": 100, "total_failures": 10},
            {"total_tests": 900, "total_failures": 270},
        ])
        self.assertEqual(summary["total_tests"], 1_000)
        self.assertEqual(summary["total_failures"], 280)
        self.assertEqual(summary["fail_rate"], 0.28)

    def test_partial_component_coverage_is_omitted_not_averaged_selectively(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE risks (
                model_id TEXT, age_band TEXT, mileage_band TEXT,
                Total_Tests INTEGER, Total_Failures INTEGER,
                Risk_Brakes REAL, Risk_Suspension REAL, Risk_Tyres REAL,
                Risk_Steering REAL, Risk_Visibility REAL,
                Risk_Lamps_Reflectors_And_Electrical_Equipment REAL,
                Risk_Body_Chassis_Structure REAL
            )"""
        )
        rows = [
            ("FORD FIESTA", "3-5", "0-30k", 100, 20, 0.10, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01),
            ("FORD FIESTA ST", "3-5", "0-30k", 100, 30, None, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03),
        ]
        conn.executemany("INSERT INTO risks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

        overall = _query_model_overall(conn, "FORD", "FIESTA")
        age_bands = _query_model_age_bands(conn, "FORD", "FIESTA")
        conn.close()

        self.assertIsNotNone(overall)
        self.assertNotIn("Brakes", {item["name"] for item in overall["components"]})
        self.assertNotIn("Brakes", age_bands[0]["components"])
        self.assertIn("Suspension", {item["name"] for item in overall["components"]})

    def test_component_comparison_includes_only_categories_supported_for_both_groups(self):
        aligned = _align_component_rates(
            [{"name": "Brakes", "risk": 0.1}, {"name": "Tyres", "risk": 0.2}],
            [{"name": "Tyres", "risk": 0.3}],
        )
        self.assertEqual(aligned, [{"name": "Tyres", "risk1": 0.2, "risk2": 0.3}])

    def test_seo_make_page_invalid_returns_404(self):
        r = client.get("/mot-check/nonexistent-make/")
        self.assertEqual(r.status_code, 404)

    def test_seo_model_page_ford_fiesta(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Fiesta", r.text)

    def test_seo_model_page_invalid_model_returns_404(self):
        r = client.get("/mot-check/ford/nonexistent-model/")
        self.assertEqual(r.status_code, 404)

    def test_evidence_pillar_uses_primary_dataset_metadata(self):
        r = client.get("/will-my-car-pass-mot/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("148,509,908", r.text)
        self.assertIn("39,969,903", r.text)
        self.assertIn("26.9%", r.text)

    def test_broken_first_mot_insights_are_retired_to_honest_guides(self):
        cases = {
            "/insights/unreliable-3-year-old-cars-2026/": "/guides/mot-failure-rates-by-car",
            "/insights/march-mot-rush-2026/": "/guides/first-mot-guide",
        }
        for path, target in cases.items():
            r = client.get(path, follow_redirects=False)
            self.assertEqual(r.status_code, 301)
            self.assertEqual(r.headers["location"], target)

    def test_missing_insights_backend_is_retired_to_the_data_guide(self):
        for path in ("/insights/", "/insights/legacy-story/"):
            r = client.get(path, follow_redirects=False)
            self.assertEqual(r.status_code, 301)
            self.assertEqual(r.headers["location"], "/guides/mot-failure-rates-by-car")

    def test_model_year_url_redirects_to_stable_model_group_evidence(self):
        model_year = date.today().year - 8
        r = client.get(f"/mot-check/ford/fiesta/{model_year}/", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/mot-check/ford/fiesta/")

    def test_unsupported_local_pages_are_retired_to_the_report_form(self):
        r = client.get("/local-mot/london/", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/")


class TestSeoCanonicalTags(unittest.TestCase):
    """Verify canonical URLs are present and correct."""

    def test_model_page_has_canonical(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn('<link rel="canonical"', r.text)
        self.assertIn("/mot-check/ford/fiesta/", r.text)

    def test_make_page_has_canonical(self):
        r = client.get("/mot-check/ford/")
        self.assertIn('<link rel="canonical"', r.text)

    def test_index_page_has_canonical(self):
        r = client.get("/mot-check/")
        self.assertIn('<link rel="canonical"', r.text)


class TestSeoStructuredData(unittest.TestCase):
    """Verify JSON-LD structured data is present and parseable."""

    def _extract_jsonld(self, html: str) -> list[dict]:
        """Extract all JSON-LD scripts from HTML."""
        pattern = r'<script type="application/ld\+json">(.*?)</script>'
        matches = re.findall(pattern, html, re.DOTALL)
        results = []
        for m in matches:
            try:
                results.append(json.loads(m))
            except json.JSONDecodeError:
                self.fail(f"Invalid JSON-LD: {m[:100]}...")
        return results

    def test_model_page_has_faq_schema(self):
        r = client.get("/mot-check/ford/fiesta/")
        schemas = self._extract_jsonld(r.text)
        types = [s.get("@type") for s in schemas]
        self.assertIn("FAQPage", types)

    def test_model_page_has_dataset_schema(self):
        r = client.get("/mot-check/ford/fiesta/")
        schemas = self._extract_jsonld(r.text)
        types = [s.get("@type") for s in schemas]
        self.assertIn("Dataset", types)

    def test_model_page_has_breadcrumb_schema(self):
        r = client.get("/mot-check/ford/fiesta/")
        schemas = self._extract_jsonld(r.text)
        types = [s.get("@type") for s in schemas]
        self.assertIn("BreadcrumbList", types)

    def test_dataset_schema_has_date_modified(self):
        r = client.get("/mot-check/ford/fiesta/")
        schemas = self._extract_jsonld(r.text)
        dataset = next((s for s in schemas if s.get("@type") == "Dataset"), None)
        self.assertIsNotNone(dataset, "No Dataset schema found")
        self.assertEqual(dataset.get("dateModified"), DATASET_ARTIFACT_REVISION)


class TestSeoMetaTags(unittest.TestCase):
    """Verify essential meta tags are present."""

    def test_model_page_has_meta_description(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn('<meta name="description"', r.text)
        # Description should mention failure or mot - use a wider pattern for multi-line content
        desc_match = re.search(r'<meta name="description" content="(.+?)"', r.text, re.DOTALL)
        if desc_match is None:
            # Try alternate pattern where content might use single quotes or be further along
            desc_match = re.search(r'<meta name="description"[^>]*content="(.+?)"', r.text, re.DOTALL)
        self.assertIsNotNone(desc_match, "Meta description content attribute not found")
        desc = desc_match.group(1).lower()
        self.assertTrue("failure" in desc or "mot" in desc or "fail" in desc,
                        f"Meta description does not mention failure/MOT: {desc[:100]}")

    def test_model_page_has_og_tags(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn('property="og:title"', r.text)
        self.assertIn('property="og:description"', r.text)

    def test_model_page_title_contains_make_model(self):
        r = client.get("/mot-check/ford/fiesta/")
        title_match = re.search(r'<title>(.*?)</title>', r.text, re.DOTALL)
        self.assertIsNotNone(title_match)
        title = title_match.group(1).lower()
        self.assertIn("ford", title)
        self.assertIn("fiesta", title)


class TestSeoFreshnessSignals(unittest.TestCase):
    """Verify artifact freshness is not relabelled as source coverage."""

    def test_footer_identifies_the_checked_in_artifact_revision(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertRegex(
            r.text,
            rf"Checked-in dataset revision:\s*{re.escape(DATASET_ARTIFACT_REVISION)}",
        )

    def test_footer_does_not_invent_a_source_coverage_date(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertNotIn("Data last updated:", r.text)
        self.assertIn("source coverage date is not encoded", r.text)


class TestSeoComparisons(unittest.TestCase):
    """Verify comparison pages work."""

    def test_comparison_page_returns_200(self):
        r = client.get("/mot-check/compare/ford-fiesta-vs-vauxhall-corsa/")
        self.assertEqual(r.status_code, 200)

    def test_comparison_page_invalid_returns_404(self):
        r = client.get("/mot-check/compare/fake-car-vs-other-car/")
        self.assertEqual(r.status_code, 404)


class TestSitemap(unittest.TestCase):
    """Verify sitemap is generated correctly."""

    def test_sitemap_returns_xml(self):
        r = client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("xml", r.headers["content-type"])

    def test_sitemap_contains_model_urls(self):
        r = client.get("/sitemap-models.xml")
        self.assertIn("/mot-check/ford/fiesta/", r.text)

    def test_sitemap_contains_make_urls(self):
        r = client.get("/sitemap-makes.xml")
        self.assertIn("/mot-check/ford/", r.text)

    def test_sitemap_index_does_not_advertise_retired_local_pages(self):
        r = client.get("/sitemap.xml")
        self.assertNotIn("sitemap-local.xml", r.text)

    def test_retired_local_sitemap_redirects_to_the_index(self):
        r = client.get("/sitemap-local.xml", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/sitemap.xml")

    def test_dataset_driven_sitemaps_are_never_older_than_the_artifact_revision(self):
        for path in ("/sitemap-makes.xml", "/sitemap-models.xml", "/sitemap-comparisons.xml"):
            r = client.get(path)
            for lastmod in re.findall(r"<lastmod>(.*?)</lastmod>", r.text):
                self.assertGreaterEqual(lastmod, DATASET_ARTIFACT_REVISION, path)


class TestNoindexDirectives(unittest.TestCase):
    """Verify noindex meta tags on thin permutation pages."""

    def test_age_band_page_has_noindex(self):
        r = client.get("/mot-check/ford/fiesta/3-5-years/")
        if r.status_code == 200:
            self.assertIn('content="noindex, follow"', r.text)

    def test_model_page_does_not_have_noindex(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('content="noindex', r.text)

    def test_age_band_canonical_points_to_parent(self):
        r = client.get("/mot-check/ford/fiesta/3-5-years/")
        if r.status_code == 200:
            self.assertIn('/mot-check/ford/fiesta/', r.text)


class TestSitemapIndex(unittest.TestCase):
    """Verify sitemap index architecture."""

    def test_sitemap_xml_is_index(self):
        r = client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("sitemapindex", r.text)
        self.assertIn("sitemap-content.xml", r.text)
        self.assertIn("sitemap-models.xml", r.text)

    def test_sub_sitemap_models_returns_xml(self):
        r = client.get("/sitemap-models.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/mot-check/ford/fiesta/", r.text)

    def test_sub_sitemap_makes_returns_xml(self):
        r = client.get("/sitemap-makes.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/mot-check/ford/", r.text)

    def test_sitemap_does_not_contain_age_band_urls(self):
        """Age-band pages are noindex and must NOT appear in any sitemap."""
        for path in ["/sitemap.xml", "/sitemap-models.xml", "/sitemap-content.xml"]:
            r = client.get(path)
            self.assertNotIn("3-5-years", r.text,
                             f"Age-band slug found in {path}")

    def test_sitemap_excludes_retired_first_mot_insight_urls(self):
        r = client.get("/sitemap-content.xml")
        self.assertNotIn("unreliable-3-year-old-cars-2026", r.text)
        self.assertNotIn("march-mot-rush-2026", r.text)
        self.assertNotIn("/insights/", r.text)


class TestLegacySlugRedirects(unittest.TestCase):
    """Verify legacy age-band slugs redirect to current ones."""

    def test_legacy_0_3_years_redirects(self):
        r = client.get("/mot-check/ford/fiesta/0-3-years/", follow_redirects=False)
        self.assertIn(r.status_code, [301, 307])

    def test_legacy_10_15_years_redirects(self):
        r = client.get("/mot-check/ford/fiesta/10-15-years/", follow_redirects=False)
        self.assertIn(r.status_code, [301, 307])


class TestModelPageDistinctiveness(unittest.TestCase):
    """Verify evidence scope and primary-source signals are present."""

    def test_model_page_has_evidence_scope(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn("Evidence scope", r.text)
        self.assertIn("not a pass prediction, diagnosis", r.text)

    def test_model_page_has_trust_signals(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn("DVSA anonymised MOT tests and results", r.text)
        self.assertIn("Open Government Licence v3", r.text)


from contextlib import contextmanager


@contextmanager
def seo_pages_conn():
    """A pooled SQLite connection with sqlite3.Row rows, as the SEO routes use it."""
    from main import get_sqlite_connection
    with get_sqlite_connection() as conn:
        old_factory = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.row_factory = old_factory


def _internal_hrefs(html: str) -> set[str]:
    """Site-relative hrefs on a rendered page, without query strings or fragments."""
    return {h.split("?")[0].split("#")[0] for h in re.findall(r'href="(/[^"]*)"', html)}


class TestAgeBandLinkConsistency(unittest.TestCase):
    """OA-006 B: age-band links are emitted only where the age-band route serves a page."""

    def _representative_models(self):
        eligible = sorted(seo_pages._age_band_eligible)[:3]
        ineligible = sorted(set(seo_pages._model_by_slug) - seo_pages._age_band_eligible)[:3]
        self.assertTrue(eligible, "fixture DB has no age-band-eligible model")
        self.assertTrue(ineligible, "fixture DB has no age-band-ineligible model")
        return eligible, ineligible

    def test_every_internal_link_on_representative_model_pages_resolves(self):
        eligible, ineligible = self._representative_models()
        checked = 0
        for make_slug, model_slug in eligible + ineligible:
            page = client.get(f"/mot-check/{make_slug}/{model_slug}/")
            self.assertEqual(page.status_code, 200)
            for href in sorted(_internal_hrefs(page.text)):
                r = client.get(href, follow_redirects=False)
                self.assertEqual(
                    r.status_code, 200,
                    f"{href} linked from /mot-check/{make_slug}/{model_slug}/ returned {r.status_code}",
                )
                checked += 1
        self.assertGreater(checked, 50)

    def test_ineligible_model_page_emits_no_age_band_links(self):
        _, ineligible = self._representative_models()
        for make_slug, model_slug in ineligible:
            self.assertFalse(seo_pages.age_band_pages_exist(make_slug, model_slug))
            page = client.get(f"/mot-check/{make_slug}/{model_slug}/")
            self.assertEqual(page.status_code, 200)
            self.assertFalse(
                [h for h in _internal_hrefs(page.text) if h.endswith("-years/")],
                f"ineligible model {make_slug}/{model_slug} still links age-band pages",
            )
            # The route agrees with the predicate.
            r = client.get(f"/mot-check/{make_slug}/{model_slug}/3-5-years/", follow_redirects=False)
            self.assertEqual(r.status_code, 404)

    def test_eligible_model_page_age_band_links_are_served(self):
        eligible, _ = self._representative_models()
        for make_slug, model_slug in eligible:
            self.assertTrue(seo_pages.age_band_pages_exist(make_slug, model_slug))
            page = client.get(f"/mot-check/{make_slug}/{model_slug}/")
            band_links = [h for h in _internal_hrefs(page.text) if h.endswith("-years/")]
            self.assertTrue(band_links, f"eligible model {make_slug}/{model_slug} emits no age-band links")
            for href in band_links:
                self.assertEqual(client.get(href, follow_redirects=False).status_code, 200, href)


class TestFooterLegalLinks(unittest.TestCase):
    """OA-006 C: footer links go straight to the canonical legal URLs, not via 301s."""

    def test_footer_links_point_at_canonical_legal_urls(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn('href="/terms"', r.text)
        self.assertIn('href="/privacy"', r.text)
        self.assertNotIn("/static/terms.html", r.text)
        self.assertNotIn("/static/privacy.html", r.text)

    def test_canonical_legal_urls_serve_directly(self):
        for path in ("/terms", "/privacy"):
            r = client.get(path, follow_redirects=False)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("text/html", r.headers["content-type"])

    def test_legacy_static_legal_urls_still_redirect(self):
        for old, new in (("/static/terms.html", "/terms"), ("/static/privacy.html", "/privacy")):
            r = client.get(old, follow_redirects=False)
            self.assertEqual(r.status_code, 301)
            self.assertEqual(r.headers["location"], new)


class TestComponentHubDiscoverability(unittest.TestCase):
    """OA-006 D: the seven /mot-check/problems/ hubs are reachable from /mot-check/."""

    def test_index_links_every_component_hub(self):
        index = client.get("/mot-check/")
        self.assertEqual(index.status_code, 200)
        hrefs = _internal_hrefs(index.text)
        hub_paths = {f"/mot-check/problems/{slug}/" for slug in seo_pages.COMPONENT_SLUGS}
        self.assertEqual(len(hub_paths), 7)
        self.assertTrue(hub_paths <= hrefs, f"missing hub links: {sorted(hub_paths - hrefs)}")
        for path in sorted(hub_paths):
            self.assertEqual(client.get(path, follow_redirects=False).status_code, 200, path)

    def test_hub_anchor_text_reuses_the_hub_h1(self):
        index = client.get("/mot-check/").text
        for slug, (_col, name) in seo_pages.COMPONENT_SLUGS.items():
            hub = client.get(f"/mot-check/problems/{slug}/").text
            h1 = re.search(r"<h1>(.*?)</h1>", hub, re.DOTALL).group(1).strip()
            self.assertIn(f">{h1}</a>", index, f"anchor for {slug} does not reuse hub H1 {h1!r}")


class TestComponentPageSignals(unittest.TestCase):
    """OA-006 E: component pages render no empty cost range and no contradictory canonical."""

    def _component_page(self):
        r = client.get("/mot-check/ford/fiesta/problems/brakes/")
        self.assertEqual(r.status_code, 200)
        return r.text

    def test_no_empty_repair_cost_range_is_rendered(self):
        html = self._component_page()
        self.assertNotIn("&pound;&ndash;&pound;", html)
        self.assertNotIn("\u00a3\u2013\u00a3", html)
        self.assertNotIn("from &pound; to &pound;", html)
        # The neutral FAQ answer already used elsewhere replaces the empty range.
        self.assertIn("Repair costs vary significantly depending on the fault, vehicle and garage.", html)
        # The typical figure and its caveat stay.
        self.assertIn("Not model-specific and not a quote", html)

    def test_noindex_pages_carry_a_self_referencing_canonical(self):
        html = self._component_page()
        self.assertIn('<meta name="robots" content="noindex, follow">', html)
        canonical = re.search(r'<link rel="canonical"\s*href="([^"]+)"', html).group(1)
        self.assertEqual(canonical, "https://www.autosafe.one/mot-check/ford/fiesta/problems/brakes/")


def _sitemap_entries() -> list[tuple[str, str]]:
    """(path, lastmod) for every URL in every sub-sitemap listed by /sitemap.xml."""
    index = client.get("/sitemap.xml").text
    entries = []
    for loc in re.findall(r"<loc>(.*?)</loc>", index):
        xml = client.get(loc.replace("https://www.autosafe.one", "")).text
        for url, lastmod in re.findall(r"<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", xml):
            entries.append((url.replace("https://www.autosafe.one", ""), lastmod))
    return entries


class TestSitemapInventoryInvariant(unittest.TestCase):
    """OA-006 invariant: the sitemap URL set is unchanged (442 URLs, no additions or removals)."""

    def test_sitemap_lists_exactly_the_known_url_inventory(self):
        paths = [p for p, _ in _sitemap_entries()]
        self.assertEqual(len(paths), 442)
        self.assertEqual(len(set(paths)), 442)
        families = {
            "content": [p for p in paths if not p.startswith("/mot-check/") or p == "/mot-check/"],
            "hubs": [p for p in paths if p.startswith("/mot-check/problems/")],
            "comparisons": [p for p in paths if p.startswith("/mot-check/compare/")],
            "makes": [p for p in paths if re.fullmatch(r"/mot-check/[^/]+/", p) and p != "/mot-check/"
                      and not p.startswith("/mot-check/problems/")],
            "models": [p for p in paths if re.fullmatch(r"/mot-check/[^/]+/[^/]+/", p)
                       and not p.startswith(("/mot-check/problems/", "/mot-check/compare/"))],
        }
        self.assertEqual({k: len(v) for k, v in families.items()},
                         {"content": 14, "hubs": 7, "comparisons": 20, "makes": 30, "models": 371})
        self.assertEqual(len(seo_pages._make_by_slug), 30)
        self.assertEqual(len(seo_pages._model_by_slug), 371)
        self.assertFalse([p for p in paths if p.endswith("-years/") or "/problems/" in p[len("/mot-check/problems/"):]])


class TestSitemapLastmodRule(unittest.TestCase):
    """OA-006 F: lastmod is derived from recorded source/dataset revisions, never from the clock."""

    def test_tracked_sources_match_the_revision_manifest(self):
        stale = page_revisions.stale_sources()
        self.assertEqual(
            stale, [],
            "page_revisions.json is stale; run `python scripts/update_page_revisions.py` "
            "so the sitemap lastmod reflects the change: " + "; ".join(f"{p} ({r})" for p, r in stale),
        )

    def test_manifest_dates_are_valid_and_not_in_the_future(self):
        for relpath, entry in page_revisions.load_manifest()["files"].items():
            revised = date.fromisoformat(entry["revised"])
            self.assertLessEqual(revised, date.today(), relpath)
            self.assertGreaterEqual(revised, date(2026, 1, 1), relpath)
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_every_lastmod_is_a_recorded_revision_date(self):
        allowed = {e["revised"] for e in page_revisions.load_manifest()["files"].values()} | {DATASET_ARTIFACT_REVISION}
        for path, lastmod in _sitemap_entries():
            self.assertIn(lastmod, allowed, f"{path} lastmod {lastmod} is not a recorded revision")
        index = client.get("/sitemap.xml").text
        for lastmod in re.findall(r"<lastmod>(.*?)</lastmod>", index):
            self.assertIn(lastmod, allowed)

    def test_family_lastmods_follow_the_rule(self):
        by_path = dict(_sitemap_entries())
        base = "templates/seo_base.html"

        def expect(*sources, dataset=True):
            return page_revisions.page_lastmod(
                *sources, dataset_revision=DATASET_ARTIFACT_REVISION if dataset else None
            )

        self.assertEqual(by_path["/mot-check/ford/fiesta/"], expect(base, "templates/seo_model.html"))
        self.assertEqual(by_path["/mot-check/ford/"], expect(base, "templates/seo_make.html"))
        self.assertEqual(by_path["/mot-check/compare/ford-fiesta-vs-vauxhall-corsa/"],
                         expect(base, "templates/seo_compare.html"))
        self.assertEqual(by_path["/mot-check/problems/brakes/"], expect(base, "templates/seo_component_hub.html"))
        self.assertEqual(by_path["/mot-check/"], expect(base, "templates/seo_index.html"))
        self.assertEqual(by_path["/will-my-car-pass-mot/"], expect(base, "templates/seo_pillar_k7.html"))
        self.assertEqual(by_path["/guides/mot-cost"], expect("static/guides/mot-cost.html", dataset=False))
        self.assertEqual(by_path["/privacy"], expect("static/privacy.html", dataset=False))
        self.assertEqual(by_path["/"], expect("index.html", dataset=False))

    def test_sitemap_index_lastmod_is_the_latest_entry_of_each_sub_sitemap(self):
        index = client.get("/sitemap.xml").text
        for loc, lastmod in re.findall(r"<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", index):
            xml = client.get(loc.replace("https://www.autosafe.one", "")).text
            self.assertEqual(lastmod, max(re.findall(r"<lastmod>(.*?)</lastmod>", xml)), loc)

    def test_page_lastmod_takes_the_latest_source(self):
        rev = page_revisions.page_lastmod("templates/seo_base.html", "templates/seo_model.html",
                                          dataset_revision="2000-01-01")
        self.assertEqual(rev, max(page_revisions.source_revision("templates/seo_base.html"),
                                  page_revisions.source_revision("templates/seo_model.html")))
        self.assertEqual(page_revisions.page_lastmod("index.html", dataset_revision="2999-12-31"), "2999-12-31")
        with self.assertRaises(KeyError):
            page_revisions.source_revision("templates/not-tracked.html")


class TestStartupModelTotals(unittest.TestCase):
    """OA-006 G: the one-pass startup aggregation reproduces the per-model SQL exactly."""

    COLUMNS = (
        "model_id TEXT, age_band TEXT, mileage_band TEXT, Total_Tests INTEGER, Total_Failures INTEGER, "
        "Risk_Brakes REAL, Risk_Suspension REAL, Risk_Tyres REAL, Risk_Steering REAL, Risk_Visibility REAL, "
        "Risk_Lamps_Reflectors_And_Electrical_Equipment REAL, Risk_Body_Chassis_Structure REAL"
    )

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(f"CREATE TABLE risks ({self.COLUMNS})")
        comps = (0.1, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01)
        rows = [
            ("FORD FIESTA", "3-5", "0-30k", 1000, 200) + comps,          # exact match
            ("FORD FIESTA ST", "6-10", "0-30k", 300, 90) + comps,        # prefix match
            ("ford fiesta zetec", "6-10", "0-30k", 200, 100) + comps,    # LIKE is case-insensitive
            ("FORD FIESTAVAN", "6-10", "0-30k", 500, 250) + comps,       # no space: must not match
            ("FORD FIESTA", "Unknown", "0-30k", 9999, 9999) + comps,     # excluded age band
            ("FORD FOCUS", "3-5", "0-30k", 150, 30) + comps,
            ("MERCEDES-BENZ C", "3-5", "0-30k", 400, 100) + comps,       # alt form for C-CLASS
            ("MERCEDES-BENZ C-CLASS", "3-5", "0-30k", 100, 50) + comps,
            ("MERCEDES-BENZ C 220", "3-5", "0-30k", 50, 10) + comps,
            ("MERCEDES-BENZ CLA", "3-5", "0-30k", 800, 1) + comps,       # must not match C-CLASS
            ("AUDI A3", "3-5", "0-30k", 40, 10) + comps,                 # below the 100-test floor
        ]
        conn.executemany("INSERT INTO risks VALUES (" + ",".join("?" * 12) + ")", rows)
        return conn

    def test_grouped_totals_equal_per_model_sql(self):
        conn = self._conn()
        known = {"FORD": ["FIESTA", "FOCUS", "KA"], "MERCEDES-BENZ": ["C-CLASS", "CLA"], "AUDI": ["A3"]}
        totals = _model_totals_from_groups(conn, known)
        self.assertEqual(totals[("FORD", "FIESTA")], (1500, 390))
        self.assertEqual(totals[("MERCEDES-BENZ", "C-CLASS")], (550, 160))
        self.assertEqual(totals[("MERCEDES-BENZ", "CLA")], (800, 1))
        self.assertEqual(totals[("FORD", "KA")], (0, 0))
        for (make, model), (t, f) in totals.items():
            overall = _query_model_overall(conn, make, model)
            if t >= 100:
                self.assertEqual((overall["total_tests"], overall["total_failures"]), (t, f), (make, model))
                rounded = conn.execute("SELECT ROUND(CAST(? AS REAL) / ?, 4)", (f, t)).fetchone()[0]
                self.assertEqual(overall["fail_rate"], rounded, (make, model))
            else:
                self.assertIsNone(overall, (make, model))
        conn.close()

    def test_startup_totals_match_live_model_pages(self):
        # Spot-check against the real fixture DB through the page-level query.
        for make_slug, model_slug in list(sorted(seo_pages._model_totals))[:5] + [("ford", "fiesta")]:
            info = seo_pages._model_by_slug[(make_slug, model_slug)]
            with seo_pages_conn() as conn:
                overall = _query_model_overall(conn, info["make"], info["model_id"])
            expected = {k: overall[k] for k in ("total_tests", "total_failures", "fail_rate")}
            self.assertEqual(seo_pages._model_totals[(make_slug, model_slug)], expected, (make_slug, model_slug))

    def test_pillar_ranks_models_from_startup_totals(self):
        r = client.get("/will-my-car-pass-mot/")
        self.assertEqual(r.status_code, 200)
        top = sorted(seo_pages._model_totals.items(), key=lambda kv: kv[1]["total_tests"], reverse=True)[:10]
        for (make_slug, model_slug), _ in top:
            self.assertIn(f"/mot-check/{make_slug}/{model_slug}/", r.text)


class TestPublicHttpBehaviour(unittest.TestCase):
    """OA-006 H: HEAD support, immutable caching for hashed assets, en-GB language tag."""

    PUBLIC_GET_PATHS = ("/", "/mot-check/", "/mot-check/ford/fiesta/", "/will-my-car-pass-mot/",
                        "/guides/mot-checklist", "/privacy", "/sitemap.xml", "/robots.txt",
                        "/mot-check/problems/brakes/")

    def test_head_matches_get_headers_with_an_empty_body(self):
        for path in self.PUBLIC_GET_PATHS:
            get = client.get(path, follow_redirects=False)
            head = client.head(path, follow_redirects=False)
            self.assertEqual(head.status_code, get.status_code, path)
            self.assertEqual(head.content, b"", path)
            self.assertEqual(head.headers.get("content-type"), get.headers.get("content-type"), path)
            self.assertEqual(head.headers.get("content-length"), get.headers.get("content-length"), path)

    def test_head_on_unknown_and_post_only_routes_is_not_widened(self):
        # HEAD answers exactly as GET would; POST-only endpoints acquire no
        # successful HEAD (GET on them already falls through to the 404 catch-all).
        self.assertEqual(client.head("/mot-check/no-such-make/", follow_redirects=False).status_code, 404)
        for path in ("/api/v2/reports", "/api/submit-lead"):
            get = client.get(path, follow_redirects=False)
            head = client.head(path, follow_redirects=False)
            self.assertEqual(head.status_code, get.status_code, path)
            self.assertNotEqual(head.status_code, 200, path)
            self.assertEqual(head.content, b"", path)

    def test_head_follows_the_same_redirects_as_get(self):
        r = client.head("/static/terms.html", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/terms")

    def test_hashed_vite_assets_are_immutable_for_a_year(self):
        from public_http import IMMUTABLE_CACHE_CONTROL, is_hashed_asset_name
        index_html = client.get("/").text
        asset_paths = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index_html)
        self.assertTrue(asset_paths, "built SPA shell references no /assets/ files")
        for path in asset_paths:
            r = client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers.get("cache-control"), IMMUTABLE_CACHE_CONTROL, path)
        # Only content-hashed names qualify.
        self.assertTrue(is_hashed_asset_name("index-DskXiKVN.js"))
        self.assertTrue(is_hashed_asset_name("ReportDashboard-4xKHfdOZ.js"))
        self.assertFalse(is_hashed_asset_name("umami.js"))
        self.assertFalse(is_hashed_asset_name("logo_clean.png"))
        self.assertFalse(is_hashed_asset_name("apple-touch-icon.png"))
        self.assertIsNone(client.get("/static/logo_clean.png").headers.get("cache-control"))
        self.assertIsNone(client.get("/static/umami.js").headers.get("cache-control"))

    def test_public_html_declares_british_english(self):
        for path in ("/", "/mot-check/", "/mot-check/ford/fiesta/", "/mot-check/ford/fiesta/problems/brakes/",
                     "/will-my-car-pass-mot/", "/guides/mot-checklist", "/privacy", "/terms",
                     "/mot-check/compare/ford-fiesta-vs-vauxhall-corsa/", "/mot-check/no-such-make/"):
            r = client.get(path)
            self.assertIn('<html lang="en-GB">', r.text, path)
            self.assertNotIn('<html lang="en">', r.text, path)


if __name__ == "__main__":
    unittest.main()
