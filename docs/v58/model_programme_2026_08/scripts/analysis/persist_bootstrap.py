#!/usr/bin/env python3
"""PERSIST Phase 2 bootstrap driver — frozen membership, checkpointed, resumable.

Supersedes the bootstrap loop in persist_analyze.py, which was killed on 2026-08-16
after it was shown to let per-replicate FIT SUCCESS decide the estimand (16.0% membership
flicker in cohort A) and to renormalise the denominator over survivors.

Design decisions, all traceable:

  MEMBERSHIP   nine systems in the denominator of every replicate; eligibility for a
               system-specific coefficient declared ONCE from the full sample. See
               persist_estimator.py.

  WEIGHTS      VARY with the resample. PREREG_PERSIST §6.3 says "g-computation over the
               observed system mix" and does NOT nominate the TRAIN composition as a
               fixed reference population; §6.3 governs TRAIN and EVAL alike, so a fixed
               TRAIN reference would force EVAL to standardise to TRAIN, which it nowhere
               says. Owner ruling 2026-08-16.

  RNG          replicate-indexed: default_rng([master_seed, rep]). Replicate i is
               reproducible in isolation, resume is exact, and worker scheduling cannot
               change which sample belongs to which replicate.

  FAILURE      an ELIGIBLE system that will not fit is a REPLICATE FAILURE, recorded with
               diagnostics, never silently skipped. Skipping is not neutral: the samples
               that break a fit are disproportionately the tail-forming ones, so dropping
               them biases the interval NARROW.

  CHECKPOINT   atomic write every --checkpoint-every replicates. A multi-hour
               deterministic job that writes nothing until the end should not exist.

  INTERVAL     percentile, alpha=0.05, numpy linear interpolation. Frozen here.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist_estimator import (FitFailure, system_eligibility,  # noqa: E402
                               standardised_effect, CONTROLS)
from persist_analyze import build_frame  # noqa: E402

PROG = Path(__file__).resolve().parents[2]
LABELS = {"eval2024": "out/TARGET_SEVERITY_LABELS.parquet",
          "train_flat4y": "out/TRAIN_SEVERITY_LABELS.parquet"}
ALPHA = 0.05                      # frozen
INTERVAL = "percentile"           # frozen
CI_METHOD_NOTE = "numpy.percentile, linear interpolation, [2.5, 97.5]"


def sha_file(p, n=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while (b := f.read(n)):
            h.update(b)
    return h.hexdigest()


def provenance(frame):
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=PROG, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return None
    import sklearn
    import pandas
    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain",
                              "docs/v58/model_programme_2026_08/scripts/analysis")),
        "estimator_sha256": sha_file(PROG / "scripts/analysis/persist_estimator.py"),
        "driver_sha256": sha_file(PROG / "scripts/analysis/persist_bootstrap.py"),
        "state_sha256": sha_file(PROG / f"out/persist/state_{frame}.parquet"),
        "labels_sha256": sha_file(PROG / LABELS[frame]),
        "prereg_sha256_16": "424dfdd4af84ea56",
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__,
                     "pandas": pandas.__version__, "sklearn": sklearn.__version__,
                     "duckdb": duckdb.__version__},
        "controls": list(CONTROLS), "alpha": ALPHA, "interval": INTERVAL,
        "ci_method": CI_METHOD_NOTE,
    }


def atomic_write(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, path)


def ci(v):
    v = np.asarray([x for x in v if np.isfinite(x)], float)
    if len(v) < 20:
        return None
    return [float(np.percentile(v, 100 * ALPHA / 2)),
            float(np.percentile(v, 100 * (1 - ALPHA / 2)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default="train_flat4y")
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260816)
    ap.add_argument("--checkpoint-every", type=int, default=50)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    g = json.loads((PROG / "out/PERSIST_CORRECTNESS_GATE.json").read_text())
    if not g.get("all_pass"):
        print("FATAL: correctness gate has not passed.", file=sys.stderr)
        sys.exit(2)

    tax = json.loads((PROG / "out/ADVSTRUCT_TAXONOMY.json").read_text())
    idx = json.loads((PROG / "out/PERSIST_SECT_INDEX.json").read_text())
    systems = tax["systems"]

    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'"); con.execute("SET threads=4")
    build_frame(con, a.frame, str(PROG / LABELS[a.frame]),
                str(PROG / f"out/persist/state_{a.frame}.parquet"), idx)
    keep = ["sys", "persistent", "vehicle_id"] + CONTROLS
    df = con.execute(f"""SELECT {', '.join(keep)},
        CASE WHEN y_same_system_md > 0 THEN 1 ELSE 0 END AS y,
        CASE WHEN tot_adv_t=1 AND n_adv_sys_t=1 AND tot_min_t=0 AND tot_md_t=0
             THEN 1 ELSE 0 END AS cohortA
        FROM frame""").df()
    df["sys"] = df["sys"].astype("category")

    # ---- eligibility: declared ONCE, per cohort, from the full sample --------------
    elig_B = system_eligibility(df, systems)
    elig_A = system_eligibility(df[df.cohortA == 1], systems)
    EB = {s: elig_B[s]["eligible"] for s in systems}
    EA = {s: elig_A[s]["eligible"] for s in systems}
    print(f"=== {a.frame} ===  rows {len(df):,} | cohort A {int(df.cohortA.sum()):,}")
    print(f"{'system':<18}{'B: own?':>9}{'A: own?':>9}   A reason")
    for s in systems:
        print(f"{s:<18}{str(EB[s]):>9}{str(EA[s]):>9}   {elig_A[s]['reason']}")
    print(f"  cohort B own-estimate systems: {sum(EB.values())}/9  "
          f"| cohort A: {sum(EA.values())}/9  (denominator is 9 for both)")

    fullB = standardised_effect(df, systems, EB)
    fullA = standardised_effect(df[df.cohortA == 1], systems, EA)
    warmB, warmA = fullB.pop("warm"), fullA.pop("warm")
    print(f"  cohort B {fullB['standardised_risk_diff_pp']:+.6f} pp  tau {fullB['tau']:.4f}")
    print(f"  cohort A {fullA['standardised_risk_diff_pp']:+.6f} pp  tau {fullA['tau']:.4f}")

    # ---- resample index scaffolding ------------------------------------------------
    veh = df.vehicle_id.to_numpy()
    uniq, inv = np.unique(veh, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    starts = np.searchsorted(inv[order], np.arange(len(uniq)))
    counts = np.diff(np.append(starts, len(order)))
    slim = df.drop(columns=["vehicle_id"])

    ckpt = PROG / f"out/persist/CHECKPOINT_{a.frame}.json"
    st = {"reps_done": [], "dB": [], "dA": [], "dD": [], "failures": []}
    if a.resume and ckpt.exists():
        st = json.loads(ckpt.read_text())["state"]
        print(f"  RESUMED from checkpoint at {len(st['reps_done'])} replicates")
    done = set(st["reps_done"])

    t0 = time.time()
    for rep in range(a.reps):
        if rep in done:
            continue
        rr = np.random.default_rng([a.seed, rep])      # replicate-indexed stream
        pick = rr.integers(0, len(uniq), len(uniq))
        idxs = order[np.repeat(starts[pick], counts[pick])
                     + np.arange(counts[pick].sum())
                     - np.repeat(np.cumsum(counts[pick]) - counts[pick], counts[pick])]
        d = slim.take(idxs)
        try:
            rb = standardised_effect(d, systems, EB, warm=warmB)
            ra = standardised_effect(d[d.cohortA == 1], systems, EA, warm=warmA)
        except FitFailure as e:
            st["failures"].append({"rep": rep, "system": e.system,
                                   "reason": e.reason, "diag": e.diag})
            print(f"    REPLICATE FAILURE rep {rep}: {e.system} — {e.reason}", flush=True)
            continue
        st["reps_done"].append(rep)
        st["dB"].append(rb["standardised_risk_diff_pp"])
        st["dA"].append(ra["standardised_risk_diff_pp"])
        st["dD"].append(ra["standardised_risk_diff_pp"]
                        - 0.5 * rb["standardised_risk_diff_pp"])
        n = len(st["reps_done"])
        if n % a.checkpoint_every == 0:
            atomic_write(ckpt, {"frame": a.frame, "provenance": provenance(a.frame),
                                "state": st, "elapsed_s": round(time.time() - t0, 1)})
            print(f"    {n}/{a.reps}  ({time.time()-t0:.0f}s)  ckpt", flush=True)

    out = {"artifact": f"PERSIST Phase 2 bootstrap — {a.frame}",
           "provenance": provenance(a.frame),
           "membership": {
               "denominator_systems": 9,
               "cohortB_own_estimate": [s for s in systems if EB[s]],
               "cohortA_own_estimate": [s for s in systems if EA[s]],
               "eligibility_B": elig_B, "eligibility_A": elig_A,
               "rule": ("eligibility declared once from the full sample; ineligible systems "
                        "take the pooled coefficient via offset in EVERY replicate and keep "
                        "their rows in the denominator; membership never varies")},
           "weights": "vary with each vehicle-level resample (PREREG §6.3, owner ruling)",
           "point": {"cohortB": fullB, "cohortA": fullA,
                     "D": fullA["standardised_risk_diff_pp"]
                          - 0.5 * fullB["standardised_risk_diff_pp"]},
           "bootstrap": {"reps_requested": a.reps, "reps_ok": len(st["reps_done"]),
                         "replicate_failures": len(st["failures"]),
                         "failure_detail": st["failures"][:50],
                         "seed": a.seed, "rng": "default_rng([seed, rep]) per replicate",
                         "cluster": "vehicle_id",
                         "ci95_cohortB_pp": ci(st["dB"]),
                         "ci95_cohortA_pp": ci(st["dA"]),
                         "ci95_D_pp": ci(st["dD"])}}
    atomic_write(PROG / f"out/PERSIST_PHASE2_{a.frame}.json", out)
    b = out["bootstrap"]
    print(f"\ncohort B {fullB['standardised_risk_diff_pp']:+.4f} pp  CI {b['ci95_cohortB_pp']}")
    print(f"cohort A {fullA['standardised_risk_diff_pp']:+.4f} pp  CI {b['ci95_cohortA_pp']}")
    print(f"D        {out['point']['D']:+.4f} pp  CI {b['ci95_D_pp']}")
    print(f"reps ok {b['reps_ok']}/{a.reps} | replicate failures {b['replicate_failures']}")


if __name__ == "__main__":
    main()
