# V57 RCA Remediation Report

*Branch `codex/v57-auc-rca-remediation` — 2026-06-12. Contract authority: the
merged Stage-1 scaffold (`model_bundle.py`, PR #24), confirmed over the older
2021-window spec the task shipped with (decision logged 2026-06-12 ~13:50).*

## Summary
- Status: PASS (all Stage-1 gates; SC-10 floor cleared)
- Branch: `codex/v57-auc-rca-remediation` (base: `200e99e` = main + PR #24)
- Commits: d47c014 (deps), 9f5ad5f (contract), d8d0df8 (serving FE),
  6947997 (recompute + fixture parity), 99ade5b (trainer), dbc3883
  (evaluator), e39eb0e (stale-test repairs), 61491cf + b5ad959 + 1969639 +
  c185c8d (input staging, OOM restructure, dedupe fixes), 09f28a1 (loader +
  gated route), f001f57 (serving-path evaluation + seed checkpoints),
  7d88a13 (bundle artifacts)
- Model artifact: `models/v57/model.cbm` — 10-seed `sum_models` merge @
  1233 iterations, evaluated through the serving load path
  (CatBoostClassifier.load_model on the saved file); the evaluated
  artifact IS the deployed artifact (v55 reported ensemble metrics but
  shipped only seed 0)

## Metrics

OOT = 2024 full year, 228,328 veteran rows (test_id-deduped), evaluated once.

| Metric | Value |
|---|---:|
| ROC-AUC | 0.7135 |
| PR-AUC | 0.4725 |
| Log Loss | 0.5166 |
| Brier | 0.1697 |
| Calibrated Brier | 0.1718 |
| Precision@1% | 0.7495 |
| Precision@5% | 0.6190 |
| Precision@10% | 0.5705 |
| Lift@1% | 2.8978 |
| Lift@5% | 2.3936 |
| Lift@10% | 2.2059 |

(OOT base rate 0.2586; precision/lift on calibrated scores.)

Comparators: v55 reported OOT AUC 0.7500 — but that number early-stopped
each seed ON the OOT pool, averaged in probability space, and described an
artifact that never shipped; ~3.1pp of its stated importance sat in RC-3
features served as constants. The GF-17 fresh-panel audit measured the
SERVED v55 at ~0.688–0.691 — against deployed reality, v57's 0.7135 is a
like-for-like improvement of ≈+2.2–2.5pp. v57 early-stops on a dev 2023-H2
tail and touches OOT exactly once. Per-seed OOT AUC spread (10 seeds):
0.7130–0.7135 (σ≈2e-4).

## Gates

| Gate | Status | Evidence |
|---|---|---|
| Shared feature contract | PASS | `model_contract_v57` derives 105 names from v55 ground truth + decision table; emitted via `emit_contract`; round-trips `load_contract` incl. `validate_decision_table` (tests/test_v57_feature_contract.py, 8 tests) |
| Train/serve parity | PASS (fixture level) | tests/test_v57_recompute_parity.py: 4 synthetic vehicles, ~78 features each, SQL recompute == fe_v57 exactly; caught + locked the expiry rule during development. Full `--expect-fixed` is the program end-gate (below) |
| Window-bounded history | PASS | WINDOW_START 2019-01-01 both paths (scaffold directive); training counts recomputed in `[2019-01-01, T)` from the lake; serving caps DVSA history before FE |
| RC-3 drop-set removed | PASS | 4 features (3 station + suspension_risk_profile) absent from contract, matrix, and serving emission; guarded by contract tests |
| Noisy feature families excluded | PASS | neglect_score_* trainer-only family not ported; system-layer / trajectory one-hot / trajectory interactions / hazard_d1_full never enter (contract-driven FEATURE_COLS) |
| Vocab parity (RC-2) | PASS (matrix level) | trainer derives gap_band/usage_band via the serving bucketizers, prev-cycle outcome + advisory trend on serving levels; hard-fail vocab gate confirmed every high-mass serving level present in matrix vocab (train log) |
| Canonical defaults (RC-1) | PASS | per-feature defaults = serving semantics in the contract; trainer fills + `features_to_array` read the same table; eb_unified_prior default emitted from the fitted global |
| Full metric reporting | PASS | metrics.json with all 11 required metrics; evaluator cross-checks the trainer-reported values and the --dataset sha256 against the manifest |
| Calibration output | PASS | models/v57/calibration_bins.csv (10 equal-frequency bins) |
| Cohort AUC output | PASS | models/v57/cohort_auc.csv (7 axes, serving band edges) |
| Lineage manifest | PASS | training_manifest.json (sha256 a80fa934…d93b4e04): hashes of all staged inputs, rows, date ranges, git SHA, interpreter + package versions, recompute coverage, vocab report, staged-path mapping |
| Serving smoke | PASS | model_v57.load_model() validates the real bundle; truncated-history and rookie vehicles predict in [0,1]; left-truncation flag surfaced in the response |
| Tests | PASS | 55 passed, 1 skipped in 170s (contract 8, parity 5, recompute fixtures 4, model_bundle 6, feature_contracts 15, leakage 2 — now running on real data, data_integrity 15; the skip = `tests_lookup.parquet` genuinely absent, the spec-allowed fixture-missing case) |

## v57 feature count

**105** = 104 served v55 features − 4 RC-3 drops + 5 coverage features, with
35 observed-history renames; 10 categorical (unchanged names).

## Artifact paths
- Calibration table: `models/v57/calibration_bins.csv`
- Cohort AUC table: `models/v57/cohort_auc.csv`
- Lineage manifest: `models/v57/training_manifest.json` (sha256:
  a80fa93434ad57554011e6550bd0942dc7b9a7b7ed801683e1cf1910d93b4e04)
- Contract: `models/v57/feature_contract.json` · Calibrator (pickle-free):
  `models/v57/calibrator.json` · Eval frame: `models/v57/oot_eval_frame.parquet`
  (gitignored) · GF-17 matrix: `~/autosafe_work/v57_prepared_data.pkl`

## Changed files
- Created: `model_contract_v57.py`, `feature_engineering_v57.py`,
  `v57_history_recompute.py`, `train_catboost_production_v57.py`,
  `evaluate_model_v57.py`, `model_v57.py`,
  `tests/test_v57_feature_contract.py`, `tests/test_v57_feature_parity.py`,
  `tests/test_v57_recompute_parity.py`, `models/v57/` bundle contents
- Modified: `main.py` (gated `/api/risk/v57`, default OFF),
  `tests/test_leakage_detection.py`, `tests/test_feature_contracts.py`,
  `requirements.txt`, `.gitignore`

## Residual risks (Stage-1, documented)
1. **Mileage substrate**: prior-test odometers come from the sampled spine —
   coverage 62% of DEV priors, 29% of OOT priors, ~100% at serve. The
   has_prev_mileage / plausibility flags carry the regime, but
   mileage-feature signal is trained on the covered subset only. Real fix:
   odometer lake (v58).
2. **Advisory substrate (RC-5 remainder)**: spine-only prior events
   contribute 0 advisories (lake convention); per-prior steering advisories
   and structure/steering mech-decay are lake-invisible (always 0 in the
   matrix, live at serve). text_* keyword features remain v52-sourced
   (RC-4 estimator divergence open). Lake re-ingest lands in v58.
3. **Retest multiplicity**: lake events are cycle-firsts; serving counts
   raw API tests (incl. retests) — observed counts differ by the retest
   factor within an identical window. Cycle-collapse at serve was rejected
   as a new serving semantic.
4. **days_late expiry approximation**: training uses prior_date+365 vs the
   API's true expiry (anniversary-preservation can add ≤~30 days).
5. **Population definition**: veterans filter retained from v55 evaluated
   on the original baked columns (population row-identical to v55, minus
   76 duplicated test_ids now dropped).
6. **Multi-change release**: window recompute + vocab + defaults + drop-4 +
   honest early stopping land together; the AUC delta vs v55 is not
   decomposable without ablations (out of scope by spec).
7. **first_use vs manufacture date**: serving age bands use
   manufacture_date, training uses first_use_date (months-scale skew).
7b. **Calibration transfer**: the v55-protocol Platt (fit on train
   predictions) slightly DEGRADES OOT Brier (0.1697 raw → 0.1718
   calibrated). Recalibrate on the dev 2023-H2 tail before enabling the
   route; rank metrics are unaffected.
8. Upstream data warts found and worked around (recorded for repo hygiene):
   `all_tests.parquet` corrupt (iCloud half-materialization);
   `oot_set_with_advisory` + `model_age_features_oot` duplicate test_ids;
   iCloud-evicted spine placeholders (training inputs now staged to local
   APFS with footer validation).

## Program end-gate (next, out of this task's scope)
```
python3.13 gf17_train_serve_parity.py --expect-fixed \
  --train-matrix ~/autosafe_work/v57_prepared_data.pkl \
  --cbm models/v57/model.cbm --fixtures v2     # run twice, byte-identical
```

## Promotion recommendation
Promote v57: **not yet — ready for the two remaining program gates.**
Reason: everything THIS task gates on passed (SC-10 floor 0.7135 ≥ 0.7000;
contract, parity-fixture, vocab, lineage, calibration/cohort outputs, test
suite 55/1-skip, serving smoke), and against the deployed v55's measured
fresh-panel AUC (~0.688–0.691) v57 is a like-for-like improvement. But the
bundle README's promotion ladder still requires (1) the GF-17 end-gate —
`--expect-fixed --train-matrix ~/autosafe_work/v57_prepared_data.pkl --cbm
models/v57/model.cbm --fixtures v2`, run twice, byte-identical — and
(2) the v55-vs-v57 comparison on identical frames (the Gate 0 re-run for
the B2B pivot). Recommend also recalibrating the Platt on the dev tail
(residual 7b) before flipping `V57_PREDICTIONS_ENABLED`.
