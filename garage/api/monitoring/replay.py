"""Production input-PSI for G6 by replay (APP_COMPLETION_V1 / T1b, task 2).

WHY A REPLAY. The model input vector is not stored anywhere (REPORT MONITORING_V1 finding F3; migrations 0003 and 0004 keep
only the scorer's margin/probability and the raw DVSA response bytes). G6 (DECISIONS.md:50, AMENDMENT_DRAFT.md:49) needs the
distribution of every model input, so the job rebuilds each input row from the stored raw snapshot by calling the bundle's
own scorer in FEATURES MODE:

    <runtime> -I <bundle>/scorer/serve.py --bundle <bundle> --emit-features        (request JSON on stdin, as for scoring)
    stdout: {"ok": true, "names": [...], "values": [...], "feature_schema_sha256": "<64 hex>", "bundle_manifest_sha256": "<64 hex>"}
         or {"ok": false, "error": "<code>"}

The request is built by the SAME code the provider uses (`bundle_provider._raw_history`), so a replayed row is the row the
scorer saw at serving time (a parity test compares the stdin bytes with `bundle_provider._scorer_input`).

RULES KEPT. G6 itself is `psi.evaluate_g6`, unchanged: >= 500 pooled supported bookings, point PSI <= 0.10 on every input and on
the score, a hash-pinned reference. This module only supplies the rows. The score column is a REQUIRED NAME (see reader.py).

SUBPROCESS HARDENING. argv list, never a shell; environment = PATH and LC_ALL only (same as the serving provider); working
directory = the bundle; stderr discarded; hard wall-clock timeout; stdout is read incrementally and the child is killed as soon as
it has written more than `max_output_bytes` (the output is never held beyond that cap).

FAIL CLOSED. The whole run is refused (ReplayRefused, no partial result) if the scorer answers for another bundle manifest than
the pinned one, if feature_schema_sha256 differs from the pinned value or between rows, if the feature names differ between
rows, or if the reference artefact's inputs are not exactly the scorer's input names. Row-level problems (timeout, malformed
output, a scorer refusal code, a missing raw snapshot, ...) exclude the row and are COUNTED by code; no feature value, booking id
or registration is logged or returned. The result is aggregates only.
"""
from __future__ import annotations

import json
import math
import os
import re
import selectors
import signal
import subprocess
import threading
import time
from collections import Counter
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..providers.bundle_provider import VerifiedBundle, _raw_history
from ._stats import MonitoringInputError
from .psi import evaluate_g6
from .reference import ReferenceArtefact

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MAX_FEATURES = 10_000
# Same environment as the serving provider (bundle_provider.BundleProvider._assess). Nothing else is passed through.
CHILD_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}


class ReplayRefused(MonitoringInputError):
    """The run cannot be trusted (mixed or unpinned bundle/schema, schema/reference mismatch). `code` is a fixed string."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# ----------------------------------------------------------------------------------------------- subprocess
def features_argv(bundle: VerifiedBundle) -> List[str]:
    """argv for the features mode of a verified BundleV1 directory."""
    return [bundle.runtime_executable, "-I", str(bundle.scorer_path), "--bundle", str(bundle.directory),
            "--emit-features"]


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def run_features_subprocess(argv: Sequence[str], stdin_bytes: bytes, *, cwd: str, timeout_s: float,
                            max_output_bytes: int) -> Tuple[Optional[bytes], Optional[str]]:
    """(stdout bytes, None) on exit 0, else (None, code) with code in launch_failed | timeout | output_too_large | exit_nonzero."""
    try:
        proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=cwd, env=dict(CHILD_ENV), shell=False, close_fds=True, start_new_session=True)
    except (OSError, ValueError):
        return None, "launch_failed"

    def feed() -> None:
        try:
            proc.stdin.write(stdin_bytes)
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    deadline = time.monotonic() + timeout_s
    buf = bytearray()
    code: Optional[str] = None
    sel = selectors.DefaultSelector()
    try:
        fd = proc.stdout.fileno()
        sel.register(fd, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not sel.select(remaining):
                code = "timeout"
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > max_output_bytes:
                code = "output_too_large"
                break
        if code is None:
            try:
                rc = proc.wait(timeout=max(deadline - time.monotonic(), 0.0))
            except subprocess.TimeoutExpired:
                code = "timeout"
            else:
                if rc != 0:
                    code = "exit_nonzero"
    finally:
        sel.close()
        if code in ("timeout", "output_too_large") or proc.poll() is None:
            _kill(proc)
        try:
            proc.stdout.close()
        except OSError:
            pass
        writer.join(timeout=1.0)
    if code is not None:
        return None, code
    return bytes(buf), None


# ---------------------------------------------------------------------------------------- output validation
def _reject_constant(name: str):
    raise ValueError("non-finite JSON constant")


def parse_features_output(raw: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(parsed ok-answer, None) | (None, code). Codes: output_malformed | schema_mismatch | scorer:<error code> |
    scorer:error_code_invalid. A scorer-supplied error code is passed on only if it is a short token, so no data value can
    reach the refusal counts through it."""
    try:
        d = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError):
        return None, "output_malformed"
    if not isinstance(d, dict) or not isinstance(d.get("ok"), bool):
        return None, "schema_mismatch"
    if d["ok"] is False:
        if set(d) != {"ok", "error"}:
            return None, "schema_mismatch"
        e = d["error"]
        return None, "scorer:" + (e if isinstance(e, str) and _CODE.match(e) else "error_code_invalid")
    if set(d) != {"ok", "names", "values", "feature_schema_sha256", "bundle_manifest_sha256"}:
        return None, "schema_mismatch"
    names, values = d["names"], d["values"]
    if not (isinstance(names, list) and 0 < len(names) <= MAX_FEATURES and len(names) == len(set(names))
            and all(isinstance(n, str) and 0 < len(n) <= 200 for n in names)):
        return None, "schema_mismatch"
    if not (isinstance(values, list) and len(values) == len(names)):
        return None, "schema_mismatch"
    for v in values:
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            return None, "schema_mismatch"
    for k in ("feature_schema_sha256", "bundle_manifest_sha256"):
        if not (isinstance(d[k], str) and _HEX64.match(d[k])):
            return None, "schema_mismatch"
    return d, None


# --------------------------------------------------------------------------------------------- request build
def scorer_request_bytes(raw_response: bytes, vehicle_sha_prefix_hex: str, cutoff_date, cutoff_at) -> bytes:
    """The scorer's stdin for a stored snapshot: byte-identical to bundle_provider._scorer_input for the same booking
    (tested). `vehicle_sha_prefix_hex` = hex of the first 8 bytes of sha256(registration), computed in SQL so the
    registration never leaves the database."""
    if cutoff_at.tzinfo is None:
        raise ValueError("cutoff_at must be timezone-aware")
    from datetime import timezone
    req = SimpleNamespace(cutoff_date=cutoff_date, cutoff_at=cutoff_at)
    body = {
        "history": _raw_history(req, SimpleNamespace(raw=bytes(raw_response))),
        "cutoff_at": cutoff_at.astimezone(timezone.utc).isoformat(),
        "vehicle_id": str(int(vehicle_sha_prefix_hex, 16)),
    }
    return json.dumps(body, allow_nan=False, sort_keys=True).encode()


# ------------------------------------------------------------------------------------------------- the run
def _for_reference(kind: str, v: Any) -> Any:
    """Categorical model inputs reach the scorer's matrix as numeric codes. The reference binner stringifies a value, so an
    integral float is written as its integer ("3", not "3.0"). Documented assumption: the reference artefact names
    categorical codes by their integer decimal string (open decision)."""
    if kind == "categorical" and isinstance(v, float) and v.is_integer():
        return int(v)
    return v


class ReplayRun:
    """Accumulates one replay. `runner(stdin_bytes) -> (stdout bytes | None, code | None)`."""

    def __init__(self, runner: Callable[[bytes], Tuple[Optional[bytes], Optional[str]]], *, bundle_manifest_sha256: str,
                 feature_schema_sha256: str, unsupported_error_codes: Sequence[str]):
        for what, h in (("bundle_manifest_sha256", bundle_manifest_sha256),
                        ("feature_schema_sha256", feature_schema_sha256)):
            if not isinstance(h, str) or not _HEX64.match(h):
                raise ReplayRefused("pin_invalid", what)
        self._runner = runner
        self.manifest_pin = bundle_manifest_sha256
        self.schema_pin = feature_schema_sha256
        self.unsupported = frozenset(unsupported_error_codes)
        self.names: Optional[Tuple[str, ...]] = None
        self.refusals: Counter = Counter()

    def features(self, stdin_bytes: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """(feature mapping, None) | (None, code). Raises ReplayRefused for any mix or pin violation."""
        out, code = self._runner(stdin_bytes)
        if code is None:
            parsed, code = parse_features_output(out)
        if code is not None:
            self.refusals[code] += 1
            return None, code
        if parsed["bundle_manifest_sha256"] != self.manifest_pin:
            raise ReplayRefused("bundle_manifest_mismatch", "the scorer answered for a bundle other than the pinned one")
        if parsed["feature_schema_sha256"] != self.schema_pin:
            raise ReplayRefused("feature_schema_mismatch", "feature_schema_sha256 differs from the pinned value "
                                "(or between rows): rows of different schemas are never mixed")
        names = tuple(parsed["names"])
        if self.names is None:
            self.names = names
        elif names != self.names:
            raise ReplayRefused("feature_names_mismatch", "feature names differ between rows with one schema hash")
        return dict(zip(names, parsed["values"])), None


def build_g6_rows(candidates: Sequence[Mapping[str, Any]], run: ReplayRun, reference: ReferenceArtefact,
                  *, expected_bundle_sha256: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """candidates (one per booking, from reader.replay_candidates or a test): {booking_id, workshop_id, vehicle_id, state,
    score (float|None), score_bundle_sha256 (str|None), raw_response (bytes|None), vehicle_sha_prefix_hex, cutoff_date,
    cutoff_at}. Returns (rows for evaluate_g6, aggregate counts). Exclusions are counted by reason and never listed."""
    rows: List[Dict[str, Any]] = []
    excluded: Counter = Counter()
    kinds = {n: s["kind"] for n, s in reference.inputs.items()}
    n_replayed = 0
    for c in candidates:
        base = {"booking_id": c["booking_id"], "workshop_id": c["workshop_id"], "vehicle_id": c["vehicle_id"]}
        if c["state"] == "unsupported":                       # the app itself declared the booking out of design
            rows.append({**base, "supported": False, "features": {}, "score": None})
            continue
        if c["raw_response"] is None:
            excluded["no_raw_snapshot"] += 1
            continue
        stored = c["score"]
        if stored is not None and c["score_bundle_sha256"] != expected_bundle_sha256:
            excluded["score_from_other_bundle"] += 1          # stored score and replayed input must come from one bundle
            continue
        try:
            stdin = scorer_request_bytes(c["raw_response"], c["vehicle_sha_prefix_hex"], c["cutoff_date"], c["cutoff_at"])
        except (ValueError, TypeError, KeyError, AttributeError):
            excluded["request_not_buildable"] += 1
            continue
        n_replayed += 1
        feats, code = run.features(stdin)
        if code is not None:
            if code.startswith("scorer:") and code[len("scorer:"):] in run.unsupported:
                if stored is not None:
                    excluded["replay_disagrees_with_stored_score"] += 1
                else:
                    rows.append({**base, "supported": False, "features": {}, "score": None})
            else:
                excluded["replay_refused"] += 1
            continue
        if stored is None:
            excluded["no_stored_score"] += 1
            continue
        if run.names is not None and set(run.names) != set(reference.inputs):
            raise ReplayRefused("reference_inputs_differ_from_scorer_schema",
                                "the reference artefact's inputs are not exactly the scorer's input names")
        rows.append({**base, "supported": True, "score": stored,
                     "features": {n: _for_reference(kinds[n], v) for n, v in feats.items()}})
    return rows, {"n_candidates": len(candidates), "n_replayed": n_replayed,
                  "excluded_by_reason": dict(sorted(excluded.items())),
                  "refusals_by_code": dict(sorted(run.refusals.items()))}


def evaluate_replay(candidates: Sequence[Mapping[str, Any]], runner, *, reference: ReferenceArtefact,
                    bundle_manifest_sha256: str, feature_schema_sha256: str, unsupported_error_codes: Sequence[str],
                    outcome_access_start_utc: str, population: str, n_boot: int, seed: int, alpha: float,
                    replay_info: Mapping[str, Any]) -> Dict[str, Any]:
    """G6 on replayed rows. Every argument is required. Returns evaluate_g6's dict plus a `replay` block (aggregates only)."""
    run = ReplayRun(runner, bundle_manifest_sha256=bundle_manifest_sha256, feature_schema_sha256=feature_schema_sha256,
                    unsupported_error_codes=unsupported_error_codes)
    rows, counts = build_g6_rows(candidates, run, reference, expected_bundle_sha256=bundle_manifest_sha256)
    if run.names is not None and set(run.names) != set(reference.inputs):
        raise ReplayRefused("reference_inputs_differ_from_scorer_schema",
                            "the reference artefact's inputs are not exactly the scorer's input names")
    res = evaluate_g6(rows, reference, outcome_access_start_utc=outcome_access_start_utc, population=population,
                      n_boot=n_boot, seed=seed, alpha=alpha)
    res["replay"] = {**dict(replay_info), **counts, "bundle_manifest_sha256": bundle_manifest_sha256,
                     "feature_schema_sha256": feature_schema_sha256, "n_supported_rows": sum(1 for r in rows if r["supported"]),
                     "n_input_names": len(run.names) if run.names is not None else None}
    return res
