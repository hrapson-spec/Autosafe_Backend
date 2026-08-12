#!/usr/bin/env python3
"""Memory-bounded streaming cycles builder (feasibility prototype, 2026-08-12).

Reformulation: instead of DuckDB's window-operator execution of
build_cycles_sql (measured ~100-160GiB spill footprint at full depth), each
vehicle shard is ORDER BY-sorted by DuckDB (spill-cheap: 4 columns, no window
operators) and streamed through the pipeline's OWN pure-Python rule of record
assign_cycles(), one vehicle at a time (O(single-vehicle) memory), writing
hive-partitioned zstd parquet identical in schema to the SQL twin's output.

Zero reimplementation drift: the actual library assign_cycles() is called
per-vehicle (it is exact for any row subset that contains whole vehicles).

Falsifier: subsample vehicle_id%61==0, SQL twin vs streaming (4 shards),
row counts equal + EXCEPT both directions == 0.
"""
import argparse, os, resource, sys, threading, time
from pathlib import Path

sys.path.insert(0, "/Users/henrirapson/autosafe-v58")
from pipeline.lake.cycles import DEFAULT_CYCLE_GAP_DAYS, assign_cycles, build_cycles_sql  # noqa: E402

import duckdb  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

WT = Path("/Users/henrirapson/autosafe-v58")
TMP = WT / f".tmp_stream_{os.getpid()}"

SCHEMA = pa.schema([
    ("test_id", pa.int64()), ("vehicle_id", pa.int64()), ("cycle_id", pa.int64()),
    ("is_cycle_first", pa.bool_()), ("cycle_outcome", pa.string()),
    ("prev_cycle_test_id", pa.int64()), ("prev_cycle_outcome", pa.string()),
    ("days_since_prev_cycle", pa.int64()),
])


def connect(mem: str = "2GB"):
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{mem}'")
    con.execute("SET threads=2")
    # preserve_insertion_order left at DEFAULT: =false proven to silently drop
    # rows from window-pipeline COPYs (2.8% loss, nondeterministic, 2026-08-12)
    con.execute(f"SET temp_directory='{TMP.as_posix()}'")
    con.execute("PRAGMA max_temp_directory_size='6GiB'")
    return con


def rel(lake: Path) -> str:
    return f"read_parquet('{(lake/'results'/'**'/'*.parquet').as_posix()}', hive_partitioning=true)"


class SpillSampler:
    def __init__(self):
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                s = sum(f.stat().st_size for f in TMP.rglob("*") if f.is_file())
                self.peak = max(self.peak, s)
            except OSError:
                pass
            time.sleep(3)

    def __enter__(self):
        TMP.mkdir(exist_ok=True)
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()


class YearWriter:
    """Accumulates cycle rows per test_year; flushes zstd parquet files into
    hive dirs (partition column carried by the directory, not the file —
    matching COPY ... PARTITION_BY output shape)."""

    def __init__(self, out_dir: Path, tag: str, flush_rows: int = 1_000_000):
        self.out, self.tag, self.flush_rows = out_dir, tag, flush_rows
        self.buf = {}   # year -> list of row tuples
        self.seq = {}

    def add(self, year: int, row: tuple):
        b = self.buf.setdefault(year, [])
        b.append(row)
        if len(b) >= self.flush_rows:
            self.flush_year(year)

    def flush_year(self, year: int):
        rows = self.buf.pop(year, [])
        if not rows:
            return
        cols = list(zip(*rows))
        table = pa.table({f.name: pa.array(cols[i], type=f.type)
                          for i, f in enumerate(SCHEMA)}, schema=SCHEMA)
        d = self.out / f"test_year={year}"
        d.mkdir(parents=True, exist_ok=True)
        n = self.seq[year] = self.seq.get(year, 0) + 1
        pq.write_table(table, d / f"{self.tag}_{n}.parquet", compression="zstd")

    def close(self):
        for y in list(self.buf):
            self.flush_year(y)


def stream_shard(lake: Path, out_dir: Path, shards: int, k: int,
                 gap: int, extra_filter: str = "") -> int:
    con = connect()
    q = (f"SELECT test_id, vehicle_id, test_date, outcome FROM {rel(lake)} "
         f"WHERE vehicle_id % {shards} = {k} {extra_filter} "
         f"ORDER BY vehicle_id, test_date, test_id")
    reader = con.execute(q).fetch_record_batch(100_000)
    writer = YearWriter(out_dir, f"stream{k}")
    total = 0
    cur_vid, buf, years = None, [], {}

    def emit():
        nonlocal total
        if not buf:
            return
        for r in assign_cycles(buf, gap):
            writer.add(years[r.test_id], (
                r.test_id, r.vehicle_id, r.cycle_id, r.is_cycle_first,
                r.cycle_outcome, r.prev_cycle_test_id, r.prev_cycle_outcome,
                r.days_since_prev_cycle))
            total += 1

    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        tids = batch.column(0).to_pylist()
        vids = batch.column(1).to_pylist()
        dates = batch.column(2).to_pylist()
        outs = batch.column(3).to_pylist()
        for t, v, d, o in zip(tids, vids, dates, outs):
            if v != cur_vid:
                emit()
                buf, years = [], {}
                cur_vid = v
            buf.append(dict(test_id=t, vehicle_id=v, test_date=d, outcome=o))
            years[t] = d.year
    emit()
    writer.close()
    con.close()
    return total


def falsify(lake: Path, scratch: Path, gap: int) -> int:
    scratch.mkdir(parents=True, exist_ok=True)
    mono, stream = scratch / "mono", scratch / "stream"
    sub = f"(SELECT * FROM {rel(lake)} WHERE vehicle_id % 61 = 0)"
    t0 = time.monotonic()
    con = connect()
    con.execute(f"COPY ({build_cycles_sql(sub, gap)}) TO '{mono.as_posix()}' "
                f"(FORMAT parquet, COMPRESSION zstd, PARTITION_BY (test_year), OVERWRITE_OR_IGNORE true)")
    con.close()
    t_sql = time.monotonic() - t0

    t0 = time.monotonic()
    total = 0
    with SpillSampler() as sampler:
        for k in range(4):
            total += stream_shard(lake, stream, 4, k, gap,
                                  extra_filter="AND vehicle_id % 61 = 0")
    t_stream = time.monotonic() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30

    con = connect()
    a = f"read_parquet('{mono.as_posix()}/**/*.parquet', hive_partitioning=true)"
    b = f"read_parquet('{stream.as_posix()}/**/*.parquet', hive_partitioning=true)"
    na = con.execute(f"SELECT count(*) FROM {a}").fetchone()[0]
    nb = con.execute(f"SELECT count(*) FROM {b}").fetchone()[0]
    d1 = con.execute(f"SELECT count(*) FROM (SELECT * FROM {a} EXCEPT SELECT * FROM {b})").fetchone()[0]
    d2 = con.execute(f"SELECT count(*) FROM (SELECT * FROM {b} EXCEPT SELECT * FROM {a})").fetchone()[0]
    ok = na == nb and na > 0 and d1 == 0 and d2 == 0
    print(f"STREAM-CYCLES FALSIFIER: {'PASS' if ok else 'FAIL'} rows_sql={na} rows_stream={nb} "
          f"except_ab={d1} except_ba={d2} shards=4 gap_days={gap} subsample=vehicle_id%61==0")
    print(f"STREAM-CYCLES MEASURE: sql_twin={t_sql:.0f}s stream={t_stream:.0f}s "
          f"rows/s={total/max(t_stream,1):.0f} peak_rss={rss:.2f}GiB "
          f"peak_spill={sampler.peak/2**30:.2f}GiB")
    return 0 if ok else 1


def sort_probe(lake: Path, shards: int, k: int) -> int:
    """Full-size shard sort feasibility: ORDER BY fetch-and-discard under the
    6GiB spill cap. Dying on the cap is a result, not an error."""
    con = connect()
    t0 = time.monotonic()
    n = 0
    with SpillSampler() as sampler:
        reader = con.execute(
            f"SELECT test_id, vehicle_id, test_date, outcome FROM {rel(lake)} "
            f"WHERE vehicle_id % {shards} = {k} "
            f"ORDER BY vehicle_id, test_date, test_id").fetch_record_batch(1_000_000)
        while True:
            try:
                n += reader.read_next_batch().num_rows
            except StopIteration:
                break
    print(f"STREAM-CYCLES SORT-PROBE: shard {k}/{shards} rows={n:,} "
          f"time={time.monotonic()-t0:.0f}s peak_spill={sampler.peak/2**30:.2f}GiB (cap 6GiB)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--lake-dir", required=True)
    p.add_argument("--out-dir")
    p.add_argument("--shards", type=int, default=8)
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--gap-days", type=int, default=DEFAULT_CYCLE_GAP_DAYS)
    p.add_argument("--falsify", action="store_true")
    p.add_argument("--sort-probe", action="store_true")
    p.add_argument("--scratch", default="/private/tmp/claude-501/-Users-henrirapson/a0b9f25c-859c-468c-93d5-a30b1ada1377/scratchpad/streamtest")
    a = p.parse_args()
    lake = Path(a.lake_dir)
    if a.falsify:
        sys.exit(falsify(lake, Path(a.scratch), a.gap_days))
    if a.sort_probe:
        sys.exit(sort_probe(lake, a.shards, a.shard))
    if not a.out_dir:
        p.error("--out-dir required for a build shard")
    n = stream_shard(lake, Path(a.out_dir), a.shards, a.shard, a.gap_days)
    print(f"STREAM-CYCLES shard {a.shard}/{a.shards}: {n:,} cycle rows written")
