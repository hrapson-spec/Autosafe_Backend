# v58 handover — local agent (workstation phases)

**Audience:** a Claude Code (or similar) agent running ON THE OWNER'S MACHINE,
with the owner present. The remote agent that wrote the v58 pipeline cannot
see this machine; you can. This document is your brief. Read it fully before
running anything.

**Mission:** execute the data phases of the v58 program — download the DVSA
bulk corpus, build the lake, prove the day-1 assumption, and reconcile the
aggregate methodology — then return small evidence files so the remote side
can continue. You are NOT retraining the model yet (that code doesn't exist
yet; see Scope boundary).

**Context you must hold:** the live v55 model was trained on 2019-bounded
history but serves on full 2005+ API histories (train/serve skew). The v58
program re-ingests the full corpus and retrains. Plan decisions and their
tripwires: `docs/v58/DECISIONS.md`. Operator detail for the lake:
`docs/v58/RUNBOOK_DATA.md`. This handover sequences and adds the
agent-specific rules; where it and the runbook overlap, they agree.

---

## 0. Ground rules (non-negotiable)

1. **Branch discipline.** All work happens on
   `claude/autosave-defects-history-xqutcw`. NEVER push to `main` — merging
   `main` auto-deploys production (autosafe.one). Do not open PRs. Do not
   merge anything.
2. **Never `git add -A` or `git add .` in this repo.** Stage explicit paths
   only. The repo root accumulates untracked junk, and on this machine
   `work/` may be a full separate research repo (private remote) — it must
   never be committed here, and you must never push to its `legacy-product`
   remote.
3. **No data in git.** The raw download and the lake live OUTSIDE the repo
   (e.g. `~/autosafe_raw`, `~/autosafe_lake`). Only the small evidence files
   listed in §6 get committed.
4. **iCloud is a hazard twice over.** (a) The frozen `AutoSafe/` iCloud tree
   (`~/Library/Mobile Documents/...`) is READ-ONLY reference — do NOT use its
   "MOT Test Results" folders as ingest input: they are mixed-vintage
   downloads, and vehicle_id spaces are NOT comparable across release
   vintages. Ingest ONLY a fresh, single-release download (§2). (b) Do not
   place the download or lake in any iCloud-synced folder (Desktop/Documents
   often are) — macOS will try to upload ~200GB.
5. **Fail-loud is the design.** The pipeline refuses unknown file layouts,
   unknown outcome codes, and unknown defect-type codes rather than guessing.
   When it halts, the fix is a small, deliberate table extension (§5), never
   a loosened reader.
6. **Escalate, don't improvise**, on the triggers in §7.

## 1. Machine prep (~15 min)

```bash
git fetch origin && git checkout claude/autosave-defects-history-xqutcw
git log --oneline -5   # expect 1e50084 (P2), 9a3e927 (P1), 17f753c (P5a), f9f12a3 (P0) atop main
```

- **Disk:** confirm ~350 GB available (`df -h ~`); an external SSD is fine —
  every tool takes `--source-dir`/`--lake-dir` paths. Record the actual free
  space in your evidence notes.
- **Python env** (3.11; venv recommended):
  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt -r requirements-train.txt
  pip install pytest pytest-asyncio
  ```
  If the heavy CV pins in requirements.txt fight your machine
  (paddle/ultralytics — nothing in the backend imports them), install the
  rest without them; the pipeline needs: pandas, numpy, duckdb, pyarrow,
  jsonschema, pytest.
- **Sanity gate before touching data:**
  ```bash
  python -m pytest tests/test_pipeline_lake.py tests/test_pipeline_rfr.py tests/test_pipeline_aggregates.py -q
  ```
  All green (≈66 tests) or stop and escalate — do not run ingest on top of a
  broken environment.

## 2. Download (owner does this, or agent with owner watching)

Source of record — the ONLY acceptable input:

> DVSA, **"Anonymised MOT tests and results"**, data.gov.uk dataset
> `e3939ef8-30c7-4ca8-9c7c-ad9475cc9b2f`, Open Government Licence v3.

Download the CURRENT release's full depth (test results + test items,
2005→present) plus the **lookup-table archive** (RfR detail + item-group
tables). Lay out as:

```
~/autosafe_raw/results/  test_result_*.csv|txt
~/autosafe_raw/items/    test_item_*.csv|txt
~/autosafe_raw/lookup/   <unzipped lookup tables>
```

**Record in your notes:** the release page URL, the download date, and each
archive's filename. This goes into the evidence file — training lineage
depends on it.

## 3. DAY-1 GATE: vehicle continuity (before anything else matters)

The whole full-depth premise assumes ONE consistent `vehicle_id` space across
2005→present within this release. Prove it first:

```bash
python -m pipeline.run_lake ingest-results \
    --source-dir ~/autosafe_raw/results --lake-dir ~/autosafe_lake
python -m pipeline.run_lake check --lake-dir ~/autosafe_lake --gate continuity
```

- **PASS** → continue to §4.
- **FAIL** (multiyear share near zero = IDs reset per file) → **STOP the
  data work entirely.** Commit the check output as evidence (§6), tell the
  owner, and escalate to the remote session: `WINDOW_START_V58` must be
  renegotiated (DECISIONS.md D1) before any further step has value.

Schema surprises during this first ingest are expected friction, not
failures — see §5.

## 4. Full lake + reconciliation

### 4a. Items, cycles, all gates

```bash
python -m pipeline.run_lake ingest-items \
    --source-dir ~/autosafe_raw/items --lake-dir ~/autosafe_lake \
    --rfr-detail ~/autosafe_raw/lookup/<detail file> \
    --rfr-group  ~/autosafe_raw/lookup/<group file>
python -m pipeline.run_lake build-cycles --lake-dir ~/autosafe_lake
python -m pipeline.run_lake check --lake-dir ~/autosafe_lake --gate all
```

Everything is sha256-idempotent: a killed run just re-runs. `--force` is only
for files whose content genuinely changed (an upstream republication —
understand it first).

### 4b. Window discovery + D7 reconciliation

The old artifact's coverage window was never recorded anywhere. Discover it
and validate the cycle/PRS semantics in one exercise. Old-artifact ground
truth to match: **148,509,908 tests, 39,969,903 failures, rate
0.269139638817903, 254,145 rows.**

Probe candidate windows cheaply, straight off the lake — both counting bases,
because whether the original counted cycle-first tests or ALL tests is itself
unrecorded:

```python
import duckdb
con = duckdb.connect(); con.execute("SET memory_limit='8GB'")
R = "read_parquet('~/autosafe_lake/results/**/*.parquet', hive_partitioning=true)"
C = "read_parquet('~/autosafe_lake/cycles/**/*.parquet',  hive_partitioning=true)"
for start, end in [("2021-01-01","2024-12-31"), ("2020-01-01","2023-12-31"),
                   ("2021-01-01","2025-12-31"), ("2020-01-01","2024-12-31")]:
    for basis, extra in [("cycle_first", f"JOIN {C} c ON c.test_id=r.test_id AND c.is_cycle_first"),
                          ("all_tests", "")]:
        t, f = con.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE r.outcome='FAIL')
            FROM {R} r {extra}
            WHERE r.test_class_id='4' AND r.test_date BETWEEN '{start}' AND '{end}'
        """).fetchone()
        print(start, end, basis, f"{t:,}", f"{f:,}", f"{f/t:.6f}")
```

Extend the window grid until one cell lands near BOTH the old totals (±~2%)
and the old rate (±0.02 absolute). Then confirm formally:

```bash
python -m pipeline.run_aggregates \
    --lake-dir ~/autosafe_lake \
    --coverage-start <discovered> --coverage-end <discovered> \
    --old-artifact prod_data_clean.csv.gz \
    --expect-rate 0.269139638817903 \
    --out /tmp/recon_probe.csv.gz
```

Outcomes:
- **Cycle-first basis reconciles** → D7 confirmed. Proceed.
- **Only all-tests basis reconciles** → the D7 semantics decision is wrong
  for the aggregate denominator. STOP; record the probe table; escalate.
  (Do NOT edit the pipeline's cycle logic yourself — the decision affects
  the model target too and belongs to the remote session + owner.)
- **Nothing reconciles** → escalate with the probe table; likely a mapping
  or dedup issue worth remote analysis.

### 4c. The real artifact (build + gate, but DO NOT LAND — see boundary)

Once reconciled, build the production candidate on the pinned window (latest
5 FULL calendar years — with today's date that is 2021-01-01→2025-12-31):

```bash
python -m pipeline.run_aggregates \
    --lake-dir ~/autosafe_lake \
    --coverage-start 2021-01-01 --coverage-end 2025-12-31 \
    --old-artifact prod_data_clean.csv.gz \
    --out ~/autosafe_lake/candidate/prod_data_clean.csv.gz
```

All audit + match-rate gates must pass. Keep the artifact, the provenance
sidecar, and the printed constants block. **Do not replace the repo's
prod_data_clean.csv.gz yet** — that replacement is an ATOMIC commit touching
~15 files (constants, public copy, fixtures, tests, claim sweep) listed in
the approved plan's P2 section, and it should be executed as its own
carefully-checked step (you may do it WITH the owner's explicit go-ahead,
running `python scripts/claim_sweep.py` and the full pytest suite locally
before committing; otherwise hand the artifact's provenance back and the
remote session will prepare the diff).

## 5. Expected friction & the sanctioned fixes

| Symptom | Meaning | Sanctioned fix |
|---|---|---|
| `SchemaDetectionError` with an observed header | real files' layout differs from the registry's guess | add ONE `SourceSchema` entry in `pipeline/lake/schemas.py` mapping the observed columns to canonical names; add a matching case to `tests/test_pipeline_lake.py`; commit |
| `no_unknown_outcomes` gate fails | outcome vocabulary variant | extend `OUTCOME_MAP` in `pipeline/lake/normalize.py` + test; commit |
| `rfr_type_coverage` gate fails, listing codes | defect-type codes outside the era tables | extend `RFR_TYPE_BY_ERA` in `pipeline/lake/rfr_mapping.py` — decide fail/advisory semantics from the DVSA data dictionary, never by guessing; + test; commit |
| `category_coverage` gate fails | top-level item names unmapped | extend `_SECTION_TO_CATEGORY` with the reported names; + test; commit |
| `year_volumes` fails on early years only | 2005/06 computerisation ramp | expected for 2005-2006; if later years fail, that's real ingest loss — escalate |

Every such fix is: edit the ONE table, add a test case, re-run the affected
ingest with `--force`, commit with an explicit-path `git add` and a message
saying what the real data taught us. These table extensions are exactly the
commits the remote side wants to see.

## 6. Evidence return protocol

Commit (on the branch, push with
`git push -u origin claude/autosave-defects-history-xqutcw`) a directory
`docs/v58/evidence/` containing ONLY small text files:

1. `download_record.md` — release URL, date, file list, sizes, free-disk
   figure.
2. `lake_manifest.json` — copy of `~/autosafe_lake/lake_manifest.json`
   (hashes, row counts, per-partition coverage, check history).
3. `continuity_gate.txt` — the day-1 gate output, verbatim.
4. `checks_all.txt` — the full `check --gate all` output.
5. `window_discovery.md` — the probe table from §4b and the concluded
   window + basis.
6. `prod_data_provenance.json` — the candidate artifact's sidecar (NOT the
   artifact itself), plus the printed constants block.

These six files are the interface back to the remote session. No parquet,
no CSVs, no VRMs, no raw rows — aggregates and hashes only.

## 7. Escalate to the owner (and remote session) when

- the continuity gate fails (§3) — program-level decision;
- only the all-tests basis reconciles, or nothing does (§4b);
- disk shortfall mid-ingest (delete partial partitions, don't squeeze);
- a schema surprise is not a simple column rename (e.g. items lack a
  test_id, or results lack vehicle_id);
- anything tempts you to weaken a gate, widen a tolerance, or hand-edit
  lake files. The gates ARE the deliverable.

## 8. Scope boundary — what you must NOT do yet

- **No training, no feature matrices.** Phase-3 code (v58 contract,
  feature engineering, trainer, `pipeline/train/*` beyond the skeleton)
  is not on the branch yet. When it lands, an updated handover section
  will cover the matrix build and training run. Do not improvise a
  trainer from `train_catboost_production_v55.py` — it is a frozen,
  non-reproducible legacy script with documented defects.
- **No serving changes, no deploys, no shadow mode.** Remote phases.
- **No edits to** `openapi.json`, `scripts/claim_sweep.py`,
  `report_contract.py` constants, or public copy — those move only inside
  the P2 atomic landing commit.
- **The old artifact stays in place** until that atomic commit.

## 9. Quick reference

| Thing | Where |
|---|---|
| Design decisions + tripwires | `docs/v58/DECISIONS.md` |
| Lake operator detail | `docs/v58/RUNBOOK_DATA.md` |
| Lake orchestrator | `python -m pipeline.run_lake --help` |
| Aggregates orchestrator | `python -m pipeline.run_aggregates --help` |
| Pipeline tests | `tests/test_pipeline_{lake,rfr,aggregates}.py` |
| Old-artifact ground truth | 148,509,908 / 39,969,903 / 0.269139638817903 / 254,145 rows |
| Est. sizes (estimates!) | download 80–150 GB, lake 150–250 GB, budget ~350 GB |
