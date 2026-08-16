#!/usr/bin/env python3
"""PERSIST Phase 1 — episode reconstruction and canonical system-state history.

Per PREREG_PERSIST_2026_08_16.md (sha 424dfdd4af84ea56...) §5.

Emits, per frame:
  out/persist/state_<frame>.parquet   one row per (tgt_id, episode_rank, system) with state
  out/persist/episodes_<frame>.parquet one row per (tgt_id, episode_rank) with episode metadata

Contract, all fatal (exit 2, write nothing):
  G1  episode rank uses dense_rank() over the NT test-DAY, never row_number() over rows
  G2  state derives ONLY from NT (initial) tests; RT contributes no state
  G3  any non-null raw section outside ADVSTRUCT_ONTOLOGY_V1 halts the build
  G4  no target-day item is read (packets carry priors only; asserted)
  G5  pre-2018 episodes are flagged; MINOR is never asserted in the ungraded regime

Reads no outcome. Outcome joins happen in Phase 2, after the correctness gate.
"""
import argparse
import json
import sys
from pathlib import Path

import duckdb

PROG = Path(__file__).resolve().parents[2]
#: ⚠ v2 PACKET SETS ONLY (PREREG_PERSIST §6). In v1 roughly half of prior rows carry
#: `defects_json IS NULL`, which conflates "no defects" with "defect detail unobservable"
#: and would silently mark ~49% of episodes unobservable. Measured 2026-08-16:
#: v1 out/frames/recipe=flat4y = 50.4% NULL; v2 out/frames_v2_flat4y = 1.0% NULL, on
#: identical row and target counts. Gate G6 enforces this; do not "fix" a failure by
#: relaxing the threshold.
FRAMES = {
    "eval2024": "out/frames_eval_v2/recipe=eval2024/rung=all/packets/*.parquet",
    "train_flat4y": "out/frames_v2_flat4y/recipe=flat4y/rung=r1m/packets/*.parquet",
}
#: Above this share of NULL defects_json the packet set is v1-shaped and refused.
MAX_NULL_DEFECTS_SHARE = 0.10
# 2018-05-20: the MOT defect-grading regime change. Before it, items are ungraded
# ('pre2018_ungraded') and MINOR does not exist as a disposition.
GRADED_FROM = "2018-05-20"


def die(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(2)


def build(con, frame, packets, tax, outdir):
    sysset = set(tax["systems"])
    con.execute("DROP TABLE IF EXISTS xw")
    con.execute("CREATE TABLE xw(sect_norm VARCHAR, sys VARCHAR)")
    con.executemany("INSERT INTO xw VALUES (?,?)", list(tax["crosswalk_normalised"].items()))

    NORM = "lower(regexp_replace(trim({}),'[^a-zA-Z0-9]+',' ','g'))"

    # ---- G4: packets are prior-side by construction; assert no p_date >= tgt_date -------
    n_bad = con.execute(f"""
        SELECT count(*) FROM read_parquet('{packets}')
        WHERE p_date IS NOT NULL AND tgt_date IS NOT NULL AND p_date >= tgt_date""").fetchone()[0]
    if n_bad:
        die(f"G4 strict-date violation: {n_bad} prior rows with p_date >= tgt_date")

    # ---- G6: refuse a v1-shaped packet set (PREREG_PERSIST §6, "v2 packets only") -------
    # The first TRAIN build of 2026-08-16 silently used v1 and marked 49% of episodes
    # unobservable. This gate is the test that fix ships with.
    n_rows, n_null = con.execute(f"""
        SELECT count(*), sum(CASE WHEN defects_json IS NULL THEN 1 ELSE 0 END)
        FROM read_parquet('{packets}')""").fetchone()
    share = n_null / max(n_rows, 1)
    if share > MAX_NULL_DEFECTS_SHARE:
        die(f"G6 packet-vintage violation: {share:.1%} of prior rows have defects_json IS NULL "
            f"(limit {MAX_NULL_DEFECTS_SHARE:.0%}). This is a v1-shaped packet set, which "
            f"conflates zero-defects with unobservable. Point at the v2 set; do not relax "
            f"this threshold. Path: {packets}")
    print(f"  G6 packet vintage OK: {share:.2%} NULL defects_json ({n_null:,}/{n_rows:,})",
          flush=True)

    # ---- episodes: one per (tgt_id, NT test-day). G1 dense_rank over the DAY ------------
    con.execute(f"""
        CREATE OR REPLACE TABLE ep AS
        WITH nt AS (
            SELECT DISTINCT tgt_id, p_date
            FROM read_parquet('{packets}')
            WHERE p_ttype = 'NT'                                   -- G2: initials only
        )
        SELECT tgt_id, p_date AS ep_date,
               dense_rank() OVER (PARTITION BY tgt_id ORDER BY p_date DESC) AS ep_rank
        FROM nt
    """)

    # ---- episode metadata: attached retests, PRS, observability ------------------------
    con.execute(f"""
        CREATE OR REPLACE TABLE ep_meta AS
        WITH t AS (SELECT * FROM read_parquet('{packets}') WHERE p_ttype IS NOT NULL),
        nt AS (
            SELECT ep.tgt_id, ep.ep_rank, ep.ep_date,
                   max(CASE WHEN t.defects_json IS NOT NULL THEN 1 ELSE 0 END) AS item_observable,
                   count(*) AS n_nt_on_day,
                   max(t.p_outcome) AS nt_outcome
            FROM ep JOIN t ON t.tgt_id = ep.tgt_id AND t.p_date = ep.ep_date AND t.p_ttype = 'NT'
            GROUP BY 1,2,3
        ),
        nxt AS (   -- retests belong to the episode they follow, up to the next NT day
            SELECT e.tgt_id, e.ep_rank, e.ep_date,
                   min(e2.ep_date) FILTER (WHERE e2.ep_date > e.ep_date) AS next_ep_date
            FROM ep e LEFT JOIN ep e2 ON e2.tgt_id = e.tgt_id
            GROUP BY 1,2,3
        )
        SELECT nt.tgt_id, nt.ep_rank, nt.ep_date, nt.item_observable, nt.n_nt_on_day,
               nt.nt_outcome,
               (nt.ep_date >= DATE '{GRADED_FROM}') AS graded_regime,     -- G5
               coalesce(count(t.p_test_id) FILTER (WHERE t.p_ttype='RT'), 0) AS n_retests,
               max(t.p_date) FILTER (WHERE t.p_ttype='RT') AS last_retest_date,
               max(CASE WHEN t.p_outcome='PRS' THEN 1 ELSE 0 END) AS episode_has_prs
        FROM nt JOIN nxt USING (tgt_id, ep_rank, ep_date)
        LEFT JOIN t ON t.tgt_id = nt.tgt_id AND t.p_ttype='RT'
                   AND t.p_date >= nt.ep_date
                   AND (nxt.next_ep_date IS NULL OR t.p_date < nxt.next_ep_date)
        GROUP BY 1,2,3,4,5,6,7
    """)

    # ---- G3 fail-closed: any non-null section outside the ontology halts ---------------
    unknown = con.execute(f"""
        WITH ex AS (
            SELECT j.sect AS sect FROM read_parquet('{packets}') t,
                   LATERAL (SELECT unnest(from_json(t.defects_json,
                       '[{{"disp":"VARCHAR","sect":"VARCHAR","rfr":"VARCHAR","loc":"VARCHAR"}}]')) j)
            WHERE t.p_ttype='NT' AND t.defects_json IS NOT NULL
        )
        SELECT DISTINCT ex.sect FROM ex
        LEFT JOIN xw ON {NORM.format('xw.sect_norm')} = {NORM.format('ex.sect')}
        WHERE ex.sect IS NOT NULL AND xw.sys IS NULL
    """).fetchall()
    if unknown:
        die(f"G3 unknown sections outside ADVSTRUCT_ONTOLOGY_V1: {[u[0] for u in unknown][:8]}")

    # ---- per (episode, system) state ---------------------------------------------------
    # dedup at (tgt_id, ep_date, system, item_key) per §5.6 before any count
    con.execute(f"""
        CREATE OR REPLACE TABLE items AS
        SELECT DISTINCT
               t.tgt_id, ep.ep_rank, ep.ep_date, xw.sys,
               j.disp AS disp,
               coalesce(j.rfr, {NORM.format('j.sect')}) AS item_key,
               j.rfr AS rfr, j.loc AS loc
        FROM read_parquet('{packets}') t
        JOIN ep ON ep.tgt_id = t.tgt_id AND ep.ep_date = t.p_date
        CROSS JOIN LATERAL (SELECT unnest(from_json(t.defects_json,
            '[{{"disp":"VARCHAR","sect":"VARCHAR","rfr":"VARCHAR","loc":"VARCHAR"}}]')) j)
        LEFT JOIN xw ON {NORM.format('xw.sect_norm')} = {NORM.format('j.sect')}
        WHERE t.p_ttype = 'NT' AND t.defects_json IS NOT NULL
    """)

    sys_list = ", ".join(f"('{s}')" for s in tax["systems"])
    con.execute(f"""
        CREATE OR REPLACE TABLE state AS
        WITH grid AS (
            SELECT m.tgt_id, m.ep_rank, m.ep_date, m.graded_regime, m.item_observable, s.sys
            FROM ep_meta m CROSS JOIN (VALUES {sys_list}) AS s(sys)
        ),
        agg AS (
            SELECT tgt_id, ep_rank, sys,
                   sum(CASE WHEN disp='A' THEN 1 ELSE 0 END) AS n_adv,
                   sum(CASE WHEN disp='M' THEN 1 ELSE 0 END) AS n_min,
                   sum(CASE WHEN disp IN ('F','P') THEN 1 ELSE 0 END) AS n_md,
                   count(DISTINCT item_key) AS n_items,
                   list(DISTINCT rfr) FILTER (WHERE disp='A') AS adv_rfrs,
                   list(DISTINCT rfr || ':' || coalesce(loc,'-')) FILTER (WHERE disp='A')
                       AS adv_rfr_locs
            FROM items WHERE sys IS NOT NULL GROUP BY 1,2,3
        )
        SELECT g.tgt_id, g.ep_rank, g.ep_date, g.sys, g.graded_regime, g.item_observable,
               coalesce(a.n_adv,0) AS n_adv, coalesce(a.n_min,0) AS n_min,
               coalesce(a.n_md,0) AS n_md, coalesce(a.n_items,0) AS n_items,
               a.adv_rfrs, a.adv_rfr_locs,
               CASE
                 WHEN g.item_observable = 0 THEN NULL             -- unobservable: NOT clean
                 WHEN coalesce(a.n_md,0) > 0 THEN 'F'
                 WHEN coalesce(a.n_min,0) > 0 THEN 'M'
                 WHEN coalesce(a.n_adv,0) > 0 THEN 'A'
                 ELSE 'C'
               END AS state
        FROM grid g LEFT JOIN agg a USING (tgt_id, ep_rank, sys)
    """)

    # G5: MINOR may never be asserted in the ungraded regime
    bad_m = con.execute(
        "SELECT count(*) FROM state WHERE state='M' AND NOT graded_regime").fetchone()[0]
    if bad_m:
        die(f"G5 violation: {bad_m} MINOR states asserted on pre-{GRADED_FROM} episodes")

    outdir.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM state ORDER BY tgt_id, ep_rank, sys) "
                f"TO '{outdir}/state_{frame}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(f"COPY (SELECT * FROM ep_meta ORDER BY tgt_id, ep_rank) "
                f"TO '{outdir}/episodes_{frame}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")

    # ---- validation stats (§5.2) -------------------------------------------------------
    q = lambda s: con.execute(s).fetchone()[0]
    gap = con.execute("""
        SELECT CASE WHEN d=0 THEN '0_same_day' WHEN d<=10 THEN '1-10' WHEN d<=30 THEN '11-30'
                    WHEN d<=90 THEN '31-90' ELSE 'over_90' END AS band, count(*) AS n
        FROM (SELECT last_retest_date - ep_date AS d FROM ep_meta WHERE n_retests>0)
        GROUP BY 1 ORDER BY 1""").fetchall()
    stats = {
        "frame": frame,
        "n_raw_prior_tests": q(f"SELECT count(*) FROM read_parquet('{packets}') "
                               f"WHERE p_ttype IS NOT NULL"),
        "n_raw_NT": q(f"SELECT count(*) FROM read_parquet('{packets}') WHERE p_ttype='NT'"),
        "n_raw_RT": q(f"SELECT count(*) FROM read_parquet('{packets}') WHERE p_ttype='RT'"),
        "n_episodes": q("SELECT count(*) FROM ep_meta"),
        "n_targets_with_episode": q("SELECT count(DISTINCT tgt_id) FROM ep_meta"),
        "n_episodes_with_retest": q("SELECT count(*) FROM ep_meta WHERE n_retests>0"),
        "pct_episodes_with_retest": round(
            100 * q("SELECT count(*) FROM ep_meta WHERE n_retests>0")
            / max(q("SELECT count(*) FROM ep_meta"), 1), 3),
        "n_episodes_with_prs": q("SELECT count(*) FROM ep_meta WHERE episode_has_prs=1"),
        "n_days_multi_NT": q("SELECT count(*) FROM ep_meta WHERE n_nt_on_day>1"),
        "n_episodes_item_unobservable": q("SELECT count(*) FROM ep_meta WHERE item_observable=0"),
        "n_episodes_pre2018_ungraded": q("SELECT count(*) FROM ep_meta WHERE NOT graded_regime"),
        "retest_interval_distribution": {b: n for b, n in gap},
        "state_distribution": dict(con.execute(
            "SELECT coalesce(state,'NULL_unobservable'), count(*) FROM state "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()),
        "episode_depth_distribution": dict(con.execute(
            "SELECT least(mx,8), count(*) FROM (SELECT tgt_id, max(ep_rank) mx FROM ep_meta "
            "GROUP BY 1) GROUP BY 1 ORDER BY 1").fetchall()),
    }
    # collapse proof: episodes whose NT FAILED and which carry a passing retest
    stats["collapse_examples"] = con.execute("""
        SELECT tgt_id, ep_rank, ep_date, nt_outcome, n_retests, last_retest_date
        FROM ep_meta WHERE nt_outcome='FAIL' AND n_retests>0 ORDER BY tgt_id LIMIT 5""").df() \
        .astype(str).to_dict("records")
    stats["collapse_invariant"] = {
        "claim": "a FAIL followed by a passing retest yields ONE episode, state from the NT",
        "n_fail_NT_with_retest": q("SELECT count(*) FROM ep_meta "
                                   "WHERE nt_outcome='FAIL' AND n_retests>0"),
        "n_extra_episodes_they_would_have_created_if_RT_counted":
            q(f"SELECT count(*) FROM read_parquet('{packets}') WHERE p_ttype='RT'"),
    }
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", nargs="+", default=["eval2024"])
    ap.add_argument("--tmp", default="/private/tmp/claude-501/-Users-henrirapson/"
                                     "9d2c7c4f-367c-44c4-a13a-cb9138e5974b/scratchpad/duck_persist")
    a = ap.parse_args()
    Path(a.tmp).mkdir(parents=True, exist_ok=True)
    tax = json.loads((PROG / "out/ADVSTRUCT_TAXONOMY.json").read_text())

    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'"); con.execute("SET threads=4")
    con.execute(f"SET temp_directory='{a.tmp}'")
    con.execute("PRAGMA max_temp_directory_size='20GiB'")
    con.execute("SET preserve_insertion_order=false")

    audit = {"artifact": "PERSIST_DATA_AUDIT — Phase 1 episode + state construction",
             "prereg": "prereg/PREREG_PERSIST_2026_08_16.md",
             "prereg_sha256_16": "424dfdd4af84ea56",
             "ontology_version": tax["ontology_version"],
             "graded_regime_from": GRADED_FROM, "frames": {}}
    for f in a.frames:
        print(f"\n=== {f} ===", flush=True)
        s = build(con, f, str(PROG / FRAMES[f]), tax, PROG / "out/persist")
        audit["frames"][f] = s
        for k, v in s.items():
            if not isinstance(v, (dict, list)):
                print(f"  {k:<42} {v:,}" if isinstance(v, int) else f"  {k:<42} {v}")
        print(f"  state_distribution                         {s['state_distribution']}")
        print(f"  retest_interval_distribution               {s['retest_interval_distribution']}")

    dest = PROG / "out/PERSIST_DATA_AUDIT.json"
    prev = json.loads(dest.read_text()) if dest.exists() else {}
    if prev.get("frames"):
        audit["frames"] = {**prev["frames"], **audit["frames"]}
    dest.write_text(json.dumps(audit, indent=1, default=str))
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
