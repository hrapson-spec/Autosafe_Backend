#!/usr/bin/env python3
"""PERSIST falsification legs — PREREG_PERSIST §8. TRAIN only until the rule is frozen.

Rewritten 2026-08-16 onto persist_estimator (frozen membership, IRLS). The earlier run
used the pre-fix estimator, whose per-replicate membership varied; those 8A/8G numbers
are superseded by this file.

8A  same-system vs other-system specificity   <- Gate B condition (3)
8B  dose-response by consecutive run length
8D  stricter novelty: C(t-2) -> C(t-1) -> A(t) vs persistent
8E  resolution: A->C vs C->C
8G  conditional permutation placebo

8C (anatomical ladder + chance baseline) is a separate script — it needs item-grain data.
8F is DROPPED: no station or tester identifier exists anywhere in the lake.

⚠ ELIGIBILITY IS OUTCOME-SPECIFIC. The other-system outcome has different per-system
event counts than the same-system outcome, so it gets its own eligibility declaration.
Reusing one set across outcomes would silently change which systems carry their own
coefficient between the two arms being compared.

⚠ Risk DIFFERENCES are not comparable across outcomes whose base rates differ (~8x
opportunity for other-system). §8A's prediction RR_same > RR_other is judged on the
RELATIVE scale; the absolute difference is reported alongside.
"""
import argparse
import json
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist_estimator import system_eligibility, standardised_effect  # noqa: E402
from persist_analyze import build_frame  # noqa: E402
from persist_estimator import CONTROLS  # noqa: E402

PROG = Path(__file__).resolve().parents[2]
LABELS = {"eval2024": "out/TARGET_SEVERITY_LABELS.parquet",
          "train_flat4y": "out/TRAIN_SEVERITY_LABELS.parquet"}


def effect(df, systems, ycol=None, yvec=None, label=""):
    """Standardised risk difference + relative risk, with its OWN eligibility."""
    d = df.copy()
    d["y"] = (d[ycol].fillna(0) > 0).astype(int) if yvec is None else yvec
    el = system_eligibility(d, systems)
    E = {s: el[s]["eligible"] for s in systems}
    r = standardised_effect(d, systems, E)
    r.pop("warm", None)
    base = float(d.loc[d.persistent == 0, "y"].mean())
    diff = r["standardised_risk_diff_pp"] / 100.0
    r.update({"label": label, "base_rate_new_advisory": base,
              "relative_risk": (base + diff) / base if base > 0 else None,
              "n": int(len(d)), "n_positive": int(d.y.sum()),
              "own_estimate_systems": [s for s in systems if E[s]],
              "ineligible": {s: el[s]["reason"] for s in systems if not E[s]}})
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default="train_flat4y")
    ap.add_argument("--seed", type=int, default=20260816)
    ap.add_argument("--legs", nargs="+", default=["8A", "8G"])
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
    df = con.execute(f"""SELECT sys, persistent, vehicle_id, {', '.join(CONTROLS)},
        y_same_system_md, y_other_system_md FROM frame""").df()
    df["sys"] = df["sys"].astype("category")

    dest = PROG / f"out/PERSIST_FALSIFY_{a.frame}.json"
    out = json.loads(dest.read_text()) if dest.exists() else {}
    out.update({"artifact": f"PERSIST falsification legs — {a.frame}",
                "estimator": "persist_estimator.py (frozen membership, IRLS)",
                "supersedes": "the 2026-08-16 run on the pre-fix sklearn estimator",
                "prereg_sha256_16": "424dfdd4af84ea56",
                "frame": a.frame, "n_rows": int(len(df))})
    out.setdefault("legs", {})

    # ------------------------------------------------------------------ 8A
    if "8A" in a.legs:
        print("\n=== 8A same-system vs other-system specificity ===", flush=True)
        same = effect(df, systems, ycol="y_same_system_md", label="same-system")
        other = effect(df, systems, ycol="y_other_system_md", label="other-system")
        for r in (same, other):
            print(f"  {r['label']:<14} base {r['base_rate_new_advisory']:.4f} | "
                  f"diff {r['standardised_risk_diff_pp']:+.4f} pp | "
                  f"RR {r['relative_risk']:.4f} | tau {r['tau']:.4f} | "
                  f"own {len(r['own_estimate_systems'])}/9")
            if r["ineligible"]:
                print(f"                 ineligible: {r['ineligible']}")
        ratio = ((same["relative_risk"] - 1) / (other["relative_risk"] - 1)
                 if other["relative_risk"] not in (None, 1) else None)
        holds = same["relative_risk"] > other["relative_risk"]
        print(f"  excess-risk ratio (same-1)/(other-1) = {ratio:.3f}")
        print(f"  PREDICTED RR_same > RR_other -> {'HOLDS' if holds else 'FAILS'}")
        out["legs"]["8A_specificity"] = {
            "same_system": same, "other_system": other, "excess_risk_ratio": ratio,
            "prediction_RR_same_gt_RR_other": bool(holds),
            "note": "judged on the RELATIVE scale; eligibility declared per outcome"}

    # ------------------------------------------------------------------ 8G
    if "8G" in a.legs:
        print("\n=== 8G conditional permutation placebo ===", flush=True)
        rng = np.random.default_rng(a.seed)
        d = df.copy()
        d["y"] = (d.y_same_system_md.fillna(0) > 0).astype(int)
        # strata preserve target composition AND per-system treated counts, so the
        # eligibility declaration is unchanged by the permutation.
        d["_stratum"] = d.sys.astype(str) + "|" + d.tot_adv_t.clip(upper=6).astype(str)
        perm = d.groupby("_stratum", group_keys=False, observed=True)["persistent"] \
                .transform(lambda v: rng.permutation(v.to_numpy()))
        el = system_eligibility(d, systems)
        E = {s: el[s]["eligible"] for s in systems}
        r_real = standardised_effect(d, systems, E); r_real.pop("warm", None)
        dp = d.copy(); dp["persistent"] = perm.to_numpy()
        el_p = system_eligibility(dp, systems)
        same_elig = all(el_p[s]["eligible"] == E[s] for s in systems)
        r_perm = standardised_effect(dp, systems, E); r_perm.pop("warm", None)
        destroyed = 1 - abs(r_perm["standardised_risk_diff_pp"]) / \
            abs(r_real["standardised_risk_diff_pp"])
        print(f"  observed  {r_real['standardised_risk_diff_pp']:+.4f} pp")
        print(f"  permuted  {r_perm['standardised_risk_diff_pp']:+.4f} pp")
        print(f"  destroyed {destroyed:.1%}")
        print(f"  eligibility unchanged by permutation: {same_elig}")
        print(f"  PREDICTED placebo destroys/substantially reduces -> "
              f"{'HOLDS' if destroyed > 0.5 else 'FAILS'}")
        out["legs"]["8G_placebo"] = {
            "observed_pp": r_real["standardised_risk_diff_pp"],
            "permuted_pp": r_perm["standardised_risk_diff_pp"],
            "share_destroyed": destroyed,
            "strata": "system x total current advisory count (capped at 6)",
            "eligibility_invariant_under_permutation": bool(same_elig),
            "destroys_effect": bool(destroyed > 0.5)}

    tmp = Path(str(dest) + ".tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str))
    import os
    os.replace(tmp, dest)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
