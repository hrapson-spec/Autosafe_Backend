#!/usr/bin/env python3
"""F7b v2 — tie-rule materiality at the metric level. Dump-based design.

Diagnosis trail (2026-08-12, all full-frame, recorded in F7B_DIAG notes):
  - scoring the arm's own dump_eval_matrix reproduces keyed.p EXACTLY
    (1,048,500 rows, max|dp|=0.0) -> scoring core + env validated;
  - my 40-col patch block from the banked FD frame == the dump's patched
    columns EXACTLY (full frame) -> patch construction validated;
  - root cause of the earlier S1 failure: the FD frame stores float64, the
    dump float32; float64 injection flips tree split sides. The
    representation of record is the DUMP's dtypes.

Design: baseline = dump as-is (== keyed, proven). Counterfactual = dump with
ONLY the 40 fulldepth columns replaced by the D13-ordered re-stream values,
cast per-column to the dump's exact dtypes. Gates:
  G1  dump-unmodified first-batch score == keyed (env drift tripwire);
  G2  dump patched with the BANKED FD block == keyed FULL FRAME (validates
      cast+patch machinery end-to-end);
  G3  S2 re-stream produces exactly the dump's target set (no gaps).
Then S3 scores the counterfactual (seed0+seed1) and reports deltas vs
FLOOR=0.002052, per-target |dp|, ECE, decile migration, exposed/unexposed.
"""
import gc
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

T0 = time.time()
def log(msg):
    print(f"[{time.time()-T0:7.0f}s] {msg}", flush=True)

FLOOR = 0.002052
SCRATCH = Path(__file__).parent
WORK = Path.home() / "autosafe/work"
ART = WORK / "goal_0750/feature_repr_review_v1/artifacts"
DUMP = ART / "fulldepth_arm/dump_eval_matrix.parquet"
PACKETS = ART / "fulldepth_packets.parquet"
TEST_GRAIN = ART / "test_grain_history_v1.parquet"
FD_BANKED = ART / "fulldepth_frame_v1.parquet"
CF_FRAME = SCRATCH / "fulldepth_frame_v1_D13cf.parquet"
RESULT = SCRATCH / "F7B_RESULT.json"
MODULE_REPO = Path.home() / "autosafe"
PINNED_SHA = "38203e4"

TYPE_RANK = {"NT": 0, "PL": 1, "PV": 1, "RT": 1, "ES": 2, "EI": 2}   # 7f3d8f1
OUTCOME_RANK = {"FAIL": 1, "PRS": 2, "PASS": 3}                       # 7f3d8f1
API_TYPE = {"A": "ADVISORY", "F": "MAJOR", "M": "MINOR", "P": "PRS"}
PATHOLOGY_BOUND = 80
SOURCE_FLOOR = date(2005, 1, 1)

def preflight():
    r = subprocess.run(["pgrep", "-f",
                        "pipeline.run_lake|sharded_cycles|stream_cycles|run_aggregates"],
                       capture_output=True, text=True)
    if r.stdout.strip():
        sys.exit(f"S0 FAIL: pipeline jobs alive: {r.stdout.split()}")
    free = shutil.disk_usage("/System/Volumes/Data").free / 2**30
    if free < 6:
        sys.exit(f"S0 FAIL: free {free:.1f}GiB < 6")
    sha = subprocess.run(["git", "-C", str(MODULE_REPO), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    if not sha.startswith(PINNED_SHA):
        sys.exit(f"S0 FAIL: module repo at {sha} != pinned {PINNED_SHA}")
    log(f"S0 OK (free={free:.1f}GiB, module {sha})")

preflight()

import duckdb                      # noqa: E402
import numpy as np                 # noqa: E402
import pandas as pd                # noqa: E402
import pyarrow as pa               # noqa: E402
import pyarrow.parquet as pq       # noqa: E402
import pickle                      # noqa: E402
from catboost import CatBoostClassifier   # noqa: E402
from xgboost import XGBClassifier         # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

sys.path.insert(0, str(MODULE_REPO))
import model_v55                                    # noqa: E402
from dvsa_client import MOTTest, VehicleHistory     # noqa: E402

COLS_META = json.loads((ART / "fulldepth_seed0_columns.json").read_text())
COLS, ALLCAT = COLS_META["cols"], COLS_META["cat"]
NAME_MAP = json.loads((ART / "fulldepth_name_map.json").read_text())
SWAP = NAME_MAP["swap"]
PATCH_COLS = list(SWAP) + ["has_prior_test_observed", "history_years_observed",
                           "window_days_available", "has_left_truncated_history",
                           "first_observed_test_is_not_true_first"]
DUMP_SCHEMA = pq.read_schema(DUMP)
PATCH_DTYPE = {c: DUMP_SCHEMA.field(c).type.to_pandas_dtype() for c in PATCH_COLS}

def build_block(fd_path: Path) -> pd.DataFrame:
    """40-col patch block from a fulldepth frame, indexed by test_id, cast to
    the DUMP's per-column dtypes (the representation of record)."""
    need = sorted(set(SWAP.values()) | {"cov_n_prior_full", "cov_history_years_fulldepth",
                                        "test_id"})
    fd = pd.read_parquet(fd_path, columns=need)
    host = pd.read_parquet(DUMP, columns=["test_id"])  # noqa: F841 target universe
    auxdates = pd.read_parquet(WORK / "fresh_2025/frame/fresh_frame_2025h1/aux.parquet",
                               columns=["test_id", "test_date"])
    fd = fd.merge(auxdates, on="test_id", how="left", validate="1:1")
    if fd["test_date"].isna().any():
        sys.exit(f"FATAL: {int(fd.test_date.isna().sum())} targets missing host test_date")
    blk = pd.DataFrame({"test_id": fd["test_id"]})
    for cc, fc in SWAP.items():
        blk[cc] = fd[fc].to_numpy()
    blk["has_prior_test_observed"] = (fd["cov_n_prior_full"] > 0).astype("float64").to_numpy()
    blk["history_years_observed"] = fd["cov_history_years_fulldepth"].to_numpy()
    blk["window_days_available"] = (pd.to_datetime(fd["test_date"]).dt.date
                                    - SOURCE_FLOOR).map(lambda d: float(d.days)).to_numpy()
    blk["has_left_truncated_history"] = 0.0
    blk["first_observed_test_is_not_true_first"] = 0.0
    for c in PATCH_COLS:  # THE fix: cast to the dump's exact dtype
        blk[c] = blk[c].astype(PATCH_DTYPE[c])
    return blk.set_index("test_id")

def load_models(tag):
    maps = json.loads((ART / f"fulldepth_{tag}_cat_encode_maps.json").read_text())
    mc = CatBoostClassifier(); mc.load_model(str(ART / f"fulldepth_{tag}_cat.cbm"))
    mx = XGBClassifier(); mx.load_model(str(ART / f"fulldepth_{tag}_xgb.ubj"))
    with open(ART / f"fulldepth_{tag}_hist.pkl", "rb") as fh:
        mh = pickle.load(fh)
    return maps, mc, mx, mh

def score_dump(tag, block, label, max_batches=None):
    """Score the dump, optionally patching PATCH_COLS from `block` (dtype-cast
    happens in build_block). Returns ids, p in dump row order."""
    maps, mc, mx, mh = load_models(tag)
    pf = pq.ParquetFile(DUMP)
    n_total = pf.metadata.num_rows
    ids_all, p_all = [], []
    n_gap = 0
    for bi, batch in enumerate(pf.iter_batches(batch_size=100_000, columns=COLS + ["test_id"])):
        if max_batches is not None and bi >= max_batches:
            break
        df = batch.to_pandas()
        bid = df["test_id"].to_numpy()
        if block is not None:
            j = block.reindex(bid)
            n_gap += int(j.isna().all(axis=1).sum())
            for c in PATCH_COLS:
                df[c] = j[c].to_numpy().astype(PATCH_DTYPE[c], copy=False)
        p = mc.predict_proba(df[COLS])[:, 1]
        for c in ALLCAT:
            df[c] = df[c].astype(str).map(maps[c]).fillna(-1).astype("float32")
        p = (p + mx.predict_proba(df[COLS])[:, 1] + mh.predict_proba(df[COLS])[:, 1]) / 3.0
        ids_all.append(bid); p_all.append(p)
        if (bi + 1) % 5 == 0:
            log(f"  {label}/{tag}: {min((bi+1)*100_000, n_total):,}/{n_total:,}")
        del df; gc.collect()
    if n_gap:
        sys.exit(f"FATAL: {n_gap} rows without block coverage ({label})")
    del mc, mx, mh; gc.collect()
    return np.concatenate(ids_all), np.concatenate(p_all)

def ece(y, p, bins=15):
    edges = np.linspace(0, 1, bins + 1)
    ix = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    return float(sum((ix == b).mean() * abs(p[ix == b].mean() - y[ix == b].mean())
                     for b in range(bins) if (ix == b).any()))

keyed0 = pd.read_parquet(ART / "fulldepth_seed0_keyed.parquet")[["test_id", "y", "p"]]
K0 = keyed0.set_index("test_id")

# G1: env tripwire — first batch unmodified must equal keyed exactly
log("G1: env tripwire (dump unmodified, first 100k)...")
ids, p = score_dump("seed0", None, "g1", max_batches=1)
d = np.abs(p - K0.reindex(ids)["p"].to_numpy())
if d.max() > 1e-12:
    sys.exit(f"G1 FAIL: max|dp|={d.max():.2e} on unmodified dump — env drift, STOP")
log("G1 PASS (max|dp|=0)")

# G2: patch machinery — banked FD block through the SAME patch path must
# reproduce keyed on the FULL frame
log("G2: patch-machinery validation (banked FD block, full frame)...")
banked_block = build_block(FD_BANKED)
ids, p = score_dump("seed0", banked_block, "g2")
d = np.abs(p - K0.reindex(ids)["p"].to_numpy())
log(f"G2: max|dp|={d.max():.3e} rows>1e-9: {int((d > 1e-9).sum())}")
if d.max() > 1e-9:
    sys.exit("G2 FAIL: banked block through patch path does not reproduce keyed — STOP")
log("G2 PASS — harness fully validated.")
del banked_block; gc.collect()

# S2: counterfactual FD frame under D13
log("S2: building counterfactual FD frame (D13 order, full h1)...")
TMP = SCRATCH / f".duckdb_tmp_f7b_{os.getpid()}"
TMP.mkdir(exist_ok=True)
con = duckdb.connect()
con.execute("SET memory_limit='2GB'"); con.execute("SET threads=2")
con.execute(f"SET temp_directory='{TMP}'")
con.execute("PRAGMA max_temp_directory_size='4GiB'")
reader = con.execute(f"""
SELECT p.*, tg.test_type AS p_test_type, tg.test_result AS p_lake_result
FROM read_parquet('{PACKETS}') p
LEFT JOIN read_parquet('{TEST_GRAIN}') tg ON p.p_test_id = tg.test_id
ORDER BY p.tgt_id
""")

rows_out, n_built, n_excl, n_typemiss = [], 0, 0, 0
exposed_flags = {}
_writer = None

def flush():
    global rows_out, _writer
    if not rows_out:
        return
    tbl = pa.Table.from_pylist(rows_out)
    fields = [pa.field(f.name, pa.float64()) if pa.types.is_null(f.type) else f
              for f in tbl.schema]
    tbl = tbl.cast(pa.schema(fields))
    if _writer is None:
        _writer = pq.ParquetWriter(CF_FRAME, tbl.schema)
    _writer.write_table(tbl.cast(_writer.schema))
    rows_out = []

def d13_key(r):
    return (r["p_date"], TYPE_RANK.get(r.get("p_test_type"), 3),
            OUTCOME_RANK.get(r.get("p_lake_result"), 0), r["p_test_id"])

t_s2 = time.time()
def build_target(rows):
    global n_built, n_excl, n_typemiss
    r0 = rows[0]
    pri = [r for r in rows if r["p_test_id"] is not None and not pd.isna(r["p_test_id"])]
    n_full = len(pri)
    if n_full > PATHOLOGY_BOUND:
        n_excl += 1
        pri = []
    n_typemiss += sum(1 for r in pri if r.get("p_test_type") is None)
    dates = [r["p_date"] for r in pri]
    exposed = len(dates) != len(set(dates))
    pri.sort(key=d13_key, reverse=True)   # reverse-D13: module stable-sorts date desc
    mots, item_covered = [], 0
    for r in pri:
        defects = []
        dj = r["defects_json"]
        if isinstance(dj, str) and dj.strip():
            item_covered += 1
            for dd in json.loads(dj):
                t = API_TYPE.get(dd.get("t"))
                if t is not None:
                    defects.append({"type": t, "text": dd.get("x") or "", "dangerous": False})
        tdt = pd.Timestamp(r["p_date"]).to_pydatetime()
        odo = r["p_miles"]
        mots.append(MOTTest(
            test_date=tdt, test_result=r["p_result"],
            expiry_date=(tdt + timedelta(days=365) if r["p_result"] == "PASSED" else None),
            odometer_value=None if odo is None or pd.isna(odo) else int(odo),
            odometer_unit="mi", test_number=str(r["p_test_id"]), defects=defects))
    fud = r0["tgt_fud"]
    fud_dt = None if fud is None or pd.isna(fud) else pd.Timestamp(fud).to_pydatetime()
    cc = r0["tgt_cc"]
    hist = VehicleHistory(
        registration="FULLDEPTH", make=str(r0["tgt_make"] or "UNKNOWN"),
        model=str(r0["tgt_model"] or "UNKNOWN"), fuel_type=str(r0["tgt_fuel"] or "PE"),
        colour="UNKNOWN", registration_date=fud_dt, manufacture_date=fud_dt,
        engine_size=None if cc is None or pd.isna(cc) else int(cc), mot_tests=mots)
    feats = model_v55.engineer_features_with_stats(
        hist, str(r0["tgt_pc"] or ""), pd.Timestamp(r0["tgt_date"]).to_pydatetime())
    first_prior = min((m.test_date for m in mots), default=None)
    rec = dict(feats)
    rec.update(
        test_id=int(r0["tgt_id"]),
        cov_n_prior_full=int(n_full), cov_n_prior_used=len(mots),
        cov_item_covered_priors=int(item_covered),
        cov_has_item_coverage=int(item_covered > 0),
        cov_history_years_fulldepth=(
            (pd.Timestamp(r0["tgt_date"]).to_pydatetime() - first_prior).days / 365.25
            if first_prior else 0.0),
        cov_excluded_pathology=int(n_full > PATHOLOGY_BOUND))
    rows_out.append(rec)
    exposed_flags[int(r0["tgt_id"])] = exposed
    n_built += 1
    if n_built % 100_000 == 0:
        flush()
        rate = n_built / max(time.time() - t_s2, 1)
        log(f"  S2: {n_built:,} built ({rate:.0f}/s, ETA {((1_048_500-n_built)/max(rate,1))/60:.0f}min)")

cur_id, cur = None, []
while True:
    batch = reader.fetch_df_chunk()
    if batch is None or len(batch) == 0:
        break
    for r in batch.to_dict("records"):
        if r["tgt_id"] != cur_id:
            if cur:
                build_target(cur)
            cur_id, cur = r["tgt_id"], [r]
        else:
            cur.append(r)
if cur:
    build_target(cur)
flush()
if _writer is not None:
    _writer.close()
con.close()
shutil.rmtree(TMP, ignore_errors=True)
log(f"S2 done: {n_built:,} targets (excl={n_excl}, prior rows missing test_type={n_typemiss:,})")

# G3 + S3: score counterfactual
log("S3: scoring counterfactual (seed0 + seed1)...")
cf_block = build_block(CF_FRAME)
if len(cf_block) != pq.ParquetFile(DUMP).metadata.num_rows:
    sys.exit(f"G3 FAIL: CF block {len(cf_block):,} targets != dump {pq.ParquetFile(DUMP).metadata.num_rows:,}")
log("G3 PASS: target sets align")
exposed = pd.Series(exposed_flags, name="exposed")
res = {"design": "F7b dump-based; D13 (7f3d8f1) vs banked p_test_id-DESC; module 38203e4; "
                 "float32 dump representation of record",
       "floor": FLOOR, "n_targets": int(n_built),
       "s2_prior_rows_missing_test_type": int(n_typemiss),
       "gates": {"G1": "PASS exact", "G2": "PASS (banked block reproduces keyed full-frame)",
                 "G3": "PASS"}}
per_seed = {}
for tag in ("seed0", "seed1"):
    kb = pd.read_parquet(ART / f"fulldepth_{tag}_keyed.parquet")[["test_id", "y", "p"]]
    ids_cf, p_cf = score_dump(tag, cf_block, "cf")
    dd = pd.DataFrame({"test_id": ids_cf, "p_cf": p_cf}).merge(kb, on="test_id", validate="1:1")
    dd = dd.join(exposed, on="test_id")
    dd["exposed"] = dd["exposed"].fillna(False).astype(bool)
    auc_b, auc_c = roc_auc_score(dd.y, dd.p), roc_auc_score(dd.y, dd.p_cf)
    sr = {"auc_banked": round(auc_b, 6), "auc_cf": round(auc_c, 6),
          "delta_auc_pooled": round(auc_c - auc_b, 6),
          "exceeds_floor": bool(abs(auc_c - auc_b) > FLOOR)}
    for grp, m in (("exposed", dd.exposed), ("unexposed", ~dd.exposed)):
        sr[f"delta_auc_{grp}"] = round(
            roc_auc_score(dd.y[m], dd.p_cf[m]) - roc_auc_score(dd.y[m], dd.p[m]), 6)
        sr[f"n_{grp}"] = int(m.sum())
    dp = np.abs(dd.p_cf - dd.p)
    sr["abs_dp"] = {"mean": round(float(dp.mean()), 6), "p50": round(float(np.quantile(dp, .5)), 6),
                    "p99": round(float(np.quantile(dp, .99)), 6), "max": round(float(dp.max()), 6),
                    "share_gt_1e-6": round(float((dp > 1e-6).mean()), 5)}
    sr["ece_raw_banked"] = round(ece(dd.y.to_numpy(), dd.p.to_numpy()), 6)
    sr["ece_raw_cf"] = round(ece(dd.y.to_numpy(), dd.p_cf.to_numpy()), 6)
    dec = np.quantile(dd.p, np.linspace(0, 1, 11))
    sr["decile_migration_share"] = round(
        float((np.clip(np.digitize(dd.p, dec[1:-1]), 0, 9)
               != np.clip(np.digitize(dd.p_cf, dec[1:-1]), 0, 9)).mean()), 5)
    per_seed[tag] = sr
    log(f"S3 {tag}: dAUC pooled {sr['delta_auc_pooled']:+.6f} "
        f"exposed {sr['delta_auc_exposed']:+.6f} | floor ±{FLOOR}")
res["per_seed"] = per_seed
res["exceeds_floor_any_seed"] = any(s["exceeds_floor"] for s in per_seed.values())
RESULT.write_text(json.dumps(res, indent=1, default=str))
log(f"RESULT: seed0 {per_seed['seed0']['delta_auc_pooled']:+.6f} "
    f"seed1 {per_seed['seed1']['delta_auc_pooled']:+.6f} "
    f"exceeds_floor={res['exceeds_floor_any_seed']} -> {RESULT}")
