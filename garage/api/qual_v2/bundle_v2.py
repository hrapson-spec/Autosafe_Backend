"""QualifiedBundle v2: writer, reader, verifier.

Layout (a BundleV1 directory, unchanged, plus):
    manifest.json, scorer/, <listed model files>      byte-identical to the assessed BundleV1 (still qualified:false / shadow to every v1 reader and the scorer)
    qualification/receipt.json + the evidence files    every file hash-listed in the sidecar
    QUALIFIED_V2.json                                  the commit marker, written last; the only place 'qualified: true' exists

Fail-closed contract: read_bundle_v2 returns a QualifiedBundle only when every check passes, otherwise raises QualError. try_read_bundle_v2 returns
(None, code) instead of raising. A directory with no sidecar, or any sidecar problem, is simply 'not qualified': callers fall back to the v1 shadow path.
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile
from pathlib import Path
from . import FORMAT
from .base_v1 import read_base_v1
from .common import (QualError, canonical_hash, file_sha256, inside, mkdir_private, parse_json_bytes, read_bytes_checked, read_json_file, write_private)
from .verify import check_sidecar, check_struct, verify_receipt

SIDECAR = "QUALIFIED_V2.json"
QDIR = "qualification"
ROLE_FILES = {   # role -> file name inside qualification/
    "rules_approval": "rules_approval.json", "display_approval": "display_approval.json", "pseudo_future_2023": "pseudo_future_2023.json",
    "usefulness_gate": "usefulness_gate.json", "assessment_2024_provenance": "assessment_2024_provenance.json", "assessment_2024_report": "assessment_2024_report.json",
    "independent_recomputation": "independent_recomputation.json", "live_qualification": "live_qualification.json",
    "read_2024_entry_receipt": "read_2024_entry_receipt.json", "read_2024_registry_records": "read_2024_registry_records.jsonl",
    "band_evidence": "band_evidence.json", "g6_shadow": "g6_shadow.json", "serving_parity": "serving_parity.json",
    # READ entries 2..4 (rules.read_2024.entries): each registered 2024 READ scope has its own entry receipt and its own READ_START/READ_END lines
    "read_2024_entry_receipt_2": "read_2024_entry_receipt_2.json", "read_2024_registry_records_2": "read_2024_registry_records_2.jsonl",
    "read_2024_entry_receipt_3": "read_2024_entry_receipt_3.json", "read_2024_registry_records_3": "read_2024_registry_records_3.jsonl",
    "read_2024_entry_receipt_4": "read_2024_entry_receipt_4.json", "read_2024_registry_records_4": "read_2024_registry_records_4.jsonl"}
OPTIONAL_ROLES = ("band_evidence", "g6_shadow", "serving_parity", "read_2024_entry_receipt_2", "read_2024_registry_records_2",
                  "read_2024_entry_receipt_3", "read_2024_registry_records_3", "read_2024_entry_receipt_4", "read_2024_registry_records_4")


def sidecar_hash(sc):
    return canonical_hash({k: v for k, v in sc.items() if k != "sidecar_content_sha256"})


def derive_app_policy(display, model_version):
    """The DisplayPolicy.to_json() dict the app builds from a verified receipt."""
    return {"rounding_pp": display["rounding_pp"], "band_edges": list(display["band_edges"]), "band_labels": dict(display["band_labels"]), "provisional": False,
            "display_state": display["display_state"], "policy_version": display["policy_version"], "model_version": model_version, "suppression_reason": None,
            "operational_qualified": True, "ranking_enabled": display["ranking_enabled"]}


def build_receipt(base, evidence_paths, *, receipt_id, issued_at_utc, issued_by, assessed_base_manifest_sha256=None):
    """Bind evidence files (role -> source Path) to the base bundle. Decides nothing: verification is verify_receipt's job. Returns (receipt dict, {name: Path}).
    assessed_base_manifest_sha256: the v1 manifest hash that was actually assessed on 2024; defaults to this base. Pass the earlier value when re-issuing for a
    repackaged bundle (the verifier then demands a serving-parity receipt)."""
    unknown = set(evidence_paths) - set(ROLE_FILES)
    required = [r for r in ROLE_FILES if r not in OPTIONAL_ROLES]
    if unknown or any(r not in evidence_paths for r in required):
        raise QualError("evidence_roles", "unknown %s / missing %s" % (sorted(unknown), [r for r in required if r not in evidence_paths]))
    evidence, files = {}, {}
    for role, src in evidence_paths.items():
        name = ROLE_FILES[role]
        evidence[role] = {"path": f"{QDIR}/{name}", "sha256": file_sha256(src)}
        files[name] = Path(src)
    rules = read_json_file(evidence_paths["rules_approval"], what="rules_approval")
    dispa = read_json_file(evidence_paths["display_approval"], what="display_approval")
    m = base["manifest"]
    try:
        amendment, display, synth = rules["protocol"]["amendment_sha256"], dispa["display"], bool(m["synthetic_fixture"])
    except (KeyError, TypeError):
        raise QualError("evidence_roles", "approvals unreadable")
    receipt = {"kind": "GARAGE_QUALIFICATION_RECEIPT_V1", "receipt_version": 1, "receipt_id": receipt_id, "issued_at_utc": issued_at_utc, "issued_by": issued_by,
               "synthetic_fixture": synth,
               "subject": {"arm": m["arm"], "model_version": m["model_version"], "integrated_freeze_sha256": m["integrated_freeze_sha256"],
                           "model_identity_sha256": base["model_identity_sha256"],
                           "assessed_base_manifest_sha256": assessed_base_manifest_sha256 or base["manifest_content_sha256"]},
               "protocol": {"amendment_sha256": amendment}, "display": display, "evidence": evidence}
    return receipt, files


def write_bundle_v2(out_dir, *, base_bundle_dir, evidence_paths, receipt_id, issued_at_utc, issued_by, allow_synthetic=False, registry_path=None, assessed_base_manifest_sha256=None):
    """Assemble a QualifiedBundle v2 in a staging directory, run the FULL verifier on it, and only then move it into place. Returns (dir, sidecar, pin) where pin is the
    receipt content hash the owner must record out-of-band (the app accepts the bundle only against it). On any refusal nothing is left at out_dir.
    assessed_base_manifest_sha256: see build_receipt (re-issue after repackaging)."""
    out = Path(out_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise QualError("bundle_exists", "refusing a non-empty output directory")
    base = read_base_v1(base_bundle_dir)
    receipt, qfiles = build_receipt(base, evidence_paths, receipt_id=receipt_id, issued_at_utc=issued_at_utc, issued_by=issued_by,
                                    assessed_base_manifest_sha256=assessed_base_manifest_sha256)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".stage_qv2_", dir=str(out.parent)))
    try:
        os.chmod(str(stage), 0o700)
        src = base["directory"]
        m = base["manifest"]
        for rel in sorted(set(m["files"]) | set(base["inventory"]) | {m["scorer"]["path"], "manifest.json"}):          # byte-identical copy of exactly the bound files (a self-contained bundle: all of its inventory)
            dst = stage / rel
            mkdir_private(dst.parent)
            write_private(dst, inside(src, rel).read_bytes())
        mkdir_private(stage / QDIR)
        listed = {}
        for name, p in qfiles.items():
            write_private(stage / QDIR / name, Path(p).read_bytes())
            listed[f"{QDIR}/{name}"] = file_sha256(stage / QDIR / name)
        rbytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        write_private(stage / QDIR / "receipt.json", rbytes)
        listed[f"{QDIR}/receipt.json"] = file_sha256(stage / QDIR / "receipt.json")
        pin = canonical_hash(receipt)
        sc = {"bundle_version": FORMAT, "qualified": True, "synthetic_fixture": bool(m["synthetic_fixture"]),
              "base": {"bundle_version": "BundleV1", "manifest_file_sha256": base["manifest_file_sha256"], "manifest_content_sha256": base["manifest_content_sha256"],
                       "arm": m["arm"], "model_version": m["model_version"], "integrated_freeze_sha256": m["integrated_freeze_sha256"],
                       "model_identity_sha256": base["model_identity_sha256"]},
              "display_state": receipt["display"]["display_state"], "ranking_enabled": receipt["display"]["ranking_enabled"],
              "display_policy": derive_app_policy(receipt["display"], m["model_version"]),
              "qualification": {"receipt": {"path": f"{QDIR}/receipt.json", "content_sha256": pin}, "files": listed}}
        sc["sidecar_content_sha256"] = sidecar_hash(sc)
        write_private(stage / SIDECAR, (json.dumps(sc, indent=2, sort_keys=True) + "\n").encode())      # last: the commit marker
        read_bundle_v2(stage, pin=pin, allow_synthetic=allow_synthetic, registry_path=registry_path)       # full verification before the bundle exists at out_dir
        if out.exists():
            out.rmdir()
        os.rename(str(stage), str(out))
        return out, sc, pin
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise


class QualifiedBundle:
    def __init__(self, directory, sidecar, receipt, base, verified):
        self.directory, self.sidecar, self.receipt, self.base, self.verified = directory, sidecar, receipt, base, verified

    @property
    def display_policy(self):
        return dict(self.sidecar["display_policy"])

    @property
    def receipt_sha256(self):
        return self.verified.receipt_sha256

    @property
    def checks(self):
        return list(self.verified.checks)


def read_bundle_v2(directory, *, pin=None, allow_unpinned=False, allow_synthetic=False, registry_path=None, verify_base_files=True):
    """Verify and open a QualifiedBundle v2. Raises QualError on ANY problem; returns QualifiedBundle otherwise.
    pin: the receipt content hash recorded out-of-band by the owner (required unless allow_unpinned, which is for tests and lead-side issuance checks only).
    registry_path: when given (issuance level), the embedded READ_START/READ_END lines must be the registry's own and unrevoked."""
    d = Path(directory).resolve()
    sc = read_json_file(inside(d, SIDECAR), what=SIDECAR)
    check_sidecar(sc)
    if sidecar_hash(sc) != sc["sidecar_content_sha256"]:
        raise QualError("sidecar_hash_mismatch", "")
    base = read_base_v1(d, verify_files=verify_base_files)
    b = sc["base"]
    for k, want in (("manifest_file_sha256", base["manifest_file_sha256"]), ("manifest_content_sha256", base["manifest_content_sha256"]),
                    ("arm", base["manifest"]["arm"]), ("model_version", base["manifest"]["model_version"]),
                    ("integrated_freeze_sha256", base["manifest"]["integrated_freeze_sha256"]), ("model_identity_sha256", base["model_identity_sha256"])):
        if b[k] != want:
            raise QualError("base_mismatch", "sidecar.base." + k)
    if sc["synthetic_fixture"] is not base["manifest"]["synthetic_fixture"]:
        raise QualError("synthetic_mismatch", "sidecar vs base")
    listed = sc["qualification"]["files"]
    qdir = d / QDIR
    if qdir.is_symlink() or not qdir.is_dir():
        raise QualError("qualification_missing", "")
    on_disk = {f"{QDIR}/{p.name}" for p in qdir.iterdir()}
    if on_disk != set(listed):
        raise QualError("qualification_files_differ", "files on disk differ from the sidecar list")
    for rel, expected in listed.items():
        if file_sha256(inside(d, rel)) != expected:
            raise QualError("qualification_file_hash_mismatch", rel)
    rpath = sc["qualification"]["receipt"]["path"]
    if rpath not in listed:
        raise QualError("evidence_not_listed", "receipt")
    receipt = parse_json_bytes(read_bytes_checked(inside(d, rpath), listed[rpath], what="receipt"), what="receipt")
    check_struct(receipt, "receipt")
    if canonical_hash(receipt) != sc["qualification"]["receipt"]["content_sha256"]:
        raise QualError("receipt_hash_mismatch", "")
    for role, ref in (receipt.get("evidence") or {}).items():
        if not isinstance(ref, dict) or listed.get(ref.get("path")) != ref.get("sha256"):
            raise QualError("evidence_not_listed", str(role)[:40])
    ver = verify_receipt(d, receipt, base=base, allow_synthetic=allow_synthetic, pin=pin, allow_unpinned=allow_unpinned, registry_path=registry_path)
    if sc["display_policy"] != derive_app_policy(receipt["display"], base["manifest"]["model_version"]) \
            or sc["display_state"] != receipt["display"]["display_state"] or sc["ranking_enabled"] != receipt["display"]["ranking_enabled"]:
        raise QualError("display_mismatch", "sidecar display_policy is not the one derived from the receipt")
    ver.ok("sidecar_display_policy_derived_from_receipt")
    return QualifiedBundle(d, sc, receipt, base, ver)


def try_read_bundle_v2(directory, **kw):
    """(bundle, None) or (None, error_code). Never raises for a refusal; an unexpected exception is also 'not qualified'."""
    try:
        return read_bundle_v2(directory, **kw), None
    except QualError as e:
        return None, e.code
    except Exception as e:      # noqa: BLE001 - fail closed
        return None, "unexpected_" + type(e).__name__
