# Evaluator reliability

Why this exists: the 2026-06-14 dev-grade smoke of the ExperimentLoop ran end-to-end but
surfaced three defects that made every keep/dead verdict untrustworthy.

| | Defect | Fix |
|---|---|---|
| **F1** | committed `evaluate.py` sampled `USING SAMPLE N ROWS` (no seed) → two 30K runs shared 449/30000 rows → keep↔discard flipped | seeded `(reservoir,42)` dev sample; promotion grade uses a deterministic `test_id` hash-cohort (`sampling.py`) |
| **F2** | the positive control `vehicle_age_years` does not promote (pooled −0.288pp, seed-unstable) under the `mot_tests=[]` shim | candidate-faithful injection (`faithful_inject.py`); a **two-tier** positive control (synthetic + domain) with an escape hatch |
| **F3** | `seed_stable = all(d>0) or all(d<0)` treated *consistently harmful* as "stable" and could keep a pooled-negative feature | `decision.classify_seed_direction` — a 4-way enum; promotion requires `stable_positive` |

While wiring, a fourth issue was caught: `arm0_harness.verdict` kept on `(within OR pooled)`,
a GF-8 gradient-gaming hole. `decision.decide` now **requires** within-segment wins (AND).
`arm0_harness.verdict` is retired.

## Two grades, one score path
`score_core.py` is the single train/score implementation; `evaluate.py` (dev) and
`validate_promotion_grade.py` (promotion) are thin callers — no parallel logic.

- **dev** — diagnostic only (`diagnostic_only=true`), every row. Seeded reservoir sample,
  `mot_tests=[]` shim, within-run comparator. Screening, never claims.
- **promotion** — the runtime invariant:
  > No canonical promotion-grade ledger row is written unless it has a durable
  > content-addressed manifest, clean pre-run git state, a cohort matching the config spec
  > under the recorded `data_snapshot_id`, candidate-faithful injection, ≥5 seeds,
  > `seed_direction == stable_positive` past predeclared thresholds, and a green required
  > control battery.

## Gate machinery vs promotion authority
- `make test-evaluator-gates` proves the **machinery** works (pytest on synthetic data) and
  **must pass for the PR**.
- `make validate-promotion-authority` runs the real-frame battery and **may lock safely**.
  `authority_decision`:
  - required battery green → **activated**.
  - a synthetic-positive fails → **locked / `evaluator_broken`** (the evaluator can't detect
    a planted lift — a real bug).
  - the domain positive fails while synthetics pass → **locked / `domain_control_failed`**,
    pending a ratified resolution state ∈ `{domain_control_invalid_redundant,
    domain_control_replaced, pipeline_injection_fault, model_truth_age_redundant}`. This is
    the expected, correct outcome if raw age is genuinely redundant — not a PR failure.

## Provenance
`manifest.py`: `manifest_id` = sha256 of the **deterministic payload only** (versions,
`data_snapshot_id`, `sample_fingerprint`, seeds, candidate/contract hashes, thresholds,
per-control status, metrics rounded to 1e-9, verdict). Volatile metadata (timestamps,
`run_id`, paths) is recorded but excluded → replay reproduces the id. Promotion manifests are
committed content-addressed under `promotion_manifests/<id>.json`, so a `promotions.tsv` row
resolves in any fresh clone. Dev artifacts stay in the gitignored `runs/`. The pre-hardening
ledger is preserved as a tracked diagnostic archive under `legacy/`.

## Scope
Out of scope (promotable path): P1 stable R4 baseline, P2 v58 frame + `gf17 --expect-fixed`
`n_fail=0`, P3 Arm-0 GF-8 golden ≤1e-6, and full serving-path FE fidelity. The
`negative_train_only` control is `pending_not_enforced` until P2.
