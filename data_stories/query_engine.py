"""
Query Engine for Data Stories
==============================

SQL queries against SQLite autosafe.db to extract newsworthy data.
Reuses the same weighted-average pattern from seo_pages.py.
"""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum test threshold (matches database.py:48)
MIN_TESTS = 100

# Component columns (matches seo_pages.py:47-55)
COMPONENTS = [
    ("Risk_Brakes", "Brakes"),
    ("Risk_Suspension", "Suspension"),
    ("Risk_Tyres", "Tyres"),
    ("Risk_Steering", "Steering"),
    ("Risk_Visibility", "Visibility"),
    ("Risk_Lamps_Reflectors_And_Electrical_Equipment", "Lamps & Electrics"),
    ("Risk_Body_Chassis_Structure", "Body & Chassis"),
]

_PROJECT_ROOT = Path(__file__).parent.parent
_CANDIDATE_PATHS = [
    Path("/tmp/autosafe.db"),          # Production (built on container start)
    _PROJECT_ROOT / "autosafe.db",     # Local development
]
DB_FILE = next((p for p in _CANDIDATE_PATHS if p.exists()), _CANDIDATE_PATHS[-1])


def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with Row factory."""
    if not DB_FILE.exists():
        raise FileNotFoundError(f"SQLite database not found at {DB_FILE}")
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


def _extract_make_model(model_id: str) -> tuple[str, str]:
    """Split 'FORD FIESTA' into ('Ford', 'Fiesta'). Handles multi-word makes."""
    # Known multi-word makes
    multi_word_makes = [
        "LAND ROVER", "MERCEDES-BENZ", "ALFA ROMEO", "ASTON MARTIN",
        "ROLLS ROYCE", "DS AUTOMOBILES",
    ]
    upper = model_id.upper()
    for make in multi_word_makes:
        if upper.startswith(make + " "):
            model = model_id[len(make) + 1:]
            return _display(make), _display(model)
    parts = model_id.split(" ", 1)
    if len(parts) == 2:
        return _display(parts[0]), _display(parts[1])
    return _display(model_id), ""


def _display(text: str) -> str:
    """Convert 'FORD' -> 'Ford', 'BMW' -> 'BMW'."""
    if len(text) <= 3 and text.isalpha():
        return text
    return text.title()


def query_reliability_ranking(limit: int = 10, order: str = "best") -> dict:
    """
    Top/bottom models by overall failure rate.

    Returns dict with 'title', 'subtitle', 'data' (list of model dicts),
    and 'methodology'.

    order: 'best' (lowest fail rate) or 'worst' (highest fail rate)
    """
    direction = "ASC" if order == "best" else "DESC"
    adjective = "Most Reliable" if order == "best" else "Least Reliable"

    conn = get_connection()
    try:
        # Aggregate by model_id: weighted failure rate across all bands
        # Only include models with substantial test volume (10k+ for top-level rankings)
        comp_cols = ", ".join(
            f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
            for col, _ in COMPONENTS
        )
        rows = conn.execute(
            f"""
            SELECT
                -- Extract make as first word (or known multi-word make)
                model_id,
                SUM(Total_Tests) as total_tests,
                SUM(Total_Failures) as total_failures,
                ROUND(CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests), 4) as fail_rate,
                {comp_cols}
            FROM risks
            WHERE age_band != 'Unknown'
            GROUP BY model_id
            HAVING SUM(Total_Tests) >= 10000
            ORDER BY fail_rate {direction}
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        data = []
        for row in rows:
            make, model = _extract_make_model(row["model_id"])
            # Find worst component
            comp_risks = {}
            for col, name in COMPONENTS:
                val = row[col]
                if val is not None:
                    comp_risks[name] = round(float(val) * 100, 1)
            worst = max(comp_risks, key=comp_risks.get) if comp_risks else "N/A"

            data.append({
                "rank": len(data) + 1,
                "make": make,
                "model": model,
                "model_id": row["model_id"],
                "fail_rate": round(float(row["fail_rate"]) * 100, 1),
                "total_tests": int(row["total_tests"]),
                "worst_component": worst,
                "component_risks": comp_risks,
            })

        return {
            "story_type": "reliability_ranking",
            "slug": f"{'most' if order == 'best' else 'least'}-reliable-cars",
            "title": f"Britain's {limit} {adjective} Cars for MOT",
            "subtitle": f"Ranked by MOT failure rate from millions of real test results",
            "data": data,
            "methodology": (
                f"Failure rates are weighted averages across all age and mileage bands, "
                f"calculated from official DVSA MOT records. Only models with 10,000+ "
                f"recorded tests are included. Data covers 142 million MOT tests across the UK."
            ),
            "key_stat": (
                f"The {adjective.lower()} car for MOT is the "
                f"{data[0]['make']} {data[0]['model']} with a {data[0]['fail_rate']}% failure rate"
                if data else "No data available"
            ),
        }
    finally:
        conn.close()


def query_first_mot_failures(limit: int = 10) -> dict:
    """
    Cars most likely to fail their first MOT (age band 3-5 years).

    Young cars failing MOT is newsworthy -- these should be reliable.
    """
    conn = get_connection()
    try:
        comp_cols = ", ".join(
            f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
            for col, _ in COMPONENTS
        )
        rows = conn.execute(
            f"""
            SELECT
                model_id,
                SUM(Total_Tests) as total_tests,
                SUM(Total_Failures) as total_failures,
                ROUND(CAST(SUM(Total_Failures) AS REAL) / SUM(Total_Tests), 4) as fail_rate,
                {comp_cols}
            FROM risks
            WHERE age_band = '3-5'
            GROUP BY model_id
            HAVING SUM(Total_Tests) >= 5000
            ORDER BY fail_rate DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        data = []
        for row in rows:
            make, model = _extract_make_model(row["model_id"])
            comp_risks = {}
            for col, name in COMPONENTS:
                val = row[col]
                if val is not None:
                    comp_risks[name] = round(float(val) * 100, 1)
            worst = max(comp_risks, key=comp_risks.get) if comp_risks else "N/A"

            data.append({
                "rank": len(data) + 1,
                "make": make,
                "model": model,
                "model_id": row["model_id"],
                "fail_rate": round(float(row["fail_rate"]) * 100, 1),
                "total_tests": int(row["total_tests"]),
                "worst_component": worst,
                "component_risks": comp_risks,
            })

        return {
            "story_type": "first_mot_failures",
            "slug": "cars-most-likely-fail-first-mot",
            "title": "Cars Most Likely to Fail Their First MOT",
            "subtitle": "Which 3-5 year old cars struggle most at the testing station?",
            "data": data,
            "methodology": (
                "Analysis of MOT test results for vehicles aged 3-5 years (the typical "
                "age for a first or second MOT). Only models with 5,000+ tests in this "
                "age band are included. Data from official DVSA MOT records covering "
                "142 million tests."
            ),
            "key_stat": (
                f"The {data[0]['make']} {data[0]['model']} has a {data[0]['fail_rate']}% "
                f"failure rate at just 3-5 years old -- the worst of any popular car"
                if data else "No data available"
            ),
        }
    finally:
        conn.close()


def query_component_breakdown() -> dict:
    """
    MOT failure breakdown by component across all vehicles.

    Shows which components cause the most MOT failures overall,
    and which vehicle segments are worst for each.
    """
    conn = get_connection()
    try:
        # Overall component failure rates (weighted average across all vehicles)
        comp_cols = ", ".join(
            f"ROUND(SUM({col} * Total_Tests) / SUM(Total_Tests), 4) as {col}"
            for col, _ in COMPONENTS
        )
        overall = conn.execute(
            f"""
            SELECT
                SUM(Total_Tests) as total_tests,
                {comp_cols}
            FROM risks
            WHERE age_band != 'Unknown'
            """,
        ).fetchone()

        overall_risks = []
        for col, name in COMPONENTS:
            val = overall[col] if overall[col] else 0
            overall_risks.append({
                "component": name,
                "risk": round(float(val) * 100, 1),
                "col": col,
            })
        overall_risks.sort(key=lambda c: c["risk"], reverse=True)

        # Per age-band component breakdown
        age_band_data = []
        age_rows = conn.execute(
            f"""
            SELECT
                age_band,
                SUM(Total_Tests) as total_tests,
                {comp_cols}
            FROM risks
            WHERE age_band != 'Unknown'
            GROUP BY age_band
            HAVING SUM(Total_Tests) >= {MIN_TESTS}
            ORDER BY CASE age_band
                WHEN '0-3' THEN 1
                WHEN '3-5' THEN 2
                WHEN '6-10' THEN 3
                WHEN '10-15' THEN 4
                WHEN '15+' THEN 5
                ELSE 6
            END
            """,
        ).fetchall()

        for row in age_rows:
            comp_risks = {}
            for col, name in COMPONENTS:
                val = row[col]
                if val is not None:
                    comp_risks[name] = round(float(val) * 100, 1)
            worst = max(comp_risks, key=comp_risks.get) if comp_risks else "N/A"
            age_band_data.append({
                "age_band": row["age_band"],
                "total_tests": int(row["total_tests"]),
                "components": comp_risks,
                "worst_component": worst,
            })

        return {
            "story_type": "component_breakdown",
            "slug": "mot-failure-breakdown-by-component",
            "title": "What Really Causes MOT Failures? Component Breakdown",
            "subtitle": "The 7 MOT failure areas ranked by how often they catch drivers out",
            "overall_risks": overall_risks,
            "age_bands": age_band_data,
            "total_tests": int(overall["total_tests"]) if overall["total_tests"] else 0,
            "methodology": (
                "Component failure rates are weighted averages across all makes, models, "
                "ages and mileages in the DVSA dataset. Each component rate represents "
                "the probability of that specific area causing an MOT failure. Data covers "
                "142 million MOT tests across the UK."
            ),
            "key_stat": (
                f"{overall_risks[0]['component']} is the #1 cause of MOT failures at "
                f"{overall_risks[0]['risk']}%, followed by {overall_risks[1]['component']} "
                f"at {overall_risks[1]['risk']}%"
                if len(overall_risks) >= 2 else "No data available"
            ),
        }
    finally:
        conn.close()


# Registry of available story queries
STORY_QUERIES = {
    "reliability_ranking": lambda: query_reliability_ranking(limit=10, order="worst"),
    "most_reliable": lambda: query_reliability_ranking(limit=10, order="best"),
    "first_mot_failures": query_first_mot_failures,
    "component_breakdown": query_component_breakdown,
}
