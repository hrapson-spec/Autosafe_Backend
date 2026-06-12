# V55 — FROZEN LEGACY LINEAGE (decision 2026-06-12)

The served V55 artifact (`catboost_production_v55/model.cbm`) is **frozen as
legacy and declared non-reproducible from this repository**. No V55 retrain
will be attempted; the successor lineage is **v57** (window-bounded observed-
history contract, versioned bundle under `models/v57/`). This directory
preserves the recovered provenance so the finding is documented rather than
silent.

## Verified provenance of the served artifact (audit session 2026-06-12)

- **Built:** 2026-01-16 14:41 (`results.json` `created`), artifacts mtime
  Jan-17. Snapshot commit `2c7fa34` ("V55 achieves 0.75 AUC", Jan-16 18:43)
  in the iCloud research repo.
- **Binary ground truth:** `model.cbm` embeds exactly **104 features whose
  names and order match serving's `FEATURE_NAMES` perfectly** (verified by
  loading the binary). External-audit claims of a 107-feature trainer with a
  mismatch at index 62 describe *post-build trainer drift*, not the artifact.
- **Why it is not reproducible:**
  1. The trainer imports `station_priors`, which was **never committed** to
     any repo. It was recovered from `~/autosafe_work/station_priors.py`
     (mtime 2026-01-04, predating the build; `__pycache__` interpreter tags
     prove the trainer was executed from `~/autosafe_work`). A byte-identical
     copy is archived here as `station_priors.py`.
  2. The deployed binary predates the trainer's later commits (Feb-05
     "align V55 inference features" etc.) — HEAD trainers are not the code
     that built production.
  3. `results.json` carries contradictory labels (`version: V40`,
     `strategy: "V37 Lean…"`, dir named v55) and a train-side `test_auc`
     (0.7500) that does not match docs (`kitchen_sink`, OOT 0.7103–0.7107).
  4. Training depended on uncommitted local state (`~/autosafe_work` priors
     parquets, hard-coded absolute paths).
- **Known defects carried by V55** (see `work/audit_2026/GF17_DEFECT_LEDGER.md`):
  42/104 features fail train/serve parity (RC-1..7), including four features
  served as constants (`station_fail_rate_smoothed`,
  `station_x_prev_outcome_fail_rate`, `station_strictness_bias`,
  `suspension_risk_profile`) — these four are **dropped** in the v57 contract.
  The recovered `station_priors.py` additionally fits in-sample on dev with
  the target (no OOF/time-slicing) — do not reuse it as-is; any future
  station/geographic effect must follow the v57 Stage-2 rules (PIT-safe key,
  OOF/time-sliced encoding, artifact serialized into the bundle, parity tests).

## What remains supported

`catboost_production_v55/` continues to serve until v57 promotes, with the
2026-06-11/12 remediations applied at the boundary: vocab shim, real
`days_since_pass_ratio`, and the pickle-free Platt calibrator
(`calibrator.json`, constants extracted exactly from the legacy pickle).
