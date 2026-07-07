"""Offline scoring + evaluation harness for the AutoSafe V55 model.

Runs raw DVSA vehicle-history JSON through the EXACT deployed serving path
(dvsa_client._parse_response -> model_v55.engineer_features_with_stats ->
predict_risk) with no network access. Two entry points:

  score_record(client, record)   one full-history prediction (what the live
                                  endpoint returns for a vehicle)
  evaluate(fixtures)             held-out-newest-test AUC + slices over a
                                  fixture corpus (matches audit a1_03)

CLI:  python -m offline_eval.harness --fixtures <file.jsonl[.gz] | dir/>
"""
import argparse
import glob
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import model_v55  # noqa: E402
from dvsa_client import DVSAClient  # noqa: E402

FAIL = {"F", "FAIL", "FAILED", "ABA", "ABR", "ABANDONED"}
PASS = {"P", "PASS", "PASSED", "PRS"}
DEFAULT_PRED_DATE = datetime(2026, 6, 10)
CYCLE_GAP_DAYS = 180   # >= this between target and prior test => new annual cycle

_loaded = False


def _ensure_loaded():
    global _loaded
    if not _loaded:
        assert model_v55.load_model(), "model artefacts failed to load"
        _loaded = True


def make_client():
    """Parser-only DVSAClient (no network, no __init__)."""
    return DVSAClient.__new__(DVSAClient)


def label(result):
    r = (result or "").upper()
    if r in FAIL:
        return 1
    if r in PASS:
        return 0
    return None


def newest_first(tests):
    return sorted(tests, key=lambda t: t.get("completedDate") or "", reverse=True)


def score_record(client, record, prediction_date=DEFAULT_PRED_DATE, postcode=""):
    """Full-history prediction, exactly as GET /api/risk/v55 would produce."""
    _ensure_loaded()
    hist = client._parse_response(record.get("registration", "X"), record)
    feats = model_v55.engineer_features_with_stats(hist, postcode, prediction_date)
    return model_v55.predict_risk(feats)


def iter_fixtures(path):
    """Yield raw history dicts from a .jsonl[.gz] file or a dir of .json files."""
    p = Path(path)
    if p.is_dir():
        for jf in sorted(p.glob("*.json")):
            yield json.loads(jf.read_text())
    elif p.suffix == ".gz":
        with gzip.open(p, "rt") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
    else:
        with open(p) as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def evaluate(fixtures_path, prediction_date=DEFAULT_PRED_DATE, cap=None):
    """Hold out each vehicle's newest test as target; score prior history;
    return AUC (overall, cycle-initial, by cohort/make) + bootstrap CI.
    AUC is on raw_probability (calibration-invariant)."""
    import numpy as np
    from sklearn.metrics import roc_auc_score

    _ensure_loaded()
    client = make_client()
    rows = []
    for v in iter_fixtures(fixtures_path):
        tests = newest_first(v.get("motTests") or [])
        if not tests:
            continue
        target = tests[0]
        y = label(target.get("testResult"))
        if y is None:
            continue
        gap = None
        if len(tests) > 1:
            try:
                gap = (datetime.fromisoformat((target.get("completedDate") or "")[:10])
                       - datetime.fromisoformat((tests[1].get("completedDate") or "")[:10])).days
            except Exception:
                gap = None
        prior = dict(v)
        prior["motTests"] = tests[1:]
        try:
            pred = score_record(client, prior, prediction_date)
        except Exception:
            continue
        rows.append({"raw": pred["raw_probability"], "y": y,
                     "n_prior": len(tests) - 1,
                     "make": (v.get("make") or "UNK").upper(),
                     "gap": gap if gap is not None else -1})
    if not rows:
        return {"error": "no scoreable fixtures"}

    raw = np.array([r["raw"] for r in rows])
    y = np.array([r["y"] for r in rows])
    n_prior = np.array([r["n_prior"] for r in rows])
    gap = np.array([r["gap"] for r in rows])
    make = np.array([r["make"] for r in rows])

    def auc_of(mask):
        yy = y[mask]
        if mask.sum() < 30 or len(set(yy)) < 2:
            return None, int(mask.sum()), None
        return (round(float(roc_auc_score(yy, raw[mask])), 4),
                int(mask.sum()), round(float(yy.mean()), 4))

    overall = float(roc_auc_score(y, raw)) if len(set(y)) == 2 else None
    rng = np.random.default_rng(42)
    boot = [roc_auc_score(y[i], raw[i]) for i in
            (rng.integers(0, len(y), len(y)) for _ in range(200))] if overall else []
    ci = [round(float(np.percentile(boot, 2.5)), 4),
          round(float(np.percentile(boot, 97.5)), 4)] if boot else None

    cyc = gap >= CYCLE_GAP_DAYS
    slices = {
        "ALL": {"auc": round(overall, 4) if overall else None,
                "n": len(y), "fail_rate": round(float(y.mean()), 4)},
        "cycle_initial(gap>=180d)": dict(zip(("auc", "n", "fail_rate"), auc_of(cyc))),
        "veterans(n_prior>=1)": dict(zip(("auc", "n", "fail_rate"), auc_of(n_prior >= 1))),
        "no_prior(n_prior=0)": dict(zip(("auc", "n", "fail_rate"), auc_of(n_prior == 0))),
    }
    for mk in [m for m, c in sorted(((m, int((make == m).sum())) for m in set(make)),
                                    key=lambda x: -x[1])[:6]]:
        slices[f"make={mk}"] = dict(zip(("auc", "n", "fail_rate"), auc_of(make == mk)))
    return {"n_scored": len(rows), "auc_all": round(overall, 4) if overall else None,
            "ci95": ci, "cycle_initial_auc": slices["cycle_initial(gap>=180d)"]["auc"],
            "slices": slices}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True,
                    help="path to .jsonl[.gz] file or directory of .json histories")
    ap.add_argument("--prediction-date", default="2026-06-10")
    args = ap.parse_args()
    res = evaluate(args.fixtures,
                   prediction_date=datetime.fromisoformat(args.prediction_date))
    print(json.dumps(res, indent=1))
    s = res.get("slices", {})
    if s:
        print(f"\n{'slice':28s} {'n':>7} {'AUC':>7} {'fail':>6}")
        for k, v in s.items():
            a = f"{v['auc']:.4f}" if v.get("auc") else "  n/a"
            print(f"{k:28s} {v['n']:7,} {a:>7} {v.get('fail_rate')}")


if __name__ == "__main__":
    main()
