# AutoSafe ExperimentLoop

A Karpathy-`autoresearch`-style **agent-driven** loop for the **v58 feature search**. The agent has
**maximal creative and scientific autonomy**; only a tiny **referee** is immutable.

> Design doc / approved plan: `~/.claude/plans/find-out-as-much-async-graham.md`
> Feature inspiration: `work/audit_2026/FEATURE_PORTFOLIO_AUDIT.md` + the rev B ladder (a *seed*, not a boundary).

## The model: maximal autonomy, immutable referee

The agent may invent any feature, build across **multiple files**, **self-direct** its research from the
diagnostics, **search the literature** for mechanisms (`research_log.md`), **drive its own substrate**
(`data_requests.md`), and even **propose changes to the referee** (`eval_proposals.md`). The rev B ladder
is inspiration; off-script invention is encouraged.

Exactly three things are fixed — not to leash the agent, but because an optimiser that can move its own
referee produces noise at machine speed, not knowledge (AutoSafe's 22 hand-tuned `auc_075` runs → an
overstated 0.75 are the proof). Holding the referee still is what makes the freedom *productive*:

1. **The scorer / promotion rule** (`referee_config.py`, `evaluate.py`, the Arm-0 harness) — within-segment
   lift, calibration-safe, actually-used, stable. The agent may *propose* changes; a human ratifies.
2. **The parity gate** (`gf17 --expect-fixed`) — a kept feature must compute identically at serving.
3. **The single held-out window** — scored once, on the final composite, never during search.

Plus structural guarantees that aren't creative limits: inputs are **prior-only** (`r2b:129`, no temporal
leakage), and any **web-sourced *number*** used as a feature is treated as outcome-derived data (as-of/PIT
+ parity + quarantine) — web *mechanisms* are free, web *quantities* are not (the `local_corrosion_index`
pathology).

## Status — SCAFFOLD ONLY, not runnable yet (gated)

This directory holds the **human-authored** half of `autoresearch` (the part that does not depend on the
gated runtime): the charter (`program.md`), the agent's primary surface (`candidate_feature.py`), the
**protected referee** (`referee_config.py` + `referee_guard.py`), the proposal/substrate/research channels,
the mutable plumbing (`config.py`), and the adversarial-control specs. The **runtime** (`evaluate.py` + the
Arm-0 segmented harness) is deferred — blocked on the preconditions below.

### Preconditions before the loop can run
1. **R4 finishes + a stable v57 baseline.** `work/bakeoff_2026/v57_auc_rca.py` is open on rebuilt OOT
   **0.7133** vs v55-frame **0.7273**. `program.md`'s baseline-to-beat is a placeholder until it settles.
2. **Stage P — v58 results-lake re-ingest** (full-depth history + fuel/engine/class, MISSING-with-flag,
   resolve the 84.8% `n_prior=0` bucketing). Exit gate: `gf17 --expect-fixed` `n_fail=0`.
3. **Arm 0 — the segmented harness**, golden-tested to reproduce GF-8 cell AUCs (0.61–0.67) ≤1e-6.

## Why a worktree, and what I found building it

Built in an **isolated git worktree off `origin/main`** (`~/autosafe-experiment-loop`, branch
`experiment-loop`) so it never touches the live `~/autosafe` tree — which had two `git add -- .` parked
11+ min, was 8 commits behind `origin/main`, and sat on a 95%-full volume. Corrections to the prior record,
verified against real code:
- **The entire `work/` research+audit apparatus is untracked** (origin/main = 179 files / 13.6 MB, the
  production app only). The loop wraps the harness/frames **by path** (see `config.py`).
- **`model_bundle.py` is not on my local `origin/main` ref** (`r4_bench.py:62` imports it from
  `~/autosafe`/`~/autosafe-phaseA`); needs a `git fetch` to confirm before correcting memory. `calibrator.json`
  *is* on origin/main.

## autoresearch → AutoSafe mapping

| `autoresearch` | here | enforced by |
|---|---|---|
| `train.py` (sole editable file) | **anything except the referee** (denylist) | `referee_guard.py` — refuses a commit only if a protected referee file is modified |
| `prepare.py` (fixed data + eval) | `referee_config.py` + `evaluate.py` *(deferred)* + frozen frames + gates | path-immutable; agent proposes changes, doesn't make them |
| `val_bpb` (scalar) | **segmented promotion verdict** (within-segment AUC + ECE + 2-seed stability) | `referee_config.PROMOTION` |
| 5-min budget | two-tier: logloss 2-seed screen / full rebuild at promotion; **no caps** | `config.BUDGET` |
| keep / `git reset HEAD~1` | keep-commit / revert on `autoexp/v58-*` | the runner |
| `program.md` | `program.md` (the autonomy charter) | — |
| results log | `ledger.tsv` + `work/audit_2026/evidence/` | the runner |

## File inventory
- `program.md` — the research charter (maximal-autonomy methodology).
- `candidate_feature.py` — the agent's primary feature surface + a worked positive control (it may add more files).
- `referee_config.py` — **the immutable core**: frames, label, gates, win criterion, slices, held-out spec (protected).
- `referee_guard.py` — protect-the-referee denylist (successor to the single-file guard).
- `eval_proposals.md` — the agent's channel to *propose* referee changes (human ratifies).
- `data_requests.md` — the agent drives its own substrate (declares data to ingest).
- `research_log.md` — provenance for web-sourced hypotheses (URL + access date + how it became a feature).
- `config.py` — mutable plumbing: paths, build inputs, params, search ladder, budget.
- `adversarial_controls.md` — the five referee-bites-back controls (more vital the freer the agent is).
- `ledger.tsv` — keep/discard/dead/crash log (header only for now).
- `evaluate.py` — **deferred** (the scorer; gated on Arm 0 + stable baseline).
