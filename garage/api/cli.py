"""Operator tool (R17: the invite-code tool is the only recruitment mechanism).

  python -m garage.api.cli migrate
  python -m garage.api.cli create-invite --note "Smith Motors" [--ttl-days 14]
  python -m garage.api.cli monitor-g7 --policy g7_policy.json                  (read-only; prints JSON)
  python -m garage.api.cli monitor-g6 --policy g6_policy.json --rows rows.jsonl --reference ref.json
  python -m garage.api.cli monitor-g6-replay --policy g6_replay_policy.json --reference ref.json --bundle DIR [--record]
  (monitor-g7 and monitor-g6-replay take --record: append the aggregate result to monitoring_runs, migration 0006)
Every monitoring value is read from the policy file (no defaults; a missing value refuses); its sha256 is printed.
The code is printed once; only its hash is stored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import auth_store
from .config import Settings
from .db import Database, migrate


# Read-only monitoring commands. They only SELECT; they never write or act. Every policy value comes from a policy
# JSON file (monitoring/policy.py): no defaults, missing value = refusal. The file's sha256 is printed to stderr and
# recorded in the JSON output.
def _policy_block(path, sha, parsed):
    return {"path": os.path.abspath(path), "sha256": sha, "values": parsed}


def _monitor_g7(dsn, policy_path, record=False):
    from .monitoring import evaluate_settled
    from .monitoring.policy import g7_policy, read_policy
    from .monitoring.reader import settled_rows
    parsed, sha = read_policy(policy_path, "monitor_g7")
    pol = g7_policy(parsed)
    print("policy_sha256=" + sha, file=sys.stderr)
    db = Database(dsn, 1, 1)
    with db.tx() as cur:
        rows = settled_rows(cur, check_day=pol["settled_check_day"], statuses=pol["settled_statuses"],
                            booking_types=pol["booking_types"], score_column=pol["score_column"])
    res = evaluate_settled(rows, pol["g7_params"], pol["outcome_policy"], band_edges=pol["band_edges"],
                           rounding_pp=pol["rounding_pp"], bands_only_min_n=pol["bands_only_min_n"],
                           n_boot=pol["n_boot"], seed=pol["seed"])
    res["policy"] = _policy_block(policy_path, sha, parsed)
    if record:
        from .monitoring.runs import record_run
        with db.tx() as cur:
            res["monitoring_run_id"] = record_run(cur, kind="g7", result=res, policy_sha256=sha)
    print(json.dumps(res, indent=1, sort_keys=True, default=str))


def _monitor_g6_replay(dsn, a):
    """G6 on production inputs: replay stored raw DVSA snapshots through the bundle scorer's features mode
    (monitoring/replay.py). Read-only on the application tables; appends one aggregate row to monitoring_runs only with --record."""
    from .monitoring import ReferenceArtefact
    from .monitoring.policy import g6_replay_policy, read_policy
    from .monitoring.reader import replay_candidates
    from .monitoring.replay import evaluate_replay, features_argv, run_features_subprocess
    from .providers.bundle_provider import BundleError, verify_bundle
    from .monitoring import MonitoringInputError
    parsed, sha = read_policy(a.policy, "monitor_g6_replay")
    pol = g6_replay_policy(parsed)
    print("policy_sha256=" + sha, file=sys.stderr)
    ref = ReferenceArtefact.from_file(a.reference, pol["reference_sha256"])
    try:
        vb = verify_bundle(a.bundle)
    except BundleError as e:
        raise MonitoringInputError(f"bundle refused: {e.code}") from e
    if vb.manifest_sha256 != pol["bundle_manifest_sha256"]:
        raise MonitoringInputError("bundle manifest sha256 differs from the policy pin")
    argv, cwd = features_argv(vb), str(vb.directory)
    runner = lambda stdin: run_features_subprocess(argv, stdin, cwd=cwd, timeout_s=pol["timeout_s"],   # noqa: E731
                                                   max_output_bytes=pol["max_output_bytes"])
    db = Database(dsn, 1, 1)
    with db.tx() as cur:
        cands = replay_candidates(cur, window_start=pol["window_start"], window_end=pol["window_end"],
                                  booking_types=pol["booking_types"], score_column=pol["score_column"],
                                  max_bookings=pol["max_bookings"])
    info = {"window": {"start_utc": pol["window_start"].isoformat(), "end_utc": pol["window_end"].isoformat()},
            "score_column": pol["score_column"], "booking_types": pol["booking_types"],
            "subprocess": {"shell": False, "env_keys": ["LC_ALL", "PATH"], "timeout_s": pol["timeout_s"],
                           "max_output_bytes": pol["max_output_bytes"]}}
    res = evaluate_replay(cands, runner, reference=ref, bundle_manifest_sha256=pol["bundle_manifest_sha256"],
                          feature_schema_sha256=pol["feature_schema_sha256"],
                          unsupported_error_codes=pol["unsupported_error_codes"],
                          outcome_access_start_utc=pol["outcome_access_start_utc"], population=pol["population"],
                          n_boot=pol["n_boot"], seed=pol["seed"], alpha=pol["alpha"], replay_info=info)
    res["policy"] = _policy_block(a.policy, sha, parsed)
    if a.record:
        from .monitoring.runs import record_run
        with db.tx() as cur:
            res["monitoring_run_id"] = record_run(
                cur, kind="g6_replay", result=res, policy_sha256=sha, window_start=pol["window_start"],
                window_end=pol["window_end"], reference_sha256=ref.sha256,
                bundle_manifest_sha256=pol["bundle_manifest_sha256"])
    print(json.dumps(res, indent=1, sort_keys=True, default=str))


def _monitor_g6(a):
    from .monitoring import ReferenceArtefact, evaluate_g6
    from .monitoring.policy import g6_policy, read_policy
    parsed, sha = read_policy(a.policy, "monitor_g6")
    pol = g6_policy(parsed)
    print("policy_sha256=" + sha, file=sys.stderr)
    ref = ReferenceArtefact.from_file(a.reference, pol["reference_sha256"])
    with open(a.rows) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    res = evaluate_g6(rows, ref, outcome_access_start_utc=pol["outcome_access_start_utc"],
                      population=pol["population"], n_boot=pol["n_boot"], seed=pol["seed"], alpha=pol["alpha"])
    res["policy"] = _policy_block(a.policy, sha, parsed)
    print(json.dumps(res, indent=1, sort_keys=True))


def _refusing(fn, *args):
    """A refused policy or input is a clean non-zero exit with the reason on stderr, never a partial result."""
    from .monitoring import MonitoringInputError
    try:
        return fn(*args)
    except MonitoringInputError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        raise SystemExit(2)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="garage.api.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    c = sub.add_parser("create-invite")
    c.add_argument("--note", default=None)
    c.add_argument("--ttl-days", type=int, default=14)
    m7 = sub.add_parser("monitor-g7")
    m7.add_argument("--policy", required=True)
    m7.add_argument("--record", action="store_true")
    mr = sub.add_parser("monitor-g6-replay")
    for flag in ("--policy", "--reference", "--bundle"):
        mr.add_argument(flag, required=True)
    mr.add_argument("--record", action="store_true")
    m6 = sub.add_parser("monitor-g6")
    for flag in ("--policy", "--rows", "--reference"):
        m6.add_argument(flag, required=True)
    a = p.parse_args(argv)
    if a.cmd == "monitor-g6":
        return _refusing(_monitor_g6, a)   # file inputs only: no database needed (features are not stored; see REPORT)
    dsn = Settings.from_env().database_url or os.environ["GARAGE_DATABASE_URL"]
    if a.cmd == "migrate":
        print("applied:", migrate(dsn) or "nothing to do")
    elif a.cmd == "monitor-g7":
        _refusing(_monitor_g7, dsn, a.policy, a.record)
    elif a.cmd == "monitor-g6-replay":
        _refusing(_monitor_g6_replay, dsn, a)
    else:
        db = Database(dsn, 1, 1)
        with db.tx() as cur:
            print(auth_store.create_invite(cur, a.note, a.ttl_days))


if __name__ == "__main__":
    main()
