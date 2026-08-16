"""Canonical D13 day-state contract.

This module is the ONLY place the factory resolves "what happened on this
calendar day". It exists because `cycles` was decommissioned as the population
authority (decision D7: cycles were only ever an estimator of `test_type='NT'`,
their 45-day gap has no derivation, and the lake's `cycles/` directory is
empty), yet its *set-based day-cluster semantics* remain correct and are still
the thing every history feature needs.

B7 depends on this module, never on `pipeline.lake.cycles`. `cycles` may call
these helpers if legacy code still needs them. This supersedes owner ruling 3
of 2026-08-12, which accepted the private `cycles._cluster_outcome` import
as-is with a public alias as the tidier long-term shape.

THE FIVE-STATE CONTRACT
-----------------------
Every prior test-day carries exactly one state::

    S_d in { PASS, PRS, FAIL, AMBIGUOUS, UNAVAILABLE }

- PASS / PRS / FAIL  -- a definitive outcome was recorded AND is identified.
- AMBIGUOUS          -- a definitive outcome exists but WHICH one is not
                        identified (same-stratum FAIL + pass; within-day order
                        is unknowable, measured at 49.91% = chance).
- UNAVAILABLE        -- no definitive outcome at all (abandoned/aborted/
                        refused), or a mix of non-definitive outcomes.

The counting rule that follows from it, and the reason this module exists::

    k = sum( S_d == FAIL )
    n = sum( S_d in {PASS, PRS, FAIL} )

AMBIGUOUS and UNAVAILABLE days are excluded from BOTH numerator and
denominator. Excluding them from the numerator alone would silently assert
"this day was not a failure", which is the unavailable-to-zero conflation the
lake was repaired to remove (DQ-01). Their exposure is emitted separately so
the exclusion is visible rather than hidden.
"""
from typing import Dict, Iterable, List, Optional

# The set-based cluster resolver is imported for its SEMANTICS only; no cycle
# substrate, cycle build, or gap constant is involved. If `cycles` is ever
# deleted outright, inline `_cluster_outcome` here -- it is 20 lines and this
# module is already its contractual owner.
from pipeline.lake.cycles import AMBIGUOUS as _CLUSTER_AMBIGUOUS
from pipeline.lake.cycles import DEFINITIVE as _DEFINITIVE
from pipeline.lake.cycles import _cluster_outcome as _cluster

PASS = "PASS"
PRS = "PRS"
FAIL = "FAIL"
AMBIGUOUS = "AMBIGUOUS"
UNAVAILABLE = "UNAVAILABLE"
NO_HISTORY = "NO_HISTORY"

#: The three states that carry identified outcome information. This frozenset
#: IS the denominator of every Lane D proportion.
OUTCOME_OBSERVABLE = frozenset({PASS, PRS, FAIL})
DAY_STATES = (PASS, PRS, FAIL, AMBIGUOUS, UNAVAILABLE)

DEFINITIVE = _DEFINITIVE
#: The RAW set-based resolver and its ambiguity token, re-exported so the rest
#: of the factory imports them from here rather than reaching into `cycles`
#: directly. B5's non-definitive SUPERSET still needs the raw vocabulary (it
#: deliberately counts mixed non-definitive days as ambiguous); B7 must use
#: `day_state` instead, which splits that case out.
cluster_outcome = _cluster
CLUSTER_AMBIGUOUS = _CLUSTER_AMBIGUOUS


def day_state(tests: Iterable[dict]) -> str:
    """Resolve one (vehicle, date) multiset to exactly one of DAY_STATES.

    `tests` are dicts carrying at least `ttype` and `outcome` (the factory's
    packet payload shape). Order-free by construction: nothing here reads
    `test_id` or list position, which is what makes falsifier F14 hold.
    """
    rows = [{"test_type": t.get("ttype"), "outcome": t.get("outcome")}
            for t in tests]
    if not rows:
        return UNAVAILABLE
    resolved = _cluster(rows)
    if resolved in OUTCOME_OBSERVABLE:
        return resolved
    if resolved == _CLUSTER_AMBIGUOUS:
        # `_cluster_outcome` returns AMBIGUOUS for TWO different situations:
        #   (a) same-stratum FAIL + definitive pass -> a real outcome exists
        #       but is unidentified                        -> AMBIGUOUS
        #   (b) a mix of non-definitive outcomes (e.g. ABANDONED + ABORTED)
        #       -> no outcome was ever recorded            -> UNAVAILABLE
        # Collapsing them would put "no test result" into the ambiguity
        # bucket and misstate the exposure denominators.
        return AMBIGUOUS if _has_definitive(rows) else UNAVAILABLE
    # A single non-definitive outcome (ABANDONED / ABORTED / ABORTED_VE /
    # REFUSED) is returned verbatim by the resolver.
    return UNAVAILABLE


def _has_definitive(rows: List[dict]) -> bool:
    return any(r.get("outcome") in DEFINITIVE for r in rows)


def is_outcome_observable(state: str) -> bool:
    """True iff this day belongs in a Lane D proportion's denominator."""
    return state in OUTCOME_OBSERVABLE


def empty_state_counts() -> Dict[str, int]:
    return {s: 0 for s in DAY_STATES}


def outcome_observable_total(counts: Dict[str, int]) -> int:
    """n = the honest denominator, from a per-state counter dict."""
    return sum(counts[s] for s in OUTCOME_OBSERVABLE)


def severity_ordinal(n_dangerous: Optional[int], n_major: Optional[int],
                     n_minor: Optional[int], n_advisory: Optional[int]) -> Optional[int]:
    """Max severity rung present, as an ordinal. NULL when unobservable.

    0 clean / 1 advisory / 2 minor / 3 major / 4 dangerous. Returns None when
    every input is None (severity unobservable), never 0 -- a 0 would assert
    "observed, and clean", which pre-2018 history cannot support.
    """
    if all(v is None for v in (n_dangerous, n_major, n_minor, n_advisory)):
        return None
    if (n_dangerous or 0) > 0:
        return 4
    if (n_major or 0) > 0:
        return 3
    if (n_minor or 0) > 0:
        return 2
    if (n_advisory or 0) > 0:
        return 1
    return 0
