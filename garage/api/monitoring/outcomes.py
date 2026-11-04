"""Pure helpers that turn existing app rows into evaluator inputs. No database, no network.

PROPOSALS pending an owner decision (see REPORT.md, open decisions): the F-or-PRS endpoint definition and
which outcome row is "settled". They are implemented so the wiring can be tested, not so they become spec.

Why this exists. `matching.match_outcome` (garage/api/matching.py:30-37) labels a booking 'ambiguous' when more
than one DVSA test falls in the window, and a PRS ("rectified at the test") appears as a FAILED test followed
by a PASSED test about a second later (garage/api/briefing.py:19-20). So the F-or-PRS positives are
systematically status 'ambiguous', not 'matched'. Evaluating G7 on status == 'matched' only would drop them and
bias the observed rate down. The helper below reads the ordered `candidates` list instead.

SUPERSEDED for G7 / band-order wiring (2026-11-03): `classify.classify_booking_outcome` is the PRS-aware
classifier with an explicit endpoint and PRS pairing rule; `f_or_prs_from_candidates` cannot pair by time (the
stored candidates carry only the test DATE) and is kept only for its existing tests.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Mapping, Optional, Sequence, Tuple

from ._stats import MonitoringInputError

SETTLED_CHECK_DAY = 14       # garage/api/config.py:26 outcome_check_days = (1, 3, 7, 14); outcome_window_days = 14 at :35


def f_or_prs_from_candidates(candidates: Optional[Sequence[Mapping[str, Any]]]) -> Optional[int]:
    """1 if the first in-window test (ordered by completed_at, as stored) was FAILED (incl. a PRS fail->pass pair),
    0 if it was PASSED, None if there is no candidate. PROPOSED definition."""
    if not candidates:
        return None
    first = candidates[0].get("result")
    if first == "FAILED":
        return 1
    if first == "PASSED":
        return 0
    return None


def settled_outcome(rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """The check_day == 14 row if its status is matched or ambiguous (candidates exist), else None.
    Earlier checks (1/3/7) can precede the test and are not settled. PROPOSED."""
    for r in rows:
        if r.get("check_day") == SETTLED_CHECK_DAY and r.get("status") in ("matched", "ambiguous"):
            return r
    return None


def band_for_probability(p: float, band_edges: Tuple[int, int] = (20, 35), rounding_pp: int = 5) -> str:
    """Band from the ROUNDED percentage (rev3 section 6, AMENDMENT_DRAFT.md:55): q = 5*round(100p/5) with ties to
    even, low q<20, medium 20<=q<35, high q>=35. Uses the shortest-decimal value of p so that .175 is a tie.
    The edges/rounding mirror DisplayPolicy (garage/api/provider.py:36-37) without importing it."""
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not (0.0 <= p <= 1.0):
        raise MonitoringInputError("p must be a probability in [0, 1]")
    q = int((Decimal(repr(float(p))) * 100 / rounding_pp).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)) * rounding_pp
    return "low" if q < band_edges[0] else "medium" if q < band_edges[1] else "high"
