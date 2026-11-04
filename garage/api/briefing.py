"""Briefing engine: deterministic rules over a stored source snapshot (no model score, no LLM; R8).

Inputs
- the stored snapshot (normalised DVSA history, `DvsaHistory.to_json()`), and
- the assessment's input cutoff (min(import time, appointment date), R10).
Tests at or after the cutoff are dropped BEFORE any rule runs, so a briefing never shows history the
assessment itself was not allowed to see.

Output: dated source records, explicit evidence gaps, rule-generated follow-up questions and a template
summary. Nothing here reads, mentions or explains an assessment result.

Versioning: a briefing is identified by (snapshot sha256, RULE_TABLE_VERSION). Changing any rule, template,
threshold or constant below REQUIRES a new RULE_TABLE_VERSION (a test pins the table digest).

Wording rule (tested): historical defects are always described in the past tense with their test date.
Defect text from DVSA is shown verbatim inside quotation marks, as a quotation of what was recorded; it is
never used as a sentence of our own.

PRS (T1b, PRS_FINDINGS.md): a FAILED test whose defects include PRS-type rows and none of type
MAJOR/DANGEROUS/FAIL, followed by a PASSED test at most PRS_PAIR_MAX_GAP_SECONDS later, is ONE event,
"rectified at the test". Observed: 21 of 21 such pairs are exactly 1 s apart; every other same-day
fail-then-pass gap is at least 35 s.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .dvsa import DvsaHistory, DvsaTest
from .timeutil import london_date

RULE_TABLE_VERSION = "briefing-rules-1"

RECORD_LIMIT = 5                 # last N events shown and used by the rules
STALE_MONTHS = 15                # "no test in the last X months" (an MOT is annual; 3 months of slack)
PRS_PAIR_MAX_GAP_SECONDS = 2     # observed 1.0 s in 21/21 (T1b); next-smallest fail->pass gap is 35 s
FAIL_THEN_PASS_WINDOW_DAYS = 60  # R-RT window (T1 FIELD_MAP.md:19)
MAX_ADVISORY_QUESTIONS = 3       # per advisory rule
MAX_ODOMETER_DROP_GAPS = 2
KM_PER_MILE = 1.609344

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


# ------------------------------------------------------------------ rule table
@dataclass(frozen=True)
class Rule:
    id: str
    kind: str          # gap | question | summary
    condition: str     # human-readable trigger (documentation; the code below implements it)
    text: str          # template; {placeholders} are filled by `render`


RULES: tuple[Rule, ...] = (
    # ---- evidence gaps
    Rule("G01_vehicle_not_found", "gap",
         "The history lookup found no vehicle for the registration.",
         "No MOT history was returned for this registration."),
    Rule("G02_no_prior_test", "gap",
         "A vehicle was found but no test is dated before the cutoff.",
         "No MOT test was recorded before {cutoff}; no dated records could be shown."),
    Rule("G03_short_history", "gap",
         "Exactly one test event is dated before the cutoff.",
         "Only 1 MOT test was recorded before {cutoff}; changes between tests cannot be shown."),
    Rule("G04_stale_history", "gap",
         f"The latest test event is more than {STALE_MONTHS} months before the cutoff.",
         "The most recent MOT test recorded before {cutoff} was on {date}, more than {months} months earlier."),
    Rule("G05_missing_odometer", "gap",
         f"A test among the last {RECORD_LIMIT} events has no odometer reading.",
         "No odometer reading was recorded at the test on {dates}."),
    Rule("G06_odometer_drop", "gap",
         f"Among the last {RECORD_LIMIT} events, a reading is lower than the previous reading.",
         "The odometer reading recorded at the test on {date_after} ({after}) was lower than the reading "
         "recorded at the test on {date_before} ({before})."),
    Rule("G07_recent_fail_no_pass", "gap",
         "The latest test event is a fail and no pass is dated after it.",
         "The test on {date} was recorded as a fail and no later pass was recorded before {cutoff}."),
    Rule("G08_prs_without_pass", "gap",
         "A failed test recorded repaired-on-the-day items but no pass follows within "
         f"{PRS_PAIR_MAX_GAP_SECONDS} seconds.",
         "The test on {date} recorded items as repaired on the day, but no immediate pass was recorded after "
         "it; it was treated as a fail."),
    # ---- follow-up questions, in priority order
    Rule("Q01_unresolved_fail", "question",
         "The latest test event is a fail and no pass is dated after it.",
         "The test on {date} was recorded as a fail and no later pass was recorded. Ask what has been repaired "
         "since and whether a retest took place."),
    Rule("Q02_fail_then_pass", "question",
         f"The latest event is a pass and the event before it is a fail at most {FAIL_THEN_PASS_WINDOW_DAYS} "
         "days earlier.",
         "The test on {fail_date} was recorded as a fail with {n} major or dangerous {items}, and a pass was "
         "recorded on {pass_date}. Ask what repairs were made in between."),
    Rule("Q03_rectified_at_last_test", "question",
         "The latest test event is a fail that was rectified at the test (PRS pair).",
         "The test on {date} recorded {n} {items} as rectified at the test. Ask what was repaired and who "
         "did the work."),
    Rule("Q04_repeat_advisory", "question",
         f"The same advisory text was recorded at two or more of the last {RECORD_LIMIT} test events.",
         "Ask whether the “{item}” advisory from {date} has been addressed. It was recorded at {n} "
         "tests ({dates})."),
    Rule("Q05_last_test_advisory", "question",
         "An advisory was recorded at the latest test event and is not covered by Q04.",
         "Ask whether the “{item}” advisory from {date} has been addressed."),
    # ---- summary
    Rule("S01_summary", "summary",
         "At least one test event is dated before the cutoff.",
         "Recorded before {cutoff}: {n} MOT test {results_word}. The most recent, on {date}, was {result}"
         "{odometer}. Listed below: {gaps} and {questions}, generated by "
         "fixed rules over the recorded history."),
    Rule("S02_summary_empty", "summary",
         "No test event is dated before the cutoff.",
         "No dated MOT records were available for this booking. Listed below: {gaps}."),
)
RULE_BY_ID = {r.id: r for r in RULES}
assert len(RULE_BY_ID) == len(RULES)
GAP_RULE_IDS = tuple(r.id for r in RULES if r.kind == "gap")
QUESTION_RULE_IDS = tuple(r.id for r in RULES if r.kind == "question")
RULE_IDS = tuple(r.id for r in RULES)


def rule_table_digest() -> str:
    """Digest of the whole table plus the numeric constants. Pinned by a test next to RULE_TABLE_VERSION."""
    payload = {"rules": [[r.id, r.kind, r.condition, r.text] for r in RULES],
               "constants": [RECORD_LIMIT, STALE_MONTHS, PRS_PAIR_MAX_GAP_SECONDS, FAIL_THEN_PASS_WINDOW_DAYS,
                             MAX_ADVISORY_QUESTIONS, MAX_ODOMETER_DROP_GAPS]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def render(rule_id: str, **kw: Any) -> str:
    return RULE_BY_ID[rule_id].text.format(**kw)


# ------------------------------------------------------------------ small helpers
def fmt_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _join(items: list[str]) -> str:
    return ", ".join(items)


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _count(n: int, one: str, many: str) -> str:
    return f"no {many}" if n == 0 else f"{n} {one if n == 1 else many}"


def add_months(d: date, months: int) -> date:
    y, m0 = divmod(d.year * 12 + d.month - 1 + months, 12)
    m = m0 + 1
    last = 31 if m == 12 else (date(y, m + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _norm_item(text: str) -> str:
    return " ".join((text or "").lower().split())


def _clean_item(text: str) -> str:
    return " ".join((text or "").split())


def _odo_text(value: int, unit: str | None) -> str:
    return f"{value:,} {'km' if (unit or '').upper() == 'KM' else 'miles'}"


def _miles(value: int, unit: str | None) -> float:
    return value / KM_PER_MILE if (unit or "").upper() == "KM" else float(value)


TYPE_LABEL = {
    "ADVISORY": "Advisory", "MINOR": "Minor defect", "MAJOR": "Major defect", "DANGEROUS": "Dangerous defect",
    "PRS": "Defect rectified at the test", "FAIL": "Failure item",
}
HARD_TYPES = ("MAJOR", "DANGEROUS", "FAIL")


# ------------------------------------------------------------------ events
@dataclass
class Event:
    kind: str                       # PASSED | FAILED | RECTIFIED
    at: datetime
    day: date
    odometer_value: int | None
    odometer_unit: str | None
    defects: list[dict]             # {type, text, dangerous}
    unpaired_prs: bool = False      # FAILED with PRS-only defects and no immediate pass


def _is_prs_only_fail(t: DvsaTest) -> bool:
    types = [d.get("type") for d in t.defects]
    return t.result == "FAILED" and "PRS" in types and not any(x in HARD_TYPES for x in types)


def build_events(tests: tuple[DvsaTest, ...]) -> list[Event]:
    """Chronological events; PRS fail+pass pairs are merged into one RECTIFIED event."""
    # same instant: the fail before the pass; then test number, so the order never depends on input order
    ts = sorted(tests, key=lambda t: (t.completed_at, t.result == "PASSED", t.test_number or ""))
    out: list[Event] = []
    i = 0
    while i < len(ts):
        t = ts[i]
        nxt = ts[i + 1] if i + 1 < len(ts) else None
        if _is_prs_only_fail(t):
            gap = (nxt.completed_at - t.completed_at).total_seconds() if nxt else None
            if nxt is not None and nxt.result == "PASSED" and 0 <= gap <= PRS_PAIR_MAX_GAP_SECONDS:
                seen, defects = set(), []
                for d in list(t.defects) + list(nxt.defects):
                    # the pass copies the fail's advisory/minor rows: keep each (type, text) once
                    k = (d.get("type"), _norm_item(d.get("text") or ""))
                    if k not in seen:
                        seen.add(k)
                        defects.append(dict(d))
                out.append(Event("RECTIFIED", t.completed_at, t.test_date, nxt.odometer_value, nxt.odometer_unit,
                                 defects))
                i += 2
                continue
            out.append(Event("FAILED", t.completed_at, t.test_date, t.odometer_value, t.odometer_unit,
                             [dict(d) for d in t.defects], unpaired_prs=True))
        else:
            out.append(Event(t.result, t.completed_at, t.test_date, t.odometer_value, t.odometer_unit,
                             [dict(d) for d in t.defects]))
        i += 1
    return out


def _defect_json(d: dict, day: date) -> dict:
    typ = (d.get("type") or "").upper()
    return {"type": typ or "UNKNOWN", "dangerous": bool(d.get("dangerous")), "text": _clean_item(d.get("text") or ""),
            "recorded_on": day.isoformat(),
            "label": f"{TYPE_LABEL.get(typ, 'Defect')}, recorded at test on {fmt_date(day)}"}


def _record_json(e: Event) -> dict:
    has_odo = e.odometer_value is not None
    unit = ((e.odometer_unit or "").upper() or None) if has_odo else None
    return {"test_date": e.day.isoformat(), "result": e.kind, "odometer_value": e.odometer_value,
            "odometer_unit": unit, "defects": [_defect_json(d, e.day) for d in e.defects]}


# ------------------------------------------------------------------ rules
def _qid(rule_id: str, key: str) -> str:
    return rule_id.split("_")[0] + "-" + hashlib.sha1(f"{rule_id}|{key}".encode()).hexdigest()[:8]


def _gap(rule_id: str, **kw) -> dict:
    return {"rule_id": rule_id, "text": render(rule_id, **kw)}


def evaluate(history: DvsaHistory | None, upstream_status: str, cutoff_date: date, cutoff_at: datetime) -> dict:
    """Pure function: (snapshot content, cutoff) -> briefing content. Same input, same output, always."""
    cutoff = fmt_date(cutoff_date)
    gaps: list[dict] = []
    questions: list[dict] = []
    if upstream_status == "not_found" or history is None:
        gaps.append(_gap("G01_vehicle_not_found"))
        return _finish([], gaps, questions, cutoff, None)

    cut = history.before(cutoff_date, cutoff_at)         # R10: nothing at or after the cutoff
    events = build_events(cut.tests)
    window = events[-RECORD_LIMIT:]                      # chronological, last N
    if not events:
        gaps.append(_gap("G02_no_prior_test", cutoff=cutoff))
        return _finish([], gaps, questions, cutoff, None)
    last = events[-1]
    prev = events[-2] if len(events) >= 2 else None

    # ---- evidence gaps
    if len(events) == 1:
        gaps.append(_gap("G03_short_history", cutoff=cutoff))
    if add_months(last.day, STALE_MONTHS) < cutoff_date:
        gaps.append(_gap("G04_stale_history", cutoff=cutoff, date=fmt_date(last.day), months=STALE_MONTHS))
    missing = [e for e in reversed(window) if e.odometer_value is None]
    if missing:
        gaps.append(_gap("G05_missing_odometer", dates=_join([fmt_date(e.day) for e in missing])))
    drops = 0
    for before, after in reversed(list(zip(window, window[1:]))):
        if before.odometer_value is None or after.odometer_value is None:
            continue
        same_unit = (before.odometer_unit or "").upper() == (after.odometer_unit or "").upper()
        lo, hi = _miles(after.odometer_value, after.odometer_unit), _miles(before.odometer_value, before.odometer_unit)
        if hi - lo > (0 if same_unit else 0.005 * hi) and drops < MAX_ODOMETER_DROP_GAPS:
            drops += 1
            gaps.append(_gap("G06_odometer_drop", date_after=fmt_date(after.day), date_before=fmt_date(before.day),
                             after=_odo_text(after.odometer_value, after.odometer_unit),
                             before=_odo_text(before.odometer_value, before.odometer_unit)))
    if last.kind == "FAILED":
        gaps.append(_gap("G07_recent_fail_no_pass", date=fmt_date(last.day), cutoff=cutoff))
    for e in reversed(window):
        if e.unpaired_prs:
            gaps.append(_gap("G08_prs_without_pass", date=fmt_date(e.day)))

    # ---- follow-up questions (priority order = rule order)
    if last.kind == "FAILED":
        questions.append({"id": _qid("Q01_unresolved_fail", last.day.isoformat()), "rule_id": "Q01_unresolved_fail",
                          "text": render("Q01_unresolved_fail", date=fmt_date(last.day)),
                          "source_test_date": last.day.isoformat()})
    if last.kind == "PASSED" and prev is not None and prev.kind == "FAILED" \
            and (last.day - prev.day).days <= FAIL_THEN_PASS_WINDOW_DAYS:
        n = sum(1 for d in prev.defects if d.get("type") in ("MAJOR", "DANGEROUS"))
        if n:
            questions.append({"id": _qid("Q02_fail_then_pass", prev.day.isoformat()), "rule_id": "Q02_fail_then_pass",
                              "text": render("Q02_fail_then_pass", fail_date=fmt_date(prev.day), n=n,
                                             items=_plural(n, "item", "items"), pass_date=fmt_date(last.day)),
                              "source_test_date": prev.day.isoformat()})
    if last.kind == "RECTIFIED":
        n = sum(1 for d in last.defects if d.get("type") == "PRS")
        questions.append({"id": _qid("Q03_rectified_at_last_test", last.day.isoformat()),
                          "rule_id": "Q03_rectified_at_last_test",
                          "text": render("Q03_rectified_at_last_test", date=fmt_date(last.day), n=n,
                                         items=_plural(n, "item", "items")),
                          "source_test_date": last.day.isoformat()})
    # repeat advisories: same normalised text at >= 2 of the last N events
    by_item: dict[str, list[tuple[date, str]]] = {}
    for e in window:
        per_event: dict[str, str] = {}
        for d in e.defects:
            if d.get("type") == "ADVISORY":
                per_event.setdefault(_norm_item(d.get("text") or ""), _clean_item(d.get("text") or ""))
        for k, shown in per_event.items():
            days = by_item.setdefault(k, [])
            if not days or days[-1][0] != e.day:        # a same-day fail + retest copies its advisories: count the day once
                days.append((e.day, shown))
    repeats = sorted(((k, v) for k, v in by_item.items() if len(v) >= 2), key=lambda kv: (-kv[1][-1][0].toordinal(), kv[0]))
    repeated_keys = {k for k, _ in repeats}
    for k, v in repeats[:MAX_ADVISORY_QUESTIONS]:
        newest_day, shown = v[-1]
        questions.append({"id": _qid("Q04_repeat_advisory", k), "rule_id": "Q04_repeat_advisory",
                          "text": render("Q04_repeat_advisory", item=shown, date=fmt_date(newest_day), n=len(v),
                                         dates=_join([fmt_date(d) for d, _ in reversed(v)])),
                          "source_test_date": newest_day.isoformat()})
    singles, seen_single = [], set()
    for d in last.defects:
        k = _norm_item(d.get("text") or "")
        if d.get("type") == "ADVISORY" and k not in repeated_keys and k not in seen_single:
            seen_single.add(k)
            singles.append(_clean_item(d["text"]))
    for item in singles[:MAX_ADVISORY_QUESTIONS]:
        questions.append({"id": _qid("Q05_last_test_advisory", _norm_item(item)), "rule_id": "Q05_last_test_advisory",
                          "text": render("Q05_last_test_advisory", item=item, date=fmt_date(last.day)),
                          "source_test_date": last.day.isoformat()})

    records = [_record_json(e) for e in reversed(window)]       # newest first
    return _finish(records, gaps, questions, cutoff, (last, len(events)))


def _finish(records: list[dict], gaps: list[dict], questions: list[dict], cutoff: str, last_info) -> dict:
    g, q = len(gaps), len(questions)
    if last_info is None:
        summary = render("S02_summary_empty", gaps=_count(g, "evidence gap", "evidence gaps"))
    else:
        last, n = last_info
        result = {"PASSED": "a pass", "FAILED": "a fail",
                  "RECTIFIED": "a fail with items rectified at the test"}[last.kind]
        odo = f" at {_odo_text(last.odometer_value, last.odometer_unit)}" if last.odometer_value is not None else ""
        summary = render("S01_summary", n=n, results_word=_plural(n, "result", "results"), cutoff=cutoff,
                         date=fmt_date(last.day), result=result, odometer=odo,
                         gaps=_count(g, "evidence gap", "evidence gaps"),
                         questions=_count(q, "follow-up question", "follow-up questions"))
    return {"summary": summary, "source_records": records, "evidence_gaps": gaps, "follow_up_questions": questions}


def briefing_version(snapshot_sha256: str) -> str:
    return f"{snapshot_sha256[:16]}.{RULE_TABLE_VERSION}"


def build(history_json: dict | None, upstream_status: str, snapshot_sha256: str, cutoff_date: date,
          cutoff_at: datetime) -> dict:
    """Briefing content plus its identity. `history_json` is the stored snapshot (None when not_found)."""
    hist = DvsaHistory.from_json(history_json) if history_json else None
    out = evaluate(hist, upstream_status, cutoff_date, cutoff_at)
    out["rule_table_version"] = RULE_TABLE_VERSION
    out["briefing_version"] = briefing_version(snapshot_sha256)
    out["history_cutoff_date"] = cutoff_date.isoformat()
    return out
