#!/usr/bin/env python3
"""Vehicle-sharded execution of the pipeline's OWN cycles SQL.

Motivation (measured, 2026-08-11): monolithic build-cycles ENOSPC'd its spill
temp on just 7 ingested years (>13 GiB demand); 19 years cannot fit this
machine. Cycles are per-vehicle independent (see assign_cycles' by_vehicle
structure in pipeline/lake/cycles.py), so running build_cycles_sql verbatim
over vehicle_id-modulo slices and unioning the partition outputs is
mathematically identical while dividing spill by the shard count.

--falsify: mandatory equivalence proof on a ~1/61 vehicle subsample
(prime modulus so the subsample spreads across all 8 shards): monolithic
vs sharded must match row-for-row (EXCEPT both ways = 0). Writes
sharded_cycles_VERDICT.txt; the full mode refuses to run without a PASS.
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, "/Users/henrirapson/autosafe-v58")
from pipeline.lake.cycles import DEFAULT_CYCLE_GAP_DAYS, build_cycles_sql  # noqa: E402

import duckdb  # noqa: E402

WT = Path("/Users/henrirapson/autosafe-v58")
VERDICT = WT / "sharded_cycles_VERDICT.txt"


def rel(lake: Path) -> str:
    return f"read_parquet('{(lake/'results'/'**'/'*.parquet').as_posix()}', hive_partitioning=true)"


def connect(mem: str):
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{mem}'")
    con.execute(f"SET temp_directory='{(WT/'.tmp').as_posix()}'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")  # no ORDER BY in the COPY;
    # falsifier compares via EXCEPT (set semantics) — row order is execution detail.
    # Without this, per-shard spill drags full-scan ordering state and ENOSPCs.
    return con


def falsify(lake: Path, mem: str, scratch: Path, shards: int, gap: int) -> int:
    scratch.mkdir(parents=True, exist_ok=True)
    sub = f"(SELECT * FROM {rel(lake)} WHERE vehicle_id % 61 = 0)"
    con = connect(mem)
    mono_dir, shard_dir = scratch / "mono", scratch / "sharded"
    con.execute(f"COPY ({build_cycles_sql(sub, gap)}) TO '{mono_dir.as_posix()}' "
                f"(FORMAT parquet, COMPRESSION zstd, PARTITION_BY (test_year), OVERWRITE_OR_IGNORE true)")
    for k in range(shards):
        piece = f"(SELECT * FROM {sub} s WHERE s.vehicle_id % {shards} = {k})"
        con.execute(f"COPY ({build_cycles_sql(piece, gap)}) TO '{shard_dir.as_posix()}' "
                    f"(FORMAT parquet, COMPRESSION zstd, PARTITION_BY (test_year), "
                    f"OVERWRITE_OR_IGNORE true, FILENAME_PATTERN 'shard{k}_{{i}}')")
    a = f"read_parquet('{mono_dir.as_posix()}/**/*.parquet', hive_partitioning=true)"
    b = f"read_parquet('{shard_dir.as_posix()}/**/*.parquet', hive_partitioning=true)"
    na = con.execute(f"SELECT count(*) FROM {a}").fetchone()[0]
    nb = con.execute(f"SELECT count(*) FROM {b}").fetchone()[0]
    d1 = con.execute(f"SELECT count(*) FROM (SELECT * FROM {a} EXCEPT SELECT * FROM {b})").fetchone()[0]
    d2 = con.execute(f"SELECT count(*) FROM (SELECT * FROM {b} EXCEPT SELECT * FROM {a})").fetchone()[0]
    ok = na == nb and na > 0 and d1 == 0 and d2 == 0
    line = (f"{'PASS' if ok else 'FAIL'} rows_mono={na} rows_sharded={nb} "
            f"except_ab={d1} except_ba={d2} shards={shards} gap_days={gap} subsample=vehicle_id%61==0")
    VERDICT.write_text(line + "\n")
    print(f"SHARDED-CYCLES FALSIFIER: {line}")
    return 0 if ok else 1


def auto_shards() -> int:
    """Pick N from live disk budget: extrapolated total spill ~38GiB (7-year
    monolithic probe ENOSPC'd >13GiB; x2.84 row scaling), per-shard budget =
    free - 10 floor - 2 headroom. Fewer shards = fewer full results scans."""
    import shutil
    free = shutil.disk_usage("/System/Volumes/Data").free / 1024**3
    budget = max(3.0, free - 12.0)
    n = max(4, min(12, -(-38 // int(budget))))  # ceil
    print(f"SHARDED-CYCLES auto-N: free={free:.1f}GiB budget/shard={budget:.1f}GiB -> shards={n}")
    return int(n)


def full(lake: Path, mem: str, shards: int, gap: int) -> int:
    if not (VERDICT.exists() and VERDICT.read_text().startswith("PASS")):
        print("SHARDED-CYCLES REFUSED: no PASS verdict on record — run --falsify first")
        return 2
    vt = VERDICT.read_text()
    import re as _re, shutil as _shu
    vn = int(_re.search(r"shards=(\d+)", vt).group(1)) if _re.search(r"shards=(\d+)", vt) else 0
    budget = max(3.0, _shu.disk_usage("/System/Volumes/Data").free / 1024**3 - 12.0)
    if vn and vn != shards:
        # Re-proof costs ~3min flat; each extra shard costs a full results scan
        # (~1.5-2.5min). Prefer the proven verdict-N unless the requested N is
        # >=4 shards smaller AND its spill fits the live budget.
        if 38.0 / shards <= budget and shards <= vn - 4:
            print(f"SHARDED-CYCLES: re-proving at N={shards} (saves {vn - shards} scans vs verdict N={vn})")
        elif 38.0 / vn <= budget:
            print(f"SHARDED-CYCLES: using proven verdict N={vn} (fits budget, no re-proof)")
            shards = vn
    if f"shards={shards}" not in vt:
        print(f"SHARDED-CYCLES: verdict was for different N — re-proving equivalence at shards={shards}")
        scratch = Path("/private/tmp/claude-501/-Users-henrirapson/a0b9f25c-859c-468c-93d5-a30b1ada1377/scratchpad/shardtest")
        import shutil as _sh
        _sh.rmtree(scratch, ignore_errors=True)
        if falsify(lake, "2GB", scratch, shards, gap) != 0:
            print("SHARDED-CYCLES REFUSED: re-proof FAILED")
            return 2
    out = lake / "cycles"
    out.mkdir(parents=True, exist_ok=True)
    import threading, time, shutil as sh
    for k in range(shards):
        for stale in out.rglob(f"shard{k}_*"):   # crash-retry hygiene: no stale duplicates
            stale.unlink()
        peak = [0]
        stop = threading.Event()
        def sample():
            tmp = WT / ".tmp"
            while not stop.is_set():
                try:
                    peak[0] = max(peak[0], sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file()))
                except OSError:
                    pass
                time.sleep(5)
        t = threading.Thread(target=sample, daemon=True); t.start()
        t0 = time.monotonic()
        piece = f"(SELECT * FROM {rel(lake)} s WHERE s.vehicle_id % {shards} = {k})"
        con = connect(mem)  # fresh connection per shard: temp fully released between shards
        con.execute(f"COPY ({build_cycles_sql(piece, gap)}) TO '{out.as_posix()}' "
                    f"(FORMAT parquet, COMPRESSION zstd, PARTITION_BY (test_year), "
                    f"OVERWRITE_OR_IGNORE true, FILENAME_PATTERN 'shard{k}_{{i}}')")
        con.close()
        stop.set()
        print(f"SHARDED-CYCLES shard {k+1}/{shards} written in {time.monotonic()-t0:.0f}s "
              f"peak_temp={peak[0]/2**30:.2f}GiB", flush=True)
    con = connect(mem)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}/**/*.parquet', "
                    f"hive_partitioning=true)").fetchone()[0]
    print(f"SHARDED-CYCLES COMPLETE: {n} cycle rows across {shards} shards (gap_days={gap})")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--lake-dir", required=True)
    p.add_argument("--memory-limit", default="3GB")
    p.add_argument("--shards", type=int, default=0, help="0 = auto from live disk budget")
    p.add_argument("--gap-days", type=int, default=DEFAULT_CYCLE_GAP_DAYS)
    p.add_argument("--falsify", action="store_true")
    p.add_argument("--scratch", default="/private/tmp/claude-501/-Users-henrirapson/a0b9f25c-859c-468c-93d5-a30b1ada1377/scratchpad/shardtest")
    a = p.parse_args()
    n = a.shards if a.shards > 0 else auto_shards()
    fn = falsify(Path(a.lake_dir), a.memory_limit, Path(a.scratch), n, a.gap_days) \
        if a.falsify else full(Path(a.lake_dir), a.memory_limit, n, a.gap_days)
    sys.exit(fn)
