# CONSUMED-MATRIX RECONCILIATION — R6 Gate 3

**Scope**: independent verification of R1's classification of 67 of the 241 adopted columns as
unusable (21 `absent`, 22 `duplicate_redundant`, 24 `unsafe_ambiguous`), measured against the
feature matrix the fitted models actually consume.
**Companion**: `CONSUMED_MATRIX_VERDICTS.csv` (101 rows — the 67 plus 34 columns R1 passed as usable).
**Constraint honoured**: no model fits were run; `queue.txt` untouched. All numbers below are DuckDB
aggregates over the materialised frames with `memory_limit ≤ 1500MB` and column projection.

---

## 0. What the learner is actually handed

R1's audit was checked against the consumption path, not the declarations.

- `fit_runner.py:603-604` resolves the column list via `fit_contract.resolve_featureset`, which
  expands block tokens and appends `extra_columns` verbatim (`fit_contract.py:176-191`).
- `fit_contract.py:215-218` **raises** on any requested column absent from both frames. There is no
  silent drop.
- **There is no constant-column filter, no variance threshold and no de-duplication anywhere in the
  fit path.** Every resolved column is materialised into `Frame.features`
  (`fit_contract.py:231-238`) and emitted by `Frame.matrix()` (`fit_contract.py:149-161`) into the
  learner.

**Finding 0.1 — R1's "declared but dropped" category does not exist here.** Resolving all 46 configs
in `out/configs/` shows all 67 disputed columns are fed to at least one fitted cell; 42 of the 46
configs feed the full 241. A column measured as constant *is* a constant column fed to the model.

**Finding 0.2 — R1's coverage is exactly right.** The resolved featureset is 241 columns; the audit
has 241 rows; the symmetric difference is empty. No column is fed unaudited.

**Finding 0.3 — no shadowing, no join inflation.** Base-frame and B0-extra column names are disjoint
(0 overlap), so the base-wins rule at `fit_contract.py:213,220` never fires. The
`LEFT JOIN … ON x.test_id = f.tgt_id` (`fit_contract.py:214`) matches 999,999/999,999 on flat4y/r1m
and 330,665/330,665 on eval2024 — no unmatched rows, no row multiplication.

**Partitions checked** (every relevant one, not a sample): B0 extras `b0_flat4y_eb` (999,999),
`b0_post2018_eb` (1,375,079), `b0_fullpop_eb` (1,004,208), `b0_eval2024_eb` (330,665),
`b0_eval2024_eb_post2018`, `b0_eval2024_eb_fullpop`; base frames `flat4y` r250k/r1m, `flat2y` r1m,
`post2018` r1m, `fullpop` r1m, `eval2024`. The ES/validation slice is a *vehicle-clustered random*
subset of the training frame (`fit_runner.py:526-537`), so train-constant implies ES-constant; the
load-bearing axis is train vs eval, and that is what was measured.

---

## 1. P0 — `frames_fullpop` has no defect data at all (train/serve skew, 53 columns)

This is the most important result of the gate and it is **not** in R1's audit as a skew.

### Evidence

```
packets defects_json fill, all built packet sets
out/frames/recipe=flat4y/rung=r1m/packets          rows= 7,845,433  non-null= 3,894,170
out/frames/recipe=post2018/rung=r1m/packets        rows=10,661,527  non-null= 5,263,278
out/frames_eval/recipe=eval2024/rung=all/packets   rows= 2,866,158  non-null= 1,459,838
out/frames_confirm/…/packets                       rows= 1,501,242  non-null=   800,816
out/frames_drift/…/packets                         rows= 1,230,816  non-null=   651,396
out/frames_fullpop/recipe=fullpop/rung=r1m/packets rows= 5,560,040  non-null=         0   <<<
```

`frames_fullpop` is the only packet set in the programme with `defects_json` 100% NULL.

### Mechanism, traced to source

1. `queue.txt:167` (`B20_BUILD_FULLPOP_1M`) is the **only** build invocation passing
   `--defect-detail counts`. Every other build takes the default.
2. `factory/build.py:112` — `--defect-detail` choices `("rows","counts","none")`, **default `rows`**.
3. `factory/emit.py:549` — `include_defects=(cfg.defect_detail == "rows")` → `False` for `counts`.
4. `factory/packets.py:163` — `"defects_json": defects_json(prior.defects) if include_defects else None`
   → NULL for all 5,560,040 prior rows.
5. `queue.txt:168` then runs `b0_module_runner … --defect-text-source section` over those NULL
   packets. The runner json-decodes `raw` to `[]` when falsy (`b0_module_runner.py:107`) and the B0
   module returns **zeros**, not NULLs, for every defect-derived feature. The mismatch is silent —
   nothing asserts that a packet set built with `counts` is incompatible with a `section` text source.

Note B3 is unaffected: `b3_n_advisory_items` sums to 5,425,388 on the fullpop base frame (max 192),
because B3 is emitted from the item stream rather than from `defects_json`. So the frame *looks*
healthy on a spot check — the defect signal is present in B1–B6 and absent only in B0.

### Blast radius

**51 columns are constant on the fullpop training frame while varying on the eval frame that cell is
paired with** (`b0_eval2024_eb_fullpop`, which does carry defects), plus **2 more lose categorical
levels** — 53 in total:

- `dominant_mechanism` — the `LEAK` level is **absent** from fullpop training (`CLEAN` 92.373%,
  `NO_HISTORY` 7.627%) but present on **9.809%** of its eval rows. A level the model can never have learned.
- `advisory_trend` — 2 levels in fullpop training vs 4 at eval.
- `mechanism_count` — 100.000% zero in fullpop training vs 9.809% ones at eval.
- `prev_count_advisory`, `raw_behavioral_count`, `prev_adv_{brakes,steering,suspension,tyres}`,
  the 3 `advisory_in_last_1_*`, 3 `advisory_in_last_2_*`, 3 `advisory_streak_len_*`,
  3 `has_prior_advisory_*`, 3 `has_prior_failure_*`, 3 `has_ever_failed_*`, 3 `failure_streak_len_*`,
  3 `tests_since_last_advisory_*`, 3 `tests_since_last_failure_*`, 2 `miles_since_last_advisory_*`,
  6 `mech_decay_*` + `mech_risk_driver`, `multi_system_advisory_count`, `front_end_advisory_intensity`,
  `has_advisory_history`, `historic_negligence_ratio_smoothed`, `negligence_band`,
  `text_leak_index`, `text_leak_index_log`, `has_leak_history`.

**Only 30 of these 51 fall inside R1's disputed 67; the other 30 R1 classified as usable
(`partial`/`different_window_or_semantics`).**

### Why this matters now

`out/configs/s2.D.confirm.final.json` is the only cell using `extra_frame=b0_fullpop_eb` with
`extra_eval_frame=b0_eval2024_eb_fullpop`. It is the **final full-grade confirmation model**, the
only one carrying `--save-model`, and it is the input to `B22B_DRIFT_SCORE` (2025-H1 drift),
`B23_SEALED_READ` (the sealed 2025-H2 read) and `B24B_SHAP` (`queue.txt:174-176, 183, 188, 194`).

**It has not been fitted yet** — no `s2.D.confirm.final*` output exists in `out/fits/`, and all four
consuming lines are still `# HELD-OWNER-SELECTION`. The defect is caught before it can contaminate
the sealed read. Had it run, ~21% of the adopted featureset would have been hard-zero in training
and live at evaluation, and the sealed 2025-H2 confirmation would have been scored by a model
fitted on a substrate that never existed.

---

## 2. Verified verdicts for R1's 67

| R1 class | n | Verified outcome |
|---|---|---|
| `absent` (measured-constant) | 21 | 19 `CONFIRMED_CONSTANT`, 2 `PARTITION_DEPENDENT` |
| `duplicate_redundant` | 22 | 21 `CONFIRMED_DUPLICATE`, 1 `NOT_DUPLICATE` |
| `unsafe_ambiguous` | 24 | 21 `CONFIRMED_UNSAFE`, 3 `PARTITION_DEPENDENT` |

### 2.1 Constants — 19 confirmed

`n_distinct = 1` with 0 NULLs on **every** partition fed (flat4y 999,999 / post2018 1,375,079 /
fullpop 1,004,208 / eval2024 330,665; `b6_location_map_status` on all 5 base partitions):

| value | columns |
|---|---|
| `0.28` | `make_fail_rate_smoothed`, `model_fail_rate_smoothed`, `segment_fail_rate_smoothed` |
| `0.25` | `station_fail_rate_smoothed`, `station_x_prev_outcome_fail_rate` |
| `1.0` | `mileage_cohort_ratio` |
| `0.0` | `advisory_cohort_delta`, `station_strictness_bias`, `suspension_risk_profile`, `text_corrosion_index(_log)`, `text_wear_index(_log)`, `text_damage_index(_log)`, `has_corrosion_history`, `has_wear_history`, `has_damage_history` |
| `'present'` | `b6_location_map_status` |

R1's specific claims verified: both station rates pinned at **0.25**, three fleet rates at **0.28**,
`mileage_cohort_ratio ≡ 1.0`, `b6_location_map_status ≡ 'present'`, and
`text_{corrosion,wear,damage}_*` constant — all **CONFIRMED**.

These 18 zero/near-zero constants are also mutually exact-equal (a degenerate consequence of being
constant), which is why the duplicate scan returns 76 pairs; only the non-degenerate ones are
reported as duplicates below.

### 2.2 Constants — 2 NOT confirmed, and dangerous

`mechanism_count` and `dominant_mechanism` are **`PARTITION_DEPENDENT`, not absent**. R1's
measurement holds only on `fullpop`; on the dominant flat4y partition (42 of 46 configs) and at
eval2024 both vary. See §1. `mechanism_count` is additionally exact-equal to `has_leak_history`
on all 5 partitions.

### 2.3 Duplicates — 21 confirmed, 1 refuted

All equality measured row-level, NULL-safe (`IS DISTINCT FROM`), on the consumed frames — and for
cross-block pairs, through the exact join the fitter uses.

**Exact identity (0 differing rows):**

| column | equals | partitions |
|---|---|---|
| `recent_fail_intensity` | `fails_last_365d` | flat4y, eval2024, pre-EB flat4y |
| `raw_behavioral_count` | `prev_count_advisory` | flat4y, eval2024, pre-EB flat4y |
| `eb_unified_prior` | `model_age_fail_rate_eb` | flat4y, eval2024, **and post-EB-swap** |
| `mech_decay_index_normalized` | `mech_decay_index` | flat4y, eval2024, pre-EB flat4y |
| `n_prior_tests` | `b1_n_prior_tests` | flat4y/r1m, eval2024 (cross-frame) |

The four named pairs in the brief — `raw_behavioral_count`=`prev_count_advisory`,
`recent_fail_intensity`=`fails_last_365d`, `eb_unified_prior`=`model_age_fail_rate_eb`,
`n_prior_tests`≡`b1_n_prior_tests` — are all **CONFIRMED**. Note `eb_unified_prior` survives the EB
fleet swap: `eb_fleet_builder` replaces both members of the pair, and they remain identical.

**Exact deterministic functions of fed columns (not identity, still redundant; 0 differing rows
unless noted):** `days_since_pass_ratio` (=`days_since_last_test`/365), `mdps_score`
(=`days_late`/365), `local_corrosion_delta` (=`local_corrosion_index`−0.5), `mech_decay_{brake,
suspension,steering}` (=`min(prev_adv_*·0.2, 1)`, saturating so many-to-one), `mech_decay_index`
(=max of the four), `mech_risk_driver` (argmax label), `front_end_advisory_intensity` (=steering+
suspension+tyres), `brake_system_stress`, `max_severity_score`, `severity_escalation_flag`,
`has_advisory_history`, `b4_burden_x_age`, and `commercial_wear_proxy` (equal to 1e−9; its 165,255
bitwise differences are float-association only, max |diff| = 0.0).

**`prev_count_advisory` — `NOT_DUPLICATE`.** R1 paired it with `b3_n_advisory_items` on
*range corroboration* ("both range [0..402]"), which does not establish equality. Measured through
the fitter's own join:

```
flat4y/r1m : 482 differing rows (0.048%), max |diff| = 6
eval2024   : 277 differing rows (0.084%), max |diff| = 6
```

The gap is the `'advisory' in str(defect)` escape clause (`feature_engineering_v55.py:308`), which
also catches the *Non-component advisories* section that B3 excludes. Small, but real: the two
columns are not interchangeable, and `prev_count_advisory` cannot be dropped in favour of B3 without
losing those rows. It *is* the exact parent of `raw_behavioral_count`.

### 2.4 Unsafe — 21 confirmed, 3 reclassified

**D13 / within-day ordering.** Confirmed at source:

```python
# autosafe/feature_engineering_v55.py:221
tests = sorted(tests, key=lambda t: t.test_date, reverse=True)
```

A **date-only** key. Python's sort is stable, so ties preserve packet order — which the programme
declares unidentified (`FACTORY_CONTRACT.md:72-74`). `tests[0]` is therefore an arbitrary pick among
same-day priors.

**Correction to the brief: this is not 8 columns.** R1's own audit already tags **18** columns with
this `asof_rule`, and the code trace finds more. Direct `tests[0]` / `tests[:1]` readers:
`test_month`, `is_winter_test`, `day_of_week` (`:224,230-232`); `prev_cycle_outcome_band` (`:245`);
`gap_band`, `days_since_last_test` (`:257-259`); `test_mileage`, `has_prev_mileage`,
`mileage_plausible_flag`, `mileage_anomaly_flag` (`:266-289`); `advisory_trend` (`:327-328`);
`advisory_in_last_1_{brakes,tyres,suspension}` (`:368-369`); `annualized_mileage_v2` (`:598-603`);
`usage_band_hybrid`, `days_late`, and the transforms `days_since_pass_ratio`, `mdps_score`.
A further ~15 columns depend on the same ordering through list iteration rather than index 0
(`advisory_in_last_2_*` via `tests[:2]` at `:371-372`, `tests_since_last_advisory_*` via `enumerate`
at `:360`, `advisory_streak_len_*`, `failure_streak_len_*`) — R1 classifies all of those as usable.

**Measured D13 exposure** (share of targets whose most recent prior *day* carries ≥2 prior records,
so `tests[0]` is genuinely ambiguous):

| partition | targets with priors | tied top day | share |
|---|---|---|---|
| flat4y r1m | 923,605 | 86,955 | **9.415%** |
| post2018 r1m | 1,262,423 | 121,977 | **9.662%** |
| eval2024 | 312,159 | 27,572 | **8.833%** |

Not an edge case: roughly one training row in eleven.

**Semantic defect found alongside D13**: `test_month`, `is_winter_test` and `day_of_week` are the
month/weekday of the **latest prior test**, not of the target test being predicted
(`:224,230-232`; `prediction_date` is used only when there are no priors). The names assert the
opposite. That is a naming/semantics fault independent of the ordering fault.

**Sentinel mass — measured, not asserted:**

| column / sentinel | train (flat4y) | eval2024 |
|---|---|---|
| `usage_band_hybrid` = `'average'` | 51.949% | 49.242% |
| `days_late` = 0 | 60.143% | 61.003% |
| `historic_negligence_ratio_smoothed` = 1.0 (clip) | 30.430% | 35.189% |
| `annualized_mileage_v2` = 10000 | 29.416% | 23.846% |
| `miles_since_last_advisory_tyres` = 8000 (cap) | 28.579% | 30.159% |
| `miles_since_last_advisory_tyres` = 0 | 43.975% | 36.012% |
| `test_mileage` = 50000 | 8.614% | 5.906% |
| `high_risk_model_flag` = 1 | 1.483% | 0.814% |

`miles_since_last_advisory_{tyres,suspension}` confirmed **fabricated** at source — the value is
`min(8000, test_mileage // max(len(tests),1))` (`:385-393`), where 8000 is a hardcoded "average UK
annual mileage"; the expression contains no reference to when the last advisory occurred. ~72% of
rows sit on one of two constants.

`historic_negligence_ratio_smoothed` is clipped at 1.0 with **no shrinkage term anywhere** — nothing
is "smoothed"; and the clip mass differs between train and eval by 4.76pp.

`high_risk_model_flag` confirmed **target-derived**: a hardcoded 20-model set selected by fail rate
on training data (`:30-39`), baked into serving code. It cannot be refreshed point-in-time.

`local_corrosion_index` confirmed a **station proxy**: keyed on `tgt_pc`, the postcode area of the
station that performed the *target* test (`blocks.py:76`, tagged `research_only_input`, not
API-observable). 9 distinct values, identical range on every partition — a coarse lookup encoding
station identity, hence station strictness, not only climate.

**3 reclassified to `PARTITION_DEPENDENT`**: `text_leak_index`, `text_leak_index_log`,
`has_leak_history` — they vary on flat4y/post2018/eval2024 and collapse only on fullpop (§1). They
are the only `text_*` columns that vary at all, so the "text mechanism" family reduces to a single
leak-keyword hit.

### 2.5 The mileage-unit hardcode — verified; row share NOT measurable

The hardcode is real:

```python
# factory/runners/b0_module_runner.py:121
odometer_unit="mi", test_number=str(prior.get("p_test_id")),
```

Every prior is declared miles, so the module's km→mi branch
(`feature_engineering_v55.py:268-270`, `:101`) never fires.

**I could not quantify the km row share, and I am not going to estimate one.** There is no odometer
unit column to measure: the lake `results` schema carries `test_mileage` and no unit field, the
packets carry `p_miles` and no unit field, and `DATA_ASSESSMENT.md:170-172` records that DVSA
publishes no unit column and applied the km→miles correction only from the 2022 dataset.

What *is* measurable is the exposure, and it is badly asymmetric:

| partition | targets whose latest prior predates 2022-01-01 | share |
|---|---|---|
| flat4y r1m | 676,273 / 923,605 | **73.221%** |
| post2018 r1m | 1,015,091 / 1,262,423 | **80.408%** |
| eval2024 | 1,479 / 312,159 | **0.474%** |

The training frame draws ~73% of its mileage readings from the pre-correction era; the eval frame
draws 0.47%. That is a **154× asymmetry across DVSA's correction boundary**, affecting
`test_mileage`, `annualized_mileage_v2`, `usage_band_hybrid`, `b4_mileage_band` and
`b4_burden_x_mileage_band_ord`. Even at DVSA's own contamination estimate (0.04%–3.0% by year), the
contaminated fraction is concentrated almost entirely on the training side, which is the direction
that biases a fitted split without showing up at eval.

`FEATURE_DICTIONARY.md:237` calls `b4_mileage_band` "unit-robust by construction". That claim is
correct about *mixing* (only one reading is used) but **not about the band assignment**: an 80,000 km
reading bands as `60k-100k` instead of `30k-60k`. The dictionary overstates the guarantee.

---

## 3. Defects R1 classified as usable

R1's audit is complete in coverage but not in verdict.

1. **30 fullpop-collapsed columns** classified `partial` / `different_window_or_semantics` (§1).
2. **A missed exact-duplicate pair**: `b1_n_prior_test_days` ≡ `b4_deterioration_slope_n_days`,
   both classified `equivalent`. Zero differing rows on all five partitions (flat4y r1m 999,999;
   flat4y r250k 249,998; post2018 r1m 1,375,079; eval2024 330,665; fullpop 1,004,208). The slope's
   "honest denominator" (`blocks.py:174`) is the depth measure itself (`blocks.py:86`).
3. **`b1_first_use_missing_flag`** — classified `equivalent`; measured near-zero variance and
   constant at eval (True on 38/999,999 = 0.0038% of flat4y, **0** of 330,665 at eval2024). Reported
   as `NOT_CONFIRMED` rather than a risk: the skew direction is benign (the model sees almost only
   `False`, and eval is entirely `False`). Dead weight, not a hazard.

Two limits on my own scan, stated so they are not mistaken for clean results:

- The duplicate sweep over all 241 was signature-first (`n_distinct, n_null, min, max`) and only then
  exact-tested. It can miss a pair whose signature strings differ for representation reasons — e.g.
  `n_prior_tests` (float `0.0..48.0`) vs `b1_n_prior_tests` (int `0..48`) was *not* found by the
  sweep and had to be tested explicitly. Other float/int twins across the B0/B1-6 boundary may
  remain undetected. A full pairwise test was not run.
- Near-duplicates (|ρ| ≈ 1 without exact equality) were not measured at all; only exact equality was.

---

## 4. Kill / repair / quarantine — input to `safe_core_v1`

### KILL — 25 columns, safe to remove now, no repair possible or needed

*19 measured constants* (§2.1): `make_fail_rate_smoothed`, `model_fail_rate_smoothed`,
`segment_fail_rate_smoothed`, `station_fail_rate_smoothed`, `station_x_prev_outcome_fail_rate`,
`station_strictness_bias`, `suspension_risk_profile`, `mileage_cohort_ratio`,
`advisory_cohort_delta`, `text_corrosion_index`, `text_corrosion_index_log`, `text_wear_index`,
`text_wear_index_log`, `text_damage_index`, `text_damage_index_log`, `has_corrosion_history`,
`has_wear_history`, `has_damage_history`, `b6_location_map_status`.

*6 exact identities — drop the copy, keep the parent*: `raw_behavioral_count` (keep
`prev_count_advisory`), `recent_fail_intensity` (keep `fails_last_365d`), `eb_unified_prior` (keep
`model_age_fail_rate_eb`), `mech_decay_index_normalized` (keep `mech_decay_index`), `n_prior_tests`
(keep `b1_n_prior_tests`), `b4_deterioration_slope_n_days` (keep `b1_n_prior_test_days`).

Removing these 25 cannot change any fitted score: 19 are constant, 6 are bit-identical to a retained
column.

### REPAIR — 2 build defects, then re-measure

**R-1 (P0, blocking). Rebuild `frames_fullpop` with defect rows.** `queue.txt:167` must drop
`--defect-detail counts` and take the default `rows`, then re-run `B20B_B0_MODULE_FULLPOP`,
`B20C_EB_SWAP_FULLPOP`, `B20D_EB_SWAP_EVAL_FULLPOP`, `B20E_EB_SWAP_CONFIRM` and `B22A3_EB_SWAP_DRIFT`.
Acceptance test: `defects_json` non-null count > 0 on the fullpop packets, and all 53 columns in §1
non-constant on `b0_fullpop_eb`. **`s2.D.confirm.final` (queue.txt:174-176) and everything downstream
of it (`B22B`, `B23`, `B24B`) must stay HELD until this passes.** Owner: frame build.

**R-2. Add a guard so this cannot recur silently.** `b0_module_runner` accepts
`--defect-text-source section` over a packet set built with `--defect-detail counts` and emits zeros
without complaint. It should fail fast when the packet set it is given has zero non-null
`defects_json` but a text source was requested. Owner: factory.

### QUARANTINE — 40 columns, pending an owner decision (do not put in `safe_core_v1` unrepaired)

*D13 / within-day-order dependent (18 + transforms)* — measured 9.4% ambiguity on training rows.
Either re-sort priors on an identified key or accept the columns as unidentified:
`prev_cycle_outcome_band`, `gap_band`, `days_since_last_test`, `days_since_pass_ratio`,
`advisory_trend`, `test_month`, `is_winter_test`, `day_of_week`, `test_mileage`, `has_prev_mileage`,
`mileage_plausible_flag`, `mileage_anomaly_flag`, `annualized_mileage_v2`, `usage_band_hybrid`,
`days_late`, `mdps_score`, `advisory_in_last_1_{brakes,tyres,suspension}`.
The `test_month` / `is_winter_test` / `day_of_week` naming defect should be fixed regardless.

*Mileage-unit / era-exposure (5, overlapping the above)*: `test_mileage`, `annualized_mileage_v2`,
`usage_band_hybrid`, `b4_mileage_band`, `b4_burden_x_mileage_band_ord` — 73.2% train vs 0.47% eval
pre-correction exposure, and no unit column exists to repair against. Decide whether the family is
usable at all; correct `FEATURE_DICTIONARY.md:237` either way.

*Fabricated / misnamed quantities (3)*: `miles_since_last_advisory_tyres`,
`miles_since_last_advisory_suspension` (not miles since anything; ~72% on two constants),
`historic_negligence_ratio_smoothed` (clipped, not smoothed; 30–35% at the clip).

*Governance risk (2)*: `high_risk_model_flag` (target-derived hardcoded lookup),
`local_corrosion_index` + `local_corrosion_delta` (target-station postcode proxy, tagged
`research_only_input`, not API-observable at serving).

*Text-mechanism family (5)*: `text_leak_index`, `text_leak_index_log`, `has_leak_history`,
`mechanism_count`, `dominant_mechanism` — these vary only because of a single leak keyword, and
`has_leak_history` ≡ `mechanism_count` exactly. Keep at most one, and only after R-1.

*Near-zero variance (1)*: `b1_first_use_missing_flag` — 38 positive rows in 1M, none at eval.

### Retained without objection

The remaining columns of the 241 are unaffected by this gate — except that any of the 30
R1-usable/fullpop-collapsed columns listed in §1 must be re-measured after R-1 before they are
trusted in a fullpop-based cell.

---

## 5. Gate 3 recommendation

**REJECT** the current state; **HOLD** outcome-driven cube fitting.

`frames_fullpop` — the substrate for the final confirmation model, the sealed 2025-H2 read and the
drift score — is missing its entire defect stream, collapsing 53 of 241 columns to constants in
training while they remain live at evaluation. That is a train/serve skew of the most dangerous
class, it is caused by one flag on one queue line, and it is fully repairable. Nothing downstream of
`s2.D.confirm.final` should run until R-1 passes.

The 25-column KILL list is safe to apply immediately and is independent of R-1.

R1's audit was directionally sound and complete in coverage, but three of its verdicts do not hold
on the consumed matrix (`mechanism_count`, `dominant_mechanism`, `prev_count_advisory`), it missed
one exact-duplicate pair, and it classified 30 skew-affected columns as usable. The disputed 67
should not be actioned as a block.
