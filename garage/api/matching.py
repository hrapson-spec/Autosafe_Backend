"""Outcome matching: which DVSA test (if any) corresponds to a booking.

The MOT API has no test-type field (T1 FIELD_MAP.md Q3), so retests cannot be told from fresh tests.
Matching therefore uses only dates and results and refuses to guess:

- candidates = PASSED/FAILED tests dated from the current appointment date to appointment + 14 days
- none -> no_subsequent_test; exactly one -> matched; more than one -> ambiguous (left unresolved)
- tests before the appointment date are never candidates (a rescheduled booking is matched against its
  current date only)
"""
from __future__ import annotations

from datetime import date, timedelta

RETEST_LOOKBACK_DAYS = 60   # R-RT proposal in T1 (W=60); informational only here


def match_outcome(tests, appointment_date: date, booking_type: str, window_days: int = 14):
    end = appointment_date + timedelta(days=window_days)
    cands = sorted((t for t in tests if appointment_date <= t.test_date <= end), key=lambda t: t.completed_at)
    ref = [{"test_date": t.test_date.isoformat(), "result": t.result, "test_number": t.test_number} for t in cands]
    detail = None
    if booking_type == "retest":
        lo = appointment_date - timedelta(days=RETEST_LOOKBACK_DAYS)
        if not any(t.result == "FAILED" and lo <= t.test_date < appointment_date for t in tests):
            detail = "no_preceding_fail_found"
    if not cands:
        return "no_subsequent_test", None, ref, detail
    if len(cands) == 1:
        return "matched", ref[0], ref, detail
    pattern = "fail_then_later_pass" if cands[0].result == "FAILED" and any(
        c.result == "PASSED" for c in cands[1:]) else "multiple_candidates"
    return "ambiguous", None, ref, pattern if detail is None else f"{pattern};{detail}"
