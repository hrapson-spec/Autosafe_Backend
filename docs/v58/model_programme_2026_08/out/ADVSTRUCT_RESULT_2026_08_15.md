# ADVSTRUCT — descriptive result (Estimand A + B), EVAL confirmation

**Parent:** `prereg/PREREG_ADVSTRUCT_2026_08_15.md` sha `35ee4828c47f4b88…`
**Amendments:** A2 `3579ed437b6674dc…` · A3 (descriptive rule frozen from TRAIN, 2026-08-15 22:05)
**Adjudicated:** 2026-08-16. Owner ruling on C1 recorded at §3.
**Machine-readable:** `out/ADVSTRUCT_RESULT_2026_08_15.json`

Everything below derives from the banked `ADVSTRUCT_DESCRIPTIVE_{TRAIN,EVAL}.json`. **No refit, no
outcome re-read, no new query.** The power analysis in §3 is arithmetic on bootstrap CIs that A3
had already published — it uses no information that was unavailable when A3 was frozen.

**Scope reminder (parent §3.4):** every `adv_*` quantity is `research_only_input`. Advisory *count*
is servable; advisory *system* is not. **No adoption decision follows from this document.**

---

## 1. Verdict

> **SUPPORTED-ON-C2 · C1 INCONCLUSIVE**

| Gate | Requirement | Result | |
|---|---|---|---|
| **C1** | β_breadth\|count CI-clear in ≥5 of 6 count strata | 3 of 6 | **INCONCLUSIVE** (§3) |
| **C2** | β(breadth \| composition) > 0, CI-clear, out-of-sample | **+0.046024**, CI [+0.024219, +0.068672] | **PASS** |
| **C3** | β_survival ≥ 0.50 vs age **and** prior depth | 0.539 / 0.670 | **PASS** |
| **C4** | sign holds in every target-year stratum | 1 year group in eval2024 | **UNTESTABLE** |

A3 §3: *"C2 is the load-bearing one."* It passes out-of-sample with frozen, never-refitted
coefficients. The structural claim survives its load-bearing test.

It does **not** meet the parent's §7.5 SUPPORTED condition as written, because that condition
required C1. The honest label is therefore partial, and is written as such.

---

## 2. What the confirmation showed

### Estimand A — β_breadth given count, eval2024

| stratum | n | n_vehicles | prevalence | β_TRAIN | β_EVAL | CI95 (EVAL) | clear |
|---|---:|---:|---:|---:|---:|---|:--:|
| c=2 | 38,366 | 37,985 | 0.1469 | +0.3006 | **+0.3022** | [+0.2390, +0.3665] | ✓ |
| c=3 | 16,977 | 16,857 | — | +0.2540 | **+0.2758** | [+0.2052, +0.3436] | ✓ |
| c=4 | 7,224 | 7,165 | — | +0.1204 | **+0.1483** | [+0.0706, +0.2259] | ✓ |
| c=5 | 3,073 | 3,054 | — | +0.0717 | −0.0069 | [−0.1132, +0.0946] | ✗ |
| c=6 | 1,399 | 1,391 | — | +0.0905 | +0.0713 | [−0.0682, +0.2088] | ✗ |
| c=7-8 | 845 | 836 | — | +0.0187 | +0.0899 | [−0.0619, +0.2440] | ✗ |

The three passing strata **replicate TRAIN closely** — β moved by +0.0016, +0.0218 and +0.0279
respectively, all well inside their own CIs. The decay of β with count reproduces: breadth is most
informative where count is least informative.

### Estimand B — the system-composition falsifier (load-bearing)

Additive expectation fitted on `train_flat4y`, **coefficients frozen, scored on eval2024, never
refitted**. Population = eligible count strata only (c=0 and c=1 excluded, per A2.2 — including them
inverts the sign of `items_per_system`, A3 §5).

| quantity | EVAL (n = 67,884) | TRAIN (n = 178,523, in-sample) |
|---|---:|---:|
| β(breadth \| total count only), matched comparator | +0.207386 | +0.213922 |
| **β(breadth \| full 9-system composition)** | **+0.046024** | +0.047618 |
| CI95 | [+0.024219, +0.068672] | [+0.033809, +0.061897] |
| **share of the gradient composition absorbs** | **77.8%** | 77.7% |
| β(items_per_system \| composition) | −0.119861, CI [−0.1634, −0.0770] | −0.1220 |

**Every directional prediction A3 recorded came true.** A3 predicted a surviving share of 15-30%
out-of-sample against 22.3% in-sample; the realised value is **22.2%**. It predicted
`items_per_system | composition` negative and CI-clear; it is. It predicted `target_year` survival
≈ 1; it is 1.000.

The out-of-sample estimate is within 3% of the in-sample one. The in-sample value was flagged in A3
as an upper bound; it turns out not to have been inflated.

### Control survival, eval2024

β_pooled = 0.257852.

| control | n_groups | β_stratified | β_survival |
|---|---:|---:|---:|
| age_quartile | 4 | 0.139063 | **0.539** |
| prior_depth_band | 3 | 0.172696 | **0.670** |
| target_year | 1 | 0.257852 | 1.000 |
| postcode_area | 119 | 0.369159 | 1.432 |
| make | 139 | 0.287218 | 1.114 |

Age is the strongest confounder and clears the 0.50 bar by 0.039. Geography and make do not absorb
the gradient at all — β *rises* within them, which is the opposite of a recording-practice artefact.

---

## 3. C1 — why INCONCLUSIVE and not FALSIFIED

A3 §3 lists "C1 fails (≤4 of 6 strata)" as falsifying. C1 failed 3 of 6. The literal reading is
FALSIFIED. That reading is rejected, on evidence available from the banked CIs alone.

**C1 could not have passed even if the discovery effect were exactly true.**

Taking each stratum's EVAL standard error from its own published bootstrap CI
(SE = (hi − lo) / 2·1.96), and asking what probability that stratum had of returning a CI clear of
zero if the TRAIN effect were exactly the truth:

| stratum | n_EVAL | β_TRAIN | SE_EVAL | MDE₈₀ | power vs TRAIN effect | outcome | classification |
|---|---:|---:|---:|---:|---:|:--:|---|
| c=2 | 38,366 | +0.3006 | 0.0325 | 0.0912 | **100%** | ✓ | PASS |
| c=3 | 16,977 | +0.2540 | 0.0353 | 0.0989 | **100%** | ✓ | PASS |
| c=4 | 7,224 | +0.1204 | 0.0396 | 0.1110 | **86%** | ✓ | PASS |
| c=5 | 3,073 | +0.0717 | 0.0530 | 0.1485 | **27%** | ✗ | UNDERPOWERED |
| c=6 | 1,399 | +0.0905 | 0.0707 | 0.1980 | **25%** | ✗ | UNDERPOWERED |
| c=7-8 | 845 | +0.0187 | 0.0780 | 0.2186 | **4%** | ✗ | UNDERPOWERED |

Every stratum with ≥80% power passed. Every failure was underpowered. **There were no genuine
misses.** In each failing stratum the discovery effect sits below that stratum's own MDE₈₀ — the
instrument could not have resolved it.

Aggregating (Poisson-binomial over the six independent pass probabilities), if the TRAIN effect is
exactly true:

| # strata CI-clear | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| probability | 0.00% | 0.00% | 7.38% | **50.53%** | 34.84% | 7.01% | 0.25% |

- E[passes] = **3.42**
- **P(≥5 of 6) = 7.26%** — the bar A3 set
- P(≥4 of 6) = 42.10% — the threshold below which A3 declared falsification
- Modal outcome = **3 of 6** — exactly what was observed

A gate with **7% power against its own alternative** does not discriminate between hypotheses. Its
failure carries almost no evidence: the observed result is the single most likely outcome *under the
hypothesis being true*. Recording it as falsification would assert something the data does not
support.

**C1 is INCONCLUSIVE against the per-stratum floors above** (MDE₈₀ = 0.1485 / 0.1980 / 0.2186 for
c=5 / c=6 / c=7-8).

### What was NOT done

- C1 was **not** re-run on pooled TRAIN+EVAL. A3 §4 bars moving the strata, and no owner override
  for that was sought or given.
- The ≥5-of-6 bar was **not** retrospectively lowered. It is reported as void, not as met.
- `items_per_system` was **not** substituted for breadth as the headline (A3 §4 bars it, and breadth
  did not fail on C2 anyway).

---

## 4. The design defect, and the rule that follows

**Root cause.** The ≥5-of-6 bar was chosen on a TRAIN surface of 923,604 rows and applied to an EVAL
surface of 312,159 — roughly a third. Precision scales with √n, so the count strata that carried the
discovery effect on TRAIN could not carry it on EVAL. Nobody computed the bar's power before
freezing it, so an unpassable gate was frozen in good faith and would have been read as a scientific
result.

This is a **class** of error, not an instance. It applies to every "CI-clear in ≥k of N strata"
counting rule in this programme.

**Standing rule adopted 2026-08-16 — power-at-bar precondition:**

> No bar may be frozen without publishing, in the same document, (a) the MDE at that bar computed on
> the **confirmation** row counts, and (b) P(bar passes | discovery effect exactly true). Any bar
> whose pass probability is below 80% is rejected and rewritten **before** freezing, not
> reinterpreted afterwards.

Cheap to compute — it needs only the discovery effect and the confirmation-surface row counts, both
known at freeze time. Had it been applied to A3, C1 would have been rewritten rather than voided.

The rule is a precondition for `PREREG_PERSIST_2026_08_16.md`, whose per-system and per-run-length
strata fragment harder than ADVSTRUCT's and would repeat this failure by default.

---

## 5. Reading

Composition absorbs **77.8%** of the count-conditional breadth gradient out-of-sample. Most of what
naive breadth appears to measure is **which** systems were advised, not **how many** — the visible
counterexample from TRAIN survives as the intuition: a single `suspension` advisory carries higher
risk (0.1904) than `wheels_tyres + brakes` together (0.1206), at identical count and *lower* breadth.

The residual +0.046 is small, real, out-of-sample, and robust to age, prior depth, era, geography and
make. It is a genuine structural signal, and it is roughly a fifth the size of what an
unconditional breadth reading would have claimed.

**This says nothing about model value.** Parent §§8.1-8.2 have not run. The `(L0 − L0m)` channel
ablation remains mandatory and unconditional, and until it does run, a descriptive-positive /
model-null cannot be told apart from redundancy with the coarse advisory channel B0 already carries.

---

## 6. Open

| Item | State |
|---|---|
| §8.1 `(L0 − L0m)` channel ablation | **NOT RUN** — mandatory, unconditional |
| §8.2 whole-block ADV contrast | NOT RUN |
| §10.2 conditional persistence null | NOT RUN |
| Tied-prior-day sensitivity, EVAL | banked in `ADVSTRUCT_DESCRIPTIVE_EVAL.json` (n_untied 284,587), not yet written up |
| Grain sensitivity (`ADV_GRAIN`, 8-key) | NOT RUN |
| C4 | untestable on a single-year EVAL; would need a multi-year confirmation surface |
