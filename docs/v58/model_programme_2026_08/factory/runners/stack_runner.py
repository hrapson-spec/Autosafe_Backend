#!/usr/bin/env python3
"""Residual-stack screen [OWNER-AMEND-6] with all four mandatory fences.

Normative source: out/replan_designs.md section 1.2 -- "without all four the
instrument is invalid, not merely noisy":

  1. DISJOINT STACK-FIT PARTITION. The stack is fitted on rows the reference was
     never trained on. Enforced, not assumed: the reference fit's
     `train_ids_path` is loaded and the intersection must be EMPTY.
  2. CONDITIONING SET = s + top-J base features, J <= 15, FIXED BEFORE ANY READ.
     Supplied as a JSON written before the read; its sha is recorded in the fit
     JSON so the freeze is auditable.
  3. B0-RECONSTRUCTION NULL ARM -- the same stack with NO block features, only
     [s + top-J]. No block is credited unless it exceeds this null.
     Cell id `s2.D.stack.null` (ablation_tables.STACK_NULL_CELL).
  4. EB-PRIOR CALIBRATION against a known refit: run the stack with the planted
     control's EB columns as the "block" and report beta_EB = d_stack/d_refit.
     Cell id `s2.D.stack.ebcal` (ablation_tables.STACK_EBCAL_CELL).

Licence (section 1.3), enforced here as refusals:
  - B3 and B4 are NEVER screened: they are interaction-defined, which is exactly
    the false-negative mode. `--block b3|b4` refuses.
  - The instrument may order the refit queue. It may NOT issue ADOPT / HARMFUL /
    LOO-REDUNDANT / K3 verdicts; the emitted JSON says so in `licence`.
  - Licence VOID if beta_EB < 0.5 or the reconstruction null exceeds the
    smallest block delta -- the harness computes that, from these fits.

Usage:
    python -m factory.runners.stack_runner --cell-type block --block b1 ...
    python -m factory.runners.stack_runner --cell-type null  ...
    python -m factory.runners.stack_runner --cell-type ebcal ...
"""
import argparse
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np

from . import fit_contract as fc
from . import metrics

STACK_NULL_CELL = "s2.D.stack.null"          # ablation_tables.STACK_NULL_CELL
STACK_EBCAL_CELL = "s2.D.stack.ebcal"        # ablation_tables.STACK_EBCAL_CELL
STACK_BLOCKS = ("b1", "b2", "b5", "b6")      # ablation_tables.STACK_BLOCKS
STACK_EXEMPT = ("b3", "b4")                  # ablation_tables.STACK_EXEMPT
MAX_CONDITIONING_J = 15
SCORE_COLUMN = "s_reference"

#: The planted control's EB family (all production-common in
#: serve_view_classes.json) -- the calibration "block" for fence 4.
EB_CALIBRATION_COLUMNS = ("eb_unified_prior", "model_age_fail_rate_eb",
                          "make_age_fail_rate_eb", "prior_fail_rate_smoothed")

#: Shallow by construction: the stack is a screen, not a model.
STACK_PARAMS = {"iterations": 300, "learning_rate": 0.08, "depth": 3,
                "loss_function": "Logloss", "eval_metric": "AUC",
                "od_type": "Iter", "od_wait": 40, "use_best_model": True,
                "allow_writing_files": False, "verbose": False}


class StackLicenceRefused(RuntimeError):
    """A fence the stack screen is not permitted to cross."""


def cell_id(cell_type: str, block: Optional[str]) -> str:
    if cell_type == "null":
        return STACK_NULL_CELL
    if cell_type == "ebcal":
        return STACK_EBCAL_CELL
    if cell_type != "block":
        raise ValueError(f"cell_type {cell_type!r} must be block|null|ebcal")
    key = (block or "").lower()
    if key in STACK_EXEMPT:
        raise StackLicenceRefused(
            f"B{key[1:]} is EXEMPT from stack screening [OWNER-AMEND-6, "
            f"replan_designs section 1.3]: its value is interaction-defined "
            f"(burden x age, burden x mileage, severity x class), which is "
            f"exactly the false-negative mode of this instrument. Refit cell only.")
    if key not in STACK_BLOCKS:
        raise ValueError(f"block {block!r} must be one of {STACK_BLOCKS}")
    return f"s2.D.stack.{key}"


def load_conditioning(path: str) -> dict:
    """The prereg'd conditioning set: s + top-J base features, J <= 15."""
    with open(path, "rb") as fh:
        raw = fh.read()
    spec = json.loads(raw)
    features = list(spec.get("top_j_features") or [])
    if not features:
        raise StackLicenceRefused(
            f"{path}: `top_j_features` is empty. Fence 2 requires a conditioning "
            f"set fixed BEFORE any read -- it is the only defence against the "
            f"base-reconstruction false positive.")
    if len(features) > MAX_CONDITIONING_J:
        raise StackLicenceRefused(
            f"{path}: J={len(features)} > {MAX_CONDITIONING_J} (fence 2).")
    return {"top_j_features": features, "J": len(features),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "chosen_by": spec.get("chosen_by", "incumbent importance"),
            "frozen_at": spec.get("frozen_at")}


def _read_preds(con, glob: str) -> Dict[int, float]:
    rel = f"read_parquet('{str(glob)}', union_by_name=true)"
    rows = con.execute(f"SELECT test_id, p FROM {rel}").fetchall()
    return {int(t): float(p) for t, p in rows}


def _read_train_ids(con, path: str) -> set:
    rel = f"read_parquet('{str(path)}', union_by_name=true)"
    return {int(r[0]) for r in con.execute(f"SELECT test_id FROM {rel}").fetchall()}


def attach_score(frame: fc.Frame, score_by_id: Dict[int, float]) -> fc.Frame:
    """Add the reference score s as a feature; refuse on any missing row."""
    missing = [int(t) for t in frame.test_id if int(t) not in score_by_id]
    if missing:
        raise StackLicenceRefused(
            f"{len(missing)} of {frame.n_rows} rows have no reference score "
            f"(e.g. test_id {missing[:3]}). s must be an OUT-OF-SAMPLE score on "
            f"every stack row; score the partition with fit_runner first.")
    frame.features[SCORE_COLUMN] = np.array(
        [score_by_id[int(t)] for t in frame.test_id], dtype=np.float64)
    return frame


def run_stack(config: dict, stack_glob: str, eval_glob: str, seed: int,
              cell_type: str, block: Optional[str], out_dir: str, *,
              reference_preds: str, reference_train_ids: str,
              conditioning_json: str, con=None, thread_count: int = 1,
              preds_dir: Optional[str] = None,
              refit_delta: Optional[float] = None) -> dict:
    """Fit one stack arm and emit the harness contract with arm='stack'."""
    import duckdb
    try:
        from catboost import CatBoostClassifier
    except ImportError as exc:                                  # pragma: no cover
        raise fc.LibraryUnavailable(f"catboost is not installed: {exc}")

    cell = cell_id(cell_type, block)
    conditioning = load_conditioning(conditioning_json)
    con = con or duckdb.connect()
    if config.get("memory_limit"):
        con.execute(f"PRAGMA memory_limit='{config['memory_limit']}'")

    if cell_type == "null":
        block_columns: List[str] = []
    elif cell_type == "ebcal":
        block_columns = list(config.get("eb_columns") or EB_CALIBRATION_COLUMNS)
    else:
        block_columns = fc.resolve_featureset([block.upper()])

    columns = list(dict.fromkeys(conditioning["top_j_features"] + block_columns))
    label = config.get("label", "y_final")
    stack_frame = fc.load_frame(con, stack_glob, columns, label,
                                use_weights=config.get("use_weights", True),
                                extra_glob=config.get("extra_frame"),
                                row_filter=config.get("row_filter"))
    eval_frame = fc.load_frame(con, eval_glob, columns, label, use_weights=False,
                               extra_glob=config.get("extra_eval_frame")
                               or config.get("extra_frame"))

    # --- fence 1: the stack partition must be disjoint from reference training
    ref_train = _read_train_ids(con, reference_train_ids)
    overlap = sorted({int(t) for t in stack_frame.test_id} & ref_train)
    if overlap:
        raise StackLicenceRefused(
            f"fence 1 VIOLATED: {len(overlap)} stack rows were in the reference's "
            f"training set (e.g. {overlap[:3]}). s is in-sample there, so the "
            f"stack down-weights s and OVER-CREDITS the block "
            f"[replan_designs section 1.2.1].")
    eval_overlap = sorted({int(t) for t in eval_frame.test_id} & ref_train)
    if eval_overlap:
        raise StackLicenceRefused(
            f"fence 1 VIOLATED on the EVAL slice: {len(eval_overlap)} eval rows "
            f"were in the reference's training set.")

    scores = _read_preds(con, reference_preds)
    stack_frame = attach_score(stack_frame, scores)
    eval_frame = attach_score(eval_frame, scores)

    fit_cols = [SCORE_COLUMN] + columns
    stack_frame.features = {k: stack_frame.features[k] for k in fit_cols}
    eval_frame.features = {k: eval_frame.features[k] for k in fit_cols}

    from .fit_runner import split_validation

    fit_part, valid_part = split_validation(
        stack_frame, config.get("valid_fraction", 0.15), seed)
    cat_idx = [fit_part.feature_names.index(c) for c in fit_part.categorical]
    params = dict(STACK_PARAMS)
    params.update(config.get("params") or {})
    model = CatBoostClassifier(random_seed=seed, thread_count=thread_count,
                              task_type="CPU", cat_features=cat_idx, **params)
    model.fit(fit_part.matrix(), fit_part.y, sample_weight=fit_part.weight,
              eval_set=(valid_part.matrix(), valid_part.y))
    p_stored = metrics.as_stored(model.predict_proba(eval_frame.matrix())[:, 1])

    best_iter = model.get_best_iteration()
    state = {
        "best_iteration": None if best_iter is None else int(best_iter),
        "n_iterations_run": int(model.tree_count_),
        "iterations_requested": int(params["iterations"]),
        "early_stopped": bool(best_iter is not None
                              and int(best_iter) + 1 < int(params["iterations"])),
        "converged": bool(best_iter is not None
                          and int(best_iter) + 1 < int(params["iterations"])),
        "quantization": "n/a", "eval_curve_tail": [],
        "valid_rows": int(valid_part.n_rows),
        "stack_partition_rows": int(stack_frame.n_rows),
    }

    preds_path = os.path.join(preds_dir or os.path.join(out_dir, "preds"),
                              f"{cell}.seed{seed}.parquet")
    fc.write_keyed_preds(preds_path, eval_frame.test_id, eval_frame.vehicle_id,
                         eval_frame.y, p_stored)

    fences = {
        "1_disjoint_partition": {"enforced": True, "overlap_rows": 0,
                                 "reference_train_ids": reference_train_ids,
                                 "stack_partition_rows": int(stack_frame.n_rows)},
        "2_conditioning_set": conditioning,
        "3_reconstruction_null_cell": STACK_NULL_CELL,
        "4_eb_calibration_cell": STACK_EBCAL_CELL,
    }
    extra = {
        "stack_cell_type": cell_type,
        "block": block,
        "block_columns": block_columns,
        "n_block_columns": len(block_columns),
        "stack_fences": fences,
        "licence": ("QUEUE-ORDERING ONLY [OWNER-AMEND-6]. This instrument may "
                    "order the refit queue and may eliminate a block from it only "
                    "when delta_stack < the reconstruction null AND the block is "
                    "not interaction-defined. It may NOT issue ADOPT, HARMFUL, "
                    "LOO-REDUNDANT or K3 verdicts. B3/B4 are never screened."),
        "estimand": ("incremental value of the block over a SCALAR 1-D projection "
                     "s=f(B0) plus J conditioning features -- NOT over the full "
                     "base feature space (replan_designs section 1.1)."),
    }
    if cell_type == "ebcal" and refit_delta:
        extra["refit_delta"] = float(refit_delta)
        extra["beta_eb_note"] = ("beta_EB = delta_stack / delta_refit is computed "
                                 "by the harness from this cell and the planted "
                                 "control's refit; licence VOID if beta_EB < 0.5.")

    payload = fc.build_fit_json(
        cell, "stack", seed, eval_frame, p_stored, preds_path,
        arch=config.get("arch", "catboost_gbm"),
        featureset=f"stack:{cell_type}:{block or '-'}",
        grade=config.get("grade", "screen"),
        surface=config.get("surface", "panel"),
        rung_rows=config.get("rung_rows"), config=config,
        convergence_state=state, train=stack_frame, extra=extra)
    fc.write_fit_json(out_dir, cell, seed, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.stack_runner",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cell-type", required=True, choices=("block", "null", "ebcal"))
    ap.add_argument("--block", default=None, help="b1|b2|b5|b6 (b3/b4 refuse)")
    ap.add_argument("--stack-frame", required=True,
                    help="the DISJOINT stack partition parquet glob")
    ap.add_argument("--eval-frame", required=True)
    ap.add_argument("--reference-preds", required=True,
                    help="reference keyed preds covering the stack partition AND eval")
    ap.add_argument("--reference-train-ids", required=True,
                    help="the reference fit's train_ids_path (fence 1 evidence)")
    ap.add_argument("--conditioning-json", required=True,
                    help="prereg'd {top_j_features: [...]}, J <= 15 (fence 2)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--preds-dir", default=None)
    ap.add_argument("--thread-count", type=int, default=1)
    ap.add_argument("--refit-delta", type=float, default=None,
                    help="ebcal only: the planted control's REFIT delta, for beta_EB")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    with open(a.config, encoding="utf-8") as fh:
        config = json.load(fh)
    try:
        payload = run_stack(config, a.stack_frame, a.eval_frame, a.seed,
                            a.cell_type, a.block, a.out_dir,
                            reference_preds=a.reference_preds,
                            reference_train_ids=a.reference_train_ids,
                            conditioning_json=a.conditioning_json,
                            thread_count=a.thread_count, preds_dir=a.preds_dir,
                            refit_delta=a.refit_delta)
    except (StackLicenceRefused, fc.FenceViolation, fc.LibraryUnavailable) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({k: payload[k] for k in
                      ("cell", "arm", "seed", "auroc", "keyed_preds_path")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
