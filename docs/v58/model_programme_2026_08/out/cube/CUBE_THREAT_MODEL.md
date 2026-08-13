# CUBE_THREAT_MODEL — independent leakage / temporal-semantics threat model

**Author:** R4, independent Leakage & Temporal-Semantics Red-Team (blocking authority)
**Committed (UTC):** 2026-08-13T13:30:38Z
**Independence statement:** written BLIND. At the start of this review no cube artifact
of any kind existed: `out/cube/` was absent and
`find -iname '*CUBE*' -o -iname '*SEMANTIC*'` over the programme root returned nothing.
Every threat, falsifier and kill criterion below was derived and the counterexamples run
before any other agent's cube artifact appeared. No `*SEMANTIC_MODEL*` file has been read
at any point, and I have not opened `out/cube/CUBE_PARITY_AUDIT.csv` (written by another
agent at 14:33, after my analysis was complete). Every
threat below is derived from (a) the committed factory code, (b) `FACTORY_CONTRACT.md`,
(c) primary measurements I made on the lake in this session. Attack surface is therefore
a property of the DATA, not of anyone's design.

**Executable counterexamples:** `out/cube/cube_redteam_fixtures.py`
(7 probes X1–X7; run with `/Users/henrirapson/autosafe-v58/.venv/bin/python`, duckdb 1.5.5
pinned, tmp fixtures only, <1GB). **All 7 reproduce.** Verbatim output is quoted inline.

---

## 0. Standing conclusion (read this first)

**Two unresolved semantic counterexamples BLOCK G2 and G4 as of this commit:**

- **BLOCK-1 (coverage-era masquerade).** Two vehicles with physically identical
  histories at different calendar placements emit **16 of 137 differing feature
  columns** (X1, measured). The frame already carries a deterministic
  era-identification basis. No falsifier in the suite tests era-invariance.
- **BLOCK-2 (train/eval publication-vintage confound).** The training fence
  (`gates.py:22`, `TRAINING_TARGET_FENCE = 2024-01-01`) makes training 100%
  `schema_epoch ∈ {results_mts, results_csv}` and evaluation 100%
  `schema_epoch = results_extracts` — **zero overlap, by construction**
  (measured, §1.7). Publisher change and temporal drift are perfectly
  collinear and cannot be separated with current holdings.

These are not softenable. They do not depend on what a cube turns out to contain;
they are properties of the substrate the cube will be built on. A cube that adds
coverage/censoring axes makes BLOCK-1 strictly worse.

**Calibration of stakes.** The entire measured value of B1–B6 over B0 is
+3.608e-03 AUROC (`out/tables/s2_pass2/stage2_decision_tables.md`:
`s2.D.anchor.b0` 0.709929 → `s2.D.anchor.b0-6` 0.713537), against a materiality
floor of 1.78e-03 quoted in the same table. **The whole programme's headroom is
about two floors.** An uncontrolled era channel worth one floor consumes half of
everything B1–B6 has ever measured. This is why era control is not hygiene here —
it is the measurement.

---

## 1. Primary measurements made this session (evidence base)

All probes are bounded row-group reads via pyarrow (`read_row_groups`), column-projected,
no full scans, no duckdb over the lake. Slices are the first N row groups of the named
partition (~0.98–1.23M rows each) and are therefore **calendar-clustered, not random** —
stated wherever it matters.

| # | Claim under test | Verdict | Evidence |
|---|---|---|---|
| 1.1 | `test_id` carries within-day chronology | **FALSIFIED** | On same-day NT/RT pairs, where true order IS known (an RT can only follow an NT): P(test_id(NT) < test_id(RT)) = **0.4978** (2016, n=84,573) and **0.4986** (2023, n=82,035). SE≈0.0017 → indistinguishable from a coin flip. Stronger than the briefed "~67% monotone" (which is inflated by across-day pairs). D13 is **correct and load-bearing**. |
| 1.2 | `test_id` is a monotone sequence | **FALSIFIED** | Every year's slice spans test_id ≈ 17 … 1,999,999,9xx. It is a pseudonymised bijection into [0, 2e9), not a sequence. |
| 1.3 | `test_id` is globally unique (items→results join is safe) | **SUPPORTED** | 2015/2016/2023 slices: 0 within-slice duplicates; pairwise cross-year overlap **0**, versus ~720 expected under a random-draw model. `atoms.py:126` `JOIN … ON r.test_id = i.test_id` does not fan out. |
| 1.4 | `is_dangerous` is identically FALSE despite `dangerous_mark='D'` | **SUPPORTED** | 2016: `is_dangerous` True on 0/1,417,823 rows while `dangerous_mark='D'` on 15,123 (1.07%). 2023: 0/1,417,659 vs 64,736 (4.57%). F-22 confirmed; `severity.py` correctly never reads it. |
| 1.5 | Stored `rfr_class` inverts 'M' | **SUPPORTED** | 2023: code `M` → `rfr_class='major'`, `is_fail_item=True` on 105,110 rows. `severity.py:109` correctly maps M → MINOR, non-fail-bearing. |
| 1.6 | **`dangerous_mark` is absent pre-2018** (`FACTORY_CONTRACT.md:50-52`; DATA_ASSESSMENT §4/§8/finding 3) | **FALSIFIED — material** | `dangerous_mark='D'` is populated in **every year from 2005**: 2005 2.17%, 2008 2.23%, 2010 2.01%, 2012 1.86%, 2014 1.66%, 2015 1.58%, 2016 1.05%, 2017 1.08%, 2018-Q1 1.09%, then **steps to 5.86% (2019)** and settles ~4.3–4.6% (2021–2025). The column is present in the schema for all years. |
| 1.7 | Training and evaluation share a publication vintage | **FALSIFIED — material** | `schema_epoch`: 2015 `results_mts`; 2018–2019 `results_csv`; **2024–2025 `results_extracts`** (DVSA-portal monthly CSVs, `out/ingest_2024_2025_log.json`). Fence is 2024-01-01 → train ∩ eval vintage = ∅. |
| 1.8 | `location_id` codes are era-stable | **FALSIFIED — material** | Code SET alternates by publication vintage, not by era: 2015/2016/2019/**2021** use set A; **2020**/2022/2023/2024/2025 use set B; 42 codes differ each way. Resolution against the current 129-row `mdr_rfr_location.csv`: **65.6–69.2% for 2005–2019, 100.000% for 2023 & 2025.** |
| 1.9 | `model_id` key space is stable (hierarchy axis) | **FALSIFIED** | Distinct model_id per ~1M-row slice: 14,830 (2015) → 9,592 (2019) → 7,476 (2023) → **9,709 (2024) → 10,858 (2025)** — the DfT-era consolidation **reverses** at the publisher switch. Distinct-key overlap with the 2023 slice: 19.9% (2015), 35.9% (2019), 53.8% (2024), 46.2% (2025). *Caveat: distinct-key overlap overstates row-level impact; head keys (FORD FIESTA, FORD FOCUS, VAUXHALL CORSA) are stable. Row-weighted falsifier is C-3 below.* |
| 1.10 | `fuel_type` vocabulary is stable | **FALSIFIED** | 2015–2023: 2-letter codes only (PE/DI/HY/EL/OT/LP/ED). 2024–2025 emit a **mixed** vocabulary: `HY` *and* the free-text level `'HYBRID ELECTRIC (CLEAN)'` (6,897 rows in the 2024 slice; 8,284 in 2025). A categorical level that exists only in eval years is a perfect vintage tag. |
| 1.11 | `test_mileage` has a 999,999 ceiling | **SUPPORTED** | max = 999,999 in every year probed (2015–2025). At-ceiling share 0.00106% (2016), 0.00017% (2023). |
| 1.12 | `test_mileage` has 0.15% zeros | **FALSIFIED (in-lake)** | Zeros = **0.0000%** in both 2016 and 2023 slices; nulls 0.84%/0.74%. The unreadable/aborted encoding is NULL, not 0, in this lake. `atoms.py:27` `VALID_MILEAGE_SQL`'s `> 0` guard is inert-but-harmless; its stated rationale does not reproduce. |
| 1.13 | Same-day multi-test days are common and era-varying | **SUPPORTED** | 7.662% of vehicle-days (2016) → 7.755% (2023) in-slice; DATA_ASSESSMENT §3 gives full-year 9.34%→7.76% over 2015–2023. |
| 1.14 | `first_use_date` conflicts within a vehicle | **SUPPORTED** | Lake's own gate: `first_use_conflict_share = 0.0088` / `0.0078` (`autosafe_lake/lake_manifest.json`, vehicle_continuity, 2026-08-11/12), i.e. ~0.8% of vehicles — just under its own 0.01 threshold. (My single-year slices show 0% because most vehicles appear once in-slice; the cross-year measure is the manifest's.) |
| 1.15 | `test_type` is enriched asymmetrically by vintage | **UNPROVEN — no evidence found** | NT/RT present with smooth shares in every year 2015–2025 (NT 80.5–84.9%); no vintage cliff, no nulls observed. The parked 2005–2014 results are not in the lake (`results/` starts at 2015; `results_PARKED` is a 50-byte pointer file) so the claim is **untestable** for those years. |

---

## 2. Threat enumeration by mechanism class

Severity scale: **S1** blocks a fit; **S2** blocks a family/axis; **S3** requires a
declared control and a reported number; **S4** document-and-move-on.

### 2.1 Target bleed (the target row's own record entering its features)

| id | Mechanism | Worked example on this data | Sev | Verdict |
|---|---|---|---|---|
| TB-1 | Target-day items/outcome entering aggregates | Not present. `emit.py:508-511` emits every event on a day *before* `state.update(day)`; `blocks.emit_all` takes only `(state, event)` and never the `DayAtom`. X6 confirms: with 2 priors + a same-day sibling, `b2_n_items_total = 2` (target-day items excluded). | S1 | **FALSIFIED — the contract is sound here.** |
| TB-2 | Target-row attribute reads | `blocks.py:449` uses `event["tgt_fud"]` (the **target row's** `first_use_date`) for `b1_age_at_target_years`, `b1_observable_years`, `b1_left_censor_flag`, `b1_opportunity_adjusted_density`. `state.first_use_date` (state.py:339, from the earliest prior day) can disagree — ~0.8% of vehicles (1.14). Registration precedes the test, so this is not future information, but it *is* a target-record read and the two are not reconciled. | S3 | **SUPPORTED (mechanism), UNPROVEN (label correlation)** |
| TB-3 | `tgt_miles` as a feature | Fenced. `blocks.py:68` marks it "NOT a feature"; `fit_contract.resolve_featureset` raises on `meta` and `test_meta_columns_are_never_features` asserts `"tgt_miles" not in cols`. **Sound.** A cube axis that computes `tgt_miles − last_valid_mileage` would breach it. | S1 if breached | Currently **FALSIFIED (sound)** |
| TB-4 | Same-day sibling initial tests | X6, measured: two NT+definitive tests on the target day get **bit-identical feature vectors and opposite labels** (`sibling_labels: (True, False)`). Not leakage — irreducible label noise that caps achievable AUROC. Any cube that aggregates "per target day" would silently average the labels and manufacture apparent signal. | S2 for day-grain cubes | **SUPPORTED** |

### 2.2 Boundary / equality

| id | Mechanism | Evidence | Sev | Verdict |
|---|---|---|---|---|
| BE-1 | `<=` where `<` is required | Not present. `packets.assert_strictly_prior` (packets.py:174) rejects `p_date >= tgt_date`; the scan structure enforces it upstream. | S1 | **FALSIFIED — sound** |
| BE-2 | Trailing-cap window edge | `state.py:279-281` `bisect_right(dates, target − cap_days)` → a prior *exactly* `cap_days` before the target is **excluded**. X6: `cap_days_2y = 730`, prior at t−730 excluded, prior at t−729 included → `b1_n_prior_test_days_cap2y = 1` of 2. Half-open, correct. Note `cap_days(2.0) = round(730.5) = 730` (Python banker's rounding), not 731. | S4 | **SUPPORTED (correct); rounding is S4 documentation** |
| BE-3 | Era boundary at the taxonomy switch | `severity.era_expr` uses `test_date >= DATE '2018-05-20'` — inclusive lower bound on post-2018, from the **parent test date**, never the code space (correct per DATA_ASSESSMENT §5). Calendar 2018 is split mid-year and the `test_year=2018` partition holds 4 files spanning both eras (measured). | S4 | **SUPPORTED — sound** |

### 2.3 Ordering ambiguity

| id | Mechanism | Evidence | Sev | Verdict |
|---|---|---|---|---|
| OA-1 | Any use of `test_id` for order | Forbidden and enforced: F9 rejects unmarked ordering in `factory/*.py` executable lines AND asserts the rendered SQL matches a frozen 1-element allowlist (`test_falsifiers.py:344-346`). Measurement 1.1 shows the rule is *necessary* (P = 0.4978). | S1 | **FALSIFIED — sound (see gap G-4)** |
| OA-2 | Stratum-rank ordering inside a day | `cycles._cluster_outcome` (cycles.py:106-125) DOES order by test-type stratum: NT-FAIL resolved by a same-day RT-PASS ⇒ cluster outcome `PASS`. This is legitimate identified chronology, not `test_id`. But it makes the day-cluster ladder and the record-grain ladder disagree: `state.py:344-349` counts the NT FAIL into `n_final_fails` while `state.py:352-356` books the day as a PASS day. **Two inconsistent fail ladders coexist in one state object.** | S3 | **SUPPORTED** |

### 2.4 Retest semantics

| id | Mechanism | Worked example | Sev | Verdict |
|---|---|---|---|---|
| RS-1 | Same-day retest re-lists defects ⇒ burden double-count | **X2, measured.** Two vehicles, identical physical condition. A: NT FAIL with 3 brake items. B: same NT FAIL + a same-day RT PASS re-listing the same 3 items. Emitted: `b2_n_items_total (3, 6)`, `b3_n_fail_items_final (3, 6)`, `b4_burden_mean_last3 (3.0, 6.0)`, while `b1_n_prior_test_days (1, 1)` and `b2_brakes_n_days (1, 1)`. **Every volume/burden feature doubles for a recording artifact.** Same-day multi-test share is era-varying (1.13), so the inflation factor drifts with calendar time. *Whether real RTs re-list items is* **UNPROVEN** *(not measured; the fixture proves the factory's response, not the publication's behaviour) — falsifier V-2 settles it.* | S2 | **SUPPORTED (mechanism)** |
| RS-2 | Same-day repair invisible to the recurrence ladder | **X3, measured.** `state.py:438-441` uses `elif`: on a day where the category fails, `cat_repair_state` is set to 1 and can never reach 2 that day, even when the day cluster resolves PASS. Identical physical sequence, different recording: same-day repair → `b4_n_recurrence_after_repair = 0`; next-day repair → `= 1`. Era-varying by 1.13. | S2 | **SUPPORTED** |
| RS-3 | `b1_n_prior_initials` mixes NT-only counting with unfiltered history | `n_initial_day` uses `target_population.initial_test_sql` (NT + definitive) over *all* history classes. Correctly tagged `ERA_RESEARCH` (`blocks.py:88`) because `test_type` is absent at serving (DATA_ASSESSMENT §10). No vintage asymmetry found (1.15). | S4 | **UNPROVEN → currently benign** |

### 2.5 Coverage-era proxy — **the primary threat** (full treatment in §4)

| id | Mechanism | Evidence | Sev |
|---|---|---|---|
| CE-1 | Severity-observability columns are calendar coordinates | X1: `b3_n_days_fine_severity_observable` 0 vs 3; `b3_severity_observability_status` `none` vs `full`; all six `b3_n_{dangerous,major,minor}_{items,days}` NULL vs valued — for **physically identical histories**. | **S1** |
| CE-2 | Absolute calendar dates are fittable features | X4: `b1_first_prior_date`, `b1_last_prior_date` are `DATE` columns in block **B1**, and `fit_contract._typed_column` converts "dates/timestamps → numeric ordinal (ordered, so a tree can split on it)". `tgt_date` is fenced as meta; its two B1 proxies are not. Under a chronological split every eval value lies outside the training range. | **S1** |
| CE-3 | Pre-2018 severity is suppressed by rule, not by absence | 1.6: `dangerous_mark='D'` is populated at 1.05–2.23% pre-2018, but `severity.py:102-103` returns `pre2018_ungraded` unconditionally and `atoms.py:117-119` filters on `post`. The "structural absence" the contract preserves is **partly a choice**. The NULL is therefore a pure era tag with no informational justification for that portion. | **S2** |
| CE-4 | B6 positional resolution rate is a 34pp vintage fingerprint | 1.8: `b6_pos_n_total / b2_n_items_total` ≈ 0.66 for pre-2020 history vs **1.000** for 2023+. Worse than non-resolution: ~2/3 of set-A codes *do* collide with map ids and resolve to a **silently wrong** lateral/longitudinal/vertical group. | **S2** |
| CE-5 | Enrichment strata are era-degenerate | X7: identical D-marked physical history → `dangerous_prior` (post-2018) vs `recent_fail` (pre-2018); `b3_n_dangerous_days` `1` vs `None`. The sampling design over-samples recent-era vehicles by rule. `inclusion_weight` corrects the marginal, not the era composition of the stratum. | **S2** |
| CE-6 | Missingness fingerprints the boundary regardless | DATA_ASSESSMENT §8: "Can a model infer the period/regime from missingness? **Yes, trivially**". `cylinder_capacity` nulls trend with EV share; the 2020 mileage-null spike marks COVID. | S3 |

### 2.6 Cross-frame encoding

| id | Mechanism | Evidence | Sev | Verdict |
|---|---|---|---|---|
| XF-1 | Fleet/hierarchy priors built from the **sampled, enriched frame** rather than the population | `eb_fleet_builder.build_sql(frame_glob, …)` reads the emitted frame; `day_global/day_make/day_model` use `count(*)`/`sum(y)` with **no `inclusion_weight`**. The frame is u-thresholded and enrichment-tilted up to 25% (`sampling.MAX_ENRICHMENT_SHARE`), and the enrichment strata (`dangerous_prior`, `recent_fail`, `deep_history`) are **label-correlated by construction** (`sampling.py:159-176`). Every EB rate is therefore a biased estimate of the fleet rate, with a bias that varies by rung and by era composition. | S2 | **SUPPORTED** |
| XF-2 | Prior includes the target's own day | Not present. `eb_fleet_builder.py:100-118`: `sum(n) OVER w − n`, `sum(k) OVER w − k` over day-grain CTEs — the target's whole **day** is subtracted, matching the strictly-earlier-day rule. Frozen mode hard-asserts disjoint windows (`:242-249`). **Sound.** | S1 | **FALSIFIED — sound** |
| XF-3 | Sampling thresholds from the evaluation batch | `emit.calibrate` (emit.py:426-436) takes `quantile_cont(u, frac)` over the staged events of the window — including for eval-slice recipes. Selection only, label-independent, and `u` is a salted hash of `vehicle_id`; no label information flows. | S4 | **UNPROVEN → benign** |
| XF-4 | Learned transformations not rebuilt inside folds | `test_quantisation_borders_are_reused_across_seeds` shows CatBoost borders are *deliberately* reused across seeds from a `borders_path`. That is correct for seed-variance isolation, but it means the quantisation grid is a **cross-fit artifact** if the same borders file is reused across a train/eval boundary or across cube variants. | S3 | **SOURCE-DEPENDENT** (safe iff borders are computed on train only, per cell) |

### 2.7 Entity identity

| id | Mechanism | Evidence | Sev | Verdict |
|---|---|---|---|---|
| EI-1 | `vehicle_id` non-comparable across vintages | DATA_ASSESSMENT finding 6: "vehicle_id spaces remain non-comparable across vintages; all old↔new reconciliation must go through test_id". The factory keys history, bucketing and sampling on `vehicle_id` (`sampling.unit_hash_sql`). Sound **within** one lake build; any cross-vintage join on `vehicle_id` is invalid. | S1 if breached | **SUPPORTED (constraint)** |
| EI-2 | `model_id` hierarchy key unstable across vintages | 1.9. Distinct-key overlap 2024↔2023 = 53.8%; the consolidation reverses at the publisher switch. An EB/cube prior fitted on 2015–2023 mis-joins in the tail exactly where shrinkage matters most (small `n_model`). | S2 | **SUPPORTED (key instability); UNPROVEN (row-weighted magnitude)** |
| EI-3 | `fuel_type` level unseen in training | 1.10: `'HYBRID ELECTRIC (CLEAN)'` occurs only in 2024–2025. `tgt_fuel` is meta-fenced today, so **currently harmless**; it becomes S1 the moment a cube uses fuel as a hierarchy or cell key. | S1 if used | **SUPPORTED** |
| EI-4 | Sampling salt collides with the panel residue | Guarded: `sampling.py:45-56` concatenates the salt before hashing; buckets use a separate salt; `test_f7_salt_is_not_the_bare_hash` and `test_p3_salted_u_is_uniform_within_a_panel_residue` cover it. **Sound.** | S1 | **FALSIFIED — sound** |

### 2.8 Source vintage

| id | Mechanism | Evidence | Sev | Verdict |
|---|---|---|---|---|
| SV-1 | Train/eval vintage disjointness | 1.7. **BLOCK-2.** | **S1** | **SUPPORTED** |
| SV-2 | The lake mixes ≥5 item publication vintages | File naming: `src_test_item_YYYY_0` (2005–2016), `src_test_item_<id>_0` (2017, 12 files), `src_test_item-from-YYYY-MM-DD…` (2018–2019), `src_test_item_<ts>_<id>_0` (2021), `src_dft_test_item_extract_YYYYMM_0` (2024–2025). The `location_id` code set alternates with these, not with calendar era (1.8). | S2 | **SUPPORTED** |
| SV-3 | Monthly extracts carry fewer post-hoc amendments than annual publications | Mechanism is plausible (a 2024-02 monthly extract cannot contain a 2025 amendment; the 2023 annual file can). **No overlap year exists in the lake to test it** — 2023 exists only as `src_test_result_0.parquet`, 2024–2025 only as extracts. | S3 | **UNPROVEN and currently UNTESTABLE with lake holdings** |
| SV-4 | Pre-2022 mileage km contamination | DATA_ASSESSMENT §9: "Pre-2022 mileage km contamination; corrected upstream from 2022 dataset … 2022 step is UPSTREAM correction, not behaviour". `b4_mileage_band` is unit-robust by design (single last-trusted reading, `blocks.py:171`). **Any two-reading exposure axis crosses this boundary and produces negative annual mileage for unit-flipped pairs.** | **S2 for any exposure axis** | **SOURCE-DEPENDENT** (documented upstream; not re-measured here) |
| SV-5 | Republish suppression / 2019 renumbering | Not reproduced: no cross-year `test_id` collisions (1.3) and no within-slice duplicates. The 2019 partition is 4 quarterly files with ordinary id ranges. | S4 | **UNPROVEN — no supporting evidence found in-lake** |

### 2.9 Population integrity (found while attacking, not in the brief)

| id | Mechanism | Evidence | Sev | Verdict |
|---|---|---|---|---|
| PI-1 | **Silent event loss.** `emit.Factory._scan_sql` (emit.py:461-463) drives from the day relation and LEFT JOINs events onto it. Events whose day produced no staged day-atom disappear with no error, no gate and no manifest discrepancy. | **X5, measured.** With `history_classes=('4',)` and default `target_classes=('3','4')`: 4 emitted rows → **2**; `lost_tgt_ids: [1, 2]`; `raised_or_warned: False`. Safe only because `history_classes` defaults to `None`. It is a one-flag defect. | **S1 if the flag is ever set** | **SUPPORTED** |
| PI-2 | Column-cap headroom | `blocks.n_new_columns() = 137` against `NEW_COLUMN_CAP = 150`. **13 columns of headroom.** Any non-trivial cube either breaches the contract cap or replaces existing columns — both require a contract amendment, not a design choice. | S3 | **SUPPORTED** |

---

## 3. Per-axis falsifiers

Every falsifier below is **executable against fixtures**. Where I have already built and
run it, the file/function is named and the observed value quoted. Where not, the
construction is fully specified (fixture, assertion, pass criterion) so it can be
implemented without further design.

Notation: *pass* = the cube survives; *fail* = the stated consequence is proven.

### A. Volume / rate
- **V-1 (built, X2).** Two vehicles, identical physical defect history; one has a
  same-day RT re-listing the items. Assert every volume/burden column is equal.
  **Pass:** equality. **Observed: FAIL** — `b2_n_items_total (3, 6)`,
  `b4_burden_mean_last3 (3.0, 6.0)`. *A failure proves volume/rate axes measure
  recording practice, and since same-day-test share drifts 9.34%→7.76% (§1.13) the
  measurement drifts with calendar time.*
- **V-2 (specified).** Publication-behaviour check: on a bounded slice, for tests with
  `test_type='RT'` and `outcome='PASS'`, compute the distribution of item counts and the
  Jaccard overlap of `rfr_id` sets with the same-day NT. **Pass:** median overlap < 0.1.
  *Failure proves RS-1 is live in the real data, not just in the fixture, and converts
  V-1 from S2 to S1.*
- **V-3 (specified).** Denominator honesty: for every rate column `x_n / y_n`, assert the
  emitted value is NULL (never 0.0) when `y_n = 0`. **Pass:** zero rows with a
  0-denominator rate materialised as a number. `_safe_div` (blocks.py:238-241) already
  returns None; a cube must inherit it.

### B. Recency
- **R-1 (built, X6).** Prior exactly on the target date must contribute nothing; prior at
  `t − cap_days` must be outside the cap; at `t − cap_days + 1` inside.
  **Pass:** `b2_n_items_total = 2`, `cap2y = 1`. **Observed: PASS.**
- **R-2 (specified).** Recency columns must be expressed as *elapsed days*, never as an
  absolute date. Assert: `{c.name for c in cube_columns if c.dtype == 'DATE'} == set()`.
  **Pass:** empty. *Failure is CE-2 — see X4, which already fails this on B1.*
- **R-3 (specified).** Left-truncation invariance: emit a vehicle twice, once with its
  full history and once with all priors before `t − 5y` deleted. Every column whose
  definition is confined to a ≤5y window must be bit-identical. **Pass:** identical.
  *Failure proves an advertised trailing-window column is secretly unbounded.*

### C. Cadence
- **C-1 (specified).** COVID invariance: a vehicle whose gap straddles 2020-03-30…
  2020-08-01 and one whose identical gap does not. Assert `b5_gap_annual_band_flag` and
  every cadence column either agree, or the difference is confined to the declared
  `b5_covid_straddle_flag`. **Pass:** at most the declared indicator differs.
  *Failure proves cadence encodes the extension policy as vehicle behaviour.*
- **C-2 (specified).** Cadence columns must be functions of gaps only. Shift a whole
  vehicle history by +K years (both priors and target). **Pass:** every cadence column
  bit-identical for all K. *This is the era-shift control, §4 — it is the single most
  important falsifier in this document.*

### D. Exposure (mileage)
- **E-1 (specified).** Unit-flip robustness: a vehicle with readings 100,000 (2021, mi)
  then 165,000 (2022, "km-corrected") — i.e. the documented 2022 upstream correction
  (SV-4). Assert no exposure column reports a physically impossible annualised mileage
  and none reports a negative delta. **Pass:** the column is NULL or flagged, never a
  number. *Failure proves the exposure axis reports a publication correction as driving
  behaviour.*
- **E-2 (specified).** Ceiling handling: a reading of exactly 999,999 (1.11 — present in
  every year). Assert it is treated as censored, not as a value. **Pass:** censoring
  flag set and the value excluded from deltas/slopes.
- **E-3 (specified).** Single-reading rule: assert every exposure column that uses ≥2
  readings emits, alongside it, the count of readings used and the span they cover
  (honest denominator, per F8's principle).

### E. Volatility
- **Vo-1 (specified).** Sample-size confound: two vehicles with the same *mean* burden but
  2 vs 8 observed days. Assert any dispersion column is emitted with its `n` and is NULL
  below a declared minimum `n`. **Pass:** NULL below the minimum. *Failure proves the
  volatility axis is measuring history depth, which is itself left-censored by the 2005
  floor (`blocks.OBSERVABLE_FLOOR`) and therefore an era proxy.*
- **Vo-2 (built pattern, X2).** Volatility computed over item counts inherits the
  retest double-count exactly as volume does — re-run V-1's fixture against every
  dispersion column.

### F. Direction (trend / slope)
- **D-1 (specified).** Anchor invariance: `state.deterioration_slope` regresses items on
  `x = (day − first_date)/365.25` (`state.py:468`). Re-emit the same vehicle with one
  extra *earlier* prior added. Assert the slope over the common days is unchanged.
  **Pass:** unchanged. *Failure proves the trend axis moves when the observation window
  opens earlier — i.e. it encodes left-censoring, not deterioration.*
- **D-2 (specified).** Zero-variance guard: a vehicle with all priors on one day.
  **Pass:** slope NULL (`state.py:311-312` returns None on degenerate span) and
  `b4_deterioration_slope_n_days` reports the honest denominator.

### G. Persistence / transition
- **P-1 (built, X3).** Same-day vs next-day repair, identical physical sequence.
  **Pass:** equal recurrence counts. **Observed: FAIL** — `0` vs `1`.
- **P-2 (specified).** Transition definitions must be era-scoped or category-level only.
  Emit a vehicle with an advisory pre-2018-05-20 in a legacy code and a fail post-switch
  in the corresponding modern code. Assert the transition is detected at *category*
  level and **not** at `rfr_id` level (code spaces are disjoint, DATA_ASSESSMENT §5).
  **Pass:** category transition = 1, code-level transition column absent or NULL.
- **P-3 (specified).** Ladder consistency: assert the day-cluster fail ladder and the
  record-grain fail ladder are either reconciled or separately named and both emitted
  (OA-2). **Pass:** no column silently mixes them.

### H. Hierarchy (make / model / fleet)
- **H-1 (specified).** Weighted-vs-unweighted prior: rebuild the EB tables with
  `sum(y * inclusion_weight) / sum(inclusion_weight)` and compare to the shipped
  `count(*)/sum(y)` version on a synthetic frame with a 25% enriched share.
  **Pass:** |Δ r_global| < 1e-4 at every date. *Failure proves XF-1 — the fleet prior is
  an estimate of the sample, not the fleet.*
- **H-2 (built pattern, XF-2 verified sound).** Own-day exclusion: a make/age cell whose
  only events are on the target date must fall back to the parent level, never to its own
  rate. **Pass:** `n_make = 0` and the EB value equals the parent. Verified by
  `test_eb_fleet_priors_exclude_the_targets_own_day` and by reading
  `eb_fleet_builder.py:100-118`.
- **H-3 (specified, and the one that matters).** Key-space stability: row-weighted, not
  key-weighted. For each eval year, compute the share of **rows** whose
  `(model_key, age_band)` cell has `n_model ≥ 30` in the training-window tables.
  **Pass:** ≥ 95% and within 2pp of the same statistic computed on a held-out slice of
  the training years. *Failure proves EI-2 — the hierarchy axis silently degrades to the
  parent level for eval rows and the "hierarchy gain" is a training-window artifact.*
- **H-4 (specified).** Unseen-level behaviour: inject `'HYBRID ELECTRIC (CLEAN)'`-style
  levels present only in eval. **Pass:** the cube emits an explicit unseen-level
  indicator; it must never silently hash to an arbitrary bucket (EI-3).

### I. Coverage / censoring
- **CV-1 (built, X1).** **The era-shift control.** Two physically identical histories at
  different calendar placements. **Pass:** zero differing feature columns other than an
  explicitly declared, pre-registered regime-indicator allow-list.
  **Observed: FAIL — 16 of 137 columns differ**, `physically_identical_cols_agree: True`
  (so the difference is purely calendar, not physical). Differing set:
  `b1_age_at_target_years, b1_density_per_observable_year, b1_first_prior_date,
  b1_last_prior_date, b1_observable_years, b1_opportunity_adjusted_density,
  b3_days_since_major, b3_n_dangerous_days, b3_n_dangerous_items,
  b3_n_days_fine_severity_observable, b3_n_major_days, b3_n_major_items,
  b3_n_minor_days, b3_n_minor_items, b3_severity_observability_status,
  b4_burden_x_age`.
- **CV-2 (built, X7).** Enrichment-stratum era-degeneracy. **Pass:** identical strata for
  identical physical history. **Observed: FAIL** — `dangerous_prior` vs `recent_fail`.
- **CV-3 (specified).** Never-zero rule: every coverage column must emit NULL + a status
  string when the quantity is unobservable, never 0. Assert on a pre-2018-only history
  that all fine-severity columns are NULL and `b3_severity_observability_status='none'`.
  **Pass:** as stated. **Currently PASSES** (`blocks.py:357-359`, X1 confirms) — the
  contract is sound on the *representation*; the problem is that the status string is
  itself the era tag (CE-1).
- **CV-4 (specified).** Vintage-composition report: every emitted frame must carry, in
  its manifest, the row-share by `schema_epoch` for the **targets** and (separately) for
  the **prior records** the features were built from. **Pass:** present and non-degenerate.
  *This does not fix BLOCK-2; it makes it visible and quantifiable.*

---

## 4. The coverage-era masquerade attack

### 4.1 The attack

A cube rich in coverage/censoring indicators does not need to learn "which era is this
row from" — **the frame hands it over.** X1 measures the channel exactly: with
*physically identical* histories, 16/137 columns differ and the differences are
deterministic functions of calendar placement:

- `b3_severity_observability_status`: `none` ⟺ every prior day precedes 2018-05-20.
- `b3_n_days_fine_severity_observable`: literally `|{prior days ≥ 2018-05-20}|`, i.e. a
  clock reading crossed with depth.
- `b1_first_prior_date` / `b1_last_prior_date`: **absolute calendar dates**, converted by
  `fit_contract._typed_column` to "numeric ordinal (ordered, so a tree can split on it)".
- `b1_observable_years`, `b1_density_per_observable_year`,
  `b1_opportunity_adjusted_density`, `b1_age_at_target_years`, `b4_burden_x_age`: all
  anchored on `max(first_use, 2005-01-01)` and therefore on the digital-records floor.

Add the vintage layer and the channel widens beyond era into *publisher*:
`b6_pos_n_total / b2_n_items_total` ≈ 0.66 vs 1.000 (1.8); the `model_id` key space
re-expands at the 2024 switch (1.9); a fuel level appears that exists only in eval (1.10).

### 4.2 Why this is not "just harmless calendar information"

Two distinct harms, and they must not be conflated:

1. **Within-window ranking harm.** For a *fixed* eval window, era columns are not
   constant — they vary with each vehicle's history placement. `b3_status='none'` at a
   2024 target means "no prior since May 2018", i.e. a ≥6-year gap. The model can learn
   the *regime step* at 2018-05-20 (fail-item volumes drop ~18% on the corrected ladder
   from 2018, DATA_ASSESSMENT §4; the taxonomy change removed minors from failing) and
   apply it as if it were a vehicle property. That is a real, transferable-looking, and
   wrong effect.
2. **Extrapolation harm.** `b1_last_prior_date` is monotone in calendar time. Under the
   chronological fence (`gates.py:22`) every eval value exceeds every training value, so
   every tree split on it routes all eval rows to one side. The model silently degrades
   to the training-terminal base rate on that path — and the secular fail-rate decline
   (28.1%→22.8% over 2015–23, DATA_ASSESSMENT §6/§9) guarantees that base rate is wrong
   for 2024–25. This is invisible in AUROC and shows up as calibration drift.

### 4.3 The control that distinguishes genuine signal from era identification

**Primary control — the era-shift (time-translation) test.** Not a permutation of rows;
a **rigid translation of a whole vehicle's history in calendar time.**

> Construction. Take the emitted training frame. For a matched sample of vehicles,
> re-emit each vehicle twice through the *committed factory*: once at its real dates, once
> with every date (priors **and** target) shifted by +Δ, Δ ∈ {−4y, −2y, +2y, +4y}, with
> `first_use_date` shifted identically so age and observable-window are preserved.
> Physically, nothing about the vehicle has changed: same gaps, same items, same
> categories, same outcomes, same age at test.
>
> **Assertion:** every cube column must be **bit-identical across Δ**, except columns on a
> pre-registered, named regime-indicator allow-list (which must be ≤ 5 columns and each
> must be justified in writing as a regime indicator, not a health indicator).
>
> **Pass criterion:** `n_columns_varying_with_Δ − |allow_list| == 0`.
> **X1 is a two-point instance of exactly this test and it returns 16.**

**Secondary control — era-scrambled label null.** Fit the cube twice: (a) as designed;
(b) with the *label* replaced by a synthetic label generated from calendar time alone
(e.g. `y* ~ Bernoulli(base_rate(tgt_year))`, preserving the per-year marginal, independent
of the vehicle). If the cube attains AUROC ≥ 0.55 on (b), it can read calendar time off
its own features, and the magnitude of that read is a **direct upper bound on how much of
the real headline could be era identification**.

- **Pre-registered falsification of the cube's headline claim:**
  **if AUROC on the era-scrambled label ≥ 0.55, or if the era-scrambled AUROC exceeds
  0.5 by more than the cube's own headline delta over its reference, the headline claim is
  FALSIFIED and the cube must be rebuilt era-blind.** With the measured programme numbers
  this bar is brutal by design: the whole B1–B6 gain is 3.608e-03, so a cube claiming
  +2e-03 must show an era-scrambled AUROC within 2e-03 of 0.5.

**Tertiary control — vintage holdout (addresses BLOCK-2, partially).** The only honest fix
for train/eval vintage disjointness is to obtain the DVSA-portal `results_extracts`
publication of a year that also exists as a DfT publication (2023 is the obvious
candidate) and measure feature-level agreement row-by-row on the shared `test_id` space
(which is stable — 1.3). **Until that exists, every 2024/2025 eval number carries an
unquantified publisher term and must be reported as such.** I am not authorised to fetch
it and have not; this is a stated prerequisite, not a task I performed.

### 4.4 Ranking the era channels by measured separation

| Channel | Separation | Status |
|---|---|---|
| `b3_severity_observability_status` (none/partial/full) | Deterministic on the 2018-05-20 boundary | S1 |
| `b1_first_prior_date` / `b1_last_prior_date` as numeric ordinals | Perfect (they *are* the calendar) | S1 |
| B6 resolution share | 0.66 → 1.000, ~34pp | S2 |
| `schema_epoch` train/eval | 100% disjoint | S1 (BLOCK-2) |
| `model_id` key space | 46–54% distinct-key turnover at the switch | S2 |
| `dangerous_mark` rate step at 2018-05-20 | 1.09% → 5.86%, 5.4× | S2 |
| Same-day multi-test share | 9.34% → 7.76% | S3 |

---

## 5. Pre-committed kill criteria (binding on me)

These are stated **before** any cube exists so I cannot rationalise around them later.
Each names an observation, not a judgement. I commit to blocking on the observation alone.

**K-1 — Era-shift.** If the era-shift control (§4.3) shows **any** cube column varying
with Δ outside a pre-registered allow-list of ≤5 named regime indicators, I block the
whole cube. *Currently: the existing frame returns 16 (X1). The cube inherits this and
therefore starts in a blocked state.*

**K-2 — Era-scrambled label.** If the cube attains AUROC ≥ 0.55 against a calendar-only
synthetic label, I block the headline claim outright. If it attains an era-scrambled
excess over 0.5 that is ≥ the cube's headline delta, I block the claim as unidentified.

**K-3 — Absolute dates.** If any cube column has arrow type `date32`/`timestamp`, or is a
monotone transform of one (year, ordinal, epoch-days, "months since 2005"), I block that
column. No exceptions for "the tree will handle it". *X4 shows `b1_first_prior_date` and
`b1_last_prior_date` already violate this in B1; I am not grandfathering them.*

**K-4 — Recording-practice sensitivity.** If a family's value changes when a same-day
retest is added or removed while the physical history is fixed (V-1 / P-1 pattern), I
block the family until either (a) the definition is made day-cluster-invariant, or (b) the
same-day-test share is shown stable to within 1pp across the full train and eval windows.
*Currently V-1 and P-1 both FAIL.*

**K-5 — Coverage denominators.** If any coverage/censoring column emits `0` where the
quantity is unobservable (rather than NULL + status), I block it. Non-negotiable: a zero
asserts "measured, and it was none".

**K-6 — Hierarchy joins.** If the row-weighted share of eval rows whose hierarchy cell has
`n ≥ 30` in the training-window tables is below 95%, or differs by >2pp from the
train-holdout value (H-3), I block the hierarchy axis. If any hierarchy key is
`model_id`, `fuel_type` or any other field shown vintage-unstable (1.9, 1.10) without an
explicit stability measurement at that key, I block it.

**K-7 — Sample-derived aggregates.** If any cube axis is computed by aggregating the
**emitted frame** rather than the population, and does not use `inclusion_weight` in both
numerator and denominator, I block it (XF-1). Unweighted `count(*)`/`sum(y)` over an
enrichment-tilted frame is not a fleet rate.

**K-8 — Exposure across the 2022 mileage correction.** If any exposure axis consumes ≥2
odometer readings spanning 2021→2022 without an explicit unit-inconsistency flag, I block
the axis (SV-4, E-1).

**K-9 — Pre-2018 severity.** Because `dangerous_mark` is populated pre-2018 (1.6,
**falsifying the contract's own premise at `FACTORY_CONTRACT.md:50-52`**), I block any
cube claim that the pre-2018 severity NULL is "structural unavailability". It must be
re-labelled as a deliberate suppression, or the suppression must be lifted and re-gated.
Either is acceptable; the current description is not.

**K-10 — Population integrity.** If a cube build sets `history_classes` narrower than
`target_classes`, or if the emitted row count for a recipe does not equal the staged event
count for that recipe, I block the build (PI-1 / X5). I require an explicit anti-join
assertion, not an inspection.

**K-11 — Column cap.** If `n_new_columns()` exceeds `NEW_COLUMN_CAP = 150` (headroom
today: **13**), I block until the contract is formally amended. A cube is not a licence to
raise a cap silently.

**K-12 — Falsifier reach.** If any cube code lives outside `factory/*.py` top-level, or
introduces a SQL builder not enumerated in F9's rendered-SQL dict, I block until F9 is
extended (see G-4). An unreached gate is not a gate.

**What would make me withdraw a block.** K-1 and K-2 are withdrawn by a passing control,
not by an argument. K-3–K-11 are withdrawn by a code change plus a fixture that fails
before it and passes after. I will not withdraw a block on the basis that an effect is
"small", "well known", or "handled downstream by calibration".

---

## 6. What the existing falsifiers cover — and the cube-shaped holes

The suite is **stronger than the contract advertises**: 12 falsifiers + 5 adversarial
probes are implemented, not 10. Where it is sound, it is genuinely sound and I say so.

### 6.1 Covered, and I could not break it

| Falsifier | What it really pins | My attack |
|---|---|---|
| F1 (`test_falsifiers.py:48`) | Planting a future dangerous item leaves earlier targets and their packets bit-identical | Not broken |
| F2 (`:92`) / F12b (`:609`) | Same-day row shuffle **and** test_id swap leave all 137 features and 20 capped columns identical | Not broken; measurement 1.1 shows it is necessary |
| F3 (`:138`) | Full era×code×mark cross-tab; lowercase `m`, `M` pre-2018, `D`-as-code all raise | Not broken; 1.4/1.5 confirm the stored columns are indeed wrong and unread |
| F4 (`:174`) | Emit-before-update, incl. `b2_visibility_n_days == 0` for the target's own advisory | X6 independently confirms |
| F5 (`:208`) | Censoring statuses never zero-filled | Not broken |
| F6 (`:234`) | Enrichment stratum is as-of | Not broken (but see CV-2 — it is as-of *and* era-degenerate) |
| F7 (`:260`,`:281`) | Rung nesting; salt ≠ bare hash | Not broken |
| F8 (`:296`,`:319`) | Independent prior-day count over rows-with-priors; `p_date < tgt_date` | Not broken |
| F9 (`:401`,`:419`,`:436`) | No unmarked `test_id` ordering in `factory/*.py`; rendered SQL matches a frozen allowlist; **and the gate itself is falsified by a planted defect**. Note it asserts `not marked` — inline `D13-ALLOW` comments are *rejected*, so this is **not** the comment-satisfiable gate DATA_ASSESSMENT §12.4 warns about | Not broken; see G-4 for reach |
| F10 (`:471`) | Missing-year fail-loud before any output | Not broken |
| F11 (`:490`) | Class-3 priors count as history for a class-4 target | Not broken — but X5 shows the *knob* is unsafe |
| P1/P1b/P1c (`test_adversarial_probes.py:33-71`) | Horvitz–Thompson weight is a function of the design cell, not the realised `u` | Not broken |
| P4/P4b (`:147`,`:183`) | Staging is wiped, not merged; identical rerun is shrink-safe | Not broken |
| XF-2 (`eb_fleet_builder.py:100-118`) | EB priors subtract the target's own **day**, not just its own row | Not broken |

### 6.2 The holes a cube opens

| # | Gap | Why the current suite cannot catch it | Consequence |
|---|---|---|---|
| **G-1** | **No era-invariance falsifier of any kind.** Every falsifier compares rows *within a single fixture at fixed dates*. None re-emits the same physical history at a different calendar placement. | F1/F2/F4 all hold perfectly while X1 returns 16 differing columns. The suite is blind to the entire coverage-era class. | **BLOCK-1** |
| **G-2** | **No absolute-date guard.** No test asserts that features are dtype-free of calendar coordinates. | `test_meta_columns_are_never_features` fences `tgt_date` but not its B1 proxies; `_typed_column` then makes them splittable ordinals. | CE-2, K-3 |
| **G-3** | **No population-conservation assertion.** No test asserts emitted rows == staged events. | X5 loses 2 of 4 events silently under a supported config. | PI-1, K-10 |
| **G-4** | **F9's reach is bounded twice.** `_package_sources()` (`test_falsifiers.py:350-354`) walks only `factory/*.py` **top level** — `factory/runners/*.py` is never scanned. And the rendered-SQL check compares against a **hardcoded dict of 6 SQL builders** (`:436-469`); a new cube SQL builder is invisible to it. | A cube in `factory/runners/` or `scripts/` inherits *zero* D13 enforcement. | K-12 |
| **G-5** | **No retest-semantics falsifier.** F2 shuffles a same-day pair but never *adds or removes* one. | V-1 (X2) and P-1 (X3) both fail while F2 passes. | K-4 |
| **G-6** | **No cross-frame / weighting falsifier for aggregates.** `test_sample_weights_are_used` checks the *fit*, not the *prior construction*. | XF-1 stands: EB rates are unweighted counts over an enriched frame. | K-7 |
| **G-7** | **No vintage falsifier.** Nothing in the suite reads `schema_epoch`; nothing reports vintage composition. | BLOCK-2 is entirely invisible to the current gates. | CV-4 |
| **G-8** | **No hierarchy key-stability falsifier.** `test_eb_fleet_priors_are_hand_computable` and `..._exclude_the_targets_own_day` verify arithmetic and fencing, not join coverage on out-of-window keys. | EI-2: 46% distinct-key turnover at the eval boundary goes unmeasured. | K-6 |
| **G-9** | **No exposure/unit falsifier.** `test_mileage_conflict_flag` covers *same-day* disagreement only. | SV-4 (the 2022 correction) is a *cross-day* unit flip and is uncovered. | K-8 |
| **G-10** | **No same-day-sibling contract.** Nothing states what happens when two initial tests on one day get identical features and opposite labels (X6). | A day-grain cube would silently pool them. | TB-4 |

---

## 7. Summary of verdicts on every material claim examined

**FALSIFIED:** `test_id` within-day chronology (1.1); `test_id` monotonicity (1.2);
"dangerous_mark absent pre-2018" — *contract premise* (1.6); train/eval vintage
commonality (1.7); `location_id` era-stability (1.8); `model_id` key stability (1.9);
`fuel_type` vocabulary stability (1.10); "0.15% mileage zeros" (1.12); TB-1 target-day
bleed (sound); BE-1 as-of equality (sound); OA-1 test_id ordering (sound); XF-2 EB own-day
(sound); EI-4 salt collision (sound).

**SUPPORTED:** `test_id` global uniqueness (1.3); `is_dangerous` all-false (1.4); `M`
inversion in `rfr_class` (1.5); 999,999 ceiling (1.11); same-day multi-test share and its
drift (1.13); first_use conflicts ~0.8% (1.14); TB-2; TB-4; OA-2; RS-1 (mechanism); RS-2;
CE-1…CE-5; XF-1; EI-1; EI-2 (key instability); EI-3; SV-1; SV-2; PI-1; PI-2.

**UNPROVEN:** RS-1 in the real publication (V-2 settles it); RS-3 vintage asymmetry —
*and untestable for 2005–2014, whose results are not in the lake*; SV-3 amendment
gradient — *untestable with current holdings*; SV-5 republish suppression / 2019
renumbering — no in-lake evidence found; TB-2 label correlation; EI-2 row-weighted
magnitude; XF-3.

**SOURCE-DEPENDENT:** SV-4 pre-2022 km contamination (documented upstream in
DATA_ASSESSMENT §9, not re-measured here — but the exposure-axis consequence follows
either way); XF-4 quantisation-border reuse (safe iff computed on train only, per cell);
CE-6 missingness fingerprints (documented, structural).

---

*End of threat model. Committed before any cube specification was read.*
