#!/usr/bin/env python3
"""Product concentration metrics for a B3/M1 score — the commercially meaningful statement.

"Selecting the highest-risk X% of vehicles identifies Y% of B3 vehicles and Z% of total
serious-defect burden."

AUROC is deliberately NOT reported here. Everything is computed on the untouched
evaluation population (all 330,665 rows), never a subsample.

Defect-burden share is derived from the SAME target-item counts the label came from, so it
carries no additional leakage beyond the label itself: both are properties of the target
MOT and neither is available at prediction time. This is a descriptive concentration
statistic, not a feature.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

CUTS = (0.01, 0.05, 0.10, 0.20, 0.30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--labels", default="out/TARGET_SEVERITY_LABELS.parquet")
    ap.add_argument("--target", default="b3", choices=("b3", "m1"))
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    lab = pq.read_table(ROOT / a.labels)
    L = {n: np.asarray(lab.column(n)) for n in lab.schema.names}
    ids = [int(t) for t in L["test_id"]]
    y = ((L["n_major_or_dangerous"] >= 3) if a.target == "b3"
         else (L["n_sections_with_md"] >= 2)).astype(np.int8)
    # secondary quantities the business statement needs
    any_fail = (L["y_initial"] == 1).astype(np.int8)      # DVSA initial failure (FAIL+PRS)
    md_items = L["n_major_or_dangerous"].astype(np.float64)   # serious-defect burden
    dang = L["n_dangerous"].astype(np.float64)

    t = pq.read_table(ROOT / a.preds).to_pydict()
    ix = {int(v): i for i, v in enumerate(t["test_id"])}
    assert set(ix) == set(ids), "prediction row set != label row set"
    sel = np.array([ix[i] for i in ids])
    p = np.asarray(t["p"], dtype=np.float64)[sel]

    n = y.size
    order = np.argsort(-p, kind="mergesort")
    prev = float(y.mean())
    tot_pos, tot_fail = float(y.sum()), float(any_fail.sum())
    tot_md, tot_dang = float(md_items.sum()), float(dang.sum())

    rows = []
    for c in CUTS:
        k = max(1, int(round(c * n)))
        s = order[:k]
        tp = float(y[s].sum())
        rows.append({
            "cut": c, "n_selected": k,
            "precision": tp / k,
            "recall": tp / tot_pos if tot_pos else float("nan"),
            "lift": (tp / k) / prev if prev else float("nan"),
            "share_of_all_initial_failures": float(any_fail[s].sum()) / tot_fail,
            "share_of_all_md_defects": float(md_items[s].sum()) / tot_md,
            "share_of_all_dangerous_defects": float(dang[s].sum()) / tot_dang,
        })

    # full gain curve (percentile grid) for plotting/inspection
    grid = np.arange(1, 101) / 100.0
    cum = np.cumsum(y[order])
    gain = [{"pct": float(g),
             "recall": float(cum[max(0, int(round(g * n)) - 1)] / tot_pos)}
            for g in grid]

    out = {"score": a.name or Path(a.preds).stem, "preds": a.preds,
           "target": a.target, "n": int(n), "positives": int(tot_pos),
           "prevalence": prev,
           "totals": {"initial_failures": int(tot_fail),
                      "major_or_dangerous_defect_items": int(tot_md),
                      "dangerous_defect_items": int(tot_dang)},
           "cuts": rows, "gain_curve": gain}
    dest = a.out or f"out/PRODUCT_LIFT_{(a.name or 'score')}_{a.target}.json"
    (ROOT / dest).write_text(json.dumps(out, indent=1, default=str))

    print(f"{out['score']}  target={a.target}  n={n:,}  positives={int(tot_pos):,}  "
          f"prevalence={prev:.4f}")
    print(f"{'cut':>6}{'n_sel':>9}{'precision':>11}{'recall':>9}{'lift':>7}"
          f"{'%all fails':>12}{'%all M/D':>10}{'%all dang':>11}")
    for r in rows:
        print(f"{r['cut']*100:>5.0f}%{r['n_selected']:>9,}{r['precision']:>11.4f}"
              f"{r['recall']:>9.4f}{r['lift']:>7.2f}"
              f"{r['share_of_all_initial_failures']*100:>11.1f}%"
              f"{r['share_of_all_md_defects']*100:>9.1f}%"
              f"{r['share_of_all_dangerous_defects']*100:>10.1f}%")
    print(f"  -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
