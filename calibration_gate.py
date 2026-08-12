#!/usr/bin/env python3
"""Pre-registered space calibration gate (runs after 2006 and 2017 ingests).

Projects the final results-lake size from bytes actually written so far,
scaled in COMPRESSED-source space (gz sizes from NUMBERS_raw.json), and
aborts the run while stopping is still cheap if the projection breaches
the disk floor. Prints one machine-greppable verdict line.
"""
import json, re, shutil, sys
from pathlib import Path

LAKE = Path(sys.argv[1])
NUMBERS = Path(sys.argv[2])
TRANSIENT_GIB = 2.0   # zip-era chunk-at-a-time (2026-08-11): zip + ONE member, not all 12
FLOOR_GIB = 10.0

gib = 1024 ** 3
res = json.loads(NUMBERS.read_text())
gz_by_year = {}
for r in res:
    m = re.search(r"results \((20\d\d)\)", r["name"])
    if m and r["bytes"] > 0:
        gz_by_year[int(m.group(1))] = r["bytes"]
total_gz = sum(gz_by_year.values())

manifest = json.loads((LAKE / "lake_manifest.json").read_text())
ingested_years = set()
for s in manifest["sources"]:
    m = re.search(r"test_result[_ ]?(20\d\d)", s["path"])
    if m and s.get("rows_ingested", 0) > 0:
        ingested_years.add(int(m.group(1)))
ingested_gz = sum(gz_by_year.get(y, 0) for y in ingested_years)

lake_bytes = sum(f.stat().st_size for f in (LAKE / "results").rglob("*.parquet"))
free_gib = shutil.disk_usage("/System/Volumes/Data").free / gib

if not ingested_gz:
    print(f"CALIBRATION SKIP: no ingested years matched ({sorted(ingested_years)})")
    sys.exit(0)

projected_total = lake_bytes * (total_gz / ingested_gz)
additional_gib = (projected_total - lake_bytes) / gib
ratio_vs_gz = lake_bytes / ingested_gz
headroom = free_gib - additional_gib - TRANSIENT_GIB - FLOOR_GIB

line = (f"years={sorted(ingested_years)} lake={lake_bytes/gib:.2f}GiB "
        f"ratio_vs_gz={ratio_vs_gz:.3f} projected_total={projected_total/gib:.2f}GiB "
        f"additional={additional_gib:.2f}GiB free={free_gib:.2f}GiB "
        f"headroom_after={headroom:.2f}GiB")
if headroom >= 0:
    print(f"CALIBRATION OK: {line}")
    sys.exit(0)
print(f"CALIBRATION ABORT: {line} — projected completion breaches the {FLOOR_GIB:.0f}GiB floor; "
      f"options: owner-approved cleanup, rung-B parking after full ingest, or re-scope")
sys.exit(1)
