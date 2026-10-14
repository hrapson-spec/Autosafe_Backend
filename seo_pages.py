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
from page_revisions import homepage_sources, page_lastmod
from organic_pilot import MODEL_TEMPLATES, COMPARISON_TEMPLATES
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
_sitemap_cache: TTLCache = TTLCache(maxsize=8, ttl=3600)

# --- Slug lookup dicts (populated at startup) ---
# slug -> {"make": "FORD", "display": "Ford"}
_make_by_slug: dict = {}
# (make_slug, model_slug) -> {"model_id": "FIESTA", "display": "Fiesta", "make": "FORD"}
_model_by_slug: dict = {}
# make_slug -> [model_slug, ...]
_models_for_make: dict = {}
# (make_slug, model_slug) -> fail_rate (for data-driven related models)
_model_fail_rates: dict = {}
# (make_slug, model_slug) -> {"total_tests", "total_failures", "fail_rate"}; the
# same figures _query_model_overall returns, computed once at startup so the
# pillar page does not re-scan the table for every model (OA-006 G).
_model_totals: dict = {}
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


def age_band_pages_exist(make_slug: str, model_slug: str) -> bool:
    """Single source of truth for whether /mot-check/{make}/{model}/{band}-years/ is served.

    The age-band route and every template that emits an age-band link must use
    this predicate, so link emission can never outrun page existence (OA-006 B:
    model pages linked bands for every model with >= 100 tests while the route
    only served models with >= 10,000).
    """
    return (make_slug, model_slug) in _age_band_eligible

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


def _model_keys(make: str, model: str) -> list[str]:
    """The model_id values and prefixes matched by ``_model_where_clause``."""
    keys = [f"{make} {model}"]
    if model.endswith("-CLASS"):
        keys.append(f"{make} {model.replace('-CLASS', '')}")
    return keys


def _model_totals_from_groups(conn, known: dict[str, list[str]]) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    """Return {(make, model): (SUM(Total_Tests), SUM(Total_Failures))} for every known model.

    Either value is None when SQL would report NULL (no matching rows, or
    every matched value NULL); callers apply the same None checks the
    per-model queries did.

    One ``GROUP BY model_id`` scan replaces one full-table scan per model (the
    LIKE predicate in ``_model_where_clause`` cannot use the model_id index, so
    each per-model query scanned all rows). Matching reproduces the SQL
    predicate exactly: ``model_id = key OR model_id LIKE key || ' %'`` for each
    key. Any key containing a LIKE wildcard is resolved with the original SQL.
    """
    grouped = conn.execute(
        """SELECT model_id, SUM(Total_Tests) AS t, SUM(Total_Failures) AS f
           FROM risks WHERE age_band != 'Unknown' GROUP BY model_id"""
    )
    # Per model_id: (SUM(Total_Tests), SUM(Total_Failures)); a SUM is None only
    # when every value in the group is NULL, exactly as SQLite reports it.
    sums: dict[str, tuple[int | None, int | None]] = {}
    by_upper: dict[bytes, list[str]] = {}
    for model_id, t, f in grouped:
        if model_id is None:
            continue  # NULL never satisfies model_id = ? or model_id LIKE ?
        sums[model_id] = (t, f)
        by_upper.setdefault(model_id.encode("utf-8").upper(), []).append(model_id)
    upper_keys = sorted(by_upper)

    from bisect import bisect_left

    def _sql_sum(values):
        present = [v for v in values if v is not None]
        return sum(present) if present else None

    totals: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for make, models in known.items():
        for model in models:
            keys = _model_keys(make, model)
            if any(ch in key for key in keys for ch in "%_"):
                where, params = _model_where_clause(make, model)
                row = conn.execute(
                    f"SELECT SUM(Total_Tests), SUM(Total_Failures) FROM risks WHERE {where} AND age_band != 'Unknown'",
                    params,
                ).fetchone()
                totals[(make, model)] = (row[0], row[1]) if row else (None, None)
                continue
            matched: set[str] = set()
            for key in keys:
                if key in sums:
                    matched.add(key)
                needle = (key + " ").encode("utf-8").upper()
                i = bisect_left(upper_keys, needle)
                while i < len(upper_keys) and upper_keys[i].startswith(needle):
                    matched.update(by_upper[upper_keys[i]])
                    i += 1
            totals[(make, model)] = (
                _sql_sum(sums[m][0] for m in matched),
                _sql_sum(sums[m][1] for m in matched),
            )
    return totals


def initialize_seo_data(get_sqlite_connection):
    """
    Build slug lookup dicts at startup from KNOWN_MODELS,
    filtered to models with >= 100 tests in SQLite.

    Also records per-model totals (``_model_totals``) so the pillar page can
    rank models without re-querying every model per worker (OA-006 G).
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

    # One grouped scan gives every model's totals; the same numbers decide
    # inclusion (>= 100 tests), age-band eligibility (>= 10,000 tests), the
    # linking fail rate and the pillar ranking.
    with get_sqlite_connection() as conn:
        if conn is None:
            logger.error("SEO: Cannot initialize - no SQLite connection")
            return
        try:
            totals = _model_totals_from_groups(conn, known)
        except sqlite3.Error as e:
            logger.error("SEO model aggregation failed: type=%s", type(e).__name__)
            return

        valid_models = {key for key, (t, _f) in totals.items() if t is not None and t >= 100}
        age_band_candidates = {key for key, (t, _f) in totals.items() if t is not None and t >= 10000}

        # Fail rates use the identical SQLite arithmetic as the per-model
        # queries they replace: CAST(SUM(F) AS REAL) / SUM(T), and ROUND(.., 4)
        # for the value _query_model_overall reports on pages. A model whose
        # failures are all NULL has no rate (SQL returned NULL), exactly as
        # before: it is listed but excluded from rate-driven linking and the
        # pillar ranking.
        rates: dict[tuple[str, str], tuple[float, float]] = {}
        for key in valid_models:
            t, f = totals[key]
            if f is None:
                continue
            raw, rounded = conn.execute(
                "SELECT CAST(? AS REAL) / ?, ROUND(CAST(? AS REAL) / ?, 4)", (f, t, f, t)
            ).fetchone()
            rates[key] = (float(raw), float(rounded))

    # Build lookup dicts
    _make_by_slug.clear()
    _model_by_slug.clear()
    _models_for_make.clear()
    _age_band_eligible.clear()
    _model_fail_rates.clear()
    _model_totals.clear()

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

        if (make, model) in rates:
            raw_rate, rounded_rate = rates[(make, model)]
            _model_fail_rates[(make_slug, model_slug)] = raw_rate
            total_tests, total_failures = totals[(make, model)]
            _model_totals[(make_slug, model_slug)] = {
                "total_tests": int(total_tests),
                "total_failures": int(total_failures),
                "fail_rate": rounded_rate,
            }

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


def _query_model_cohorts(conn, make: str, model: str) -> list[dict]:
    """Recorded age/mileage groups, with no inference or sparse fallback."""
    where, params = _model_where_clause(make, model)
    rows = conn.execute(
        f"""SELECT age_band, mileage_band, SUM(Total_Tests) AS total_tests,
                   SUM(Total_Failures) AS total_failures
            FROM risks WHERE {where}
              AND age_band != 'Unknown' AND mileage_band != 'Unknown'
            GROUP BY age_band, mileage_band
            HAVING SUM(Total_Tests) >= 100 AND COUNT(Total_Failures) = COUNT(*)
            ORDER BY CASE age_band WHEN '0-2' THEN 1 WHEN '3-5' THEN 2
                      WHEN '6-10' THEN 3 WHEN '11-15' THEN 4 WHEN '15+' THEN 5 ELSE 6 END,
                     CASE mileage_band WHEN '0-30k' THEN 1 WHEN '30k-60k' THEN 2
                      WHEN '60k-100k' THEN 3 WHEN '100k+' THEN 4 ELSE 5 END""", params).fetchall()
    return [{"age_band": r["age_band"], "mileage_band": r["mileage_band"],
             "total_tests": int(r["total_tests"]), "total_failures": int(r["total_failures"]),
             "fail_rate": float(r["total_failures"] / r["total_tests"])}
            for r in rows if r["total_failures"] is not None
            and 0 <= r["total_failures"] <= r["total_tests"]]


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
<html lang="en-GB">
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

        template = jinja_env.get_template(COMPARISON_TEMPLATES.get(f"{slug1}-vs-{slug2}", "seo_compare.html"))
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

        # Crawlable links to the seven top-level component hubs, which are in
        # the sitemap but previously had no inbound internal link (OA-006 D).
        # Anchor text reuses each hub's own H1; no new copy claims.
        component_hubs = [
            {"slug": slug, "name": name} for slug, (_col, name) in COMPONENT_SLUGS.items()
        ]

        template = jinja_env.get_template("seo_index.html")
        html = template.render(makes=makes, component_hubs=component_hubs)
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
            # Query only the frozen treatment pages. Controls keep their content.
            cohorts = (_query_model_cohorts(conn, make, model)
                       if MODEL_TEMPLATES.get((make_slug, model_slug)) == "programme_model.html" else [])
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

        template = jinja_env.get_template(MODEL_TEMPLATES.get((make_slug, model_slug), "seo_model.html"))
        html = template.render(
            make_display=make_info["display"],
            make_slug=make_slug,
            model_display=model_info["display"],
            model_slug=model_slug,
            overall_fail_rate=overall["fail_rate"],
            overall_tests=overall["total_tests"],
            age_bands=age_bands,
            cohorts=cohorts,
            age_band_pages_enabled=age_band_pages_exist(make_slug, model_slug),
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
        if not age_band_pages_exist(make_slug, model_slug):
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

            # Top 20 models by test volume, from the startup totals (previously
            # one full-table scan per model on every cold cache: ~9 s).
            top_models = []
            for (make_slug, model_slug), model_info in _model_by_slug.items():
                overall = _model_totals.get((make_slug, model_slug))
                if overall:
                    make_info = _make_by_slug.get(make_slug, {})
                    top_models.append({
                        "make_display": make_info.get("display", model_info["make"]),
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


    # --- Sitemaps -------------------------------------------------------------
    # <lastmod> rule (OA-006 F): the last *significant* main-content change —
    # either the checked-in dataset artifact or the main content of a source
    # file that renders the page. Significant-revision dates come from
    # page_revisions.json (hash-verified by tests/test_seo.py; boilerplate,
    # footer, markup and analytics edits are recorded as non-significant and do
    # not move lastmod); the dataset date is DATASET_ARTIFACT_REVISION. No
    # runtime clock is consulted, so a worker restart never claims freshness.

    # Every template page is rendered by this module (Python-built copy such as
    # comparison titles, 404 text and link selection) plus seo_base + its template.
    TEMPLATE_PAGE_SOURCES = ("seo_pages.py", "templates/seo_base.html")

    def _template_lastmod(template_name: str, pilot_template: str | None = None) -> str:
        """lastmod for a dataset-rendering page built from seo_pages + seo_base + one template."""
        sources = (*TEMPLATE_PAGE_SOURCES, f"templates/{template_name}")
        if pilot_template:
            sources += (f"templates/{pilot_template}",)
        return page_lastmod(*sources,
                            dataset_revision=DATASET_ARTIFACT_REVISION)

    def _content_entries() -> list[tuple[str, str, str, str]]:
        """(loc, lastmod, priority, changefreq) for homepage, pillar, guides, legal, hubs."""
        base = "https://www.autosafe.one"
        entries = [
            (f"{base}/", page_lastmod(*homepage_sources()), "1.0", "weekly"),
            (f"{base}/mot-check/", _template_lastmod("seo_index.html"), "0.9", "weekly"),
            (f"{base}/will-my-car-pass-mot/", _template_lastmod("seo_pillar_k7.html"), "0.95", "weekly"),
        ]
        for slug in (
            "mot-checklist", "common-mot-failures", "when-is-mot-due",
            "mot-failure-rates-by-car", "mot-rules-2026", "mot-defect-categories",
            "mot-cost", "mot-history-check", "first-mot-guide",
        ):
            entries.append((f"{base}/guides/{slug}", page_lastmod(f"static/guides/{slug}.html"), "0.8", "monthly"))
        entries.append((f"{base}/privacy", page_lastmod("static/privacy.html"), "0.3", "yearly"))
        entries.append((f"{base}/terms", page_lastmod("static/terms.html"), "0.3", "yearly"))
        # Component hubs (top-level aggregation — indexable)
        for comp_slug in COMPONENT_SLUGS:
            entries.append((f"{base}/mot-check/problems/{comp_slug}/",
                            _template_lastmod("seo_component_hub.html"), "0.7", "monthly"))
        return entries

    def _make_entries() -> list[tuple[str, str, str, str]]:
        base = "https://www.autosafe.one"
        lastmod = _template_lastmod("seo_make.html")
        return [(f"{base}/mot-check/{make_slug}/", lastmod, "0.8", "monthly")
                for make_slug in sorted(_make_by_slug.keys())]

    def _model_entries() -> list[tuple[str, str, str, str]]:
        base = "https://www.autosafe.one"
        return [(f"{base}/mot-check/{make_slug}/{model_slug}/",
                 _template_lastmod("seo_model.html", MODEL_TEMPLATES.get((make_slug, model_slug))), "0.7", "monthly")
                for (make_slug, model_slug) in sorted(_model_by_slug.keys())]

    def _comparison_entries() -> list[tuple[str, str, str, str]]:
        base = "https://www.autosafe.one"
        entries = []
        for (make1, model1), (make2, model2) in COMPARISON_PAIRS:
            s1 = f"{_slugify(make1)}-{_slugify(model1)}"
            s2 = f"{_slugify(make2)}-{_slugify(model2)}"
            lastmod = _template_lastmod("seo_compare.html", COMPARISON_TEMPLATES.get(f"{s1}-vs-{s2}"))
            entries.append((f"{base}/mot-check/compare/{s1}-vs-{s2}/", lastmod, "0.6", "monthly"))
        return entries

    SUB_SITEMAPS = {
        "sitemap-content.xml": _content_entries,
        "sitemap-makes.xml": _make_entries,
        "sitemap-models.xml": _model_entries,
        "sitemap-comparisons.xml": _comparison_entries,
    }

    def _xml_response(xml: str) -> Response:
        return Response(content=xml, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=3600"})

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

    def _sub_sitemap(name: str) -> Response:
        cache_key = f"sitemap:{name}"
        if cache_key not in _sitemap_cache:
            urls = [_url_entry(*entry) for entry in SUB_SITEMAPS[name]()]
            _sitemap_cache[cache_key] = _build_urlset(urls)
        return _xml_response(_sitemap_cache[cache_key])

    @app.get("/sitemap.xml", response_class=Response)
    def sitemap_index():
        """Sitemap index pointing to segmented sub-sitemaps."""
        # Each sub-sitemap's lastmod is the latest of its entries.
        cache_key = "sitemap:index"
        if cache_key in _sitemap_cache:
            return _xml_response(_sitemap_cache[cache_key])

        base = "https://www.autosafe.one"
        entries = []
        complete = True
        for name, build in SUB_SITEMAPS.items():
            # lastmod is optional in a sitemap index; an empty sub-sitemap
            # (e.g. SEO data not yet initialised) is listed without one.
            lastmod = max((entry[1] for entry in build()), default=None)
            if lastmod is None:
                complete = False
            lastmod_line = f"    <lastmod>{lastmod}</lastmod>\n" if lastmod else ""
            entries.append(
                f"  <sitemap>\n"
                f"    <loc>{base}/{name}</loc>\n"
                f"{lastmod_line}"
                f"  </sitemap>"
            )

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(entries)
            + "\n</sitemapindex>\n"
        )
        if complete:
            _sitemap_cache[cache_key] = xml
        return _xml_response(xml)

    @app.get("/sitemap-content.xml", response_class=Response)
    def sitemap_content():
        """Sub-sitemap: homepage, pillar, guides, insights, legal pages."""
        # (Also the seven component hubs; the docstring is part of the OpenAPI snapshot.)
        return _sub_sitemap("sitemap-content.xml")

    @app.get("/sitemap-makes.xml", response_class=Response)
    def sitemap_makes():
        """Sub-sitemap: all make hub pages."""
        return _sub_sitemap("sitemap-makes.xml")

    @app.get("/sitemap-models.xml", response_class=Response)
    def sitemap_models():
        """Sub-sitemap: all model detail pages (the core money pages)."""
        return _sub_sitemap("sitemap-models.xml")

    @app.get("/sitemap-comparisons.xml", response_class=Response)
    def sitemap_comparisons():
        """Sub-sitemap: comparison pages."""
        return _sub_sitemap("sitemap-comparisons.xml")

    @app.get("/sitemap-local.xml", response_class=Response)
    def sitemap_local():
        """Retired local-page sitemap; point crawlers to the live index."""
        return RedirectResponse(url="/sitemap.xml", status_code=301)

    logger.info("SEO: Routes registered (/mot-check/, /mot-check/{make}/{model}/{age}/, /sitemap.xml index)")
