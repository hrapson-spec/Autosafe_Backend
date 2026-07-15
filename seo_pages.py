from typing import Optional

"""
SEO Landing Pages for AutoSafe
===============================

Generates ~400 data-driven landing pages for long-tail keywords like
"ford fiesta MOT failure rate" and "BMW 3 series MOT problems".

Three tiers:
  /mot-check/                    - Index listing all makes
  /mot-check/{make}/             - Make page listing models with failure rates
  /mot-check/{make}/{model}/     - Model page with full stats, component breakdown, FAQs

Plus /insights/ data story pages and a dynamic /sitemap.xml.
"""

import logging
import sqlite3
import re
from datetime import date
from repair_costs import REPAIR_COSTS, normalise_component_name
from pathlib import Path

from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from report_contract import (
    DATASET_ARTIFACT_REVISION,
    DATASET_TOTAL_FAILURES,
    DATASET_TOTAL_TESTS,
    POPULATION_DEFAULT_FAILURE_RISK,
)

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert e.g. '3 SERIES' -> '3-series', 'LAND ROVER' -> 'land-rover', 'Lamps & Electrics' -> 'lamps-and-electrics'."""
    text = text.lower()
    text = text.replace("&", "and")
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text).strip('-')
    return text


def _display_name(text: str) -> str:
    """Convert e.g. 'FORD' -> 'Ford', 'LAND ROVER' -> 'Land Rover', 'BMW' -> 'BMW'."""
    # Keep all-uppercase short names (BMW, MG, etc.)
    if len(text) <= 3 and text.isalpha():
        return text
    # Title-case everything else
    return text.title()

# --- Jinja2 setup ---
TEMPLATE_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
jinja_env.globals.update(
    dataset_total_failures=DATASET_TOTAL_FAILURES,
    dataset_total_tests=DATASET_TOTAL_TESTS,
    dataset_artifact_revision=DATASET_ARTIFACT_REVISION,
    dataset_reference_rate=POPULATION_DEFAULT_FAILURE_RISK,
)

# --- Dedicated SEO cache (separate from API cache in main.py) ---
_seo_cache: TTLCache = TTLCache(maxsize=2000, ttl=3600)
_sitemap_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)

# --- Slug lookup dicts (populated at startup) ---
# slug -> {"make": "FORD", "display": "Ford"}
_make_by_slug: dict = {}
# (make_slug, model_slug) -> {"model_id": "FIESTA", "display": "Fiesta", "make": "FORD"}
_model_by_slug: dict = {}
# make_slug -> [model_slug, ...]
_models_for_make: dict = {}
# (make_slug, model_slug) -> fail_rate (for data-driven related models)
_model_fail_rates: dict = {}
# Models with enough tests for age-band pages (staged rollout)
_age_band_eligible: set = set()  # set of (make_slug, model_slug)
# Age band slug mappings
AGE_BAND_SLUGS = {
    "0-2-years": "0-2",
    "3-5-years": "3-5",
    "6-10-years": "6-10",
    "11-15-years": "11-15",
    "15-plus-years": "15+",
}
AGE_BAND_DISPLAY = {
    "0-2": "0-2",
    "3-5": "3-5",
    "6-10": "6-10",
    "11-15": "11-15",
    "15+": "15+",
}

# Competitor model mapping (same-segment rivals for internal linking)
COMPETITOR_MODELS = {
    "FIESTA": [("VAUXHALL", "CORSA"), ("VOLKSWAGEN", "POLO"), ("RENAULT", "CLIO"), ("PEUGEOT", "208")],
    "FOCUS": [("VAUXHALL", "ASTRA"), ("VOLKSWAGEN", "GOLF"), ("PEUGEOT", "308"), ("KIA", "CEED")],
    "CORSA": [("FORD", "FIESTA"), ("VOLKSWAGEN", "POLO"), ("RENAULT", "CLIO"), ("PEUGEOT", "208")],
    "ASTRA": [("FORD", "FOCUS"), ("VOLKSWAGEN", "GOLF"), ("PEUGEOT", "308"), ("KIA", "CEED")],
    "GOLF": [("FORD", "FOCUS"), ("VAUXHALL", "ASTRA"), ("PEUGEOT", "308"), ("SEAT", "LEON")],
    "POLO": [("FORD", "FIESTA"), ("VAUXHALL", "CORSA"), ("RENAULT", "CLIO"), ("SEAT", "IBIZA")],
    "3 SERIES": [("AUDI", "A4"), ("MERCEDES-BENZ", "C-CLASS"), ("JAGUAR", "XE")],
    "A3": [("VOLKSWAGEN", "GOLF"), ("BMW", "1 SERIES"), ("MERCEDES-BENZ", "A-CLASS")],
    "A4": [("BMW", "3 SERIES"), ("MERCEDES-BENZ", "C-CLASS"), ("JAGUAR", "XE")],
    "CLIO": [("FORD", "FIESTA"), ("VAUXHALL", "CORSA"), ("VOLKSWAGEN", "POLO"), ("PEUGEOT", "208")],
    "QASHQAI": [("KIA", "SPORTAGE"), ("HYUNDAI", "TUCSON"), ("FORD", "KUGA"), ("TOYOTA", "RAV4")],
    "YARIS": [("HONDA", "JAZZ"), ("FORD", "FIESTA"), ("VOLKSWAGEN", "POLO"), ("SUZUKI", "SWIFT")],
    "CIVIC": [("FORD", "FOCUS"), ("VOLKSWAGEN", "GOLF"), ("TOYOTA", "COROLLA"), ("MAZDA", "3")],
    "208": [("FORD", "FIESTA"), ("VAUXHALL", "CORSA"), ("VOLKSWAGEN", "POLO"), ("RENAULT", "CLIO")],
    "308": [("FORD", "FOCUS"), ("VOLKSWAGEN", "GOLF"), ("VAUXHALL", "ASTRA"), ("KIA", "CEED")],
    "1 SERIES": [("AUDI", "A3"), ("VOLKSWAGEN", "GOLF"), ("MERCEDES-BENZ", "A-CLASS")],
    "SPORTAGE": [("NISSAN", "QASHQAI"), ("HYUNDAI", "TUCSON"), ("FORD", "KUGA")],
    "TUCSON": [("NISSAN", "QASHQAI"), ("KIA", "SPORTAGE"), ("FORD", "KUGA")],
}

# Top 20 comparison pairs for SEO (derived from COMPETITOR_MODELS)
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

# Component columns in the risks table (in display order)
COMPONENTS = [
    ("Risk_Brakes", "Brakes"),
    ("Risk_Suspension", "Suspension"),
    ("Risk_Tyres", "Tyres"),
    ("Risk_Steering", "Steering"),
    ("Risk_Visibility", "Visibility"),
    ("Risk_Lamps_Reflectors_And_Electrical_Equipment", "Lamps & Electrics"),
    ("Risk_Body_Chassis_Structure", "Body & Chassis"),
]


RETIRED_LOCAL_CITY_SLUGS = frozenset({
    "london", "manchester", "birmingham", "leeds", "glasgow", "liverpool",
    "bristol", "newcastle", "sheffield", "edinburgh", "cardiff", "belfast",
})

# Mapping for component slugs to (db_column, display_name)
COMPONENT_SLUGS = {
    _slugify(name): (col, name) for col, name in COMPONENTS
}

DATASET_REFERENCE_FAIL_RATE = POPULATION_DEFAULT_FAILURE_RISK
# Source-controlled page-copy revision for non-dataset sitemap entries. Unlike
# ``date.today()``, this does not claim every URL changed whenever a worker
# restarts. Update deliberately when those public pages materially change.
SITE_CONTENT_REVISION = "2026-07-11"





def _model_where_clause(make: str, model: str):
    """
    Build SQL WHERE clause and params for matching a model in the risks table.
    Handles variants like C-CLASS matching both 'MERCEDES-BENZ C-CLASS' and 'MERCEDES-BENZ C'.
    """
    model_id = f"{make} {model}"
    conditions = ["model_id = ?", "model_id LIKE ? || ' %'"]
    params = [model_id, model_id]

    # For X-CLASS style models, also match the single-letter form (e.g. C, E, S)
    if model.endswith("-CLASS"):
        alt = model.replace("-CLASS", "")
        alt_id = f"{make} {alt}"
        conditions.append("model_id = ?")
        conditions.append("model_id LIKE ? || ' %'")
        params.extend([alt_id, alt_id])

    return f"({' OR '.join(conditions)})", params


def initialize_seo_data(get_sqlite_connection):
    """
    Build slug lookup dicts at startup from KNOWN_MODELS,
    filtered to models with >= 100 tests in SQLite.
    """
    from consolidate_models import get_canonical_models_for_make

    # Get all makes from KNOWN_MODELS
    # Re-import the dict directly
    known = {}
    for make in [
        "FORD", "VAUXHALL", "VOLKSWAGEN", "BMW", "AUDI", "MERCEDES-BENZ",
        "TOYOTA", "HONDA", "NISSAN", "PEUGEOT", "RENAULT", "KIA", "HYUNDAI",
        "FIAT", "SEAT", "SKODA", "MINI", "MAZDA", "CITROEN", "SUZUKI",
        "VOLVO", "JAGUAR", "LAND ROVER", "PORSCHE", "LEXUS", "MITSUBISHI",
        "SUBARU", "JEEP", "DACIA", "MG",
    ]:
        models = get_canonical_models_for_make(make)
        if models:
            known[make] = models

    # Query SQLite to filter to models with >= 100 total tests
    valid_models = set()
    with get_sqlite_connection() as conn:
        if conn is None:
            logger.error("SEO: Cannot initialize - no SQLite connection")
            return

        for make, models in known.items():
            for model in models:
                try:
                    where, params = _model_where_clause(make, model)
                    row = conn.execute(
                        f"SELECT SUM(Total_Tests) as total FROM risks WHERE {where} AND age_band != 'Unknown'",
                        params,
                    ).fetchone()
                    if row and row[0] and row[0] >= 100:
                        valid_models.add((make, model))
                except sqlite3.Error as e:
                    logger.warning(
                        "SEO model check failed: make=%s model=%s type=%s",
                        make,
                        model,
                        type(e).__name__,
                    )

    # Identify top models eligible for age-band pages (>= 10,000 total tests)
    age_band_candidates = set()
    with get_sqlite_connection() as conn:
        if conn:
            for make, model in valid_models:
                try:
                    where, params = _model_where_clause(make, model)
                    row = conn.execute(
                        f"SELECT SUM(Total_Tests) as total FROM risks WHERE {where} AND age_band != 'Unknown'",
                        params,
                    ).fetchone()
                    if row and row[0] and row[0] >= 10000:
                        age_band_candidates.add((make, model))
                except sqlite3.Error:
                    pass

    # Build lookup dicts
    _make_by_slug.clear()
    _model_by_slug.clear()
    _models_for_make.clear()
    _age_band_eligible.clear()

    makes_with_models = set()
    for make, model in valid_models:
        make_slug = _slugify(make)
        model_slug = _slugify(model)
        makes_with_models.add(make)

        _model_by_slug[(make_slug, model_slug)] = {
            "model_id": model,
            "display": _display_name(model),
            "make": make,
        }
        _models_for_make.setdefault(make_slug, []).append(model_slug)

        if (make, model) in age_band_candidates:
            _age_band_eligible.add((make_slug, model_slug))

    for make in makes_with_models:
        slug = _slugify(make)
        _make_by_slug[slug] = {"make": make, "display": _display_name(make)}

    # Sort model lists alphabetically by display name
    for make_slug in _models_for_make:
        _models_for_make[make_slug].sort(
            key=lambda ms: _model_by_slug[(make_slug, ms)]["display"]
        )

    # Compute failure rates for all models (for data-driven related model linking)
    _model_fail_rates.clear()
    with get_sqlite_connection() as conn:
        if conn:
            for make, model in valid_models:
                try:
                    where, params = _model_where_clause(make, model)
                    row = conn.execute(
                        f"""SELECT CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests) as fail_rate
                            FROM risks WHERE {where} AND age_band != 'Unknown'
                            HAVING SUM(Total_Tests) >= 100""",
                        params,
                    ).fetchone()
                    if row and row[0] is not None:
                        make_slug = _slugify(make)
                        model_slug = _slugify(model)
                        _model_fail_rates[(make_slug, model_slug)] = float(row[0])
                except sqlite3.Error:
                    pass

    total_pages = len(_make_by_slug) + len(_model_by_slug)
    logger.info(
        f"SEO: Initialized {len(_make_by_slug)} makes, "
        f"{len(_model_by_slug)} models ({total_pages} landing pages), "
        f"{len(_age_band_eligible)} models eligible for age-band pages, "
        f"{len(_model_fail_rates)} models with fail rates for linking"
    )


def _get_similar_models(make_slug: str, model_slug: str, max_results: int = 4) -> list[dict]:
    """
    Find models with similar failure rates from OTHER makes.
    This provides data-driven cross-brand internal links for every model page,
    supplementing the hardcoded COMPETITOR_MODELS mappings.
    """
    current_rate = _model_fail_rates.get((make_slug, model_slug))
    if current_rate is None:
        return []

    # Find models from other makes, sorted by similarity in failure rate
    candidates = []
    for (ms, mds), rate in _model_fail_rates.items():
        if ms == make_slug:  # Skip same-make models (already shown as siblings)
            continue
        diff = abs(rate - current_rate)
        candidates.append((diff, ms, mds, rate))

    candidates.sort(key=lambda x: x[0])

    results = []
    seen_makes = set()
    for diff, ms, mds, rate in candidates:
        if len(results) >= max_results:
            break
        # Diversify: max one model per make
        if ms in seen_makes:
            continue
        seen_makes.add(ms)

        model_info = _model_by_slug.get((ms, mds))
        make_info = _make_by_slug.get(ms)
        if model_info and make_info:
            results.append({
                "make_slug": ms,
                "model_slug": mds,
                "make_display": make_info["display"],
                "model_display": model_info["display"],
                "fail_rate": rate,
            })

    return results


def _query_model_age_bands(conn, make: str, model: str) -> list[dict]:
    """Query age-band breakdown for a model (weighted average across mileage bands)."""
    where, params = _model_where_clause(make, model)
    comp_cols = ", ".join(
        f"CASE WHEN COUNT({col}) = COUNT(*) "
        f"THEN ROUND(SUM({col} * Total_Tests) / NULLIF(SUM(Total_Tests), 0), 4) END as {col}"
        for col, _ in COMPONENTS
    )
    rows = conn.execute(
        f"""SELECT age_band,
                   SUM(Total_Tests) as total_tests,
                   SUM(Total_Failures) as total_failures,
                   ROUND(CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests), 4) as fail_rate,
                   {comp_cols}
            FROM risks
            WHERE {where}
              AND age_band != 'Unknown'
            GROUP BY age_band
            HAVING SUM(Total_Tests) >= 100
            ORDER BY CASE age_band
                WHEN '0-2' THEN 1
                WHEN '3-5' THEN 2
                WHEN '6-10' THEN 3
                WHEN '11-15' THEN 4
                WHEN '15+' THEN 5
                ELSE 6
            END""",
        params,
    ).fetchall()

    result = []
    for row in rows:
        if row["fail_rate"] is None or row["total_failures"] is None:
            continue
        # Find worst component for this age band
        comp_risks = {}
        for col, name in COMPONENTS:
            val = row[col]
            if val is not None:
                comp_risks[name] = float(val)

        worst = max(comp_risks, key=comp_risks.get) if comp_risks else "N/A"

        result.append({
            "age_band": row["age_band"],
            "total_tests": int(row["total_tests"]),
            "total_failures": int(row["total_failures"]),
            "fail_rate": float(row["fail_rate"]),
            "worst_component": worst,
            "components": comp_risks,
        })
    return result


def _query_model_overall(conn, make: str, model: str) -> Optional[dict]:
    """Query overall failure rate for a model."""
    where, params = _model_where_clause(make, model)
    comp_cols = ", ".join(
        f"CASE WHEN COUNT({col}) = COUNT(*) "
        f"THEN ROUND(SUM({col} * Total_Tests) / NULLIF(SUM(Total_Tests), 0), 4) END as {col}"
        for col, _ in COMPONENTS
    )
    row = conn.execute(
        f"""SELECT SUM(Total_Tests) as total_tests,
                   SUM(Total_Failures) as total_failures,
                   ROUND(CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests), 4) as fail_rate,
                   {comp_cols}
            FROM risks
            WHERE {where}
              AND age_band != 'Unknown'
            HAVING SUM(Total_Tests) >= 100""",
        params,
    ).fetchone()

    if not row or not row["total_tests"] or row["fail_rate"] is None or row["total_failures"] is None:
        return None

    components = []
    for col, name in COMPONENTS:
        val = row[col]
        if val is not None:
            components.append({"name": name, "risk": float(val), "col": col})

    return {
        "total_tests": int(row["total_tests"]),
        "total_failures": int(row["total_failures"]),
        "fail_rate": float(row["fail_rate"]),
        "components": sorted(components, key=lambda c: c["risk"], reverse=True),
    }


def _align_component_rates(left: list[dict], right: list[dict]) -> list[dict]:
    """Return only component categories supported for both model groups."""
    right_by_name = {item["name"]: item["risk"] for item in right}
    return [
        {"name": item["name"], "risk1": item["risk"], "risk2": right_by_name[item["name"]]}
        for item in left
        if item["name"] in right_by_name
    ]


def _query_make_models(conn, make: str, model_ids: list[str]) -> list[dict]:
    """Query failure rates for all models of a make."""
    results = []
    for model in model_ids:
        overall = _query_model_overall(conn, make, model)
        if overall:
            results.append({
                "model": model,
                "display_name": _display_name(model),
                "slug": _slugify(model),
                "fail_rate": overall["fail_rate"],
                "total_tests": overall["total_tests"],
                "total_failures": overall["total_failures"],
            })
    # Sort by failure rate descending
    results.sort(key=lambda m: m["fail_rate"], reverse=True)
    return results


def _summarise_models(models: list[dict]) -> dict:
    """Return a sample-size-weighted summary for the included model groups."""
    total_tests = sum(model["total_tests"] for model in models)
    total_failures = sum(model["total_failures"] for model in models)
    return {
        "total_tests": total_tests,
        "total_failures": total_failures,
        "fail_rate": total_failures / total_tests if total_tests else 0.0,
    }


def _html_response(content: str) -> HTMLResponse:
    """Return HTML response with SEO-friendly cache headers."""
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _not_found_html(message: str) -> HTMLResponse:
    """Return a 404 HTML page."""
    template = jinja_env.get_template("seo_base.html")
    html = template.render(content=f'<h1>Not Found</h1><p>{message}</p>')
    # For 404, render inline since we can't easily use block overrides
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Not Found | AutoSafe</title>
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .guide-content {{ max-width: 800px; margin: 0 auto; padding: 2rem; }}
        .guide-content h1 {{ font-family: 'Playfair Display', serif; font-size: 2.5rem; margin-bottom: 1rem; }}
        .guide-content p {{ line-height: 1.8; color: #a0a0a0; }}
        .guide-content a {{ color: #e5c07b; }}
    </style>
</head>
<body>
    <div class="app-container" style="max-width: 100%;">
        <header class="app-header" style="padding: 1rem 0;">
            <div class="logo">
                <a href="/"><img src="/static/logo_clean.png" alt="AutoSafe" class="logo-image"></a>
            </div>
        </header>
        <main class="guide-content">
            <h1>Page Not Found</h1>
            <p>{message}</p>
            <p><a href="/mot-check/">Browse all makes and models</a></p>
        </main>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=404)


def register_seo_routes(app: FastAPI, get_sqlite_connection):
    """Register all SEO landing page routes on the FastAPI app."""

    # --- Homepage (must be registered before the SPA catch-all) ---

    @app.get("/", response_class=HTMLResponse)
    def seo_homepage():
        # The product homepage is the React app again; keep the SEO routes below intact.
        return FileResponse("static/index.html")

    # --- Comparison pages (registered first so /mot-check/compare/ isn't caught by {make_slug}) ---

    @app.get("/mot-check/compare/{slug1}-vs-{slug2}/", response_class=HTMLResponse)
    def seo_compare(slug1: str, slug2: str):
        # Find matching pair
        pair_key = f"{slug1}-vs-{slug2}"
        cache_key = f"seo:compare:{pair_key}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        # Resolve slugs to models
        target_pair = None
        for (make1, model1), (make2, model2) in COMPARISON_PAIRS:
            s1 = f"{_slugify(make1)}-{_slugify(model1)}"
            s2 = f"{_slugify(make2)}-{_slugify(model2)}"
            if slug1 == s1 and slug2 == s2:
                target_pair = ((make1, model1), (make2, model2))
                break

        if not target_pair:
            return _not_found_html("Comparison not found.")

        (make1, model1), (make2, model2) = target_pair
        make1_slug, model1_slug = _slugify(make1), _slugify(model1)
        make2_slug, model2_slug = _slugify(make2), _slugify(model2)

        display1 = f"{_display_name(make1)} {_display_name(model1)}"
        display2 = f"{_display_name(make2)} {_display_name(model2)}"

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row

            overall1 = _query_model_overall(conn, make1, model1)
            overall2 = _query_model_overall(conn, make2, model2)
            age_bands1 = _query_model_age_bands(conn, make1, model1)
            age_bands2 = _query_model_age_bands(conn, make2, model2)

            conn.row_factory = old_factory

        if not overall1 or not overall2:
            return _not_found_html("Not enough data for this comparison.")

        component_comparisons = _align_component_rates(
            overall1["components"], overall2["components"]
        )

        # Determine verdict
        if overall1["fail_rate"] < overall2["fail_rate"]:
            winner = display1
            loser = display2
            diff = overall2["fail_rate"] - overall1["fail_rate"]
        elif overall2["fail_rate"] < overall1["fail_rate"]:
            winner = display2
            loser = display1
            diff = overall1["fail_rate"] - overall2["fail_rate"]
        else:
            winner = None
            loser = None
            diff = 0

        canonical_url = f"https://www.autosafe.one/mot-check/compare/{slug1}-vs-{slug2}/"

        template = jinja_env.get_template("seo_compare.html")
        html = template.render(
            display1=display1, display2=display2,
            make1_slug=make1_slug, model1_slug=model1_slug,
            make2_slug=make2_slug, model2_slug=model2_slug,
            overall1=overall1, overall2=overall2,
            age_bands1=age_bands1, age_bands2=age_bands2,
            winner=winner, loser=loser, diff=diff,
            component_comparisons=component_comparisons,
            canonical_url=canonical_url,
            slug1=slug1, slug2=slug2,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    @app.get("/mot-check/", response_class=HTMLResponse)
    def seo_index():
        cache_key = "seo:index"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        makes = sorted(
            [{"slug": slug, "display_name": info["display"]} for slug, info in _make_by_slug.items()],
            key=lambda m: m["display_name"],
        )

        template = jinja_env.get_template("seo_index.html")
        html = template.render(makes=makes)
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- Phase 3: Top-Level Component Aggregation Hubs ---
    # NOTE: Must be registered BEFORE /{make_slug}/ to prevent 'problems' matching as a make.

    @app.get("/mot-check/problems/{component_slug}/", response_class=HTMLResponse)
    def seo_component_hub(component_slug: str):
        if component_slug not in COMPONENT_SLUGS:
            return _not_found_html("Component category not found.")

        cache_key = f"seo:component_hub:{component_slug}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        db_col, display_name = COMPONENT_SLUGS[component_slug]

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row

            query = f"""
                SELECT model_id,
                       SUM(Total_Tests) as total_tests,
                       ROUND(SUM({db_col} * Total_Tests) / NULLIF(SUM(Total_Tests), 0), 4) as comp_risk
                FROM risks
                WHERE age_band != 'Unknown'
                GROUP BY model_id
                HAVING SUM(Total_Tests) >= 5000 AND COUNT({db_col}) = COUNT(*)
                ORDER BY comp_risk DESC
                LIMIT 50
            """
            try:
                rows = conn.execute(query).fetchall()
            except sqlite3.OperationalError:
                return _not_found_html("Data unavailable.")

            conn.row_factory = old_factory

        worst_models = []
        for rank, row in enumerate(rows, 1):
            model_id = row["model_id"]
            page_link = None
            for (ms, mds), info in _model_by_slug.items():
                if info["model_id"] == model_id:
                     page_link = f"/mot-check/{ms}/{mds}/problems/{component_slug}/"
                     break
                full_constructed = f"{info['make']} {info['model_id']}"
                if full_constructed == model_id:
                     page_link = f"/mot-check/{ms}/{mds}/problems/{component_slug}/"
                     break

            worst_models.append({
                "rank": rank,
                "model_id": model_id,
                "display_name": _display_name(model_id),
                "total_tests": int(row["total_tests"]),
                "comp_risk": float(row["comp_risk"]),
                "page_link": page_link,
            })

        template = jinja_env.get_template("seo_component_hub.html")
        html = template.render(
            component_slug=component_slug,
            component_name=display_name,
            worst_models=worst_models
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    @app.get("/mot-check/{make_slug}/", response_class=HTMLResponse)
    def seo_make(make_slug: str):
        if make_slug not in _make_by_slug:
            return _not_found_html(f"Make not found. We don't have data for this manufacturer.")

        cache_key = f"seo:make:{make_slug}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        make_info = _make_by_slug[make_slug]
        make = make_info["make"]
        model_slugs = _models_for_make.get(make_slug, [])
        model_ids = [_model_by_slug[(make_slug, ms)]["model_id"] for ms in model_slugs]

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            models = _query_make_models(conn, make, model_ids)
            make_summary = _summarise_models(models)
            conn.row_factory = old_factory

        other_makes = sorted(
            [{"slug": s, "display_name": info["display"]}
             for s, info in _make_by_slug.items() if s != make_slug],
            key=lambda m: m["display_name"],
        )

        template = jinja_env.get_template("seo_make.html")
        html = template.render(
            make_display=make_info["display"],
            make_slug=make_slug,
            models=models,
            make_summary=make_summary,
            other_makes=other_makes,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    @app.get("/mot-check/{make_slug}/{model_slug}/", response_class=HTMLResponse)
    def seo_model(make_slug: str, model_slug: str):
        if make_slug not in _make_by_slug:
            return _not_found_html("Make not found.")
        if (make_slug, model_slug) not in _model_by_slug:
            return _not_found_html(
                f"Model not found for {_make_by_slug[make_slug]['display']}."
            )

        cache_key = f"seo:model:{make_slug}:{model_slug}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        make_info = _make_by_slug[make_slug]
        model_info = _model_by_slug[(make_slug, model_slug)]
        make = make_info["make"]
        model = model_info["model_id"]

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            overall = _query_model_overall(conn, make, model)
            if not overall:
                conn.row_factory = old_factory
                return _not_found_html(
                    f"Not enough test data for {make_info['display']} {model_info['display']}."
                )
            age_bands = _query_model_age_bands(conn, make, model)
            conn.row_factory = old_factory

        # Sibling models (other models from same make, excluding current)
        sibling_models = [
            {"slug": ms, "display_name": _model_by_slug[(make_slug, ms)]["display"]}
            for ms in _models_for_make.get(make_slug, [])
            if ms != model_slug
        ]

        # Competitor models (same-segment rivals for cross-brand linking)
        competitors = []
        rival_list = COMPETITOR_MODELS.get(model, [])
        for rival_make, rival_model in rival_list:
            rival_make_slug = _slugify(rival_make)
            rival_model_slug = _slugify(rival_model)
            if (rival_make_slug, rival_model_slug) in _model_by_slug:
                rival_info = _model_by_slug[(rival_make_slug, rival_model_slug)]
                competitors.append({
                    "make_slug": rival_make_slug,
                    "model_slug": rival_model_slug,
                    "make_display": _make_by_slug.get(rival_make_slug, {}).get("display", rival_make),
                    "model_display": rival_info["display"],
                })

        # Comparison page links (find any COMPARISON_PAIRS involving this model)
        comparisons = []
        for (m1_make, m1_model), (m2_make, m2_model) in COMPARISON_PAIRS:
            if (make == m1_make and model == m1_model) or (make == m2_make and model == m2_model):
                s1 = f"{_slugify(m1_make)}-{_slugify(m1_model)}"
                s2 = f"{_slugify(m2_make)}-{_slugify(m2_model)}"
                d1 = f"{_display_name(m1_make)} {_display_name(m1_model)}"
                d2 = f"{_display_name(m2_make)} {_display_name(m2_model)}"
                comparisons.append({
                    "url": f"/mot-check/compare/{s1}-vs-{s2}/",
                    "title": f"{d1} vs {d2}",
                })

        # Data-driven similar models (supplements hardcoded competitors)
        similar_models = _get_similar_models(make_slug, model_slug)

        # Step 6: Compute Key Findings context for distinctiveness
        best_age = min(age_bands, key=lambda b: b["fail_rate"]) if age_bands else None
        worst_age = max(age_bands, key=lambda b: b["fail_rate"]) if age_bands else None

        template = jinja_env.get_template("seo_model.html")
        html = template.render(
            make_display=make_info["display"],
            make_slug=make_slug,
            model_display=model_info["display"],
            model_slug=model_slug,
            overall_fail_rate=overall["fail_rate"],
            overall_tests=overall["total_tests"],
            age_bands=age_bands,
            components=overall["components"],
            top_components=overall["components"][:3],
            sibling_models=sibling_models,
            competitors=competitors,
            comparisons=comparisons,
            similar_models=similar_models,
            dataset_reference_rate=DATASET_REFERENCE_FAIL_RATE,
            best_age_band=best_age,
            worst_age_band=worst_age,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- Age-band pages and legacy year URLs: /mot-check/{make}/{model}/{detail_slug}/ ---
    # The dataset contains age bands, not model-year cohorts. Year-looking URLs
    # therefore redirect to the stable model-group page instead of inventing
    # year-level precision or a permanent redirect that ages incorrectly.

    # Legacy age-band slugs that were renamed — 301 redirect to current slug
    LEGACY_AGE_SLUGS = {
        "0-3-years": "0-2-years",
        "10-15-years": "11-15-years",
    }

    @app.get("/mot-check/{make_slug}/{model_slug}/{detail_slug}/", response_class=HTMLResponse)
    def seo_model_detail(make_slug: str, model_slug: str, detail_slug: str):
        # --- Step 4: Redirect legacy age-band slugs ---
        if detail_slug in LEGACY_AGE_SLUGS:
            new_slug = LEGACY_AGE_SLUGS[detail_slug]
            return RedirectResponse(
                url=f"/mot-check/{make_slug}/{model_slug}/{new_slug}/",
                status_code=301,
            )

        if make_slug not in _make_by_slug:
            return _not_found_html("Make not found.")
        if (make_slug, model_slug) not in _model_by_slug:
            return _not_found_html(
                f"Model not found for {_make_by_slug[make_slug]['display']}."
            )
        if (make_slug, model_slug) not in _age_band_eligible:
            return _not_found_html("Detailed data not available for this model yet.")

        # --- Determine whether this is a year or age-band request ---
        age_band_raw = None
        age_slug = None

        # Try year first (e.g. "2012")
        try:
            year = int(detail_slug)
            current_year = date.today().year
            if year < 1980 or year > current_year + 1:
                return _not_found_html("Invalid year.")
            # Model-year pages previously relabelled a broad, moving age band
            # as year-specific evidence. Retire them to the stable model-group
            # page; a permanent redirect to a computed age band would become
            # wrong as the calendar advances.
            return RedirectResponse(
                url=f"/mot-check/{make_slug}/{model_slug}/",
                status_code=301,
            )
        except ValueError:
            # Not an int — try age-band slug (e.g. "3-5-years")
            age_band_raw = AGE_BAND_SLUGS.get(detail_slug)
            if not age_band_raw:
                return _not_found_html("Invalid age range or year.")
            age_slug = detail_slug

        # --- Common logic for both year and age-band pages ---
        cache_key = f"seo:detail:{make_slug}:{model_slug}:{detail_slug}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        make_info = _make_by_slug[make_slug]
        model_info = _model_by_slug[(make_slug, model_slug)]
        make = make_info["make"]
        model = model_info["model_id"]

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            all_age_bands = _query_model_age_bands(conn, make, model)
            conn.row_factory = old_factory

        # Find the specific age band data
        current_band = None
        for band in all_age_bands:
            if band["age_band"] == age_band_raw:
                current_band = band
                break

        if not current_band:
            label = AGE_BAND_DISPLAY.get(age_band_raw, age_band_raw)
            return _not_found_html(
                f"Not enough test data for {label} {make_info['display']} {model_info['display']}."
            )

        # Build component list sorted by risk
        components = sorted(
            [{"name": name, "risk": current_band["components"][name]}
             for _, name in COMPONENTS if name in current_band["components"]],
            key=lambda c: c["risk"],
            reverse=True,
        )

        # Get competitor models for navigation only; no equivalence is claimed.
        competitors = []
        rival_list = COMPETITOR_MODELS.get(model, [])
        for rival_make, rival_model in rival_list:
            rival_make_slug = _slugify(rival_make)
            rival_model_slug = _slugify(rival_model)
            if (rival_make_slug, rival_model_slug) in _model_by_slug:
                rival_info = _model_by_slug[(rival_make_slug, rival_model_slug)]
                competitors.append({
                    "make_slug": rival_make_slug,
                    "model_slug": rival_model_slug,
                    "make_display": _make_by_slug.get(rival_make_slug, {}).get("display", rival_make),
                    "model_display": rival_info["display"],
                })

        canonical_url = f"https://www.autosafe.one/mot-check/{make_slug}/{model_slug}/{age_slug}/"
        template_name = "seo_model_age.html"

        template = jinja_env.get_template(template_name)
        html = template.render(
            make_display=make_info["display"],
            make_slug=make_slug,
            model_display=model_info["display"],
            model_slug=model_slug,
            age_band_display=AGE_BAND_DISPLAY.get(age_band_raw, age_band_raw),
            age_band_raw=age_band_raw,
            age_slug=age_slug,
            fail_rate=current_band["fail_rate"],
            total_tests=current_band["total_tests"],
            components=components,
            top_components=components[:3],
            all_age_bands=all_age_bands,
            competitors=competitors,
            canonical_url=canonical_url,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- Component Deep-Dive pages: /mot-check/{make}/{model}/problems/{component}/ ---

    @app.get("/mot-check/{make_slug}/{model_slug}/problems/{component_slug}/", response_class=HTMLResponse)
    def seo_model_component(make_slug: str, model_slug: str, component_slug: str):
        # Resolve component slug to internal name
        # We need a map from slug (e.g. 'suspension') to display name and internal key
        # COMPONENTS list has (col, name) e.g. ('Risk_Suspension', 'Suspension')
        
        target_component = None
        target_col = None
        
        # Simple slug matching
        for col, name in COMPONENTS:
            if _slugify(name) == component_slug:
                target_component = name
                target_col = col
                break
        
        if not target_component:
             return _not_found_html("Component not found.")

        if make_slug not in _make_by_slug:
            return _not_found_html("Make not found.")
        if (make_slug, model_slug) not in _model_by_slug:
            return _not_found_html(
                f"Model not found for {_make_by_slug[make_slug]['display']}."
            )

        cache_key = f"seo:comp:{make_slug}:{model_slug}:{component_slug}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        make_info = _make_by_slug[make_slug]
        model_info = _model_by_slug[(make_slug, model_slug)]
        make = make_info["make"]
        model = model_info["model_id"]

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            # Query overall stats to check if this component is actually an issue
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            overall = _query_model_overall(conn, make, model)
            conn.row_factory = old_factory
            
        if not overall:
             return _not_found_html("Data not available.")

            # Find the recorded component-category rate.
        comp_risk = None
        for c in overall["components"]:
            if c["name"] == target_component:
                comp_risk = c["risk"]
                break

        if comp_risk is None:
            return _not_found_html("Complete component evidence is not available for this model group.")
        
        # Threshold check: Is this actually a problem?
        # If risk is very low (< 2%), maybe don't index this page to avoid thin content
        # But for now, let's render it if it matches user intent
        
        # Get repair cost data
        # Normalize name for lookup in REPAIR_COSTS
        # e.g. "Lamps, Reflectors And Electrical Equipment" -> "Lamps_Reflectors_And_Electrical_Equipment"
        # Actually our REPAIR_COSTS keys are simplified. 
        # let's try to match logic in repair_costs.py
        
        # COMPONENT_MAP in repair_costs.py maps Risk columns to simple keys
        # We can replicate that mapping here or reuse it if it was public
        # It's inside the function. Let's recreate a simple map or modify repair_costs.py (too invasive)
        # Let's just hardcode the mapping here as it's stable
        
        COST_KEY_MAP = {
            "Risk_Brakes": "Brakes",
            "Risk_Suspension": "Suspension",
            "Risk_Tyres": "Tyres",
            "Risk_Steering": "Steering",
            "Risk_Visibility": "Visibility",
            "Risk_Lamps_Reflectors_And_Electrical_Equipment": "Lamps_Reflectors_And_Electrical_Equipment",
            "Risk_Body_Chassis_Structure": "Body_Chassis_Structure",
        }
        
        cost_key = COST_KEY_MAP.get(target_col)
        cost_data = REPAIR_COSTS.get(cost_key)
        
        canonical_url = f"https://www.autosafe.one/mot-check/{make_slug}/{model_slug}/problems/{component_slug}/"

        template = jinja_env.get_template("seo_component.html")
        html = template.render(
            make_display=make_info["display"],
            make_slug=make_slug,
            model_display=model_info["display"],
            model_slug=model_slug,
            component_name=target_component,
            component_slug=component_slug,
            risk=comp_risk,
            overall_fail_rate=overall["fail_rate"],
            total_tests=overall["total_tests"],
            cost_data=cost_data,
            top_components=overall["components"][:3], # For context
            canonical_url=canonical_url,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)


    # --- Regional pages: /local-mot/{city_slug}/ ---

    @app.get("/local-mot/{city_slug}/", response_class=HTMLResponse)
    def seo_local_page(city_slug: str):
        city_slug = city_slug.lower()
        if city_slug not in RETIRED_LOCAL_CITY_SLUGS:
            return _not_found_html("City page not found.")

        # These pages inferred local failure rates from the dataset-wide
        # reference and labelled unranked database entries as approved/top
        # rated. Preserve known URLs without preserving unsupported claims.
        return RedirectResponse(url="/", status_code=301)


    # --- K7 Pillar Page: "Will My Car Pass Its MOT?" ---

    @app.get("/will-my-car-pass-mot/", response_class=HTMLResponse)
    def seo_k7_pillar():
        cache_key = "seo:k7-pillar"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row

            # Top 20 models by test volume
            top_models = []
            for (make_slug, model_slug), model_info in _model_by_slug.items():
                make = model_info["make"]
                model = model_info["model_id"]
                overall = _query_model_overall(conn, make, model)
                if overall:
                    make_info = _make_by_slug.get(make_slug, {})
                    top_models.append({
                        "make_display": make_info.get("display", make),
                        "model_display": model_info["display"],
                        "make_slug": make_slug,
                        "model_slug": model_slug,
                        "fail_rate": overall["fail_rate"],
                        "total_tests": overall["total_tests"],
                    })

            top_models.sort(key=lambda m: m["total_tests"], reverse=True)
            top_models = top_models[:20]

            # Dataset-wide weighted component-category rates.
            comp_cols = ", ".join(
                f"CASE WHEN COUNT({col}) = COUNT(*) "
                f"THEN ROUND(SUM({col} * Total_Tests) / NULLIF(SUM(Total_Tests), 0), 4) END as {col}"
                for col, _ in COMPONENTS
            )
            row = conn.execute(
                f"""SELECT SUM(Total_Tests) as total_tests,
                           {comp_cols}
                    FROM risks
                    WHERE age_band != 'Unknown'"""
            ).fetchone()

            if not row or not row["total_tests"]:
                conn.row_factory = old_factory
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            total_tests_analysed = int(row["total_tests"])

            top_components = []
            if row:
                for col, name in COMPONENTS:
                    val = row[col]
                    if val is not None:
                        top_components.append({"name": name, "avg_risk": float(val)})
                top_components.sort(key=lambda c: c["avg_risk"], reverse=True)

            conn.row_factory = old_factory

        template = jinja_env.get_template("seo_pillar_k7.html")
        html = template.render(
            top_models=top_models,
            dataset_reference_rate=DATASET_REFERENCE_FAIL_RATE,
            top_components=top_components,
            total_tests_analysed=total_tests_analysed,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- /insights/ data story: Unreliable 3-year-old cars 2026 ---

    @app.get("/insights/unreliable-3-year-old-cars-2026/", response_class=HTMLResponse)
    def seo_unreliable_cars():
        # The historical page queried a non-existent ``0-3`` band and then
        # described it as first-MOT evidence. Preserve inbound links without
        # serving that invalid interpretation.
        return RedirectResponse(
            url="/guides/mot-failure-rates-by-car",
            status_code=301,
        )


    # --- March 2026 MOT Rush insight page ---

    @app.get("/insights/march-mot-rush-2026/", response_class=HTMLResponse)
    def seo_march_rush():
        # Retired for the same invalid ``0-3``-band/first-MOT assumption as
        # the ranking page above. The general guide is the honest successor.
        return RedirectResponse(url="/guides/first-mot-guide", status_code=301)


    # --- /insights/ routes (Data PR stories) ---

    @app.get("/insights/", response_class=HTMLResponse)
    def insights_index():
        return RedirectResponse(
            url="/guides/mot-failure-rates-by-car",
            status_code=301,
        )


    @app.get("/insights/{story_slug}/", response_class=HTMLResponse)
    def insights_story(story_slug: str):
        return RedirectResponse(
            url="/guides/mot-failure-rates-by-car",
            status_code=301,
        )


    @app.get("/sitemap.xml", response_class=Response)
    def sitemap_index():
        """Sitemap index pointing to segmented sub-sitemaps."""
        cache_key = "sitemap:index"
        if cache_key in _sitemap_cache:
            return Response(
                content=_sitemap_cache[cache_key],
                media_type="application/xml",
                headers={"Cache-Control": "public, max-age=3600"},
            )

        base = "https://www.autosafe.one"
        sitemaps = [
            (f"{base}/sitemap-content.xml", SITE_CONTENT_REVISION),
            (f"{base}/sitemap-makes.xml", DATASET_ARTIFACT_REVISION),
            (f"{base}/sitemap-models.xml", DATASET_ARTIFACT_REVISION),
            (f"{base}/sitemap-comparisons.xml", DATASET_ARTIFACT_REVISION),
        ]

        entries = []
        for loc, lastmod in sitemaps:
            entries.append(
                f"  <sitemap>\n"
                f"    <loc>{loc}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                f"  </sitemap>"
            )

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(entries)
            + "\n</sitemapindex>\n"
        )

        _sitemap_cache[cache_key] = xml
        return Response(
            content=xml,
            media_type="application/xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    def _build_urlset(urls: list[str]) -> str:
        """Build a <urlset> XML string from a list of <url> entries."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>\n"
        )

    def _url_entry(loc: str, lastmod: str, priority: str, changefreq: str) -> str:
        return (
            f"  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <priority>{priority}</priority>\n"
            f"    <changefreq>{changefreq}</changefreq>\n"
            f"  </url>"
        )

    @app.get("/sitemap-content.xml", response_class=Response)
    def sitemap_content():
        """Sub-sitemap: homepage, pillar, guides, insights, legal pages."""
        cache_key = "sitemap:content"
        if cache_key in _sitemap_cache:
            return Response(content=_sitemap_cache[cache_key], media_type="application/xml",
                            headers={"Cache-Control": "public, max-age=3600"})

        base = "https://www.autosafe.one"
        urls = []

        static_pages = [
            ("/", "1.0", "weekly"),
            ("/mot-check/", "0.9", "weekly"),
            ("/will-my-car-pass-mot/", "0.95", "weekly"),
            ("/guides/mot-checklist", "0.8", "monthly"),
            ("/guides/common-mot-failures", "0.8", "monthly"),
            ("/guides/when-is-mot-due", "0.8", "monthly"),
            ("/guides/mot-failure-rates-by-car", "0.8", "monthly"),
            ("/guides/mot-rules-2026", "0.8", "monthly"),
            ("/guides/mot-defect-categories", "0.8", "monthly"),
            ("/guides/mot-cost", "0.8", "monthly"),
            ("/guides/mot-history-check", "0.8", "monthly"),
            ("/guides/first-mot-guide", "0.8", "monthly"),
            ("/privacy", "0.3", "yearly"),
            ("/terms", "0.3", "yearly"),
        ]
        for path, priority, freq in static_pages:
            urls.append(_url_entry(f"{base}{path}", SITE_CONTENT_REVISION, priority, freq))

        # Component hubs (top-level aggregation — indexable)
        for comp_slug in COMPONENT_SLUGS:
            urls.append(_url_entry(f"{base}/mot-check/problems/{comp_slug}/", DATASET_ARTIFACT_REVISION, "0.7", "monthly"))

        xml = _build_urlset(urls)
        _sitemap_cache[cache_key] = xml
        return Response(content=xml, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/sitemap-makes.xml", response_class=Response)
    def sitemap_makes():
        """Sub-sitemap: all make hub pages."""
        cache_key = "sitemap:makes"
        if cache_key in _sitemap_cache:
            return Response(content=_sitemap_cache[cache_key], media_type="application/xml",
                            headers={"Cache-Control": "public, max-age=3600"})

        base = "https://www.autosafe.one"
        urls = []
        for make_slug in sorted(_make_by_slug.keys()):
            urls.append(_url_entry(f"{base}/mot-check/{make_slug}/", DATASET_ARTIFACT_REVISION, "0.8", "monthly"))

        xml = _build_urlset(urls)
        _sitemap_cache[cache_key] = xml
        return Response(content=xml, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/sitemap-models.xml", response_class=Response)
    def sitemap_models():
        """Sub-sitemap: all model detail pages (the core money pages)."""
        cache_key = "sitemap:models"
        if cache_key in _sitemap_cache:
            return Response(content=_sitemap_cache[cache_key], media_type="application/xml",
                            headers={"Cache-Control": "public, max-age=3600"})

        base = "https://www.autosafe.one"
        urls = []
        for (make_slug, model_slug) in sorted(_model_by_slug.keys()):
            urls.append(_url_entry(f"{base}/mot-check/{make_slug}/{model_slug}/", DATASET_ARTIFACT_REVISION, "0.7", "monthly"))

        xml = _build_urlset(urls)
        _sitemap_cache[cache_key] = xml
        return Response(content=xml, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/sitemap-comparisons.xml", response_class=Response)
    def sitemap_comparisons():
        """Sub-sitemap: comparison pages."""
        cache_key = "sitemap:comparisons"
        if cache_key in _sitemap_cache:
            return Response(content=_sitemap_cache[cache_key], media_type="application/xml",
                            headers={"Cache-Control": "public, max-age=3600"})

        base = "https://www.autosafe.one"
        urls = []
        for (make1, model1), (make2, model2) in COMPARISON_PAIRS:
            s1 = f"{_slugify(make1)}-{_slugify(model1)}"
            s2 = f"{_slugify(make2)}-{_slugify(model2)}"
            urls.append(_url_entry(f"{base}/mot-check/compare/{s1}-vs-{s2}/", DATASET_ARTIFACT_REVISION, "0.6", "monthly"))

        xml = _build_urlset(urls)
        _sitemap_cache[cache_key] = xml
        return Response(content=xml, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/sitemap-local.xml", response_class=Response)
    def sitemap_local():
        """Retired local-page sitemap; point crawlers to the live index."""
        return RedirectResponse(url="/sitemap.xml", status_code=301)

    logger.info("SEO: Routes registered (/mot-check/, /mot-check/{make}/{model}/{age}/, /sitemap.xml index)")
