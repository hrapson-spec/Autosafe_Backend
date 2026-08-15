#!/usr/bin/env python3
"""Append the BURDEN block to the experiment-local frame copies, part by part.

Row order is preserved exactly (pyarrow per part, never a SQL join) for the same reason
the relabelled frames were built that way. Appending is inert for the baseline arm: its
config lists its 241 columns explicitly, so extra columns in the frame are never selected.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, pyarrow as pa, pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent
COLS = ["bd_n_prior_initials","bd_md_last","bd_md_mean","bd_md_max","bd_md_sum",
        "bd_md_last3_mean","bd_md_trend","bd_sec_last","bd_sec_mean","bd_sec_max",
        "bd_sec_persistence","bd_sec_repeat_last2"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True); ap.add_argument("--burden", required=True)
    a = ap.parse_args()
    t = pq.ParquetFile(ROOT / a.burden).read()
    idx = {int(v): i for i, v in enumerate(np.asarray(t.column("tgt_id")))}
    src = {c: np.asarray(t.column(c)).astype(np.float64) for c in COLS}
    parts = sorted((ROOT / a.frames).glob("part_*.parquet"))
    n, miss = 0, 0
    for p in parts:
        tbl = pq.ParquetFile(p).read()   # not read_table: the path contains recipe=... which pyarrow would infer as a hive partition key and clash with the real `recipe` column
        if COLS[0] in tbl.schema.names:
            tbl = tbl.drop(COLS)                      # idempotent re-run
        rows = np.array([idx.get(int(v), -1) for v in np.asarray(tbl.column("tgt_id"))])
        miss += int((rows < 0).sum()); safe = np.where(rows < 0, 0, rows)
        for c in COLS:
            v = src[c][safe].copy(); v[rows < 0] = np.nan
            tbl = tbl.append_column(c, pa.array(v, type=pa.float64()))
        pq.write_table(tbl, p, compression="zstd"); n += tbl.num_rows
    print(json.dumps({"frames": a.frames, "parts": len(parts), "rows": n,
                      "targets_without_burden_row": miss, "appended": COLS}, indent=1))
    return 2 if miss else 0

if __name__ == "__main__":
    raise SystemExit(main())
