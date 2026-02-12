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
from datetime import date
from pathlib import Path

from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# --- Jinja2 setup ---
TEMPLATE_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

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
# Models with enough tests for age-band pages (staged rollout)
_age_band_eligible: set = set()  # set of (make_slug, model_slug)

# Age band slug mappings
AGE_BAND_SLUGS = {
    "0-3-years": "0-3",
    "3-5-years": "3-5",
    "6-10-years": "6-10",
    "10-15-years": "10-15",
    "15-plus-years": "15+",
}
AGE_BAND_DISPLAY = {
    "0-3": "0-3",
    "3-5": "3-5",
    "6-10": "6-10",
    "10-15": "10-15",
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

UK_AVERAGE_FAIL_RATE = 0.28


def _slugify(text: str) -> str:
    """Convert e.g. '3 SERIES' -> '3-series', 'LAND ROVER' -> 'land-rover'."""
    return text.lower().replace(" ", "-")


def _display_name(text: str) -> str:
    """Convert e.g. 'FORD' -> 'Ford', 'LAND ROVER' -> 'Land Rover', 'BMW' -> 'BMW'."""
    # Keep all-uppercase short names (BMW, MG, etc.)
    if len(text) <= 3 and text.isalpha():
        return text
    # Title-case everything else
    return text.title()


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
                    logger.warning(f"SEO: Error checking {make} {model}: {e}")

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

    total_pages = len(_make_by_slug) + len(_model_by_slug)
    logger.info(
        f"SEO: Initialized {len(_make_by_slug)} makes, "
        f"{len(_model_by_slug)} models ({total_pages} landing pages), "
        f"{len(_age_band_eligible)} models eligible for age-band pages"
    )


def _query_model_age_bands(conn, make: str, model: str) -> list[dict]:
    """Query age-band breakdown for a model (weighted average across mileage bands)."""
    where, params = _model_where_clause(make, model)
    comp_cols = ", ".join(
        f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
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
                WHEN '0-3' THEN 1
                WHEN '3-5' THEN 2
                WHEN '6-10' THEN 3
                WHEN '10-15' THEN 4
                WHEN '15+' THEN 5
                ELSE 6
            END""",
        params,
    ).fetchall()

    result = []
    for row in rows:
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
            "fail_rate": float(row["fail_rate"]) if row["fail_rate"] else 0,
            "worst_component": worst,
            "components": comp_risks,
        })
    return result


def _query_model_overall(conn, make: str, model: str) -> Optional[dict]:
    """Query overall failure rate for a model."""
    where, params = _model_where_clause(make, model)
    comp_cols = ", ".join(
        f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
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

    if not row or not row["total_tests"]:
        return None

    components = []
    for col, name in COMPONENTS:
        val = row[col] if row[col] else 0
        components.append({"name": name, "risk": float(val), "col": col})

    return {
        "total_tests": int(row["total_tests"]),
        "total_failures": int(row["total_failures"]),
        "fail_rate": float(row["fail_rate"]) if row["fail_rate"] else 0,
        "components": sorted(components, key=lambda c: c["risk"], reverse=True),
    }


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
            })
    # Sort by failure rate descending
    results.sort(key=lambda m: m["fail_rate"], reverse=True)
    return results


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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Playfair+Display:wght@500;600;700&display=swap" rel="stylesheet">
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

    @app.get("/mot-check/", response_class=HTMLResponse)
    async def seo_index():
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

    @app.get("/mot-check/{make_slug}/", response_class=HTMLResponse)
    async def seo_make(make_slug: str):
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
            other_makes=other_makes,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    @app.get("/mot-check/{make_slug}/{model_slug}/", response_class=HTMLResponse)
    async def seo_model(make_slug: str, model_slug: str):
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
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- Age-band pages: /mot-check/{make}/{model}/{age_slug}/ ---

    @app.get("/mot-check/{make_slug}/{model_slug}/{age_slug}/", response_class=HTMLResponse)
    async def seo_model_age(make_slug: str, model_slug: str, age_slug: str):
        if make_slug not in _make_by_slug:
            return _not_found_html("Make not found.")
        if (make_slug, model_slug) not in _model_by_slug:
            return _not_found_html(
                f"Model not found for {_make_by_slug[make_slug]['display']}."
            )
        # Only serve age-band pages for eligible models (staged rollout)
        if (make_slug, model_slug) not in _age_band_eligible:
            return _not_found_html("Age-band data not available for this model.")

        age_band_raw = AGE_BAND_SLUGS.get(age_slug)
        if not age_band_raw:
            return _not_found_html("Invalid age range.")

        cache_key = f"seo:age:{make_slug}:{model_slug}:{age_slug}"
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

        # Find the specific age band
        current_band = None
        for band in all_age_bands:
            if band["age_band"] == age_band_raw:
                current_band = band
                break

        if not current_band:
            return _not_found_html(
                f"Not enough test data for {make_info['display']} {model_info['display']} "
                f"in the {AGE_BAND_DISPLAY.get(age_band_raw, age_band_raw)} year age range."
            )

        # Build component list sorted by risk
        components = sorted(
            [{"name": name, "risk": current_band["components"].get(name, 0)}
             for _, name in COMPONENTS],
            key=lambda c: c["risk"],
            reverse=True,
        )

        # Get competitor models
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

        template = jinja_env.get_template("seo_model_age.html")
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

    # --- K7 Pillar Page: "Will My Car Pass Its MOT?" ---

    @app.get("/will-my-car-pass-mot/", response_class=HTMLResponse)
    async def seo_k7_pillar():
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

            # National average component risks
            comp_cols = ", ".join(
                f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
                for col, _ in COMPONENTS
            )
            row = conn.execute(
                f"""SELECT SUM(Total_Tests) as total_tests,
                           {comp_cols}
                    FROM risks
                    WHERE age_band != 'Unknown'"""
            ).fetchone()

            total_tests_analysed = int(row["total_tests"]) if row and row["total_tests"] else 142000000

            top_components = []
            if row:
                for col, name in COMPONENTS:
                    val = row[col] if row[col] else 0
                    top_components.append({"name": name, "avg_risk": float(val)})
                top_components.sort(key=lambda c: c["avg_risk"], reverse=True)

            conn.row_factory = old_factory

        template = jinja_env.get_template("seo_pillar_k7.html")
        html = template.render(
            top_models=top_models,
            national_avg_fail_rate=UK_AVERAGE_FAIL_RATE,
            top_components=top_components,
            total_tests_analysed=total_tests_analysed,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- Comparison pages: /mot-check/compare/{slug1}-vs-{slug2}/ ---

    @app.get("/mot-check/compare/{slug1}-vs-{slug2}/", response_class=HTMLResponse)
    async def seo_compare(slug1: str, slug2: str):
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
            canonical_url=canonical_url,
            slug1=slug1, slug2=slug2,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- /insights/ data story: Unreliable 3-year-old cars 2026 ---

    @app.get("/insights/unreliable-3-year-old-cars-2026/", response_class=HTMLResponse)
    async def seo_unreliable_cars():
        cache_key = "seo:unreliable-cars-2026"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row

            # Top 10 most unreliable 3-year-old cars (5,000+ tests for statistical significance)
            rows = conn.execute("""
                SELECT model_id,
                       SUM(Total_Tests) as total_tests,
                       SUM(Total_Failures) as total_failures,
                       ROUND(CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests), 4) as fail_rate
                FROM risks
                WHERE age_band = '0-3'
                GROUP BY model_id
                HAVING SUM(Total_Tests) >= 5000
                ORDER BY fail_rate DESC
                LIMIT 10
            """).fetchall()

            cars = []
            for rank, row in enumerate(rows, 1):
                model_id = row["model_id"]
                # Get component breakdown
                comp_row = conn.execute(f"""
                    SELECT {', '.join(f'ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}' for col, _ in COMPONENTS)}
                    FROM risks
                    WHERE model_id = ? AND age_band = '0-3'
                """, (model_id,)).fetchone()

                comp_risks = []
                if comp_row:
                    for col, name in COMPONENTS:
                        val = comp_row[col] if comp_row[col] else 0
                        comp_risks.append({"name": name, "risk": float(val)})
                    comp_risks.sort(key=lambda c: c["risk"], reverse=True)

                # Try to find AutoSafe page link for this model
                page_link = None
                for (ms, mds), info in _model_by_slug.items():
                    full_id = f"{info['make']} {info['model_id']}"
                    if full_id == model_id or model_id.startswith(full_id):
                        page_link = f"/mot-check/{ms}/{mds}/"
                        break

                cars.append({
                    "rank": rank,
                    "model_id": model_id,
                    "display_name": _display_name(model_id),
                    "total_tests": int(row["total_tests"]),
                    "total_failures": int(row["total_failures"]),
                    "fail_rate": float(row["fail_rate"]),
                    "top_components": comp_risks[:3],
                    "all_components": comp_risks,
                    "page_link": page_link,
                })

            # Total tests in the 0-3 age band for methodology note
            total_row = conn.execute("""
                SELECT SUM(Total_Tests) as total FROM risks WHERE age_band = '0-3'
            """).fetchone()
            total_young_tests = int(total_row["total"]) if total_row and total_row["total"] else 0

            conn.row_factory = old_factory

        template = jinja_env.get_template("seo_unreliable_cars.html")
        html = template.render(
            cars=cars,
            total_young_tests=total_young_tests,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- March 2026 MOT Rush insight page ---

    @app.get("/insights/march-mot-rush-2026/", response_class=HTMLResponse)
    async def seo_march_rush():
        cache_key = "seo:march-rush-2026"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        with get_sqlite_connection() as conn:
            if conn is None:
                return HTMLResponse("Service temporarily unavailable", status_code=503)
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row

            # Query top failing 0-3 year old cars (March 2023 plates hitting first MOT in 2026)
            rows = conn.execute("""
                SELECT model_id,
                       SUM(Total_Tests) as total_tests,
                       SUM(Total_Failures) as total_failures,
                       ROUND(CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests), 4) as fail_rate
                FROM risks
                WHERE age_band = '0-3'
                GROUP BY model_id
                HAVING SUM(Total_Tests) >= 5000
                ORDER BY fail_rate DESC
                LIMIT 20
            """).fetchall()

            # Build cars list with component breakdown
            cars = []
            for row in rows:
                model_id = row["model_id"]
                # Get component risks for this model at 0-3 age band
                comp_row = conn.execute(f"""
                    SELECT {', '.join(f'ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}' for col, _ in COMPONENTS)}
                    FROM risks
                    WHERE model_id = ? AND age_band = '0-3'
                """, (model_id,)).fetchone()

                comp_risks = []
                if comp_row:
                    for col, name in COMPONENTS:
                        val = comp_row[col] if comp_row[col] else 0
                        comp_risks.append({"name": name, "risk": float(val)})
                    comp_risks.sort(key=lambda c: c["risk"], reverse=True)

                cars.append({
                    "model_id": model_id,
                    "display_name": _display_name(model_id),
                    "total_tests": int(row["total_tests"]),
                    "fail_rate": float(row["fail_rate"]),
                    "top_components": comp_risks[:3],
                })

            # Get top 5 failure areas across all 0-3 year old vehicles
            comp_cols = ", ".join(
                f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
                for col, _ in COMPONENTS
            )
            overall_comp = conn.execute(f"""
                SELECT {comp_cols}, SUM(Total_Tests) as total_tests
                FROM risks
                WHERE age_band = '0-3'
            """).fetchone()

            top_failure_areas = []
            if overall_comp:
                for col, name in COMPONENTS:
                    val = overall_comp[col] if overall_comp[col] else 0
                    top_failure_areas.append({"name": name, "risk": float(val)})
                top_failure_areas.sort(key=lambda c: c["risk"], reverse=True)

            conn.row_factory = old_factory

        # Popular 2023 sellers to link to
        popular_models = [
            ("ford", "fiesta"), ("vauxhall", "corsa"), ("volkswagen", "golf"),
            ("nissan", "qashqai"), ("ford", "focus"), ("toyota", "yaris"),
            ("kia", "sportage"), ("hyundai", "tucson"), ("peugeot", "208"),
            ("volkswagen", "polo"),
        ]
        model_links = []
        for make_slug, model_slug in popular_models:
            if (make_slug, model_slug) in _model_by_slug:
                info = _model_by_slug[(make_slug, model_slug)]
                make_info = _make_by_slug.get(make_slug, {})
                model_links.append({
                    "make_slug": make_slug,
                    "model_slug": model_slug,
                    "make_display": make_info.get("display", make_slug.title()),
                    "model_display": info["display"],
                })

        template = jinja_env.get_template("seo_march_rush.html")
        html = template.render(
            cars=cars,
            top_failure_areas=top_failure_areas[:5],
            model_links=model_links,
        )
        _seo_cache[cache_key] = html
        return _html_response(html)

    # --- /insights/ routes (Data PR stories) ---

    @app.get("/insights/", response_class=HTMLResponse)
    async def insights_index():
        cache_key = "seo:insights:index"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        from data_stories.query_engine import STORY_QUERIES
        stories = []
        for name, query_fn in STORY_QUERIES.items():
            try:
                story = query_fn()
                stories.append(story)
            except Exception as e:
                logger.warning(f"Insights: Failed to load story '{name}': {e}")

        template = jinja_env.get_template("seo_insights.html")
        html = template.render(stories=stories)
        _seo_cache[cache_key] = html
        return _html_response(html)

    @app.get("/insights/{story_slug}/", response_class=HTMLResponse)
    async def insights_story(story_slug: str):
        cache_key = f"seo:insights:{story_slug}"
        if cache_key in _seo_cache:
            return _html_response(_seo_cache[cache_key])

        from data_stories.query_engine import STORY_QUERIES
        # Find the story by slug
        story_data = None
        for name, query_fn in STORY_QUERIES.items():
            try:
                candidate = query_fn()
                if candidate["slug"] == story_slug:
                    story_data = candidate
                    break
            except Exception as e:
                logger.warning(f"Insights: Failed to query story '{name}': {e}")

        if not story_data:
            return _not_found_html("Insight report not found.")

        from data_stories.story_templates import render_html
        html = render_html(story_data)
        _seo_cache[cache_key] = html
        return _html_response(html)

    @app.get("/sitemap.xml", response_class=Response)
    async def sitemap():
        cache_key = "sitemap"
        if cache_key in _sitemap_cache:
            return Response(
                content=_sitemap_cache[cache_key],
                media_type="application/xml",
                headers={"Cache-Control": "public, max-age=3600"},
            )

        today = date.today().isoformat()
        base = "https://www.autosafe.one"

        urls = []
        # Static pages
        static_pages = [
            ("/", "1.0", "weekly"),
            ("/will-my-car-pass-mot/", "0.95", "weekly"),
            ("/static/guides/mot-checklist.html", "0.8", "monthly"),
            ("/static/guides/common-mot-failures.html", "0.8", "monthly"),
            ("/static/guides/when-is-mot-due.html", "0.8", "monthly"),
            ("/static/guides/mot-failure-rates-by-car.html", "0.8", "monthly"),
            ("/static/guides/mot-rules-2026.html", "0.8", "monthly"),
            ("/static/guides/mot-defect-categories.html", "0.8", "monthly"),
            ("/static/guides/mot-cost.html", "0.8", "monthly"),
            ("/static/guides/mot-history-check.html", "0.8", "monthly"),
            ("/static/guides/first-mot-guide.html", "0.8", "monthly"),
            ("/static/privacy.html", "0.3", "yearly"),
            ("/static/terms.html", "0.3", "yearly"),
            ("/insights/unreliable-3-year-old-cars-2026/", "0.7", "monthly"),
            ("/insights/march-mot-rush-2026/", "0.7", "monthly"),
        ]
        for path, priority, freq in static_pages:
            urls.append(
                f"  <url>\n"
                f"    <loc>{base}{path}</loc>\n"
                f"    <lastmod>{today}</lastmod>\n"
                f"    <priority>{priority}</priority>\n"
                f"    <changefreq>{freq}</changefreq>\n"
                f"  </url>"
            )

        # Insights pages
        urls.append(
            f"  <url>\n"
            f"    <loc>{base}/insights/</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <priority>0.8</priority>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"  </url>"
        )
        try:
            from data_stories.query_engine import STORY_QUERIES
            for name, query_fn in STORY_QUERIES.items():
                try:
                    story = query_fn()
                    urls.append(
                        f"  <url>\n"
                        f"    <loc>{base}/insights/{story['slug']}/</loc>\n"
                        f"    <lastmod>{today}</lastmod>\n"
                        f"    <priority>0.7</priority>\n"
                        f"    <changefreq>monthly</changefreq>\n"
                        f"  </url>"
                    )
                except Exception:
                    pass
        except ImportError:
            pass

        # SEO index page
        urls.append(
            f"  <url>\n"
            f"    <loc>{base}/mot-check/</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <priority>0.9</priority>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"  </url>"
        )

        # Make pages
        for make_slug in sorted(_make_by_slug.keys()):
            urls.append(
                f"  <url>\n"
                f"    <loc>{base}/mot-check/{make_slug}/</loc>\n"
                f"    <lastmod>{today}</lastmod>\n"
                f"    <priority>0.8</priority>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"  </url>"
            )

        # Model pages
        for (make_slug, model_slug) in sorted(_model_by_slug.keys()):
            urls.append(
                f"  <url>\n"
                f"    <loc>{base}/mot-check/{make_slug}/{model_slug}/</loc>\n"
                f"    <lastmod>{today}</lastmod>\n"
                f"    <priority>0.7</priority>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"  </url>"
            )

        # Age-band pages (only for eligible models)
        for (make_slug, model_slug) in sorted(_age_band_eligible):
            for age_slug in AGE_BAND_SLUGS:
                urls.append(
                    f"  <url>\n"
                    f"    <loc>{base}/mot-check/{make_slug}/{model_slug}/{age_slug}/</loc>\n"
                    f"    <lastmod>{today}</lastmod>\n"
                    f"    <priority>0.6</priority>\n"
                    f"    <changefreq>monthly</changefreq>\n"
                    f"  </url>"
                )

        # Comparison pages
        for (make1, model1), (make2, model2) in COMPARISON_PAIRS:
            s1 = f"{_slugify(make1)}-{_slugify(model1)}"
            s2 = f"{_slugify(make2)}-{_slugify(model2)}"
            urls.append(
                f"  <url>\n"
                f"    <loc>{base}/mot-check/compare/{s1}-vs-{s2}/</loc>\n"
                f"    <lastmod>{today}</lastmod>\n"
                f"    <priority>0.6</priority>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"  </url>"
            )

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>\n"
        )

        _sitemap_cache[cache_key] = xml
        return Response(
            content=xml,
            media_type="application/xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    logger.info("SEO: Routes registered (/mot-check/, /mot-check/{make}/{model}/{age}/, /sitemap.xml)")
