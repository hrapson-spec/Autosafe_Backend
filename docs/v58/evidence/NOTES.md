# Local-agent execution notes (workstation phases)

Machine-truth record for the remote session. Started 2026-08-11.

## Environment record

- Worktree of `claude/autosave-defects-history-xqutcw` at head `0a279bc`
  (expected P0/P5a/P1/P2 stack verified). Python 3.11 venv; CV heavies
  (ultralytics/paddle*/opencv) skipped per handover §1. Sanity gate:
  **66/66 pipeline tests green** before any data work.
- Workstation: Apple M3, **8 GB RAM** (runbook prerequisite is 16 GB) —
  all lake tooling runs with `--memory-limit 4GB`; recorded as a deviation,
  not a gate change. Internal disk 251 GB with ~12 GB free at start; owner
  ruled out external drives — data phases run a staged architecture:
  per-year download → ingest → delete archive (DVSA is the archive;
  manifest sha chain makes any rebuild identity-provable), plus owner-approved
  offload of cold research artifacts to owner's Google Drive.

## Measured corpus sizing (see sizing_2026_08_11.md)

Entire current release is **26.22 GB compressed** (results 19.43, items 6.78,
lookup 0.25 MB). The runbook's 80–150 GB estimate matches the UNCOMPRESSED
total, which the staged design never materializes at once. Full-depth lake
projected ~31 GB at gz→parquet ratio 1.0 (ratio re-measured at first ingest).

## ⚠ ESCALATION — the release ends at full-year 2023

No 2024 or 2025 archives exist (CKAN catalogue, live data.gov.uk page, and
direct URL probes all agree, 2026-08-11). Consequences needing remote+owner
ruling — not decided locally:

1. **D3 window**: "latest 5 full calendar years" of this release =
   **2019-01-01 → 2023-12-31**, not the handover §4c's assumed 2021→2025.
2. **Phase-3 recency gap**: training data would end 2023-12-31 while serving
   scores histories through the present — a temporal variant of the very
   skew v58 exists to remove. The remote session should reason about this
   before the trainer design lands.

Local behavior meanwhile: §4b window discovery proceeds (it targets the OLD
artifact's window and is unaffected); the §4c fresh-window build waits for
the ruling.

## Deviations-with-rationale (execution level, model/data semantics untouched)

- E1 fail-fast continuity probes after ingesting {2005,2006} and {2016,2017}
  (the gz→zip format boundary): advisory reads of `multiyear_share` (the
  designed tripwire) to stop early on a per-file ID reset. The FORMAL
  full-depth `check --gate continuity` exit code remains the binding gate;
  early probes are advisory because the 200–800-day median-gap band is
  calibrated for full depth.
- **Cycles build executed vehicle-sharded (8-way), not monolithic** — measured
  necessity, not preference: a 7-year monolithic `build-cycles` probe ENOSPC'd
  its duckdb spill temp (>13 GiB demand; full depth ≈35 GiB vs ≤20 GiB free on
  this machine). The sharded path runs `pipeline.lake.cycles.build_cycles_sql`
  VERBATIM over `vehicle_id % 8` slices (cycles are per-vehicle independent,
  mirroring `assign_cycles`' by_vehicle structure). Equivalence falsifier:
  monolithic vs sharded on a `vehicle_id % 61 == 0` subsample (spreads across
  all shards) — 3,543,912 rows, `EXCEPT` both directions = 0
  (`sharded_cycles_VERDICT.txt`; tool `sharded_cycles.py` refuses to run
  without a PASS on record). Same SQL, same gap_days, same output schema and
  partitioning; only the execution plan differs.
- gz-era sources (2005–2016) are decompressed per-year before ingest because
  `run_lake._source_files` globs only `*.csv|*.txt`. duckdb's `read_csv`
  handles `.txt.gz` natively — the remote session may want to add that
  pattern to eliminate ~5 GB/yr transient expansion. Not changed locally
  (outside the §5 sanctioned tables).

## Amendment (2026-08-11 21:10, owner-approved): dynamic-ledger start for final six years

The calibration instrument's static formula (free >= remaining_lake + transient
+ floor, evaluated once) does not model mid-run space releases. With the last
offload delete (+2.2 GiB) in flight and chunk-at-a-time processing measured at
~2 GiB transient, the owner approved starting years 2018-2023 on the dynamic
ledger: first three years covered by present free space, back half by the
in-flight delete. Every floor guard and every proof gate (continuity, checks,
reconciliation) unchanged; worst case is a floor-guarded auto-resuming pause,
not a floor breach. Recorded openly as an amendment rather than silently
overriding the instrument's ABORT.

## Schedule variance ledger (exact, signed) — evidence-commit forecast

Original central forecast (17:40 audit): **20:30**.

Gross delays (+215 min):
- +91  orchestration rework, avoidable (unresumable-partial loop 19; one-shot
       waiter gap 11; parking saga — unmeasured premise, API-bound crawl,
       dead-end sequencers 35; rename-shim archive corruption + stale-marker
       loops 26)
- +38  necessary repairs tonight, preventable at pipeline design level
       (continuity gate sampling defect; 2018+ backslash-escape dialect stack
       — both would have blocked any full-depth run; a first-contact
       one-file probe per archive era would have surfaced them cheaply)
- +60  physics/external (evening uplink congestion ~35; Drive shared-client
       API pacer on small files ~25)
- +26  necessary science + durable scope (checkpoint probe/calibration/cohort,
       two sharding falsifiers, chunk-mode redesign, restore proof)

Recovered/overlapped (−33 min, previously netted invisibly):
- −8   marker-based skips + waiter recalibration made relaunches free
- −9   prefetch-warm zip years beat the cadence model (43-56s vs 3.5min)
- −13  cycles scans are 4-column columnar (~4-5GB) not whole-file — segment
       re-modelled 25-40 → 12-25 min
- −3   sharding falsifier pre-proven at N=16 (no re-proof at cycles time)

**20:30 + 215 − 33 ≈ 23:32 → current central 23:35 (range 23:20-23:55; residual
spread = shard timing + any §5 friction at the full gate).**
