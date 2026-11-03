"""BundleV1 provider (INTERFACE_SCORER_V1): verifies a model bundle and runs its scorer as a subprocess.

The BundleV1 manifest is unqualified by construction (`qualified` must be false), so by default the provider never
returns a percentage, band or rank score. A successful scoring is reported as `unavailable` under a `shadow`
DisplayPolicy and carries the raw margin/probability in `ShadowProviderResult` for server-side persistence only.
Every failure becomes `ProviderResult(state="unavailable", reason=<specific code>)`; `assess()` never raises.

Qualified mode (route-to-live step 9): when the directory also holds a QUALIFIED_V2.json sidecar that verifies against the
out-of-band `pin` (qual_v2, synthetic bundles refused unless `allow_synthetic`), AND a `rounding_reading` is configured, a
successful scoring is reported as `available` under the bundle's qualified DisplayPolicy, with the rounded percentage, the
band and the unrounded `rank_score`. Any qualification problem (missing sidecar/pin/reading, QualError, unexpected exception)
leaves the provider in shadow mode and logs only the error code. The sidecar is re-verified (evidence + receipt + pin, not the
model files, which the scorer hashes itself) on every call, so a bundle changed on disk degrades to shadow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pathlib
import re
import signal
import subprocess
from dataclasses import dataclass, replace
from datetime import timezone

from ..display_values import READINGS, rank_score, round_percent
from ..dvsa import _parse_dt
from ..provider import STUB_DISPLAY_POLICY, AssessmentRequest, ProviderResult
from ..timeutil import london_date
from .qualification import QualifiedDisplay, load_qualification

log = logging.getLogger("garage.provider.bundle")

DEFAULT_TIMEOUT_S = 10.0
MAX_STDOUT_BYTES = 65536
BUNDLE_VERSION = "BundleV1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_REQUIRED = ("bundle_version", "arm", "model_version", "policy_version", "qualified", "display_state",
             "integrated_freeze_sha256", "dag_node_ids", "files", "runtime", "scorer", "schema_sha256",
             "synthetic_fixture", "manifest_content_sha256")

REASON_TEXT = {
    "model_not_qualified": "The model is running in shadow mode only: no estimate is shown and nothing is ranked.",
    "bundle_manifest_invalid": "The model bundle manifest is missing or invalid.",
    "bundle_hash_mismatch": "The model bundle manifest failed its integrity check.",
    "scorer_hash_mismatch": "The model scorer failed its integrity check.",
    "scorer_launch_failed": "The model scorer could not be started.",
    "scorer_timeout": "The model scorer did not answer in time.",
    "scorer_exit_nonzero": "The model scorer failed.",
    "scorer_output_malformed": "The model scorer returned unreadable output.",
    "scorer_schema_mismatch": "The model scorer returned an unexpected result shape.",
    "scorer_bundle_mismatch": "The model scorer answered for a different bundle.",
    "scorer_refused": "The model scorer declined to assess this booking.",   # fallback; see _parse
    "scorer_input_invalid": "The booking history could not be prepared for the model scorer.",
}


class BundleError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class ShadowProviderResult(ProviderResult):
    """A ProviderResult that also carries server-only shadow values. Never serialised to a client."""
    shadow_margin: float | None = None
    shadow_probability: float | None = None
    shadow_bundle_sha256: str | None = None
    shadow_model_version: str | None = None


def canonical_hash(value) -> str:
    """Same canonical form as T2c/rev3/contracts.canonical_hash."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class VerifiedBundle:
    directory: pathlib.Path
    manifest: dict
    runtime_executable: str
    scorer_path: pathlib.Path

    @property
    def manifest_sha256(self) -> str:
        return self.manifest["manifest_content_sha256"]

    @property
    def model_version(self) -> str:
        return self.manifest["model_version"]


def verify_bundle(directory) -> VerifiedBundle:
    """Raises BundleError(code) unless the manifest, its content hash and the scorer file all verify.
    (The scorer itself verifies every `files` hash before loading models: INTERFACE_SCORER_V1.)"""
    d = pathlib.Path(directory)
    try:
        d = d.resolve(strict=True)
        raw = (d / "manifest.json").read_bytes()
        m = json.loads(raw)
    except (OSError, ValueError) as e:
        raise BundleError("bundle_manifest_invalid", type(e).__name__)
    if not isinstance(m, dict) or any(k not in m for k in _REQUIRED):
        raise BundleError("bundle_manifest_invalid", "missing fields")
    if m["bundle_version"] != BUNDLE_VERSION:
        raise BundleError("bundle_manifest_invalid", "unknown bundle_version")
    if m["qualified"] is not False:
        raise BundleError("bundle_manifest_invalid", "qualified must be false")
    if m["display_state"] != "shadow":
        raise BundleError("bundle_manifest_invalid", "display_state must be shadow")
    for k in ("model_version", "policy_version"):
        if not isinstance(m[k], str) or not m[k]:
            raise BundleError("bundle_manifest_invalid", f"{k} must be a non-empty string")
    sc, rt = m["scorer"], m["runtime"]
    if not isinstance(sc, dict) or not isinstance(sc.get("path"), str) or not isinstance(sc.get("sha256"), str) \
            or not isinstance(sc.get("runtime_executable"), str) or not isinstance(rt, dict):
        raise BundleError("bundle_manifest_invalid", "scorer block")
    claimed = m["manifest_content_sha256"]
    if not isinstance(claimed, str) or not _SHA.match(claimed):
        raise BundleError("bundle_manifest_invalid", "manifest_content_sha256 format")
    try:
        actual = canonical_hash({k: v for k, v in m.items() if k != "manifest_content_sha256"})
    except (ValueError, TypeError):
        raise BundleError("bundle_manifest_invalid", "manifest not canonicalisable")
    if actual != claimed:
        raise BundleError("bundle_hash_mismatch", "manifest content hash")
    sp = pathlib.Path(sc["path"])
    sp = sp if sp.is_absolute() else d / sp
    try:
        sp = sp.resolve(strict=True)
    except OSError:
        raise BundleError("bundle_manifest_invalid", "scorer path missing")
    if d not in sp.parents:
        raise BundleError("bundle_manifest_invalid", "scorer path escapes the bundle")
    exe = sc["runtime_executable"]
    if not os.path.isabs(exe) or not os.access(exe, os.X_OK):
        raise BundleError("bundle_manifest_invalid", "runtime_executable must be an absolute executable path")
    if not _SHA.match(sc["sha256"]) or _file_sha256(sp) != sc["sha256"]:
        raise BundleError("scorer_hash_mismatch", "scorer sha256")
    return VerifiedBundle(directory=d, manifest=m, runtime_executable=exe, scorer_path=sp)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


_PLATE_KEYS = ("registration", "registrationAtTimeOfTest")


def _raw_history(req: AssessmentRequest, hist) -> dict:
    """D-D: the stored raw DVSA response, cut to tests strictly before the frozen cutoff (R10) with plates removed.
    A raw test whose date cannot be parsed is dropped (it cannot be shown to precede the cutoff)."""
    data = json.loads(hist.raw.decode("utf-8"))
    if isinstance(data, list):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("raw response is not an object")
    kept = []
    for t in data.get("motTests") or []:
        try:
            at = _parse_dt(t["completedDate"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if london_date(at) < req.cutoff_date and at < req.cutoff_at:
            kept.append({k: v for k, v in t.items() if k not in _PLATE_KEYS})
    return {**{k: v for k, v in data.items() if k not in _PLATE_KEYS}, "motTests": kept}


def _normalised_history(hist) -> dict:
    iso = lambda x: x.isoformat() if x is not None else None   # noqa: E731
    return {
        "make": hist.make, "model": hist.model, "fuelType": hist.fuel_type, "engineSize": hist.engine_size,
        "primaryColour": hist.colour,
        "firstUsedDate": iso(hist.first_used_date), "registrationDate": iso(hist.registration_date),
        "manufactureDate": iso(hist.manufacture_date),
        "motTests": [{"completedDate": t.completed_at.astimezone(timezone.utc).isoformat(),
                      "testResult": t.result, "motTestNumber": t.test_number,
                      "odometerValue": t.odometer_value, "odometerUnit": t.odometer_unit,
                      "odometerResultType": t.odometer_result_type, "dataSource": t.data_source,
                      "expiryDate": iso(t.expiry_date), "defects": [dict(x) for x in t.defects]}
                     for t in hist.tests],
    }


def _scorer_input(req: AssessmentRequest) -> bytes:
    """Scorer stdin. DVSA-shaped (`motTests`), tests strictly before the frozen cutoff, no registration
    plate: the vehicle is an opaque canonical-decimal id. Built from the stored raw response when the history
    has one (so serving sees the fields the API returned), else from the normalised history."""
    if req.cutoff_at.tzinfo is None:
        raise ValueError("cutoff_at must be timezone-aware")
    hist = req.history.before(req.cutoff_date, req.cutoff_at)   # idempotent guard (R10)
    vid = int.from_bytes(hashlib.sha256(req.registration.encode()).digest()[:8], "big")
    body = {
        "history": _raw_history(req, hist) if getattr(hist, "raw", None) is not None else _normalised_history(hist),
        "cutoff_at": req.cutoff_at.astimezone(timezone.utc).isoformat(),
        "vehicle_id": str(vid),
    }
    return json.dumps(body, allow_nan=False, sort_keys=True).encode()


class BundleProvider:
    """Shadow-only provider over a verified BundleV1 directory."""
    name = "bundle"

    def __init__(self, bundle_dir, timeout_s: float = DEFAULT_TIMEOUT_S, *, pin: str | None = None,
                 rounding_reading: str | None = None, allow_synthetic: bool = False):
        self._dir = pathlib.Path(bundle_dir)
        self.timeout_s = float(timeout_s)
        vb = verify_bundle(self._dir)           # BundleError here => caller falls back to the stub
        self.version = vb.model_version
        self.model_version = vb.model_version
        self.manifest_sha256 = vb.manifest_sha256
        self._pin = pin
        self._reading = rounding_reading
        self._allow_synthetic = bool(allow_synthetic)
        self.qualified = False
        self.qualification_code: str | None = None
        self._qual, self.qualification_code = self._qualification(verify_base_files=True)
        self.qualified = self._qual is not None
        if self._qual is None and self.qualification_code != "no_sidecar":
            log.error("bundle not qualified (%s): serving shadow only", self.qualification_code)

    def _qualification(self, verify_base_files: bool) -> tuple[QualifiedDisplay | None, str | None]:
        qd, code = load_qualification(self._dir, pin=self._pin, allow_synthetic=self._allow_synthetic,
                                      verify_base_files=verify_base_files)
        if qd is None:
            return None, code
        if self._reading not in READINGS:     # no silent default (open decision O9)
            return None, "rounding_reading_missing" if not self._reading else "rounding_reading_invalid"
        if qd.model_version != self.model_version or qd.policy.model_version != self.model_version:
            return None, "model_version_mismatch"
        return qd, None

    # -- result helpers -------------------------------------------------
    def _unavailable(self, reason: str, text: str | None = None) -> ProviderResult:
        return ProviderResult(state="unavailable", reason=reason, reason_text=text or REASON_TEXT[reason],
                              provider_name=self.name, provider_version=self.version,
                              display_policy=replace(STUB_DISPLAY_POLICY, display_state="unavailable"))

    def assess(self, req: AssessmentRequest) -> ProviderResult:
        try:
            return self._assess(req)
        except Exception:    # last line of defence: the worker must never see an exception from here
            log.exception("bundle provider internal error")
            return self._unavailable("scorer_launch_failed")

    def _assess(self, req: AssessmentRequest) -> ProviderResult:
        try:
            vb = verify_bundle(self._dir)       # re-verified per call: manifest + scorer file may change on disk
        except BundleError as e:
            return self._unavailable(e.code if e.code in REASON_TEXT else "bundle_manifest_invalid")
        if vb.manifest_sha256 != self.manifest_sha256:
            return self._unavailable("bundle_hash_mismatch")
        try:
            stdin = _scorer_input(req)
        except (ValueError, TypeError, AttributeError):
            return self._unavailable("scorer_input_invalid")
        cmd = [vb.runtime_executable, "-I", str(vb.scorer_path), "--bundle", str(vb.directory)]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    cwd=str(vb.directory), env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                                    start_new_session=True)
        except OSError:
            return self._unavailable("scorer_launch_failed")
        try:
            out, _ = proc.communicate(stdin, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            return self._unavailable("scorer_timeout")
        except OSError:
            self._kill(proc)
            return self._unavailable("scorer_launch_failed")
        if proc.returncode != 0:
            return self._unavailable("scorer_exit_nonzero")
        return self._parse(vb, out)

    @staticmethod
    def _kill(proc) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass

    def _parse(self, vb: VerifiedBundle, out: bytes) -> ProviderResult:
        if len(out) > MAX_STDOUT_BYTES:
            return self._unavailable("scorer_output_malformed")
        try:
            d = json.loads(out.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError):
            return self._unavailable("scorer_output_malformed")
        if not isinstance(d, dict) or not isinstance(d.get("ok"), bool):
            return self._unavailable("scorer_schema_mismatch")
        reasons = d.get("support_reasons")
        if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
            return self._unavailable("scorer_schema_mismatch")
        if d["ok"] is False:
            if set(d) != {"ok", "error_code", "support_reasons"} or not isinstance(d["error_code"], str) \
                    or not _CODE.match(d["error_code"]):
                return self._unavailable("scorer_schema_mismatch")
            # Out-of-design / gated histories: the scorer's own error_code is the reason (never one of ours).
            code = d["error_code"]
            return self._unavailable("scorer_refused" if code in REASON_TEXT else code,
                                     f"The model scorer could not assess this booking ({code}).")
        if set(d) != {"ok", "margin", "probability", "support_reasons", "bundle_manifest_sha256", "model_version"} \
                or not _num(d["margin"]) or not _num(d["probability"]) or not 0.0 <= d["probability"] <= 1.0 \
                or not isinstance(d["bundle_manifest_sha256"], str) or not isinstance(d["model_version"], str):
            return self._unavailable("scorer_schema_mismatch")
        if d["bundle_manifest_sha256"] != vb.manifest_sha256 or d["model_version"] != vb.model_version:
            return self._unavailable("scorer_bundle_mismatch")
        qd = self._current_qualification()
        if qd is not None:
            return self._qualified_result(qd, float(d["probability"]))
        policy = replace(STUB_DISPLAY_POLICY, display_state="shadow", provisional=True,
                         policy_version=vb.manifest["policy_version"], model_version=vb.model_version,
                         suppression_reason="model_not_qualified", operational_qualified=False,
                         ranking_enabled=False)
        return ShadowProviderResult(
            state="unavailable", reason="model_not_qualified", reason_text=REASON_TEXT["model_not_qualified"],
            provider_name=self.name, provider_version=self.version, display_policy=policy,
            shadow_margin=float(d["margin"]), shadow_probability=float(d["probability"]),
            shadow_bundle_sha256=vb.manifest_sha256, shadow_model_version=vb.model_version)

    def _current_qualification(self) -> QualifiedDisplay | None:
        """Per-call re-check (small evidence files; model files are hashed by the scorer). A change on disk degrades to shadow."""
        if self._qual is None:
            return None
        qd, code = self._qualification(verify_base_files=False)
        if qd is None or qd.receipt_sha256 != self._qual.receipt_sha256:
            log.error("bundle qualification lost (%s): serving shadow only", code or "receipt_changed")
            return None
        return qd

    def _qualified_result(self, qd: QualifiedDisplay, p: float) -> ProviderResult:
        pol = qd.policy
        try:
            pct = round_percent(p, self._reading, pol.rounding_pp)
            score = rank_score(p)
        except ValueError:
            return self._unavailable("scorer_schema_mismatch")
        return ProviderResult(state="available", percent_rounded=pct, band=pol.band_for(pct), provider_name=self.name,
                              provider_version=self.version, display_policy=pol, rank_score=score)


def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name}")
