"""Lake manifest: the lineage record every downstream stage verifies against.

One JSON file at the lake root records, for every ingested source file and
every emitted partition: content hash, row count, and date coverage. The
aggregate builder and the v58 training_manifest both re-verify these hashes
(v57 promotion gate 3: "every hash re-verifiable"). Ingest is idempotent
through this file: an unchanged source (same sha256) is skipped, a changed
one fails unless --force (a source file silently changing content under the
same name is an incident, not a refresh).
"""
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MANIFEST_NAME = "lake_manifest.json"


def sha256_file(path: Path, chunk_bytes: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tool_git_sha(repo_root: Optional[Path] = None) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


@dataclass
class SourceRecord:
    path: str
    sha256: str
    bytes: int
    schema: str
    rows_ingested: int
    ingested_at: str


@dataclass
class PartitionRecord:
    path: str
    rows: int
    min_test_date: Optional[str] = None
    max_test_date: Optional[str] = None


@dataclass
class CheckRecord:
    name: str
    passed: bool
    detail: str
    ran_at: str


@dataclass
class LakeManifest:
    created_at: str
    tool_git_sha: str
    config: Dict[str, Any] = field(default_factory=dict)
    sources: List[SourceRecord] = field(default_factory=list)
    partitions: Dict[str, List[PartitionRecord]] = field(default_factory=dict)
    checks: List[CheckRecord] = field(default_factory=list)

    # -- construction / persistence -------------------------------------

    @classmethod
    def new(cls, config: Dict[str, Any]) -> "LakeManifest":
        return cls(
            created_at=datetime.now(timezone.utc).isoformat(),
            tool_git_sha=tool_git_sha(),
            config=dict(config),
        )

    @classmethod
    def load(cls, lake_dir: Path) -> Optional["LakeManifest"]:
        path = Path(lake_dir) / MANIFEST_NAME
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        manifest = cls(
            created_at=raw["created_at"],
            tool_git_sha=raw.get("tool_git_sha", "unknown"),
            config=raw.get("config", {}),
        )
        manifest.sources = [SourceRecord(**s) for s in raw.get("sources", [])]
        manifest.partitions = {
            dataset: [PartitionRecord(**p) for p in parts]
            for dataset, parts in raw.get("partitions", {}).items()
        }
        manifest.checks = [CheckRecord(**c) for c in raw.get("checks", [])]
        return manifest

    def save(self, lake_dir: Path) -> Path:
        path = Path(lake_dir) / MANIFEST_NAME
        payload = {
            "created_at": self.created_at,
            "tool_git_sha": self.tool_git_sha,
            "config": self.config,
            "sources": [asdict(s) for s in self.sources],
            "partitions": {k: [asdict(p) for p in v] for k, v in self.partitions.items()},
            "checks": [asdict(c) for c in self.checks],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    # -- source idempotency ---------------------------------------------

    def source_state(self, path: Path) -> str:
        """'new' | 'unchanged' | 'changed' for an incoming source file."""
        digest = sha256_file(path)
        for record in self.sources:
            if record.path == str(path):
                return "unchanged" if record.sha256 == digest else "changed"
        return "new"

    def record_source(self, path: Path, schema: str, rows: int) -> SourceRecord:
        record = SourceRecord(
            path=str(path),
            sha256=sha256_file(path),
            bytes=path.stat().st_size,
            schema=schema,
            rows_ingested=rows,
            ingested_at=datetime.now(timezone.utc).isoformat(),
        )
        self.sources = [s for s in self.sources if s.path != record.path]
        self.sources.append(record)
        return record

    def record_check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(CheckRecord(
            name=name, passed=passed, detail=detail,
            ran_at=datetime.now(timezone.utc).isoformat(),
        ))
