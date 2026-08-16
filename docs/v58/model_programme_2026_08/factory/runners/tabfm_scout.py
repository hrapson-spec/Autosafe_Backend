#!/usr/bin/env python3
"""RESEARCH SCOUT: Google TabFM 1.0.0 as an architecture-screen context model.

    python -m factory.runners.tabfm_scout \
        --frame  'out/frames/recipe=flat4y/rung=r1m/frame/*.parquet' \
        --eval-frame 'out/frames_eval/recipe=eval2024/rung=all/frame/*.parquet' \
        --config out/configs/as.tabfm.scout.json --seed 101 \
        --cell as.tabfm.ctx.1m --arm AS --out-dir out/fits/s3 \
        --preds-dir out/fits/s3/preds --thread-count 4

LICENCE FENCE -- READ BEFORE USING ANY NUMBER THIS SCRIPT PRODUCES
------------------------------------------------------------------
The TabFM *weights* ship under the **TabFM Non-Commercial License v1.0**
(the inference code is Apache-2.0; the checkpoint is not). Clause 3(a)
prohibits use of "the TabFM Model (or any Derivative thereof, or any Outputs
and data produced by the TabFM Model), in whole or in part for any commercial
or production purposes", and clause 1(c) scopes "Non-Commercial Purpose" to
testing/evaluation/research "provided the results are not used in commercial
decision-making, client deliverables, or paid products/services".

Consequently this runner is DELIBERATELY NOT wired into `fit_runner.FITTERS`
or `fit_runner.ARCHES`. It is a standalone scout so that no queue line, stack
build or blend can reach TabFM by accident. Every payload it writes carries
the `licence` field naming the restriction, and the cell it produces must be
excluded from deployable rankings and blends.

WHAT IT MEASURES
----------------
The same contract as `fit_runner._fit_context_model` (TabPFN / TabICL /
TabDPT): a bounded seeded SUPPORT SET drawn from the train rung is handed to
the model as in-context examples, the FULL eval frame is scored in batches,
and a runtime projection guard refuses early -- with the text
`CPU-INFEASIBLE-AT-MATCHED-EVAL` -- rather than burning hours. A refusal is a
legitimate screen outcome, not an error in the model.

TabFM-specific cost shape (matters for choosing `batch_rows`): TabFM is a
single-forward-pass ICL model. `fit()` trains nothing -- it only fits the
encoders and the ensemble generator. The support rows are re-embedded and
re-attended on EVERY `predict_proba` call, so the total eval cost is roughly

    (n_eval / batch_rows) * n_estimators * f(support_rows + batch_rows)

Small `batch_rows` therefore pays the support-set forward pass over and over.
Large `batch_rows` amortises it but raises peak RAM, which on an 8GB CPU box
is the binding constraint. Both knobs are recorded in convergence_state.

MEMORY: the published `classification/model.safetensors` is 1,639,444,522
float32 parameters = 6.107 GiB on disk. The vendor loader
(`tabfm_v1_0_0_pytorch.load`) materialises those float32 weights in RAM and
only then casts to bfloat16, peaking near the full 6.1 GiB. On an 8 GiB box
that is not survivable next to a running screen job, so this scout defaults to
a STREAMING bfloat16 load (`low_memory_load`), which peaks near 3.06 GiB by
casting each tensor as it is read. The path actually taken is recorded.

DTYPE TRAP (measured on this box, not assumed): PyTorch 2.13 has no vectorised
CPU bfloat16 GEMM on Apple Silicon. At the ICL block's own shape the measured
throughput was ~1400 GFLOP/s in float32 and ~0.7 GFLOP/s in bfloat16 -- a
~2000x penalty for running the model in the dtype it was designed for. Storing
float32 fixes the speed and costs 6.11 GiB resident, which does not fit. The
scout therefore stores bfloat16 and COMPUTES float32 (`compute_dtype`), which
measured ~978 GFLOP/s -- fp32 speed at bf16 residency. See
`_promote_linear_compute`. Anyone porting this to x86 (oneDNN bf16) or to a
box with >=12 GiB should re-measure before keeping the promotion.

Determinism: `random_state` is threaded from `--seed` into every stochastic
component of the ensemble generator (row subsampling, feature shuffling, class
shift, SVD, crosses), `torch.manual_seed` is set, the model is in eval mode
with no dropout, and `torch.set_num_threads(--thread-count)` pins the reduction
order. CPU only; no GPU/MPS device is ever requested.
"""
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np

from . import fit_contract as fc
from . import metrics
from .fit_runner import split_validation

#: The verbatim licence stamp that must appear on every artefact this scout
#: writes. Downstream readers key on this string to exclude the cell.
LICENCE = ("TabFM Non-Commercial License v1.0 — RESEARCH SCOUT ONLY, "
           "excluded from deployable rankings and blends")

HF_REPO_ID = "google/tabfm-1.0.0-pytorch"

#: Screen preset. `support_rows` is TabFM's name for what the other context
#: models call `context_rows`; both spellings are accepted in a config.
PRESET_SCREEN: Dict[str, Any] = {
    "family": "tabfm",
    # 500/250 is NOT a performance choice -- see MEASURED_COST. TabFM cannot
    # reach the 75-minute cap at ANY (support, batch) on this CPU, so the
    # screen-optimal config is the one that reaches the projection guard
    # cheapest: 750 tokens = a ~2.5 min probe, then a documented refusal.
    "support_rows": 500,
    "batch_rows": 250,
    "max_eval_minutes": 75,
    # inference knobs -> TabFMClassifier
    "n_estimators": 1,          # 1 = the card's zero-shot mode; each extra
                                # member is another full forward pass on CPU
    "batch_size": 1,            # ensemble members forwarded at once (NOT rows)
    "use_amp": False,           # no autocast on CPU; weights are already bf16
    "softmax_temperature": 0.9,
    "average_logits": True,
    # loader knobs -> _load_tabfm_model
    "dtype": "bfloat16",        # STORAGE dtype: 3.05 GiB instead of 6.11 GiB
    "compute_dtype": "float32",  # COMPUTE dtype: see _promote_linear_compute
    "low_memory_load": True,
    "checkpoint_path": None,
    # activation-memory chunking; see _set_chunk_sizes. None = library default
    "chunk_sizes": {"row_chunk_size": 250, "col_chunk_size": None,
                    "ffn_chunk_size": None},
    # refuse to start a 3GB+ load that would push a busy 8GB box into swap
    "min_free_ram_gib": 3.5,
}

#: MEASURED on this box 2026-08-13 (M3, 8 GiB, CPU, torch 2.13, 2 threads,
#: 241 features, n_estimators=1, bf16 storage + fp32 compute). The 24-block
#: cost is fitted from real 1-block and 3-block runs of the published
#: architecture: T(k) = encoders + k * per_block is exact in k because the ICL
#: stack is 24 identical blocks.
MEASURED_COST = {
    "measured_at": "2026-08-13",
    "box": "Apple M3, 8 GiB, CPU only, torch 2.13, 2 threads",
    "n_features": 241,
    "n_estimators": 1,
    "t_1_block_500sup_500batch_s": 82.57,
    "t_3_block_500sup_500batch_s": 92.98,
    "per_icl_block_s_at_1000_tokens": 5.205,
    "encoder_s_at_1000_tokens": 77.37,
    "per_token_ms_24_blocks": 202.3,
    "peak_rss_gib_at_1000_rows_241_features": 2.96,
    "matmul_gflops_float32": 1424.5,
    "matmul_gflops_bfloat16": 0.7,
    "matmul_gflops_bf16_stored_fp32_computed": 977.9,
    "cap_requires_s_per_1k": 13.61,
    "floor_s_per_1k_at_zero_support": 202.3,
    "floor_full_eval_hours": 18.6,
    "verdict": ("CPU-INFEASIBLE-AT-MATCHED-EVAL: the 75-minute cap needs "
                "<=13.61 s per 1k eval rows; the best achievable on this box "
                "is 202.3 s per 1k (zero support, infinite batch), 14.9x over. "
                "At a usable 500 support / 500 batch it is 404.6 s per 1k = "
                "37.2 h = 29.7x the cap. Only ~22,245 of the 330,665 eval rows "
                "(6.7%) could be scored inside the cap, which would break the "
                "matched-eval contract."),
}

#: Measured from the published safetensors header (2026-08-13), not guessed.
CHECKPOINT_FACTS = {
    "repo": HF_REPO_ID,
    "file": "classification/model.safetensors",
    "parameters": 1_639_444_522,
    "on_disk_dtype": "float32",
    "on_disk_bytes": 6_557_888_408,
    "bfloat16_resident_gib": round(1_639_444_522 * 2 / 2 ** 30, 3),
    "float32_resident_gib": round(1_639_444_522 * 4 / 2 ** 30, 3),
    "licence": "TabFM Non-Commercial License v1.0 (weights); Apache-2.0 (code)",
    "gated": False,
}


class ScoutRefusal(RuntimeError):
    """A refusal that is a screen OUTCOME, not a defect (exit 3, like a fence).

    Carries the partial `state` so the measurement that JUSTIFIES the refusal
    (seconds per 1k rows, projected full-eval minutes, support/batch/threads)
    survives into the job log. For this scout the refusal is the expected
    result, so throwing its evidence away would throw away the finding.
    """

    def __init__(self, message: str, state: Optional[dict] = None):
        super().__init__(message)
        self.state = dict(state or {})


def preset_params(overrides: Optional[dict] = None) -> dict:
    params = dict(PRESET_SCREEN)
    params.update(overrides or {})
    # accept the sibling runners' spelling so a config can be copied across
    if "context_rows" in params:
        params["support_rows"] = int(params.pop("context_rows"))
    return params


def _free_ram_gib() -> Optional[float]:
    """Free + inactive + speculative pages, in GiB. None if unavailable."""
    try:
        import subprocess
        out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return None
    page = 4096
    counts: Dict[str, int] = {}
    for line in out.splitlines():
        if "page size of" in line:
            page = int(line.split("page size of")[1].split("bytes")[0].strip())
        if ":" in line:
            key, _, value = line.partition(":")
            digits = value.strip().rstrip(".")
            if digits.isdigit():
                counts[key.strip().lower()] = int(digits)
    reclaimable = sum(counts.get(k, 0) for k in
                      ("pages free", "pages inactive", "pages speculative",
                       "pages purgeable"))
    if not reclaimable:
        return None
    return round(reclaimable * page / 2 ** 30, 3)


def _promote_linear_compute(model, compute_dtype: str, state: dict) -> int:
    """Keep the weights in their storage dtype; run every GEMM in `compute_dtype`.

    MEASURED on this box (M3, torch 2.13, CPU, 2 threads, [2000x2048]@[2048x8192]):

        float32   matmul   ~1400 GFLOP/s
        bfloat16  matmul      ~0.7 GFLOP/s   <-- ~2000x slower
        float16   matmul      ~0.7 GFLOP/s

    PyTorch has no vectorised CPU bf16 GEMM kernel on Apple Silicon, so the
    model's own design dtype is the slowest thing it can run in. Storing fp32
    weights instead would fix the speed and cost 6.11 GiB resident, which does
    not fit beside anything else on an 8 GiB box. Upcasting each weight at the
    matmul instead measured ~978 GFLOP/s -- i.e. full fp32 speed at bf16
    residency, with only a one-weight (64 MiB) transient.

    99.4% of TabFM's parameters live in `nn.Linear`, and promoting those makes
    the activations fp32 too, so the attention SDPA follows into the fast path.
    The class swap is in place and behaviour-only: no parameter is copied.
    """
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    if not compute_dtype or str(compute_dtype).lower() == "none":
        state["compute_dtype"] = "storage dtype (no promotion)"
        return 0
    target = {"float32": torch.float32, "bfloat16": torch.bfloat16,
              "float16": torch.float16}[str(compute_dtype).lower()]

    class _PromotedLinear(nn.Linear):
        def forward(self, x):
            bias = None if self.bias is None else self.bias.to(target)
            return F.linear(x.to(target), self.weight.to(target), bias)

    promoted = 0
    for module in model.modules():
        if type(module) is nn.Linear:
            module.__class__ = _PromotedLinear
            promoted += 1
    state["compute_dtype"] = compute_dtype
    state["linear_layers_promoted"] = promoted
    return promoted


def _set_chunk_sizes(model, chunks: Optional[dict], state: dict) -> dict:
    """Bound ACTIVATION memory by chunking TabFM's three widest intermediates.

    The weights are not the whole memory story: the cell embedder materialises
    a [batch, rows, feature_groups, group, embed] Fourier tensor, and with ~241
    features that intermediate — not the checkpoint — is what a scoring batch
    actually costs. MEASURED here: 1,000 rows x 241 features peaked at 2.96 GiB
    RSS against a 0.18 GiB model, i.e. ~2.8 GiB of activations, scaling roughly
    linearly in (support_rows + batch_rows).

    `model.py` exposes three opt-in chunk knobs that default to None (no
    chunking): `CellEmbedder.row_chunk_size`, `ColEmbedding.col_chunk_size` and
    `MultiheadAttentionBlock.ffn_chunk_size`. Setting them trades wall time for
    a bounded peak, which is the right trade on an 8 GiB box.
    """
    applied: Dict[str, Any] = {}
    for name, value in (chunks or {}).items():
        if value is None:
            continue
        hit = 0
        for module in model.modules():
            if hasattr(module, name):
                setattr(module, name, int(value))
                hit += 1
        applied[name] = {"value": int(value), "modules_set": hit}
    state["activation_chunking"] = applied or "none (library default)"
    return applied


def _load_tabfm_model(dtype_name: str, checkpoint_path: Optional[str],
                      low_memory: bool, state: dict):
    """Return an eval-mode TabFM classification backbone on CPU.

    `low_memory` streams the checkpoint tensor-by-tensor into the requested
    dtype so peak RSS is ~3.06 GiB instead of the ~6.11 GiB the vendor loader
    needs. Falls back to the vendor loader (recording why) if the streamed
    module is left with any un-materialised meta tensor.
    """
    import torch

    try:
        from tabfm import tabfm_v1_0_0_pytorch as tabfm_torch
    except ImportError as exc:  # pragma: no cover - environment guard
        raise fc.LibraryUnavailable(
            f"tabfm not installed: {exc}. `pip install 'tabfm[pytorch]'` "
            f"(inference code Apache-2.0; weights are NON-COMMERCIAL).")

    dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16,
             "float32": torch.float32, "none": None}[str(dtype_name).lower()]

    def _vendor(reason: Optional[str] = None):
        state["load_path"] = "vendor tabfm_v1_0_0_pytorch.load"
        if reason:
            state["load_path_fallback_reason"] = reason
        return tabfm_torch.load(model_type="classification",
                                checkpoint_path=checkpoint_path,
                                device="cpu", dtype=dtype, use_cache=True)

    if not low_memory:
        return _vendor()

    try:
        from huggingface_hub import snapshot_download
        from safetensors import safe_open
        from tabfm.src.pytorch.model import TabFM

        root = checkpoint_path or snapshot_download(
            HF_REPO_ID, allow_patterns=["classification/**"])
        sub = os.path.join(root, "classification")
        if not os.path.isdir(sub):
            sub = root
        with open(os.path.join(sub, "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        for drop in ("model_type", "version", "framework", "frameworks"):
            cfg.pop(drop, None)
        task = cfg.pop("task", None)
        if task is not None:
            cfg["is_classifier"] = (task == "classification")
        cfg.setdefault("is_classifier", True)

        with torch.device("meta"):
            model = TabFM(**cfg)
        weights = os.path.join(sub, "model.safetensors")
        tensors = {}
        with safe_open(weights, framework="pt", device="cpu") as fh:
            for key in fh.keys():
                tensor = fh.get_tensor(key)
                tensors[key] = (tensor if dtype is None
                                else tensor.to(dtype))
        model.load_state_dict(tensors, assign=True, strict=True)
        del tensors

        stranded = [n for n, t in list(model.named_parameters())
                    + list(model.named_buffers()) if t.is_meta]
        if stranded:
            del model
            return _vendor(f"{len(stranded)} tensor(s) left on the meta device "
                           f"by the streamed load, e.g. {stranded[:3]}")
        model.eval()
        state["load_path"] = f"streamed safetensors -> {dtype_name}"
        state["checkpoint_dir"] = sub
        return model
    except (ImportError, OSError, KeyError, RuntimeError, ValueError) as exc:
        return _vendor(f"{type(exc).__name__}: {exc}")


def fit_tabfm_context(train: fc.Frame, valid: fc.Frame, eval_frame: fc.Frame,
                      params: dict, seed: int, thread_count: int):
    """Screen fitter for TabFM, contract-identical to `_fit_context_model`.

    Consumes the (train, valid, eval) Frame trio; `valid` is accepted for
    signature parity and deliberately unused -- TabFM has no early stopping to
    tune, so spending rows on a validation split it cannot read would only
    shrink the support set. That is recorded in the state.

    Preprocessing: ordinal-encode categoricals (category codes, NaN=-1),
    train-median impute continuous. A seeded subsample of the TRAIN frame
    becomes the support set. Eval is predicted for the FULL eval frame in
    batches behind a runtime projection guard.
    """
    import torch

    params = dict(params)
    kind = params.pop("family", "tabfm")
    if kind != "tabfm":
        raise ValueError(f"tabfm_scout cannot fit family {kind!r}")
    support_rows = int(params.pop("support_rows"))
    batch_rows = int(params.pop("batch_rows", 1000))
    max_eval_minutes = float(params.pop("max_eval_minutes", 75.0))
    min_free_ram = float(params.pop("min_free_ram_gib", 0.0) or 0.0)
    dtype_name = str(params.pop("dtype", "bfloat16"))
    compute_dtype = params.pop("compute_dtype", "float32")
    low_memory = bool(params.pop("low_memory_load", True))
    checkpoint_path = params.pop("checkpoint_path", None)
    chunk_sizes = params.pop("chunk_sizes", None)

    state: Dict[str, Any] = {}

    free_gib = _free_ram_gib()
    state["free_ram_gib_at_load"] = free_gib
    need = (CHECKPOINT_FACTS["bfloat16_resident_gib"] if dtype_name != "float32"
            else CHECKPOINT_FACTS["float32_resident_gib"])
    if min_free_ram and free_gib is not None and free_gib < min_free_ram:
        raise ScoutRefusal(
            f"tabfm: {free_gib:.2f} GiB reclaimable RAM < required "
            f"{min_free_ram:.2f} GiB; the checkpoint alone is {need:.2f} GiB "
            f"resident at dtype={dtype_name}. Refusing to load and push a busy "
            f"8GB box into swap — recorded as CPU-INFEASIBLE-AT-MATCHED-EVAL "
            f"(memory), a screen outcome, not an error in the model.",
            {**state, "refusal": "CPU-INFEASIBLE-AT-MATCHED-EVAL (memory)",
             "required_resident_gib": need, "licence": LICENCE})

    torch.set_num_threads(max(1, int(thread_count)))
    torch.manual_seed(seed)

    from tabfm import TabFMClassifier

    t_load = time.time()
    backbone = _load_tabfm_model(dtype_name, checkpoint_path, low_memory, state)
    _promote_linear_compute(backbone, compute_dtype, state)
    _set_chunk_sizes(backbone, chunk_sizes, state)
    state["model_load_seconds"] = round(time.time() - t_load, 2)

    # --- preprocessing, identical to the sibling context models -------------
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
    take = min(support_rows, n_train)
    idx = rng.choice(n_train, size=take, replace=False)
    X_all = encoded(train)

    model = TabFMClassifier(model=backbone, random_state=seed, verbose=False,
                            **params)
    model.fit(X_all[idx], train.y[idx])
    del X_all

    # --- batched full-eval scoring behind the projection guard --------------
    X_eval = encoded(eval_frame)
    n_eval = X_eval.shape[0]
    p = np.empty(n_eval, dtype=np.float64)
    t0 = time.time()
    probe = min(batch_rows, n_eval)
    p[:probe] = model.predict_proba(X_eval[:probe])[:, 1]
    probe_seconds = time.time() - t0
    projected_min = probe_seconds / probe * n_eval / 60.0
    state["probe_rows"] = int(probe)
    state["probe_seconds"] = round(probe_seconds, 3)
    state["seconds_per_1k_predictions"] = round(probe_seconds / probe * 1000.0, 3)
    state["projected_full_eval_minutes"] = round(projected_min, 2)
    if projected_min > max_eval_minutes:
        raise ScoutRefusal(
            f"tabfm: projected full-eval scoring {projected_min:.0f} min > "
            f"cap {max_eval_minutes:.0f} min on CPU "
            f"({state['seconds_per_1k_predictions']:.1f}s per 1k rows at "
            f"support_rows={take:,}, batch_rows={batch_rows:,}, "
            f"n_estimators={params.get('n_estimators', 32)}) — recorded as "
            f"CPU-INFEASIBLE-AT-MATCHED-EVAL, a screen outcome, not an error "
            f"in the model.",
            {**state, "refusal": "CPU-INFEASIBLE-AT-MATCHED-EVAL (runtime)",
             "support_rows_used": int(take), "rung_rows_available": int(n_train),
             "n_eval_rows": int(n_eval), "max_eval_minutes": max_eval_minutes,
             "licence": LICENCE})
    for lo in range(probe, n_eval, batch_rows):
        hi = min(lo + batch_rows, n_eval)
        p[lo:hi] = model.predict_proba(X_eval[lo:hi])[:, 1]

    limitation = (
        f"tabfm consumed a {take:,}-row seeded support set from the "
        f"{n_train:,}-row rung (context ceiling), not the full rung: levels are "
        f"NOT rows-matched to the GBDT/MLP anchors; eval rows, target and "
        f"metric ARE matched.")
    state.update({
        "best_iteration": None, "n_iterations_run": None,
        "iterations_requested": None, "early_stopped": None,
        "best_score": None, "eval_curve_tail": [], "quantization": "n/a",
        "converged": None, "family": kind,
        "context_rows_used": int(take), "support_rows_used": int(take),
        "rung_rows_available": int(n_train),
        "batch_rows": int(batch_rows),
        "n_estimators": int(params.get("n_estimators", 32)),
        "torch_threads": int(max(1, int(thread_count))),
        "dtype": dtype_name,
        "valid_rows_unused": int(valid.n_rows),
        "matched_comparison_limitation": limitation,
        "eval_scoring_minutes": round((time.time() - t0) / 60.0, 2),
        "licence": LICENCE,
        "checkpoint": dict(CHECKPOINT_FACTS),
        "scout_cost_baseline": dict(MEASURED_COST),
        "note": "architecture screen SCOUT: TabFM weights are NON-COMMERCIAL; "
                "this cell is information-only and must never enter a "
                "deployable ranking, stack or blend. TabFM has no early "
                "stopping, so the es-validation split is not consumed.",
    })
    return p, state, model


def run_scout(config: dict, frame_glob: str, eval_glob: str, seed: int,
              cell: str, arm: str, out_dir: str, *, con=None,
              thread_count: int = 1, preds_dir: Optional[str] = None) -> dict:
    """One TabFM scout fit -> the same contract artefacts as any screen cell."""
    import duckdb

    arch = config.get("arch", "tabfm")
    if arch != "tabfm":
        raise ValueError(f"tabfm_scout runs arch 'tabfm', not {arch!r}")
    grade = config.get("grade", "screen")
    if grade != "screen":
        raise ScoutRefusal(
            f"grade={grade!r}: a non-commercially-licensed model may only ever "
            f"produce screen-grade information. Full grade is refused.")
    params = preset_params(config.get("params"))

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

    fit_part, valid_part = split_validation(
        train, config.get("valid_fraction", 0.15), seed)
    p, state, _model = fit_tabfm_context(fit_part, valid_part, eval_frame,
                                         params, seed, thread_count)
    state["valid_rows"] = int(valid_part.n_rows)
    state["valid_vehicles"] = int(len(np.unique(valid_part.vehicle_id)))

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
        arch="tabfm", featureset=",".join(str(f) for f in config["featureset"]),
        grade=grade, surface=config.get("surface", "panel"),
        rung_rows=config.get("rung_rows"), config=config,
        convergence_state=state, train=train,
        extra={"label": label, "preset": config.get("preset", "screen"),
               "params": params, "has_time": False,
               "train_ids_path": train_ids_path,
               "base": config.get("base", "b0-104"),
               # --- the three scout-only fields -------------------------
               "licence": LICENCE,
               "support_rows_used": int(state["support_rows_used"]),
               "matched_comparison_limitation":
                   state["matched_comparison_limitation"],
               **({"ref_cell": config["ref_cell"]} if config.get("ref_cell")
                  else {})})
    fc.write_fit_json(out_dir, cell, seed, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.runners.tabfm_scout",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frame", required=True, help="training frame parquet glob")
    ap.add_argument("--eval-frame", required=True, help="eval-slice parquet glob")
    ap.add_argument("--config", required=True, help="config JSON path")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--cell", required=True, help="prereg cell id, e.g. as.tabfm.ctx.1m")
    ap.add_argument("--arm", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--preds-dir", default=None)
    ap.add_argument("--thread-count", type=int, default=1,
                    help="one compute job at a time; CPU only, never MPS")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    print(f"LICENCE: {LICENCE}", file=sys.stderr)
    with open(a.config, encoding="utf-8") as fh:
        config = json.load(fh)
    try:
        payload = run_scout(config, a.frame, a.eval_frame, a.seed, a.cell,
                            a.arm, a.out_dir, thread_count=a.thread_count,
                            preds_dir=a.preds_dir)
    except (fc.FenceViolation, fc.LibraryUnavailable, ScoutRefusal) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        measured = getattr(exc, "state", None)
        if measured:
            print("REFUSAL_EVIDENCE " + json.dumps(measured, default=str),
                  file=sys.stderr)
        return 3
    print(json.dumps({k: payload[k] for k in
                      ("cell", "arm", "seed", "auroc", "auprc", "logloss",
                       "keyed_preds_path", "grade", "licence",
                       "support_rows_used")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
