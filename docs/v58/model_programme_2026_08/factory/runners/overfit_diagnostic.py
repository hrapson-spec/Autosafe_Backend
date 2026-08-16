#!/usr/bin/env python3
"""Overfitting diagnostic for a SAVED full-grade model. Read-only; never fits.

Prereg: `prereg/PREREG_OVERFIT_2026_08_16.md` (sha256 89cf85befb1c0005...).

    python -m factory.runners.overfit_diagnostic \
        --model out/models/drift_cb_s101.pkl \
        --config out/configs/drift.cb.json --seed 101 \
        --surface train \
        --frame "out/frames/recipe=flat4y/rung=r1m/frame/*.parquet" \
        --extra-frame out/b0/b0_flat4y_eb.parquet \
        --out-dir out/overfit

ONE surface per invocation, by design: the box is an 8 GB Mac and a 1M-row
241-column frame plus its design matrix is already most of it. Shards are merged
by `--merge` afterwards. See [[feedback_8gb_oom_parquet]].

`--surface train` reconstructs the fit's OWN partition with the same
`fit_runner.split_validation(frame, valid_fraction, seed)` call and emits curves
for BOTH parts, plus the instrument proof.

INSTRUMENT PROOF (prereg 4.0, BLOCKING). Two independent checks:

  structural  the reconstructed valid part must have exactly the `valid_rows`
              and `valid_vehicles` the fit recorded;
  metric      AUROC on the valid part at `ntree_end=tree_count_` must reproduce
              `convergence_state.best_score` to <= --proof-tol.

DECLARED BEFORE THE FIRST RUN, and it is a clarification of prereg 4.0 rather
than a choice made after seeing a number: **the metric gate is evaluated in
float64.** CatBoost computed `best_score` in float64 from the raw approx; a
float32-rounded recomputation is a different estimator, and comparing the two
would test rounding rather than reconstruction. The float32 value that prereg
4.1 pins for the CURVE is computed and reported alongside, always, so the
difference is visible rather than chosen.

Exit codes: 0 ok, 3 instrument-proof failure (nothing downstream is reportable).
"""
import argparse
import gc
import json
import os
import sys
import time
from typing import Dict, List, Optional

import numpy as np

from . import fit_contract as fc
from . import fit_runner
from . import metrics
from . import score_runner

#: prereg 4.1, pinned. `tree_count_` is appended by `truncation_grid`.
GRID = (25, 50, 100, 150, 200, 300, 400, 600, 800, 1200, 1600)

PROOF_TOL = 1e-6


class InstrumentProofFailure(RuntimeError):
    """The reconstruction does not reproduce the fit. Nothing downstream holds."""


def truncation_grid(n_trees: int) -> List[int]:
    """The pinned grid, clipped to the model, with `tree_count_` always last."""
    ks = sorted({k for k in GRID if k < n_trees} | {int(n_trees)})
    return ks


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def curve(model, design, y, ks: List[int]) -> Dict[str, dict]:
    """AUROC at each `ntree_end`, in float64 (gate) and float32 (prereg 4.1)."""
    out: Dict[str, dict] = {}
    for k in ks:
        p64 = model.predict_proba(design, ntree_end=int(k))[:, 1]
        p32 = metrics.as_stored(p64)
        out[str(k)] = {
            "auroc_f64": metrics.auroc(y, p64),
            "auroc_f32": metrics.auroc(y, p32),
            "auprc_f32": metrics.auprc(y, p32),
            "logloss_f32": metrics.logloss(y, p32),
            "mean_p": float(np.mean(p32)),
        }
        _log(f"    ntree_end={k:>5}  auroc_f32={out[str(k)]['auroc_f32']:.6f}")
    return out


def restore_legacy_categoricals(frame: fc.Frame, meta: dict) -> List[str]:
    """Undo today's ORDERED rendering for a model that was fitted BEFORE it.

    `factory/blocks.py` gained `ORDERED_VOCABULARIES` on 2026-08-14 14:35:54 --
    3h28m AFTER `drift_cb_s101.pkl` was fitted at 11:07:06. Since that change,
    `fit_contract.load_frame` renders `b1_history_coverage_grade` as its
    vocabulary INDEX (0..3) and drops it from `frame.categorical`. The saved
    model, however, lists it in `meta["categorical"]`: CatBoost hashed it as a
    STRING level, and its CTR is keyed on that hash.

    Feeding the ordinal would not raise -- `score_runner._design` puts the name
    back into `frame.categorical`, `matrix()` stringifies the float, and the
    pinned-vocabulary check rejects '0.0'. That loud failure is the good case.
    The dangerous case is a model whose CTR silently keys on the wrong level.

    So: invert `_ordinal_column` exactly (`vocabulary_ordinal` is `vocab.index`,
    hence the inverse is `vocab[i]`, and NaN was MISSING_CATEGORY). Whether the
    inversion is right is NOT asserted here -- the instrument proof adjudicates
    it, because a wrong reconstruction cannot reproduce `best_score`.
    """
    from .. import blocks

    restored = []
    for name in meta.get("categorical", []):
        values = frame.features.get(name)
        if values is None or name in frame.categorical:
            continue
        vocab = blocks.PINNED_VOCABULARIES.get(name)
        if vocab is None or getattr(values, "dtype", None) is None:
            continue
        if values.dtype.kind != "f":
            continue
        frame.features[name] = np.array(
            [fc.MISSING_CATEGORY if not np.isfinite(v) else vocab[int(round(v))]
             for v in values], dtype=object)
        frame.categorical.append(name)
        restored.append(name)
    return restored


def surface_block(model, meta, frame: fc.Frame, ks: List[int], name: str) -> dict:
    """One surface: design matrix -> curve -> freed."""
    restored = restore_legacy_categoricals(frame, meta)
    if restored:
        _log(f"  {name}: restored legacy string categoricals {restored}")
    _log(f"  {name}: {frame.n_rows:,} rows / "
         f"{len(np.unique(frame.vehicle_id)):,} vehicles, building design")
    design = score_runner._design(frame, meta)
    y = frame.y
    block = {
        "n_rows": int(frame.n_rows),
        "n_vehicles": int(len(np.unique(frame.vehicle_id))),
        "positive_rate": float(np.mean(y)),
        "curve": curve(model, design, y, ks),
    }
    del design
    gc.collect()
    return block


def instrument_proof(fit_json: dict, valid_block: dict, valid_frame: fc.Frame,
                     n_trees: int, tol: float) -> dict:
    """Structural + metric reproduction of the fit's own early-stopping read."""
    state = fit_json["convergence_state"]
    got_rows = int(valid_frame.n_rows)
    got_veh = int(len(np.unique(valid_frame.vehicle_id)))
    want_rows = int(state["valid_rows"])
    want_veh = int(state["valid_vehicles"])
    structural = (got_rows == want_rows) and (got_veh == want_veh)

    at_full = valid_block["curve"][str(n_trees)]
    want_score = float(state["best_score"])
    delta64 = at_full["auroc_f64"] - want_score
    delta32 = at_full["auroc_f32"] - want_score
    metric_ok = abs(delta64) <= tol

    proof = {
        "structural": {
            "valid_rows_expected": want_rows, "valid_rows_got": got_rows,
            "valid_vehicles_expected": want_veh, "valid_vehicles_got": got_veh,
            "pass": structural,
        },
        "metric": {
            "best_score_recorded": want_score,
            "recomputed_f64": at_full["auroc_f64"], "delta_f64": delta64,
            "recomputed_f32": at_full["auroc_f32"], "delta_f32": delta32,
            "tolerance": tol, "gate_dtype": "float64", "pass": metric_ok,
        },
        "pass": bool(structural and metric_ok),
    }
    return proof


def run(model_path: str, config_path: str, seed: int, surface: str,
        frame_glob: str, extra_frame: Optional[str], out_dir: str,
        fit_json_path: Optional[str], proof_tol: float,
        memory_limit: str) -> dict:
    import duckdb

    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)
    model, meta = score_runner.load_artifact(model_path)
    n_trees = int(model.tree_count_)
    ks = truncation_grid(n_trees)
    label = meta.get("label", "y_final")

    con = duckdb.connect()
    con.execute(f"PRAGMA memory_limit='{memory_limit}'")

    payload = {
        "model_path": model_path, "config_path": config_path,
        "config_sha_meta": meta.get("config_sha"),
        "seed": int(seed), "surface": surface, "frame_glob": frame_glob,
        "extra_frame": extra_frame, "label": label,
        "tree_count": n_trees, "grid": ks,
        "prereg": "prereg/PREREG_OVERFIT_2026_08_16.md",
        "surfaces": {},
    }

    if surface == "train":
        if not fit_json_path:
            raise ValueError("--surface train needs --fit-json for the proof")
        with open(fit_json_path, encoding="utf-8") as fh:
            fit_json = json.load(fh)
        if fit_json.get("config_sha") != meta.get("config_sha"):
            raise InstrumentProofFailure(
                f"config_sha mismatch: fit JSON {fit_json.get('config_sha')} vs "
                f"model sidecar {meta.get('config_sha')}. Different fits.")
        _log(f"loading train frame (row_filter applied): {frame_glob}")
        train = fc.load_frame(
            con, frame_glob, meta["feature_names"], label,
            use_weights=config.get("use_weights", True),
            extra_glob=extra_frame or meta.get("extra_frame"),
            row_filter=config.get("row_filter"))
        _log(f"train frame {train.n_rows:,} rows; splitting with "
             f"split_validation(fraction={config.get('valid_fraction', 0.15)}, "
             f"seed={seed})")
        fit_part, valid_part = fit_runner.split_validation(
            train, config.get("valid_fraction", 0.15), int(seed))
        del train
        gc.collect()

        payload["surfaces"]["valid_part"] = surface_block(
            model, meta, valid_part, ks, "valid_part")
        payload["instrument_proof"] = instrument_proof(
            fit_json, payload["surfaces"]["valid_part"], valid_part,
            n_trees, proof_tol)
        del valid_part
        gc.collect()

        if not payload["instrument_proof"]["pass"]:
            _write(out_dir, model_path, surface, payload)
            raise InstrumentProofFailure(
                json.dumps(payload["instrument_proof"], indent=1))

        payload["surfaces"]["fit_part"] = surface_block(
            model, meta, fit_part, ks, "fit_part")
        del fit_part
        gc.collect()
    else:
        _log(f"loading eval surface {surface}: {frame_glob}")
        frame = fc.load_frame(
            con, frame_glob, meta["feature_names"], label, use_weights=False,
            extra_glob=extra_frame)
        payload["surfaces"][surface] = surface_block(
            model, meta, frame, ks, surface)
        del frame
        gc.collect()

    return _write(out_dir, model_path, surface, payload)


def _write(out_dir: str, model_path: str, surface: str, payload: dict) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.basename(model_path).replace(".pkl", "")
    path = os.path.join(out_dir, f"{stem}.{surface}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    payload["_path"] = path
    _log(f"wrote {path}")
    return payload


def merge(out_dir: str) -> str:
    """Collapse the per-surface shards into one curve table per model."""
    import glob

    models: Dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        if os.path.basename(path) == "OVERFIT_CURVES.json":
            continue
        shard = json.load(open(path, encoding="utf-8"))
        key = os.path.basename(shard["model_path"])
        entry = models.setdefault(key, {
            "model_path": shard["model_path"], "seed": shard["seed"],
            "tree_count": shard["tree_count"], "grid": shard["grid"],
            "config_sha": shard.get("config_sha_meta"), "surfaces": {}})
        entry["surfaces"].update(shard["surfaces"])
        if "instrument_proof" in shard:
            entry["instrument_proof"] = shard["instrument_proof"]
    path = os.path.join(out_dir, "OVERFIT_CURVES.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"prereg": "prereg/PREREG_OVERFIT_2026_08_16.md",
                   "models": models}, fh, indent=1, default=str)
    _log(f"merged -> {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="factory.runners.overfit_diagnostic", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merge", action="store_true",
                    help="merge shards in --out-dir and exit")
    ap.add_argument("--model")
    ap.add_argument("--config")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--surface", help="'train', or an eval surface name")
    ap.add_argument("--frame")
    ap.add_argument("--extra-frame", default=None)
    ap.add_argument("--fit-json", default=None,
                    help="the fit JSON to prove against (--surface train)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--proof-tol", type=float, default=PROOF_TOL)
    ap.add_argument("--memory-limit", default="2GB")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if a.merge:
        merge(a.out_dir)
        return 0
    for required in ("model", "config", "seed", "surface", "frame"):
        if getattr(a, required) is None:
            print(f"--{required} is required unless --merge", file=sys.stderr)
            return 2
    try:
        payload = run(a.model, a.config, a.seed, a.surface, a.frame,
                      a.extra_frame, a.out_dir, a.fit_json, a.proof_tol,
                      a.memory_limit)
    except InstrumentProofFailure as exc:
        print(f"INSTRUMENT PROOF FAILED:\n{exc}", file=sys.stderr)
        return 3
    keys = sorted(payload["surfaces"])
    print(json.dumps({"surface": a.surface, "blocks": keys,
                      "tree_count": payload["tree_count"],
                      "proof": payload.get("instrument_proof", {}).get("pass")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
