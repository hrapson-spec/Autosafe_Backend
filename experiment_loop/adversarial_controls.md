# Adversarial controls — prove the LOOP is correct before granting autonomy

These five planted candidates must each be handled correctly **before** the agent is trusted to
hill-climb on its own. They prove the gates *bite* — not just that a feature can pass. Each is run
through `evaluate.py` (deferred) and the verdict checked against the expectation. This is operational
proof, not a passing unit test.

| # | Control | Planted feature | Expected loop behaviour | Defends |
|---|---|---|---|---|
| 1 | **Positive** | `vehicle_age_years` (the worked example in `candidate_feature.py`) | **KEEP** — promotes on ≥2 within-segment slices | the rule isn't impossibly strict |
| 2 | **Dead-feature** | serving-emitted `advisory_trend` (the known dead vocab) | **auto-REVERT**, log `dead` (leakage ablation 0-drop) | catches dead-in-serving features |
| 3 | **Gradient-gaming** | a monotone transform of age adding no within-band info | **REJECT** — pooled AUC rises, within-segment flat | GF-8: rule is within-segment, not pooled |
| 4 | **Train-only** | a feature using a field absent at serving | **block at promotion** — `gf17 --expect-fixed` FAILs | train/serve parity (the 42/104 class) |
| 5 | **Replayability** | re-run control #1 at the same seed | identical ledger metrics; manifest reproduces them | determinism / provenance |

## Acceptance
Implemented in `controls.py` (battery + per-control status enum) and run by
`validate_promotion_grade.py`. Promotion authority activates only when the **required**
controls are green; until then the loop stays **locked** (the runner withholds promotion
authority) and verdicts are diagnostic-only. The positive control is now **two-tier** — two
fenced synthetic planted features (which test the evaluator itself) plus the domain control
`vehicle_age_years` (which can lock pending a ratified resolution state without meaning the
evaluator is broken). Control #4 (train-only) is `pending_not_enforced` until the P2
serving-FE/`gf17` tier. See `EVALUATOR_RELIABILITY.md`.

## Notes
- Controls #2/#3/#4 are *planted failures*; the loop is correct iff it **rejects** them. A green run that
  keeps any of #2–#4 means the gates are not wired — stop and fix the evaluator, not the feature.
- Control #4 requires the confirmation-tier path (full serving-FE rebuild + `gf17`), so it is gated on
  the same preconditions as the rest of the runtime.
