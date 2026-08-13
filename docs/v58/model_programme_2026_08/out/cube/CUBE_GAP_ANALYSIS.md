# CUBE_GAP_ANALYSIS — R1 parity audit of the 241 adopted columns

**Scope.** The adopted featureset (`out/configs/s2.D.cum.b0-6.json`, `out/configs/s3.cb_inc.adopted.250k.json`)
is `featureset = [B1..B6]` (137 factory columns) plus `extra_columns` (the 104 serving
features, verified identical to `feature_engineering_v55.get_feature_names()`), joined
`x.test_id = f.tgt_id` (`factory/runners/fit_contract.py:209-214`). **241 columns.**
Counts verified against code, not the dictionary: `meta 26 · B1 26 · B2 50 · B3 18 · B4 14 ·
B5 15 · B6 14 = 137` (`factory/blocks.py:215-229`); the training frame carries
163 columns = 26 meta + 137 (`out/frames/recipe=flat4y/rung=r1m/frame/*.parquet`). Meta is
excluded from features by contract (`fit_contract.py:181-182`).

**Companion file.** `CUBE_PARITY_AUDIT.csv`, one row per adopted column, 18 fields,
every non-trivial cell cited `file:line`. Measured ranges and NULL rates are read from
parquet **row-group statistics** (metadata only — no data was loaded, no scan was run).

**Dictionary-vs-code check.** `factory/FEATURE_DICTIONARY.md` agrees with `factory/blocks.py`
on every column name, count and definition. **No disagreement found.** Two dictionary
*claims* are narrower than they read, and are recorded below (§4): the `b4_mileage_band`
"unit-robust by construction" claim, and the reading-convention 4 claim that "NULL is not
zero" — which holds for severity and position but **not** for item presence.

**Classification hierarchy used** (first match wins, so a column is reported at its most
severe defect):
`absent` (constant / structurally degenerate — the cell it names is not populated) →
`duplicate_redundant` (exact or monotone-deterministic function of another adopted column) →
`unsafe_ambiguous` (fabricated value, in-range sentinel with no companion, unit ambiguity,
target-derived lookup, or dependence on within-day record order) →
`different_window_or_semantics` → `partial` → `equivalent`.

---

## 0. Headline

| finding | number |
|---|---|
| Adopted columns audited | **241** |
| Carrying no information at all in the adopted frame (constant) | **21** (8.7%) |
| Exact / monotone duplicates of another adopted column | **22** (9.1%) |
| Unsafe as parity evidence | **24** (10.0%) |
| **Columns that cannot serve as parity evidence (union of the three)** | **67 (27.8%)** |
| Columns whose window is `lifetime` | **162 (67.2%)** |
| Columns on any trailing calendar window (2y / 5y / 365d / 730d) | **23 (9.5%)** |
| Columns measuring **sd**, **min**, **range (max−min)** or **EWM** | **0** |
| Columns measuring **amplitude (latest − earliest)** as the cube defines it | **0** |
| Columns measuring recency in **miles** that are not fabricated | **0** |
| Columns indicating **item-data availability** | **0** |

The gap is **not** where a first reading would put it. The factory blocks B1–B6 are
substantially clean: 43 of the 137 are `equivalent`, only 2 are `unsafe`, only 2 are
duplicates, and **none** is a constant except `b6_location_map_status` — 5 unusable columns
out of 137 (3.6%). Almost the whole information loss sits in **B0** — 20 constants, 20
duplicates and 22 unsafe columns out of 104, i.e. **62 of 104 serving features (60%) cannot
be used as parity evidence for any cube cell.**
The two blocks are complementary, not overlapping: where B0 is degenerate it is
degenerate *because the substrate cannot feed it* (no free text, no station table, no cohort
tables), and where B1–B6 is thin it is thin *because the statistic was never commissioned*
(no sd, no min, no range, no EWM, no mileage exposure, no item-data coverage).

---

## 1. Cube axes that are FULLY COVERED

These cells are populated by a column whose grain, window and semantics match the
commission, and which is safe to cite as parity evidence.

| cube cell | covering columns |
|---|---|
| **prior test × lifetime × count** | `b1_n_prior_test_days` (day grain, D13-safe), `b1_n_prior_tests` (record grain) |
| **prior test × lifetime × calendar duration** | `b1_history_years` |
| **prior test × lifetime × observable-window duration** | `b1_observable_years` + `b1_observable_years_status` |
| **prior test × lifetime × rate per observable year** | `b1_density_per_observable_year` (numerator **and** denominator both ship) |
| **prior test × trailing 24m / 60m × count** | `b1_n_prior_test_days_cap2y`, `b1_n_prior_test_days_cap5y` |
| **prior test × trailing 24m / 60m × calendar duration** | `b1_history_years_cap2y`, `b1_history_years_cap5y` (span to the earliest **in-window** prior, not `min(lifetime, N)` — falsifier F12) |
| **prior test × recency in days** | `b5_days_since_prior_day` |
| **prior test × cadence band** | `b5_gap_annual_band_flag` |
| **prior test × inter-occurrence gap × mean, max** | `b1_mean_gap_days`, `b1_max_gap_days` |
| **fail × lifetime × count and rate** | `n_prior_fails`, `prior_fail_rate_smoothed` (B0; record grain, all test types) |
| **fail × trailing 12m / 24m × count** | `fails_last_365d`, `fails_last_730d` — **the only trailing-12m columns in the whole 241** |
| **any-disposition item × family × affected-tests count** | `b2_{8 families}_n_days` |
| **any-disposition item × family × rate per observed test** | `b2_{8 families}_persistence` (both raw terms ship) |
| **latest observable test-day, all outcomes** | `b5_last_day_has_pass / _has_fail / _has_nonresult`, `b5_last_day_n_tests`, `b5_last_day_n_distinct_outcomes`, `b2_last_day_n_categories` — the D13-safe "latest" family |
| **PRS repair proxy × rate** | `b3_fail_item_rectified_share` (both raw terms ship) |
| **recurrence after apparent resolution** | `b4_n_recurrence_after_repair`, `b4_recurrence_categories`, `b4_days_since_recurrence` |
| **same-day multiset / D13 ambiguity exposure** | `b5_n_prior_multi_test_days`, `b5_max_tests_on_a_prior_day`, `b5_n_prior_days_pass_and_fail`, `b5_n_prior_nondefinitive_days`, `b5_n_prior_ambiguous_days` |
| **coverage: left-censored history** | `b1_left_censor_flag`, `b1_observable_years_status`, `b1_first_use_missing_flag` |
| **coverage: thin history** | `b1_history_coverage_grade` (none/left_censored/partial/full), `b1_n_prior_test_days` |
| **coverage: legacy vs current coding era** | `b3_n_days_fine_severity_observable`, `b3_severity_observability_status` |
| **coverage: taxonomy resolution** | `b2_n_catalogue_miss_items` |
| **coverage: COVID regime** | `b5_covid_straddle_flag` |
| **denominator honesty (slope)** | `b4_deterioration_slope_n_days` |
| **denominator honesty (positional)** | `b6_pos_n_total` |

**46 columns** carry `parity_class = equivalent`.

---

## 2. Cube axes that are PARTIALLY covered — and exactly what is missing

### 2.1 Event / outcome axes

| axis | state | what exists | what is missing |
|---|---|---|---|
| prior test | **covered** | day and record counts, windows, gaps | — |
| **pass** | **partial (1 column)** | `b5_last_day_has_pass` — presence on the latest day only | **No pass count, no pass rate, no pass streak, no days-since-pass.** `AsOfState.n_pass_days` and `last_pass_date` are already accumulated (`factory/state.py:147,155`) and **never emitted** |
| **fail** | **partial** | `n_prior_fails`, `prior_fail_rate_smoothed`, `fails_last_365d/730d`, `b5_last_day_has_fail` (B0 + B5) | **B1–B6 has no serving-safe fail column at all.** `b1_n_prior_final_fails` / `_initial_fails` are `research_only_input` (they read `test_type` in history, `blocks.py:88-90`). `AsOfState.n_fail_days` and `last_fail_date` exist (`state.py:146,154`) and are used **only** for the sampling stratum (`emit.py:539`), never emitted as a feature. So the day-grain fail history — the D13-safe form — is absent |
| PRS | **covered** | `b3_n_prs_items`, `b3_n_prs_item_days`, `b3_days_since_prs_item`, `b5_last_day_has_prs` | window variants; `b5_last_day_has_prs` is research-only (SERVE_VIEW invariant 3) |
| **advisory** | **partial** | `b3_n_advisory_items` (lifetime item count, the *only* advisory column in B1–B6); `prev_adv_{brakes,suspension,steering,tyres}`, `multi_system_advisory_count`, and the 3-component ladder in B0 | **No advisory DAYS count, no advisory recency, no advisory window, no per-canonical-family advisory in B1–B6.** `state.cat_ever_advisory` (`state.py:211`) is a boolean that feeds B4 only — the per-family advisory *count* and *day count* are never accumulated |
| major / dangerous / minor | **partial** | `b3_n_{dangerous,major,minor}_{items,days}` + `b3_days_since_*` | **no window variants, no per-family split, no severity-weighted composite** (see §6) |
| canonical defect family (8) | **partial** | days, days-since, longest run, persistence, 2y/5y day counts | **no per-family item count, no per-family severity, no per-family advisory, no per-family fail** |
| **subsystem / parent family (26 sections)** | **ABSENT** | — | The 26 top-level sections are resolved in the atom and carried in the packets payload as `sect` (`factory/packets.py:26`) but **no column counts them** |
| **exact defect item / code (rfr_id)** | **ABSENT** | `b2_n_catalogue_miss_items` only | No code-grain count, no distinct-code count, no per-code recency |

### 2.2 History windows

| window | state | columns |
|---|---|---|
| lifetime | **covered** | 162 of 241 |
| trailing 12 months | **partial (2)** | `fails_last_365d`, `recent_fail_intensity` (its exact copy) — fail axis only |
| trailing 24 months | **partial (11)** | `b1_*_cap2y` (×3), `b2_{7 canonical}_n_days_cap2y`, `fails_last_730d` |
| **trailing 36 months** | **ABSENT** | — |
| trailing 60 months | **partial (10)** | `b1_*_cap5y` (×3), `b2_{7 canonical}_n_days_cap5y` |
| last 1 test | **partial (3)** | `advisory_in_last_1_{brakes,tyres,suspension}` only |
| last 2 tests | **partial (3)** | `advisory_in_last_2_{brakes,tyres,suspension}` only |
| last 3 tests | **partial (3)** | `b4_burden_mean_last3`, `b4_burden_delta_1`, `b4_burden_delta_2` — burden only |
| **last 5 tests** | **ABSENT** | — |

**Window support** (the cube requires each window to retain its own support):

| support quantity | state |
|---|---|
| observable test count | **covered** — `b1_n_prior_test_days_cap2y/cap5y` |
| calendar duration observed | **covered** — `b1_history_years_cap2y/cap5y` |
| **mileage exposure** | **ABSENT at every window, including lifetime** |
| **item-data coverage** | **ABSENT at every window** (see §5 — this is the most serious gap) |
| left-censoring flag | **partial** — lifetime only (`b1_left_censor_flag`); no per-window flag for "this window extends before first observability" |

### 2.3 Statistics

| statistic | state | evidence |
|---|---|---|
| presence | **covered** | 30 columns |
| count | **covered** | 90 columns |
| proportion / rate | **covered** | 25 columns |
| **mean** | **partial (2)** | `b1_mean_gap_days`, `b4_burden_mean_last3` — nothing else has a mean |
| **sd** | **ABSENT (0)** | no standard deviation of any quantity, anywhere |
| **min** | **ABSENT (0)** | no minimum of any quantity over time |
| **max** | **partial (2)** | `b1_max_gap_days`, `b5_max_tests_on_a_prior_day`. (`mech_decay_index` is a max across four *features*, not over time) |
| **range (max − min) — VOLATILITY** | **ABSENT (0)** | see §6 |
| **amplitude (latest − earliest) — DIRECTION** | **ABSENT (0)** | see §6 |
| recent linear / robust slope | **partial (1)** | `b4_deterioration_slope` is fitted over the **whole observed span** (`state.py:305-313`), not a recent window; no robust variant; the span denominator is not shipped (only `n`) |
| **EWM** | **ABSENT (0)** | — |
| latest | **covered (8)** | the `b5_last_day_*` family + `b2_last_day_n_categories` + `b4_mileage_band` |
| **latest − lifetime mean** | **ABSENT** | `b4_burden_delta_1` is latest − *second latest*, not latest − mean |
| **recent-window rate − lifetime rate** | **ABSENT** | `fail_rate_trend` compares *last 2 tests* against *tests 3–4*, in counts, never against lifetime. The 2y/5y-vs-lifetime difference is derivable from shipped pairs but is not emitted |
| longest streak | **partial (8)** | `b2_{family}_max_run` — but over **any disposition**, so it is neither an advisory streak nor a failure streak; and it counts consecutive *observed test-days*, so a skipped year does not break a run |
| current streak | **partial (6)** | `advisory_streak_len_*`, `failure_streak_len_*` — 3 components only, record grain. `state.cat_run` (`state.py:209`) holds the current run per family and is never emitted |

### 2.4 Recency and cadence

The commission requires recency **simultaneously** in days, intervening tests and miles.

| recency dimension | state |
|---|---|
| **days** | **covered (19 columns)** — `b2_*_days_since` ×8, `b3_days_since_*` ×5, `b4_days_since_*` ×2, `b5_days_since_prior_day`, plus 3 B0 |
| **intervening tests** | **partial (6 columns)** — `tests_since_last_{advisory,failure}_{brakes,tyres,suspension}` only. **No B1–B6 category, severity, outcome or family has an intervening-tests recency** |
| **miles** | **ABSENT in substance** — the only two columns on this axis, `miles_since_last_advisory_{tyres,suspension}`, are **fabricated**: the value is `min(8000, test_mileage // n_prior_tests)` (`feature_engineering_v55.py:387-393`) |

Cadence:

| quantity | state |
|---|---|
| mean time between occurrences | **partial** — `b1_mean_gap_days`, for the prior-test event only |
| max time between occurrences | **partial** — `b1_max_gap_days`, prior-test only |
| **sd / min time between occurrences** | **ABSENT** |
| **mileage gaps between occurrences** | **ABSENT** — `state.prev_valid_mileage` and `prev_valid_mileage_date` (`state.py:176-177`) are accumulated and never emitted |
| time since **first** occurrence | **partial** — `b1_first_prior_date` / `b1_history_years` for the prior-test event only; no other event ships a first-occurrence marker |
| time since **latest** occurrence | **covered** (days only) |
| **frequency acceleration / deceleration** | **ABSENT** — no column compares a recent cadence against an older one |

### 2.5 Exposure normalisation

| normaliser | state |
|---|---|
| per observed test | **covered** — `b2_*_persistence` ×8, `prior_fail_rate_smoothed`, `historic_negligence_ratio_smoothed` (clipped — see §4) |
| per year of observable history | **covered** — `b1_density_per_observable_year`, `b1_opportunity_adjusted_density` |
| **per 1,000 miles** | **ABSENT (0 columns)** — there is no mileage-normalised statistic of any kind |
| raw numerator **and** denominator alongside every ratio | **partial** — holds for `b2_*_persistence`, `b1_density_per_observable_year`, `b3_fail_item_rectified_share`, `prior_fail_rate_smoothed`. **Fails for two:** `b1_opportunity_adjusted_density` (its `testable_years` denominator is computed at `blocks.py:281-288` and never emitted) and `b4_deterioration_slope` (ships `n`, not the span) |

### 2.6 Persistence / transition / repair proxies

| proxy | state |
|---|---|
| **advisory repeated at the NEXT test** | **ABSENT** — `b2_*_max_run` is any-disposition, not advisory |
| advisory becoming a failure in the same family | **partial, different semantics** — `b4_n_adv_to_fail_transitions` fires when the family carried an advisory on **some strictly earlier day** (`state.py:430`), not on the immediately preceding test |
| **repeated failure (no intervening pass)** | **ABSENT** — only the fail→pass→fail form is counted |
| **defect disappearance at the next observable test** | **ABSENT** |
| recurrence after apparent resolution | **covered** — `b4_n_recurrence_after_repair` (+2) |
| **failure followed by a short-interval pass / PRS** | **ABSENT** — `b5_n_prior_days_pass_and_fail` is same-day only |
| current & longest **pass / fail / advisory** streaks | **partial** — current advisory & failure streaks for 3 components; longest run for 8 families but any-disposition. **No pass streak, no test-level fail streak, no longest advisory or failure streak** |
| **proportion of prior advisories that recur** | **ABSENT** |

### 2.7 Coverage and censoring

| indicator required | state |
|---|---|
| **item-data availability** | **ABSENT** — see §5 |
| **years and tests with observable item data** | **ABSENT** |
| **first observable item-level MOT** | **ABSENT** |
| legacy / current coding era | **covered** — `b3_n_days_fine_severity_observable`, `b3_severity_observability_status` |
| thin history | **covered** — `b1_history_coverage_grade`, `b1_n_prior_test_days` |
| left-censored history | **covered** — `b1_left_censor_flag`, `b1_observable_years_status`, `b1_first_use_missing_flag` |
| unreliable / discontinuous mileage | **partial** — `b5_n_prior_mileage_conflict_days` detects **same-day** conflicts only (`atoms.py:232-234`); `mileage_anomaly_flag` / `mileage_plausible_flag` cover the **two most recent records** only, and are themselves unit-ambiguous. **Cross-test rollback is detected nowhere**, although `state.prev_valid_mileage` is available |

---

## 3. Cube axes that are ENTIRELY ABSENT

1. **Statistics:** standard deviation; minimum; **range (max − min)**; **amplitude (latest − earliest)**; EWM; latest-minus-lifetime-mean; recent-rate-minus-lifetime-rate.
2. **Windows:** trailing 36 months; last 5 tests.
3. **Exposure:** per-1,000-miles normalisation; mileage exposure as a window support.
4. **Recency:** genuine recency in miles; mileage gaps between occurrences; frequency acceleration.
5. **Coverage:** item-data availability; years/tests with observable item data; first observable item-level MOT.
6. **Event grains:** the 26-section subsystem grain; the rfr-code grain; severity-weighted burden.
7. **Persistence:** advisory-repeated-next-test; repeated failure without an intervening pass; defect disappearance; failure→short-interval-pass; proportion of advisories that recur.
8. **Pass as an event:** count, rate, recency and streak.

**Free-to-close subset.** Nine quantities are already computed inside `AsOfState` and thrown
away at emit time. Closing them requires **no new scan, no new atom and no new semantics** —
only additional entries in `blocks.py`:

| accumulator | line | cube cell it would fill |
|---|---|---|
| `n_fail_days`, `last_fail_date` | `state.py:146,154` | fail × lifetime × count / recency, **at day grain and serving-safe** |
| `n_pass_days`, `last_pass_date` | `state.py:147,155` | pass × lifetime × count / recency |
| `n_definitive_tests` | `state.py:134` | denominator honesty for the record-grain counts |
| `prev_valid_mileage`, `prev_valid_mileage_date` | `state.py:176-177` | mileage delta, mileage exposure, per-1,000-mile normalisers, mileage recency |
| `n_mileage_days` | `state.py:178` | mileage-data coverage indicator |
| `last_day_n_items` | `state.py:170` | latest burden (`b4_burden_delta_1` and `b4_burden_mean_last3` are already built from it) |
| `cat_run[key]` | `state.py:209` | current per-family run, the missing twin of `b2_*_max_run` |
| `min_valid_mileage_day` | `atoms.py:230` | staged in the atom, consumed only by the conflict test |
| `cat_{key}_n` per-day item counts | `atoms.py:64` | per-family **recorded-items** count (see §6) |

---

## 4. Columns that are UNSAFE or AMBIGUOUS — must NOT be used as parity evidence

**24 columns** carry `parity_class = unsafe_ambiguous`. Grouped by defect:

### 4a. Within-day order dependence (D13 violation) — 8 columns
`prev_cycle_outcome_band`, `gap_band`, `days_since_last_test`, `test_month`, `is_winter_test`,
`day_of_week`, `advisory_in_last_1_{brakes,tyres,suspension}`.
The module sorts the prior list by **date only**, stably (`feature_engineering_v55.py:221`),
then reads `tests[0]`. On any vehicle with a multi-test prior day the value depends on the
order duckdb happened to return — precisely what `FACTORY_CONTRACT.md:72-74` declares
unidentified. `b5_n_prior_multi_test_days` (max 12 in the adopted frame) bounds the exposed
population. D13-safe twins already exist for the outcome family
(`b5_last_day_has_pass` / `_has_fail`, `blocks.py:186-187`).

### 4b. Fabricated or sentinel-in-range values — 6 columns
- `miles_since_last_advisory_tyres`, `miles_since_last_advisory_suspension` — the value is
  `min(8000, test_mileage // n_prior_tests)`, not miles since anything (`FE:387-393`). These
  are the **only** two columns on the recency-in-miles axis.
- `days_late` (and its transform `mdps_score`) — the certificate expiry is **synthesised** as
  `p_date + 365 days` for PASSED priors only (`b0_module_runner.py:116-118`); the substrate
  has no expiry field. `0` therefore means "on time" **or** "no priors" **or** "the most
  recent prior failed".
- `annualized_mileage_v2`, `usage_band_hybrid` — in-range sentinels 10000 / `'average'` on
  every default path, with no distinguishing flag.
- `test_mileage` — in-range sentinel 50000; flagged by `has_prev_mileage`, but also
  unit-ambiguous (below).

### 4c. Era-dependent semantics — 4 columns
`test_mileage`, `b4_mileage_band`, `b4_burden_x_mileage_band_ord` (plus
`annualized_mileage_v2` / `usage_band_hybrid` already listed).
Pre-2022 `test_mileage` is unit-ambiguous — DVSA's own documentation says it "sometimes holds
a value that is actually kilometres", and the km→miles correction was applied only from the
2022 dataset; there is no unit column (`DATA_ASSESSMENT.md:170-172`). The B0 runner hardcodes
`odometer_unit="mi"` (`b0_module_runner.py:121`), so the module's km→mi branch never fires.
The `flat4y` training window (2020–2023) **straddles the correction**.
`FEATURE_DICTIONARY.md` calls `b4_mileage_band` "unit-robust by construction" — that claim is
correct about *mixing* (only one reading is used) but **the band assignment itself is
unit-dependent**: 80,000 km bands as `60k-100k` instead of `30k-60k`. This is a narrowing of
the dictionary's claim, recorded here as a finding.

### 4d. Information-destroying transforms — 1 column
`historic_negligence_ratio_smoothed` — clipped at 1.0 (`FE:815`), so every true rate ≥ 1
collapses onto one value (`prev_count_advisory` reaches 402 against `n_prior_tests` ≤ 48).
Nothing is "smoothed"; there is no shrinkage in the expression.

### 4e. Target-derived or proxy-risk lookups — 2 columns
- `high_risk_model_flag` — a hardcoded 20-model set selected **by fail rate on training data**
  (`FE:30-39`). A static target-derived lookup baked into serving code.
- `local_corrosion_index` — keyed on `tgt_pc`, the postcode area of the station that performed
  the **target** test (`blocks.py:76`, tagged `research_only_input`, not API-observable). It
  encodes station location, and therefore **station strictness**, not only climate. This is the
  station/geography proxy risk the commission asked to be flagged. Its transform
  `local_corrosion_delta` is redundant.

### 4f. Name asserts a mechanism the substrate cannot support — 3 columns
`text_leak_index`, `text_leak_index_log`, `has_leak_history` — the only text columns that vary,
and they vary because the keyword `leak` matches exactly one **section name**
("Noise, emissions and leaks", `pipeline/lake/rfr_mapping.py:68`). They measure section
membership, not a leak mechanism.

### 4g. Also disqualified as parity evidence (different class, same practical effect)

**21 constant columns** (`parity_class = absent`), measured on the adopted frame
`out/b0/b0_flat4y_eb.parquet` (n = 999,999) and `out/frames/recipe=flat4y/rung=r1m`:

| constant value | columns |
|---|---|
| `0.28` | `make_fail_rate_smoothed`, `model_fail_rate_smoothed`, `segment_fail_rate_smoothed` |
| `0.25` | `station_fail_rate_smoothed`, `station_x_prev_outcome_fail_rate` |
| `0.0` | `station_strictness_bias`, `suspension_risk_profile`, `advisory_cohort_delta`, `text_{corrosion,wear,damage}_index`, `text_{corrosion,wear,damage}_index_log`, `has_{corrosion,wear,damage}_history` |
| `1.0` | `mileage_cohort_ratio` |
| `'present'` | `b6_location_map_status` |
| degenerate (≤ 2 distinct values reachable) | `mechanism_count`, `dominant_mechanism` |

Cause, in each case, is the substrate rather than the code: `engineer_features` is called with
no `cohort_stats`, no `model_hierarchical`, no `segment_hierarchical`
(`b0_module_runner.py:136-138`); the v58 lake carries **no defect free text**, and the runner
supplies the catalogue **section name** as `defects[].text`
(`--defect-text-source section` on all six B0 builds in `queue.txt`), which matches no
corrosion, wear or damage keyword. `eb_fleet_builder` repairs only three columns
(`model_age_fail_rate_eb`, `make_age_fail_rate_eb`, `eb_unified_prior`) — the other three
fleet-rate columns stay at 0.28.

**22 duplicate columns** (`parity_class = duplicate_redundant`), each an exact or
monotone-deterministic function of another adopted column:
`raw_behavioral_count` = `prev_count_advisory` (`FE:831`);
`recent_fail_intensity` = `fails_last_365d` (`FE:422`);
`eb_unified_prior` = `model_age_fail_rate_eb` (`FE:725`);
`mech_decay_index_normalized` = `mech_decay_index` (no cohort table, `FE:564`);
`days_since_pass_ratio` = `days_since_last_test`/365; `mdps_score` = `days_late`/365;
`local_corrosion_delta` = `local_corrosion_index` − 0.5;
`severity_escalation_flag` = `1[fail_rate_trend>0]`;
`has_advisory_history` = `1[prev_count_advisory>0]`;
`max_severity_score` = f(`n_prior_fails`>0, `prev_count_advisory`>0);
`mech_decay_{brake,suspension,steering}` = `min(prev_adv_* × 0.2, 1)`;
`mech_decay_index`, `mech_risk_driver` = max/argmax of those;
`front_end_advisory_intensity` = sum of three shipped columns;
`brake_system_stress`, `commercial_wear_proxy` = closed-form sums of shipped columns;
`b4_burden_x_age` = product of two shipped columns;
`n_prior_tests` ≡ `b1_n_prior_tests` (**cross-block duplicate**);
`prev_count_advisory` ≡ `b3_n_advisory_items` (**cross-block duplicate**, both measured
`[0..402]` on `flat4y/r1m`).

---

## 5. THE PRINCIPAL SAFETY FINDING — item-data availability is not represented

> The commission's rule: *"a zero must NEVER ambiguously mean both 'event did not occur' and
> 'item-level information unavailable'."*

**This rule is violated at the atom, for every item-derived column in the audit.**

`vehicle_day_atom` LEFT JOINs the results relation to the per-test item aggregate
(`atoms.py:240`) and then folds a missing match to zero:
`coalesce(sum(a.n_items), 0)` and the same for every category and severity counter
(`atoms.py:140-144`). A prior test with **no item rows in the items extract** is therefore
byte-identical to a prior test that **genuinely recorded no defect**.

Affected columns: all 32 `b2_{family}_n_days` / `_n_days_cap*`, `b2_n_items_total`,
`b3_n_fail_items_initial` / `_final`, `b3_n_advisory_items`, `b4_burden_*`,
`b4_deterioration_slope`, and every B0 defect-derived feature — because they consume the same
`defects_json` payload.

The programme is otherwise scrupulous about this. `FEATURE_DICTIONARY.md` reading convention 4
("NULL is not zero") is honoured for **severity** (pre-2018 → NULL + `b3_severity_observability_status`),
for **position** (absent map → NULL + `b6_location_map_status`), and for **first-use**
(NULL + `b1_first_use_missing_flag`). There is **no equivalent for item-row availability**.
`b2_n_catalogue_miss_items` is a different quantity: it counts rows whose `rfr_id` failed to
resolve, not rows that were never there.

No column in the 241 answers "how many of this vehicle's prior tests have item data at all",
"over which years", or "what is the first prior test with item-level detail". Those three are
on the commission's required list and are **entirely absent**.

---

## 6. The two explicit call-outs

### 6.1 Range (max − min, VOLATILITY) vs amplitude (latest − earliest, DIRECTION)

**Neither exists. Zero columns of 241 measure either.**

- **Range / volatility:** there is no `min` of any quantity over time anywhere in the
  featureset, so `max − min` cannot be formed even downstream. The only maxima over time are
  `b1_max_gap_days` and `b5_max_tests_on_a_prior_day`; neither has a matching minimum. There is
  no standard deviation either, so volatility is unrepresented in every form.
- **Amplitude / direction:** the closest columns are **lag-1 differences**, not
  latest-minus-earliest:
  - `b4_burden_delta_1` = items(latest day) − items(second-latest day) (`blocks.py:167`)
  - `b4_burden_delta_2` = items(2nd-latest) − items(3rd-latest) (`blocks.py:168`)
  - `advisory_trend` = a 3-level *categorical* comparison of the two most recent priors, so even
    the magnitude is discarded (`FE:326-336`)
  - `fail_rate_trend` = fails(last 2 tests) − fails(tests 3–4) — a window-vs-window count
    difference, not an endpoint difference (`FE:414-419`)

  A lag-1 delta and a latest-minus-earliest amplitude coincide only for a 2-test history. For
  the modal vehicle (`b1_n_prior_test_days` reaches 48) they are different statistics, and the
  direction of travel across the window is not measured.

- **Slope is not a substitute:** `b4_deterioration_slope` is an OLS slope over the **whole**
  observed span (`state.py:305-313`), it has no recent-window variant, no robust variant, and
  its measured range (−4383 .. +2191) shows it blowing up on near-degenerate spans.

### 6.2 Item-history conflation: affected tests vs recorded items vs distinct families vs severity-weighted burden

The commission requires these four to be **distinct**. Status:

| quantity | state | evidence |
|---|---|---|
| **number of affected tests** | **covered at family grain** | `b2_{8 families}_n_days` (`blocks.py:121`); day grain, so "a test with many items" counts once |
| **number of recorded items** | **covered POOLED ONLY** | `b2_n_items_total` (`blocks.py:129`) is the total across all families. **There is no per-family item count.** The atom already computes `cat_{key}_n` per day (`atoms.py:64`), but `AsOfState.update` reads it only as a presence test — `day.cat_n(key) > 0` (`state.py:425`) — and never sums it. The per-family item counter is therefore **one line away** but does not exist |
| **number of distinct families / codes** | **partial** | `b2_breadth_categories` (8-category grain), `b2_last_day_n_categories`, `multi_system_advisory_count` (5 serving components, advisory only). **Distinct 26-sections and distinct rfr codes: absent** |
| **severity-weighted burden** | **ABSENT** | B3 emits `dangerous`, `major`, `minor` counts separately and post-2018 only; there is no weighted composite, no per-family severity split, and no severity-weighted per-test burden. `max_severity_score` (B0) is **not** a substitute: it is `2 / 1 / 0` from `(any fail, any advisory)` (`FE:532`) and never reads DANGEROUS/MAJOR/MINOR at all |

**Verdict on the conflation:** the *dangerous* direction of the conflation — treating repeated
failure across many tests as equivalent to one test with many items — is **correctly avoided**
in B2/B3, which count DAYS. The *opposite* leg is missing: at family grain the featureset cannot
distinguish "one test with five brake items" from "one test with one brake item", and
severity-weighted burden does not exist at any grain.

---

## 7. Per-axis coverage table

Counts are adopted columns supporting that axis; "evidence" cites the primary column family.

| axis | covered | partial | absent | n cols | evidence |
|---|:--:|:--:|:--:|--:|---|
| **EVENT: prior test** | ✔ | | | 26 | `b1_*` (`blocks.py:86-116`) |
| **EVENT: pass** | | ✔ | | 1 | `b5_last_day_has_pass` only; `state.n_pass_days` unemitted (`state.py:147`) |
| **EVENT: fail** | | ✔ | | 10 | B0 `n_prior_fails`/`fails_last_*`; B1 twins are research-only (`blocks.py:88-90`) |
| **EVENT: PRS** | ✔ | | | 4 | `b3_n_prs_*` (`blocks.py:151-153`) |
| **EVENT: advisory** | | ✔ | | 9+B0 | `b3_n_advisory_items` is the sole B1–B6 advisory column (`blocks.py:156`) |
| **EVENT: major/dangerous/minor** | | ✔ | | 9 | `b3_*` post-2018 only (`blocks.py:142-150`) |
| **EVENT: canonical family (8)** | | ✔ | | 46 | `b2_*` (`blocks.py:119-137`) |
| **EVENT: subsystem / 26 sections** | | | ✖ | 0 | present in `packets.defects_json.sect` only (`packets.py:26`) |
| **EVENT: exact item / rfr code** | | | ✖ | 0 | only `b2_n_catalogue_miss_items` (`blocks.py:130`) |
| **WINDOW: lifetime** | ✔ | | | 162 | — |
| **WINDOW: trailing 12m** | | ✔ | | 2 | `fails_last_365d` (`FE:410`) |
| **WINDOW: trailing 24m** | | ✔ | | 11 | `*_cap2y` (`blocks.py:107-116,132-137`) |
| **WINDOW: trailing 36m** | | | ✖ | 0 | `state.DEPTH_CAPS = ((2.0,…),(5.0,…))` (`state.py:43`) |
| **WINDOW: trailing 60m** | | ✔ | | 10 | `*_cap5y` |
| **WINDOW: last 1 / 2 tests** | | ✔ | | 6 | `advisory_in_last_{1,2}_*` (`FE:369,372`) |
| **WINDOW: last 3 tests** | | ✔ | | 3 | `b4_burden_*` (`blocks.py:167-169`) |
| **WINDOW: last 5 tests** | | | ✖ | 0 | `state.burden` is `deque(maxlen=3)` (`state.py:219`) |
| **SUPPORT: observable test count** | ✔ | | | 2 | `b1_n_prior_test_days_cap*` |
| **SUPPORT: calendar duration** | ✔ | | | 2 | `b1_history_years_cap*` |
| **SUPPORT: mileage exposure** | | | ✖ | 0 | — |
| **SUPPORT: item-data coverage** | | | ✖ | 0 | §5 |
| **SUPPORT: left-censoring flag** | | ✔ | | 3 | lifetime only (`blocks.py:95-98`) |
| **STAT: presence** | ✔ | | | 30 | — |
| **STAT: count** | ✔ | | | 90 | — |
| **STAT: rate / proportion** | ✔ | | | 25 | — |
| **STAT: mean** | | ✔ | | 2 | `b1_mean_gap_days`, `b4_burden_mean_last3` |
| **STAT: sd** | | | ✖ | 0 | — |
| **STAT: min** | | | ✖ | 0 | — |
| **STAT: max** | | ✔ | | 2 | `b1_max_gap_days`, `b5_max_tests_on_a_prior_day` |
| **STAT: range (max−min)** | | | ✖ | 0 | §6.1 |
| **STAT: amplitude (latest−earliest)** | | | ✖ | 0 | §6.1 — 4 lag-1 deltas only |
| **STAT: recent slope** | | ✔ | | 1 | `b4_deterioration_slope` is lifetime (`state.py:305-313`) |
| **STAT: EWM** | | | ✖ | 0 | — |
| **STAT: latest** | ✔ | | | 8 | `b5_last_day_*` (`blocks.py:184-189`) |
| **STAT: latest − lifetime mean** | | | ✖ | 0 | — |
| **STAT: recent rate − lifetime rate** | | | ✖ | 0 | `fail_rate_trend` is last2-vs-tests3-4 |
| **STAT: longest streak** | | ✔ | | 8 | `b2_*_max_run`, any-disposition (`blocks.py:123`) |
| **STAT: current streak** | | ✔ | | 6 | B0, 3 components (`FE:381,468`) |
| **RECENCY: days** | ✔ | | | 19 | `b2/b3/b4/b5 days_since*` |
| **RECENCY: intervening tests** | | ✔ | | 6 | `tests_since_last_*` (`FE:361,473`) |
| **RECENCY: miles** | | | ✖ | 0 | 2 fabricated columns (`FE:387-393`) |
| **CADENCE: mean/max gap** | | ✔ | | 2 | prior-test event only (`blocks.py:103-104`) |
| **CADENCE: sd/min gap** | | | ✖ | 0 | — |
| **CADENCE: mileage gaps** | | | ✖ | 0 | `state.prev_valid_mileage` unemitted (`state.py:176`) |
| **CADENCE: since first occurrence** | | ✔ | | 2 | prior-test only (`blocks.py:91,93`) |
| **CADENCE: freq. acceleration** | | | ✖ | 0 | — |
| **EXPOSURE: per observed test** | ✔ | | | 11 | `b2_*_persistence` + 3 |
| **EXPOSURE: per observable year** | ✔ | | | 2 | `blocks.py:100-101` |
| **EXPOSURE: per 1,000 miles** | | | ✖ | 0 | — |
| **EXPOSURE: numerator+denominator ship** | | ✔ | | — | fails for `b1_opportunity_adjusted_density`, `b4_deterioration_slope` |
| **PERSIST: advisory repeats next test** | | | ✖ | 0 | — |
| **PERSIST: advisory → fail same family** | | ✔ | | 3 | semantics = "ever advisory before" (`state.py:430`) |
| **PERSIST: repeated failure** | | | ✖ | 0 | only fail→pass→fail |
| **PERSIST: defect disappearance** | | | ✖ | 0 | — |
| **PERSIST: recurrence after resolution** | ✔ | | | 3 | `blocks.py:164-166` |
| **PERSIST: fail → short-interval pass** | | | ✖ | 0 | — |
| **PERSIST: pass/fail/advisory streaks** | | ✔ | | 14 | §2.3 |
| **PERSIST: share of advisories recurring** | | | ✖ | 0 | — |
| **COVERAGE: item-data availability** | | | ✖ | 0 | §5 |
| **COVERAGE: years/tests with item data** | | | ✖ | 0 | §5 |
| **COVERAGE: first item-level MOT** | | | ✖ | 0 | §5 |
| **COVERAGE: coding era** | ✔ | | | 2 | `blocks.py:140-141` |
| **COVERAGE: thin history** | ✔ | | | 2 | `blocks.py:96` |
| **COVERAGE: left-censored** | ✔ | | | 3 | `blocks.py:95-98` |
| **COVERAGE: unreliable mileage** | | ✔ | | 4 | same-day / 2-record scope only |

**Totals across the 65 audited cube axes: 16 covered · 23 partial · 26 absent.**

---

## 8. Findings that are not gaps but affect how parity may be claimed

1. **Train / eval EB semantics differ.** `model_age_fail_rate_eb`, `make_age_fail_rate_eb` and
   `eb_unified_prior` are **expanding, strictly-earlier-day** in the training frame
   (`out/b0/b0_flat4y_eb.parquet.manifest.json → as_of_rule`) but **frozen at 2023-12-31** in
   the eval, confirm and drift frames (`out/b0/b0_eval2024_eb.parquet.manifest.json`). One
   column name, two estimators. Deliberate (frozen matches serving), but any parity claim about
   these three must name which estimator it refers to.
2. **`b1_n_prior_initials`, `b1_n_prior_final_fails`, `b1_n_prior_initial_fails`,
   `b1_n_prior_initials_cap2y/cap5y`, `b5_last_day_has_prs` are `research_only_input`**
   (`blocks.py:88-90,113,188`) — they consume `test_type` in history or PRS visibility that
   serving does not have. They may not be cited as serving parity evidence for any cube cell.
   The whole of **B6 (14 columns)** is likewise research-only.
3. **`b3_days_since_{dangerous,major,minor}` merge two NULL meanings** — "never happened" and
   "era-unobservable" (`blocks.py:366,369,372`). `b3_severity_observability_status` resolves it,
   so the pair is safe *together*; a single-column parity claim is not.
4. **Reconstructible-by-design columns.** `b2_{cat}_days_since_capNy` were deliberately dropped
   (FEATURE_DICTIONARY ruling 6) and are exactly recoverable as
   `b2_{cat}_days_since WHERE < 731 / 1826`. Not a gap.
5. **`b1_opportunity_adjusted_density` reaches 2556.75** and `b4_deterioration_slope` spans
   −4383 .. +2191 on the adopted frame — both are near-zero-denominator blow-ups. Neither is
   incorrect, but both are heavy-tailed inputs for the non-tree challengers (RealMLP, the
   context models), which see them as raw float64 (`fit_contract.py:267-268`).

---

## 9. Suggested Phase-1 probes (cheap, run after any rebuild)

These are **verification** probes, not new features. Each is a single projected query.

1. **Duplicate identity.** Assert `n_prior_tests == b1_n_prior_tests` and
   `prev_count_advisory == b3_n_advisory_items` row-for-row on one frame shard. Both pairs match
   on range; identity is claimed from code and should be confirmed. Also test
   `n_prior_fails == b1_n_prior_final_fails` — the predicates genuinely differ (`state.py:344`
   requires `test_type=='NT'`, `FE:398` does not), so a match would mean retest failures are
   effectively absent, and a mismatch quantifies them.
2. **Recency divergence.** Confirm `days_since_last_test != b5_days_since_prior_day` on the
   majority of rows, and that `days_since_last_test == b1_max_gap_days` for single-gap
   histories. Their measured maxima already differ (5916 vs 6450).
3. **D13 order sensitivity.** Restrict to `b5_n_prior_multi_test_days > 0` and report the share
   of rows; that is the exact exposure of the eight order-dependent B0 columns (§4a).
4. **Constant sweep.** Assert zero-variance for the 21 columns listed in §4g, and fail the build
   if any *other* adopted column becomes constant. Cheap from parquet row-group statistics.
5. **Item-data blindness.** Count prior tests with `p_n_items IS NULL` versus `p_n_items = 0` in
   the packets view (`packets.py:158`) — the packets layer still distinguishes them, the day
   atom does not (`atoms.py:140`). That single number sizes the §5 gap before any new column is
   specified.

---

## 10. Where the gap is smaller than expected — reported honestly

- **Trailing-window fail counts already exist.** The cube's "fail × trailing 12/24 months ×
  count" cells are populated by `fails_last_365d` / `fails_last_730d`. I expected these to be
  absent.
- **Both fail bases already ship.** `b3_n_fail_items_initial` (F+P) and `_final` (F) mean no
  basis re-derivation is needed for the item axis.
- **The affected-tests vs repeated-failure conflation is already avoided** in B2/B3 by counting
  DAYS, which is the harder half of the commission's requirement.
- **Coverage/censoring is well served on three of its seven required indicators**
  (left-censoring, thin history, coding era) with explicit status columns, not flags smuggled
  into zeros.
- **Exposure denominators mostly ship** — 4 of 6 ratios carry both raw terms.
- **B1–B6 has almost no dead weight**: 2 duplicates (`b1_n_prior_tests`, `b4_burden_x_age`),
  1 constant (`b6_location_map_status`), 2 unsafe (`b4_mileage_band`,
  `b4_burden_x_mileage_band_ord`) out of 137 — 3.6%. The 27.8% programme-level unusable share
  is overwhelmingly a B0 property (62 of the 67).

---

*R1, parity audit. No columns proposed — specification of new columns is R2's remit. No fits
run; no lake scans issued; parquet metadata only.*
