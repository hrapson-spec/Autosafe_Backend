"""faithful_inject.py — candidate-FAITHFUL injection (NOT full serving-path fidelity).

The promotion path must not use the dev `mot_tests=[]` shim. This reconstructs a real
VehicleHistory from v57_packets (mirroring the canonical builder in r2b_build_v57.py
`build_target`) and computes ONLY the candidate feature over it. It deliberately does NOT
re-run the full serving feature-engineering (`engineer_features_with_stats`) — that full
parity is P2-adjacent and out of scope (review pt 11): this is *candidate*-faithful, not
serving-path-faithful. The reconstruction is one pure function so it cannot drift; the
golden test pins it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace


def reconstruct_history_kwargs(rows) -> dict:
    """Pure: packet rows for ONE target (ordered p_date DESC) -> VehicleHistory kwargs with
    POPULATED mot_tests. Mirrors r2b_build_v57.build_target's anchor/make/model/mots logic
    (first-use anchor = latest prior test's first-use date, else tgt_fud)."""
    r0 = rows[0]
    mots = []
    for r in rows:
        if r.get("p_test_id") is None:
            break  # no-prior target (LEFT JOIN null row)
        defects = []
        dj = r.get("defects_json")
        if dj:
            for d in json.loads(dj):
                defects.append({"type": d.get("t"), "text": d.get("x")})
        mots.append({"test_date": r.get("p_date"), "test_result": r.get("p_result"),
                     "odometer_value": r.get("p_miles"), "test_number": r.get("p_test_id"),
                     "defects": defects})
    latest = rows[0] if mots else None
    fud = (latest and latest.get("p_fud")) or r0.get("tgt_fud")
    make = (latest and latest.get("p_make")) or r0.get("tgt_make") or "UNKNOWN"
    model = (latest and latest.get("p_model")) or str(r0.get("tgt_model_id") or "UNKNOWN")
    fuel = (latest and latest.get("p_fuel")) or None
    return {"registration_date": fud, "manufacture_date": fud, "make": make,
            "model": model, "fuel_type": fuel, "mot_tests": mots}


def _to_history(kwargs, VehicleHistory=None, MOTTest=None):
    mots = kwargs["mot_tests"]
    mots = [(MOTTest(**m) if MOTTest else SimpleNamespace(**m)) for m in mots]
    hk = dict(kwargs, mot_tests=mots)
    return VehicleHistory(**hk) if VehicleHistory else SimpleNamespace(**hk)


def compute_candidate_over_history(rows, candidate_fn, *, target_date, postcode="",
                                   VehicleHistory=None, MOTTest=None) -> dict:
    """Reconstruct the faithful VehicleHistory for one target and compute the candidate.
    Defaults to SimpleNamespace stubs (the candidate reads only the attributes it needs),
    so no serving-code import is required for the age control."""
    hist = _to_history(reconstruct_history_kwargs(rows), VehicleHistory, MOTTest)
    return candidate_fn(hist, postcode, target_date)


def faithful_inject_frames(dev, oot, candidate_fn, packets_path, *,
                           VehicleHistory=None, MOTTest=None):
    """Heavy path (used by the promotion CLI): for every target in dev+oot, read its full
    packet rows, reconstruct the faithful history, compute the candidate, and merge the new
    columns into the frames. Returns (dev, oot, candidate_cols)."""
    import duckdb
    import pandas as pd

    ids = pd.concat([dev["test_id"], oot["test_id"]]).unique().tolist()
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("CREATE TEMP TABLE want(id BIGINT)")
    con.executemany("INSERT INTO want VALUES (?)", [(int(i),) for i in ids])
    pk = con.execute(f"""
        SELECT p.* FROM read_parquet('{packets_path}') p JOIN want w ON p.tgt_id = w.id
        ORDER BY p.tgt_id, p.p_date DESC
    """).df()
    con.close()

    recs, cand_cols = {}, set()
    for tgt_id, grp in pk.groupby("tgt_id", sort=False):
        rows = grp.to_dict("records")
        tdate = rows[0].get("tgt_date")
        feats = compute_candidate_over_history(
            rows, candidate_fn, target_date=tdate, postcode="",
            VehicleHistory=VehicleHistory, MOTTest=MOTTest)
        recs[int(tgt_id)] = feats
        cand_cols.update(feats)
    cand_cols = sorted(cand_cols)

    cand_df = pd.DataFrame.from_dict(recs, orient="index").reset_index(names="test_id")
    out = []
    for df in (dev, oot):
        merged = df.merge(cand_df, on="test_id", how="left")
        for col in cand_cols:
            import candidate_feature as cf
            fill = cf.MISSING if "missing" not in col else 1
            merged[col] = merged[col].fillna(fill)
        out.append(merged)
    return out[0], out[1], cand_cols
