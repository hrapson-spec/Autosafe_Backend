# AutoSafe 2005–present data assessment — analysis log

Owner-commissioned assessment (Henri, 2026-08-12). Plan of record:
`~/.claude/plans/establish-what-we-now-swirling-fox.md` (approved 2026-08-12).

## Authority

Henri's ruling (2026-08-12, AskUserQuestion): OWNER OVERRIDE of the #18 lake-token
protocol for this owner-commissioned assessment, recorded in the assessment memo
(no separate authority artifact). Operational one-scanner discipline still honoured:
pre-flight process check before every heavy batch; yield to any live peer lake scan.
The seat-6 census mission held the token at plan time (granted ~12:2x, d7-side
OPEN_QUEUE) — this assessment's reads run under the owner override, not a token
transfer; the d7-side record should be reconciled to note this.

This assessment creates NO lake datasets (no cycles, no frames, no lake writes),
honouring the G2/G4 holds. Outputs live in this directory and the session scratchpad.

## Environment pins

- duckdb 1.5.5 (venv `~/autosafe-v58/.venv`, python 3.11.14) — pinned because
  `hash()` is version-dependent and the 1/100 panel is hash-defined. ALL panel
  extractions must run this venv.
- duckdb session settings for every lake read: per-PID `temp_directory`,
  `max_temp_directory_size` cap counted in the disk ledger, `memory_limit` 3–4GB,
  default `preserve_insertion_order`, column projection, one process at a time.
- Disk floor 10 GiB (abort, never squeeze).

## Preregistered scripts (sha256 recorded BEFORE first execution)

| script | sha256 | registered | outputs |
|---|---|---|---|
| scripts/anchors.py | 8832cc62ddde04744eeed87ca7626d2fe2a0535bedfb9be984ac24ecee07470d | 2026-08-12 13:43:00 BST | out/anchors.json |
| scripts/profile_results_local.py | b48004813ac8c4061a8e1f02e2490e2d94ce7c4aa23ec0e854c1efb61cb7642a | 2026-08-12 13:48:39 BST | out/results_*.json |
| scripts/profile_items_all.py | 383684b50e58db342a2a0ed5ca94b537b3fd882c49f1f6ba7e4b78222f37edd1 | 2026-08-12 13:48:39 BST | out/items_*.json, out/catalogue_guards.json |
| scripts/run_phase2.sh | 29c769b52b2ccaf786de5b878dd5245e85ae2f7608f573276d450e197af4b45e | 2026-08-12 13:48:39 BST | logs (phase2) |

## Instrument proof result

anchors.py VERDICT: PASS (all 7 anchors, 2026-08-12 13:43 BST) — local footer sums,
manifest totals, parked derivation, 2022 exact count, and the recorded class-4
year_volumes table all reproduce/cite-check exactly.

| scripts/panel_extract_year.py (r2) | 13bd6789caf4f102cd995bae0d0f513c054ff4fd598a963ad5e84b1de1fd0 → see below | 2026-08-12 13:54:41 BST | panel shards, out/parked_year_profiles.json |
| scripts/run_phase3.sh (r2) | 6cab912cb8045fa72ea5f3c4bae637074a1349cd13a192cfd754b8a12f1d8473 | 2026-08-12 13:54:41 BST | phase3 log |
| scripts/panel_items_join.py | aceb4a499286ca82cfcea32dfbc4b18a817659a4ed6dec09934a87a7a52693da | 2026-08-12 13:52:32 BST | panel/items_panel.parquet |
| scripts/run_phase3.sh (r3) | 55818fdce6d3a72de41d354e97e38b6e31f9fff74f1dec329b052e13f34e96bd | 2026-08-12 13:57:30 BST | phase3 log |
| scripts/analyze_panel.py | 6cf76b8ecc61ab5ab2733916177c0ad7cc933aa867cce26eaefe24452f5c5729 | 2026-08-12 14:01:59 BST (before any parked-year panel outputs were read; ladder mid-flight at year 2006) | out/panel_*.json |

Correction: panel_extract_year.py (r2) sha256 =
13bd6789caf4f102cd995ba3eae0d0f513c054ff4fd598a963ad5e84b1de1fd0.
Superseded r1 shas (registered 13:52:32, never executed): panel_extract_year.py
6e56de2dbde6683348519c40db9667aafb2f66d77e3443df1e7d3184f3a1ff3d, run_phase3.sh
db09ba9f7e40cd04de831525ea09ad9f4b5a0a36b02f4c5c538d5f5d9f6810d0.

## Completion (2026-08-12 ~14:40 BST)

All phases complete. Ladder end-state == start-state (2005–2014 parked, sentinel
untouched; verified in phase3 log §3). Panel artifacts in session scratchpad
(results_2005..2023.parquet, items_panel.parquet, all_vehicles_running.parquet;
NOT committed — regenerable from the sha'd scripts). Self-audit: 14/14 memo
headline numbers reproduce from their cited out/ artifacts (transcript in session
log). Continuity n≥50k charter item closed by panel (n=628,177). Identity check
resolved with a follow-up date-agreement query (1,237,152 matches, 100.000000%
date agreement; run inline, recorded in panel_identity_check.json + memo).

## Deviations

1. 2026-08-12 13:54 — PRE-EXECUTION amendment of panel_extract_year.py/run_phase3.sh
   (r1→r2, zero panel outputs existed; panel dir empty at re-registration): measured
   free disk ~13–14 GiB vs the r1 ledger's 14 GiB gate. r2 closes the counted ledger
   at 13 GiB: spill cap 6→1.5 GiB (per-year queries fit 3 GB memory), running-merge
   moved AFTER year deletion via per-year _veh_*.parquet distinct files. Floor 10 GiB
   unchanged.
2. 2026-08-12 13:55–13:58 — run_phase3.sh r2 launch printed `declare -A` errors
   (macOS bash 3.2); operator killed the chain mid-year-2017 as a suspected failure.
   In fact bash 3.2 had parsed the year-keyed literal as a sparse INDEXED array and
   years 2015–2016 completed with anchors matching. Kill was therefore premature
   (lesson: watch the log for actual failure before killing). Because the running
   merge is not idempotent, panel scratch state was RESET to empty and the chain
   relaunched from scratch as r3 (portable case-function lookup replaces the array;
   sha 55818fdce6d3a72de41d354e97e38b6e31f9fff74f1dec329b052e13f34e96bd). No repo or
   lake state was touched by the aborted run; local years only.
