# Item-observability index — design (Gate 4)

**Author:** R3 (factory engineer) · **Date:** 2026-08-13 · **Status:** design + fixtures, awaiting owner approval
**Defect:** Henri's P0 ruling — `vehicle_day_atom` LEFT JOINs items then coalesces to 0, so
"this prior test recorded no defect" and "this prior test's items are absent" are the same value.
**Deliverable rule:** no item-derived cube feature may be built until this clears.

Evidence artifacts alongside this doc:

| File | What it is |
|---|---|
| `item_coverage_measured.json` | every number in §1, machine-readable |
| `test_item_observability_fixtures.py` | 14 executable fixtures — 7 prove the conflation, 7 `xfail(strict)` assert the repair |

**Provenance discipline.** Everything in §1 is MEASURED in this session against
`/Users/henrirapson/autosafe/autosafe_lake` with duckdb 1.5.5 (the contract-pinned
`.venv`), exact `SEMI`/`ANTI JOIN` on `test_id`, no sampling, `memory_limit=1800MB`,
`threads=3`. Numbers READ FROM A DOC (not measured) are labelled `[read]` inline.
`approx_count_distinct` was tried and **rejected**: it returned distinct counts up to
1.38× the row count (impossible), so every distinct/join figure here is exact.

---

## 1. What actually distinguishes the states in this lake

### 1.1 Per-partition item coverage — the evidence base

Exact, whole-population. `join rate` = share of `results` rows with ≥1 `items` row.

| test_year | results rows | distinct test_id | item rows | distinct test_id | results w/ items | **join rate** | orphan item test_ids | schema_epoch |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2015 | 37,490,736 | 37,490,736 | 70,077,368 | 20,898,031 | 20,898,031 | **0.5574** | 0 | results_mts |
| 2016 | 37,693,380 | 37,693,380 | 69,784,106 | 21,031,551 | 21,031,551 | **0.5580** | 0 | results_mts |
| 2017 | 38,056,161 | 38,056,161 | 71,195,133 | 21,410,418 | 21,410,418 | **0.5626** | 0 | results_mts |
| 2018 | 38,681,801 | 38,681,801 | 74,691,268 | 22,165,284 | 22,165,284 | **0.5730** | 0 | results_csv |
| 2019 | 39,310,698 | 39,310,698 | 79,966,042 | 23,275,686 | 23,275,686 | **0.5921** | 0 | results_csv |
| 2020 | 38,594,013 | 38,594,013 | 75,907,074 | 22,264,880 | 22,264,880 | **0.5769** | 0 | results_csv |
| 2021 | 40,380,646 | 40,380,646 | 82,426,968 | 23,834,491 | 23,834,491 | **0.5902** | 0 | results_csv |
| 2022 | 41,632,878 | 41,632,878 | 86,352,341 | 24,869,910 | 24,869,910 | **0.5974** | 0 | results_mts |
| 2023 | 42,216,721 | 42,216,721 | 89,302,629 | 25,506,643 | 25,506,643 | **0.6042** | 0 | results_mts |
| 2024 | 42,637,055 | 42,637,055 | 91,827,424 | 25,983,217 | 25,983,217 | **0.6094** | 0 | results_extracts |
| 2025 | 42,728,066 | 42,728,066 | 92,473,454 | 26,141,545 | 26,141,545 | **0.6118** | 0 | results_extracts |

Four structural facts fall out, and they reshape the design:

1. **No partition in the staged range is items-absent.** Every `test_year` 2015–2025 has items.
   Partition-level absence is *not* the generator of `ITEMS_UNAVAILABLE` here.
2. **Zero orphan item `test_id`s in every partition**, and `n_results_with_items` equals
   `distinct item test_id` exactly. Items and results partitions are perfectly aligned:
   an item never lives in a partition whose parent test does not.
3. **`test_id` is unique in `results` in every year** (rows == distinct). The
   `atoms.py:240` LEFT JOIN is genuinely 1:1; there is no fan-out.
4. **The join rate rises monotonically, 0.5574 → 0.6118, with no cliff.** This is a
   *behavioural* trend (advisory recording increased), not an availability edge. A cube
   feature that reads `n_items` as a level therefore carries a +9.8% relative drift
   across the window that has nothing to do with vehicle condition.

Also measured: `results` hive `test_year` is **exactly** `year(test_date)` for all
439,422,155 staged rows (zero skew), so partition and date agree and per-partition
measurement is the correct unit.

### 1.2 The decisive instrument: fail-bearing tests with zero items

A `FAIL` or `PRS` cannot have zero reasons-for-rejection. Any such row is a
**definitionally impossible state** and is the strongest available detector of missing
items. Measured over the whole lake:

| test_year | fail-bearing tests | zero items | rate |
|---:|---:|---:|---:|
| 2015 | 10,910,267 | 2 | 0.0000002 |
| 2016 | 10,698,996 | 0 | 0 |
| 2017 | 10,524,198 | 1 | 0.0000001 |
| 2018 | 10,481,704 | 0 | 0 |
| 2019 | 10,201,959 | 0 | 0 |
| 2020 | 9,411,994 | 1 | 0.0000001 |
| 2021 | 9,746,974 | 0 | 0 |
| 2022 | 9,795,244 | **157** | 0.000016 |
| 2023 | 9,919,172 | 20 | 0.000002 |
| 2024 | 9,920,669 | **9,108** | 0.000918 |
| 2025 | 9,750,529 | 3 | 0.0000003 |

The instrument reads ≈0 in eight of eleven years — so **ingestion is sound and the
detector is not firing on noise**. It fires hard exactly twice.

### 1.3 Anomaly A — 2024-12-31 is entirely dark (`ITEMS_EXPECTED_MISSING`)

All 9,105 of 2024's globally item-less fail-bearing tests fall on **one single date**.
Probing those `test_id`s against *every* items partition 2005–2025 (not just 2024) returns zero.

| date | tests | with items | coverage | fail-bearing | fb with items |
|---|---:|---:|---:|---:|---:|
| 2024-12-28 | 23,411 | 14,732 | 0.6293 | 5,121 | 5,121 |
| 2024-12-29 | 985 | 561 | 0.5695 | 225 | 225 |
| 2024-12-30 | 66,334 | 41,580 | 0.6268 | 15,866 | 15,866 |
| **2024-12-31** | **41,349** | **0** | **0.0000** | **9,105** | **0** |

Three independent confirmations that these items *should* exist:

- **`completed_ts` carries all 41,349 rows for 2024-12-31.** The sidecar is exactly 1:1
  with `results` in 2024 and 2025 (42,637,055 and 42,728,066 rows — identical to the
  results partitions). Results and the sidecar cover the day; only items do not.
- **Neighbouring days sit at 0.627 coverage** with a 1.0000 fail-bearing rate.
- **The 2025 series has no equivalent gap** (2025-12-30 is the lake's last day; no 2025-12-31 rows exist).

**Attribution — and a correction to the brief's state definition.** The brief defines
`ITEMS_EXPECTED_MISSING` as "a defect in OUR pipeline". Measured, it is not:

| | bytes | rows |
|---|---:|---:|
| DVSA published `dft_test_item_extracts_2024.zip → dft_test_item_extract_202412.csv` `[read: out/probe_item_extracts_2024.json]` | 279,587,213 | — |
| Lake-ingested same file (`lake_manifest.json`, sha256 `e9ac2c86…`) | 279,587,213 | 5,930,254 |

**Byte-exact: our ingestion lost nothing.** The published December-2024 item file is
itself short of 2024-12-31. So *expectation* and *blame* are different questions, and the
design must keep them in different fields (§3.1).

A recovery lane exists but is **unverified**: DVSA also publishes
`MOT+Testing+data+failure+item+(2024).zip`, whose `test_item_202412.csv` member is
**320,742,396** uncompressed bytes `[read: out/probe_item_orig_2024.json]` — 14.7% larger
than the `dft_` file we ingested. That is *suggestive*, not conclusive: the two
publications have different schemas, so the size delta is not by itself proof of extra
rows. Confirming it needs a download the owner must authorise.

### 1.4 Anomaly B — non-definitive outcomes lose items entirely in 2024–25 (`ITEMS_UNAVAILABLE`)

| test_year | ABANDONED/ABORTED/ABORTED_VE tests | with items | rate |
|---:|---:|---:|---:|
| 2015 | 265,462 | 21,078 | 0.0794 |
| 2016–2023 | 212,508 … 246,859 | — | 0.127 – 0.150 |
| **2024** | **255,216** | **0** | **0.00000** |
| **2025** | **256,054** | **0** | **0.00000** |

511,270 tests with **exactly zero** item rows anywhere in the lake — verified by probing
all items partitions, not just the same year. The cliff lands precisely on the
`results_extracts`/`items_extracts` schema-epoch boundary. This is a publisher schema
change, not loss: a clean, cell-shaped `ITEMS_UNAVAILABLE`.

### 1.5 Anomaly C — scattered row-level loss, 2022

157 fail-bearing tests with zero items, spread across the year at 1–7 per day (max
2022-11-23 and 2022-11-30, 7 each). No date or partition structure. Volume is
1.6 × 10⁻⁵ of the year's fail-bearing population — real, negligible, and correctly
classified `ITEMS_EXPECTED_MISSING` at row grain.

### 1.6 The `ITEMS_PRESENT_ZERO_DEFECTS` base rate — the design's load-bearing test

The brief requires this population to be non-trivial or the state design is wrong.

| test_year | PASS tests | PASS with zero items | **share** |
|---:|---:|---:|---:|
| 2015 | 26,315,007 | 16,348,319 | **0.6213** |
| 2016 | 26,781,876 | 16,476,979 | 0.6152 |
| 2017 | 27,296,486 | 16,441,265 | 0.6023 |
| 2018 | 27,970,529 | 16,316,115 | 0.5833 |
| 2019 | 28,888,822 | 15,843,917 | 0.5484 |
| 2020 | 28,969,655 | 16,143,990 | 0.5573 |
| 2021 | 30,408,140 | 16,351,226 | 0.5377 |
| 2022 | 31,605,478 | 16,562,871 | 0.5241 |
| 2023 | 32,050,690 | 16,500,187 | 0.5148 |
| 2024 | 32,461,170 | 16,389,514 | 0.5049 |
| 2025 | 32,721,483 | 16,330,464 | **0.4991** |

**≈16.4M tests per year, 49.9%–62.1% of all passes.** The state is overwhelmingly the
majority explanation for a zero and the four-state design survives contact with the data.

The corollary is a hard constraint on the repair: **the fix must not null-out zeros.**
Nulling every zero would destroy 179,704,847 genuine observations across the window to
repair 552,806 — a 325:1 damage ratio. Fixture
`test_repaired_clean_pass_keeps_its_honest_zero` pins this.

### 1.7 Within-partition uniformity

- **By month:** uniform to ≈10⁻⁶ everywhere except 2024-12 (0.0146, driven wholly by one day).
- **By `schema_epoch`:** the only epoch-aligned effect is §1.4 (non-definitive outcomes, `*_extracts`).
- **By `taxonomy_era`:** no availability effect. Items partition 2018 splits pre/post
  (29,051,275 / 45,639,993) exactly as the 2018-05-20 switch requires; 2017 is wholly
  `pre_2018` and 2019+ wholly `post_2018`.
- **By source file:** the manifest's per-file `rows_ingested` sums **exactly** to each
  partition's row count for all 21 items and 11 results partitions (e.g. items 2017 =
  the 12 monthly CSVs = 71,195,133; items 2021 = 82,426,968; results/items 2024+2025 =
  85,365,121 / 184,300,878). Ingestion is lossless at file grain everywhere.

### 1.8 What the manifest can and cannot tell you

`lake_manifest.json` gives per-source-file `rows_ingested`, `bytes` and `sha256` for all
148 sources. Because those sums reconcile exactly to the partitions (§1.7), the manifest
**proves ingestion completeness but cannot detect a short publication** — the 2024-12-31
gap is invisible to it (5,930,254 rows ingested, 5,930,254 expected). The manifest is
therefore necessary but not sufficient; the fail-bearing detector (§1.2) is what actually
finds publication-side gaps, and `completed_ts` is what confirms them for 2024–25.

One caveat on the manifest, worth flagging to the owner: its `year_volumes` check reports
2015 = 35,445,915 `[read]` while the partition holds 37,490,736 rows all dated 2015. The
check's scope is narrower than the partition (its detail says "final results-scope run").
It is **not** a usable expectation source for this design; I used it for nothing.

### 1.9 Out-of-scope but load-bearing: the 2005–2014 asymmetry

`items` has partitions 2005–2014 (589,626,541 rows); `results` for those years are parked
to Drive (`results_PARKED`). Coverage for those partitions is therefore **unmeasurable
today**. If the owner restores them, §1.1 must be re-measured before any pre-2015 history
is admitted — a restored results year joined against items that were never checked would
reintroduce exactly this defect at 10× the volume.

---

## 2. Blast radius — exact columns and mechanisms

### 2.1 The four mechanisms

| # | Mechanism | Site | Effect |
|---|---|---|---|
| **M1** | `LEFT JOIN ita a ON a.test_id = r.test_id` | `factory/atoms.py:240` | unmatched test ⇒ all `a.*` NULL |
| **M2** | `coalesce(sum(a.<col>), 0)` over 10 scalars + 32 category columns | `factory/atoms.py:140`, `factory/atoms.py:144` | NULL ⇒ **0**; the primary defect |
| **M3** | `DayAtom.item(name, default=0)` → `return default if value is None else value` | `factory/state.py:102-104` | a second, independent coalesce — repairing only M2 would be silently undone here |
| **M4** | `n_items := coalesce(a.n_items, 0)`, `n_adv := coalesce(…,0)`, `n_fail_final := coalesce(…,0)` in the packet payload; `n_items=int(t.get("n_items") or 0)` | `factory/atoms.py:167`, `:168`, `:174`; `factory/state.py:118` | carries the conflation into the packets view → the 104 serving features |

`atoms.py:150` is the **counter-example that proves the fix is cheap**: the positional
columns use plain `sum()`, which is NULL-preserving, and `state.py:417-418` skips None.
B6 already does the right thing. The repair is to extend that discipline, not invent it.

### 2.2 Value-changed columns (the conflation changes the number)

**B2 — all 50 of 50.** Every column in the block is item-derived.

- 32 per-category (8 categories × `_n_days`, `_days_since`, `_max_run`, `_persistence`) — via
  `state.py:424-452` reading `day.cat_n/cat_adv/cat_fail`, emitted `blocks.py:330-335`
- `b2_breadth_categories`, `b2_last_day_n_categories`, `b2_n_items_total`, `b2_n_catalogue_miss_items` — `blocks.py:336-340`
- 14 depth-capped (`b2_<7 canonical>_n_days_cap2y` / `_cap5y`) — `blocks.py:341-344`, fed by `state.py:447`

**B3 — 16 of 18.** All except the two observability fields in §2.3.
`b3_n_dangerous_items/_days`, `b3_days_since_dangerous`, `b3_n_major_items/_days`,
`b3_days_since_major`, `b3_n_minor_items/_days`, `b3_days_since_minor`,
`b3_n_prs_items`, `b3_n_prs_item_days`, `b3_days_since_prs_item`,
`b3_n_fail_items_initial`, `b3_n_fail_items_final`, `b3_n_advisory_items`,
`b3_fail_item_rectified_share` — `state.py:385-411`, emitted `blocks.py:361-381`.

**B4 — 12 of 14.**
`b4_n_adv_to_fail_transitions`, `b4_adv_to_fail_categories`, `b4_days_since_adv_to_fail`,
`b4_n_recurrence_after_repair`, `b4_recurrence_categories`, `b4_days_since_recurrence`
(`state.py:425-441`); `b4_burden_delta_1`, `b4_burden_delta_2`, `b4_burden_mean_last3`,
`b4_burden_x_age`, `b4_burden_x_mileage_band_ord`, `b4_deterioration_slope`
(`state.py:466-473`, `state.py:267-272`, `state.py:305-313`).
`b4_mileage_band` is unaffected; `b4_deterioration_slope_n_days` is §2.3.

**Packets / B0 path — 1 of 18 packet columns, but it propagates to all 104 serving features.**
`p_n_items` is falsely 0 (M4). `defects_json` is correctly NULL (`packets.py:83-84`), which
makes the packet **internally contradictory**: the count says "clean", the payload says
"unknown". Every downstream feature in `feature_engineering_v55` that reads either one
inherits the conflation, including the 42-column B0-PC subset.

**Total value-changed: 78 of the 137 B1–B6 columns (56.9%), plus the whole B0/packets path.**

### 2.3 Interpretation-changed columns (number unchanged, meaning broken)

These are the dangerous ones, because they *look* fine.

| Column | Site | Why it lies |
|---|---|---|
| `b3_n_days_fine_severity_observable` | `blocks.py:362`, `state.py:396-397` | Documented as "the DENOMINATOR for every dangerous/major/minor count". Derived from `severity_observable`, which is a **date** predicate (`atoms.py:235`). A post-2018 day with dark items counts as observable. The denominator overstates. |
| `b3_severity_observability_status` | `blocks.py:349-356` | Returns `full` when every prior day is post-2018 — including days with no item data at all. |
| `b4_deterioration_slope_n_days` | `blocks.py:406` | Documented as the slope's "honest denominator". `slope_n` increments on every day (`state.py:469`), including dark days that contributed a false 0 to `slope_sy`. |
| `b1_history_coverage_grade`, `b1_observable_years_status` | `blocks.py:290-297`, `:274-278` | Grade `full` describes **results** coverage only and is silent about items. A consumer reads "full" and assumes the defect history is complete. |
| `b2_*_persistence` (8) | `blocks.py:335` | Numerator is item-derived, denominator is `state.n_days` (results). Dark days inflate the denominator only — a systematic downward bias, not noise. |
| `b6_*` (13) | `blocks.py:438-442`, `state.py:414-420` | Correctly NULL-preserving per day, but dark days are silently dropped from **both** numerator and denominator with no count. `b6_pos_n_total` is called "the honest denominator" and is not. |

### 2.4 Unaffected

All 26 `meta`, all 26 B1 (except the two interpretive entries above), all 15 B5, and
`b4_mileage_band` — these read only `results`. **B1 and B5 must stay exactly as they are**:
a vehicle's *test* history is fully observed even when its *item* history is not, and
conflating the two repairs would destroy the depth features that currently work.

---

## 3. The repair

### 3.1 The observability index

Determined from **source, partition and cell coverage — never from whether a row joined.**
Two orthogonal fields, because §1.3 proved expectation and blame are different questions:

```
items_observability ∈ { present_zero_defects, present_with_defects,
                        unavailable, expected_missing }

items_missing_attribution ∈ { publication_short, ingest_loss, unknown }   -- only when
                                                       observability = expected_missing
```

Following the contract's severity precedent (`FACTORY_CONTRACT.md` §Severity: "Pre-2018:
severity = UNOBSERVABLE — emitted as `status='pre2018_ungraded'`, never zero"), the index
is a `status=` enum and the affected counts go NULL, never zero.

**Resolution order, per test.** The index is built at `item_test_atom` grain from a
`factory_item_coverage` cell ledger keyed `(test_year, test_date, schema_epoch, outcome_class)`,
produced by the owner alongside the P4 certification and gated the same way:

| # | Rule | Evidence | State |
|---|---|---|---|
| 1 | Cell is declared dark (zero item rows lake-wide for that cell) **and** the cell's absence is publisher-structural | schema_epoch × outcome_class ledger (§1.4) | `unavailable` |
| 2 | Cell is declared dark but neighbouring cells are covered **and** `completed_ts`/results carry the rows | day ledger (§1.3) | `expected_missing` |
| 3 | Test has ≥1 item row | join | `present_with_defects` |
| 4 | Test has zero item rows **and** outcome ∈ {FAIL, PRS} | definitional impossibility (§1.2) | `expected_missing` |
| 5 | Test has zero item rows in a covered cell, outcome ∉ fail-bearing | residual (§3.3) | `present_zero_defects` |

Rules 1–2 are cell-grain and never consult the join. Rules 4–5 are row-grain; rule 4 is
the only row-grain rule that can *prove* absence, which is why the design's decidability
boundary sits exactly there.

**Attribution** is set from the manifest: `publication_short` when the ingested
`sha256`/`bytes` match the published artifact (2024-12-31 — proven), `ingest_loss` when
they do not, `unknown` when no published-artifact record exists.

### 3.2 Emitted contract

Per prior test-day, the day atom gains four counts, so the frame can always reconstruct
the denominator it was scored on:

```
n_days_items_present          n_days_items_unavailable
n_days_items_expected_missing n_days_items_present_zero_defects
```

Per feature family:

| Family | Corrected semantics | Null/status convention |
|---|---|---|
| B2 counts (`_n_days`, `_n_items_total`, caps) | count over **item-observed** prior days only | NULL when zero observed days; new `b2_item_observability_status` ∈ {`full`,`partial`,`none`} mirroring `b3_severity_observability_status` |
| B2 `_persistence` (8) | numerator and denominator both restricted to observed days | NULL when observed days = 0 |
| B2 `_days_since`, `_max_run` | unchanged rule, computed over observed days | NULL when never observed; a dark day **breaks** a run rather than resetting it to 0 |
| B3 counts (16) | as today but over observed days | NULL when `n_days_items_present` = 0, exactly as `graded()` already does for pre-2018 (`blocks.py:357-359`) — reuse that function |
| B3 `n_days_fine_severity_observable` | `severity_observable AND items_present` | value changes; that is the point |
| B4 burden/slope (12) | dark days excluded from the window and from the regression | NULL when <2 observed days |
| B4 `deterioration_slope_n_days` | count of **observed** days only | value changes |
| B6 (13) | unchanged (already NULL-preserving) + emit the dark-day count | `b6_location_map_status` precedent extended |
| packets `p_n_items` | NULL when items not present | drop the coalesce; add `p_items_observability` |

### 3.3 The residual, stated honestly

Rule 5 is an **assumption**, not a proof: inside a covered cell a PASS with zero items
could still be a dropped row. Fixture
`test_conflation_is_undecidable_at_row_grain_inside_a_covered_cell` demonstrates this is
irreducible — two vehicles differing by three real brake failures emit **159 of 163
columns identical**, including all 137 B1–B6 features (the four exceptions are the
identifiers and the salted vehicle hash).

The residual is bounded by the same-cell fail-bearing miss rate (§1.2): **≤1.6 × 10⁻⁵**
in the worst clean year (2022), ≈0 in eight of eleven. That bound belongs in the
BUILD_MANIFEST next to the coverage table, and the cube must report it rather than claim
row-grain certainty.

### 3.4 Code changes

| File | Change |
|---|---|
| `atoms.py:140,144` | `coalesce(sum(x),0)` → `sum(x)` (NULL-preserving, as `:150` already is) |
| `atoms.py:167,168,174` | drop the packet coalesces; add `items_obs` to the packet struct |
| `atoms.py` (new) | join `factory_item_coverage`; emit `items_observability` per test and the four day-level counts |
| `state.py:102-104` | `DayAtom.item()` must stop defaulting to 0 — return None and let callers decide. **This is the change most likely to be missed**: repairing SQL alone leaves M3 silently restoring the defect |
| `state.py:118` | `n_items=t.get("n_items")`, no `or 0` |
| `state.py:385-411,424-452,466-473` | guard every item accumulator on `day.items_present`; add the four day counters |
| `blocks.py:328-345,348-381,384-407` | emit NULL + status per §3.2; reuse `graded()` |
| `emit.py:467-475` | `_item_columns` must include the new observability columns or they never reach the scan |
| `packets.py:162` | `p_n_items` passthrough; add `p_items_observability` to `PACKET_COLUMNS` and `d13_invariant_projection` |

**Contract impact (deviate-with-test).** This is not a deviation — the contract already
mandates it. `FACTORY_CONTRACT.md` §Severity requires unobservable quantities to be
emitted as a status and "never zero", and §Feature blocks B3 requires an
"`n_days_fine_severity_observable` denominator". The current code violates both for item
availability. The failing tests are attached: the seven `test_repaired_*` fixtures.
Column count rises from 137 to ~146 (still inside the 150 cap) — the four day counts,
`b2_item_observability_status`, and per-block status fields.

**Falsifier suite.** Add F13 (item-observability): planting a dark cell must change every
affected column and must NOT change B1/B5; and a genuine zero must stay 0.

---

## 4. Verdict

**The four-state design survives contact with the data, with one correction.**

- `ITEMS_PRESENT_ZERO_DEFECTS` — ≈16.4M tests/year, 49.9%–62.1% of passes. Dominant, non-trivial, must keep its honest zero.
- `ITEMS_PRESENT_WITH_DEFECTS` — 20.9M–26.1M tests/year.
- `ITEMS_UNAVAILABLE` — 511,270 tests (2024–25 non-definitive outcomes), publisher-structural, exactly 0 with items.
- `ITEMS_EXPECTED_MISSING` — **41,536 tests detected**: 41,349 on 2024-12-31 (whole day dark, `completed_ts` present) + 187 row-grain fail-bearing detections elsewhere (157 of them in 2022). This is a **lower bound**: outside a dark cell only fail-bearing rows are decidable, so non-fail-bearing row-level losses are undetectable and bounded by §3.3.

**The correction:** `ITEMS_EXPECTED_MISSING` cannot be defined as "a defect in OUR
pipeline". The 2024-12-31 gap is proven byte-for-byte *not* ours — DVSA's published
December-2024 item file is short. Expectation (does the evidence say items should be
here?) and attribution (whose loss is it?) must be separate fields, because expectation
drives the feature semantics and attribution drives the recovery lane.

**Not yet established, and blocking a complete answer:** whether
`MOT Testing data failure item (2024)/test_item_202412.csv` actually carries 2024-12-31.
It is 14.7% larger than the file we ingested, but the publications have different schemas.
Owner decision needed on downloading it — if it carries the day, 41,349 tests move from
`expected_missing` to `present_*` and the recovery is a re-ingest, not a feature caveat.
