# v55/v57 leakage & parity remediation — action items

Tracks the five action items from the train/serve audit. Findings recap: the
deployed **v55** path violates OOT independence, train/serve feature parity,
current-mileage semantics, and the served feature contract; EB target-encoding
temporal safety is partly leaky and partly unverifiable; only first-test
*filtering symmetry* was clean. **v57** (`model_bundle.py` + this directory) is
the intended fix and is currently unwired scaffolding.

This change set adds the tooling/fixes that can be authored in-repo. The numbers
for the data-dependent tools come from running them in an environment that has
the per-test data (off-repo: `~/autosafe_work/`) and the scientific stack.

| # | Item | Deliverable | Status |
|---|------|-------------|--------|
| 1 | Stop OOT early-stopping | `training_utils.time_based_eval_split` + trainer patch | Done (code) |
| 2 | Train/serve parity harness | `gf17_train_serve_parity.py` + `tests/test_train_serve_parity.py` | Done (CI-runnable) |
| 3 | Quantify mileage skew | `scripts/mileage_skew_replay.py` | Tool done; run on real data |
| 4 | First-test / no-prior policy | Decision recorded below | Decided |
| 5 | Audit EB temporal safety | `scripts/eb_temporal_safety_check.py` | Tool done; run on real data |

## #1 — Stop using OOT for early stopping

`train_catboost_production_v55.py` previously fit every seed with
`eval_set = <OOT pool>` and `early_stopping_rounds=150`, letting OOT labels pick
the iteration count and biasing the reported OOT metric. The fit now carves a
**time-based validation fold from the most-recent slice of DEV**
(`time_based_eval_split`, default 10%); OOT is used only for the final
evaluation. The helper is stdlib-only and unit-tested
(`tests/test_training_utils.py`).

> Note: the served v55 binary is frozen/non-reproducible and HEAD ≠ the trainer
> that built it (`work/legacy_v55/README.md`); this fix is the correct pattern
> the **v57** trainer inherits. It does not alter the deployed v55 artifact.

## #2 — Train/serve parity harness (keystone; v57 gate #2)

`gf17_train_serve_parity.py` runs a diverse fixture panel through the serving
feature path and checks names/order vs the contract, the deployed `model.cbm`
schema (when catboost is present), categorical-vocab membership, and
constant-served features — the first non-test wiring of `model_bundle`.

```bash
# v55 audit mode (tolerates only the documented known-broken set):
python gf17_train_serve_parity.py

# v57 gate mode (fails on ANY violation; point at the v57 contract + artifacts):
python gf17_train_serve_parity.py --expect-fixed \
    --contract models/v57/feature_contract.json \
    --cbm models/v57/model.cbm \
    --train-matrix /path/to/v57_train_matrix.parquet
```

CI lock: `tests/test_train_serve_parity.py` pins that the four RC-3 features are
served as constants and that shimmed categoricals stay in-vocabulary, so a new
parity regression fails the build.

## #3 — Quantify the mileage skew

`scripts/mileage_skew_replay.py` reconstructs, per target test, the **train**
mileage (`d.test_mileage` = the target test's own odometer) vs the
**serving-faithful** mileage (the last *completed* test's odometer) and reports
the distribution of the gap (and of annualized mileage).

```bash
python scripts/mileage_skew_replay.py /path/to/per_test.parquet \
    --vehicle-col vehicle_id --date-col test_date \
    --mileage-col test_mileage --target-col is_failure --out mileage_skew.json
```

Follow-up (needs a model): feed `serve_*` vs `train_*` mileage through the model
and compare AUC to convert the feature-level skew into a score-impact number.

## #4 — First-test / no-prior policy — DECISION: one model via v57 coverage

**Decision (2026-06-17): serve first-test / no-prior vehicles from the same v57
model, using the coverage features to disambiguate** (not a separate model, not
a baseline fallback).

Implications for the v57 build:

- **Drop `veterans_only`** from the v57 train/eval queries so first-test rows are
  in **both** training and OOT evaluation (today they are excluded from both —
  consistent, but the model neither trains nor validates on them while serving
  still scores them).
- Rely on the five **coverage features** already defined in `model_bundle.py`
  (`window_days_available`, `history_years_observed`, `has_prior_test_observed`,
  `has_left_truncated_history`, `first_observed_test_is_not_true_first`) so the
  model can tell "clean observed history" from "no/again-unobservable history".
- Serving must emit those coverage features (v57 serving rebuild) and align the
  no-prior fills with training (the parity harness enforces this once the v57
  contract exists). The documented `days_since_pass_ratio` default mismatch
  (serve `0.0` vs train `2.0`) closes in the same rebuild.

## #5 — EB temporal-safety evidence (v57 gate #4)

`scripts/eb_temporal_safety_check.py` verifies point-in-time integrity of the
time-sliced priors and heuristically detects in-sample (no-OOF) make/model
target encoders.

```bash
python scripts/eb_temporal_safety_check.py \
    --priors /path/to/time_sliced_eb_priors_dual.parquet \
    --dev /path/to/dev_encoded.parquet --oot /path/to/oot_encoded.parquet \
    --encoded-cols make_fail_rate_smoothed=make model_fail_rate_smoothed=model_id
```

Gate #4 must ultimately be **enforced in the off-repo encoder builders** (expose
fold assignments / time-slice cutoffs); this script provides outside-in evidence
and flags the leaky-looking encoders, but cannot prove builder internals.
