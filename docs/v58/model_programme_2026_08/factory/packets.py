"""Packets emitter (contract v2 §1): prior-test tuples in the module's shape.

The 104 serving features are NOT computed here. They come from the proven,
parity-gated packets -> feature_engineering_v55 path. This module emits the
packets that path consumes, mirroring the banked fulldepth shape
(FULLDEPTH_SUBSTRATE_V1.md, `artifacts/fulldepth_packets.parquet`):

    LONG form, one row per (target, prior); zero-prior targets emit ONE row with
    NULL p_* fields so every target appears exactly once in the target set.

    tgt_id, vehicle_id, tgt_date, tgt_make, tgt_model, tgt_fuel, tgt_cc,
    tgt_fud, tgt_pc, n_priors,
    p_test_id, p_date, p_result, p_outcome, p_miles, p_ttype, p_n_items,
    p_items_observability, defects_json

PACKET SCHEMA v2 (2026-08-13, INC-2026-08-13). `defects_json` carries THREE
states and `p_items_observability` names which one, per prior test:

    JSON array   observed; these defect items
    '[]'         observed; NO defect items
    NULL         detail unavailable, or the builder emitted no payload

In v1 the last two were the same NULL, so a consumer could not tell "clean" from
"unknown" -- and the B0 module read every NULL as clean over 5,560,040 rows.
`p_n_items` is likewise NULL, never 0, when the detail is unobservable. Each
packet set ships a `PACKET_CAPABILITY.json` (factory/capability.py) declaring
what it can serve; a consumer asserts against it BEFORE it runs.

THE ONE DELIBERATE DIFFERENCE FROM THE BANKED SHAPE: the v58 lake carries NO
defect free text (DATA_ASSESSMENT §4: "No free text"), so `defects_json` has no
`x`/text key. It carries the code-grain and severity-axis fields instead:

    rfr   rfr_id (class-4 catalogue key)
    code  raw rfr_type_code, verbatim and case-preserved
    disp  F-22-corrected disposition: A | M | F | P
    sev   dangerous | major | minor | advisory | pre2018_ungraded
    sect  top-level catalogue section name (26 canonical names)
    cat   section-category key (7 canonical + 'other'); NULL = catalogue miss
    comp  serving component name (brakes/tyres/suspension/steering/structure)
    dang  dangerous_mark = 'D'
    loc   location_id (research-only)
    pos   coarse position group (research-only; NULL without the location map)

A downstream adapter must therefore build the module's `defects[].text` itself
(catalogue text join, as r2b_build_v57.py:70-72 did) or consume `cat`/`comp`
directly. The factory deliberately does NOT synthesise an empty text field: an
empty string would make the serving keyword parser silently return "no
component" for every defect, which is exactly the D1-class defect this
programme is repairing.
"""
import json
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from . import observability as obs
from .capability import PAYLOAD_MODES, PAYLOAD_ROWS
from .state import PriorTest

#: Lake outcome -> the module's test_result vocabulary (r2b_build_v57.py:50-51).
#: PRS folds to PASSED: the final-basis label treats it as the pass it was
#: recorded as (target_population.is_final_failure).
RESULT_MAP: Dict[str, str] = {
    "PASS": "PASSED", "PRS": "PASSED", "FAIL": "FAILED",
    "ABANDONED": "ABANDONED", "ABORTED": "ABANDONED",
    "ABORTED_VE": "ABANDONED", "REFUSED": "ABANDONED",
}

PACKET_COLUMNS: List[str] = [
    "tgt_id", "vehicle_id", "tgt_date", "tgt_make", "tgt_model", "tgt_fuel",
    "tgt_cc", "tgt_fud", "tgt_pc", "n_priors",
    "p_test_id", "p_date", "p_result", "p_outcome", "p_miles", "p_ttype",
    "p_n_items", "p_items_observability", "defects_json",
]

#: Defect-payload keys, frozen so a downstream adapter can rely on them.
DEFECT_KEYS = ("rfr", "code", "disp", "sev", "sect", "cat", "comp", "dang", "loc", "pos")

#: Full-depth priors observed max 50, pathology bound 80 (FULLDEPTH_SUBSTRATE_V1).
DEFAULT_PATHOLOGY_BOUND = 80


class PacketPathology(RuntimeError):
    """A vehicle exceeded the prior-count pathology bound."""


def map_result(outcome: Optional[str]) -> Optional[str]:
    if outcome is None:
        return None
    return RESULT_MAP.get(outcome, "ABANDONED")


#: The empty payload: "this prior test WAS observed and carried no defect
#: items". Distinct from NULL, which means "unknown". This one character of
#: difference is the whole three-state contract at the packet grain.
EMPTY_PAYLOAD = "[]"


def defects_json(defects: Optional[Iterable[dict]],
                 items_observability: Optional[str] = None,
                 defect_payload_mode: str = PAYLOAD_ROWS) -> Optional[str]:
    """Serialise one prior test's defect payload -- in THREE states.

        populated   observed, defect items present
        '[]'        observed, no defect items          (PREREG §4 middle state)
        NULL        detail NOT observable, or the builder emitted no payload

    Before this repair the function returned NULL for BOTH of the last two --
    `if defects is None: return None` -- so `defects_json IS NULL` could not be
    interpreted at all, and the B0 consumer read every NULL as "no defects"
    (INC-2026-08-13). The state is now decided by `items_observability`, never
    by whether the payload happens to be None.

    Key order is fixed and values are copied verbatim from the atom -- no
    re-derivation of disposition/severity happens here.
    """
    if defect_payload_mode not in PAYLOAD_MODES:
        raise ValueError(f"unknown defect_payload_mode {defect_payload_mode!r}")
    if defect_payload_mode != PAYLOAD_ROWS:
        # The BUILDER emitted no per-item payload. NULL is the only honest
        # value; the packet set's capability declares the mode so a consumer
        # that needs items fails at preflight instead of reading zeros.
        return None
    if items_observability is not None and not obs.is_observed(items_observability):
        return None
    if defects is None:
        # Observed and empty. (When observability is unknown -- a caller that
        # passes no state at all -- we keep the historical NULL rather than
        # inventing an observation.)
        return EMPTY_PAYLOAD if items_observability is not None else None
    rows = []
    for defect in defects:
        rows.append({
            "rfr": defect.get("rfr"),
            "code": defect.get("code"),
            "disp": defect.get("disp"),
            "sev": defect.get("sev"),
            "sect": defect.get("sect"),
            "cat": defect.get("cat"),
            "comp": defect.get("comp"),
            "dang": bool(defect.get("dang")),
            "loc": defect.get("loc"),
            "pos": defect.get("pos"),
        })
    return json.dumps(rows, separators=(",", ":"), sort_keys=False, default=str)


def emit_packet_rows(event: Dict[str, Any], priors: List[PriorTest],
                     max_priors: Optional[int] = None,
                     pathology_bound: int = DEFAULT_PATHOLOGY_BOUND,
                     defect_payload_mode: str = PAYLOAD_ROWS) -> List[Dict[str, Any]]:
    """Packet rows for ONE target. Priors are the as_of_state's prior tests.

    `priors` is a SET by contract -- the emitted rows carry no ordering claim
    beyond the calendar date in `p_date`. The list is trimmed by DATE when
    `max_priors` is set (most recent kept), and same-date ties are kept whole so
    the trim can never depend on within-day order.
    """
    n_priors = len(priors)
    if n_priors > pathology_bound:
        raise PacketPathology(
            f"vehicle {event.get('vehicle_id')} has {n_priors} priors at target "
            f"{event.get('tgt_id')}, above the pathology bound {pathology_bound}. "
            f"Full-depth measured max is 50 (p99.9=28) -- investigate before raising.")

    kept = priors
    if max_priors is not None and n_priors > max_priors:
        cutoff_dates = sorted({p.test_date for p in priors}, reverse=True)
        keep_dates = set()
        running = 0
        for day in cutoff_dates:
            same_day = sum(1 for p in priors if p.test_date == day)
            if running and running + same_day > max_priors:
                break
            keep_dates.add(day)
            running += same_day
        kept = [p for p in priors if p.test_date in keep_dates]

    base = {
        "tgt_id": event["tgt_id"],
        "vehicle_id": event["vehicle_id"],
        "tgt_date": event["tgt_date"],
        "tgt_make": event.get("tgt_make"),
        "tgt_model": event.get("tgt_model"),
        "tgt_fuel": event.get("tgt_fuel"),
        "tgt_cc": event.get("tgt_cc"),
        "tgt_fud": event.get("tgt_fud"),
        "tgt_pc": event.get("tgt_pc"),
        "n_priors": n_priors,
    }
    if not kept:
        empty = dict(base)
        # A zero-prior target: there is no prior test, so there is no defect
        # observation to describe. NULL everywhere, including the state.
        empty.update({"p_test_id": None, "p_date": None, "p_result": None,
                      "p_outcome": None, "p_miles": None, "p_ttype": None,
                      "p_n_items": None, "p_items_observability": None,
                      "defects_json": None})
        return [empty]

    rows: List[Dict[str, Any]] = []
    for prior in kept:
        row = dict(base)
        row.update({
            "p_test_id": prior.test_id,
            "p_date": prior.test_date,
            "p_result": map_result(prior.outcome),
            "p_outcome": prior.outcome,
            "p_miles": prior.mileage,
            "p_ttype": prior.test_type,
            # NULL when unobservable -- never 0 (M4).
            "p_n_items": prior.n_items,
            "p_items_observability": prior.items_observability,
            "defects_json": defects_json(prior.defects, prior.items_observability,
                                         defect_payload_mode),
        })
        rows.append(row)
    return rows


def assert_strictly_prior(rows: Iterable[Dict[str, Any]]) -> int:
    """Gate mirroring build_packets_fulldepth: zero rows with p_date >= tgt_date."""
    leaks = 0
    for row in rows:
        p_date, tgt_date = row.get("p_date"), row.get("tgt_date")
        if isinstance(p_date, date) and isinstance(tgt_date, date) and p_date >= tgt_date:
            leaks += 1
    if leaks:
        raise AssertionError(
            f"{leaks} packet row(s) carry p_date >= tgt_date: the as-of fence is "
            f"broken. Priors are STRICTLY earlier calendar days.")
    return leaks


def d13_invariant_projection(row: Dict[str, Any]) -> tuple:
    """The D13-invariant content of one packet row.

    `p_test_id` (and `tgt_id`) are identifiers: swapping two same-day test_ids
    permutes them by construction, so D13 invariance is asserted on everything
    ELSE -- which is the whole information content the module consumes.
    """
    return (row.get("vehicle_id"), row.get("tgt_date"), row.get("p_date"),
            row.get("p_result"), row.get("p_outcome"), row.get("p_miles"),
            row.get("p_ttype"), row.get("p_n_items"),
            row.get("p_items_observability"), row.get("defects_json"),
            row.get("n_priors"))
