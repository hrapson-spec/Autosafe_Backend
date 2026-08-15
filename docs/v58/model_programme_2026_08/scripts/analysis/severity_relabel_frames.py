#!/usr/bin/env python3
"""Stage 2 -- write experiment-local frame copies carrying y_b3 / y_m1.

Per PREREG_SEVERITY_STAGE2_2026_08_15.md. The canonical frames are NEVER modified.

Row order is preserved EXACTLY, part by part, because CatBoost's Bernoulli subsampling
is order-sensitive: a reordered copy would be a second change alongside the target and
would break the "only the target changed" claim. This is why the copy is done with
pyarrow per part rather than a SQL join (duckdb runs with preserve_insertion_order=false
across the programme, which does not guarantee row order).

Existing columns are passed through untouched; two BOOLEAN columns are appended.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent

LABEL_DEFS = {
    "y_b3": ("n_major_or_dangerous", 3),   # >=3 major/dangerous failing items
    "y_m1": ("n_sections_with_md", 2),     # >=2 distinct DVSA sections with M/D
}


def load_labels(path: Path) -> dict:
    t = pq.read_table(path, columns=["test_id", "n_major_or_dangerous",
                                     "n_sections_with_md"])
    ids = np.asarray(t.column("test_id"))
    cols = {n: np.asarray(t.column(n)) for n in
            ("n_major_or_dangerous", "n_sections_with_md")}
    return {int(i): j for j, i in enumerate(ids)}, cols


def relabel(src_glob: str, labels_path: Path, dst_dir: Path) -> dict:
    index, cols = load_labels(labels_path)
    parts = sorted(Path().glob(src_glob)) or sorted(
        (ROOT).glob(src_glob))
    if not parts:
        raise SystemExit(f"no parts matched {src_glob!r}")
    dst_dir.mkdir(parents=True, exist_ok=True)

    total, missing, pos = 0, 0, {k: 0 for k in LABEL_DEFS}
    for p in parts:
        tbl = pq.read_table(p)
        tgt = np.asarray(tbl.column("tgt_id"))
        rows = np.array([index.get(int(v), -1) for v in tgt])
        missing += int((rows < 0).sum())
        safe = np.where(rows < 0, 0, rows)
        for name, (col, thresh) in LABEL_DEFS.items():
            vals = cols[col][safe] >= thresh
            vals = np.where(rows < 0, False, vals)
            tbl = tbl.append_column(name, pa.array(vals, type=pa.bool_()))
            pos[name] += int(vals.sum())
        out = dst_dir / p.name
        pq.write_table(tbl, out, compression="zstd")
        total += tbl.num_rows

    return {"src": src_glob, "dst": str(dst_dir), "parts": len(parts),
            "rows": total, "targets_without_label": missing,
            "positives": pos}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="glob of source frame parts")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    rep = relabel(a.src, ROOT / a.labels, ROOT / a.dst)
    if rep["targets_without_label"]:
        print(f"FAIL: {rep['targets_without_label']} target rows had no label row")
        return 2
    rep["part_sha256"] = {p.name: sha256(p)
                          for p in sorted((ROOT / a.dst).glob("*.parquet"))}
    text = json.dumps(rep, indent=1, default=str)
    if a.report:
        (ROOT / a.report).write_text(text)
    print(json.dumps({k: v for k, v in rep.items() if k != "part_sha256"},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
