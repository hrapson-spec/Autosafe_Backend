"""Metric twins of the harness's own implementations.

`ablation_tables.load_cell_preds` RE-COMPUTES each fit's AUROC from its keyed
preds and refuses the whole analysis (exit 4) when the JSON value does not
reproduce within 1e-6. Two consequences that drive this module:

1. AUROC must be computed with the SAME tie handling as the harness
   (`ablation_tables.weighted_auroc`: average-rank, mergesort). `auroc` here is
   a line-for-line twin; `tests/test_runners.py::test_auroc_twin_matches_the_harness`
   asserts equality against the harness itself on random data.
2. It must be computed from the FLOAT32 values that land in the parquet, not
   from the float64 the model produced. Rounding to float32 moves the AUROC
   whenever it breaks or creates a tie, which is well above 1e-6 on a large
   eval slice. `as_stored` is the only sanctioned way to prepare predictions.
"""
import numpy as np

EPS = 1e-15


def as_stored(p) -> np.ndarray:
    """The exact float32 vector that will be written to the keyed preds parquet.

    Every reported metric must be computed from THIS array.
    """
    return np.asarray(p, dtype=np.float64).astype(np.float32)


def weighted_auroc(y, p, w) -> float:
    """Average-rank tie handling; twin of ablation_tables.weighted_auroc."""
    y = np.asarray(y)
    p = np.asarray(p)
    w = np.asarray(w, dtype=float)
    order = np.argsort(p, kind="mergesort")
    p_o, y_o, w_o = p[order], y[order], w[order]
    pos_w = w_o * (y_o == 1)
    neg_w = w_o * (y_o == 0)
    wp, wn = pos_w.sum(), neg_w.sum()
    if wp <= 0 or wn <= 0:
        return float("nan")
    new = np.empty(len(p_o), dtype=bool)
    new[0] = True
    np.not_equal(p_o[1:], p_o[:-1], out=new[1:])
    grp = np.cumsum(new) - 1
    n_grp = int(grp[-1]) + 1
    gpos = np.bincount(grp, weights=pos_w, minlength=n_grp)
    gneg = np.bincount(grp, weights=neg_w, minlength=n_grp)
    cum_before = np.concatenate(([0.0], np.cumsum(gneg)[:-1]))
    return float(np.sum(gpos * (cum_before + 0.5 * gneg)) / (wp * wn))


def auroc(y, p) -> float:
    return weighted_auroc(y, p, np.ones(len(np.asarray(p)), dtype=float))


def auprc(y, p) -> float:
    """Average precision, step-wise (sklearn `average_precision_score` rule)."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    order = np.argsort(-p, kind="mergesort")
    y_o = p_o = None
    y_o, p_o = y[order], p[order]
    n_pos = int(y_o.sum())
    if n_pos == 0 or n_pos == len(y_o):
        return float("nan")
    tp = np.cumsum(y_o)
    fp = np.cumsum(1 - y_o)
    # collapse tied scores to their last index: precision/recall are only
    # defined at threshold boundaries
    last = np.empty(len(p_o), dtype=bool)
    last[-1] = True
    np.not_equal(p_o[1:], p_o[:-1], out=last[:-1])
    tp, fp = tp[last], fp[last]
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / n_pos
    recall_prev = np.concatenate(([0.0], recall[:-1]))
    return float(np.sum((recall - recall_prev) * precision))


def logloss(y, p) -> float:
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auroc_fprs(y, p, fprs=(0.01, 0.05, 0.10, 0.20)) -> dict:
    """Partial TPR at fixed FPRs -- the `auroc_fprs` recommended field."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    order = np.argsort(-p, kind="mergesort")
    y_o = y[order]
    tp = np.cumsum(y_o)
    fp = np.cumsum(1 - y_o)
    n_pos, n_neg = int(y_o.sum()), int(len(y_o) - y_o.sum())
    if n_pos == 0 or n_neg == 0:
        return {str(f): float("nan") for f in fprs}
    out = {}
    for target in fprs:
        idx = np.searchsorted(fp / n_neg, target, side="right") - 1
        out[str(target)] = float(tp[idx] / n_pos) if idx >= 0 else 0.0
    return out
