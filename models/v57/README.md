# models/v57 — versioned model bundle (Stage-1 directive, 2026-06-12)

The v57 lineage ships as a self-describing bundle. The trainer EMITS every
file below from the actual training run; serving LOADS the bundle and
validates against `feature_contract.json` at startup. No hand-maintained
feature lists anywhere (the 107-vs-104 drift class is structurally retired).

## Layout (populated by the v57 training run)

| File | Contents |
|---|---|
| `model.cbm` | CatBoost model |
| `calibrator.json` | Pickle-free Platt constants (`sigmoid(A·raw+B)`) — NOT a pickle; see audit P0 gf2b |
| `feature_contract.json` | Names+order, dtypes, categorical indices, defaults, source columns, PIT availability, artifact deps, history window, parity tolerance |
| `training_manifest.json` | Lineage gate: every input file's path, sha256, row count, date range, generation command, git SHA, seed |
| `metrics.json` | OOT metrics produced by THIS packaged bundle (metric gate) |
| `feature_artifacts/` | Any fitted lookup tables serving needs (each listed in the contract's `artifact_dependencies`) |

## Decision table (single source: `model_bundle.py` at repo root)

- **History window**: training and serving both cap histories at
  `WINDOW_START = 2019-01-01` (substrate coverage). Full-depth serving = v58.
- **RC-3 drop-set** (4): station_fail_rate_smoothed,
  station_x_prev_outcome_fail_rate, station_strictness_bias,
  suspension_risk_profile — were served as constants by v55; removed rather
  than reconstructed (see `work/legacy_v55/README.md`).
- **Observed renames** (35): history accumulation/recency features carry an
  explicit `*_observed` name under the bounded window.
- **Coverage features** (5): window_days_available, history_years_observed,
  has_prior_test_observed, has_left_truncated_history,
  first_observed_test_is_not_true_first — so "zero prior failures" is never
  ambiguous between clean-observed and unobservable.

## Promotion gates (all must pass before this bundle serves)

1. **Schema**: trained model `feature_names_` == contract == serving emission order.
2. **Parity**: `gf17_train_serve_parity.py --expect-fixed --train-matrix <v57> --cbm <v57> --fixtures v2` (26 fixtures), twice, byte-identical.
3. **Lineage**: `training_manifest.json` complete; every hash re-verifiable.
4. **Temporal**: target encodings out-of-fold or time-sliced only.
5. **Metric**: OOT metrics in `metrics.json` produced by the exact packaged bundle, compared against live v55 on identical frames.

⚠ Deployment note: `.railwayignore` excludes `*.json` globally — every JSON
artifact in this bundle is exception-listed there. When adding a new JSON
file to the bundle, add its `!models/v57/<file>` exception or it will be
silently dropped from the build context (this exact failure shipped a broken
calibrator image on 2026-06-12).
