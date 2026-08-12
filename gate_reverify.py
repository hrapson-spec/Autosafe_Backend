"""One-attempt continuity-gate re-verification (owner spec, 2026-08-12).

Default insertion order (the preserve-disabled pipeline is under a row-loss
cloud), isolated per-PID spill, hard 10GiB temp cap, memory 6GB on the idle
box. Emits: input row count, distinct-test_id duplicate check, the three
continuity metrics, verdict, and explicit comparison with the invalidated
2026-08-11 result. No retry on failure — a safe-completion failure is itself
the recorded result.
"""
import os, sys
sys.path.insert(0, "/Users/henrirapson/autosafe-v58")
from pathlib import Path
import duckdb
from pipeline.lake import checks as lake_checks
from pipeline.lake.manifest import LakeManifest

LAKE = Path("/Users/henrirapson/autosafe_lake")
PREV = "PASS multiyear_share=0.997 first_use_conflict_share=0.0088 median_gap_days=362.0 (2026-08-11, preserve-disabled session — INVALIDATED)"
con = duckdb.connect()
con.execute("SET memory_limit='6GB'")
con.execute("SET threads=2")
tmp = f"/Users/henrirapson/autosafe-v58/.tmp_gate_{os.getpid()}"
con.execute(f"SET temp_directory='{tmp}'")
con.execute("PRAGMA max_temp_directory_size='9GiB'")
rel = f"read_parquet('{(LAKE/'results'/'**'/'*.parquet').as_posix()}', hive_partitioning=true)"

n, nd = con.execute(f"SELECT count(*), count(DISTINCT test_id) FROM {rel}").fetchone()
print(f"GATE-REVERIFY input: rows={n:,} distinct_test_ids={nd:,} duplicates={n-nd:,}")

r = lake_checks.check_vehicle_continuity(con, rel)
status = "PASS" if r.passed else "FAIL"
print(f"GATE-REVERIFY verdict: {status} [{r.name}] {r.detail}")
print(f"GATE-REVERIFY previous: {PREV}")

manifest = LakeManifest.load(LAKE)
manifest.record_check(r.name, r.passed,
                      r.detail + " [re-verification, default insertion order, after row-loss hazard finding]")
manifest.save(LAKE)
Path("/Users/henrirapson/autosafe-v58/continuity_gate_fulldepth.txt").write_text(
    f"{status} [{r.name}] {r.detail}\n[re-verified 2026-08-12, default insertion order; "
    f"supersedes the invalidated 2026-08-11 preserve-disabled result]\n")
sys.exit(0 if r.passed else 1)
