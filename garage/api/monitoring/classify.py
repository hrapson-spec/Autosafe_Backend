"""PRS-aware outcome classifier for monitoring (G7 and the band-order check).

Why a separate classifier. `matching.match_outcome` (garage/api/matching.py:30-37) labels a booking 'ambiguous'
when more than one DVSA test falls in the window, and a PRS ("rectified at the test") appears as a FAILED test
followed about a second later by a PASSED test (garage/api/briefing.py:19-20; PRS_PAIR_MAX_GAP_SECONDS =
briefing.py:39, observed 1 s in 21/21 pairs per that comment). A G7 fed only status 'matched' therefore drops
most F-or-PRS positives. matching.py and briefing.py are the product display and are NOT changed; this module
re-reads the stored vehicle history (source_snapshots.history, tests with completed_at/result/defects) and
classifies it.

What the written protocol fixes (and what it does not)
  FIXED     "Endpoint is F-or-PRS, fixing hard-fail-only wording in legal drafts"
            (protocol/AMENDMENT_DRAFT.md:57; PLAN_rev3_20261026.md:96 "classify the F-or-PRS endpoint
            consistently"). The endpoint therefore is `fail_or_prs`; `fail_only` is supported for the
            comparison/legacy reading and must be asked for by name.
  NOT FIXED no document read gives the PRS pairing rule (time window, defect composition, same-vehicle and
            same-station treatment) or what to do with several distinct tests in the window. They are REQUIRED
            arguments with no defaults (`PrsPairingRule`, `OutcomePolicy`) and are listed as open decisions
            in execution/tickets/APP_COMPLETION_V1/REPORT_T1a.md. The numbers in briefing.py are product
            display constants, quoted as evidence, not adopted.

Pure and stdlib-only (plus garage.api.timeutil for the London calendar day, the same day bucketing the matcher
uses). No database, no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..timeutil import london_date
from ._stats import MonitoringInputError

ENDPOINTS = ("fail_only", "fail_or_prs")
MULTI_EVENT_RULES = ("exclude_unresolved", "first_event")
DEFECT_RULES = ("prs_only_fail_required", "ignore_defects")
IDENTITY_RULES = ("same_required", "ignore")
RESULTS = ("PASSED", "FAILED")
PRS_TYPE = "PRS"                 # DVSA defect type literal (briefing.py:179,198)

LABELS = ("no_test", "pass", "fail", "prs_pair", "ambiguous_non_prs")


@dataclass(frozen=True)
class PrsPairingRule:
    """When is a FAILED test followed by a PASSED test ONE 'pass after rectification at station' event.
    Every field is required; nothing is defaulted."""
    min_gap_seconds: float            # smallest fail->pass gap accepted as a pair
    max_gap_seconds: float            # largest fail->pass gap accepted as a pair (inclusive)
    defect_rule: str                  # prs_only_fail_required | ignore_defects
    hard_defect_types: Tuple[str, ...]   # defect types that stop a FAILED test being a PRS-only fail
    vehicle_rule: str                 # same_required | ignore
    station_rule: str                 # same_required | ignore

    def validate(self) -> None:
        for name in ("min_gap_seconds", "max_gap_seconds"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0:
                raise MonitoringInputError(f"{name} must be a number >= 0")
        if self.max_gap_seconds < self.min_gap_seconds:
            raise MonitoringInputError("max_gap_seconds must be >= min_gap_seconds")
        if self.defect_rule not in DEFECT_RULES:
            raise MonitoringInputError(f"defect_rule must be one of {DEFECT_RULES}")
        if not isinstance(self.hard_defect_types, tuple) or not all(isinstance(x, str) and x for x in self.hard_defect_types):
            raise MonitoringInputError("hard_defect_types must be a tuple of non-empty strings")
        if self.defect_rule == "prs_only_fail_required" and not self.hard_defect_types:
            raise MonitoringInputError("defect_rule prs_only_fail_required needs a non-empty hard_defect_types")
        for name in ("vehicle_rule", "station_rule"):
            if getattr(self, name) not in IDENTITY_RULES:
                raise MonitoringInputError(f"{name} must be one of {IDENTITY_RULES}")

    def as_dict(self) -> Dict[str, Any]:
        return {"min_gap_seconds": self.min_gap_seconds, "max_gap_seconds": self.max_gap_seconds,
                "defect_rule": self.defect_rule, "hard_defect_types": list(self.hard_defect_types),
                "vehicle_rule": self.vehicle_rule, "station_rule": self.station_rule}


@dataclass(frozen=True)
class OutcomePolicy:
    endpoint: str                     # fail_only | fail_or_prs
    window_days: int                  # tests dated appointment .. appointment + window_days (London date), inclusive
    multi_event_rule: str             # exclude_unresolved | first_event
    prs_rule: PrsPairingRule

    def validate(self) -> None:
        if self.endpoint not in ENDPOINTS:
            raise MonitoringInputError(f"endpoint must be one of {ENDPOINTS}")
        if not isinstance(self.window_days, int) or isinstance(self.window_days, bool) or self.window_days < 0:
            raise MonitoringInputError("window_days must be an integer >= 0")
        if self.multi_event_rule not in MULTI_EVENT_RULES:
            raise MonitoringInputError(f"multi_event_rule must be one of {MULTI_EVENT_RULES}")
        if not isinstance(self.prs_rule, PrsPairingRule):
            raise MonitoringInputError("prs_rule must be a PrsPairingRule")
        self.prs_rule.validate()

    def as_dict(self) -> Dict[str, Any]:
        return {"endpoint": self.endpoint, "window_days": self.window_days,
                "multi_event_rule": self.multi_event_rule, "prs_pairing": self.prs_rule.as_dict()}


@dataclass(frozen=True)
class OutcomeClass:
    label: str                        # one of LABELS
    y: Optional[int]                  # 1 / 0 for the chosen endpoint, None = unresolved or no test (excluded)
    n_tests_in_window: int            # distinct DVSA tests dated inside the window (the matcher's candidate count)
    n_events: int                     # events in the window after merging PRS pairs
    pair_gap_seconds: Optional[float]


def _to_dt(v: Any, what: str) -> datetime:
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as e:
            raise MonitoringInputError(f"{what} is not ISO-8601: {v!r}") from e
    else:
        raise MonitoringInputError(f"{what} must be a datetime or ISO-8601 string")
    if d.tzinfo is None:
        raise MonitoringInputError(f"{what} must carry a timezone offset")
    return d.astimezone(timezone.utc)


def _to_date(v: Any, what: str) -> date:
    if isinstance(v, datetime):
        raise MonitoringInputError(f"{what} must be a date, not a datetime")
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError as e:
            raise MonitoringInputError(f"{what} is not an ISO date: {v!r}") from e
    raise MonitoringInputError(f"{what} must be a date or ISO date string")


def _prepare(tests: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, t in enumerate(tests):
        res = t.get("result")
        if res not in RESULTS:
            raise MonitoringInputError(f"test {i}: result must be one of {RESULTS}, got {res!r}")
        defects = t.get("defects") or []
        out.append({"at": _to_dt(t.get("completed_at"), f"test {i}.completed_at"), "result": res,
                    "types": [d.get("type") for d in defects if isinstance(d, Mapping)],
                    "number": t.get("test_number") or "", "vehicle_id": t.get("vehicle_id"),
                    "station_id": t.get("station_id")})
    # Same order as briefing.build_events: at the same instant the fail precedes the pass, then test number.
    out.sort(key=lambda d: (d["at"], d["result"] == "PASSED", d["number"]))
    return out


def _is_pair(f: Mapping[str, Any], p: Mapping[str, Any], rule: PrsPairingRule) -> Optional[float]:
    """The fail->pass gap in seconds if (f, p) is a PRS pair under `rule`, else None."""
    if f["result"] != "FAILED" or p["result"] != "PASSED":
        return None
    gap = (p["at"] - f["at"]).total_seconds()
    if not (rule.min_gap_seconds <= gap <= rule.max_gap_seconds):
        return None
    if rule.defect_rule == "prs_only_fail_required":
        if PRS_TYPE not in f["types"] or any(x in rule.hard_defect_types for x in f["types"]):
            return None
    for rname, key in (("vehicle_rule", "vehicle_id"), ("station_rule", "station_id")):
        if getattr(rule, rname) == "same_required":
            a, b = f[key], p[key]
            if a is None or b is None:
                raise MonitoringInputError(f"{rname} is same_required but a test carries no {key} (fail closed)")
            if a != b:
                return None
    return gap


def classify_booking_outcome(tests: Sequence[Mapping[str, Any]], appointment_date: Any,
                             policy: OutcomePolicy) -> OutcomeClass:
    """Classify one booking from the vehicle's full test history (all dates; pairing needs the neighbours).

    tests: [{completed_at (aware), result PASSED|FAILED, defects?[{type}], test_number?, vehicle_id?, station_id?}].
    Steps: sort; merge PRS pairs into one event anchored at the FAILED test; keep events whose anchor London date
    is in [appointment_date, appointment_date + window_days] (the matcher's window, matching.py:23-24);
      * no event                          -> no_test            y None
      * exactly one event (or `first_event`) -> its kind decides:
          PASSED -> pass, y 0;  FAILED -> fail, y 1;
          PRS pair -> prs_pair, y 1 for fail_or_prs and y 0 for fail_only
      * several events, `exclude_unresolved` -> ambiguous_non_prs, y None (counted, not guessed)
    """
    policy.validate()
    appt = _to_date(appointment_date, "appointment_date")
    last = appt + timedelta(days=policy.window_days)
    ts = _prepare(tests)
    events: List[Tuple[str, date, Optional[float], int]] = []     # kind, anchor date, gap, n tests
    i = 0
    while i < len(ts):
        t = ts[i]
        gap = _is_pair(t, ts[i + 1], policy.prs_rule) if i + 1 < len(ts) else None
        if gap is not None:
            events.append(("prs_pair", london_date(t["at"]), gap, 2))
            i += 2
        else:
            events.append(("pass" if t["result"] == "PASSED" else "fail", london_date(t["at"]), None, 1))
            i += 1
    inwin = [e for e in events if appt <= e[1] <= last]
    n_tests = sum(e[3] for e in inwin)
    if not inwin:
        return OutcomeClass("no_test", None, 0, 0, None)
    if len(inwin) > 1 and policy.multi_event_rule == "exclude_unresolved":
        return OutcomeClass("ambiguous_non_prs", None, n_tests, len(inwin), None)
    kind, _, gap, _ = inwin[0]
    if kind == "pass":
        y = 0
    elif kind == "fail":
        y = 1
    else:
        y = 1 if policy.endpoint == "fail_or_prs" else 0
    return OutcomeClass(kind, y, n_tests, len(inwin), gap)
