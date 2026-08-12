"""AutoSafe target population: DVSA initial tests, defined by DVSA's own fields.

This module is the single authority for which rows are prediction events. It
replaces the cycle reconstruction (`cycles.assign_cycles` / `is_cycle_first`)
in that role -- see decision D7 in docs/v58/DECISIONS.md.

WHY THE CHANGE. Cycles never defined the AutoSafe label; they only inferred
which rows counted as events. Every historical trainer labelled the selected
row directly from its own recorded outcome (build_v7_features.py:175,
train_catboost_streaming.py:67, train_catboost_production_v55.py:1487) and no
trainer has ever read `cycle_outcome`. DVSA records the event explicitly, so a
45-day temporal reconstruction is a lossy estimator of a field we already have.

DVSA SEMANTICS (MOT testing data user guide v5.1, section "Comparison with DVSA
published statistics", and lookup.zip/mdr_test_type.csv):

    Traditionally, Normal (Initial) tests with outcomes of Pass, Fail or PRS
    are used. All other tests are omitted.
    Initial failure rate = (Test Fail Results + the initial Test Fail result
                            from a PRS) / Total Tests
    Final failure rate   = Test Fail Results / Total Tests

The guide's own worked example is `WHERE TESTTYPE='NT' AND TESTRESULT IN
('P','F','PRS')`, which is exactly what this module encodes.

ERA STABILITY. The test-type and outcome vocabularies do NOT change at the
20 May 2018 EU-directive boundary. mdr_test_type.csv and mdr_test_outcome.csv
are single-version and era-blind, and normalize.OUTCOME_MAP has no era branch.
Verified empirically over all 640,091,459 lake rows (2026-08-12):

    pre_2018   NT 360,806,328   RT 95,765,612   ES 1,011   (no PL/PV/EI)
    post_2018  NT 150,996,706   RT 32,521,107   ES   360   EI 335

`PL`/`PV` (partial retests) never appear in either era: since the MOT testing
service "simplified how MOT retests are recorded", every retest presents as
`RT`. `EI` appears only in 2023. What DOES change at 20 May 2018 is the RfR
item taxonomy (rfr_mapping.RFR_TYPE_BY_ERA), which affects component features,
not population membership.

FAIL-CLOSED. `assert_known_test_types` rejects any value outside the DVSA
lookup vocabulary rather than silently dropping it. A new DVSA code must be
classified deliberately, because defaulting it either way is a silent change
to the training population.
"""
from typing import Iterable, Set

# --- DVSA vocabularies (lookup.zip, verbatim) --------------------------------

# mdr_test_type.csv. `NT` is the initial presentation; RT/PL/PV are retests;
# ES/EI are appeals against a previous result, not fresh presentations.
INITIAL_TEST_TYPE = "NT"
RETEST_TEST_TYPES: Set[str] = {"RT", "PL", "PV"}
APPEAL_TEST_TYPES: Set[str] = {"ES", "EI"}
KNOWN_TEST_TYPES: Set[str] = {INITIAL_TEST_TYPE} | RETEST_TEST_TYPES | APPEAL_TEST_TYPES

# Canonical outcomes (normalize.OUTCOME_MAP targets) that DVSA counts as a
# completed test with a result. Everything else -- ABANDONED, ABORTED,
# ABORTED_VE, REFUSED -- is omitted from the published population.
RESULT_OUTCOMES: Set[str] = {"PASS", "FAIL", "PRS"}
NON_RESULT_OUTCOMES: Set[str] = {"ABANDONED", "ABORTED", "ABORTED_VE", "REFUSED"}
KNOWN_OUTCOMES: Set[str] = RESULT_OUTCOMES | NON_RESULT_OUTCOMES


class UnknownTestTypeError(ValueError):
    """A test_type outside the DVSA lookup vocabulary reached the gate."""


class UnknownOutcomeError(ValueError):
    """An outcome outside the canonical vocabulary reached the gate."""


# --- Rule of record ----------------------------------------------------------

def is_initial_test(test_type: str, outcome: str) -> bool:
    """True iff this row is a DVSA initial test with a recorded result.

    Rule of record; `initial_test_sql` is the asserted-equivalent SQL twin.
    Raises rather than returning False on an unrecognised code -- a silent
    False would quietly shrink the training population.
    """
    if test_type not in KNOWN_TEST_TYPES:
        raise UnknownTestTypeError(
            f"test_type {test_type!r} is not in the DVSA lookup vocabulary "
            f"{sorted(KNOWN_TEST_TYPES)}; classify it deliberately"
        )
    if outcome not in KNOWN_OUTCOMES:
        raise UnknownOutcomeError(
            f"outcome {outcome!r} is not canonical {sorted(KNOWN_OUTCOMES)}"
        )
    return test_type == INITIAL_TEST_TYPE and outcome in RESULT_OUTCOMES


def is_initial_failure(test_type: str, outcome: str) -> bool:
    """DVSA *initial* failure: the vehicle's condition as presented (F or PRS).

    NOT the AutoSafe training label -- see `is_final_failure`. Provided so the
    published-statistics gate can reproduce DVSA's initial fail rate.
    """
    return is_initial_test(test_type, outcome) and outcome in ("FAIL", "PRS")


def is_final_failure(test_type: str, outcome: str) -> bool:
    """DVSA *final* failure: FAIL only, PRS counts as the pass it was recorded as.

    THIS is the historical AutoSafe label semantics and is preserved unchanged
    by the D7 replacement. Whether AutoSafe should instead predict FAIL+PRS is
    an open decision (docs/v58/DECISIONS.md, D12); adopting it would flip
    ~7.22% of 2019 labels and break comparability with every historical AUC.
    """
    return is_initial_test(test_type, outcome) and outcome == "FAIL"


# --- SQL twins ---------------------------------------------------------------

def _quote(values: Iterable[str]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


def initial_test_sql(alias: str = "r") -> str:
    """SQL twin of `is_initial_test` over a results relation."""
    return (f"({alias}.test_type = '{INITIAL_TEST_TYPE}' "
            f"AND {alias}.outcome IN ({_quote(RESULT_OUTCOMES)}))")


def final_failure_sql(alias: str = "r") -> str:
    """SQL twin of `is_final_failure` (the preserved AutoSafe label)."""
    return f"({alias}.outcome = 'FAIL')"


def initial_failure_sql(alias: str = "r") -> str:
    """SQL twin of `is_initial_failure` (DVSA initial basis; gate only)."""
    return f"({alias}.outcome IN ('FAIL', 'PRS'))"


def unknown_vocabulary_sql(alias: str = "r") -> str:
    """Rows whose test_type or outcome is outside the known vocabularies.

    The fail-closed gate counts these; a non-zero count must halt the build.
    """
    return (f"({alias}.test_type IS NULL "
            f"OR {alias}.test_type NOT IN ({_quote(KNOWN_TEST_TYPES)}) "
            f"OR {alias}.outcome IS NULL "
            f"OR {alias}.outcome NOT IN ({_quote(KNOWN_OUTCOMES)}))")


def assert_known_test_types(observed: Iterable[str]) -> None:
    """Fail closed on any test_type outside the DVSA lookup vocabulary."""
    unknown = sorted(set(observed) - KNOWN_TEST_TYPES)
    if unknown:
        raise UnknownTestTypeError(
            f"unclassified DVSA test_type(s) {unknown}: refusing to build a "
            f"target population. Add them to RETEST_TEST_TYPES / "
            f"APPEAL_TEST_TYPES / INITIAL_TEST_TYPE deliberately."
        )


def assert_known_outcomes(observed: Iterable[str]) -> None:
    """Fail closed on any outcome outside the canonical vocabulary."""
    unknown = sorted(set(observed) - KNOWN_OUTCOMES)
    if unknown:
        raise UnknownOutcomeError(
            f"unclassified outcome(s) {unknown}: refusing to build a target "
            f"population (normalize.OUTCOME_MAP maps unmapped codes to "
            f"'UNKNOWN', which must never enter the population)."
        )
