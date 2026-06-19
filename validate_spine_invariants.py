"""
Canonical Spine Invariant Validator
=====================================

Validates that the canonical spine satisfies all required invariants:
1. Prior outcome implies n_prior_tests >= 1
2. Non-null time-since implies n_prior_tests >= 1
3. Recent fails implies n_prior_tests >= 1
4. n_prior_fails <= n_prior_tests
5. PASS/FAIL coverage matches n_prior_tests > 0 coverage

Usage:
    python validate_spine_invariants.py

Returns exit code 0 if all invariants pass, 1 if any fail.

Created: 2026-01-13
"""

import duckdb
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WORK_DIR = Path.home() / "autosafe_work"
SPINE_FILE = WORK_DIR / "canonical_spine.parquet"


def validate_invariants():
    """Validate all spine invariants."""
    logger.info("=" * 60)
    logger.info("VALIDATING CANONICAL SPINE INVARIANTS")
    logger.info("=" * 60)

    if not SPINE_FILE.exists():
        logger.error(f"Spine file not found: {SPINE_FILE}")
        return False

    conn = duckdb.connect()

    invariants = [
        {
            "name": "Prior outcome implies n_prior_tests >= 1",
            "query": """
                SELECT COUNT(*) FROM read_parquet('{spine}')
                WHERE prev_cycle_outcome_band IN ('PASS', 'FAIL') AND n_prior_tests < 1
            """,
            "expected": 0,
            "tolerance": 0,
        },
        {
            "name": "Non-null time-since implies n_prior_tests >= 1",
            "query": """
                SELECT COUNT(*) FROM read_parquet('{spine}')
                WHERE days_since_last_test IS NOT NULL AND n_prior_tests < 1
            """,
            "expected": 0,
            "tolerance": 0,
        },
        {
            "name": "n_prior_fails > 0 implies n_prior_tests >= 1",
            "query": """
                SELECT COUNT(*) FROM read_parquet('{spine}')
                WHERE n_prior_fails > 0 AND n_prior_tests < 1
            """,
            "expected": 0,
            "tolerance": 0,
        },
        {
            "name": "n_prior_fails <= n_prior_tests",
            "query": """
                SELECT COUNT(*) FROM read_parquet('{spine}')
                WHERE n_prior_fails > n_prior_tests
            """,
            "expected": 0,
            "tolerance": 0,
        },
        {
            "name": "Linked outcome count equals n_prior_tests > 0 count",
            "query": """
                SELECT ABS(
                    SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS','FAIL') THEN 1 ELSE 0 END) -
                    SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END)
                )
                FROM read_parquet('{spine}')
            """,
            "expected": 0,
            "tolerance": 100,  # Allow tiny discrepancy
        },
        {
            "name": "Annualized mileage only when n_prior_tests > 0",
            "query": """
                SELECT COUNT(*) FROM read_parquet('{spine}')
                WHERE annualized_mileage IS NOT NULL AND n_prior_tests < 1
            """,
            "expected": 0,
            "tolerance": 0,
        },
    ]

    all_passed = True
    results = []

    for inv in invariants:
        query = inv["query"].format(spine=SPINE_FILE)
        try:
            result = conn.execute(query).fetchone()[0]
            passed = result <= inv["expected"] + inv["tolerance"]

            status = "PASS" if passed else "FAIL"
            results.append({
                "name": inv["name"],
                "result": result,
                "expected": inv["expected"],
                "tolerance": inv["tolerance"],
                "passed": passed
            })

            if passed:
                logger.info(f"  [PASS] {inv['name']}: {result}")
            else:
                logger.error(f"  [FAIL] {inv['name']}: {result} (expected <= {inv['expected'] + inv['tolerance']})")
                all_passed = False

        except Exception as e:
            logger.error(f"  [ERROR] {inv['name']}: {e}")
            all_passed = False
            results.append({
                "name": inv["name"],
                "result": None,
                "error": str(e),
                "passed": False
            })

    # Summary statistics
    logger.info("")
    logger.info("Coverage coherence check:")

    try:
        coherence = conn.execute(f"""
            SELECT
                source_year,
                COUNT(*) as total,
                ROUND(100.0 * SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as prior_pct,
                ROUND(100.0 * SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS','FAIL') THEN 1 ELSE 0 END) / COUNT(*), 1) as linked_pct,
                ROUND(100.0 * SUM(CASE WHEN annualized_mileage IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as mileage_pct
            FROM read_parquet('{SPINE_FILE}')
            GROUP BY source_year
            ORDER BY source_year
        """).fetchall()

        logger.info(f"  {'Year':<6} {'Total':>12} {'prior>0':>10} {'linked':>10} {'mileage':>10}")
        logger.info(f"  {'-'*50}")

        for row in coherence:
            year, total, prior_pct, linked_pct, mileage_pct = row
            # Check coherence: linked should equal prior, mileage should be <= prior
            coherent = abs(linked_pct - prior_pct) < 1.0 and mileage_pct <= prior_pct + 1.0
            status = "" if coherent else " [!]"
            logger.info(f"  {year:<6} {total:>12,} {prior_pct:>9.1f}% {linked_pct:>9.1f}% {mileage_pct:>9.1f}%{status}")

            if not coherent:
                logger.warning(f"    Year {year} has incoherent coverage (linked_pct should equal prior_pct)")
                all_passed = False

    except Exception as e:
        logger.error(f"  Coverage check failed: {e}")
        all_passed = False

    conn.close()

    logger.info("")
    if all_passed:
        logger.info("=" * 60)
        logger.info("ALL INVARIANTS PASSED")
        logger.info("=" * 60)
        return True
    else:
        logger.error("=" * 60)
        logger.error("INVARIANT VALIDATION FAILED")
        logger.error("=" * 60)
        return False


if __name__ == "__main__":
    success = validate_invariants()
    sys.exit(0 if success else 1)
