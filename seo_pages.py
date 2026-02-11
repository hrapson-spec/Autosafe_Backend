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
_seo_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)
_sitemap_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)

# --- Slug lookup dicts (populated at startup) ---
# slug -> {"make": "FORD", "display": "Ford"}
_make_by_slug: dict = {}
# (make_slug, model_slug) -> {"model_id": "FIESTA", "display": "Fiesta", "make": "FORD"}
_model_by_slug: dict = {}
# make_slug -> [model_slug, ...]
_models_for_make: dict = {}

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

    # Build lookup dicts
    _make_by_slug.clear()
    _model_by_slug.clear()
    _models_for_make.clear()

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
        f"{len(_model_by_slug)} models ({total_pages} landing pages)"
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
            ("/static/guides/first-mot-guide.html", "0.8", "monthly"),
            ("/static/privacy.html", "0.3", "yearly"),
            ("/static/terms.html", "0.3", "yearly"),
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

    logger.info("SEO: Routes registered (/mot-check/, /sitemap.xml)")
