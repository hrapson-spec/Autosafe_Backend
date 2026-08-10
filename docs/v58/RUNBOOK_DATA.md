# v58 runbook — full-depth DVSA lake build (workstation)

Operator recipe for Phase 1 of the v58 program: download the DVSA
anonymised MOT bulk corpus and build the canonical parquet lake. Everything
here runs on YOUR workstation — never in CI, never in the serving
environment. Decisions and their tripwires: `docs/v58/DECISIONS.md`.

## 0. Prerequisites

- Python 3.11 with `pip install -r requirements.txt -r requirements-train.txt`
  (duckdb is the engine; nothing here touches the serving deps).
- Disk: budget **~350 GB free** — raw downloads ~80–150 GB compressed,
  lake parquet ~150–250 GB (zstd), plus working space. The lake lives
  OUTSIDE the repo (`~/autosafe_lake` below); nothing from it is committed.
- RAM: 16 GB minimum (`--memory-limit 8GB` default; raise on bigger boxes).
- Wall-clock: downloads are the long pole (hours, connection-dependent);
  ingest itself is I/O-bound, typically several hours for the full depth.

## 1. Download

Source identity (the same publication the SEO pages cite, pinned by
`tests/test_seo.py:376`):

> DVSA, "Anonymised MOT tests and results", data.gov.uk dataset
> `e3939ef8-30c7-4ca8-9c7c-ad9475cc9b2f`, Open Government Licence v3.

Download into one directory tree, e.g.:

```
~/autosafe_raw/
  results/   test_result_*.csv|txt      (per-year files, full depth 2005→present)
  items/     test_item_*.csv|txt
  lookup/    <the publication's lookup-table archive, unzipped:
              RfR detail + test-item group tables, pipe-delimited>
```

Record which release you downloaded (URL + date) — it goes into the
training manifest later.

**Layout expectations are enforced, not assumed**: every file's header is
matched against `pipeline/lake/schemas.py`'s registry and ingest FAILS LOUD
on any unknown layout, printing the observed header. If the publication
changed shape, extend the registry (one entry) — never loosen a reader.

## 2. Day-1 gate: vehicle continuity (DO THIS FIRST)

The program's riskiest assumption: the download carries ONE consistent
`vehicle_id` space across 2005→present. If IDs reset per file, cross-year
history accumulation is impossible and `WINDOW_START_V58` must be
renegotiated (DECISIONS.md D1) **before any further work**.

```bash
python -m pipeline.run_lake ingest-results \
    --source-dir ~/autosafe_raw/results --lake-dir ~/autosafe_lake
python -m pipeline.run_lake check --lake-dir ~/autosafe_lake --gate continuity
```

PASS criteria (see `pipeline/lake/checks.py::check_vehicle_continuity`):
≥20% of multi-test vehicles span ≥2 calendar years, ≤1% carry conflicting
first_use_dates, median inter-test gap ≈ annual. A per-file ID reset drives
the multiyear share toward zero — that is the STOP signal.

## 3. Items, cycles, full checks

```bash
python -m pipeline.run_lake ingest-items \
    --source-dir ~/autosafe_raw/items --lake-dir ~/autosafe_lake \
    --rfr-detail ~/autosafe_raw/lookup/<detail>.csv \
    --rfr-group  ~/autosafe_raw/lookup/<group>.csv

python -m pipeline.run_lake build-cycles --lake-dir ~/autosafe_lake

python -m pipeline.run_lake check --lake-dir ~/autosafe_lake --gate all
```

(Or `python -m pipeline.run_lake all --source-dir ~/autosafe_raw/results ...`
which runs the whole sequence and stops hard on a continuity failure.
Note `all` expects results and items under the same `--source-dir` root.)

What the full gate asserts:

| Check | Meaning of a failure |
|---|---|
| `vehicle_continuity` | ID space broken → STOP, renegotiate window |
| `year_volumes` | catastrophic ingest loss for some year |
| `no_unknown_outcomes` | outcome vocabulary drifted → extend OUTCOME_MAP deliberately |
| `class_mix` | class filter/shape wrong |
| `taxonomy_step` | May-2018 switch not visible where it must be → era wiring wrong |
| `rfr_type_coverage` | unregistered rfr_type codes → extend RFR_TYPE_BY_ERA deliberately |
| `category_coverage` | RfR→7-category mapping broken or lookup tables missing |

Expected first-run friction (by design): `rfr_type_coverage` and
`category_coverage` may fail on real top-level item names the mapping
table hasn't met. Extend `RFR_TYPE_BY_ERA` / `_SECTION_TO_CATEGORY` in
`pipeline/lake/rfr_mapping.py` with the reported names/codes, re-run
`ingest-items --force`, and commit the table change — the tables are the
deliberate, reviewed record of the mapping.

## 4. Idempotency & resume

- Every source file's sha256 is recorded in `~/autosafe_lake/lake_manifest.json`;
  re-running skips unchanged files, so a killed run just re-runs.
- A source file whose CONTENT changed under the same name refuses to
  ingest without `--force` — that's an upstream republication, understand
  it before forcing.
- Check results append to the manifest with timestamps: the manifest is the
  evidence artifact. Keep it; Phase 3's training manifest re-verifies its
  hashes.

## 5. What "done" looks like

- `check --gate all` exits 0;
- `lake_manifest.json` lists every source file with hashes and every
  partition with row counts and date coverage;
- per-year volumes look like the published DVSA statistics;
- the 2018 taxonomy step is visible (D/M/m codes exactly and only post
  20 May 2018).

Then proceed to Phase 2 (aggregates regeneration) and Phase 3 (matrix
build + training) — their runbooks assume this manifest exists.
