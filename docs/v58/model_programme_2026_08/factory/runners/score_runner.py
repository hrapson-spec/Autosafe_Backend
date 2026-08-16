#!/usr/bin/env python3
"""SCORE-ONLY runner. It never trains.

This is what B23's SEALED ONE-TOUCH READ uses, and what B22's drift read uses.
The distinction is the whole point: `fit_runner` always trains, so pointing it
at the sealed 2025-H2 frame would be a SECOND FIT and would break the one-touch
seal. This module loads a model saved by `fit_runner --save-model` (full-grade
only) and applies it. There is no training path in this file.

    python -m factory.runners.score_runner \
        --model out/models/s2.D.confirm.final.seed101.pkl \
        --frame 'out/frames_confirm/recipe=confirm2025h2/rung=all/frame/*.parquet' \
        --cell s2.D.confirm.sealed2025h2 --arm confirm --out-dir out/fits/confirm

Emits the same harness contract as a fit (`ablation_tables.py:21-28`) with:

    grade         'score_only'
    config_sha    the SOURCE FIT's config_sha, carried verbatim -- the score is
                  a property of that model, not of a new configuration
    scored_from   {model, source_cell, source_seed, eval_auroc_at_fit}

`max_train_target_date` and `train_mixture` are carried from the source fit too:
the harness's date/COVID fences are statements about the MODEL's training data,
which scoring does not change.
"""
import argparse
import json
import os
import pickle
import sys
from typing import List, Optional

import numpy as np

from . import fit_contract as fc
from . import metrics

GRADE = "score_only"


class ModelArtifactError(RuntimeError):
    """The model or its sidecar is missing, unreadable, or does not match."""


def load_artifact(model_path: str):
    """(model, meta). Refuses a model with no sidecar -- feature ORDER matters."""
    meta_path = model_path + ".meta.json"
    for path in (model_path, meta_path):
        if not os.path.exists(path):
            raise ModelArtifactError(
                f"missing {path}. score_runner needs both the pickled model and "
                f"its .meta.json (feature order, categorical set, imputation "
                f"medians, source config_sha); re-run the fit with --save-model.")
    with open(model_path, "rb") as fh:
        model = pickle.load(fh)
    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    if meta.get("grade") != "full":
        raise ModelArtifactError(
            f"{model_path} was saved from a {meta.get('grade')!r}-grade fit. Only "
            f"full-grade models may be scored from.")
    return model, meta


def _design(frame: fc.Frame, meta: dict):
    """Rebuild the design matrix in the SOURCE FIT's exact column order."""
    missing = [c for c in meta["feature_names"] if c not in frame.features]
    if missing:
        raise ModelArtifactError(
            f"the frame is missing {len(missing)} column(s) the model was fitted "
            f"on, e.g. {missing[:5]}. Scoring a model on a different feature "
            f"space silently produces a different model.")
    frame.features = {name: frame.features[name] for name in meta["feature_names"]}
    frame.categorical = [c for c in meta["categorical"] if c in frame.features]
    df = frame.matrix()
    medians = (meta.get("convergence_state") or {}).get("medians")
    if medians:                       # RealMLP refuses NaN; reuse TRAIN medians
        for name in df.columns:
            if name not in frame.categorical:
                df[name] = df[name].fillna(medians.get(name, 0.0))
    return df


def run_score(model_path: str, frame_glob: str, cell: str, arm: str, out_dir: str,
              *, con=None, seed: Optional[int] = None,
              preds_dir: Optional[str] = None, extra_frame: Optional[str] = None,
              row_filter: Optional[str] = None) -> dict:
    import duckdb

    model, meta = load_artifact(model_path)
    con = con or duckdb.connect()
    frame = fc.load_frame(
        con, frame_glob, meta["feature_names"], meta.get("label", "y_final"),
        use_weights=False, extra_glob=extra_frame or meta.get("extra_eval_frame"),
        row_filter=row_filter)
    if frame.n_rows == 0:
        raise ModelArtifactError(f"no rows to score in {frame_glob}")

    p_stored = metrics.as_stored(model.predict_proba(_design(frame, meta))[:, 1])
    seed = int(meta.get("source_seed", 0)) if seed is None else int(seed)
    preds_path = os.path.join(preds_dir or os.path.join(out_dir, "preds"),
                              f"{cell}.seed{seed}.parquet")
    fc.write_keyed_preds(preds_path, frame.test_id, frame.vehicle_id, frame.y,
                         p_stored)

    payload = {
        "cell": cell, "arm": arm, "seed": seed,
        "auroc": metrics.auroc(frame.y, p_stored),
        "auprc": metrics.auprc(frame.y, p_stored),
        "logloss": metrics.logloss(frame.y, p_stored),
        "keyed_preds_path": preds_path,
        "arch": meta["arch"],
        "featureset": ",".join(str(f) for f in meta.get("featureset") or []),
        "rung_rows": None,
        "surface": os.path.basename(str(frame_glob)),
        # the fences describe the MODEL's training data; scoring changes neither
        "max_train_target_date": meta.get("max_train_target_date"),
        "train_mixture": meta.get("train_mixture"),
        "config_sha": meta["config_sha"],
        "auroc_fprs": metrics.auroc_fprs(frame.y, p_stored),
        "grade": GRADE,
        "convergence_state": {"scored_only": True, "trained_here": False,
                              **{k: v for k, v in
                                 (meta.get("convergence_state") or {}).items()
                                 if k != "medians"}},
        "n_eval_rows": int(frame.n_rows),
        "n_eval_vehicles": int(len(np.unique(frame.vehicle_id))),
        "n_features": len(meta["feature_names"]),
        "scored_from": {"model": model_path,
                        "source_cell": meta.get("source_cell"),
                        "source_seed": meta.get("source_seed"),
                        "source_arm": meta.get("source_arm"),
                        "eval_auroc_at_fit": meta.get("eval_auroc_at_fit"),
                        "surface_at_fit": meta.get("surface_at_fit")},
        "scored_frame": str(frame_glob),
    }
    reproduced = metrics.auroc(frame.y, np.asarray(p_stored, dtype=np.float32))
    if abs(reproduced - payload["auroc"]) > fc.AUROC_RECOMPUTE_TOL:
        raise fc.FenceViolation(
            f"reported auroc {payload['auroc']!r} does not reproduce from the "
            f"stored float32 preds ({reproduced!r}); the harness would exit 4.")
    fc.write_fit_json(out_dir, cell, seed, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.score_runner",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="path saved by --save-model")
    ap.add_argument("--frame", required=True, help="frame parquet glob to score")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--preds-dir", default=None)
    ap.add_argument("--seed", type=int, default=None,
                    help="defaults to the source fit's seed")
    ap.add_argument("--extra-frame", default=None,
                    help="B0-104 frame joined on test_id (defaults to the model's)")
    ap.add_argument("--row-filter", default=None)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        payload = run_score(a.model, a.frame, a.cell, a.arm, a.out_dir,
                            seed=a.seed, preds_dir=a.preds_dir,
                            extra_frame=a.extra_frame, row_filter=a.row_filter)
    except (ModelArtifactError, fc.FenceViolation) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({k: payload[k] for k in
                      ("cell", "arm", "seed", "auroc", "grade",
                       "keyed_preds_path")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
