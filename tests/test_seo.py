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
from seo_pages import (
    _align_component_rates,
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

    def test_dataset_driven_sitemaps_use_the_artifact_revision(self):
        for path in ("/sitemap-makes.xml", "/sitemap-models.xml", "/sitemap-comparisons.xml"):
            r = client.get(path)
            self.assertIn(f"<lastmod>{DATASET_ARTIFACT_REVISION}</lastmod>", r.text)


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


if __name__ == "__main__":
    unittest.main()
