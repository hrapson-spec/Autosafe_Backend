#!/usr/bin/env python3
"""Score-only SHAP diagnostic on the FINAL model — a leakage tripwire.

Never trains, never refits: it loads a model saved by `fit_runner --save-model`
and asks it, over a subsample of the 2024 selection slice, which features carry
its decisions. Two things it is for:

1. **Dominance flag.** If one feature's |mean SHAP| exceeds the second's by more
   than `--dominance-ratio` (default 3x), that is the classic leakage signature:
   a single column carrying the model. It is a TRIPWIRE, not a verdict — a
   genuinely dominant prior (an EB rate on a 104-feature base) can trip it
   honestly. It says LOOK, not FAIL, and the JSON says so.
2. A per-feature ranking that can be read against the block registry, so an
   adopted block's contribution is visible rather than inferred from a delta.

CatBoost only: `get_feature_importance(type='ShapValues')` is exact for trees.
Any other arch refuses rather than substituting a different estimator, because
SHAP values from two different approximations are not comparable.
"""
import argparse
import json
import os
import sys
from typing import List, Optional

import numpy as np

from . import fit_contract as fc
from . import score_runner

DEFAULT_SUBSAMPLE = 50_000
DEFAULT_TOP_N = 30
DEFAULT_DOMINANCE_RATIO = 3.0


def run(model_path: str, frame_glob: str, out_path: str, *, con=None,
        subsample: int = DEFAULT_SUBSAMPLE, top_n: int = DEFAULT_TOP_N,
        dominance_ratio: float = DEFAULT_DOMINANCE_RATIO, seed: int = 20260812,
        extra_frame: Optional[str] = None) -> dict:
    import duckdb

    model, meta = score_runner.load_artifact(model_path)
    if meta["arch"] != "catboost_gbm":
        raise score_runner.ModelArtifactError(
            f"SHAP diagnostic is CatBoost-only (exact tree SHAP); this model is "
            f"{meta['arch']}. Refusing to substitute a different estimator -- "
            f"SHAP values from two approximations are not comparable.")
    try:
        from catboost import Pool
    except ImportError as exc:                                  # pragma: no cover
        raise fc.LibraryUnavailable(f"catboost is not installed: {exc}")

    con = con or duckdb.connect()
    frame = fc.load_frame(con, frame_glob, meta["feature_names"],
                          meta.get("label", "y_final"), use_weights=False,
                          extra_glob=extra_frame or meta.get("extra_eval_frame"))
    n_total = frame.n_rows
    rng = np.random.default_rng(seed)
    take = (np.arange(n_total) if n_total <= subsample
            else np.sort(rng.choice(n_total, size=subsample, replace=False)))
    subset = fc.Frame(
        test_id=frame.test_id[take], vehicle_id=frame.vehicle_id[take],
        tgt_date=frame.tgt_date[take], tgt_outcome=frame.tgt_outcome[take],
        y=frame.y[take], weight=frame.weight[take],
        features={n: v[take] for n, v in frame.features.items()},
        categorical=list(frame.categorical))
    fc.assert_categorical_representation(subset, "shap-subsample")

    names = meta["feature_names"]
    subset.features = {n: subset.features[n] for n in names}
    cat_idx = [names.index(c) for c in subset.categorical]
    pool = Pool(subset.matrix(), label=subset.y, cat_features=cat_idx)
    shap = np.asarray(model.get_feature_importance(type="ShapValues", data=pool))
    contributions = shap[:, :-1]          # last column is the expected value
    mean_abs = np.abs(contributions).mean(axis=0)

    order = np.argsort(-mean_abs)
    ranked = [{"rank": i + 1, "feature": names[j],
               "mean_abs_shap": float(mean_abs[j]),
               "mean_shap": float(contributions[:, j].mean())}
              for i, j in enumerate(order[:top_n])]
    top1 = float(mean_abs[order[0]]) if len(order) else 0.0
    top2 = float(mean_abs[order[1]]) if len(order) > 1 else 0.0
    dominant = bool(top2 > 0 and top1 > dominance_ratio * top2)

    payload = {
        "model": model_path,
        "source_cell": meta.get("source_cell"),
        "source_seed": meta.get("source_seed"),
        "config_sha": meta["config_sha"],
        "scored_frame": frame_glob,
        "rows_available": int(n_total),
        "rows_used": int(len(take)),
        "subsample_seed": seed,
        "n_features": len(names),
        "expected_value": float(shap[:, -1].mean()),
        "top_features": ranked,
        "dominance": {
            "flag": dominant,
            "ratio": (top1 / top2) if top2 > 0 else None,
            "threshold": dominance_ratio,
            "top_feature": names[order[0]] if len(order) else None,
            "second_feature": names[order[1]] if len(order) > 1 else None,
            "meaning": ("TRIPWIRE, not a verdict: one feature carrying the model "
                        "is the classic leakage signature, but a genuinely "
                        "dominant prior can trip it honestly. Investigate the "
                        "named feature's as-of construction; do not fail a read "
                        "on this flag alone."),
        },
        "method": ("CatBoost exact tree SHAP (get_feature_importance "
                   "type='ShapValues'); score-only, the model is never refitted."),
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.shap_diagnostic",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-frame", default=None)
    ap.add_argument("--subsample", type=int, default=DEFAULT_SUBSAMPLE)
    ap.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    ap.add_argument("--dominance-ratio", type=float, default=DEFAULT_DOMINANCE_RATIO)
    ap.add_argument("--seed", type=int, default=20260812)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        payload = run(a.model, a.frame, a.out, subsample=a.subsample,
                      top_n=a.top_n, dominance_ratio=a.dominance_ratio,
                      seed=a.seed, extra_frame=a.extra_frame)
    except (score_runner.ModelArtifactError, fc.LibraryUnavailable) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({"top_features": payload["top_features"][:5],
                      "dominance": payload["dominance"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
