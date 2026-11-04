"""The vendored copy of T2c/qual_v2 (route-to-live step 7) is byte-identical to the original. VENDOR_SHA256.json (committed with the app)
records the original hashes, so drift is caught even where T2c is not on disk; when the original tree IS present it is compared too."""
import hashlib
import json
import os
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "qual_v2_fixtures"
VENDORED = HERE.parent / "qual_v2"
MANIFEST = json.loads((FIX / "VENDOR_SHA256.json").read_text())["files"]


def local(rel: str) -> pathlib.Path:
    return FIX / rel[len("tests/"):] if rel.startswith("tests/") else VENDORED / rel


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _t2c():
    env = os.environ.get("GARAGE_T2C_QUAL_V2")
    for c in ([pathlib.Path(env)] if env else []) + [HERE.parents[3] / "T2c" / "qual_v2"]:
        if (c / "bundle_v2.py").is_file():
            return c
    return None


def test_manifest_is_complete():
    assert len(MANIFEST) == 47
    for must in ("bundle_v2.py", "verify.py", "common.py", "base_v1.py", "QUALIFIED_BUNDLE_V2.schema.json", "tests/SAMPLE_PINS.json"):
        assert must in MANIFEST


@pytest.mark.parametrize("rel", sorted(MANIFEST))
def test_vendored_file_equals_recorded_original(rel):
    assert sha(local(rel)) == MANIFEST[rel]


def test_no_unlisted_files_in_vendored_trees():
    def files(root):
        return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"}
    assert files(VENDORED) == {k for k in MANIFEST if not k.startswith("tests/")}
    assert files(FIX) == {k[len("tests/"):] for k in MANIFEST if k.startswith("tests/")} | {"VENDOR_SHA256.json"}


def test_vendored_files_equal_the_t2c_originals_when_present():
    src = _t2c()
    if src is None:
        pytest.skip("T2c/qual_v2 not on disk (set GARAGE_T2C_QUAL_V2 to compare)")
    for rel in MANIFEST:
        assert sha(src / rel) == sha(local(rel)), rel
