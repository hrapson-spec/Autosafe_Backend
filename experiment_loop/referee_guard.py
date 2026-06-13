"""referee_guard.py — protect the referee, free everything else (denylist model).

The maximal-autonomy successor to the single-file allowlist. The agent may edit ANY
file and ADD any new module EXCEPT the small protected referee set below. A commit that
modifies a protected path is refused; everything else is the agent's to build.

This is the mechanical expression of program.md's one boundary: the agent cannot
silently move its own scorer, parity gate, or held-out window. It can PROPOSE changes
to them (eval_proposals.md) for a human to ratify between runs.

Exit 0 = clean; exit 1 = a protected file was modified (the runner must abort the keep-commit).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The referee. Small and explicit by design — everything outside it is free.
PROTECTED = {
    "experiment_loop/referee_config.py",   # the win criterion + slices + held-out spec
    "experiment_loop/referee_guard.py",    # the guard cannot disable itself
    "experiment_loop/evaluate.py",         # the scorer (deferred; protected on arrival)
    "experiment_loop/arm0_harness.py",     # the segmented harness (deferred)
    # the held-out frame and the gate wrappers are path-guarded at runtime by the runner
}


def _changed_paths(repo: Path) -> set[str]:
    # Only MODIFICATIONS to tracked files matter for the denylist; new untracked files
    # are the agent's building freedom and are allowed.
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "HEAD"],
                         capture_output=True, text=True).stdout.split()
    return {p for p in out if p}


def check(repo: Path | None = None) -> int:
    repo = repo or Path(__file__).resolve().parents[1]
    violations = sorted(_changed_paths(repo) & PROTECTED)
    if violations:
        print("REFEREE GUARD: commit refused — protected referee files modified:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print("Propose referee changes in eval_proposals.md for a human to ratify instead.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(check())
