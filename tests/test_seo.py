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
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app

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
        self.assertIn("dateModified", dataset, "Dataset should include dateModified")


class TestSeoMetaTags(unittest.TestCase):
    """Verify essential meta tags are present."""

    def test_model_page_has_meta_description(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn('<meta name="description"', r.text)
        # Description should mention failure rate
        desc_match = re.search(r'<meta name="description" content="([^"]+)"', r.text)
        self.assertIsNotNone(desc_match)
        self.assertIn("failure rate", desc_match.group(1).lower())

    def test_model_page_has_og_tags(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn('property="og:title"', r.text)
        self.assertIn('property="og:description"', r.text)

    def test_model_page_title_contains_make_model(self):
        r = client.get("/mot-check/ford/fiesta/")
        title_match = re.search(r'<title>(.*?)</title>', r.text)
        self.assertIsNotNone(title_match)
        title = title_match.group(1).lower()
        self.assertIn("ford", title)
        self.assertIn("fiesta", title)


class TestSeoFreshnessSignals(unittest.TestCase):
    """Verify freshness indicators are present."""

    def test_footer_contains_data_updated(self):
        r = client.get("/mot-check/ford/fiesta/")
        self.assertIn("Data last updated:", r.text)

    def test_data_updated_is_valid_date(self):
        r = client.get("/mot-check/ford/fiesta/")
        match = re.search(r"Data last updated: (\d{4}-\d{2}-\d{2})", r.text)
        self.assertIsNotNone(match, "Data updated date should be in YYYY-MM-DD format")


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
        r = client.get("/sitemap.xml")
        self.assertIn("/mot-check/ford/fiesta/", r.text)

    def test_sitemap_contains_make_urls(self):
        r = client.get("/sitemap.xml")
        self.assertIn("/mot-check/ford/", r.text)


if __name__ == "__main__":
    unittest.main()
