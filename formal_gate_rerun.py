"""Formal full-depth continuity gate re-run (2026-08-11 ~22:55).

The runner's invocation crashed on spill-disk exhaustion (12.3GiB temp cap,
the whole free disk) before rendering ANY verdict. This re-runs the pipeline's
own checks.check_vehicle_continuity through the pipeline's own manifest
recording, with duckdb session tuning only (its own recommended remedies):
memory_limit 5GB, threads 2, preserve_insertion_order=false (pure read —
the known COPY ordering trap does not apply). Check semantics untouched.
"""
import sys
sys.path.insert(0, "/Users/henrirapson/autosafe-v58")
from pathlib import Path
import duckdb
from pipeline.lake import checks as lake_checks
from pipeline.lake.manifest import LakeManifest

LAKE = Path("/Users/henrirapson/autosafe_lake")
con = duckdb.connect()
con.execute("SET memory_limit='5GB'")
con.execute("SET threads=2")
con.execute("SET preserve_insertion_order=false")
con.execute("SET temp_directory='/Users/henrirapson/autosafe-v58/.tmp'")
rel = f"read_parquet('{(LAKE/'results'/'**'/'*.parquet').as_posix()}', hive_partitioning=true)"
r = lake_checks.check_vehicle_continuity(con, rel)
manifest = LakeManifest.load(LAKE)
manifest.record_check(r.name, r.passed, r.detail + " [rerun: tuned session after spill-disk crash; check code identical]")
manifest.save(LAKE)
status = "PASS" if r.passed else "FAIL"
line = f"{status} [{r.name}] {r.detail}"
print(line)
Path("/Users/henrirapson/autosafe-v58/continuity_gate_fulldepth.txt").write_text(line + "\n")
sys.exit(0 if r.passed else 1)
