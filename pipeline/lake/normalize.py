"""Canonicalization rules: raw DVSA bulk values -> lake values.

Every rule exists as BOTH a DuckDB SQL expression builder (production path,
set-based) and a plain-Python twin (unit-testable, and the documentation of
record for the rule). The twins are asserted equivalent in
tests/test_pipeline_lake.py by running the SQL against fixture frames.

Hazards these rules exist for (all recorded in the repo's history):
- ID float-contamination: test_id/vehicle_id parsed as float64 lose
  precision and grow a ".0" suffix (check_match.py:11,24 had to strip it on
  both sides). IDs are read as VARCHAR and cast here, never inferred.
- first_use_date garbage: sentinel pre-1900 dates, and first-use recorded
  AFTER the test date (import vehicles with backfilled records). Age bands
  and the v58 left-truncation coverage flag both depend on this field.
- May-2018 RfR taxonomy switch: outcome vocabularies and item classes
  changed mid-2018; every lake row carries its taxonomy_era.
"""
from datetime import date
from typing import Optional

# Digital MOT records begin 2005 (static/guides/mot-history-check.html); the
# EU roadworthiness-directive taxonomy switched on 20 May 2018.
DIGITAL_RECORDS_START = date(2005, 1, 1)
TAXONOMY_SWITCH_DATE = date(2018, 5, 20)

# Canonical test outcomes. Source files carry several spellings across
# epochs; unknown values become 'UNKNOWN' and are gated by
# checks.check_no_unknown_outcomes rather than silently dropped.
OUTCOME_MAP = {
    "P": "PASS", "PASS": "PASS", "PASSED": "PASS",
    "F": "FAIL", "FAIL": "FAIL", "FAILED": "FAIL",
    "PRS": "PRS",  # pass after rectification at station: a pass whose defects were real
    "ABA": "ABANDONED", "ABANDONED": "ABANDONED",
    "ABR": "ABORTED", "ABORTED": "ABORTED",
    "ABRVE": "ABORTED_VE",  # aborted by vehicle examiner
    "R": "REFUSED", "REFUSED": "REFUSED",
}

# Outcomes that complete a test cycle with a definitive result (cycles.py).
DEFINITIVE_OUTCOMES = {"PASS", "FAIL", "PRS"}


# ---------------------------------------------------------------------------
# Python twins (rule of record)
# ---------------------------------------------------------------------------

def clean_id(raw: Optional[str]) -> Optional[int]:
    """Parse a source ID defensively: tolerate a float-contaminated '.0'
    suffix, reject anything else non-integer loudly."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    if not (s.isdigit() or (s.startswith("-") and s[1:].isdigit())):
        raise ValueError(f"non-integer ID value {raw!r}")
    return int(s)


def build_model_id(make: Optional[str], model: Optional[str]) -> Optional[str]:
    """Canonical comparison key: 'MAKE MODEL', uppercased, whitespace
    collapsed. The aggregate artifact's model_id grain uses exactly this
    construction; pipeline/aggregates/match_rate_check.py is the gate that
    the regenerated vocabulary still matches read-side lookups."""
    make_s = " ".join((make or "").upper().split())
    model_s = " ".join((model or "").upper().split())
    if not make_s and not model_s:
        return None
    return f"{make_s} {model_s}".strip()


def sanitize_first_use(first_use: Optional[date], test_date: Optional[date]) -> Optional[date]:
    """Null out unusable first-use dates: sentinel pre-1900 values, and
    values after the test date (backfilled import records). The row keeps
    age_source='missing' so downstream code can tell 'unknown' from 'new'."""
    if first_use is None:
        return None
    if first_use.year < 1900:
        return None
    if test_date is not None and first_use > test_date:
        return None
    return first_use


def age_years(first_use: Optional[date], test_date: Optional[date]) -> Optional[float]:
    if first_use is None or test_date is None:
        return None
    return (test_date - first_use).days / 365.25


def canonical_outcome(raw: Optional[str]) -> str:
    if raw is None:
        return "UNKNOWN"
    return OUTCOME_MAP.get(str(raw).strip().upper(), "UNKNOWN")


def taxonomy_era(test_date: date) -> str:
    return "post_2018" if test_date >= TAXONOMY_SWITCH_DATE else "pre_2018"


# ---------------------------------------------------------------------------
# DuckDB expression builders (production path)
# ---------------------------------------------------------------------------

def clean_id_expr(col: str) -> str:
    """SQL twin of clean_id: strip a trailing '.0', cast BIGINT. A
    non-integer residue makes the CAST raise -- fail loud, never coerce."""
    return (
        f"CAST(regexp_replace(trim({col}), '\\.0$', '') AS BIGINT)"
    )


def model_id_expr(make_col: str = "make", model_col: str = "model") -> str:
    return (
        f"nullif(trim(regexp_replace("
        f"upper(coalesce({make_col}, '')) || ' ' || upper(coalesce({model_col}, '')),"
        f" '\\s+', ' ', 'g')), '')"
    )


def outcome_expr(col: str = "test_result") -> str:
    branches = " ".join(
        f"WHEN '{raw}' THEN '{canon}'" for raw, canon in OUTCOME_MAP.items()
    )
    return f"(CASE upper(trim({col})) {branches} ELSE 'UNKNOWN' END)"


def first_use_expr(first_use_col: str = "first_use_date", test_date_col: str = "test_date") -> str:
    """SQL twin of sanitize_first_use (columns already cast to DATE)."""
    return (
        f"(CASE WHEN {first_use_col} IS NULL THEN NULL"
        f" WHEN year({first_use_col}) < 1900 THEN NULL"
        f" WHEN {test_date_col} IS NOT NULL AND {first_use_col} > {test_date_col} THEN NULL"
        f" ELSE {first_use_col} END)"
    )


def taxonomy_era_expr(test_date_col: str = "test_date") -> str:
    return (
        f"(CASE WHEN {test_date_col} >= DATE '{TAXONOMY_SWITCH_DATE.isoformat()}'"
        f" THEN 'post_2018' ELSE 'pre_2018' END)"
    )
