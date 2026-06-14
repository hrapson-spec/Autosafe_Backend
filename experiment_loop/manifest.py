"""manifest.py — durable, content-addressed provenance + the canonical ledger. PROTECTED.

manifest_id = sha256 of the canonical JSON of the DETERMINISTIC payload only (review pt
10): the evidence that must reproduce on replay. Volatile run metadata (run_id,
timestamps, paths, python build) is recorded inside the manifest but excluded from the
hash, so a replay with identical inputs reproduces the id. Float metrics are rounded so
sub-precision noise cannot break replay.

Durability (review pt 1): promotion manifests are written content-addressed to a TRACKED
directory, so a `promotions.tsv` manifest_id resolves in any fresh clone. Dev-grade
artifacts stay in the gitignored runs/ dir. Legacy rows are preserved (review pt 9);
read_ledger hides diagnostic_only by default so smoke evidence never mixes with promotion
evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVALUATOR_VERSION = "1.0.0"
CONTROL_BATTERY_VERSION = "1.0.0"
LEDGER_SCHEMA_VERSION = 1
_ROUND = 9   # decimal places for replay-stable metric hashing

LEDGER_COLUMNS = [
    "timestamp", "run_id", "grade", "candidate_id", "feature_names",
    "seed_direction", "pooled_d_auc_pp", "median_seed_d_auc_pp", "within_segment_wins",
    "worst_d_ece", "leakage_drop_pp", "n_seeds", "verdict", "promotable",
    "diagnostic_only", "worktree_clean", "ledger_schema_version", "evaluator_version",
    "control_battery_version", "sample_fingerprint", "data_snapshot_id", "manifest_id",
    "notes",
]


def _round_floats(obj, ndigits=_ROUND):
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, ndigits) for v in obj]
    return obj


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def manifest_id(deterministic_payload) -> str:
    return hashlib.sha256(
        _canonical_json(_round_floats(deterministic_payload)).encode("ascii")).hexdigest()


def build_manifest(*, deterministic_payload, volatile) -> dict:
    """A manifest: a content-addressed id over the rounded deterministic payload, plus
    volatile metadata (recorded, not hashed)."""
    payload = _round_floats(deterministic_payload)
    return {"manifest_id": manifest_id(payload),
            "deterministic_payload": payload, "volatile": volatile}


def write_promotion_manifest(manifest: dict, directory) -> Path:
    """Write a promotion manifest content-addressed as <manifest_id>.json (durable)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{manifest['manifest_id']}.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path


def resolve_manifest(mid: str, directory) -> dict:
    return json.loads((Path(directory) / f"{mid}.json").read_text())


def append_ledger_row(path, row: dict) -> None:
    path = Path(path)
    if not path.exists():
        path.write_text("\t".join(LEDGER_COLUMNS) + "\n")
    line = "\t".join(str(row.get(c, "")) for c in LEDGER_COLUMNS)
    with path.open("a") as f:
        f.write(line + "\n")


def read_ledger(path, include_diagnostic=False) -> list:
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = [dict(zip(header, ln.split("\t"))) for ln in lines[1:] if ln.strip()]
    if not include_diagnostic:
        rows = [r for r in rows if r.get("diagnostic_only", "").lower() != "true"]
    return rows
