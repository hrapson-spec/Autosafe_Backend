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

## D7 STATUS: OPEN / DEFERRED — MUST PASS BEFORE PHASE-3 TARGET DEFINITION IS FROZEN

Owner ruling 2026-08-12: reconciliation is a hard PRE-TRAINING gate, not a
local-completion gate. Preference: run it LOCALLY at trainer-design time
(streaming implementation below makes that plausible); remote hardware only
if genuinely necessary. When run, compute the old-artifact comparison under
BOTH cycle semantics (canonical assign_cycles AND the legacy pre-repair SQL
semantics) — the ~6.10%% one-directional FAIL->PASS/PRS divergence measured
below fingerprints which semantics production's 26.9139638817903%% reflects.

## Defect #16 (2026-08-12): SQL twin mislabelled rectified cycles — REPAIRED

DVSA test_ids are not chronological. build_cycles_sql picked cycle_id /
outcome_test_id / last_test_id by min/max(test_id); assign_cycles (rule of
record) defines them by (test_date, test_id) position. Measured on the 1/61
subsample (10,498,887 cycle rows): 640,786 rows (6.10%%) differed, 100%%
one-directional FAIL->PASS (636,916) and FAIL->PRS (3,870) — the legacy twin
systematically labelled rectified retest chains as failures. Repaired with
arg_min/arg_max over row(test_date, test_id) (commit 5996f53); repaired twin
vs a streaming per-vehicle execution of the actual assign_cycles library is
bit-identical (EXCEPT 0/0). Discriminating fixture added (fixtures with
monotone ids could never catch this). assign_cycles itself untouched.

## Row-loss hazard (provisional classification — NOT yet a proven DuckDB defect)

A 2.8%% silent output-row loss was observed in a window-pipeline COPY
(build_cycles_sql shape) under: DuckDB 1.5.5, memory_limit 2GB, threads 2,
preserve_insertion_order=false, temp cap 6GiB, concurrent duckdb load.
The identical query with default insertion order produced the correct count.
Loss is nondeterministic (other preserve-disabled runs lost nothing).
Classified as: ROW-LOSS ASSOCIATED WITH THE PRESERVE-DISABLED PIPELINE.
Setting purged from all load-bearing tooling; a minimal reproducer
(version/build, query shape, threads, settings, repeated counts, default-
ordering and explicit-ordering contrasts) is queued after mission-critical
work.

## Continuity gate PASS: INVALIDATED PENDING RE-VERIFICATION

The 2026-08-11 formal PASS (0.997 / 0.0088 / 362d) was produced by a session
using preserve_insertion_order=false. Given the row-loss association above
and the 0.0088-vs-0.01 margin on conflict share, that PASS is not sufficient
evidence. One-attempt re-verification with default ordering, isolated spill,
hard temp cap, input/output row counts and duplicate checks is queued behind
items completion; if it cannot complete safely on this machine, that outcome
is recorded rather than retried.

## Defect #17 (2026-08-12): concurrent DuckDB processes shared a spill dir

DuckDB temp filenames are not process-unique; two processes sharing
temp_directory corrupted one's spill mid-read. INVARIANT: every concurrent
DuckDB process gets its own spill directory (per-PID), with hard
max_temp_directory_size caps and one-attempt execution.

## Defect #18 (2026-08-12, found by the parallel test-type session): same-day
## fail->retest mis-ordering — IN BOTH IMPLEMENTATIONS — decision pending

The shared ordering key (test_date, test_id) mis-orders same-day NT-fail ->
RT-retest pairs whenever the retest drew a smaller test_id: the retest sorts
BEFORE its failure, LAG sees no prior FAIL, and the retest opens its own
cycle. Peer-measured (2019 C3&4): 99.1%% of no-prior RT rows have a same-day
higher-id NT; effect on D7 cycle-first basis: denominator +4.55%% of
near-certain passes (RT pass rate 99.6%%), rate depressed 1.26pp. Same error
class and direction as Defect #16. Candidate repair: within-day tiebreak
ORDER BY test_date, (NT before RT via test_type), test_id — test_type is
populated on every lake row (peer census: 0 nulls, NT/RT/ES + EI-2023-only).
NOT REPAIRED: this changes assign_cycles' own semantics (the rule of record),
i.e. the D7 target definition — owner has routed all such decisions to the
D7/trainer-design review. The eventual dual-semantics D7 run is now
THREE-way: legacy-SQL, repaired-canonical, and same-day-repaired.

## Sampling/sharding bias (2026-08-12, peer-found, independently verified)

vehicle_id is not uniform over residues: %%20==0 is oversized (1.87M vs
1.50M expected) and fail-heavy (+7-8pp vs truth); abs(hash(vehicle_id))%%N
matches population truth exactly (verified on 2019 C3&4: hash slice
24.93%% F-only vs 24.93%% population). Consequences recorded: (a) all future
sampling/sharding uses hash residues (stream_cycles patched); (b) shard-size
imbalance from raw modulo plausibly contributed to per-shard ENOSPC; (c) the
twin-equivalence falsifier verdicts on vehicle_id%%61==0 remain valid AS
EQUIVALENCE evidence (bit-identical EXCEPT on 10.5M rows tests semantic
agreement — a row-level EXCEPT disagreement on any slice is a real
disagreement); they are NOT rate-representative. CORRECTION 08-12: an
earlier draft called the slice "retest-rich/adversarial" — measured FALSE
(RT share 18.385% vs 18.380% hash baseline; 3+-test-vehicle rows 3.89% vs
3.86%). The slice is unremarkable; the verdicts stand on the EXCEPT logic
alone. Hash-sample re-run queued as belt-and-braces.

## D7 fingerprint is FOUR-armed: legacy gap was 120 days, not 45

Verified verbatim (autosafe-icloud-dev/build_cycle_index_duckdb.py:18):
legacy CYCLE_GAP_DAYS = 120; the v58 pipeline pins gap_days = 45. The
eventual D7 reconciliation must fingerprint production's 26.9139638817903%
against: (1) legacy-SQL semantics, (2) repaired-canonical, (3)
same-day-repaired (NT-before-RT tiebreak), (4) gap=120 variants as needed.
Peer-measured reference points (2019 C3&4, unbiased 1/20 hash sample,
failure = cycle-first row outcome FAIL): as-implemented 23.670% (denom
1,573,060); same-day-repaired 24.831% (1,498,298); DVSA final basis NT/F
24.934% (1,504,549) — same-day-repaired sits 0.10pp from final basis, the
unrepaired arm 1.26pp below.

## Defect #19 (2026-08-12, found by the invariants-audit session): 2022
## results year silently skipped — REPAIRED

The 2021 zip carries a test_result_2022/ spillover subdirectory; its manifest
paths satisfied the runner's substring year-skip, so year 2022 was never
extracted (zip sat fully downloaded) and the lake held 18 years while being
reported as complete. Repaired: substring fallback removed (markers are the
sole skip authority), 2022 ingested 08:18 (157s, chunked). CONSEQUENCE: the
invalidated 2026-08-11 continuity PASS was additionally computed on an
INCOMPLETE (18-year) lake; the queued re-verification is the first gate run
over the true 19 years.

## Near-miss (2026-08-12 08:2X, flagged live by the d7-cycles session): the
## stage-3 runner's INLINE formal-gate step — uncapped spill in shared .tmp —
## re-fired after the 2022 repair and drove free space to 1.4GiB before being
## stopped. The items runner fail-closed at its floor exactly as designed
## (zero corruption). The inline gate step is SUPERSEDED by gate_reverify.py
## (isolated per-PID temp, hard cap, one attempt); its exit lines in runner
## logs after 08:15 are non-authoritative. Root class: the temp-isolation
## sweep missed the runner's inline gate (credit: invariants session's
## wait_idle/self-exclusion observation).

## 2022 backfill EXTERNALLY VALIDATED + a correction of my own record

The d7-cycles session's 8-FY published-stats gate (canonical a70cc64, run
under direct owner authorisation) validates the 2022 backfill against DVSA
published volumes: FY2022-23 32,541,091 vs DVSA 32,543,026 (-0.01%%, 0.00pp
rate delta); FY2021-22 -0.12%%; median |final delta-pp| across 8 gated years
= 0.004. This is the strongest available completeness evidence for the
repaired year.

CORRECTION: an earlier peer-message of mine stated 2022 ingested
"42,584,997" rows. That figure was NEVER MEASURED — it appears in no tool
output and was fabricated precision on my part. The true count, measured
directly and matching the peer's before/after lake delta exactly:
**41,632,878** (total results rows now 681,724,337). Recorded as an instance
of the unverified-attribution failure class this record repeatedly documents.
