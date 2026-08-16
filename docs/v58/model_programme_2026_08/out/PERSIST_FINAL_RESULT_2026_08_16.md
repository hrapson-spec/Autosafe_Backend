# PERSIST — final result

**Prereg** `PREREG_PERSIST_2026_08_16.md` sha `424dfdd4af84ea56…`
**Amendment** `AMENDMENT_PERSIST_A1_2026_08_16.md` sha `e32666a2478f5e58…` (boundary commit `d393d7a`)
**Closed** 2026-08-16

---

## SCIENTIFIC QUESTION

Does repeated advisory status identify persistent deterioration?

## MECHANISM VERDICT

> ### NOT SUPPORTED — on both surfaces, by the frozen rule, with adequate power

| | TRAIN (flat4y 2020-23) | EVAL (2024) |
|---|---:|---:|
| realised `p0` | 0.108925 | 0.105454 |
| **RR 1.20 bar** | **2.1785 pp** | **2.1091 pp** |
| Cohort B effect | **+1.4522 pp** (RR 1.1333) | **+1.6859 pp** (RR 1.1599) |
| CI95, 2000 vehicle-clustered reps | [+1.2334, +1.6775] | [+1.3468, +2.0267] |
| CI upper vs bar | 1.6775 **< 2.1785** | 2.0267 **< 2.1091** |
| Gate B condition (1) | **FAIL** | **FAIL** |
| verdict | **NOT SUPPORTED** | **NOT SUPPORTED** |

On both surfaces the interval **excludes** the pre-declared materiality bar rather than
merely failing to reach it. This is a precise measurement of a below-threshold effect, not
an ambiguous near miss.

## KEY EFFECT

Standardised same-system risk difference, A→A versus genuinely new C→A, across all nine
systems, risk scale, g-computation:

```
C→A same-system risk (base)   TRAIN 0.1089   EVAL 0.1055
A→A adjusted excess           TRAIN +1.4522 pp   EVAL +1.6859 pp
relative risk                 TRAIN 1.1333       EVAL 1.1599
```

**The effect replicated out-of-time**, slightly larger on EVAL, and remained below bar on
both.

## SPECIFICITY

| | TRAIN | EVAL |
|---|---:|---:|
| same-system RR | 1.1333 | 1.1599 |
| other-system RR | 1.0148 | 1.0215 |
| **excess-risk ratio** | **9.013** | **7.421** |

The excess concentrates on the advised system 7–9× more than on the rest of the vehicle.
General neglect would raise both together. It does not.

## DOSE RESPONSE — **PREREGISTERED FAILURE**

§8B predicted `Risk₀ < Risk₁ < Risk₂ < Risk₃₊`. Adjusted effect versus run 0:

| run | TRAIN | EVAL |
|---|---:|---:|
| 1 | +1.5539 pp | +1.6703 pp |
| 2 | +1.3689 pp | +1.5046 pp |
| 3+ | **+0.9075 pp** | **+1.7934 pp** |

**FAILS on both surfaces.** TRAIN declines with run length; EVAL is non-monotone without a
clear trend. Neither shows the predicted gradient. Recorded as a FAIL, not softened.

This argues specifically against a chronic-**progressive** deterioration reading, and
against encoding run length as a monotonically increasing deterioration score.

## OTHER LEGS

| leg | TRAIN | EVAL | |
|---|---|---|---|
| **8C anatomical** | same-RfR 75.8% vs chance 30.7% → **2.47×** | 75.1% vs 28.1% → **2.67×** | HOLDS |
| **8G placebo** | destroys **98.7%** | destroys **88.0%** | HOLDS |
| **8D strict novelty** | +1.8493 pp (RR 1.1766) vs +1.4522 headline | not run (not required) | strengthens |
| **8E resolution** | A→C carries **+1.7747 pp / RR 1.4495** over C→C, n=5.6M | not run (not required) | observed |
| **8F station/tester** | **DROPPED** — no station or tester identifier exists in the lake | — | unresolved confounding |

**§10.3 concordance — NOT MATERIALLY DISCORDANT on both surfaces.**
`D = Δ_A − 0.5·Δ_B`: TRAIN +0.7645 [+0.2640, +1.2829]; EVAL +0.8529 [+0.0853, +1.6640].
Both lower bounds above zero, so Δ_A > 0.5·Δ_B is positively established. Holding current
burden nearly constant does not attenuate the effect.

## ML QUESTION

**Not run.** Gate C was dropped at design time (D2): every PERSIST column is
`research_only_input`, because advisory *system* is not servable and episode
reconstruction needs research-only `test_type`. **ADOPT was never an available verdict.**

## COVERAGE

Cohort B 412,960 TRAIN / 172,304 EVAL (target × advised system).
Cohort A 62,917 / 25,372. 2000/2000 replicates on both surfaces, **zero replicate failures**.

## FINAL CONFIRMATION

**NOT AVAILABLE — declared, seal preserved.** `confirm2025h2` remains unscored (D7). Per
§15 risk 9, eval2024 is an **adaptively reused evaluation surface** with ≥7 prior
outcome-conditional reads; PERSIST is a **pre-frozen test of a previously unqueried
contrast on a repeatedly-read surface**, not an independent or pristine confirmation.

## VERDICT

> ### MECHANISM: NOT SUPPORTED · ML: NOT RUN (barred by design)

## INTERPRETATION

Repetition of an advisory marks genuinely elevated subsequent same-system risk. The effect
replicates out-of-time, is specific to the advised system by a factor of 7–9, survives a
conditional permutation placebo that destroys 88–99% of it, and sharpens at item grain
2.5–2.7× above chance. It is nonetheless **smaller than the effect declared materially
interesting before any data was seen**, on both surfaces, with the interval excluding that
threshold. Persistence behaves as a **risk marker/state**, not as a chronic-progressive
dose — the run-length gradient predicted by the mechanism failed on both surfaces.

---

## CORRECTIONS AND DEVIATIONS

| ref | what |
|---|---|
| `DEVIATIONS.md §5` | ADVSTRUCT C1 declared INCONCLUSIVE: the bar had 7.26% power against its own alternative |
| `DEVIATIONS.md §6` | Cohort B count corrected 312,789 → 172,304; frozen count contradicted the frozen state ladder. Pre-outcome |
| `DEVIATIONS.md §6a` | Cohort A corrected 40,911 → 25,372. Pre-outcome |
| A1 §1 | **An interim report described TRAIN as "a SUPPORTED reading". Wrong** — Gate B condition (1)'s third sub-clause was not checked against the RR 1.20 bar |
| A1 §3 E1 | membership flicker: per-replicate fit success was setting the estimand; measured 16.0% of cohort-A replicates |
| A1 §3 E3 | sklearn lbfgs under-converged (max\|score\| ~1e-4); replaced by IRLS (7e-14) |
| Phase 1 | TRAIN state first built from a **v1 packet set** (50.4% NULL `defects_json`); rebuilt from v2 (1.0%); gate G6 added and proven to fire |
| `PERSIST_SECT_INDEX.json` | `sect_NN` had **no banked name mapping**; recovered and verified against `SEVERITY_RESULT.json` |
| `PERSIST_B0_ADVISORY_COLUMNS.json` | audit record corrected: **53 of 104** B0 columns are advisory-derived, not "≥23" |

## NOT RUN, NOT REQUIRED

RfR+location chance baseline; A→C vs A→A contrast; 8D/8E on EVAL. None was needed for
either gate and **no claim rests on any of them**.

## OPEN QUESTIONS — for a separate programme, not this one

Survivorship among long-run vehicles; the owner/garage repair channel (inside the estimand
by §1, not adjustable away); why the 3+ group falls on TRAIN; the location-specific chance
baseline; station/tester confounding, which is **unresolvable without data that does not
exist**.

## ARTIFACTS

`PERSIST_PHASE2_{train_flat4y,eval2024}.json` · `PERSIST_FALSIFY{,2}_{train_flat4y,eval2024}.json`
`PERSIST_EVAL_VERDICT.json` · `PERSIST_CORRECTNESS_GATE.json` · `PERSIST_DATA_AUDIT.json`
`PERSIST_ESTIMATOR_VALIDATION.json` · `PERSIST_POWER_AT_BAR.json` · `PERSIST_SECT_INDEX.json`
`persist/state_{train_flat4y,eval2024}.parquet`
