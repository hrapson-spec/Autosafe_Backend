#!/usr/bin/env python3
"""PERSIST falsification legs 8B / 8C / 8D / 8E — PREREG_PERSIST §8. TRAIN only.

8B  dose-response by consecutive same-system advisory run length (0/1/2/3+)
8C  anatomical specificity: system -> +rfr -> +rfr+loc, WITH the chance baseline
8D  stricter novelty: C(t-2) -> C(t-1) -> A(t) versus persistent
8E  resolution: does a disappeared advisory return to baseline? A->C vs C->C

All use persist_estimator (frozen membership, IRLS). Eligibility is declared per
analysis population and per outcome, because event counts differ between them.

⚠ 8C REQUIRES ITS CHANCE BASELINE. A system with few distinct RfR codes forces
same-item recurrence by construction, so a raw "82% of system recurrences are same-item"
figure is uninterpretable. The baseline is the same-item recurrence rate expected if the
item at t were drawn at random from that system's own marginal item distribution at t,
holding the vehicle's system recurrence fixed.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist_estimator import system_eligibility, standardised_effect, CONTROLS  # noqa: E402

PROG = Path(__file__).resolve().parents[2]
LABELS = {"eval2024": "out/TARGET_SEVERITY_LABELS.parquet",
          "train_flat4y": "out/TRAIN_SEVERITY_LABELS.parquet"}


def std_effect(d, systems, label):
    el = system_eligibility(d, systems)
    E = {s: el[s]["eligible"] for s in systems}
    r = standardised_effect(d, systems, E)
    r.pop("warm", None)
    base = float(d.loc[d.persistent == 0, "y"].mean()) if (d.persistent == 0).any() else None
    diff = r["standardised_risk_diff_pp"] / 100.0
    r.update({"label": label, "n": int(len(d)), "n_positive": int(d.y.sum()),
              "base_rate_reference": base,
              "relative_risk": (base + diff) / base if base else None,
              "own_estimate_systems": [s for s in systems if E[s]],
              "ineligible": {s: el[s]["reason"] for s in systems if not E[s]}})
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default="train_flat4y")
    ap.add_argument("--legs", nargs="+", default=["8B", "8C", "8D", "8E"])
    a = ap.parse_args()
    g = json.loads((PROG / "out/PERSIST_CORRECTNESS_GATE.json").read_text())
    if not g.get("all_pass"):
        print("FATAL: correctness gate has not passed.", file=sys.stderr); sys.exit(2)

    tax = json.loads((PROG / "out/ADVSTRUCT_TAXONOMY.json").read_text())
    idx = json.loads((PROG / "out/PERSIST_SECT_INDEX.json").read_text())
    systems = tax["systems"]
    bridge = idx["ontology_bridge"]["systems"]
    same_sql = "CASE p.sys " + " ".join(
        f"WHEN '{s}' THEN " + " + ".join(c["column"] for c in cols)
        for s, cols in bridge.items()) + " ELSE NULL END"

    ST = str(PROG / f"out/persist/state_{a.frame}.parquet")
    LB = str(PROG / LABELS[a.frame])
    con = duckdb.connect(); con.execute("SET memory_limit='3GB'"); con.execute("SET threads=4")

    # run length: consecutive same-system 'A' episodes ending at ep_rank 1
    con.execute(f"""CREATE OR REPLACE TABLE runs AS
      WITH s AS (SELECT tgt_id, ep_rank, sys, state, graded_regime, adv_rfrs, adv_rfr_locs
                 FROM read_parquet('{ST}')),
      firstnonA AS (SELECT tgt_id, sys, min(ep_rank) AS r FROM s
                    WHERE state IS DISTINCT FROM 'A' GROUP BY 1,2)
      -- run_len = count of consecutive PRIOR same-system 'A' episodes (ranks 2,3,...).
      -- firstnonA.r is the first rank whose state is not 'A'; for a C->A pair that is
      -- rank 2, so the prior run is r-2 = 0. An earlier version used r-1, which scored
      -- C->A as run 1 and left run 0 empty.
      SELECT s.tgt_id, s.sys, coalesce(f.r, 99) - 2 AS run_len
      FROM (SELECT DISTINCT tgt_id, sys FROM s) s
      LEFT JOIN firstnonA f USING (tgt_id, sys)""")

    con.execute(f"""CREATE OR REPLACE TABLE base AS
      WITH s AS (SELECT tgt_id, ep_rank, ep_date, sys, state, graded_regime,
                        n_adv, n_min, n_md, n_items, adv_rfrs, adv_rfr_locs
                 FROM read_parquet('{ST}')),
      p AS (SELECT a.tgt_id, a.sys, a.ep_date AS t_date, a.state AS s_t, b.state AS s_t1,
                   c.state AS s_t2, a.n_adv AS n_adv_sys_t, a.n_items AS n_items_sys_t,
                   a.adv_rfrs AS rfr_t, a.adv_rfr_locs AS rfrloc_t,
                   b.adv_rfrs AS rfr_t1, b.adv_rfr_locs AS rfrloc_t1
            FROM s a JOIN s b USING (tgt_id, sys)
                     LEFT JOIN s c ON c.tgt_id=a.tgt_id AND c.sys=a.sys AND c.ep_rank=3
            WHERE a.ep_rank=1 AND b.ep_rank=2 AND a.graded_regime AND b.graded_regime),
      burden AS (SELECT tgt_id, sum(n_adv) tot_adv_t,
                        sum(CASE WHEN state='A' THEN 1 ELSE 0 END) n_advised_systems_t,
                        sum(n_min) tot_min_t, sum(n_md) tot_md_t
                 FROM s WHERE ep_rank=1 GROUP BY 1),
      depth AS (SELECT tgt_id, max(ep_rank) n_episodes FROM s GROUP BY 1),
      hist AS (SELECT tgt_id, sum(CASE WHEN state='F' THEN 1 ELSE 0 END) n_prior_fail_sysdays,
                      sum(n_md) prior_md_items FROM s WHERE ep_rank>=2 GROUP BY 1)
      SELECT p.*, r.run_len, b.tot_adv_t, b.n_advised_systems_t, b.tot_min_t, b.tot_md_t,
             d.n_episodes, h.n_prior_fail_sysdays, h.prior_md_items,
             l.vehicle_id, date_diff('day', p.t_date, l.tgt_date) AS interval_days,
             ({same_sql}) AS y_same_system_md
      FROM p JOIN read_parquet('{LB}') l ON l.test_id=p.tgt_id
             JOIN burden b USING (tgt_id) JOIN depth d USING (tgt_id)
             LEFT JOIN hist h USING (tgt_id) LEFT JOIN runs r USING (tgt_id, sys)""")

    dest = PROG / f"out/PERSIST_FALSIFY2_{a.frame}.json"
    out = json.loads(dest.read_text()) if dest.exists() else {}
    out.update({"artifact": f"PERSIST falsification legs 8B/8C/8D/8E — {a.frame}",
                "estimator": "persist_estimator.py (frozen membership, IRLS)",
                "prereg_sha256_16": "424dfdd4af84ea56"})
    out.setdefault("legs", {})
    cols = ", ".join(CONTROLS)

    # ---------------------------------------------------------------- 8B
    if "8B" in a.legs:
        print("\n=== 8B dose-response by consecutive run length ===", flush=True)
        df = con.execute(f"""SELECT sys, {cols}, run_len,
            CASE WHEN y_same_system_md>0 THEN 1 ELSE 0 END AS y
            FROM base WHERE s_t='A' AND s_t1 IN ('A','C')""").df()
        df["sys"] = df["sys"].astype("category")
        rows = []
        print(f"  {'run':<7}{'n':>9}{'events':>9}{'raw rate':>11}{'adj vs run0':>14}")
        ref = None
        for lab, pred in (("0", "run_len==0"), ("1", "run_len==1"),
                          ("2", "run_len==2"), ("3+", "run_len>=3")):
            sub = df.query(pred)
            if len(sub) < 200:
                print(f"  {lab:<7}{len(sub):>9,}   too few"); continue
            rate = float(sub.y.mean())
            rows.append({"run": lab, "n": int(len(sub)), "events": int(sub.y.sum()),
                         "raw_rate": rate})
            print(f"  {lab:<7}{len(sub):>9,}{int(sub.y.sum()):>9,}{rate:>11.4f}", flush=True)
        # adjusted: contrast each run>=1 level against run 0 using the same machinery
        adj = {}
        for lab, pred in (("1", "run_len==1"), ("2", "run_len==2"), ("3+", "run_len>=3")):
            d2 = df.query(f"run_len==0 or ({pred})").copy()
            d2["persistent"] = (d2.run_len != 0).astype(int)
            if d2.persistent.sum() < 200:
                continue
            r = std_effect(d2, systems, f"run{lab}_vs_run0")
            adj[lab] = {"risk_diff_pp": r["standardised_risk_diff_pp"],
                        "relative_risk": r["relative_risk"], "n": r["n"]}
            print(f"    adjusted run{lab} vs run0: "
                  f"{r['standardised_risk_diff_pp']:+.4f} pp  RR {r['relative_risk']:.4f}",
                  flush=True)
        mono = all(adj[k]["risk_diff_pp"] < adj[j]["risk_diff_pp"]
                   for k, j in zip(["1", "2"], ["2", "3+"]) if k in adj and j in adj)
        print(f"  PREDICTED monotone increase with run length -> "
              f"{'HOLDS' if mono else 'FAILS'}")
        out["legs"]["8B_dose_response"] = {"raw": rows, "adjusted_vs_run0": adj,
                                           "monotone": bool(mono)}

    # ---------------------------------------------------------------- 8D
    if "8D" in a.legs:
        print("\n=== 8D stricter novelty: C(t-2)->C(t-1)->A(t) vs persistent ===", flush=True)
        df = con.execute(f"""SELECT sys, {cols},
            CASE WHEN y_same_system_md>0 THEN 1 ELSE 0 END AS y,
            CASE WHEN s_t1='A' THEN 1 ELSE 0 END AS persistent
            FROM base WHERE s_t='A' AND (s_t1='A' OR (s_t1='C' AND s_t2='C'))""").df()
        df["sys"] = df["sys"].astype("category")
        r = std_effect(df, systems, "strict_novelty")
        print(f"  n {r['n']:,} | A->A {int(df.persistent.sum()):,} | "
              f"strict C->A {int((1-df.persistent).sum()):,}")
        print(f"  effect {r['standardised_risk_diff_pp']:+.4f} pp  RR {r['relative_risk']:.4f}")
        print(f"  (headline, unrestricted C->A, was +1.4522 pp / RR 1.1333)")
        out["legs"]["8D_strict_novelty"] = r

    # ---------------------------------------------------------------- 8E
    if "8E" in a.legs:
        print("\n=== 8E resolution: A->C vs C->C ===", flush=True)
        df = con.execute(f"""SELECT sys, {cols},
            CASE WHEN y_same_system_md>0 THEN 1 ELSE 0 END AS y,
            CASE WHEN s_t1='A' THEN 1 ELSE 0 END AS persistent
            FROM base WHERE s_t='C' AND s_t1 IN ('A','C')""").df()
        df["sys"] = df["sys"].astype("category")
        r = std_effect(df, systems, "resolution_AtoC_vs_CtoC")
        print(f"  n {r['n']:,} | A->C {int(df.persistent.sum()):,} | "
              f"C->C {int((1-df.persistent).sum()):,}")
        print(f"  A->C excess over C->C: {r['standardised_risk_diff_pp']:+.4f} pp  "
              f"RR {r['relative_risk']:.4f}")
        print(f"  reading: ~0 => the advisory disappearing means genuine resolution;")
        print(f"           >0 => a historical advisory marks durable vulnerability")
        out["legs"]["8E_resolution"] = r

    # ---------------------------------------------------------------- 8C
    if "8C" in a.legs:
        print("\n=== 8C anatomical specificity + chance baseline ===", flush=True)
        lad = con.execute("""
          SELECT count(*) AS n_AA,
            sum(CASE WHEN len(list_intersect(rfr_t, rfr_t1))>0 THEN 1 ELSE 0 END) AS same_rfr,
            sum(CASE WHEN len(list_intersect(rfrloc_t, rfrloc_t1))>0 THEN 1 ELSE 0 END) AS same_rfr_loc
          FROM base WHERE s_t='A' AND s_t1='A'
            AND rfr_t IS NOT NULL AND rfr_t1 IS NOT NULL""").fetchone()
        n_aa, s_rfr, s_loc = lad
        print(f"  A->A pairs with item detail: {n_aa:,}")
        print(f"    same system                : {n_aa:,} (100% by construction)")
        print(f"    ... AND same rfr           : {s_rfr:,} ({s_rfr/n_aa:.1%})")
        print(f"    ... AND same rfr+location  : {s_loc:,} ({s_loc/n_aa:.1%})")
        # chance baseline: probability the t item matches the t-1 item if the t item were
        # drawn from that system's marginal item distribution
        chance = con.execute("""
          WITH obs AS (SELECT sys, unnest(rfr_t) AS rfr FROM base WHERE s_t='A'),
          marg AS (SELECT sys, rfr, count(*)::DOUBLE/sum(count(*)) OVER (PARTITION BY sys) AS pr
                   FROM obs GROUP BY 1,2),
          prior AS (SELECT sys, unnest(rfr_t1) AS rfr FROM base WHERE s_t='A' AND s_t1='A')
          SELECT sum(m.pr)/count(*) FROM prior p JOIN marg m USING (sys, rfr)""").fetchone()[0]
        lift = (s_rfr / n_aa) / chance if chance else None
        print(f"  CHANCE baseline (random draw from the system's own item mix): {chance:.1%}")
        print(f"  LIFT over chance: {lift:.2f}x")
        print(f"  PREDICTED specificity sharpens at finer grain -> "
              f"{'HOLDS' if lift and lift > 1.5 else 'WEAK/FAILS'}")
        out["legs"]["8C_anatomical"] = {
            "n_AA_with_item_detail": int(n_aa), "same_rfr": int(s_rfr),
            "same_rfr_share": s_rfr / n_aa, "same_rfr_loc": int(s_loc),
            "same_rfr_loc_share": s_loc / n_aa, "chance_baseline": chance,
            "lift_over_chance": lift,
            "note": ("raw shares are uninterpretable without the baseline: systems with few "
                     "distinct RfR codes force same-item recurrence by construction")}

    tmp = Path(str(dest) + ".tmp"); tmp.write_text(json.dumps(out, indent=1, default=str))
    os.replace(tmp, dest)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
