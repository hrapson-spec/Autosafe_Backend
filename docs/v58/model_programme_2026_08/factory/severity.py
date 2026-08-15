"""F-22-corrected item disposition and severity -- the ONLY allowed rule.

The lake's stored `is_fail_item` / `is_advisory` / `is_dangerous` / `rfr_class`
columns are KNOWN-WRONG post-2018 (F-22: the stored table registers 'M' as
major and 'm' as minor, but the publication uses 'M' for MINOR and lowercase
'm' never occurs). The factory therefore NEVER reads those columns; it derives
disposition and severity here, from `rfr_type_code` + `dangerous_mark` + the
taxonomy era of the PARENT TEST DATE (never the code space -- DATA_ASSESSMENT
§5: post-2018-dated tests legitimately carry codes from both trees).

Contract table (FACTORY_CONTRACT.md, "Severity/disposition derivation"):

    disposition   A = advisory
                  M = minor           (post-2018 only)
                  F = fail-bearing, unrectified
                  P = fail-bearing, rectified at station
    case          post-2018 CASE-SENSITIVE; pre-2018 case-insensitive {F,P,A}
    severity      post-2018 only: dangerous <=> trim(dangerous_mark)='D'
                                  major     <=> disposition in {F,P} and not dangerous
                                  minor     <=> disposition = 'M'
                  pre-2018: UNOBSERVABLE -> status 'pre2018_ungraded', never zero
    fail-bearing  {F,P} in BOTH eras; counts emittable on both bases
                  (initial = F+P, final-unrectified = F)

Anything else RAISES. Lowercase 'm' raises (post-2018), 'M' pre-2018 raises,
'D' as an rfr_type_code raises: each would be a publication change that must be
classified deliberately, never coerced.
"""
from typing import Optional

PRE_2018 = "pre_2018"
POST_2018 = "post_2018"

# Taxonomy switch: EU roadworthiness directive, 20 May 2018 (mirrors
# pipeline.lake.normalize.TAXONOMY_SWITCH_DATE; asserted equal in tests).
TAXONOMY_SWITCH_ISO = "2018-05-20"

ADVISORY = "A"
MINOR = "M"
FAIL_UNRECTIFIED = "F"
FAIL_RECTIFIED = "P"

#: Dispositions that count as fail-bearing, both eras.
FAIL_BEARING = frozenset({FAIL_UNRECTIFIED, FAIL_RECTIFIED})
#: Basis names for the two emittable fail counts.
BASIS_INITIAL = "initial"      # F + P  (condition as presented)
BASIS_FINAL = "final"          # F only (unrectified at the end of the test)

POST_2018_DISPOSITIONS = frozenset({ADVISORY, MINOR, FAIL_UNRECTIFIED, FAIL_RECTIFIED})
PRE_2018_DISPOSITIONS = frozenset({ADVISORY, FAIL_UNRECTIFIED, FAIL_RECTIFIED})

SEVERITY_DANGEROUS = "dangerous"
SEVERITY_MAJOR = "major"
SEVERITY_MINOR = "minor"
SEVERITY_ADVISORY = "advisory"
#: Pre-2018 severity is not a zero -- it is unobservable.
SEVERITY_UNGRADED = "pre2018_ungraded"

DANGEROUS_MARK = "D"


class UnknownDispositionCode(ValueError):
    """An rfr_type_code outside the contract's era table reached the gate."""


class UnknownEra(ValueError):
    """A taxonomy era outside {pre_2018, post_2018}."""


# --- Rule of record ---------------------------------------------------------

def classify_disposition(rfr_type_code: Optional[str], era: str) -> str:
    """rfr_type_code -> disposition letter, era-aware. Raises on anything else.

    Post-2018 is case-sensitive by construction: 'm' is NOT minor here, it is
    an unregistered code, because the publication's minor code is 'M' (F-22).
    """
    if era not in (PRE_2018, POST_2018):
        raise UnknownEra(f"unknown taxonomy era {era!r}")
    code = (rfr_type_code or "").strip()
    if era == POST_2018:
        if code in POST_2018_DISPOSITIONS:
            return code
        raise UnknownDispositionCode(
            f"unregistered post-2018 rfr_type_code {rfr_type_code!r}; the "
            f"contract table is {sorted(POST_2018_DISPOSITIONS)} (case-sensitive). "
            f"Classify a new publication code deliberately -- never coerce."
        )
    upper = code.upper()
    if upper in PRE_2018_DISPOSITIONS:
        return upper
    raise UnknownDispositionCode(
        f"unregistered pre-2018 rfr_type_code {rfr_type_code!r}; the contract "
        f"table is {sorted(PRE_2018_DISPOSITIONS)} (case-insensitive)."
    )


def classify_severity(rfr_type_code: Optional[str], dangerous_mark: Optional[str],
                      era: str) -> str:
    """Full severity grade. Pre-2018 always returns SEVERITY_UNGRADED.

    DANGEROUS IS FAIL-GATED (repaired 2026-08-15). Under DVSA rules a dangerous defect
    is a failure: only a fail-bearing disposition can be graded dangerous. The previous
    order tested `dangerous_mark` BEFORE disposition, so a D-marked ADVISORY graded
    `dangerous` -- 73,814 such items exist post-2018 lake-wide, 0.248% of everything the
    old rule called dangerous. Those are anomalies in the publication, not dangerous
    failures; `is_anomalous_dangerous_mark` keeps them countable rather than silent.
    """
    disposition = classify_disposition(rfr_type_code, era)
    if era == PRE_2018:
        return SEVERITY_UNGRADED
    if disposition in FAIL_BEARING:
        if (dangerous_mark or "").strip() == DANGEROUS_MARK:
            return SEVERITY_DANGEROUS
        return SEVERITY_MAJOR
    if disposition == MINOR:
        return SEVERITY_MINOR
    return SEVERITY_ADVISORY


def is_anomalous_dangerous_mark(rfr_type_code: Optional[str],
                                dangerous_mark: Optional[str], era: str) -> bool:
    """TRUE for a D-marked item whose disposition is NOT fail-bearing.

    Additive companion to the fail-gating repair: the mark is real in the publication
    but cannot mean 'dangerous failure', so consumers should be able to count it rather
    than have it silently absorbed into `advisory`.
    """
    if era == PRE_2018:
        return False
    disposition = classify_disposition(rfr_type_code, era)
    return ((dangerous_mark or "").strip() == DANGEROUS_MARK
            and disposition not in FAIL_BEARING)


def is_fail_bearing(rfr_type_code: Optional[str], era: str, basis: str) -> bool:
    """Fail-bearing on the requested basis: 'initial' = F+P, 'final' = F."""
    disposition = classify_disposition(rfr_type_code, era)
    if basis == BASIS_INITIAL:
        return disposition in FAIL_BEARING
    if basis == BASIS_FINAL:
        return disposition == FAIL_UNRECTIFIED
    raise ValueError(f"unknown fail basis {basis!r}; use {BASIS_INITIAL!r}/{BASIS_FINAL!r}")


# --- SQL twins --------------------------------------------------------------

def era_expr(test_date_col: str) -> str:
    """Taxonomy era from the PARENT TEST DATE (never the code space)."""
    return (f"(CASE WHEN {test_date_col} >= DATE '{TAXONOMY_SWITCH_ISO}' "
            f"THEN '{POST_2018}' ELSE '{PRE_2018}' END)")


def disposition_expr(code_col: str, era_col_expr: str) -> str:
    """SQL twin of classify_disposition. Unregistered codes yield NULL; the
    prepare step counts NULLs and raises -- SQL cannot raise per-row, so the
    loudness lives in `unknown_disposition_expr` + the gate that reads it."""
    return (
        f"(CASE WHEN {era_col_expr} = '{POST_2018}' AND trim({code_col}) IN "
        f"({', '.join(repr(c) for c in sorted(POST_2018_DISPOSITIONS))}) "
        f"THEN trim({code_col}) "
        f"WHEN {era_col_expr} = '{PRE_2018}' AND upper(trim({code_col})) IN "
        f"({', '.join(repr(c) for c in sorted(PRE_2018_DISPOSITIONS))}) "
        f"THEN upper(trim({code_col})) "
        f"ELSE NULL END)"
    )


def unknown_disposition_expr(code_col: str, era_col_expr: str) -> str:
    """TRUE for rows whose rfr_type_code is outside the contract table."""
    return f"({disposition_expr(code_col, era_col_expr)} IS NULL)"


def severity_expr(code_col: str, mark_col: str, era_col_expr: str) -> str:
    """SQL twin of classify_severity."""
    disp = disposition_expr(code_col, era_col_expr)
    fail_set = ", ".join(repr(c) for c in sorted(FAIL_BEARING))
    return (
        f"(CASE WHEN {disp} IS NULL THEN NULL "
        f"WHEN {era_col_expr} = '{PRE_2018}' THEN '{SEVERITY_UNGRADED}' "
        f"WHEN {disp} IN ({fail_set}) AND trim(coalesce({mark_col}, '')) = "
        f"'{DANGEROUS_MARK}' THEN '{SEVERITY_DANGEROUS}' "
        f"WHEN {disp} IN ({fail_set}) THEN '{SEVERITY_MAJOR}' "
        f"WHEN {disp} = '{MINOR}' THEN '{SEVERITY_MINOR}' "
        f"ELSE '{SEVERITY_ADVISORY}' END)"
    )


def anomalous_dangerous_mark_expr(code_col: str, mark_col: str,
                                  era_col_expr: str) -> str:
    """SQL twin of is_anomalous_dangerous_mark."""
    disp = disposition_expr(code_col, era_col_expr)
    fail_set = ", ".join(repr(c) for c in sorted(FAIL_BEARING))
    return (f"({era_col_expr} = '{POST_2018}' AND {disp} IS NOT NULL "
            f"AND {disp} NOT IN ({fail_set}) "
            f"AND trim(coalesce({mark_col}, '')) = '{DANGEROUS_MARK}')")


def fail_bearing_expr(code_col: str, era_col_expr: str, basis: str) -> str:
    """SQL twin of is_fail_bearing."""
    disp = disposition_expr(code_col, era_col_expr)
    if basis == BASIS_INITIAL:
        codes = ", ".join(repr(c) for c in sorted(FAIL_BEARING))
        return f"({disp} IN ({codes}))"
    if basis == BASIS_FINAL:
        return f"({disp} = '{FAIL_UNRECTIFIED}')"
    raise ValueError(f"unknown fail basis {basis!r}")
