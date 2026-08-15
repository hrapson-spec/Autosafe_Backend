# PREREG — Advisory structure as a measurement of latent systemic deterioration

**Written 2026-08-15 BEFORE any advisory-structure feature was built and before any
performance number, breadth gradient or cell rate existed.** The only numbers quoted below
are (a) figures already banked by earlier studies and cited to their artifacts, and
(b) row/vehicle counts read from banked label parquets. No `adv_*` column has been computed.

Owner: Henri. Design decisions taken in the commissioning session are recorded in §3.

---

## 1. Hypothesis and causal frame

The working hypothesis is that AutoSafe detects a **persistent latent state of systemic
vehicle deterioration / accumulated maintenance debt**. `Y_B3` and `Y_M1` are highly
predictable because they are downstream manifestations of that state; isolated dangerous
defects are less predictable because they carry more idiosyncratic component-level noise.

**Immediate hypothesis under test:**

> The breadth, persistence and trajectory of prior advisories across vehicle systems predict
> future high defect burden **above and beyond** the total number of advisories.

Intended DAG (advisory breadth is a **measurement**, never a cause):

```
age / design / usage / environment
              │
              ▼
     latent deterioration ─────────────► continuing deterioration
              │                                     │
    ┌─────────┼─────────┐                           ▼
    ▼         ▼         ▼                    future defect burden
  tyres    brakes   suspension …               ┌────┴────┐
    │         │         │                      ▼         ▼
    └────► prior advisories ◄────┐          Y_B3      Y_M1
              │                  │
              ▼                  └── inspection process (CONFOUND, §10)
   depth / breadth / persistence / trajectory
              │
              ▼
        AutoSafe observes
```

**Competing pathway, explicitly modelled:** `advisory → owner/garage response → repair →
lower future risk`. Under this pathway a single advisory is *protective*, and only
**non-resolution** carries risk. This is why §5.H exists and why disappearance is never
labelled "repair" — repair invoices are not observed.

---

## 2. Prior-art audit — what already exists

This is recorded first because it determines what every ladder delta actually means.

### 2.1 Concepts already in the frame

Source `factory/blocks.py` unless noted.

| Concept | Existing columns | Gap this study fills |
|---|---|---|
| Depth | `b3_n_advisory_items` :308 (whole history); `b2_n_items_total` :272; `b4_burden_mean_last3` / `_delta_1` / `_delta_2` :319-321 | no advisory-only windowed counts; no max-burden-on-any-prior-day |
| Breadth | `b2_breadth_categories` :270; `b2_last_day_n_categories` :271; `b7d_last_fail_day_n_categories` :390 | **all are "any disposition"** (:264) — advisory-only breadth does not exist |
| Persistence | `b2_{cat}_max_run` :266; `b2_{cat}_persistence` :267 | same disposition gap |
| Spread / emergence | — | absent entirely |
| Trajectory | `b4_deterioration_slope` :325 (items/yr); `b7d_recent3day_minus_earlier_burden` :404 | **breadth**-slope and acceleration absent; `1→3→5` vs `5→3→1` is not currently representable |
| Concentration | — | absent entirely |
| Recency | `b2_{cat}_days_since` :265; `b3_days_since_*` :296-305 | no advisory-specific days-since; no recency-weighted breadth |
| Resolution proxies | `b4_n_adv_to_fail_transitions` :313; `b4_n_recurrence_after_repair` :316; `b7d_n_adv_to_minor_transitions` :405 | resolved/unresolved section counts and recurrence-after-disappearance absent |

**The honest description of this study's increment: a disposition-split of the existing B2
cube at a finer taxonomic grain, plus four concepts that do not exist at all** (spread,
concentration, breadth-trajectory, recency-weighted breadth).

### 2.2 ⚠ B0 already carries an advisory channel

`out/SERVE_VIEW_AUDIT.md` enumerates B0's advisory features, including:

| # | column | note |
|---|---|---|
| 4 | `advisory_trend` | `defect.type=='ADVISORY'` counts on last 2 tests |
| 6 | `prev_count_advisory` | across history; tagged `[ABA]` — type-word only, no taxonomy needed |
| 19 | `advisory_cohort_delta` | vs cohort artifact |
| **23** | **`multi_system_advisory_count`** | **components with ≥1 advisory (5 cats) — this is advisory breadth** |
| 30 | `front_end_advisory_intensity` | steering+suspension+tyres |
| 34-50 | per-component advisory family | brakes / tyres / suspension: `has_prior_*`, `tests_since_*`, `advisory_in_last_1/2_*`, `advisory_streak_len_*` |

At least 23 B0 columns consume the advisory channel. **The exact list is enumerated at
Phase 1 and frozen into `out/ADVSTRUCT_B0_ADVISORY_COLUMNS.json`; the number above is a
lower bound read from the audit table, not a verified census.**

**Consequence, and the single most important interpretive rule in this prereg:**

```
L0 − L0m  = value of the advisory CHANNEL at its current crude representation
L2 − L1   = value of a better REPRESENTATION of breadth, GIVEN the channel
```

`L1 − L0` and `L2 − L1` do **not** measure the value of depth or breadth. Reporting either
without `L0 − L0m` repeats the representation-vs-channel error `PREREG_B7.md` §2 was written
to prevent.

### 2.3 Standing evidence that cuts against the hypothesis

Declared in advance so that a null is not later reported as a surprise.

| Artifact | Result |
|---|---|
| `out/B7_PRIMARY_RESULT.json` | 34 richer-history columns → Δ = **+7.754e-4**, CI [5.920e-4, 9.537e-4], k=2, against `floor_F` **1.78e-3**. `clears_floor: false`. Class `SUB-FLOOR-POSITIVE` |
| same | its control arm **FIRED at −9.134e-3** (`b7.ctrl.b0-minus-ebprior` vs `b7.cum.b0`) |
| `out/NY_COHORT_STUDY_2026_08_15.md` | persistence-cohort ΔAUROC **−0.0238**, CI [−0.0291, −0.0185]; sign reversed; ΔMB-c −0.0195 ⇒ **82% case-mix** |
| same | 85.8% of lowest-quartile-risk failures are clean-history vehicles |
| `out/INTERVAL_AUDIT_2026_08_15.md` | perfect interval-mileage oracle worth ~0.012 univariate AUC; deployable proxies "already exhausted" |
| `out/SEVERITY_RESULT_2026_08_15.md` | `Y_S1` AUROC 0.6545 — **0.059 worse** than broad failure |

The prior probability that the ladder produces a floor-clearing gain is therefore **low**.
This study is commissioned anyway because the *descriptive* estimand (§7) is well powered,
independent of the fit surface, and has never been measured.

---

## 3. Scope, naming, banking posture

### 3.1 Naming (resolves two live collisions)

| Referent | Symbol | Definition |
|---|---|---|
| ordinary initial MOT failure | `Y_T0` | `y_final = 1` |
| high major/dangerous count | `Y_B3` | `n_major_or_dangerous >= 3` |
| multi-system major/dangerous | `Y_M1` | `n_sections_with_md >= 2` |
| **novel-system** multi-system | `Y_M1N` | `n_sections_with_md >= 2` **and** ≥1 such section carries **no advisory on any prior item-observable day** |
| dangerous defect | `Y_S1` | `n_dangerous >= 1`, fail-gated |

`Y_B3` is written with the `Y_` prefix throughout because **`B3` is also an 18-column feature
block**. Ladder rungs are `L0`…`L7`, not `H0`…`H6`, because **`H0`–`H4` are the NY cohort
study's strata**.

### 3.2 `Y_M1N` — why it exists

`Y_M1` is itself a breadth target. Prior-section-breadth → `Y_M1` is the same construct one
step apart, so `Y_M1` is flattered relative to `Y_B3` (a count target) **by construction, not
by theory**, and the headline `B3/M1 > T0 > S1` prediction is partly guaranteed. `Y_M1N`
requires deterioration in a section with no prior advisory and therefore cannot be satisfied
by same-construct autocorrelation. It is the sharp test of *spreading* rather than
*persisting* deterioration.

### 3.3 Banking posture

| Component | Posture |
|---|---|
| §7 descriptive falsification | **prereg-bankable** — never touches the fit surface |
| §8 ladder | **EXPLORATORY / NOT-BANKABLE-AS-VERDICT** — runs under owner override while `D13_REPAIR → D13_SEMANTIC_LOCK → BASELINE_REENTRY_LOCK` remain open, same posture as `out/B7_OVERRIDE_RECORD_2026_08_15.md` |

⚠ **Working-tree precondition.** At the time of writing, `factory/{atoms,blocks,emit,state}.py`
and `factory/tests/test_falsifiers.py` are modified and uncommitted on branch
`claude/autosave-defects-history-xqutcw`. Phase 1 does not start until that state is committed
or reverted; otherwise no result is reproducible. Staging is path-limited — never `git add -A`
in this repo.

### 3.4 Deployability — declared, not assumed

`out/SERVE_VIEW_AUDIT.md:10`: **`rfr_id`, `location_id` and `postcode_area` are NOT in the
DVSA API.** `defect.type == 'ADVISORY'` is (:92, `[ABA]`).

Therefore:

- advisory **count** is servable today;
- advisory **section/group is not** — it requires the text→taxonomy bridge, which the audit
  records as **UNBUILT/ungated** with the live-text precision gate **never run** (:21, :81);
- the `postcode_area` artefact control (§10) is research-only by the same rule.

**Every `adv_*` column in this study is `research_only_input`. No adoption decision follows
from any result here.** A deployable variant would require the `[TXT]` bridge to be built and
gated first, and that is out of scope.

---

## 4. Taxonomy of record

**Primary grain — top-level DVSA section.** `item_name`, resolved via
`pipeline.lake.rfr_mapping.load_rfr_mapping`, the 14 sections present in the lake. Identical
to `sect_00..sect_13` in `out/TARGET_SEVERITY_LABELS.parquet`, so breadth and `Y_M1` are
commensurate. `factory/taxonomy.py:4-5` records section as "the exact, verified aggregation
level" and the only level whose code spaces survive the 2018-05-20 disjoint-code-space break.

**Secondary grain — the 8 `CATEGORY_KEYS`** (7 canonical + `other`).
`rfr_mapping._SECTION_TO_CATEGORY:45-84` maps ~19 top-level names to `None`, collapsing them
into `other`. A vehicle advised on noise, seat belts, speedometer and identification scores
**breadth = 1** at category grain and **breadth = 4** at section grain.

The secondary cube carries **only** breadth, persistent-section count, and HHI — the three
grain-sensitive concepts. It is **not a ladder rung**. Its single job: *is B2's 6-of-14→`other`
compression why defect-breadth has never shown value?*

`rfr_id` grain is rejected: breadth ≈ count, which makes the §7 within-count test degenerate.

Catalogue misses (`rfr_id` absent from the class-4 map) are **counted separately and never
folded into a section**, per B2's existing rule (`blocks.py:273`).

Exact section vocabulary and volumes are emitted to
`out/ADVSTRUCT_TAXONOMY.json` at Phase 0 and frozen. Output #1 of §14 is that file.

---

## 5. Feature family — `adv_*`, section grain

Aggregates only. No per-section × per-stat cross product: B2 already emits that at category
grain, and redundant variants are excluded by design.

All columns are **NULL-honest** against `b2_item_observability_status`. An unobservable prior
day yields NULL, never 0. "Item-observable prior day" (`iod`) is the denominator throughout
and is the same object as `b2_n_prior_days_items_observed`.

Ordering is by **day**, never by within-day `test_id` (see §6).

### 5.A Depth (7)

| column | definition |
|---|---|
| `adv_n_last` | advisory items on the most recent `iod` |
| `adv_n_w2` / `_w3` / `_w4` | advisory items summed over the last 2 / 3 / 4 `iod` |
| `adv_n_max_day` | max advisory items on any single prior `iod` |
| `adv_n_cap2y` / `_cap4y` | advisory items within the trailing 2 / 4 years before `tgt_date` |

Whole-history total is **`b3_n_advisory_items` reused, not re-emitted**.

### 5.B Breadth (6)

| column | definition |
|---|---|
| `adv_breadth_last` | distinct sections advised on the most recent `iod` |
| `adv_breadth_w2` / `_w3` / `_w4` | distinct sections advised across the last 2 / 3 / 4 `iod` |
| `adv_breadth_max_day` | max distinct sections advised simultaneously on any prior `iod` |
| `adv_breadth_cumulative` | distinct sections ever advised |

### 5.C Persistence (5)

| column | definition |
|---|---|
| `adv_n_persistent_sections` | sections advised on the most recent `iod` **and** on the `iod` before it |
| `adv_max_persistence_run` | longest run of consecutive `iod` carrying the same section |
| `adv_recur_frac` | share of ever-advised sections advised on ≥2 `iod` |
| `adv_n_sections_ge2days` / `_ge3days` | sections advised on ≥2 / ≥3 `iod` |

### 5.D Spread / emergence (5)

| column | definition |
|---|---|
| `adv_n_new_sections_last` | sections on the most recent `iod` absent from every earlier `iod` |
| `adv_n_dropped_sections_last` | sections on the previous `iod` absent from the most recent |
| `adv_net_emergence_last` | new − dropped |
| `adv_cum_new_section_rate` | `adv_breadth_cumulative` / number of `iod` |
| `adv_days_since_new_section` | days since a section was first advised; NULL when never |

### 5.E Trajectory (6)

Let `B_t` = distinct sections advised on the *t*-th most recent `iod`.

| column | definition |
|---|---|
| `adv_breadth_delta_1` | `B_1 − B_2`; NULL with <2 `iod` |
| `adv_breadth_delta_2` | `B_2 − B_3`; NULL with <3 `iod` |
| `adv_breadth_slope` | least-squares slope of `B` on years-before-`tgt_date`; NULL with <3 `iod` or degenerate span |
| `adv_breadth_slope_n_days` | `iod` the slope was fitted on — its honest denominator |
| `adv_breadth_accel` | `adv_breadth_delta_1 − adv_breadth_delta_2` |
| `adv_trajectory_class` | ordinal, rules below |

`adv_trajectory_class` rules, evaluated on the last 3 `iod` in order `B_3, B_2, B_1`, with
`m = median(all B over history)`:

| level | rule | ordinal |
|---|---|---:|
| `insufficient` | fewer than 3 `iod` | NULL |
| `improving` | `B_1 < B_3` and `B_1 ≤ B_2` | 0 |
| `stable_low` | `B_1 = B_2 = B_3` and `B_1 ≤ m` | 1 |
| `stable_high` | `B_1 = B_2 = B_3` and `B_1 > m` | 2 |
| `mixed` | none of the above | 3 |
| `deteriorating` | `B_1 > B_3` and `B_1 ≥ B_2` | 4 |

`1→1→1` → `stable_low`, `4→4→4` → `stable_high`, `5→3→1` → `improving`, `1→3→5` →
`deteriorating`. Six levels, not five: `mixed` is emitted explicitly rather than folded, so no
trajectory is silently misclassified.

`1→1→1`, `4→4→4`, `5→3→1` and `1→3→5` map to four distinct `adv_trajectory_class` values and
distinct `adv_breadth_delta_*`. **A build in which they collapse to a common latest-value
representation is a build defect and is asserted against in the fixture suite.**

⚠ `adv_trajectory_class` enters models as an **ORDINAL, converted at load, not at render**.
A rare level falling entirely on one side of the seed-dependent 80/20 split makes `quantize()`
derive different Pool layouts and CatBoost refuses the fit — the B7
`b1_history_coverage_grade` trap. The neural-arch imputation path reads `frame.features`
directly and breaks on a render-time conversion. Vocabulary pinned; membership validated;
coverage deliberately not.

### 5.F Concentration (5)

Over sections advised on the most recent `iod`, with shares `p_s` = section items / total items.

| column | definition |
|---|---|
| `adv_items_per_affected_section_last` | `adv_n_last / adv_breadth_last` |
| `adv_max_section_share_last` | `max p_s` |
| `adv_hhi_last` | `Σ p_s²` |
| `adv_entropy_last` | `−Σ p_s log p_s` |
| `adv_hhi_cumulative` | HHI over all advisory items in history |

### 5.G Recency (5)

| column | definition |
|---|---|
| `adv_days_since_any` | days since the most recent advisory item |
| `adv_median_days_since_advised_section` | median over ever-advised sections of days since that section was last advised |
| `adv_rw_breadth_hl1y` / `_hl3y` | recency-weighted breadth, exponential half-life 1 / 3 years |
| `adv_rw_persistent_breadth_hl1y` | as above, restricted to sections advised on ≥2 `iod` |

### 5.H Resolution / non-resolution proxies (6)

**Disappearance is never labelled "repair".** Repair invoices are not observed. These are
resolution *proxies* and the evidence pack must say so at every use.

| column | definition |
|---|---|
| `adv_n_unresolved_sections` | sections advised on some `iod` and advised again on the next `iod` |
| `adv_n_resolved_sections` | sections advised and then absent on every later `iod` |
| `adv_resolved_share` | resolved / (resolved + unresolved) |
| `adv_n_recur_after_gap` | sections advised → absent ≥1 `iod` → advised again |
| `adv_n_adv_to_md_sections` | sections carrying an advisory and later an M/D item, section grain |
| `adv_days_since_adv_to_md` | days since the most recent such transition |

`adv_n_adv_to_md_sections` is the section-grain analogue of `b4_adv_to_fail_categories` :314,
which is category grain. Both are emitted; the difference is itself a grain measurement.

### 5.I Era exposure (4) — enters at `L1`

Mandated by the §10 confound. These are denominators, not predictors.

| column | definition |
|---|---|
| `adv_n_item_obs_days_pre2018` / `_post2018` | `iod` on either side of 2018-05-20 |
| `adv_pre2018_exposure_share` | pre-2018 share of `iod` |
| `adv_exposure_status` | `no_priors` / `pre_only` / `post_only` / `spanning` |

**Total: 49 columns**, section grain. Plus 8 secondary-grain robustness columns outside the
ladder.

---

## 6. Leakage audit

### 6.1 Rules

| Rule | Applies to | Basis |
|---|---|---|
| strictly-earlier **calendar day** | every `adv_*` | matches `b1_n_prior_test_days`, `blocks.py:229` |
| **never** use within-day `test_id` ordering | `_last`, `_delta_*`, `_new_sections_last`, `_dropped_*` | 35.09% of targets carry a prior day with **both** a PASS and a FAIL record; within-day `test_id` order agrees with truth at **49.91%** — chance |
| target-day items wholly excluded | all | severity labels are computed *from* the target test; exclusion keyed on `(test_id, vehicle_id, tgt_date)` |
| `tgt_date`-anchored only | `_days_since_*`, `_cap2y/4y`, `_rw_*` | `tgt_date` is known at prediction time |
| NULL, never 0, on unobservable days | all | `b2_item_observability_status` contract |

⚠ The forbidden-column scan runs against **the SQL actually executed**, not module source.
The G0.9 self-match trap fired when a source-scanning guard matched its own
`FORBIDDEN_COLUMNS` declaration.

### 6.2 Two-sided control — a green audit proves nothing until the fixture is shown able to fail

| Arm | Requirement |
|---|---|
| **planted-leak** — inject target-day advisory count | audit **must** flag it; its incremental ΔAUC **must** be large. If the audit passes it, the audit is broken and Phase 1 halts |
| **nominal-null** — `L0` vs `L0`, seeds varied only | must return ≈0 beyond σ. Not guaranteed: B7's control **fired at −9.134e-3** |

### 6.3 As-of reconstruction test

For a fixture sample of vehicles, `adv_breadth_last`, `adv_n_persistent_sections` and
`adv_breadth_delta_1` are recomputed by hand from raw packets and asserted equal.
Fixtures-only, per `FACTORY_CONTRACT.md`; the owner runs real builds.

---

## 7. PRIMARY FALSIFICATION TEST — within-count breadth

This is the estimand the study exists for, and it needs no model.

### 7.1 Estimand

> Within strata of equal total prior advisory count `c`, is future `Y_B3` / `Y_M1` risk
> increasing in prior advisory breadth `b`?

- **Window, primary:** the most recent item-observable prior test-day — one presentation,
  matching the `4 advisories / 1 group … 4 advisories / 4 groups` framing.
- **Window, secondary:** the trailing 3 `iod`. Tests whether the property is a snapshot or an
  accumulation.
- **Cells:** `c ∈ {2,3,4,5,6,7,8+}` × `b ∈ {1 … min(c,14)}`. `c = 1` is dropped — it forces
  `b = 1` and carries no information.
- **Per cell:** n, n_vehicles, `Y_B3` / `Y_M1` / `Y_M1N` rate, vehicle-clustered bootstrap CI,
  2000 reps, shared draws (NY cohort method; vectorised resample via
  `np.repeat(starts,counts) + arange − repeat(cumsum−counts)`).
- **Summary statistic:** `β_breadth|count` — within-count logistic on `b`, vehicle-clustered SE.
  One value per (count stratum × control stratum).

### 7.2 Population

**Discovery on TRAIN, confirmation out-of-time on EVAL.**

| frame | rows | vehicles | targets | `Y_T0` | `Y_B3` | `Y_M1` | `Y_S1` |
|---|---:|---:|---|---:|---:|---:|---:|
| `out/TRAIN_SEVERITY_LABELS.parquet` | 999,999 | 297,055 | 2020-01-02 → 2023-12-31 | 0.2292 | 0.0998 | 0.1256 | 0.0801 |
| `out/TARGET_SEVERITY_LABELS.parquet` | 330,665 | 315,300 | 2024 | 0.2288 | 0.0952 | 0.1201 | 0.0790 |

The train/eval split doubles as a free era-stability check. The eval frame is not both
discovery and confirmation surface.

### 7.3 Control stratifications

Each re-runs the whole table: age quartiles (`b1_age_at_target_years`); prior-depth bands
(`b1_n_prior_test_days ∈ {1-2, 3-5, 6+}`); target year; `postcode_area` region; make-model
group.

### 7.4 Correctness gate — before any analysis

Recompute `Y_T0` / `Y_B3` / `Y_M1` / `Y_S1` prevalence and positive counts from the label
parquets and assert **exact** equality with `out/SEVERITY_RESULT.json`. Proves join, label and
grain in one shot, as the NY cohort study proved its pooled AUROC to 0.00e+00. A mismatch
halts Phase 0.

### 7.5 Cell-count preflight — an explicit gate, not an assumption

The joint distribution of `(c, b)` on the last `iod` is **not known at the time of writing**.
Phase 0 measures and publishes the full cell-count grid **before** §7.6 is frozen. If
`{c=4, b=4}` × age-quartile is thin, the cell scheme is coarsened **then**, and the coarsening
is recorded as a prereg amendment with its own sha. Coarsening after seeing rates is
prohibited.

### 7.6 Preregistered verdict rule

| Verdict | Condition |
|---|---|
| **SUPPORTED** | `β > 0` with clustered CI clear of 0 in **≥5 of 7** count strata, for **both** `Y_B3` and `Y_M1`; **and** `β_survival ≥ 0.50` against age **and** prior-depth; **and** sign holds in **every** era stratum |
| **WEAK** | pooled `β` positive and CI-clear, but `β_survival < 0.50`, or era signs disagree |
| **FALSIFIED** | pooled `β ≤ 0`, or CI includes 0 for **both** `Y_B3` and `Y_M1` |
| **INCONCLUSIVE** | any required stratum below **500 rows** or **50 positives** |

**`β_survival` is defined, not left to judgement:**

```
β_pooled     = within-count logistic coefficient on b, no control stratification
β_stratified = n-weighted mean of the within-(count × control-stratum) coefficients
β_survival   = β_stratified / β_pooled
```

`β_survival = 1` means the gradient is untouched by the control; `β_survival = 0` means the
control fully explains it; a negative value means the gradient reverses inside strata. It is
computed separately for age and for prior-depth, and **both** must clear 0.50 for SUPPORTED.

---

## 8. The ladder

### 8.1 Rungs

| Rung | Featureset | n |
|---|---|---:|
| `L0m` | `b7.R0` **minus** the B0 advisory columns of §2.2 | `247 − |A|`, `|A| ≥ 23` ⇒ **≤ 224** |
| `L0` | `b7.R0` (`config_sha 4c2efc7871dc1040`, `r0_n = 247`) | 247 |
| `L1` | + Depth (5.A) + Era exposure (5.I) | 258 |
| `L2` | + Breadth (5.B) | 264 |
| `L3` | + Persistence (5.C) | 269 |
| `L4` | + Spread (5.D) | 274 |
| `L5` | + Trajectory (5.E) | 280 |
| `L6` | + Concentration (5.F) + Recency (5.G) + Resolution (5.H) | 296 |
| `L7` | + 5 interactions — **conditional**, §8.3 | 301 |

### 8.2 Critical comparisons

| Contrast | Question |
|---|---|
| `L0 − L0m` | value of the advisory **channel** at its current crude representation |
| `L2 − L1` | does breadth add beyond count, **given the channel**? |
| `L3 − L2` | does persistence add beyond breadth? |
| `L4 − L3`, `L5 − L4` | does *direction* of deterioration add beyond *level*? |
| **leave-one-out** | per-concept attribution the nested ladder cannot give |

⚠ The nested ladder makes `L3 − L2` conditional on what `L2` already absorbed; with correlated
concepts this **systematically understates later rungs**. Stage-2 precedent is that no
individual block clears the bar and only the cumulative gain does. The **leave-one-out pass**
(drop each concept from full `L6`, 7 cells per target) is therefore run alongside, and
per-concept verdicts in §11 read LOO as well as cumulative.

### 8.3 Interactions (`L7`)

Theory-driven only. **No unrestricted combinatorial search.**

`depth × breadth` · `breadth × persistence` · `breadth × recency` · `breadth × breadth-slope` ·
`persistence × new-group emergence`

Candidate latent-state signature: *high breadth + persistent sections + recently observed +
increasing breadth = systemic unresolved deterioration.*

`L7` fires **only if a rung moved at Phase 5**. An interaction rung may not rescue a dead
ladder.

### 8.4 Row eligibility

Feature construction must not change row eligibility. **Row-count identity is asserted at
every rung.** Any rung that drops rows triggers an explicit flag and a matched-row re-run, and
the drop is reported.

---

## 9. Statistical protocol

**"Bootstrap CI excludes zero" is NOT the adoption gate.** The planted nominal-null refit has
already shown that an unchanged model can produce an apparently significant AUC difference,
because evaluation-sample uncertainty is much smaller than training/refit nuisance. B7's
control fired at −9.134e-3 on this very surface.

| Element | Protocol |
|---|---|
| per-target noise | `L0` refit, **k=5 seeds**, separately for each of the 5 targets → empirical `σ_target` → `F_target`. **No target inherits `y_final`'s 1.78e-3** |
| screen | `cb_inc @ 250k`, k=2 — the only surface with a tight measured MDE (4.34e-4) |
| promotion | a rung promotes to 1M **only if** Δ > `MDE_screen(target, k=2)`, derived from that target's own `σ_target` measured at 250k — never from a pooled or inherited MDE |
| confirmation | 1M, k=3, against `F_target(1M, k=3)` |
| quantisation | rungs **must** share bit-identical borders on all common features, composed-file, verified byte-level, as B7 did. Otherwise the deltas measure border drift |
| reporting | every Δ ships its **seed panel**, not just the mean |
| bootstrap CIs | reported **descriptively**, explicitly labelled as not the gate |

⚠ Per-architecture floors only. The `cb_inc` floor is never applied to a neural arch, and the
LightGBM legs remain NO-FLOOR / UNCHARACTERISED by `PREREG_STAGE3` OWNER-AMEND-4.

⚠ 250k rank order need not hold at 1M. A 250k result is a screen, never an adopted-benchmark
number.

**Fit budget.** 15 cells (8 rungs + 7 LOO) × 5 targets × 2 seeds = **150 screening fits**,
plus 25 null-panel and 20 control fits. ~3-4 h serial at the 62-75 s the ledger shows.
**One compute job at a time** — the box is 8 GB and three architecture screens died on
2026-08-13 to swap-thrash and SIGKILL. Long runs go through the night queue, unbuffered log,
tail path published up front.

---

## 10. Measurement-process falsification

Advisories are measurements, not physical truth: `actual condition → recorded advisory ←
inspection process`. Apparent persistence may be persistence in **measurement practice**.

### 10.1 Feasible dimensions

| Dimension | Feasible | Test |
|---|---|---|
| calendar era | yes | `β_breadth|count` by target year; `adv_pre2018_exposure_share` |
| geography | yes, coarse | `postcode_area` regions — `ingest_results.py:43`, `schemas.py:50` |
| vehicle cohort | yes | make-model groups |
| **inspection-context change** | **NO** | **no station or tester identifier exists in the lake.** This leg is DROPPED and reported as dropped, not weakened |

### 10.2 The discriminating prediction: level versus slope

Advisory recording drifts through the entire window. `DATA_ASSESSMENT.md:361-366`: advisory
volumes rise 2.9M → 39.7M/yr across 2005-2014 while fail items stay flat at ~34-35M/yr; :450:
share of tests carrying ≥1 item rises **53.5% (2010) → 59.2% (2019) → 60.4% (2023)**. Advisory
rows are 695,128,834 of 1,289,329,470 items (53.9%).

Prior-window recency correlates with vehicle age, so this is a live mechanism for
manufacturing the predicted breadth gradient.

**Recording drift moves the LEVEL of breadth. Latent deterioration predicts a stable GRADIENT
of risk in breadth given count. If `β` tracks the drift, it is practice, not deterioration.**

### 10.3 Two substitute instruments

- **Area-practice control.** Leave-one-vehicle-out, as-of mean advisories-per-test in the
  vehicle's `postcode_area`. If `β` shrinks materially once conditioned on it, breadth is
  partly measuring *where the car was tested*.
- **Persistence excess.** Raw persistence is confounded by breadth. Compare observed
  section-recurrence against recurrence expected under independent per-day section draws
  matched on each vehicle's per-day breadth and marginal section frequencies. **Excess**
  persistence is the quantity the theory is about. If excess ≈ 0 while raw persistence
  predicts, persistence is a breadth artefact.

### 10.4 Built-in negative control

`Y_S1` is the theory's own predicted negative control: the severity study already measured it
at **0.6545**, worse than broad failure, hypothesised as acute events rather than cumulative
deterioration.

---

## 11. Per-concept verdicts

Each of the 8 concepts receives one verdict, from four inputs: descriptive `β`, cumulative Δ,
leave-one-out Δ, artefact survival.

| Verdict | Condition |
|---|---|
| **SUPPORTED** | descriptive evidence positive and stratification-surviving; **and** cumulative-or-LOO Δ clears `F_target` on ≥1 of `Y_B3`/`Y_M1` at **1M confirmation**; **and** survives era, geography and cohort checks |
| **WEAK** | sub-floor positive with consistent sign across seeds and targets — B7's `SUB-FLOOR-POSITIVE` class |
| **FALSIFIED** | Δ ≤ 0 with CI clear, or descriptive `β ≤ 0` |
| **INCONCLUSIVE** | \|Δ\| < `F_target` with inconsistent sign, or a cell-count failure |

⚠ **Every INCONCLUSIVE must state the MDE it was inconclusive against.** A verdict without its
power is not a verdict.

⚠ **SUPPORTED requires a 1M confirmation, and 1M is only reached through the screen.** A
concept that fails the 250k screen therefore cannot be SUPPORTED under any circumstance, and
is recorded as WEAK or INCONCLUSIVE with its screen MDE stated. This asymmetry is deliberate
and declared in advance so it is not later mistaken for a missing analysis.

⚠ A concept may be **SUPPORTED descriptively and FALSIFIED as a feature**. That combination is
the expected outcome given §2.3 and is a real result, not a failure of the study: it would mean
breadth *is* an informative measurement of latent deterioration whose information the model
already holds through another channel.

---

## 12. Cross-target prediction

The theory predicts breadth / persistence / trajectory add materially more for `Y_B3` and
`Y_M1` than for `Y_S1`, and plausibly more than for `Y_T0`. **That cross-target pattern matters
as much as the absolute AUC gain.**

⚠ Raw Δ is **not comparable** across targets with different prevalence. The ranking is tested
on **Δ / σ_target**, standardised by each target's own measured refit noise from §9.

⚠ **Never compare a subgroup AUROC to a pooled benchmark.** The NY cohort study established
that pooled AUROC is high *because* it ranks across subgroups with different baselines; the
error always flatters the subgroup.

Sub-prediction: if the theory is about **spreading** rather than **persisting** deterioration,
the spread rung `L4` benefits `Y_M1N` most.

---

## 13. Sequencing and gates

| Phase | Work | Gate to pass |
|---|---|---|
| 0 | freeze this prereg + sha; taxonomy census; `(c,b)` cell-count preflight; prevalence correctness gate | prevalence exact; cell scheme frozen |
| 1 | commit/revert the dirty tree; enumerate B0 advisory columns; build `adv_*`; leakage audit | planted-leak arm **caught**; nominal-null arm **quiet**; as-of reconstruction exact |
| 2 | descriptive falsification on TRAIN → freeze pattern → confirm on EVAL | §7.6 verdict recorded before any fit |
| 3 | per-target null panels + control arms | `σ_target` measured for all 5 |
| 4 | 250k screen, 150 fits, cumulative + LOO | row-eligibility identity holds at every rung |
| 5 | 1M promotion of screen survivors only | quantisation parity byte-verified |
| 6 | artefact checks on whatever survived | — |
| 7 | evidence pack + per-concept verdicts | — |

`L7` fires only if a rung moved at Phase 5.

---

## 14. Deliverable

One evidence pack: `out/ADVSTRUCT_RESULT_2026_08_15.md` + `out/ADVSTRUCT_RESULT.json`,
structured to these 13 outputs.

1. exact advisory-group taxonomy used → `out/ADVSTRUCT_TAXONOMY.json`
2. definitions for every constructed feature
3. leakage audit per feature, with both control arms
4. prevalence, distribution, missingness — with observability denominators
5. within-count breadth falsification tables for `Y_B3` and `Y_M1`
6. `L0m`/`L0`→`L6` matched results for `Y_T0`/`Y_B3`/`Y_M1`/`Y_M1N`/`Y_S1`
7. repeated-seed deltas for every incremental step
8. does breadth add beyond count?
9. does persistence add beyond breadth?
10. does trajectory/spread add beyond static history?
11. is the predicted `B3/M1 > T0 > S1` pattern observed — on `Δ/σ_target`?
12. evidence the result is inspection/era artefact rather than vehicle deterioration
13. final verdict per concept: SUPPORTED / WEAK / FALSIFIED / INCONCLUSIVE

If the theory survives, §15 of the result document proposes the next causally motivated feature
hypotheses. **Undirected feature generation is not an acceptable fallback if it does not.**

---

## 15. Declared risks

1. **The ladder is likely to return sub-floor.** Every comparable precedent (§2.3) did. This is
   declared now so a null is a result, not a disappointment, and so no primary is rescued
   post-hoc.
2. **`L0` already carries the advisory channel.** Without `L0 − L0m` the ladder deltas are
   uninterpretable. This is the study's most likely misreading.
3. **Recording drift can manufacture the predicted gradient.** §10.2 is the load-bearing check,
   not a footnote.
4. **No station identifier exists.** The strongest available artefact test cannot be run;
   §10.3 substitutes are weaker and are labelled as such.
5. **Cell counts are unmeasured.** §7.5 may force coarsening. Coarsening after seeing rates is
   prohibited.
6. **`Y_M1` is construct-coupled to breadth.** `Y_M1N` exists to break it; if `Y_M1N` is too
   rare to estimate, that sub-prediction is reported INCONCLUSIVE rather than dropped.
7. **The fit surface is under owner override with D13 open.** No ladder number is bankable as a
   verdict.

---

## 16. Deviations register

Deviations from this prereg are recorded in `factory/DEVIATIONS.md` under the
deviate-with-test rule of `FACTORY_CONTRACT.md`: a deviation ships with the test that made it
necessary. Amendments after freeze carry their own sha and state whether any result had been
seen at the time of amendment.
