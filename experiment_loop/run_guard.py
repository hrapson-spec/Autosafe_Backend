"""run_guard.py — promotion-grade run gate. PROTECTED referee module.

A promotion-grade run must start from a clean source tree so its result is reproducible
from committed state. Policy (review pt 8): EVERY tracked/untracked file must be clean
EXCEPT an explicit output allowlist (run artifacts + the dev ledger). We do NOT enumerate
"source" — that lets a changed .sql/.yaml/model-contract slip through. Run-generated
writes go only to allowlisted paths, so a run never dirties its own gate (review pt 4).
"""
from __future__ import annotations

import subprocess

# Paths a promotion run may write / leave dirty; everything else must be clean.
DEFAULT_OUTPUT_ALLOWLIST = [
    "experiment_loop/runs/",        # gitignored dev artifacts (logs, dev manifests, dev ledger)
    "experiment_loop/ledger.tsv",   # the dev ledger (gitignored from commit 4 on)
]


class DirtyWorktreeError(RuntimeError):
    """Raised when a promotion-grade run is attempted with a dirty source tree."""

    def __init__(self, paths):
        self.paths = list(paths)
        super().__init__("promotion-grade run blocked; uncommitted source changes: "
                         + ", ".join(self.paths))


def _porcelain_path(line: str) -> str:
    """Path from a `git status --porcelain` line (XY<space>PATH); for a rename/copy
    'old -> new', return the new path."""
    body = line[3:] if len(line) > 3 else line.strip()
    if " -> " in body:
        body = body.split(" -> ", 1)[1]
    return body.strip()


def _allowed(path: str, allowlist) -> bool:
    return any(path == a or path.startswith(a) for a in allowlist)


def dirty_blocking_paths(porcelain_text: str, allowlist) -> list:
    """Dirty paths from `git status --porcelain` that are NOT under the output allowlist
    — the changes that block a promotion-grade run."""
    out = []
    for line in porcelain_text.splitlines():
        if not line.strip():
            continue
        path = _porcelain_path(line)
        if path and not _allowed(path, allowlist):
            out.append(path)
    return out


def worktree_porcelain(repo_root) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True, text=True, check=True).stdout


def assert_source_clean(repo_root, allowlist=DEFAULT_OUTPUT_ALLOWLIST) -> None:
    """Raise DirtyWorktreeError if any non-allowlisted file is dirty."""
    blocking = dirty_blocking_paths(worktree_porcelain(repo_root), allowlist)
    if blocking:
        raise DirtyWorktreeError(blocking)
