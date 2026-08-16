# AMENDMENT A1 — TRAIN discovery closed; estimator corrected; EVAL rule frozen

**Parent:** `PREREG_PERSIST_2026_08_16.md` sha `424dfdd4af84ea5661ba4dc14599a0eb1cb91bed8fce68c53a9de65b85ef959e`
**Written:** 2026-08-16, after TRAIN discovery, **before any eval2024 outcome-conditional
quantity conditional on persistence state has been computed.**

This amendment closes TRAIN discovery, records the estimator corrections made during it,
and freezes the rule that will decide EVAL. Nothing below may be revised after the sha
sidecar exists except through `factory/DEVIATIONS.md`.

---

## 1. TRAIN VERDICT — Gate B: **NOT SUPPORTED**

§10.2 condition (1) requires three things together: effect > 0, vehicle-clustered CI clear
of zero, **and point estimate ≥ the RR 1.20 equivalent at the realised `p0`**.

| quantity | value |
|---|---:|
| realised `p0` (C→A same-system base rate) | 0.108925 |
| **RR 1.20 equivalent — the frozen bar** | **2.1785 pp** |
| observed Cohort B standardised effect | **1.4522 pp** (RR 1.1333) |
| vehicle-clustered CI95, 2000 reps | **[1.2334, 1.6775]** |

- effect > 0 — **PASS**
- CI clear of zero — **PASS**
- point estimate ≥ 2.1785 pp — **FAIL**
- CI upper bound 1.6775 < 2.1785 ⇒ **RR ≥ 1.20 is positively excluded at 95%**

Condition (1) therefore fails **with adequate power**, which §10.2 maps to exactly one
verdict:

> ### TRAIN Gate B = **NOT SUPPORTED**

Conditions (2) and (3) and the optional legs do not rescue this. The frozen table requires
**all three** of (1)(2)(3) for SUPPORTED. They are recorded below because they are
informative, **not** because they bear on the verdict.

⚠ **Recorded error.** An interim report of 2026-08-16 described TRAIN as "a SUPPORTED
reading". That was wrong: conditions (2) and (3) and the optional legs were checked and
the third sub-clause of (1) was not. The strength of 8A and 8G carried the reading past a
bar set in writing before any data was seen. The verdict above is the frozen rule applied
correctly.

### 1.1 The refined conclusion, stated narrowly

**Repetition of an advisory marks elevated subsequent same-system risk. The effect is real,
precisely measured, specific to the advised system, and survives every required
conditioning and falsification test — and it is SMALLER than the effect pre-declared as
materially interesting, with the interval excluding that threshold rather than merely
failing to reach it.**

Detectability and materiality are different quantities. This is a precise measurement of a
below-threshold effect, not an ambiguous near miss.

---

## 2. TRAIN results, recorded as observed

### 2.1 Primary

| | estimate | CI95 (2000 reps, vehicle-clustered) |
|---|---:|---|
| Cohort B (confirmatory) | +1.4522 pp | [+1.2334, +1.6775] |
| Cohort A (clean room) | +1.4906 pp | [+0.9594, +2.0290] |
| `D = Δ_A − 0.5·Δ_B` | +0.7645 pp | [+0.2640, +1.2829] |

τ = 0.0568. 2000/2000 replicates succeeded; **zero replicate failures**.

**§10.3 concordance: NOT MATERIALLY DISCORDANT.** D's *lower* bound is +0.2640 > 0, so
Δ_A > 0.5·Δ_B is positively established, not merely un-refuted. Holding current burden
nearly constant does not attenuate the effect (ratio 1.026).

### 2.2 Falsification legs

| leg | result | prediction |
|---|---|---|
| **8A specificity** | same-system RR 1.1333 vs other-system RR 1.0148; excess-risk ratio **9.013** | **HOLDS** |
| **8B dose-response** | adjusted vs run 0: run1 **+1.5539 pp**, run2 +1.3689, run3+ **+0.9075**. Largest at run 1, **declining** thereafter | **FAILS** |
| **8C anatomical** | same-RfR recurrence 75.8% vs chance baseline 30.7% → **2.47× lift** | **HOLDS** |
| **8D stricter novelty** | requiring C(t−2)→C(t−1)→A(t) raises the effect to **+1.8493 pp** (RR 1.1766) | strengthens |
| **8E resolution** | A→C carries **+1.7747 pp / RR 1.4495** over C→C (n=5.6M) — disappearance ≠ resolution | observed |
| **8G placebo** | conditional permutation destroys **98.7%** (+1.4522 → +0.0193 pp) | **HOLDS** |
| 8F station/tester | **DROPPED** — no station or tester identifier exists in the lake | n/a |

### 2.3 ⚠ 8B is a genuine preregistered falsification

§8B predicted `Risk₀ < Risk₁ < Risk₂ < Risk₃₊`. Observed: the effect is **largest at run 1
and declines with longer runs**. A step, not a gradient.

This is recorded as a **FAIL**, not softened. It argues specifically against a
chronic-*progressive*-deterioration reading, and against encoding run length as a
monotonically increasing deterioration score. Candidate explanations — survivorship among
long-run vehicles, and the owner/garage repair channel that §1 declares inside the estimand
— are **hypotheses for a separate programme**, not findings of this one.

### 2.4 Not run, not required

Neither is needed for the TRAIN gate and **neither was computed**:

- chance baseline for **RfR + location** (so the 22.4% same-RfR-and-location figure is
  uninterpretable and no claim rests on it);
- the **A→C vs A→A** contrast, which is Gate B criterion 6's phrasing; 8E as run measures
  A→C vs C→C per the §8 table.

---

## 3. Estimator corrections, frozen

Made during TRAIN discovery, all before the numbers above.

| # | Correction | Why |
|---|---|---|
| E1 | **Nine-system standardisation denominator, always.** Every system's rows enter the denominator in every replicate; it is never renormalised over survivors. | The prior version let per-replicate fit success set membership. Measured flicker: **16.0%** of cohort-A replicates (visibility, 200 resamples). Both the effect and the population it standardised to moved between replicates. |
| E2 | **Eligibility declared ONCE from the full sample**, per cohort and per outcome, on `GUARDS = {min_n:40, min_pos:10, min_treated:5, min_untreated:5}`. Ineligible systems take the **pooled coefficient via offset** in every replicate and keep their rows. | Numerical fit availability must not be part of the scientific estimand. |
| E3 | **IRLS replaces sklearn.** sklearn's lbfgs plateaus at max\|score\| ≈ 1e-4 regardless of tolerance; IRLS reaches **7e-14**. | The previous estimates came from an under-converged optimiser. Point estimates moved B +1.4535→+1.4522, A +1.4981→+1.4906. |
| E4 | **Bootstrap n-weights VARY** with each vehicle-level resample. | §6.3 says "g-computation over the observed system mix" and does **not** nominate the TRAIN composition as a fixed reference; §6.3 governs TRAIN and EVAL alike, so a fixed TRAIN reference would force EVAL to standardise to TRAIN, which it nowhere says. Owner ruling 2026-08-16. |
| E5 | **Replicate failure, not silent skip.** An *eligible* system that will not fit triggers a deterministic retry, then a recorded replicate failure with diagnostics. | Samples that break a fit are disproportionately tail-forming; dropping them biases intervals **narrow**. |
| E6 | **Interval definition frozen**: percentile, α = 0.05, `numpy.percentile` linear interpolation, [2.5, 97.5]. | — |
| E7 | Replicate-indexed RNG `default_rng([seed, rep])`; atomic checkpointing every 100 replicates. | Reproducibility of any single replicate; recovery without loss. |

Verified by `scripts/analysis/persist_test_estimator.py` (5 tests): IRLS closer to the MLE
than sklearn and agreeing to 1.04e-06 once sklearn is tightly converged; SEs identical to
1e-6 relative; warm starts inert to 1.9e-15; offset pinning exact to 1e-9; **own-estimate
set identical across 40 replicates and denominator = 9 in every one**; planted-effect
recovery and true-null behaviour preserved.

---

## 4. THE EVAL RULE — frozen, algorithmic, fixed before any EVAL result

### 4.1 Pipeline, applied without variation

1. Build the eval2024 frame by `persist_analyze.build_frame`, identical definitions.
2. **Eligibility:** run `persist_estimator.system_eligibility` on the **full eval2024
   sample**, separately for Cohort B and Cohort A, with `GUARDS` exactly as frozen in §3/E2.
   The resulting eligible **set may differ from TRAIN's**. That is correct and intended:
   **the RULE is frozen, not the set.** TRAIN's set is not transplanted.
3. Fit, pool (DerSimonian–Laird), shrink and g-compute exactly as `persist_estimator`.
4. Bootstrap: **2000 replicates** (§7/§10.3/§13 of the parent — frozen, not reduced),
   vehicle-clustered, `default_rng([20260816, rep])`, weights varying per resample.
5. Interval: percentile, α = 0.05, linear interpolation.

⚠ **Declared:** step 2 reads **marginal per-system event counts on eval2024**. That is an
outcome read. It is **not** conditional on persistence state, and it is step 1 of the frozen
pipeline rather than an exploratory look. It is declared here so it cannot later be
presented as anything else.

### 4.2 The EVAL verdict, computed mechanically

Only these quantities are computed, and the verdict is assigned by arithmetic:

```
p0_eval   = same-system M/D rate among C→A rows in eval2024
bar_eval  = p0_eval × 0.20 × 100          (pp)   [RR 1.20, unchanged]
effect    = Cohort B standardised risk difference (pp)
CI        = percentile [2.5, 97.5] of the 2000 vehicle-clustered replicates

C1 = (effect > 0) AND (CI lower > 0) AND (effect >= bar_eval)
C2 = model includes the §6.1 control set          [true by construction]
C3 = RR_same > RR_other                            [leg 8A on eval2024]
OPT = any of: 8B monotone · 8C sharpening vs its chance baseline · 8G placebo destroys

SUPPORTED      iff C1 and C2 and C3 and OPT
PARTIAL        iff C1 and C2 and not C3
NOT SUPPORTED  iff not C1, and the instrument had power (CI half-width < bar_eval)
INCONCLUSIVE   iff not C1 and the instrument lacked power; the floor is stated
```

**The RR 1.20 bar does not move** (parent §16). **No lower threshold may be introduced.**
A near miss is a miss. Any post-EVAL exploration is reported separately and **cannot alter
the confirmatory verdict**.

### 4.3 Replication reading, fixed in advance

| EVAL outcome | reading |
|---|---|
| effect reproduces, still below bar | persistence is an out-of-sample risk **marker**, below the pre-declared materiality bar on both surfaces |
| effect reproduces and clears bar on EVAL but not TRAIN | discordant across surfaces; report both, claim neither |
| effect does not reproduce | the TRAIN discovery **did not externally replicate** |
| 8B non-monotone again | preserve the refined interpretation: a risk **state/marker**, not a chronic-progressive dose |

---

## 5. What may not happen after this freeze

- The RR 1.20 bar may not move.
- The TRAIN verdict (NOT SUPPORTED) may not be revised on EVAL evidence.
- The estimator, eligibility rule, pooling, standardisation, interval definition and
  replicate count may not change between TRAIN and EVAL.
- Eligibility may not be transplanted from TRAIN to EVAL, nor recomputed after seeing
  EVAL effects.
- The uncomputed items of §2.4 may not be added to rescue a verdict.
- Feature-adoption work is a **separate programme** and may not be presented as part of
  PERSIST.

---

## 6. Provenance

| | |
|---|---|
| parent prereg sha | `424dfdd4af84ea5661ba4dc14599a0eb1cb91bed8fce68c53a9de65b85ef959e` |
| estimator | `scripts/analysis/persist_estimator.py` |
| driver | `scripts/analysis/persist_bootstrap.py` |
| tests | `scripts/analysis/persist_test_estimator.py` |
| TRAIN results | `out/PERSIST_PHASE2_train_flat4y.json` |
| legs | `out/PERSIST_FALSIFY_train_flat4y.json`, `out/PERSIST_FALSIFY2_train_flat4y.json` |
| state | `out/persist/state_train_flat4y.parquet` |
| deviations | `factory/DEVIATIONS.md` §6, §6a |

**Frozen 2026-08-16. No eval2024 persistence-conditioned outcome seen.**
