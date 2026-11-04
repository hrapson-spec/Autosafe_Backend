"""Stdlib re-implementation of the BundleV1 integrity checks (rev3 spine/bundle.py read_bundle_v1), so qual_v2 needs no rev3 import at serve time.
tests/test_crosscheck_rev3.py runs both readers on the same directories and requires identical accept/refuse decisions."""
from __future__ import annotations
from pathlib import Path
from .common import QualError, bytes_sha256, canonical_hash, file_sha256, inside, is_sha, is_bool, parse_json_bytes, read_bytes_checked

REQUIRED = ("bundle_version", "arm", "model_version", "policy_version", "qualified", "display_state", "integrated_freeze_sha256", "dag_node_ids", "files",
            "runtime", "scorer", "schema_sha256", "synthetic_fixture", "manifest_content_sha256")


def model_identity_of(m):
    """Identity of the FITTED artefacts: arm, model version, freeze, schema, DAG nodes and every listed file hash. Excludes the scorer, source paths, runtime and
    policy_version, so a repackaging (step 8) that leaves the fitted bytes alone keeps the identity and needs only a serving-parity receipt."""
    return canonical_hash({"arm": m["arm"], "model_version": m["model_version"], "integrated_freeze_sha256": m["integrated_freeze_sha256"],
                           "schema_sha256": m["schema_sha256"], "dag_node_ids": m["dag_node_ids"], "files": m["files"]})


def read_base_v1(directory, *, verify_files=True):
    """Return dict(manifest, manifest_file_sha256, manifest_content_sha256, model_identity_sha256). Refuses anything read_bundle_v1 refuses."""
    d = Path(directory).resolve()
    mpath = inside(d, "manifest.json")
    mbytes = read_bytes_checked(mpath, what="manifest.json")
    m = parse_json_bytes(mbytes, what="manifest.json")
    if not isinstance(m, dict) or any(k not in m for k in REQUIRED):
        raise QualError("base_manifest_invalid", "missing mandatory field")
    if m["bundle_version"] != "BundleV1":
        raise QualError("base_version_unknown", str(m["bundle_version"])[:40])
    if m["qualified"] is not False or m["display_state"] != "shadow":
        raise QualError("base_not_shadow", "the assessed base must be an unqualified shadow BundleV1")
    if not is_bool(m["synthetic_fixture"]):
        raise QualError("base_manifest_invalid", "synthetic_fixture")
    for k in ("manifest_content_sha256", "schema_sha256", "integrated_freeze_sha256"):
        if not is_sha(m[k]):
            raise QualError("base_manifest_invalid", k)
    if not isinstance(m["files"], dict) or not isinstance(m["dag_node_ids"], list) or not isinstance(m["runtime"], dict):
        raise QualError("base_manifest_invalid", "types")
    if canonical_hash({k: v for k, v in m.items() if k != "manifest_content_sha256"}) != m["manifest_content_sha256"]:
        raise QualError("base_hash_mismatch", "manifest content hash")
    sc = m["scorer"]
    if not isinstance(sc, dict) or not all(isinstance(sc.get(k), str) for k in ("path", "sha256", "runtime_executable")):
        raise QualError("base_manifest_invalid", "scorer block")
    inventory = {}
    if "selfcontained" in m:                           # SelfContainedV1 (selfcontained_v1 packager): a BundleV1 superset that lists EVERY file of the bundle (vendored sources, tz, runtime pins)
        sc_block = m["selfcontained"]
        inv = sc_block.get("inventory") if isinstance(sc_block, dict) else None
        if not isinstance(inv, dict) or not inv or not all(is_sha(v) for v in inv.values()):
            raise QualError("base_manifest_invalid", "selfcontained.inventory")
        for rel, expected in m["files"].items():
            if inv.get(rel) != expected:
                raise QualError("base_manifest_invalid", "files entry not in the selfcontained inventory: " + rel[:80])
        if inv.get(sc["path"]) != sc["sha256"]:
            raise QualError("base_manifest_invalid", "scorer entry not in the selfcontained inventory")
        inventory = dict(inv)
    if verify_files:
        for rel, expected in inventory.items():            # every vendored file, not only the model files: the launcher refuses the bundle if one differs
            if file_sha256(inside(d, rel)) != expected:
                raise QualError("base_file_hash_mismatch", rel[:80])
        for rel, expected in m["files"].items():
            if not is_sha(expected) or file_sha256(inside(d, rel)) != expected:
                raise QualError("base_file_hash_mismatch", rel[:80])
        if file_sha256(inside(d, sc["path"])) != sc["sha256"]:
            raise QualError("base_scorer_hash_mismatch", sc["path"][:80])
    return {"manifest": m, "manifest_file_sha256": bytes_sha256(mbytes), "manifest_content_sha256": m["manifest_content_sha256"],
            "model_identity_sha256": model_identity_of(m), "directory": d, "inventory": inventory}
