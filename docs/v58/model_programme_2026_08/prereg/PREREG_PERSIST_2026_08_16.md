# PREREG — PERSIST: advisory persistence as a measurement of latent unresolved deterioration

**Written 2026-08-16, BEFORE any outcome quantity conditional on persistence state existed.**
Every number quoted below is either (a) a prior-side count carrying no outcome information,
(b) a figure already banked by an earlier study and cited to its artifact, or (c) arithmetic on
(a) and (b). **No same-system failure rate conditional on any transition has been computed.**

Owner: Henri. Sequencing and scope set by five rounds of owner decisions, recorded in §3.

---

## 1. Claim under test

> Among vehicles presenting with the same currently advised system and comparable observable
> current condition, does a system that was **also advised at the preceding annual MOT episode**
> carry materially greater subsequent major/dangerous failure risk **in that same system** than a
> genuinely newly appearing advisory?

Advisory persistence is treated throughout as a **measurement of a proposed latent state**, never
as a cause. The estimand is prognosis **under the maintenance and repair behaviour actually present
in the population** — an advisory is a notification that can itself trigger repair, so a null is
consistent with "no information" *or* "information owners act on." This design cannot separate
those and does not claim to.

Three questions, answered separately, never substituted for one another:

| | Question | Where |
|---|---|---|
| Q1 | **Association** — does a repeated advisory predict greater same-system risk? | §7 |
| Q2 | **Mechanism** — is the excess specific to the advised system, dose-dependent, and anatomically sharpening? | §8 |
| Q3 | **ML value** — does explicit persistence add out-of-sample beyond what AutoSafe already encodes? | §10 |

**Success on one does not establish the others.** §11 fixes the reading of every combination.

---

## 2. Prior art — declared in advance, not rediscovered

| Artifact | Result | Bearing |
|---|---|---|
| `out/ADVSTRUCT_RESULT_2026_08_15.md` | breadth beyond composition **+0.046024**, CI [+0.0242, +0.0687], out-of-sample; composition absorbs **77.8%** | Sibling lane. Estimands declared disjoint at §4. |
| `out/B7_K5_RESULT.json` | 34 richer-history cols, Δ **+2.765e-04**, t=0.72, seed 303 negative; k≈76 needed | History *representation* is a measured null on that surface. |
| `out/NY_COHORT_STUDY_2026_08_15.md` | persistence-cohort ΔAUROC **−0.0238**, 82% case-mix | ⚠ Falsified persistence as a **ranking aid**. Did NOT test persistence as a **risk factor** — prevalence rose P0 17.7% → P3 45.4%. Different estimand. |
| `out/B3_BURDEN_RESULT.json` | 12 prior-burden cols, **+1.68e-04**, one seed negative → NULL | ⚠ `burden_features.py:52` filters `rfr_type_code IN ('F','P')` — **advisory items structurally excluded**. Its own prereg names advisory persistence out of scope. |
| `out/CANONICAL_CEILING_STATEMENT.md` | "the binding constraint is neither the objective nor the representation" | A model-side null is the **expected** outcome. |
| `out/CAL_RESULT.json` | +1.294e-04, powered null | — |

**A model-side null is pre-declared as the likely outcome and is a result, not a disappointment.**
The value of this study rests on §§7-8, which need no model.

---

## 3. Owner decisions (locked before freeze)

| # | Decision |
|---|---|
| D1 | Separate prereg, run **concurrently** with ADVSTRUCT; estimands declared disjoint (§4). |
| D2 | **Science only. No adoption gate.** Every PERSIST column is `research_only_input`. ADOPT is not an available verdict. |
| D3 | Full ML ladder A+B, **whole-block first**; decomposition fires only on MOVED. Experiment B is a **false-null guard**, not a product comparison. |
| D4 | All nine systems retained; one hierarchical model, partial pooling. **Sole confirmatory estimand = the population-standardised same-system persistence effect in Cohort B** (§6.3). Cohort A is a prespecified clean-room mechanistic sensitivity analysis on the same estimand, **reported regardless of result**. System-specific, run-length and anatomical effects are hierarchical secondaries and **cannot individually determine PASS/FAIL**. |
| D5 | **Power-at-bar precondition** — no bar freezes without its MDE on confirmation row counts and P(pass \| discovery effect true). §9 is that appendix. |
| D6 | Banked flat4y → eval2024 contrast primary for the ML half; rolling folds are stability evidence. |
| D7 | **No final confirmation surface.** `confirm2025h2` stays sealed. Declared, not manufactured. |
| D8 | ADVSTRUCT C1 → INCONCLUSIVE (executed; `out/ADVSTRUCT_RESULT_2026_08_15.md`, `factory/DEVIATIONS.md` §5). |

---

## 4. Disjoint-estimand declaration

Parent sibling: `PREREG_ADVSTRUCT_2026_08_15.md` sha `35ee4828c47f4b88…`, amendments A2
`3579ed437b6674dc…` and A3.

| | ADVSTRUCT owns | PERSIST owns |
|---|---|---|
| Estimand | breadth of advisories **within count strata** | **same-system state transition** A→A vs C→A |
| Grain | canonical system, most recent prior day | canonical system **× multiple prior days**; also item (`rfr`) and item+location (`rfr`+`loc`) |
| Outcome | `Y_B3` (≥3 M/D, any section) | **same-system** M/D at t+1 (§6.4) |
| Falsifier | system composition | same-system vs other-system specificity |

Overlap is confined to ADVSTRUCT's five `adv_*` persistence columns
(`_n_persistent_systems`, `_max_persistence_run`, `_recur_frac`, `_n_systems_ge2days`,
`_n_systems_ge3days`). PERSIST **cites and reuses** them; it does not re-emit them.

**A third lane is live on the same surface.** `PREREG_OVERFIT_2026_08_16.md` (frozen, sidecar
present) asks whether the V58 research CatBoost overfits — 4 single-parameter arms × 2 seeds ×
{eval2024, drift2025h1}, registered but not yet executed at the time of this freeze. Its estimand
is a **generalisation gap on a fixed feature set**, disjoint from both breadth-within-count
(ADVSTRUCT) and same-system state transition (PERSIST). It independently declares `confirm2025h2`
out of bounds, so all three live lanes agree on the seal.

All three lanes read `eval2024`. Each appends to `out/EVAL2024_READ_LOG.json` so the surface's total
read count is auditable in one place. ⚠ Any lane may only claim the §15 risk 9 terminology for
itself; none may describe the surface as fresh.

---

## 5. Substrate, episodes, and state

### 5.1 Prediction-time contract

```
t-1  preceding completed annual MOT episode
t    most recent completed annual MOT episode   (= AutoSafe's most recent prior test-day)
t+1  target initial MOT                          (= the label)
```

Every feature is observable at or before completion of t. Fatal gates, exit 2, write nothing:
`G_strict_date_violations`, `G_self_reference_violations`, `G_unknown_system > 0`. The
forbidden-column scan runs against **the SQL actually executed** via `severity_collect`'s
`RecordingConnection`, never module source (the G0.9 self-match trap).

### 5.2 Episodes

An initial MOT plus its PRS/repair/retest activity is **one** episode. State derives from the
**initial**, using `p_ttype`.

⚠ **Within-day `test_id` ordering is never used** — measured agreement with truth is **49.91%**,
chance, and **35.09%** of targets carry ≥1 prior day with both a PASS and a FAIL record. Day-union
dedup at `(tgt_id, p_date, canonical_system, item_key)`, `item_key` = `rfr` when present else
normalised item text.

Validation stats published before any outcome number: raw tests, reconstructed episodes, retest
count and share, retest-interval distribution, and worked examples proving fail→retest collapses.

### 5.3 State ladder

`C` clean · `A` advisory only · `M` ≥1 minor, no M/D · `F` ≥1 major or dangerous.

⚠ **Pre-2018 items are `sev: pre2018_ungraded` — MINOR does not exist in that regime.** The ladder
is **C/A/F** for pre-2018 prior days, carried with an explicit regime flag. Ungraded is never mapped
to CLEAN. `F → A` is **not** a new advisory and is a distinct retained transition.

Three-state NULL semantics: no prior test / observable-with-zero / present-but-unobservable. Counts
may be certain zeros; **rates, slopes and run fractions are NULL wherever the denominator is
undefined**. Reuse `blocks.item_graded` and `blocks._coverage_status` — no third implementation.

### 5.4 Taxonomy

`ADVSTRUCT_ONTOLOGY_V1` (`out/ADVSTRUCT_TAXONOMY.json`), reused verbatim, fail-closed. Any non-null
`sect` outside it raises and halts.

---

## 6. Cohorts, estimand, outcome

### 6.1 Cohort B — confirmatory (D4)

Targets with ≥2 item-observable prior days and ≥1 advised canonical system at t. Unit =
(target × advised system). **eval2024: 312,789 pairs — A→A 165,436 / C→A 147,353.**

Conditioned on: current advisory count in that system, total current advisory count, number of
advised systems, current minor count, age, mileage, mileage accumulation, inter-test interval,
make/model group, fuel, calendar period, history depth, historical failure count, historical M/D
burden. **No control may contain future information.**

### 6.2 Cohort A — clean-room mechanistic sensitivity (D4)

At t: Class 4 initial MOT, modern regime, **passes**, **exactly one advisory in total**, no minor
defects, advisory in system s, valid observable state at t-1, observable initial MOT at t+1.
**eval2024: 40,911 pairs — A→A 22,431 / C→A 24,093** across the 9 systems (nested within B).

Current measured burden is held almost perfectly constant. **Reported regardless of result.**

### 6.3 The confirmatory estimand

One hierarchical model per cohort: system-specific baseline risk **and** system-specific persistence
effects under partial pooling, with flexible (non-linear) terms for age and mileage.

> **Primary = the population-standardised average persistence effect across all nine systems, on
> the RISK scale**, obtained by g-computation over the observed system mix: predict each unit's
> risk under A→A and under C→A, average with population weights, difference.

**Not** the mean random-effect coefficient on the log-odds scale — log-odds are non-collapsible and
their average is not the population effect. Reported as absolute percentage-point difference,
relative risk, and vehicle-clustered CI.

System-specific effects are reported with uncertainty **and shrinkage**, as secondaries. **No system
receives its own PASS/FAIL.**

### 6.4 Outcome

Same-system major/dangerous at t+1, from banked `sect_NN_n_md` via `out/PERSIST_SECT_INDEX.json`
(recovered and verified 2026-08-16; TRAIN/EVAL mappings identical; `positive_n` matches
`SEVERITY_RESULT.json` exactly for all 14 columns).

⚠ **The bridge is many-to-one:**
`wheels_tyres = sect_06 + sect_12` · `seatbelts_srs = sect_07 + sect_08`.
`identification` (sect_03) and `not_tested` (sect_02, sect_09) are **barred from the same-system
outcome** and retained only for any-failure outcomes.

Secondary outcomes: any next-MOT failure · `Y_B3` · M/D count · **other-system** M/D.

### 6.5 Discovery and confirmation

Discovery on TRAIN (flat4y, 2020-2023). **Rule frozen in an amendment carrying its own power-at-bar
table.** Then a **pre-frozen test of a previously unqueried contrast on `eval2024`, a repeatedly-read
surface** (§15 risk 9 — this is the required terminology, not "confirmation on a holdout"). No EVAL
outcome-conditional quantity is computed before that amendment is sha-frozen.

---

## 7. Q1 — descriptive transitions (no model)

Hardened correctness gate first (ADVSTRUCT §7.1: exact `tgt_id` set equality, row-level label
equality, ordered hash equality, anti-join = 0 both directions, prevalence exact). **No outcome
number until all five pass.**

Transition tables `C→C`, `C→A`, `A→C`, `A→A`, `F→A`, `A→C→A` for all 9 systems and pooled, each with
n, n_vehicles, same-system M/D rate, other-system M/D rate, any-failure rate, `Y_B3` rate, mean M/D
burden, and vehicle-clustered bootstrap CI (2000 reps, shared draws).

**All nine systems remain in the hierarchical model**, fixed in advance and frozen before any
outcome inspection. No system is dropped, promoted or demoted on precision or observed effect size.
**There is no `k-of-9 systems significant` rule** (§9.2 shows why none could be admissible).
System-specific effects are secondary heterogeneity estimates, not separate PASS/FAIL tests.

---

## 8. Q2 — adversarial falsification

| Leg | Design |
|---|---|
| **8A specificity** | same-system vs other-system effect from the same standardisation. Predicted RR_same > RR_other. Approximate equality ⇒ whole-vehicle/maintenance state, not component deterioration. |
| **8B dose-response** | run 0/1/2/3+ (eval2024: 98,521 / 56,879 / 26,228 / 30,767). n reported at every level; sparse levels never collapsed before their distribution is inspected. |
| **8C anatomical** | system → +`rfr` → +`rfr`+`loc`. ⚠ **Requires a chance baseline**: expected same-item recurrence under a random draw from that system's own marginal item distribution. Systems with few distinct RfR codes force same-item recurrence by construction, so the raw ladder (147,490 → 131,245 → 121,055; 18% attrition) is **uninterpretable without it**. |
| **8D stricter novelty** | `C(t-2) → C(t-1) → A(t)` vs persistent, testing whether C→A hides one-test disappearances. |
| **8E resolution** | `A→C` vs `C→C`. Informative to AutoSafe whether or not PERSIST succeeds. |
| **8F tester/station** | ⚠ **DROPPED, not weakened.** No station or tester identifier exists anywhere in the lake (`ingest_results.py:43` carries `postcode_area` only). Reported as **unresolved confounding**. |
| **8G permutation placebo** | **Conditional null only**: permute system identity within strata of (target year × per-day breadth), preserving year, per-system prevalence and vehicle composition. The unconditional null is rejected by year effects alone and is not used. |
| **8H recording drift** | Discriminating test is **level vs slope**. Drift moves the level (advisory volumes 2.9M→39.7M/yr 2005-2014 with fail items flat; tests with ≥1 item 59.2%→60.4% 2019-2023, i.e. through train **and** eval). A real latent state predicts a stable risk **gradient**. |

---

## 9. Power at bar (D5) — the freezing precondition

`out/PERSIST_POWER_AT_BAR.json`. Inputs: prior-side cell counts (no outcome information) and
same-system M/D base rates already published in `SEVERITY_RESULT.json`. Because the clean-room base
rate `p0` is not knowable without an outcome read, **MDE is reported as a function of `p0` over a
grid**; the bar is frozen against the grid, never a guessed point value.

Pre-declared minimum interesting effect: **RR = 1.20**.

### 9.1 Confirmatory estimand — adequately powered

⚠ **Everything in §9 is design-stage sensitivity analysis, not the realised inference rule.** It
sizes the study and exposes where precision is thin. No quantity in §9 acts as a post-analysis
switch on any verdict.

Population-weighted standardised average, assuming homogeneous system effects:

| p0 | Cohort B MDE₈₀ | Cohort B power @ RR 1.20 | Cohort A MDE₈₀ | Cohort A power |
|---|---:|---:|---:|---:|
| 0.02 | 0.148 pp | 100.0% | 0.410 pp | 77.9% |
| 0.05 | 0.230 pp | 100.0% | 0.638 pp | 99.2% |
| 0.10 | 0.315 pp | 100.0% | 0.875 pp | 100.0% |
| 0.15 | 0.374 pp | 100.0% | 1.038 pp | 100.0% |

At `p0 = 0.02` Cohort A's prospective power is 77.9% — a design-stage signal that the clean-room
sensitivity analysis is the thinner of the two, as expected from its 7.6× smaller n. Cohort B is
comfortable across the whole grid. **Neither figure gates any verdict.**

**Planning calculations assume homogeneous system effects and are therefore approximate, and
optimistic where true between-system heterogeneity is material.** No heuristic SE inflation is
applied to compensate — that would bolt an ad-hoc correction onto a model that estimates the
quantity properly. Instead the realised analysis must:

1. estimate between-system heterogeneity **τ** through the prespecified hierarchical model (§6.3);
2. **report τ**;
3. report all nine partially pooled system effects with their shrinkage;
4. **propagate model uncertainty directly** into the population-standardised effect and its
   interval — not through any closed-form inflation factor.

### 9.2 Why no per-system bar exists

Power to detect RR = 1.20 at p0 = 0.05:

| system | Cohort A | Cohort B |
|---|---:|---:|
| wheels_tyres | 79% | 100% |
| brakes | 66% | 100% |
| suspension | 45% | 100% |
| noise_emissions | 18% | 85% |
| body_structure | 14% | 73% |
| lamps_electrical | 7% | 47% |
| steering | 8% | 37% |
| seatbelts_srs | 6% | 20% |
| visibility | 4% | 8% |
| **≥80% power** | **0 of 9** | **4 of 9** |

**No per-system counting rule is admissible.** A "CI-clear in ≥k of 9" bar would repeat ADVSTRUCT's
C1 failure at worse odds. This is why D4 makes per-system effects hierarchical secondaries.

### 9.3 Design diagnostic for the concordance contrast

Cohort A's prospective power to resolve a **halved** effect — the substantive floor used in §10.3:

| p0 | A resolves full effect | A resolves 0.5× effect |
|---|---:|---:|
| 0.02 | 78% | 28% |
| 0.05 | 99% | 59% |
| 0.10 | 100% | 89% |
| 0.15 | 100% | 98% |

⚠ **This table is prospective design characterisation only.** It describes where Cohort A is
*expected* to be weak. It does **not** determine the realised concordance verdict, and realised base
rate must never be used as a switch that decides whether §10.3 may fire. An arbitrary discontinuity
at 80% prospective power is explicitly rejected: the realised verdict comes from direct uncertainty
on the concordance contrast (§10.3), estimated on the data actually observed.

---

## 10. Frozen decision rule

### 10.1 Gate A — data validity

Proceeds only if: leakage audit passes both sides (§12); episodes reconstructed with retests never
counted as annual observations; taxonomy frozen; unknown history explicit; `G_unknown_system = 0`;
correctness gate 5/5.

### 10.2 Gate B — mechanism

**SUPPORTED** requires all three:

1. Cohort B standardised effect **> 0**, vehicle-clustered CI clear of zero, **and** point estimate
   ≥ the RR 1.20 equivalent at the realised `p0`;
2. survives conditioning on current condition and history (§6.1 control set);
3. **same-system effect exceeds other-system effect** (8A);

and **at least one** of: 8B dose-response monotone · 8C effect sharpening with anatomical
specificity against its chance baseline · 8E `A→C` materially below `A→A` · 8G placebo destroys or
substantially reduces the same-system effect.

**PARTIAL** — (1) and (2) hold, (3) fails or is equivocal.
**NOT SUPPORTED** — (1) fails with adequate power.
**INCONCLUSIVE** — (1) fails below the §9 floor; the floor is stated.

### 10.3 ⚠ The Cohort A concordance contrast (owner ruling, frozen)

The substantive floor is **Δ_A ≥ 0.5 · Δ_B**. Realised base rate and prospective power status play
**no part** in deciding whether this clause may fire; §9.3 is design characterisation only.

Because **Cohort A is nested within Cohort B**, the two effects are dependent and their difference
must be estimated jointly, never by comparing two independently-computed intervals. Prespecified
contrast:

```
D = Δ_A − 0.5 · Δ_B
```

estimated with the **same vehicle-cluster bootstrap** used everywhere else in this programme
(2000 reps, shared draws). For every replicate:

1. resample vehicle IDs with replacement;
2. reconstruct the **Cohort B** standardised persistence effect Δ_B,b;
3. reconstruct the **nested Cohort A** standardised persistence effect Δ_A,b on the corresponding
   resampled vehicles;
4. compute `D_b = Δ_A,b − 0.5 · Δ_B,b`.

The bootstrap distribution of `D` carries the verdict:

| Verdict | Condition |
|---|---|
| **MATERIALLY DISCORDANT** | the upper 95% confidence bound for `D` is **below 0** |
| **NOT MATERIALLY DISCORDANT** | Δ_A is at least 50% of Δ_B **and** the data do not establish material attenuation |
| **INCONCLUSIVE** | Δ_A point estimate is below the 50% floor, or even opposite-sign, but uncertainty does not establish `D < 0` |

> **A positive Cohort B result accompanied by a MATERIALLY DISCORDANT Cohort A result supports a
> predictive persistence association but NOT the proposed burden-independent chronic-deterioration
> mechanism.**

That reading is frozen now, before any rate is seen, precisely because it is the combination most
easily rationalised after the fact.

⚠ **This is a within-sample mechanistic consistency and sensitivity test, not independent
replication.** Cohort A's rows are a subset of Cohort B's; the contrast asks whether the effect
survives holding current burden nearly constant, not whether it reproduces on new data.

### 10.4 Gate C — ML

There is no adoption gate (D2). The ML half reports whether explicit persistence adds out-of-sample.
Bands inherited from ADVSTRUCT §8.3, **not invented**:

| mean paired Δ AUROC (k=5) | reading |
|---|---|
| < +0.0007 | NULL |
| +0.0007 … +0.002 | detectable |
| ≥ +0.002 **and all 5 seeds positive** | MOVED → decomposition fires |
| ≥ +0.010 | **HALT** — leakage audit before any interpretation |

No lower success threshold may be invented after seeing results.

**ML evidence hierarchy, frozen:**

1. **Banked 2020-2023 → eval2024 contrast — the primary ΔAUROC estimate.** Chosen because it is
   past → future, large (755,389 training rows), experimentally characterised (per-seed sd
   9.53e-05), and commensurable with the banked B3 / BURDEN / B7 studies. Its governance status is
   not upgraded beyond §15 risk 9.
2. **Rolling temporal folds — a stability and transport challenge**, each reporting its own
   fold-specific refit floor. They can qualify or undermine the primary; they do not replace it.
3. **No standalone final-confirmation read exists for PERSIST** (D7).
4. `confirm2025h2` stays sealed for final-architecture evaluation.

⚠ **Rolling folds may not be averaged with the banked contrast to create a new primary effect.**
They are a separate question answered on a noisier substrate; pooling them would silently redefine
the estimand and break commensurability with every banked comparator.

### 10.5 Mandatory channel ablation — three frozen contrasts

**Runs unconditionally, whether or not PERSIST improves the model.**

| arm | definition | n |
|---|---|---:|
| `L0m` | frozen 241 baseline **minus** the advisory-column membership recorded in `out/PERSIST_B0_ADVISORY_COLUMNS.json` | **184** |
| `L0` | existing 241 baseline | 241 |
| `L1` | `L0 + PERSIST` | 241 + PERSIST |

Frozen contrasts:

| contrast | quantity |
|---|---|
| **`L0 − L0m`** | unique incremental value of the existing coarse advisory block, **conditional on all remaining baseline features** |
| **`L1 − L0`** | incremental value of explicit PERSIST features beyond current AutoSafe |
| **`L1 − L0m`** | value of the rich advisory/PERSIST representation relative to the baseline with the frozen coarse advisory block removed |

Identical rows, seeds, hyperparameters, preprocessing and evaluation protocol across all three.

| Pattern | Interpretation |
|---|---|
| `L0−L0m` material; `L1−L0 ≈ 0` | Existing coarse advisory block has unique predictive value and richer persistence is **redundant conditional on it** |
| `L0−L0m` material; `L1−L0` material | Existing advisory representation matters **and** PERSIST contributes additional unique information |
| `L0−L0m ≈ 0`; `L1−L0` material | Coarse block contributes little unique value, but explicit persistence unlocks useful longitudinal structure |
| both ≈ 0 | Neither tested advisory representation contributes material **unique marginal signal conditional on the remaining model features** |

⚠ **`L0 − L0m ≈ 0` does NOT license the conclusion that "AutoSafe does not use advisory
information."** L0m is a **declared-membership** ablation, not a lineage purge. Retained features may
still encode advisory-derived or highly correlated information — concretely, B2's ~50 per-category
columns are computed over **any disposition** (`blocks.py:262-272`) and therefore carry advisory
signal that L0m leaves in place.

The stronger claim — *"what happens if AutoSafe has no advisory-derived information at all?"* —
requires a separate **lineage-purged** ablation removing all direct and derived descendants of
advisory data. That is **out of scope for PERSIST-1** unless separately preregistered.

⚠ **Membership correction on record.** `SERVE_VIEW_AUDIT.md` and `PREREG_ADVSTRUCT` §2.2 record
"≥23 B0 advisory columns". Code inspection for this prereg finds **53 of 104 B0 columns (51%) are
advisory-derived** — the audit counted name-matched columns only and missed three families that are
advisory-gated at source: `text_*`/`has_*_history`/`mechanism_count`/`dominant_mechanism` (fed by
`text_signals_total`, incremented **only** inside the ADVISORY branch,
`feature_engineering_v55.py:381-393`), `mech_decay_*`/`mech_risk_driver` (component advisory
counts, `:610-613`), and `historic_negligence_ratio_smoothed`/`negligence_band`/
`raw_behavioral_count` (functions of `prev_count_advisory`, `:886-904`). Plus
`b3_n_advisory_items` and three `b4_*adv_to_fail*` columns outside B0.

---

## 11. Interpretation grid — fixed in advance

| Q2 mechanism | Q3 ML | Reading |
|---|---|---|
| SUPPORTED | positive | Strongest outcome: AutoSafe can measure **and** exploit a persistent longitudinal deterioration state. |
| SUPPORTED | null | Persistence is real; existing history variables already encode it. **Interpretable only against `(L0 − L0m)`** (§10.5). |
| NOT SUPPORTED | positive | Useful historical risk marker; evidence does **not** support a component-specific unresolved-deterioration explanation. Candidate latent constructs: general neglect, owner propensity, systemic deterioration, tester behaviour. State causal reading conservatively. |
| NOT SUPPORTED | null | Persistence adds nothing beyond current condition and existing history. Reject and stop. |
| B positive, A discordant | either | **Predictive association, not burden-independent mechanism** (§10.3). |

---

## 12. Leakage audit — two-sided

| arm | requirement |
|---|---|
| planted-leak (target-day same-system advisory injected) | audit **must** flag it and ΔAUC **must** move materially. If it passes clean, the audit is broken and the phase **halts**. |
| nominal-null (base vs base, seeds only) | ≈0 beyond refit variability. **Not assumed** — B7's fired at −9.134e-3. |

A green audit proves nothing until the fixture is shown able to fail.

---

## 13. Statistical protocol

**"Bootstrap CI excludes zero" is not a gate.** Measured on this programme: a paired CI on pure seed
noise excludes zero (+0.000575 [+0.000484, +0.000664], same arm, two seeds); and the measured refit
nuisance for `Y_B3` is −6.87e-4 with a CI excluding zero.

All three quantities reported, always:
1. seed panel (k=5) + empirical refit-variability floor `3 × (max − min)` — **which is not an MDE**;
2. paired vehicle-clustered bootstrap from saved row-level predictions, 2000 reps, shared draws;
3. seed-ensemble Δ.

Instrument characterised: `Y_B3` LightGBM per-seed sd **9.53e-05**, paired-delta sd 2.31e-04, refit
nuisance 1.74e-04 → **MDE₈₀ ≈ +2.9e-04 at k=5**, below the NULL band edge. This surface produces
powered nulls. `sec_per_fit` = 58.

⚠ Bit-identical quantisation borders is a **CatBoost-only** mechanism; the requirement does not bind
on LightGBM arms. ⚠ Row-eligibility identity asserted across arms; any drop triggers a matched-row
re-run. ⚠ Never compare a subgroup AUROC to a pooled benchmark — the error always flatters the
subgroup.

Metrics: ΔAUROC primary. Secondary: ΔAUPRC (**always ÷ prevalence**), Δlog-loss, ΔBrier,
calibration slope/intercept, top-decile capture and lift, top-1% precision. **Secondary metrics may
not rescue a failed primary** — stated here, before any number, so reaching for one later is
visibly a protocol violation.

---

## 14. Feature family (research-only)

`PERSIST-1A` basic · `1B` duration · `1C` fine-grained item/item+location · `1D` dynamics.

- **Observability and coverage columns sit in BOTH arms.** Otherwise a positive Δ driven by history
  depth or catalogue coverage is indistinguishable from one driven by persistence. This is the
  single most important structural rule, inherited from ADVSTRUCT §5.1.
- Same evaluation population scored in both arms. **Never restrict to persistence-observable
  vehicles** — that selects easier, history-rich vehicles and manufactures a gain.
- No hand-assigned risk weights. No target-derived encodings fitted outside training folds.
- Whole-block first (D3); `1A→1D` decomposition fires only on MOVED.

---

## 15. Declared risks

1. A model-side null is the **expected** outcome, pre-declared so no primary is rescued post hoc.
2. The estimand includes owner/garage response; a null cannot separate "no information" from
   "information acted on."
3. B0 already carries a coarse advisory channel — §10.5 is what makes a null interpretable.
4. Recording drift can manufacture a gradient — 8H is load-bearing.
5. **No station identifier exists**; the strongest artefact test cannot be run (8F).
6. Model results are **EXPLORATORY / NOT-BANKABLE-AS-VERDICT** while `D13_REPAIR →
   D13_SEMANTIC_LOCK → BASELINE_REENTRY_LOCK` are open **and** the B7 control anomaly (−9.134e-3)
   is unexplained. Both conditions. The descriptive half is unaffected and is prereg-bankable.
7. `confirm2025h2` is **unscored in this lineage** — verified zero fits and zero prediction files.
   It is not thereby free of prior exposure: the same calendar window was read in the goal-0750
   programme, whose own rebuilt 2025-H2 surface was adjudicated EXPOSED / INELIGIBLE. D7 keeps it
   sealed for the final frozen AutoSafe architecture and **it is not spent on PERSIST**, so nothing
   in this study depends on its status.
8. Cohort A is nested within Cohort B; the concordance clause of §10.3 is therefore a
   within-sample consistency check, not an independent replication, and is labelled as such.
9. ⚠ **`eval2024` governance status.** The persistence-conditioned same-system outcome contrast has
   not previously been queried. However, `eval2024` is an **adaptively reused evaluation surface**,
   with **at least seven prior outcome-conditional reads** in the wider AutoSafe programme
   (`out/EVAL2024_READ_LOG.json`: B7, CAL, NY cohort, SEVERITY, B3 reference, B3 burden, ADVSTRUCT;
   a lower bound, since it enumerates only reads still evidenced by a present artifact). PERSIST
   therefore constitutes a **pre-frozen test of a previously unqueried contrast on a
   repeatedly-read surface**, not an independent or pristine confirmation.

   **The result deliverables must use that terminology.** The words *uncontaminated*, *untouched*,
   *pristine* and *independent holdout* may not be applied to the PERSIST estimand or to
   `eval2024`.

   The primary ML evidence remains the banked full-size temporal contrast because it is past →
   future, large, experimentally characterised, and commensurable with the banked B3/BURDEN/B7
   studies — **but its governance status is not upgraded beyond what it actually is.**
   `confirm2025h2` remains sealed for the final frozen AutoSafe architecture and is **not spent on
   PERSIST**.

---

## 16. What may not happen after this freeze

- The confirmatory estimand may not change from the Cohort B population-standardised effect.
- Per-system, run-length or anatomical results may not be promoted to confirmatory.
- The RR 1.20 minimum interesting effect may not move.
- No control may be added to rescue a failed Gate B.
- The ML bands may not be lowered.
- `confirm2025h2` may not be scored.
- Amendments after freeze carry their own sha and state whether any result had been seen.

---

## 17. Deliverables

`PERSIST_DATA_AUDIT` · `PERSIST_MECHANISM_RESULT` · `PERSIST_FEATURE_SPEC` · `PERSIST_ML_RESULT` ·
one-page verdict. Verdict page carries `FINAL CONFIRMATION = NOT AVAILABLE — declared, seal
preserved` and the verdict set `MECHANISM-SUPPORTED / PARTIAL / NOT-SUPPORTED` ×
`ML-POSITIVE / NULL`. **ADOPT is not an available verdict.**
