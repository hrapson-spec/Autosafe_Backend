# v58 program — pinned decisions

Decision record for the full-depth defect-data integration and retrain
(approved 2026-08-10). Each entry states the decision, the rationale, and the
tripwire that invalidates it. Scope and sequencing live in the approved plan;
this file is the durable in-repo record.

## D1. History window: `WINDOW_START_V58 = 2005-01-01`

Full-depth. Matches the start of digital MOT records and therefore what the
DVSA MOT History API serves, so the training and serving observation windows
are structurally identical. The window-cap function remains in BOTH paths
(trainer matrix build and serving feature engineering) as a guard, even though
it is a no-op for API histories — the contract's `history_window` rule stays
literally true.

*Tripwire:* Phase-1 day-1 `check_vehicle_continuity` — if the current bulk
publication does not provide one consistent `vehicle_id` space across
2005→present, this window is renegotiated before any further work.

## D2. Keep the 35 `*_observed` renames and the 5 coverage features

Contract continuity with the v57 decision table. Coverage-feature semantics
are re-documented for the 2005 boundary: `has_left_truncated_history` means
"MOT-liable before 2005" (pre-2002 registrations); `window_days_available`
becomes near-constant and is expected to carry ~zero model weight — kept for
contract shape stability, documented, not fought.

## D3. Aggregate comparison window: latest 5 full calendar years

The public comparison denominator (prod_data_clean.csv.gz / mot_risk) is
rebuilt from cycle-first class-4 tests in the most recent five complete
calendar years — NOT 2005+. Full depth exists for per-vehicle HISTORY
features; mixing 2005-era failure regimes into the comparison rate would
misstate current fleet risk. Pinned as `DATASET_COVERAGE_START/END` in
`report_contract.py` and stated factually in public copy, closing the
"artifact revision date is not a coverage claim" hole.

## D4. New enum value `prediction_source = "model_v58"`

Honest provenance over zero-churn reuse of `model_v55`. Expand/contract:
the value ships end-to-end (backend enum + validators, OpenAPI snapshot,
`types.ts`, SPA runtime validator, exhaustiveness tests) at least one release
BEFORE anything can emit it — cached SPAs hard-reject unknown sources.
`model_v55` remains a readable value permanently (90-day TTL of persisted
payloads replays through `ReportResponse.model_validate`).

## D5. Veterans-only model scope retained

The serving gate (DVSA source AND ≥1 recorded MOT test) already means every
predicted vehicle has history; zero-history vehicles fall back to the
comparison assessment. The three-cohort ensemble described in CLAUDE.md was
never in this repo's code and stays out of scope; CLAUDE.md is corrected at
v55 retirement (P7).

## D6. Categorical features: 10, matching serving emission

Settles the trainer-10 vs v57-test-8 discrepancy (`test_month`,
`day_of_week`). v58 trains THROUGH the serving feature engineering, so
serving's dtypes are ground truth by construction.

## D7. Cycle/PRS semantics

Denominators are cycle-first tests (fail→retest chains collapse to one
event). PRS (pass with rectification) counts as its recorded result for the
outcome label and as a failure-signal for component/defect features.

*Tripwire:* the regenerated method, run on a window equivalent to the
checked-in artifact, must reconcile its 26.9139638817903% global rate within
tolerance before any new totals are published. Failure means the semantics
are wrong — halt, revisit, do not ship numbers.

## D8. JSON-only v58 bundle

`models/v58/` contains only JSON artifacts plus `model.cbm` — no pickles.
Extends the pickle-free calibrator remediation to every fitted lookup
(`feature_artifacts/*.json`), keeps the `.railwayignore` exception list short
and provable, and makes every artifact hash-verifiable at load time.

## D9. Shadow before promotion; both bundles in the image

v58 serves shadow-only (log sink `shadow_predictions`, never the wire) for a
2–4 week soak; promotion is an env flip (`MODEL_VERSION=v58`), rollback is
the reverse flip — instant, no redeploy, because both bundles ship in the
image. `PREDICTIONS_ENABLED=false` remains the global kill switch.

## D10. vocab_shim deletion deferred to v55 retirement (P7)

Rollback-to-v55 requires the v55 slot fully functional, and v55 needs the
shim. The v58 path never imports it; it is deleted only after the
post-promotion soak, together with the `tests/test_feature_path.py`
source-string pin rewrite.
