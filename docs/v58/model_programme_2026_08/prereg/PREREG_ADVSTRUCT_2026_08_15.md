# PREREG — Advisory structure as a prognostic signature of future multi-defect burden

**Revision 2, 2026-08-15. Written BEFORE any `adv_*` column was built and before any
performance number, breadth gradient or cell rate existed.** Revision 1 was committed
(`480bf22`) but **deliberately never sha-frozen** — no `.sha256` sidecar was ever generated.
This is therefore a **pre-freeze revision, not a post-freeze amendment**. The full change
ledger is §17.

The only numbers quoted are (a) figures banked by earlier studies, cited to their artifacts,
(b) row/vehicle counts read from banked label parquets, and (c) a read-only vocabulary census
of raw section strings. **No outcome rate conditional on any advisory quantity has been
computed.**

Owner: Henri.

---

## 1. Claim under test

**This experiment does not attempt to prove that a latent systemic deterioration state
exists.** The confirmatory claim is narrower and falsifiable:

> Do the **breadth, persistence and trajectory** of prior recorded advisories across vehicle
> systems provide a **stable prognostic signature** of future multi-defect burden **beyond
> advisory count** — consistent with, but not proof of, persistent multi-system deterioration?

**The estimand is prognosis under the naturally occurring maintenance and repair behaviour
present in the data.** An advisory is not an inert observation: it is a notification that can
itself trigger repair. So the quantity measured is *"given that this vehicle was advised in
this pattern, and given how owners and garages actually responded in this population, what
happens next"* — not a counterfactual risk under no repair. Every downstream statement must
respect that scope.

This matters for interpretation. A null on breadth is consistent with **either** "breadth
carries no prognostic information" **or** "breadth carries information that owners act on, and
the acting cancels the signal." This design cannot separate those, and does not claim to.

Structure hypothesised, not asserted:

```
age / design / usage / environment
              │
              ▼
   persistent multi-system deterioration ─────► continuing deterioration
              │                                          │
    ┌─────────┼─────────┐                                ▼
    ▼         ▼         ▼                        future defect burden
  tyres    brakes   suspension …                    ┌────┴────┐
    │         │         │                           ▼         ▼
    └──► prior advisories ──► owner/garage ──►    Y_B3      Y_M1
              │               response/repair
              ▼                    (IN the estimand, not adjusted away)
   depth / breadth / persistence / trajectory
              │
              ├── inspection & recording process (CONFOUND, §10)
              ▼
        AutoSafe observes
```

Advisory structure is treated throughout as a **measurement**, never as a cause.

---

## 2. Prior art — what exists and what it settles

### 2.1 Concepts already in the frame

Source `factory/blocks.py`.

| Concept | Existing columns | Gap |
|---|---|---|
| Depth | `b3_n_advisory_items` :308 (whole history); `b2_n_items_total` :272; `b4_burden_mean_last3`/`_delta_1`/`_delta_2` :319-321 | no advisory-only windowed counts; no max-burden-on-any-prior-day |
| Breadth | `b2_breadth_categories` :270; `b2_last_day_n_categories` :271; `b7d_last_fail_day_n_categories` :390 | **all "any disposition"** (:264) — advisory-only breadth absent |
| Persistence | `b2_{cat}_max_run` :266; `b2_{cat}_persistence` :267 | same disposition gap |
| Spread / emergence | — | absent |
| Trajectory | `b4_deterioration_slope` :325; `b7d_recent3day_minus_earlier_burden` :404 | **breadth**-slope/volatility absent |
| Concentration | — | absent |
| Recency | `b2_{cat}_days_since` :265; `b3_days_since_*` :296-305 | no advisory-specific days-since; no recency-weighted breadth |
| Resolution proxies | `b4_n_adv_to_fail_transitions` :313; `b4_n_recurrence_after_repair` :316 | resolved/unresolved system counts, recurrence-after-disappearance absent |

### 2.2 ⚠ AutoSafe ALREADY contains a coarse advisory channel

This is a correction to Revision 1's framing. It is **not** the case that advisory information
is absent from the model. `out/SERVE_VIEW_AUDIT.md` enumerates B0's advisory features:

| # | column | note |
|---|---|---|
| 4 | `advisory_trend` | `defect.type=='ADVISORY'` counts on last 2 tests |
| 6 | `prev_count_advisory` | across history; `[ABA]` — type-word only, no taxonomy |
| 19 | `advisory_cohort_delta` | vs cohort artifact |
| **23** | **`multi_system_advisory_count`** | **components with ≥1 advisory (5 cats) — coarse advisory breadth, in production V55 today** |
| 30 | `front_end_advisory_intensity` | steering+suspension+tyres |
| 34-50 | per-component advisory family | brakes/tyres/suspension: `has_prior_*`, `tests_since_*`, `advisory_in_last_1/2_*`, `advisory_streak_len_*` |

≥23 B0 columns consume the advisory channel. **What is untested is not "does advisory
information help" but "does a richer canonical-system, longitudinal representation of it help
beyond the coarse representation already present."**

Exact membership is enumerated at Phase 2 and frozen to
`out/ADVSTRUCT_B0_ADVISORY_COLUMNS.json`. The count above is a lower bound read from the audit
table, not a verified census.

**Consequence — the interpretive rule this whole design turns on:**

```
L0m → L0   = value of the EXISTING coarse advisory channel      [MANDATORY, §8.2]
base → ADV = value of the RICHER representation, given that channel
```

A descriptive-positive / model-null result is **uninterpretable without the first number**. It
cannot distinguish *"the richer representation is redundant because B0 already carries the
advisory channel"* from *"the model does not use advisory information materially."* The
ablation is therefore unconditional.

### 2.3 Standing evidence, declared in advance

| Artifact | Result |
|---|---|
| `out/B3_BURDEN_RESULT.json` | 12 prior-burden columns → mean **+1.68e-04**, sd 2.31e-04, **one seed negative** → pre-registered **NULL**, inside LightGBM's 1.74e-04 refit nuisance |
| ⚠ but `burden_features.py:52` | `rfr_type_code IN ('F','P')` — **advisory items structurally excluded from all 12 columns**; its own prereg §6 names *"advisory persistence"* as out of scope |
| `out/CANONICAL_CEILING_STATEMENT.md` | ratified: "the binding constraint is neither the objective nor the representation" |
| `out/B7_PRIMARY_RESULT.json` | 34 richer-history columns → +7.754e-4 vs floor 1.78e-3, `clears_floor: false`; **its control FIRED at −9.134e-3** |
| `out/NY_COHORT_STUDY_2026_08_15.md` | persistence-cohort ΔAUROC **−0.0238**, sign reversed, 82% case-mix |
| `out/SEVERITY_RESULT_2026_08_15.md` | `Y_S1` 0.6545 — 0.059 **worse** than broad failure |

The burden null is the **fail-disposition twin** of this study: a structurally similar "does
distinct-system breadth add beyond item count" question, on the wrong disposition, at
single-day grain, with no trajectory/concentration/spread/recency/resolution. It is a strong
analogical prior, **not** a substitute.

**Prior probability of a model-side null is high, and pre-declared as such.** The value of this
work rests on §7, which needs no model.

---

## 3. Scope, naming, target roles, banking posture

### 3.1 Naming

`B3` is also an 18-column feature block, so outcomes carry a `Y_` prefix. `H0`–`H4` are the NY
cohort study's strata, so ladder rungs are `L*`.

### 3.2 ⚠ Target roles — asymmetric by design

| Target | Definition | **Role** |
|---|---|---|
| **`Y_B3`** | `n_major_or_dangerous >= 3` | **PRIMARY. Sole trigger for the MOVED decision.** |
| `Y_M1` | `n_sections_with_md >= 2` | Confirmatory only |
| `Y_S1` | `n_dangerous >= 1`, fail-gated | Falsifier / reference contrast |
| `Y_M1N` | `Y_M1` **and** ≥1 such section carries no advisory on any prior item-observable day | **EXPLORATORY ONLY — never confirmatory** |
| `Y_T0` | `y_final = 1` | Reference |

⚠ **`Y_M1N` cannot be a clean confirmatory test of advisory breadth**, because its outcome
definition itself depends on whether future failing sections appeared in prior advisory
history. It is constructed from the same object it is meant to test. It is retained because
the spread-versus-persistence distinction is interesting, and it is fenced to exploratory.

⚠ `Y_M1` is construct-coupled to breadth in the opposite direction — prior-system-breadth →
future-system-count is the same construct one step apart. Hence confirmatory, not primary.
`Y_B3` is a **count** target and is the only clean one.

### 3.3 Banking posture

| Component | Posture |
|---|---|
| §7 descriptive | **prereg-bankable** — never touches the fit surface |
| §8 model results | **EXPLORATORY / NOT-BANKABLE-AS-VERDICT** |

⚠ The model half stays exploratory while `D13_REPAIR → D13_SEMANTIC_LOCK →
BASELINE_REENTRY_LOCK` remain open **and** while the B7 control anomaly (−9.134e-3 on a
nominal null) stands unexplained. Both conditions, not either.

⚠ **Working-tree precondition.** `factory/{atoms,blocks,emit,state}.py` and
`factory/tests/test_falsifiers.py` are modified and uncommitted on
`claude/autosave-defects-history-xqutcw`. Phase 2 does not start until that is committed or
reverted. Staging is path-limited — never `git add -A` in this repo.

### 3.4 Deployability

`out/SERVE_VIEW_AUDIT.md:10`: `rfr_id`, `location_id`, `postcode_area` are **not in the DVSA
API**; `defect.type == 'ADVISORY'` is (:92). Advisory **count** is servable; advisory **system**
is not — the text→taxonomy bridge is UNBUILT/ungated, live-text precision gate never run
(:21, :81). **Every `adv_*` column is `research_only_input`. No adoption decision follows.**

---

## 4. Taxonomy — a versioned physical-system ontology

### 4.1 Status

The canonical-system crosswalk is an **explicit versioned ontology**, `ADVSTRUCT_ONTOLOGY_V1`,
emitted to `out/ADVSTRUCT_TAXONOMY.json` with a `version` field and its own sha. It is a
research artifact in its own right, not an implementation detail, and any later change to it
increments the version and invalidates prior results rather than silently re-mapping them.

### 4.2 Census scope — full eligible history, both frames

⚠ Revision 1 built the crosswalk from advisories on the **most recent prior day of eval2024
only**. That is not a sufficient basis for an ontology. The census runs over **every advisory
item on every eligible prior test-day, TRAIN and EVAL**, and reports per-raw-string volumes,
per-year volumes (to expose catalogue vintage), and the folded-versus-unfolded breadth delta.

### 4.3 Fail-closed

**Any non-null raw `sect` value not present in `ADVSTRUCT_ONTOLOGY_V1` raises and halts the
build.** It is never silently bucketed to `other`, never dropped, never mapped by fuzzy match.
A new catalogue vintage is a reason to version the ontology, not to guess. `NULL` `sect` is a
distinct, expected state (catalogue miss) and is counted, not raised on.

### 4.4 The crosswalk

Case and punctuation are normalised through `pipeline.lake.rfr_mapping._norm_item_name` — never
a new normaliser. Vintage variants are folded. Volumes below are from the read-only eval2024
most-recent-day preflight and are **indicative only**; §4.2's full census supersedes them.

| canonical system | raw `sect` folded in | indicative n |
|---|---|---:|
| **`wheels_tyres`** | `Tyres`, `Wheels` | 137,936 |
| `brakes` | `Brakes` | 115,144 |
| `suspension` | `Suspension` | 92,009 |
| `noise_emissions` | `Noise, emissions and leaks`, `Exhaust, Fuel and Emissions` | 16,236 |
| `body_structure` | `Body, chassis, structure`, `Body, Structure and General Items` | 14,062 |
| `lamps_electrical` | `Lamps, reflectors…`, `Lamps, Reflectors…` | 8,556 |
| `steering` | `Steering` | 5,466 |
| `seatbelts_srs` | `Seat belts and…`, `Seat Belts and…` | 2,513 |
| `visibility` | `Visibility`, `Driver's View of the Road` | 304 |

⚠ **Renamed `tyres` → `wheels_tyres`**, because `Wheels` is folded into it. A system label must
not name a proper subset of what it contains.

**Excluded from breadth, retained as ADV_AUDIT exposures** — these are not vehicle systems:

| bucket | indicative n | column |
|---|---:|---|
| `Non-component advisories` | 23,440 | `adv_n_noncomponent` |
| `Identification of the vehicle`, `Registration Plates and VIN` | 8,389 | `adv_n_identification` |
| `NULL` sect (catalogue miss) | 1 | `adv_n_catalogue_miss` |

⚠ Vintage folding is load-bearing: unfolded counting inflates breadth as a function of
catalogue vintage, which is a function of era — manufacturing precisely the gradient §10 exists
to detect. The folded-versus-unfolded delta is published so the size of that artefact is on
record.

### 4.5 Alternative-grain sensitivity

The 8-key `CATEGORY_KEYS` cube (`ADV_GRAIN`, §5.2) is retained as a **coarser** alternative
grain, run as a sensitivity analysis on `Y_B3` only. Its job: does the answer depend on grain?
`rfr_id` grain is rejected — breadth ≈ count, degenerating §7.

---

## 5. Feature family

### 5.1 ⚠ Four groups, not one block

Revision 1 conflated structural signal with observability bookkeeping and reported an
inconsistent column count. Corrected:

| group | n | contents | **which arms** |
|---|---:|---|---|
| **`ADV_CORE`** | 45 | breadth, persistence, spread, trajectory primitives, concentration, recency, resolution, depth | **treatment arm only** |
| **`ADV_COVERAGE`** | 7 | observability + era exposure denominators | **BOTH arms** |
| **`ADV_AUDIT`** | 5 | non-component / identification / catalogue-miss exposures, nominal trajectory class | **BOTH arms** |
| `ADV_GRAIN` | 8 | category-grain breadth / persistence / HHI | sensitivity arm only |

⚠ **`ADV_COVERAGE` and `ADV_AUDIT` sit in BOTH fit arms.** Otherwise a positive Δ could be
driven by catalogue coverage or observability artefacts rather than advisory structure, and
would be indistinguishable from one. This is the single most important structural change in
Revision 2.

Arm sizes: **base = 241 + 12 = 253**, **ADV = 253 + 45 = 298**, grain sensitivity = 253 + 8 =
261.

⚠ The base arm is 253, not the burden study's 241, so cross-study Δ comparison is approximate.
The pure-241 `Y_B3` baseline is already banked at k=5 (`out/B3_REFERENCE_BASELINE.json`, mean
0.7990599, sd 9.53e-05) and is reported as the bridge — no new fits required.

### 5.2 `ADV_CORE` (45)

All at `ADVSTRUCT_ONTOLOGY_V1` system grain. Aggregates only — no per-system × per-stat cross
product. "iod" = item-observable prior test-day.

**Depth (7)** — `adv_n_last`, `adv_n_w2/_w3/_w4`, `adv_n_max_day`, `adv_n_cap2y`, `adv_n_cap4y`.
Whole-history total is `b3_n_advisory_items`, reused not re-emitted.

**Breadth (6)** — `adv_breadth_last`, `_w2/_w3/_w4`, `_max_day`, `_cumulative`.

**Persistence (5)** — `adv_n_persistent_systems`, `_max_persistence_run`, `_recur_frac`,
`_n_systems_ge2days`, `_n_systems_ge3days`.

**Spread (5)** — `adv_n_new_systems_last`, `_n_dropped_systems_last`, `_net_emergence_last`,
`_cum_new_system_rate`, `_days_since_new_system`.

**Trajectory (6) — numeric primitives only** — `adv_breadth_delta_1`, `_delta_2`,
`_breadth_slope`, `_breadth_slope_n_days`, `_breadth_accel`, `_breadth_volatility` (sd of
per-day breadth over iod).

⚠ **`adv_trajectory_class` is NOT an ordinal and is NOT in any fit arm.** Revision 1 encoded it
as an ordinal to dodge a CatBoost quantise trap. That was wrong: `stable_low`, `stable_high`,
`improving`, `worsening`, `mixed` have **no defensible scalar ordering** — placing `stable_high`
above `improving` on a number line asserts a comparison the data does not support. It is frozen
as **nominal**, emitted to `ADV_AUDIT` for descriptive reporting only. The six numeric
primitives above carry the trajectory information into the model, and they separate `1→1→1`,
`4→4→4`, `5→3→1`, `1→3→5` on `_delta_1`/`_slope`/`_volatility` without asserting an order.

**Concentration (5)** — `adv_items_per_affected_system_last`, `_max_system_share_last`,
`_hhi_last`, `_entropy_last`, `_hhi_cumulative`.

**Recency (5)** — `adv_days_since_any`, `_median_days_since_advised_system`,
`_rw_breadth_hl1y`, `_rw_breadth_hl3y`, `_rw_persistent_breadth_hl1y`.

**Resolution proxies (6)** — `adv_n_unresolved_systems`, `_n_resolved_systems`,
`_resolved_share`, `_n_recur_after_gap`, `_n_adv_to_md_systems`, `_days_since_adv_to_md`.
⚠ **Disappearance is never labelled "repair".** Repair invoices are not observed. These are
resolution *proxies* and the deliverable says so at every use. Per §1 they are part of the
estimand, not a nuisance to adjust away.

### 5.3 `ADV_COVERAGE` (7, both arms)

`adv_n_prior_days`, `adv_n_item_obs_days`, `adv_observability_status`,
`adv_n_item_obs_days_pre2018`, `adv_n_item_obs_days_post2018`, `adv_pre2018_exposure_share`,
`adv_exposure_status`.

### 5.4 `ADV_AUDIT` (5, both arms)

`adv_n_noncomponent`, `adv_n_identification`, `adv_n_catalogue_miss`,
`adv_n_unknown_system` (fail-closed counter, must be 0 in any emitted build),
`adv_trajectory_class` (nominal).

### 5.5 ⚠ Three-state NULL semantics

Revision 1 collapsed two distinct unknowns. Corrected — three states, distinguished everywhere:

| state | counts | rates / slopes / trajectory |
|---|---|---|
| **no prior test** | **0, and certain** | **NULL** — undefined, not zero |
| **prior history observable, zero advisories** | **0, and certain** | **0** where the denominator is defined; NULL where it is not (e.g. slope with <3 iod) |
| **prior history present, items unobservable** | **NULL** | **NULL** |

A count may legitimately be a certain zero in the first two states. A **rate, share, slope,
volatility or trajectory measure is NULL whenever its denominator or minimum support is
undefined**, in every state. `adv_observability_status` names which state applies per row.
Reuse `blocks.item_graded` (`blocks.py:613-629`) and `blocks._coverage_status` (:770-781) —
do not reimplement the three-state logic a third time.

### 5.6 ⚠ Same-day duplication

The most recent prior day can carry multiple tests — 8.8% of eval targets, and a fail followed
by a retest will re-record the same physical advisory. Naive day-level counting inflates depth
and can inflate breadth.

**Deduplicate at `(tgt_id, p_date, canonical_system, item_key)`** before any count, where
`item_key` = `rfr_id` when present, else the normalised item text. Day-union is then the state
representation.

⚠ **Permutation invariance does not settle this.** The D13 falsifier proves features are
invariant to within-day row order; it says nothing about whether day-union is the semantically
right state. That is an open modelling choice, declared here, and tested by a **tied-prior-day
sensitivity analysis**: re-run §7 restricted to targets whose most recent prior day carries
exactly one test, and report whether the gradient changes.

---

## 6. Leakage audit

| Rule | Basis |
|---|---|
| strictly-earlier calendar **day** | matches `b1_n_prior_test_days`, `blocks.py:229` |
| **never** within-day `test_id` ordering | within-day order agrees with truth at **49.91%** — chance |
| dedup per §5.6 before counting | — |
| target-day items wholly excluded | labels are computed *from* the target test |
| `tgt_date`-anchored only | known at prediction time |
| v2 packets only | in v1, 48.7% of prior rows have `defects_json IS NULL` conflating zero-defects with unobservable |

⚠ Forbidden-column scan runs against **the SQL actually executed**, not module source — reuse
`severity_collect.py`'s `RecordingConnection` (the G0.9 self-match trap).

Fatal gates, exit 2 and write nothing: `G_strict_date_violations`,
`G_self_reference_violations`, `adv_n_unknown_system > 0`.

**Two-sided control** — a green audit proves nothing until the fixture is shown able to fail:

| arm | requirement |
|---|---|
| planted-leak (target-day advisory count injected) | audit **must** flag it; ΔAUC **must** be large. If it passes, the audit is broken and Phase 2 halts |
| nominal-null (`base` vs `base`, seeds only) | must return ≈0 beyond refit variability. Not guaranteed: B7's fired at −9.134e-3 |

---

## 7. PRIMARY DESCRIPTIVE ANALYSIS — no model

### 7.1 ⚠ Hardened correctness gate

Revision 1 gated on prevalence equality. **Necessary but not sufficient** — two different row
sets can share a prevalence. Required, all of them:

1. **Exact `tgt_id` set equality** between label parquet and packet-derived target set.
2. **Row-level equality** for every recomputed label against its banked value — not aggregates.
3. **Ordered hash equality**: sort by `tgt_id`, hash the label vector, compare.
4. **Anti-join count = 0**, both directions.
5. Prevalence and positive counts exact against `out/SEVERITY_RESULT.json`.

Any failure halts Phase 1. No outcome number is computed until all five pass.

### 7.2 Estimand A — breadth within count strata

Within strata of equal total prior advisory count `c`, does future `Y_B3` risk rise with
breadth `b`? Window primary = most recent iod; secondary = trailing 3 iod. Cells
`c ∈ {2..7, 8+}` × `b ∈ {1..min(c,9)}`. Per cell: n, n_vehicles, rate, vehicle-clustered
bootstrap CI (2000 reps, shared draws). Summary: `β_breadth|count`, clustered SE.

**Cell-count grid is published BEFORE the verdict rule is frozen.** Read-only preflight on
eval2024 (n=312,159 with an observed prior day) shows `{c=3,b=2}=12,819 · {3,3}=3,554 ·
{4,2}=7,053 · {4,3}=3,946 · {4,4}=813`. The `{4,4}` diagonal and `c=8+` tail are thin and get
coarsened **now**, not after seeing rates.

Zero-prior targets (18,506 eval / 76,394 train) are a **separate category**, never folded into
the `(0,0)` cell.

### 7.3 ⚠ Estimand B — the system-composition falsifier

**Breadth-conditional-on-count is not sufficient.** A 4-system history may simply contain
intrinsically higher-risk systems than a 1-system history — brakes-and-suspension is not
exchangeable with lamps-and-identification. Without this test, a positive Estimand A is
consistent with pure composition and says nothing about structure.

1. **Fit on TRAIN** an additive system-composition expectation:
   `logit P(Y_B3) ~ Σ_s β_s · n_advisories_in_system_s`, one term per canonical system, **no
   breadth or dispersion term**. This is the risk predicted by *which* systems were advised and
   *how much*, with no credit for structure.
2. **Score EVAL** with the TRAIN-fitted coefficients — frozen, never refitted on EVAL.
3. **Test on EVAL** whether breadth and dispersion (`adv_hhi_last`, `adv_entropy_last`) predict
   beyond it, using the additive expectation as an offset.
4. **Report the common system combinations within each count stratum** — for `c=4`, the
   observed multisets, their frequencies and their `Y_B3` rates — so composition is visible,
   not just adjusted for.

Estimand B, not A, is the load-bearing descriptive result.

### 7.4 Population and control stratifications

Discovery on TRAIN (999,999 rows / 297,055 vehicles / 2020-2023), **rule frozen**, confirmation
out-of-time on EVAL (330,665 / 2024). Stratifications: age quartiles, prior-depth bands, target
year, `postcode_area` region, make-model group, and the §5.6 tied-prior-day restriction.

### 7.5 Preregistered verdict

| Verdict | Condition |
|---|---|
| **SUPPORTED** | `β > 0`, CI clear of 0, in ≥5 of 7 count strata for `Y_B3`; **and** survives **Estimand B** on EVAL; **and** `β_survival ≥ 0.50` against age **and** prior-depth; **and** sign holds in every era stratum |
| **WEAK** | positive and CI-clear but fails Estimand B, or `β_survival < 0.50`, or era signs disagree |
| **FALSIFIED** | `β ≤ 0`, or CI includes 0 |
| **INCONCLUSIVE** | any required stratum below 500 rows or 50 positives |

```
β_pooled     = within-count coefficient on b, no control stratification
β_stratified = n-weighted mean of within-(count × control-stratum) coefficients
β_survival   = β_stratified / β_pooled
```

---

## 8. Model contrasts

### 8.1 ⚠ Mandatory existing-channel ablation — runs regardless

| arm | featureset |
|---|---|
| `L0m` | 241 **minus** the B0 advisory columns of §2.2 |
| `L0` | 241 |

`Y_B3`, k=5, paired. **This runs whether or not ADV moves.** Without `(L0 − L0m)` a
descriptive-positive / model-null cannot be interpreted, and that is the single most likely
outcome of this study. 10 fits.

### 8.2 Whole-block contrast

| arm | featureset | n |
|---|---|---:|
| base | 241 + `ADV_COVERAGE` + `ADV_AUDIT` | 253 |
| ADV | base + `ADV_CORE` | 298 |

k=5 paired, on `Y_B3` (primary), `Y_M1` (confirmatory), `Y_S1` (falsifier), `Y_M1N`
(exploratory). 40 fits. Plus grain sensitivity on `Y_B3` (261 vs 253), 5 fits.

Decomposition into the 8-concept ladder fires **only if `Y_B3` returns MOVED**. Stage-2
precedent: individual blocks never clear the bar, only cumulative gain does — decomposing first
spends fits measuring quantities guaranteed sub-floor.

### 8.3 Pre-registered bands — `Y_B3` only

Inherited from the burden prereg so the two studies are commensurable.

| mean paired Δ (k=5) | verdict |
|---|---|
| < +0.0007 | **NULL** |
| +0.0007 … +0.002 | detectable, do not adopt |
| ≥ +0.002 **and all 5 seeds positive** | **MOVED** → decomposition fires |
| ≥ +0.010 | **HALT** — leakage audit before any interpretation |

`Y_M1`/`Y_S1`/`Y_M1N` are reported against the same bands but **cannot trigger MOVED**.

---

## 9. Statistical protocol

**"Bootstrap CI excludes zero" is NOT the adoption gate.** B7's control fired at −9.134e-3 on
this surface.

### 9.1 ⚠ Terminology

`3 × (max − min)` over a k=5 seed vector (`mde.floor_from_seed_vector`) is an **empirical
refit-variability floor**. It is **not an MDE** — it is not derived from a power calculation
and carries no stated type-II error rate. Revision 1 used the terms interchangeably; that is
corrected throughout. `mde.mde()` computes an MDE and is reported separately where a σ exists.

⚠ `BANKED_SIGMA["lightgbm"] = None` — NO-FLOOR by design. `FLOOR_MEASURED_MIN_K = 5`, so
k must be 5 for the floor to report `MEASURED` rather than `LOW-K-PROVISIONAL`.

### 9.2 Three uncertainty quantities, all reported

1. **Seed panel** — all five per-arm AUROCs, and the empirical refit-variability floor.
2. **Paired vehicle-clustered bootstrap CI** over the evaluation set, from **saved row-level
   predictions** (`--preds-dir`), 2000 reps, shared draws.
3. **Seed-ensemble Δ** — mean predicted probability across the five seeds per arm, then a
   single Δ. Removes seed noise from the point estimate; reported alongside, not instead.

### 9.3 Reporting

**Report raw ΔAUC and Δ/nuisance side by side.** The standardised value never replaces the raw
effect. Cross-target comparison uses the standardised form (prevalence spans 7.9%-22.9%);
adoption bands apply to the raw form.

⚠ Bit-identical quantisation borders is a **CatBoost-only** mechanism (`fit_runner.py:137-193`);
`_fit_lightgbm` has no borders parameter. The requirement binds on CatBoost arms only.

⚠ Row-eligibility identity asserted across arms. Any drop triggers a matched-row re-run.

---

## 10. Measurement-process falsification

| Dimension | Feasible | Treatment |
|---|---|---|
| calendar era | yes | `β` by target year; `adv_pre2018_exposure_share` |
| geography | yes, coarse | `postcode_area` — see §10.2 |
| vehicle cohort | yes | make-model groups |
| **inspection-context change** | **NO** | **no station or tester identifier exists in the lake.** DROPPED and reported as dropped |

**Discriminating prediction:** recording drift (tests carrying ≥1 item, 59.2% → 60.4%,
2019→2023; advisory volumes 2.9M → 39.7M/yr across 2005-2014 while fail items stayed flat)
moves the breadth **level**. A prognostic signature predicts a stable risk **gradient** in
breadth given count. If `β` tracks the drift, it is recording practice.

### 10.1 ⚠ Not an instrument

Revision 1 called leave-one-vehicle-out area advisory intensity an "instrument". **It is not.**
Area has genuine causal routes to deterioration — roads, salt, climate, socioeconomics, fleet
age. It violates the exclusion restriction by construction.

It is reported as a **cross-fitted recording-practice proxy / sensitivity variable**:
computed leave-one-vehicle-out, **residualised on year, vehicle age, make-model group and
mileage band** (or entered matched on them) so that what remains is closer to local recording
practice than to local road conditions. Conclusion: if `β` shrinks materially once conditioned
on it, breadth is partly measuring where the car was tested. Directional evidence, not
identification.

### 10.2 ⚠ Conditional persistence null

Revision 1 compared observed system-recurrence against draws from an **unconditional** system
distribution. That null is too weak — it would be rejected by year effects, system prevalence,
or vehicle composition alone, none of which is the question.

The null **preserves year, per-system prevalence and vehicle composition**, permuting system
identities within strata of (target year × per-day breadth) while holding each vehicle's day
structure fixed. **Excess** persistence relative to *that* null is the quantity of interest. If
excess ≈ 0 while raw persistence predicts, persistence is a breadth-and-composition artefact.

### 10.3 Built-in negative control

`Y_S1`, already measured at 0.6545 — worse than broad failure.

---

## 11. Verdicts

| Verdict | Condition |
|---|---|
| **SUPPORTED** | §7.5 SUPPORTED (including Estimand B) **and** `Y_B3` returns MOVED **and** survives era/geography/cohort |
| **WEAK** | sub-floor positive with consistent sign across all five seeds and both `Y_B3`/`Y_M1` |
| **FALSIFIED** | Δ ≤ 0 with CI clear, or descriptive `β ≤ 0` |
| **INCONCLUSIVE** | \|Δ\| below the refit-variability floor with inconsistent sign, or cell-count failure |

⚠ Every INCONCLUSIVE **states the floor it was inconclusive against**.

⚠ **The expected outcome is descriptive-SUPPORTED / model-NULL.** That is a real result, not a
failure — but only if §8.1 has run. Its interpretation is then read directly off `(L0 − L0m)`:

| `(L0 − L0m)` | reading |
|---|---|
| **large** | the coarse advisory channel already carries the signal; **the richer representation is redundant** |
| **≈ 0** | the model is not using advisory information materially, coarse or rich — a different and more surprising finding |

---

## 12. Cross-target pattern

Prediction: breadth/persistence/trajectory add more for `Y_B3` and `Y_M1` than for `Y_S1`.
Tested on **Δ / nuisance**, with raw Δ reported alongside. ⚠ Never compare a subgroup AUROC to
a pooled benchmark — the NY cohort study established the error always flatters the subgroup.

---

## 13. Sequencing

1. **Freeze this revision + `.sha256`**
2. Taxonomy census (§4.2, full history, fail-closed) + correctness gates (§7.1)
3. **TRAIN descriptive discovery** (§7.2, §7.3)
4. **Freeze the descriptive rule**
5. **EVAL confirmation**
6. Feature build + tests + two-sided leakage control (§5, §6)
7. **Mandatory existing-channel ablation** (§8.1)
8. Whole-block ADV contrast (§8.2)
9. Conditional decomposition — only if the representation genuinely MOVES

---

## 14. Deliverable

`out/ADVSTRUCT_RESULT_2026_08_15.md` + `.json`. Thirteen outputs: ontology; feature
definitions; per-feature leakage audit with both control arms; prevalence/distribution/
missingness with observability denominators; within-count breadth tables; **system-composition
falsifier results**; `(L0 − L0m)` channel ablation; whole-block contrast across four targets;
seed panels; does breadth add beyond count; does persistence add beyond breadth; artefact
evidence; per-concept verdicts.

### 14.1 Secondary product metrics — descriptive only

AutoSafe is a ranking and triage product, so **AUROC remains the sole pre-registered adoption
gate**, and alongside it are reported: top-decile serious-burden capture and lift, top-1%
precision, Brier and log loss, and calibration slope/intercept.

⚠ **These are secondary descriptive outputs. They are not alternative routes to adoption and
may not rescue a failed AUC gate.** Stated here, before any number, so that reaching for one
later is visibly a protocol violation.

---

## 15. Declared risks

1. **A model-side null is the expected outcome.** Pre-declared so it is a result, not a
   disappointment, and so no primary is rescued post-hoc.
2. **The estimand includes owner/garage response.** A null is consistent with "no information"
   *or* "information that gets acted on." This design cannot separate them.
3. **B0 already carries a coarse advisory channel.** §8.1 is what makes the null interpretable.
4. **Recording drift can manufacture the gradient.** §10 is load-bearing.
5. **System composition can manufacture the gradient.** §7.3 is load-bearing.
6. **No station identifier exists.** The strongest artefact test cannot be run.
7. **`Y_M1N` is construct-dependent** and fenced to exploratory.
8. **The fit surface is under owner override with D13 open and the B7 control anomaly
   unexplained.** No model number is bankable as a verdict.

---

## 16. Deviations

Recorded in `factory/DEVIATIONS.md` under the deviate-with-test rule of `FACTORY_CONTRACT.md`:
a deviation ships with the test that made it necessary. Amendments after freeze carry their own
sha and state whether any result had been seen.

---

## 17. Revision ledger — R1 → R2

**No performance number, breadth gradient or outcome rate had been seen at the time of this
revision.** R1 was committed (`480bf22`) but never sha-frozen.

*Found by verification against artifacts:*

| # | Change |
|---|---|
| V1 | §4 section vocabulary was wrong — 19 raw strings, uncanonicalised, vintage-split |
| V2 | §7/§9 named no packet set → v2 only |
| V3 | §9 screened at k=2, unfloorable given `BANKED_SIGMA["lightgbm"] = None` → k=5 |
| V4 | §9 required bit-identical borders "as B7 did" — CatBoost-only mechanism |
| V5 | §8 ran 15 cells before knowing the block moves → whole-block first |

*Owner amendments, 2026-08-15:*

| # | Change |
|---|---|
| O1 | Framing narrowed: prognostic signature, not proof of a latent state; estimand explicitly under naturally occurring maintenance/repair behaviour (§1) |
| O2 | Taxonomy: full TRAIN+EVAL history census; fail-closed on unknown non-null sections; versioned ontology; `tyres` → `wheels_tyres`; coarser-grain sensitivity retained (§4) |
| O3 | **System-composition falsifier added** — TRAIN-fitted additive expectation, tested on EVAL; common combinations reported within count strata (§7.3) |
| O4 | Correctness gate hardened — set equality, row-level equality, ordered hashes, anti-join = 0 (§7.1) |
| O5 | `Y_M1N` demoted to exploratory; `Y_B3` sole MOVED trigger; `Y_M1` confirmatory; `Y_S1` falsifier (§3.2) |
| O6 | **`adv_trajectory_class` is no longer ordinal** — nominal, out of all fit arms; six numeric primitives carry trajectory instead (§5.2) |
| O7 | Three-state NULL semantics made explicit; rates/slopes NULL when undefined even where counts are certain zeros (§5.5) |
| O8 | Same-day dedup at `(tgt_id, p_date, canonical_system, item_key)`; tied-prior-day sensitivity; permutation invariance explicitly declared insufficient (§5.6) |
| O9 | Block split into `ADV_CORE` / `ADV_GRAIN` / `ADV_COVERAGE` / `ADV_AUDIT`; **coverage and audit columns in BOTH arms**; column count reconciled (§5.1) |
| O10 | **Existing-channel ablation made mandatory and unconditional** (§8.1); §2.2 corrected to state AutoSafe already contains a coarse advisory channel |
| O11 | Area intensity is **not** an instrument — cross-fitted recording-practice proxy, residualised/matched; persistence null made conditional on year, system prevalence and vehicle composition (§10.1, §10.2) |
| O12 | `3 × (max−min)` renamed **empirical refit-variability floor**, not MDE; row-level predictions saved; paired clustered bootstrap and seed-ensemble Δ added; raw and standardised Δ both reported (§9) |
| O13 | Secondary product metrics added as descriptive-only, explicitly barred from rescuing a failed AUC gate (§14.1) |
| O14 | Model results stay EXPLORATORY while D13 locks **and** the B7 control anomaly remain open — both conditions (§3.3) |
