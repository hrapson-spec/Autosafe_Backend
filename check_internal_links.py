"""
Check Internal Links
====================

Validates that all internal links in SEO templates resolve to known routes.
Run in CI to prevent broken links after template or route changes.

Usage:
    python check_internal_links.py
"""

import re
import sqlite3
import sys
from pathlib import Path

# --- Reproduce slug/route logic from seo_pages.py ---

TEMPLATE_DIR = Path(__file__).parent / "templates"
DB_PATH = Path(__file__).parent / "autosafe.db"

AGE_BAND_SLUGS = {"0-3-years", "3-5-years", "6-10-years", "10-15-years", "15-plus-years"}

COMPARISON_PAIRS = [
    (("FORD", "FIESTA"), ("VAUXHALL", "CORSA")),
    (("FORD", "FOCUS"), ("VOLKSWAGEN", "GOLF")),
    (("VOLKSWAGEN", "POLO"), ("FORD", "FIESTA")),
    (("BMW", "3 SERIES"), ("AUDI", "A4")),
    (("BMW", "3 SERIES"), ("MERCEDES-BENZ", "C-CLASS")),
    (("AUDI", "A3"), ("VOLKSWAGEN", "GOLF")),
    (("FORD", "FOCUS"), ("VAUXHALL", "ASTRA")),
    (("NISSAN", "QASHQAI"), ("KIA", "SPORTAGE")),
    (("NISSAN", "QASHQAI"), ("HYUNDAI", "TUCSON")),
    (("TOYOTA", "YARIS"), ("HONDA", "JAZZ")),
    (("HONDA", "CIVIC"), ("TOYOTA", "COROLLA")),
    (("FORD", "FIESTA"), ("VOLKSWAGEN", "POLO")),
    (("VAUXHALL", "CORSA"), ("PEUGEOT", "208")),
    (("VAUXHALL", "ASTRA"), ("PEUGEOT", "308")),
    (("KIA", "SPORTAGE"), ("HYUNDAI", "TUCSON")),
    (("RENAULT", "CLIO"), ("PEUGEOT", "208")),
    (("BMW", "1 SERIES"), ("AUDI", "A3")),
    (("FORD", "KUGA"), ("NISSAN", "QASHQAI")),
    (("MERCEDES-BENZ", "A-CLASS"), ("BMW", "1 SERIES")),
    (("VOLKSWAGEN", "GOLF"), ("SEAT", "LEON")),
]


def _slugify(text: str) -> str:
    return text.lower().replace(" ", "-")


def build_known_routes(db_path: Path) -> set[str]:
    """Build set of all valid internal URL paths from the database."""
    routes = set()

    # Static pages
    routes.update([
        "/", "/will-my-car-pass-mot/", "/privacy", "/terms",
        "/mot-check/",
        "/guides/mot-checklist", "/guides/common-mot-failures",
        "/guides/when-is-mot-due", "/guides/mot-failure-rates-by-car",
        "/guides/mot-rules-2026", "/guides/mot-defect-categories",
        "/guides/mot-cost", "/guides/mot-history-check",
        "/guides/first-mot-guide",
        "/insights/", "/insights/unreliable-3-year-old-cars-2026/",
        "/insights/march-mot-rush-2026/",
    ])

    if not db_path.exists():
        print(f"WARNING: Database not found at {db_path}, skipping dynamic routes")
        return routes

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Get all makes and models from the database
    makes = set()
    models = set()

    try:
        rows = conn.execute(
            "SELECT DISTINCT model_id FROM mot_risk WHERE total_tests >= 100"
        ).fetchall()
    except sqlite3.OperationalError:
        # Try alternate table name
        rows = conn.execute(
            "SELECT DISTINCT model_id FROM risks WHERE total_tests >= 100"
        ).fetchall()

    for row in rows:
        model_id = row["model_id"]
        parts = model_id.split(" ", 1)
        if len(parts) != 2:
            continue
        make_raw, model_raw = parts
        make_slug = _slugify(make_raw)
        model_slug = _slugify(model_raw)
        makes.add(make_slug)
        models.add((make_slug, model_slug))

    conn.close()

    # Make pages
    for make_slug in makes:
        routes.add(f"/mot-check/{make_slug}/")

    # Model pages
    for make_slug, model_slug in models:
        routes.add(f"/mot-check/{make_slug}/{model_slug}/")

    # Age-band pages (for all models — checker is permissive here)
    for make_slug, model_slug in models:
        for age_slug in AGE_BAND_SLUGS:
            routes.add(f"/mot-check/{make_slug}/{model_slug}/{age_slug}/")

    # Comparison pages
    for (make1, model1), (make2, model2) in COMPARISON_PAIRS:
        s1 = f"{_slugify(make1)}-{_slugify(model1)}"
        s2 = f"{_slugify(make2)}-{_slugify(model2)}"
        routes.add(f"/mot-check/compare/{s1}-vs-{s2}/")

    return routes


def extract_links_from_templates(template_dir: Path) -> list[tuple[str, int, str]]:
    """Extract all internal href links from HTML templates.

    Returns list of (file_path, line_number, href).
    """
    links = []
    # Match href="..." capturing the path, ignoring Jinja expressions for now
    href_pattern = re.compile(r'href="(/[^"{}]*)"')

    for html_file in sorted(template_dir.glob("seo_*.html")):
        for line_num, line in enumerate(html_file.read_text().splitlines(), 1):
            for match in href_pattern.finditer(line):
                href = match.group(1)
                # Skip anchors-only and external
                if href.startswith("/#"):
                    continue
                links.append((html_file.name, line_num, href))

    return links


def check_links(known_routes: set[str], links: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """Check each extracted link against known routes.

    Returns list of broken (file, line, href).
    """
    broken = []
    for file_name, line_num, href in links:
        # Strip query strings and fragments
        clean = href.split("?")[0].split("#")[0]

        if clean in known_routes:
            continue

        # Check if it's a pattern route (contains Jinja template vars we can't resolve)
        # These are handled by the template at render time — skip them
        if "{{" in href or "{%" in href:
            continue

        broken.append((file_name, line_num, clean))

    return broken


def main():
    print("Checking internal links in SEO templates...\n")

    known_routes = build_known_routes(DB_PATH)
    print(f"Known routes: {len(known_routes)}")

    links = extract_links_from_templates(TEMPLATE_DIR)
    print(f"Links found in templates: {len(links)}")

    # Filter out Jinja template links (they contain {{ }})
    static_links = [(f, l, h) for f, l, h in links if "{{" not in h and "{%" not in h]
    template_links = len(links) - len(static_links)
    print(f"Static links to check: {len(static_links)} ({template_links} dynamic/Jinja links skipped)\n")

    broken = check_links(known_routes, links)

    if broken:
        print(f"BROKEN LINKS FOUND: {len(broken)}\n")
        for file_name, line_num, href in broken:
            print(f"  {file_name}:{line_num}  →  {href}")
        print()
        sys.exit(1)
    else:
        print("All static internal links are valid.")
        sys.exit(0)


if __name__ == "__main__":
    main()
