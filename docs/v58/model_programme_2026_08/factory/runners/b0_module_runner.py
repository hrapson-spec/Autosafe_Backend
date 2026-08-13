#!/usr/bin/env python3
"""Packets view -> feature_engineering_v55 -> the B0-104 frame.

Contract v2 removed B0 from the factory's own remit: the 104 serving features
come from the proven, parity-gated packets -> module path. This runner IS that
path. It streams the factory's packets parquet, rebuilds each target's
`VehicleHistory`, calls the REPAIRED module's `engineer_features`, and writes
one row per target keyed by `test_id`, joinable to the factory frame.

    module of record: /Users/henrirapson/autosafe/feature_engineering_v55.py
                      (READ-ONLY import; FAILURE_DEFECT_TYPES =
                      {DANGEROUS,MAJOR,FAIL,F}, has_prior_failure_* distinct
                      from has_ever_failed_*, NaN sentinels not 999)

TWO DOCUMENTED ADAPTATIONS, both recorded in the emitted manifest:

1. DEFECT TYPE is the enum mapping, not a guess (SERVE_VIEW B3): dangerous_mark
   'D' -> DANGEROUS; F not D-marked -> MAJOR; M -> MINOR; A -> ADVISORY;
   P -> PRS. The module's repaired vocabulary counts {DANGEROUS, MAJOR, FAIL, F}
   as failures and excludes MINOR and PRS.
2. DEFECT TEXT does not exist in the lake (DATA_ASSESSMENT section 4). The
   module classifies components by keyword-matching free text, so this runner
   supplies the catalogue SECTION NAME as the text (`--defect-text-source
   section`) -- the same training-direction emulation r2b_build_v57.py:70-72
   used. This is NOT live tester text: the text->taxonomy precision gate is the
   unbuilt bridge (SERVE_VIEW finding 1). `--defect-text-source none` emits the
   family honestly empty instead.

Parallelism is over VEHICLES (never over rows of one vehicle), so a vehicle's
whole history lands in one worker and no cross-process state is needed.
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .. import capability
from .. import observability as obs

#: The read-only reference checkout carrying the repaired module.
MODULE_REPO = "/Users/henrirapson/autosafe"

#: What to do with a prior test whose defect detail is UNAVAILABLE.
#: There is deliberately no silent option: `empty` is the pre-incident
#: behaviour and must now be typed out and stamped into the manifest.
POLICY_FAIL = "fail"
POLICY_DROP_PRIOR = "drop-prior"
POLICY_EMPTY = "empty"
UNOBSERVED_POLICIES = (POLICY_FAIL, POLICY_DROP_PRIOR, POLICY_EMPTY)


class UnobservedDefectDetail(RuntimeError):
    """A prior test's defect detail is unavailable and no policy permits it."""

#: lake disposition/severity -> the module's defect `type` vocabulary.
DEFECT_TYPE_BY_SEVERITY = {"dangerous": "DANGEROUS", "major": "MAJOR",
                           "minor": "MINOR", "advisory": "ADVISORY"}
DEFECT_TYPE_BY_DISPOSITION = {"F": "MAJOR", "P": "PRS", "M": "MINOR", "A": "ADVISORY"}

RESULT_TO_MODULE = {"PASSED": "PASSED", "FAILED": "FAILED", "ABANDONED": "ABANDONED"}


def import_module_of_record():
    """Import the repaired serving module (read-only) and its dependencies."""
    if MODULE_REPO not in sys.path:
        sys.path.insert(0, MODULE_REPO)
    import feature_engineering_v55 as fe

    if not getattr(fe, "FAILURE_DEFECT_TYPES", None) == {
            "DANGEROUS", "MAJOR", "FAIL", "F"}:
        raise RuntimeError(
            f"{fe.__file__} is not the REPAIRED copy: FAILURE_DEFECT_TYPES="
            f"{getattr(fe, 'FAILURE_DEFECT_TYPES', None)!r}. The v58 copy tests "
            f"type=='FAIL' only, so every post-2018 history computes ZERO "
            f"component failures (SERVE_VIEW finding 3). Refusing to build B0.")
    return fe


def defect_type(defect: dict) -> str:
    sev = str(defect.get("sev") or "").lower()
    if sev in DEFECT_TYPE_BY_SEVERITY:
        return DEFECT_TYPE_BY_SEVERITY[sev]
    return DEFECT_TYPE_BY_DISPOSITION.get(str(defect.get("disp") or ""), "ADVISORY")


def defect_text(defect: dict, source: str) -> str:
    if source == "none":
        return ""
    if source == "section":
        return str(defect.get("sect") or defect.get("cat") or "")
    if source == "component":
        return str(defect.get("comp") or defect.get("cat") or "")
    raise ValueError(f"unknown defect text source {source!r}")


def _to_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.fromisoformat(str(value)[:19].replace(" ", "T"))


def decode_defects(prior: dict, text_source: str, unobserved_policy: str,
                   counters: Optional[Dict[str, int]] = None):
    """The defect list for one prior test -- or a refusal.

    THIS IS THE CONSUMER-SIDE HALF OF INC-2026-08-13. The line it replaces was

        payload = json.loads(raw) if raw else []

    which turned a NULL `defects_json` into "this vehicle had no defects" for
    5,560,040 rows without a word of complaint. `[]` and NULL are now different
    values (packets.defects_json), and NULL is never silently emptied:

        observed  ->  the payload ('[]' means observed with no defect items)
        unknown   ->  refuse, drop the prior, or empty it -- but only ever the
                      one the operator asked for, and always counted.
    """
    raw = prior.get("defects_json")
    state = prior.get("p_items_observability")
    if counters is not None and state:
        counters[state] = counters.get(state, 0) + 1

    if state is not None and not obs.is_observed(state):
        if unobserved_policy == POLICY_FAIL:
            raise UnobservedDefectDetail(
                f"prior test {prior.get('p_test_id')} carries "
                f"p_items_observability={state!r}: its defect detail is NOT "
                f"observable, so this consumer cannot know whether the vehicle "
                f"had defects. Emitting an empty defect list would record "
                f"'no defects' for an unknown -- INC-2026-08-13. Choose a "
                f"declared degradation with --unobserved-defect-policy "
                f"{{{POLICY_DROP_PRIOR}|{POLICY_EMPTY}}}; it is recorded in the "
                f"emitted manifest.")
        if unobserved_policy == POLICY_DROP_PRIOR:
            return None
        return []                       # POLICY_EMPTY, declared and counted

    if raw is None:
        if state is None:
            # A legacy (schema v1) packet set: NULL is ambiguous by
            # construction. The capability gate refuses these before we get
            # here for any consumer that reads defect items; reaching this
            # branch at all means the gate was bypassed.
            raise UnobservedDefectDetail(
                f"prior test {prior.get('p_test_id')} has a NULL defects_json "
                f"and no p_items_observability: this is a packet-schema-v1 set "
                f"in which 'no defect items' and 'defect detail unavailable' "
                f"are the same value. Rebuild the packet set.")
        raise UnobservedDefectDetail(
            f"prior test {prior.get('p_test_id')} is {state!r} (observed) but "
            f"carries a NULL defects_json. An observed prior must carry '[]' at "
            f"minimum -- this packet set violates its own contract.")
    return json.loads(raw)


def build_history(fe, target: dict, priors: List[dict], text_source: str,
                  unobserved_policy: str = POLICY_FAIL,
                  counters: Optional[Dict[str, int]] = None):
    """One target's VehicleHistory, from its packet rows."""
    from dvsa_client import MOTTest, VehicleHistory

    tests = []
    for prior in priors:
        if prior.get("p_test_id") is None:
            continue
        payload = decode_defects(prior, text_source, unobserved_policy, counters)
        if payload is None:             # POLICY_DROP_PRIOR
            continue
        defects = [{"type": defect_type(d), "text": defect_text(d, text_source),
                    "dangerous": bool(d.get("dang"))} for d in payload]
        test_date = _to_datetime(prior["p_date"])
        result = RESULT_TO_MODULE.get(str(prior.get("p_result")), "ABANDONED")
        tests.append(MOTTest(
            test_date=test_date, test_result=result,
            # +365 days, never .replace(year=+1): a 29-Feb test would raise.
            # The substrate has no expiry field; this is the documented
            # approximation of record (r2b_build_v57.py:187-194).
            expiry_date=(test_date + timedelta(days=365)
                         if (test_date and result == "PASSED") else None),
            odometer_value=(int(prior["p_miles"])
                            if prior.get("p_miles") is not None else None),
            odometer_unit="mi", test_number=str(prior.get("p_test_id")),
            defects=defects))
    fud = _to_datetime(target.get("tgt_fud"))
    return VehicleHistory(
        registration="", make=str(target.get("tgt_make") or ""),
        model=str(target.get("tgt_model") or ""),
        fuel_type=target.get("tgt_fuel"), colour=None,
        registration_date=fud, manufacture_date=fud,
        engine_size=(int(target["tgt_cc"]) if target.get("tgt_cc") is not None else None),
        mot_tests=tests)


def features_for_target(fe, target: dict, priors: List[dict], text_source: str,
                        unobserved_policy: str = POLICY_FAIL,
                        counters: Optional[Dict[str, int]] = None) -> List[Any]:
    history = build_history(fe, target, priors, text_source, unobserved_policy,
                            counters)
    features = fe.engineer_features(
        history, postcode=str(target.get("tgt_pc") or ""),
        prediction_date=_to_datetime(target["tgt_date"]))
    return fe.features_to_array(features)


def assert_consumer_capability(packets_glob: str, text_source: str,
                               require_certified_coverage: bool = False,
                               con=None) -> "capability.PacketCapability":
    """THE PREFLIGHT GATE. Runs BEFORE the module is imported or a row is read.

    A consumer asking for defect text over a packet set built without a defect
    payload fails here, with the packet set's own declaration quoted back at it.
    A legacy set with no declaration is MEASURED, so the fullpop packets that
    caused INC-2026-08-13 (0 of 5,560,040 non-null) are refused too.
    """
    import duckdb

    requirement = capability.requirement_for(f"b0_module:{text_source}")
    if require_certified_coverage:
        requirement = capability.ConsumerRequirement(
            **{**requirement.as_manifest(),
               "needs_coverage_mode": obs.COVERAGE_CERTIFIED})
    return capability.assert_packet_set_supports(
        con or duckdb.connect(), packets_glob, requirement)


_WORKER: Dict[str, Any] = {}


def _init_worker(text_source: str, unobserved_policy: str = POLICY_FAIL):
    _WORKER["fe"] = import_module_of_record()
    _WORKER["text_source"] = text_source
    _WORKER["unobserved_policy"] = unobserved_policy


def _run_vehicle(payload: Tuple[int, List[dict]]):
    """One vehicle: every target of that vehicle, in one worker."""
    fe, text_source = _WORKER["fe"], _WORKER["text_source"]
    policy = _WORKER.get("unobserved_policy", POLICY_FAIL)
    # Per-CALL, never per-worker: the caller sums these, so a worker-scoped dict
    # would be re-added once per vehicle it has already seen.
    counters: Dict[str, int] = {}
    _vehicle_id, rows = payload
    by_target: Dict[int, List[dict]] = {}
    targets: Dict[int, dict] = {}
    for row in rows:
        tid = int(row["tgt_id"])
        targets.setdefault(tid, row)
        by_target.setdefault(tid, []).append(row)
    out = []
    for tid, target in targets.items():
        out.append((tid, int(target["vehicle_id"]),
                    features_for_target(fe, target, by_target[tid], text_source,
                                        policy, counters)))
    return out, dict(counters)


def iter_vehicle_groups(con, packets_glob: str, limit: Optional[int] = None
                        ) -> Iterable[Tuple[int, List[dict]]]:
    """Packet rows grouped by vehicle, streamed in vehicle order."""
    rel = f"read_parquet('{str(packets_glob)}', union_by_name=true)"
    where = ""
    if limit:
        where = (f"WHERE vehicle_id IN (SELECT DISTINCT vehicle_id FROM {rel} "
                 f"ORDER BY vehicle_id LIMIT {int(limit)})")
    columns = [d[0] for d in con.execute(f"SELECT * FROM {rel} LIMIT 0").description]
    # A packet-schema-v1 set has no state column. It is refused by the
    # capability gate for every defect-reading consumer; projected as NULL here
    # only so the refusal message can name the row.
    state_col = ("p_items_observability" if "p_items_observability" in columns
                 else "CAST(NULL AS VARCHAR) AS p_items_observability")
    cursor = con.execute(
        f"SELECT tgt_id, vehicle_id, tgt_date, tgt_make, tgt_model, tgt_fuel, "
        f"tgt_cc, tgt_fud, tgt_pc, p_test_id, p_date, p_result, p_miles, "
        f"p_ttype, {state_col}, defects_json FROM {rel} {where} "
        f"ORDER BY vehicle_id, tgt_id")
    names = [c[0] for c in cursor.description]
    current, batch = None, []
    while True:
        rows = cursor.fetchmany(20_000)
        if not rows:
            break
        for raw in rows:
            row = dict(zip(names, raw))
            vid = int(row["vehicle_id"])
            if current is None:
                current = vid
            if vid != current:
                yield current, batch
                current, batch = vid, []
            batch.append(row)
    if batch:
        yield current, batch


def run(packets_glob: str, out_path: str, *, text_source: str = "section",
        processes: int = 1, con=None, limit: Optional[int] = None,
        chunk_rows: int = 50_000, unobserved_policy: str = POLICY_FAIL,
        require_certified_coverage: bool = False) -> dict:
    """Stream the packets view through the module; write the B0-104 frame."""
    import duckdb

    if unobserved_policy not in UNOBSERVED_POLICIES:
        raise ValueError(f"unknown unobserved_defect_policy {unobserved_policy!r}")
    con = con or duckdb.connect()
    # PREFLIGHT FIRST: refuse an incompatible packet set before importing the
    # module or reading a single row (PREREG_CUBE_v2 §5).
    cap = assert_consumer_capability(packets_glob, text_source,
                                     require_certified_coverage, con=con)

    fe = import_module_of_record()
    names = fe.get_feature_names()
    schema = pa.schema([("test_id", pa.int64()), ("vehicle_id", pa.int64())]
                       + [(n, pa.string() if i in set(fe.get_categorical_indices())
                           else pa.float64()) for i, n in enumerate(names)])
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    writer = pq.ParquetWriter(out_path, schema, compression="zstd")
    cat_idx = set(fe.get_categorical_indices())

    def flush(rows: List[Tuple[int, int, List[Any]]]):
        if not rows:
            return
        columns: Dict[str, list] = {"test_id": [r[0] for r in rows],
                                    "vehicle_id": [r[1] for r in rows]}
        for i, name in enumerate(names):
            if i in cat_idx:
                columns[name] = [None if r[2][i] is None else str(r[2][i]) for r in rows]
            else:
                columns[name] = [
                    None if r[2][i] is None else float(r[2][i]) for r in rows]
        writer.write_table(pa.Table.from_pydict(columns, schema=schema))

    groups = iter_vehicle_groups(con, packets_glob, limit)
    buffer: List[Tuple[int, int, List[Any]]] = []
    n_targets = n_vehicles = 0
    seen_states: Dict[str, int] = {}

    def absorb(result):
        nonlocal n_vehicles, n_targets, buffer
        rows, counters = result
        n_vehicles += 1
        buffer.extend(rows)
        n_targets += len(rows)
        for key, value in counters.items():
            seen_states[key] = seen_states.get(key, 0) + int(value)
        if len(buffer) >= chunk_rows:
            flush(buffer)
            buffer = []

    if processes > 1:
        with mp.get_context("spawn").Pool(
                processes, initializer=_init_worker,
                initargs=(text_source, unobserved_policy)) as pool:
            for result in pool.imap(_run_vehicle, groups, chunksize=8):
                absorb(result)
    else:
        _init_worker(text_source, unobserved_policy)
        for group in groups:
            absorb(_run_vehicle(group))
    flush(buffer)
    writer.close()

    manifest = {
        "out_path": out_path, "n_targets": n_targets, "n_vehicles": n_vehicles,
        "n_features": len(names), "module_path": fe.__file__,
        "module_failure_vocabulary": sorted(fe.FAILURE_DEFECT_TYPES),
        "module_advisory_vocabulary": sorted(fe.ADVISORY_DEFECT_TYPES),
        # The builder/consumer contract this run was admitted under.
        "packet_capability": cap.as_manifest(),
        "unobserved_defect_policy": unobserved_policy,
        "item_observability_seen": dict(sorted(seen_states.items())),
        "defect_text_source": text_source,
        "defect_text_caveat": (
            "the lake carries NO defect free text; text is the catalogue SECTION "
            "name (r2b_build_v57.py:70-72 training-direction emulation), not live "
            "tester text. The text->taxonomy precision gate is UNRUN: every "
            "component/mech_decay/text_* feature is catalogue-text parity, not "
            "live-text parity (SERVE_VIEW finding 1)."),
        "defect_type_mapping": {"dangerous_mark D": "DANGEROUS", "F not D": "MAJOR",
                                "M": "MINOR", "A": "ADVISORY", "P": "PRS"},
        "join_key": "test_id",
    }
    with open(out_path + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, default=str)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.b0_module_runner",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packets", required=True, help="packets parquet glob")
    ap.add_argument("--out", required=True, help="B0-104 frame parquet path")
    ap.add_argument("--processes", type=int, default=1,
                    help="workers over VEHICLES; one compute job at a time")
    ap.add_argument("--defect-text-source", default="section",
                    choices=("section", "component", "none"),
                    help="all three read the per-item defect payload, so all "
                         "three require a packet set built with --defect-detail "
                         "rows. The pairing is asserted before any row is read.")
    ap.add_argument("--unobserved-defect-policy", default=POLICY_FAIL,
                    choices=UNOBSERVED_POLICIES,
                    help="what to do with a prior test whose defect detail is "
                         "UNAVAILABLE. Default 'fail'. 'drop-prior' removes that "
                         "prior test from the history; 'empty' is the "
                         "pre-INC-2026-08-13 behaviour and must be typed out. "
                         "The choice and the per-state counts are recorded in "
                         "the emitted manifest.")
    ap.add_argument("--require-certified-item-coverage", action="store_true",
                    help="refuse a packet set whose items_coverage_mode is "
                         "'assumed_covered' (no owner item-coverage ledger).")
    ap.add_argument("--limit-vehicles", type=int, default=None)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        manifest = run(a.packets, a.out, text_source=a.defect_text_source,
                       processes=a.processes, limit=a.limit_vehicles,
                       unobserved_policy=a.unobserved_defect_policy,
                       require_certified_coverage=a.require_certified_item_coverage)
    except (capability.CapabilityMismatch, capability.CapabilityMissing,
            UnobservedDefectDetail) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(manifest, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
