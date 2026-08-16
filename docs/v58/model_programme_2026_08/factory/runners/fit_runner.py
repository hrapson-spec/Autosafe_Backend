#!/usr/bin/env python3
"""ONE (frame_parquet, config_json, seed) fit -> the harness fit contract.

    python -m factory.runners.fit_runner \
        --frame  'out/frames/recipe=train/rung=r250k/frame/*.parquet' \
        --eval-frame 'out/frames/recipe=eval2024/rung=all/frame/*.parquet' \
        --config configs/s2_D_cum_b0.json --seed 101 \
        --cell s2.D.cum.b0 --arm D --out-dir out/fits

Architectures and their presets (config `arch` + `preset`):

  catboost_gbm / screen   600 iterations   [OWNER-AMEND-5: screen-grade default]
  catboost_gbm / full    2000 iterations   banked-style full-grade anchor
  lightgbm     / *       sensible defaults; ensemble leg only (NO-FLOOR by design)
  realmlp      / *       the known recipe: label smoothing OFF, AUC checkpoint
                         selection, batch 4096, CPU device, early stopping

Every preset is a named constant below, so a config is reviewable (and the
tests assert the recipe) even on a machine where the library is not installed.

Determinism: CatBoost quantisation borders are computed once and reused across
seeds via --borders (bit-identical binning), so a seed-to-seed delta is the seed
and nothing else. One compute job at a time; `thread_count` is an argument and
no GPU/MPS device is ever requested.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from . import fit_contract as fc
from . import metrics

PRESETS: Dict[str, Dict[str, Any]] = {
    "catboost_gbm/screen": {
        "iterations": 600, "learning_rate": 0.06, "depth": 6,
        "l2_leaf_reg": 3.0, "loss_function": "Logloss", "eval_metric": "AUC",
        "bootstrap_type": "Bernoulli", "subsample": 0.8,
        "border_count": 128, "od_type": "Iter", "od_wait": 60,
        "use_best_model": True, "allow_writing_files": False, "verbose": False,
    },
    "catboost_gbm/full": {
        "iterations": 2000, "learning_rate": 0.03, "depth": 6,
        "l2_leaf_reg": 3.0, "loss_function": "Logloss", "eval_metric": "AUC",
        "bootstrap_type": "Bernoulli", "subsample": 0.8,
        "border_count": 254, "od_type": "Iter", "od_wait": 200,
        # MEASURED (close-out probe 2026-08-12): with od_type="Iter" the
        # overfitting detector forces per-iteration eval-metric computation and
        # CatBoost ignores metric_period entirely (stderr warning; identical
        # best_iteration/curve length/panel AUC across {1,5,25}). The former 25
        # realised no saving and mis-recorded banked curves as sparse. Pinned to
        # 1 — not deleted — so that if od_type is ever turned off, the value
        # cannot silently become a real 25x quantisation of use_best_model.
        "metric_period": 1,
        "use_best_model": True, "allow_writing_files": False, "verbose": False,
    },
    "lightgbm/screen": {
        "n_estimators": 600, "learning_rate": 0.06, "num_leaves": 63,
        "min_child_samples": 100, "subsample": 0.8, "subsample_freq": 1,
        "colsample_bytree": 0.8, "reg_lambda": 3.0, "objective": "binary",
        "metric": "auc", "n_jobs": 1, "verbosity": -1, "early_stopping_rounds": 60,
    },
    "lightgbm/full": {
        "n_estimators": 2000, "learning_rate": 0.03, "num_leaves": 63,
        "min_child_samples": 100, "subsample": 0.8, "subsample_freq": 1,
        "colsample_bytree": 0.8, "reg_lambda": 3.0, "objective": "binary",
        "metric": "auc", "n_jobs": 1, "verbosity": -1, "early_stopping_rounds": 200,
    },
    # ---- architecture screen (owner brief 2026-08-13): tuned/near-default
    # screening presets, 2-seed screen grade, NO deep tuning until a family
    # shows signal. GBDT presets mirror the lightgbm shape.
    "xgboost/screen": {
        "n_estimators": 600, "learning_rate": 0.06, "max_depth": 8,
        "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.8,
        "reg_lambda": 3.0, "max_bin": 256, "early_stopping_rounds": 60,
    },
    "xgboost/full": {
        "n_estimators": 2000, "learning_rate": 0.03, "max_depth": 8,
        "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.8,
        "reg_lambda": 3.0, "max_bin": 256, "early_stopping_rounds": 200,
    },
    # pytabkit tuned-default (TD/D) wrappers: the library's own benchmark-tuned
    # configs ARE the sensible screen; only the class varies.
    "tabm/screen": {"model_class": "TabM_D_Classifier",
                    "val_metric_name": "1-auc_ovr", "device": "cpu"},
    "ftt/screen": {"model_class": "FTT_D_Classifier",
                   "val_metric_name": "1-auc_ovr", "device": "cpu"},
    "tabr/screen": {"model_class": "TabR_S_D_Classifier",
                    "val_metric_name": "1-auc_ovr", "device": "cpu"},
    "mlp_plr/screen": {"model_class": "MLP_PLR_D_Classifier",
                       "val_metric_name": "1-auc_ovr", "device": "cpu"},
    # context/foundation models: max defensible context from the same rung,
    # full-eval scoring behind a runtime projection guard.
    "xrfm/screen": {"time_limit_s": 2400, "max_leaf_size": 30000},
    "tabpfn/screen": {"family": "tabpfn", "context_rows": 10000,
                      "batch_rows": 2000, "max_eval_minutes": 75},
    "tabicl/screen": {"family": "tabicl", "context_rows": 60000,
                      "batch_rows": 2000, "max_eval_minutes": 75},
    "tabdpt/screen": {"family": "tabdpt", "context_rows": 60000,
                      "batch_rows": 2000, "max_eval_minutes": 75},
    # the known RealMLP recipe (project_autosafe_realmlp_challenger config of
    # record): label smoothing OFF, checkpoint on val AUC, batch 4096, CPU.
    "realmlp/screen": {
        "n_epochs": 256, "batch_size": 4096, "device": "cpu",
        "use_ls": False, "ls_eps": 0.0, "val_metric_name": "1-auc_ovr",
        "use_early_stopping": True, "early_stopping_additive_patience": 20,
        "early_stopping_multiplicative_patience": 2, "verbosity": 0,
    },
    "realmlp/full": {
        "n_epochs": 512, "batch_size": 4096, "device": "cpu",
        "use_ls": False, "ls_eps": 0.0, "val_metric_name": "1-auc_ovr",
        "use_early_stopping": True, "early_stopping_additive_patience": 40,
        "early_stopping_multiplicative_patience": 2, "verbosity": 0,
    },
}

ARCHES = ("catboost_gbm", "lightgbm", "realmlp", "xgboost", "tabm", "ftt",
          "tabr", "mlp_plr", "tabpfn", "tabicl", "tabdpt", "xrfm")
GRADES = ("screen", "full")


def preset_params(arch: str, preset: str, overrides: Optional[dict] = None) -> dict:
    key = f"{arch}/{preset}"
    if key not in PRESETS:
        raise ValueError(f"unknown preset {key!r}; known: {sorted(PRESETS)}")
    params = dict(PRESETS[key])
    params.update(overrides or {})
    return params


# --- per-arch fits ----------------------------------------------------------

def _fit_catboost(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                  params: dict, seed: int, thread_count: int,
                  borders_path: Optional[str], has_time: bool = False):
    try:
        from catboost import CatBoostClassifier, Pool
    except ImportError as exc:                                  # pragma: no cover
        raise fc.LibraryUnavailable(f"catboost is not installed: {exc}")

    cat_idx = [train.feature_names.index(c) for c in train.categorical]
    for frame, label in ((train, "train"), (valid, "valid"), (eval_frame, "eval")):
        fc.assert_categorical_representation(frame, label)
        if has_time:
            fc.assert_time_sorted(frame, label)

    def pool(frame: fc.Frame, with_weight: bool) -> "Pool":
        return Pool(frame.matrix(), label=frame.y, cat_features=cat_idx,
                    weight=frame.weight if with_weight else None)

    train_pool, valid_pool = pool(train, True), pool(valid, True)
    quantization = "none"
    if borders_path:
        try:
            if os.path.exists(borders_path):
                train_pool.quantize(input_borders=borders_path)
                quantization = "reused"
            else:
                train_pool.quantize(border_count=params.get("border_count", 128))
                train_pool.save_quantization_borders(borders_path)
                quantization = "computed"
            valid_pool.quantize(input_borders=borders_path)
        except Exception as exc:                                # pragma: no cover
            quantization = f"unavailable:{type(exc).__name__}"

    model = CatBoostClassifier(random_seed=seed, thread_count=thread_count,
                               task_type="CPU", has_time=has_time, **params)
    model.fit(train_pool, eval_set=valid_pool)
    p = model.predict_proba(eval_frame.matrix())[:, 1]
    best_iter = model.get_best_iteration()
    n_trees = model.tree_count_
    curve = (model.get_evals_result().get("validation", {})
             .get(params.get("eval_metric", "AUC"), []))
    state = {
        "best_iteration": None if best_iter is None else int(best_iter),
        "n_iterations_run": int(n_trees),
        "iterations_requested": int(params.get("iterations", 0)),
        "early_stopped": bool(best_iter is not None
                              and int(best_iter) + 1 < int(params.get("iterations", 0))),
        "best_score": (float(model.get_best_score()["validation"][params["eval_metric"]])
                       if model.get_best_score().get("validation") else None),
        "eval_curve_tail": [float(v) for v in curve[-10:]],
        "quantization": quantization,
        "has_time": bool(has_time),
        "metric_period": int(params.get("metric_period", 1)),
        "converged": bool(best_iter is not None
                          and int(best_iter) + 1 < int(params.get("iterations", 0))),
    }
    return p, state, model


def _fit_lightgbm(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                  params: dict, seed: int, thread_count: int):
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise fc.LibraryUnavailable(
            f"lightgbm is not installed in this venv ({exc}). It is the ENSEMBLE "
            f"LEG ONLY and NO-FLOOR by design [OWNER-AMEND-4]; install it before "
            f"scheduling lightgbm cells. The preset is pinned in "
            f"fit_runner.PRESETS and reviewable without the library.")

    for frame, label in ((train, "train"), (valid, "valid"), (eval_frame, "eval")):
        fc.assert_categorical_representation(frame, label)
    params = dict(params)
    early = params.pop("early_stopping_rounds", None)
    params["n_jobs"] = thread_count
    cat_idx = [train.feature_names.index(c) for c in train.categorical]
    def frame_df(frame: fc.Frame):
        df = frame.matrix()
        for name in frame.categorical:
            df[name] = df[name].astype("category")
        return df

    model = lgb.LGBMClassifier(random_state=seed, **params)
    callbacks = [lgb.early_stopping(early, verbose=False)] if early else []
    model.fit(frame_df(train), train.y, sample_weight=train.weight,
              eval_set=[(frame_df(valid), valid.y)], eval_metric="auc",
              categorical_feature=cat_idx, callbacks=callbacks)
    p = model.predict_proba(frame_df(eval_frame))[:, 1]
    best = getattr(model, "best_iteration_", None)
    state = {"best_iteration": None if best is None else int(best),
             "n_iterations_run": int(model.n_estimators_),
             "iterations_requested": int(params.get("n_estimators", 0)),
             "early_stopped": bool(best and best < params.get("n_estimators", 0)),
             "best_score": None, "eval_curve_tail": [], "quantization": "n/a",
             "converged": bool(best and best < params.get("n_estimators", 0))}
    return p, state, model


def _fit_realmlp(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                 params: dict, seed: int, thread_count: int):
    try:
        from pytabkit import RealMLP_TD_Classifier
    except ImportError as exc:
        raise fc.LibraryUnavailable(
            f"pytabkit (RealMLP) is not installed in this venv ({exc}). The recipe "
            f"of record is pinned in fit_runner.PRESETS['realmlp/*'] -- label "
            f"smoothing OFF, AUC checkpoint selection, batch 4096, CPU, early "
            f"stopping -- and 3-seed means are mandatory (+/-0.001 seed noise).")

    # RealMLP REFUSES NaN in continuous columns (measured: "NaN values in
    # continuous columns are currently not allowed!"), and this frame is full of
    # them BY DESIGN -- every *_days_since is NULL when the thing was never
    # observed, and pre-2018 severity is NULL rather than zero. CatBoost and
    # LightGBM consume that missingness natively; RealMLP cannot, so the NaNs are
    # imputed with TRAIN medians and the choice is reported, never silent.
    medians = {name: float(np.nanmedian(values))
               for name, values in train.features.items()
               if name not in train.categorical
               and not np.all(np.isnan(values))}

    def frame_df(frame: fc.Frame):
        df = frame.matrix()
        for name in df.columns:
            if name in frame.categorical:
                continue
            df[name] = df[name].fillna(medians.get(name, 0.0))
        return df

    n_imputed = sum(1 for name, values in train.features.items()
                    if name not in train.categorical and np.isnan(values).any())
    model = RealMLP_TD_Classifier(random_state=seed, n_threads=thread_count, **params)
    model.fit(frame_df(train), train.y, X_val=frame_df(valid), y_val=valid.y,
              cat_col_names=train.categorical)
    p = model.predict_proba(frame_df(eval_frame))[:, 1]
    state = {"best_iteration": None, "n_iterations_run": int(params.get("n_epochs", 0)),
             "iterations_requested": int(params.get("n_epochs", 0)),
             "early_stopped": None, "best_score": None, "eval_curve_tail": [],
             "quantization": "n/a", "converged": None,
             "nan_imputation": {"rule": "train_median", "columns_imputed": n_imputed,
                                "why": "RealMLP refuses NaN in continuous columns; "
                                       "CatBoost/LightGBM consume the missingness natively"},
             "note": "3-seed mean mandatory for RealMLP (seed noise +/-0.001)",
             "medians": medians}
    return p, state, model


def _fit_xgboost(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                 params: dict, seed: int, thread_count: int):
    """Architecture-screen fitter (owner brief 2026-08-13): GBDT trio member.
    Same contract as _fit_lightgbm: HT weights on the loss, unweighted es-val
    AUC, native categorical handling via pandas category dtype."""
    try:
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise fc.LibraryUnavailable(f"xgboost not installed: {exc}")

    for frame, label in ((train, "train"), (valid, "valid"), (eval_frame, "eval")):
        fc.assert_categorical_representation(frame, label)
    params = dict(params)

    def frame_df(frame: fc.Frame):
        df = frame.matrix()
        for name in frame.categorical:
            df[name] = df[name].astype("category")
        return df

    model = XGBClassifier(random_state=seed, n_jobs=thread_count,
                          tree_method="hist", device="cpu",
                          enable_categorical=True, eval_metric="auc", **params)
    model.fit(frame_df(train), train.y, sample_weight=train.weight,
              eval_set=[(frame_df(valid), valid.y)], verbose=False)
    p = model.predict_proba(frame_df(eval_frame))[:, 1]
    best = getattr(model, "best_iteration", None)
    req = int(params.get("n_estimators", 0))
    state = {"best_iteration": None if best is None else int(best),
             "n_iterations_run": None if best is None else int(best) + 1,
             "iterations_requested": req,
             "early_stopped": bool(best is not None and best + 1 < req),
             "best_score": None, "eval_curve_tail": [], "quantization": "n/a",
             "converged": bool(best is not None and best + 1 < req)}
    return p, state, model


def _concurrent_compute_snapshot(min_cpu: float = 20.0):
    """Foreign compute processes (>= min_cpu% CPU, not this process tree).
    Recorded into every fit payload per the 2026-08-13 confound doctrine."""
    import subprocess
    me = os.getpid()
    try:
        out = subprocess.run(["ps", "-eo", "pid,ppid,%cpu,rss,comm"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception as exc:
        return [{"error": str(exc)}]
    mine = {me, os.getppid()}
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, ppid, cpu, rss, comm = parts
        try:
            pid_i, ppid_i, cpu_f = int(pid), int(ppid), float(cpu)
        except ValueError:
            continue
        if pid_i in mine or ppid_i in mine:
            continue
        if cpu_f >= min_cpu and ("ython" in comm or "duckdb" in comm.lower()):
            rows.append({"pid": pid_i, "cpu_pct": cpu_f,
                         "rss_mb": int(rss) // 1024, "comm": comm[-40:]})
    return rows


def _phase_logger():
    """Owner observability standard (ruling 2026-08-13 19:50): phase-boundary
    timestamps + a 60s heartbeat (elapsed, RSS) to stderr -> the job log.
    'Process alive + CPU' is not evidence of useful progress."""
    import resource
    import threading
    import time as _t
    t0 = _t.time()
    state = {"phase": "init", "stop": False}

    def mark(phase):
        state["phase"] = phase
        print(f"PHASE {phase} t=+{_t.time()-t0:.0f}s", file=sys.stderr, flush=True)

    def beat():
        while not state["stop"]:
            _t.sleep(60)
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1 << 20)
            print(f"HEARTBEAT phase={state['phase']} t=+{_t.time()-t0:.0f}s "
                  f"maxrss={rss}MB", file=sys.stderr, flush=True)
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    return mark, state


def _fit_pytabkit(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                  params: dict, seed: int, thread_count: int):
    """Architecture-screen fitter: any pytabkit tuned-default classifier
    (TabM_D, FTT_D, TabR_S_D, MLP_PLR_D, ...) named by params['model_class'].
    Mirrors _fit_realmlp's contract: train-median NaN imputation for continuous
    columns (reported, never silent), pytabkit fit/predict API, cat_col_names."""
    import pytabkit

    params = dict(params)
    cls_name = params.pop("model_class")
    try:
        cls = getattr(pytabkit, cls_name)
    except AttributeError as exc:
        raise fc.LibraryUnavailable(
            f"pytabkit has no {cls_name!r} in this venv: {exc}")

    medians = {name: float(np.nanmedian(values))
               for name, values in train.features.items()
               if name not in train.categorical
               and not np.all(np.isnan(values))}

    def frame_df(frame: fc.Frame):
        df = frame.matrix()
        for name in df.columns:
            if name in frame.categorical:
                continue
            df[name] = df[name].fillna(medians.get(name, 0.0))
        return df

    n_imputed = sum(1 for name, values in train.features.items()
                    if name not in train.categorical and np.isnan(values).any())
    mark, hb = _phase_logger()
    mark("encode")
    Xt, Xv = frame_df(train), frame_df(valid)
    val_metric_used = params.get("val_metric_name", "library-default")
    try:
        model = cls(random_state=seed, n_threads=thread_count, **params)
    except TypeError:
        # some pytabkit wrappers reject optional kwargs (e.g. val_metric_name)
        params.pop("val_metric_name", None)
        val_metric_used = "library-default"
        model = cls(random_state=seed, n_threads=thread_count, **params)
    mark("fit")
    try:
        model.fit(Xt, train.y, X_val=Xv, y_val=valid.y,
                  cat_col_names=train.categorical)
    except ValueError as exc:
        if "Validation metric" not in str(exc) or "val_metric_name" not in params:
            raise
        # rtdl-family wrappers (MLP-PLR, ResNet, sometimes FTT) don't implement
        # '1-auc_ovr'; fall back to the library default and RECORD the
        # selection-metric difference rather than failing the screen cell.
        params.pop("val_metric_name")
        val_metric_used = "library-default (1-auc_ovr unsupported)"
        model = cls(random_state=seed, n_threads=thread_count, **params)
        model.fit(Xt, train.y, X_val=Xv, y_val=valid.y,
                  cat_col_names=train.categorical)
    mark("predict")
    p = model.predict_proba(frame_df(eval_frame))[:, 1]
    mark("serialise")
    hb["stop"] = True
    state = {"best_iteration": None, "n_iterations_run": None,
             "iterations_requested": None, "early_stopped": None,
             "best_score": None, "eval_curve_tail": [], "quantization": "n/a",
             "converged": None, "model_class": cls_name,
             "val_metric_used": val_metric_used,
             "nan_imputation": {"rule": "train_median", "columns_imputed": n_imputed},
             "note": "architecture screen: tuned-default config, 2-seed screen "
                     "grade; contender status requires the 3-seed neural rule"}
    return p, state, model


def _fit_context_model(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                       params: dict, seed: int, thread_count: int):
    """Architecture-screen fitter for context/foundation models (TabPFN,
    TabICL, TabDPT). These consume a bounded TRAIN CONTEXT, not the full rung —
    the matched comparison is 'max defensible context drawn from the same rung,
    same eval rows, same target/metric', with the limitation recorded verbatim
    in convergence_state (owner brief 2026-08-13: never force artificial
    equivalence, make it explicit instead).

    Preprocessing: ordinal-encode categoricals (category codes, NaN=-1),
    train-median impute continuous. Eval is predicted for the FULL eval frame
    in batches; a runtime projection guard refuses early rather than burning
    hours (a refusal is a legitimate screen outcome: CPU-infeasible at matched
    eval)."""
    import time

    params = dict(params)
    kind = params.pop("family")
    context_rows = int(params.pop("context_rows"))
    batch_rows = int(params.pop("batch_rows", 2000))
    max_eval_minutes = float(params.pop("max_eval_minutes", 75.0))

    if kind == "tabpfn":
        from tabpfn import TabPFNClassifier
        cls = lambda: TabPFNClassifier(device="cpu", **params)  # noqa: E731
    elif kind == "tabicl":
        from tabicl import TabICLClassifier
        cls = lambda: TabICLClassifier(device="cpu", **params)  # noqa: E731
    elif kind == "tabdpt":
        from tabdpt import TabDPTClassifier
        cls = lambda: TabDPTClassifier(device="cpu", **params)  # noqa: E731
    else:
        raise ValueError(f"unknown context-model family {kind!r}")

    medians = {name: float(np.nanmedian(values))
               for name, values in train.features.items()
               if name not in train.categorical
               and not np.all(np.isnan(values))}

    def encoded(frame: fc.Frame):
        df = frame.matrix()
        for name in df.columns:
            if name in frame.categorical:
                df[name] = df[name].astype("category").cat.codes.astype("float32")
            else:
                df[name] = df[name].fillna(medians.get(name, 0.0)).astype("float32")
        return df.to_numpy()

    rng = np.random.default_rng(seed)
    n_train = len(train.y)
    take = min(context_rows, n_train)
    idx = rng.choice(n_train, size=take, replace=False)
    X_all = encoded(train)
    model = cls()
    model.fit(X_all[idx], train.y[idx])

    X_eval = encoded(eval_frame)
    n_eval = X_eval.shape[0]
    p = np.empty(n_eval, dtype=np.float64)
    t0 = time.time()
    probe = min(batch_rows, n_eval)
    p[:probe] = model.predict_proba(X_eval[:probe])[:, 1]
    projected_min = (time.time() - t0) / probe * n_eval / 60.0
    if projected_min > max_eval_minutes:
        raise RuntimeError(
            f"{kind}: projected full-eval scoring {projected_min:.0f} min > "
            f"cap {max_eval_minutes:.0f} min on CPU — recorded as "
            f"CPU-INFEASIBLE-AT-MATCHED-EVAL, a screen outcome, not an error "
            f"in the model.")
    for lo in range(probe, n_eval, batch_rows):
        hi = min(lo + batch_rows, n_eval)
        p[lo:hi] = model.predict_proba(X_eval[lo:hi])[:, 1]

    state = {"best_iteration": None, "n_iterations_run": None,
             "iterations_requested": None, "early_stopped": None,
             "best_score": None, "eval_curve_tail": [], "quantization": "n/a",
             "converged": None, "family": kind,
             "context_rows_used": int(take), "rung_rows_available": int(n_train),
             "matched_comparison_limitation": (
                 f"{kind} consumed a {take:,}-row seeded context from the "
                 f"{n_train:,}-row rung (context ceiling), not the full rung: "
                 f"levels are NOT rows-matched to the GBDT/MLP anchors; eval "
                 f"rows, target and metric ARE matched."),
             "eval_scoring_minutes": round((time.time() - t0) / 60.0, 2)}
    return p, state, model


def _fit_xrfm(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
              params: dict, seed: int, thread_count: int):
    """Architecture-screen fitter: xRFM (tree-partitioned Recursive Feature
    Machine, pip xrfm — owner wave-2 directive 2026-08-13). Distinct inductive
    bias: local partitioning + learned feature geometry (AGOP). Preprocessing
    mirrors the context models: ordinal-encoded categoricals, train-median
    imputation. The es-val split is passed for its internal tuning when the
    API accepts it."""
    try:
        from xrfm import xRFM
    except ImportError as exc:
        raise fc.LibraryUnavailable(f"xrfm not installed: {exc}")

    params = dict(params)
    medians = {name: float(np.nanmedian(values))
               for name, values in train.features.items()
               if name not in train.categorical
               and not np.all(np.isnan(values))}

    def encoded(frame: fc.Frame):
        df = frame.matrix()
        for name in df.columns:
            if name in frame.categorical:
                df[name] = df[name].astype("category").cat.codes.astype("float32")
            else:
                df[name] = df[name].fillna(medians.get(name, 0.0)).astype("float32")
        return df.to_numpy()

    import time as _time

    batch_rows = int(params.pop("batch_rows", 5000))
    max_eval_minutes = float(params.pop("max_eval_minutes", 75.0))
    model = xRFM(random_state=seed, n_threads=thread_count, device="cpu",
                 verbose=False, **params)
    Xt, Xv = encoded(train), encoded(valid)
    try:
        model.fit(Xt, train.y, Xv, valid.y)
    except TypeError:
        model.fit(Xt, train.y)

    # Batched eval with a projection guard — the first xRFM attempt sat 60 min
    # in one monolithic kernel-predict over 330k rows (sampled: aten kernel
    # frames, no bound, no progress). Same discipline as the context models.
    X_eval = encoded(eval_frame)
    n_eval = X_eval.shape[0]
    p = np.empty(n_eval, dtype=np.float64)

    def batch_proba(lo, hi):
        proba = model.predict_proba(X_eval[lo:hi])
        return proba[:, 1] if getattr(proba, "ndim", 1) == 2 else np.asarray(proba)

    t0 = _time.time()
    probe = min(batch_rows, n_eval)
    p[:probe] = batch_proba(0, probe)
    projected_min = (_time.time() - t0) / probe * n_eval / 60.0
    if projected_min > max_eval_minutes:
        raise RuntimeError(
            f"xrfm: projected full-eval scoring {projected_min:.0f} min > cap "
            f"{max_eval_minutes:.0f} min on CPU — CPU-INFEASIBLE-AT-MATCHED-EVAL "
            f"(screen outcome).")
    for lo in range(probe, n_eval, batch_rows):
        p[lo:lo + batch_rows] = batch_proba(lo, min(lo + batch_rows, n_eval))
    state = {"best_iteration": None, "n_iterations_run": None,
             "iterations_requested": None, "early_stopped": None,
             "best_score": None, "eval_curve_tail": [], "quantization": "n/a",
             "converged": None, "family": "xrfm",
             "note": "wave-2 screen: tree-partitioned RFM; ordinal-encoded "
                     "categoricals + train-median imputation (recorded)"}
    return p, state, model


FITTERS = {"catboost_gbm": _fit_catboost, "lightgbm": _fit_lightgbm,
           "realmlp": _fit_realmlp, "xgboost": _fit_xgboost,
           "tabm": _fit_pytabkit, "ftt": _fit_pytabkit,
           "tabr": _fit_pytabkit, "mlp_plr": _fit_pytabkit,
           "tabpfn": _fit_context_model, "tabicl": _fit_context_model,
           "tabdpt": _fit_context_model, "xrfm": _fit_xrfm}


def split_validation(frame: fc.Frame, fraction: float, seed: int,
                     mode: str = "vehicle"):
    """Validation split for early stopping.

    `mode="vehicle"` (default, unchanged): a random vehicle-clustered slice.
    Clustered by vehicle so a vehicle's rows never straddle the split -- an
    in-sample sibling row would make early stopping optimistic.

    `mode="temporal"` [PREREG_OVERFIT_2026_08_16 R5]: the LAST `fraction` of
    rows by `tgt_date`. The default draws its stopping surface at random from
    the same era as the fit rows, so early stopping is tuned in-era and then
    judged out-of-era; this mode asks whether that is where the measured
    ES-val -> eval gap comes from.

    Vehicle disjointness is preserved in BOTH modes. After the date cut, any
    vehicle appearing in the valid part is removed from the fit part -- never
    the reverse, which would leave the valid part populated only by vehicles
    that first appear late (a different population, and a first-MOT-enriched
    one). The dropped-row count is returned to the caller through
    `frame`-independent bookkeeping in `run_fit`, so the cost is reported and
    not silently absorbed.
    """
    if mode == "temporal":
        return _split_temporal(frame, fraction)
    if mode != "vehicle":
        raise ValueError(f"valid_split must be 'vehicle' or 'temporal', got {mode!r}")
    vehicles = np.unique(frame.vehicle_id)
    rng = np.random.default_rng(seed)
    held = set(rng.choice(vehicles, size=max(1, int(len(vehicles) * fraction)),
                          replace=False).tolist())
    mask = np.array([v in held for v in frame.vehicle_id])
    return _subset(frame, ~mask), _subset(frame, mask)


def _split_temporal(frame: fc.Frame, fraction: float):
    """Last `fraction` of rows by tgt_date as valid; vehicle-disjoint fit part.

    Deterministic and seed-free by construction -- which is the point. The
    vehicle mode's draw is seeded with the MODEL seed, and that draw alone
    moves the ES-val AUROC by a measured -0.002490 between seeds 101 and 202
    while leaving the eval AUROC at +0.000032 (PREREG_OVERFIT_2026_08_16 §2).
    A temporal cut has no such degree of freedom.
    """
    days = np.array([(fc._as_date(d) or date(1900, 1, 1)).toordinal()
                     for d in frame.tgt_date], dtype=np.int64)
    cut = np.quantile(days, 1.0 - float(fraction), method="lower")
    valid_mask = days > cut
    if not valid_mask.any() or valid_mask.all():
        raise ValueError(
            f"temporal split at day {cut} put {int(valid_mask.sum())} of "
            f"{len(days)} rows in the valid part; the frame's date range "
            f"cannot support fraction={fraction}.")
    held_vehicles = set(np.unique(frame.vehicle_id[valid_mask]).tolist())
    straddle = np.array([v in held_vehicles for v in frame.vehicle_id]) & ~valid_mask
    return _subset(frame, ~valid_mask & ~straddle), _subset(frame, valid_mask)


def _subset(frame: fc.Frame, mask) -> fc.Frame:
    return fc.Frame(
        test_id=frame.test_id[mask], vehicle_id=frame.vehicle_id[mask],
        tgt_date=frame.tgt_date[mask], tgt_outcome=frame.tgt_outcome[mask],
        y=frame.y[mask], weight=frame.weight[mask],
        features={n: v[mask] for n, v in frame.features.items()},
        categorical=list(frame.categorical))


def save_model(path: str, model, arch: str, train: "fc.Frame", config: dict,
               payload_extra: dict) -> str:
    """Persist a fitted model + everything score_runner needs to reproduce it.

    Pickle for every arch (CatBoost, LightGBM and RealMLP estimators all pickle
    in this env) plus a sidecar JSON, so scoring never has to re-derive the
    feature order, the categorical set or the RealMLP imputation medians -- any
    of which drifting would silently change the score.
    """
    import pickle

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)
    meta = {
        "arch": arch,
        "feature_names": list(train.feature_names),
        "categorical": list(train.categorical),
        "config_sha": fc.config_sha(config),
        "label": config.get("label", "y_final"),
        "extra_frame": config.get("extra_frame"),
        "extra_eval_frame": config.get("extra_eval_frame"),
        "featureset": config["featureset"],
        "extra_columns": config.get("extra_columns"),
    }
    meta.update(payload_extra)
    with open(path + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, default=str)
    return path


def run_fit(config: dict, frame_glob: str, eval_glob: str, seed: int, cell: str,
            arm: str, out_dir: str, *, con=None, thread_count: int = 1,
            borders_path: Optional[str] = None,
            preds_dir: Optional[str] = None,
            model_path: Optional[str] = None) -> dict:
    """Train one fit and emit the contract. Returns the fit JSON payload."""
    import duckdb

    arch = config.get("arch", "catboost_gbm")
    if arch not in ARCHES:
        raise ValueError(f"arch {arch!r} must be one of {ARCHES}")
    preset = config.get("preset", "screen")
    grade = config.get("grade", "screen" if preset == "screen" else "full")
    if grade not in GRADES:
        raise ValueError(f"grade {grade!r} must be one of {GRADES}")
    params = preset_params(arch, preset, config.get("params"))
    has_time = bool(config.get("has_time", False))
    if has_time and arch != "catboost_gbm":
        raise ValueError(f"has_time is a CatBoost ordering contract; arch={arch}")

    con = con or duckdb.connect()
    if config.get("memory_limit"):
        con.execute(f"PRAGMA memory_limit='{config['memory_limit']}'")
    columns = fc.resolve_featureset(config["featureset"],
                                    config.get("extra_columns"))
    label = config.get("label", "y_final")
    train = fc.load_frame(con, frame_glob, columns, label,
                          use_weights=config.get("use_weights", True),
                          extra_glob=config.get("extra_frame"),
                          row_filter=config.get("row_filter"))
    eval_frame = fc.load_frame(con, eval_glob, columns, label,
                               use_weights=False,
                               extra_glob=config.get("extra_eval_frame")
                               or config.get("extra_frame"))
    if train.n_rows == 0 or eval_frame.n_rows == 0:
        raise ValueError("empty train or eval frame after filtering")

    # PLANTED NULL [PREREG_OVERFIT_2026_08_16 R6]. Permuting the label BEFORE
    # the split is the whole point: the fit rows, the early-stopping rows and
    # the CTR target statistics must ALL see noise. Shuffling after the split
    # would leave the two parts internally consistent with each other and test
    # nothing. The EVAL frame is never touched -- its labels are the real ones,
    # which is what makes the resulting AUROC a chance-level read on the real
    # target rather than a comparison of two shuffles.
    shuffle_seed = config.get("shuffle_label_seed")
    label_shuffle = None
    if shuffle_seed is not None:
        before = int(train.y.sum())
        original = train.y.copy()
        train.y = original[np.random.default_rng(int(shuffle_seed))
                           .permutation(train.n_rows)]
        label_shuffle = {"seed": int(shuffle_seed), "n_rows": int(train.n_rows),
                         "positives_before": before,
                         "positives_after": int(train.y.sum()),
                         # a permutation that happened to be near-identity would
                         # be a silently useless null; count the moved rows.
                         "n_positions_changed": int((original != train.y).sum()),
                         "eval_labels_touched": False}
        if label_shuffle["positives_after"] != before:
            raise fc.FenceViolation(
                "label shuffle changed the positive count; it must be a "
                "permutation, not a resample.")

    valid_split = config.get("valid_split", "vehicle")
    fit_part, valid_part = split_validation(
        train, config.get("valid_fraction", 0.15), seed, mode=valid_split)
    fitter = FITTERS[arch]
    foreign_before = _concurrent_compute_snapshot()
    if arch == "catboost_gbm":
        p, state, model = fitter(fit_part, valid_part, eval_frame, params, seed,
                                 thread_count, borders_path, has_time)
    else:
        p, state, model = fitter(fit_part, valid_part, eval_frame, params, seed,
                                 thread_count)
    state["valid_rows"] = int(valid_part.n_rows)
    state["valid_vehicles"] = int(len(np.unique(valid_part.vehicle_id)))
    state["valid_split"] = valid_split
    if valid_split == "temporal":
        # The disjointness drop is a real cost of this split, so it is recorded
        # rather than absorbed: fit_part + valid_part < train for this mode.
        state["fit_rows"] = int(fit_part.n_rows)
        state["rows_dropped_for_vehicle_disjointness"] = int(
            train.n_rows - fit_part.n_rows - valid_part.n_rows)
        fit_dates = [fc._as_date(d) for d in fit_part.tgt_date]
        val_dates = [fc._as_date(d) for d in valid_part.tgt_date]
        state["fit_max_tgt_date"] = max(d for d in fit_dates if d).isoformat()
        state["valid_min_tgt_date"] = min(d for d in val_dates if d).isoformat()
    if label_shuffle is not None:
        state["label_shuffle"] = label_shuffle
    # Owner doctrine 2026-08-13: concurrent foreign compute is an experimental
    # confound for every feasibility/runtime judgment. Captured automatically.
    state["concurrent_compute"] = {"before_fit": foreign_before,
                                   "after_fit": _concurrent_compute_snapshot()}

    p_stored = metrics.as_stored(p)
    preds_path = os.path.join(preds_dir or os.path.join(out_dir, "preds"),
                              f"{cell}.seed{seed}.parquet")
    fc.write_keyed_preds(preds_path, eval_frame.test_id, eval_frame.vehicle_id,
                         eval_frame.y, p_stored)
    train_ids_path = os.path.join(preds_dir or os.path.join(out_dir, "preds"),
                                  f"{cell}.seed{seed}.train_ids.parquet")
    fc.write_train_ids(train_ids_path, train.test_id)

    payload = fc.build_fit_json(
        cell, arm, seed, eval_frame, p_stored, preds_path,
        arch=arch, featureset=",".join(str(f) for f in config["featureset"]),
        grade=grade, surface=config.get("surface", "panel"),
        rung_rows=config.get("rung_rows"), config=config,
        convergence_state=state, train=train,
        extra={"label": label, "preset": preset, "params": params,
               "has_time": has_time,
               "train_ids_path": train_ids_path,
               "base": config.get("base", "b0-104"),
               **({"ref_cell": config["ref_cell"]} if config.get("ref_cell") else {})})
    if model_path:
        # Screen-grade cells must NOT save: a screen model is a measurement, not
        # an artifact, and keeping it invites scoring from a model no read ever
        # licensed. Full-grade (F8 / anchors) is the sanctioned case.
        if payload["grade"] != "full":
            raise fc.FenceViolation(
                f"--save-model on a {payload['grade']}-grade fit ({cell}). Only "
                f"full-grade cells may persist a model; screen fits are "
                f"measurements, not artifacts.")
        # Every saved model records WHAT it may be used for; an arm that claims
        # deployability is held to it. Research arms still save (scoring, SHAP)
        # but are stamped non-deployable so nothing downstream can mistake one
        # for a shippable artifact.
        census = fc.serve_class_census(columns)
        if config.get("require_deployable"):
            fc.assert_deployable(columns)
        save_model(model_path, model, arch, train, config, {
            "source_cell": cell, "source_seed": int(seed), "source_arm": arm,
            "grade": payload["grade"],
            "serve_class_census": census,
            "featureset_sha256": fc.featureset_hash(columns),
            "max_train_target_date": payload["max_train_target_date"],
            "train_mixture": payload["train_mixture"],
            "convergence_state": state, "eval_auroc_at_fit": payload["auroc"],
            "surface_at_fit": payload["surface"]})
        payload["model_path"] = model_path
    fc.write_fit_json(out_dir, cell, seed, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.fit_runner",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frame", required=True, help="training frame parquet glob")
    ap.add_argument("--eval-frame", required=True, help="eval-slice parquet glob")
    ap.add_argument("--config", required=True, help="config JSON path")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--cell", required=True, help="prereg cell id, e.g. s2.D.cum.b0")
    ap.add_argument("--arm", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--preds-dir", default=None)
    ap.add_argument("--thread-count", type=int, default=1,
                    help="one compute job at a time; CPU only, never MPS")
    ap.add_argument("--save-model", default=None,
                    help="persist the fitted model for score_runner; FULL-GRADE ONLY")
    ap.add_argument("--borders", default=None,
                    help="quantisation-border cache reused across seeds "
                         "(bit-identical binning)")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    with open(a.config, encoding="utf-8") as fh:
        config = json.load(fh)
    try:
        payload = run_fit(config, a.frame, a.eval_frame, a.seed, a.cell, a.arm,
                          a.out_dir, thread_count=a.thread_count,
                          borders_path=a.borders, preds_dir=a.preds_dir,
                          model_path=a.save_model)
    except (fc.FenceViolation, fc.LibraryUnavailable) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        if "INFEASIBLE" not in str(exc):
            raise
        # REFUSAL: a legitimate, informative screen outcome (e.g. projected
        # eval time over cap). Distinct exit code 7 lets the queue record-and-
        # continue instead of halting, and the payload makes the refusal DATA
        # in the results dir — visible to the board, not buried in a job log.
        refusal = {"cell": a.cell, "arm": a.arm, "seed": a.seed,
                   "outcome": "REFUSAL", "reason": str(exc),
                   "config": a.config}
        out_dir = Path(a.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"_{a.cell}.seed{a.seed}.REFUSED.json").write_text(
            json.dumps(refusal, indent=1))
        print(f"REFUSAL (recorded): {exc}", file=sys.stderr)
        return 7
    print(json.dumps({k: payload[k] for k in
                      ("cell", "arm", "seed", "auroc", "auprc", "logloss",
                       "keyed_preds_path", "grade")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
