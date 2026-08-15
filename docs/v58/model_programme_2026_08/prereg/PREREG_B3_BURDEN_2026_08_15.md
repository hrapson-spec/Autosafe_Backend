# PREREG — B3 prior-burden feature block

**Written 2026-08-15 BEFORE the features were built or any burden number existed.**
Baseline frozen first (`out/B3_REFERENCE_BASELINE.json`); this is the first experiment to
change the **feature substrate**, and it changes only that.

## §1 Hypothesis

> A vehicle's prior **defect burden** — how many major/dangerous items it carried at each
> previous initial test, and whether that count is rising — predicts B3 materially better
> than the prior **pass/fail events** the current substrate encodes.

Motivation: the B1–B6 substrate was designed for broad failure. The NY audit found the
burden channel **effectively absent** (one column, `b4_burden_mean_last3`, measuring the
wrong quantity). Stage 1/2 established the *target-side* burden axis is where the signal
lives; the *history side* has no matching representation. This is the history-side mirror of
a proven-learnable quantity.

**Falsifiable either way.** If prior-burden columns do not move B3, the +0.008 from direct
training is objective-shaping rather than a missing representation — itself a real answer.

## §2 What changes, and what does not

**Changes:** the feature matrix gains an appended BURDEN block. Nothing else.

Held fixed: target `y_b3` · LightGBM `s3.lgbm.adopted.1m` config (`grade: full`) · flat4y
r1m train frame · eval2024 eval frame · `row_filter` (COVID hole) · `valid_fraction` ·
seeds · evaluation code. The 241 baseline columns keep their identity and **order**; new
columns are **appended last**, so the baseline column list is a strict prefix.

## §3 Feature definitions — strictly as-of

For target `(vehicle_id v, tgt_date d)`, over that vehicle's **prior INITIAL tests only**:

```
prior set := tests where vehicle_id = v
                    AND test_type = 'NT'
                    AND test_date <  d        -- STRICT: same-day priors EXCLUDED
md(t)     := count(items of t where rfr_type_code IN ('F','P'))
sec(t)    := distinct normalised DVSA section names with an F/P item at t
```

| column | definition |
|---|---|
| `bd_n_prior_initials` | count of prior initial tests |
| `bd_md_last` | `md(most recent prior)` |
| `bd_md_mean` | mean `md` over all priors |
| `bd_md_max` | max `md` |
| `bd_md_sum` | sum `md` |
| `bd_md_last3_mean` | mean `md` over the 3 most recent priors |
| `bd_md_trend` | `bd_md_last3_mean − mean(md over earlier priors)` |
| `bd_sec_last` | `|sec(most recent prior)|` |
| `bd_sec_mean` | mean `|sec(t)|` |
| `bd_sec_max` | max `|sec(t)|` |
| `bd_sec_persistence` | max over sections of (# priors in which that section had an F/P item) ÷ `bd_n_prior_initials` |
| `bd_sec_repeat_last2` | 1 if the two most recent priors share ≥1 failing section, else 0 |

**Era safety.** `is_fail_bearing` is era-agnostic — `{F,P}` is defined both pre- and
post-2018. Only the dangerous/major *split* is post-2018-only, and no column here uses it.
So the block is well-defined for priors reaching back before 2018-05-20 and needs no era
gate. Section names are normalised (lowercase, whitespace-collapsed) so the legacy 5xxx and
modern 2xxxx trees resolve consistently.

**NULL, not zero.** A vehicle with no prior initial tests gets **NULL** on every column
except `bd_n_prior_initials` (which is 0). A zero would assert "no prior burden", which is
not what an absent history means. LightGBM handles NaN natively.

## §4 Leakage controls — the ones that have burned this house before

1. **Strict `test_date < tgt_date`.** Same-day priors excluded. Precedent: the tyre
   programme's owner arm read +0.0121 and was truly +0.00063 — **95% leak** — because a
   prior episode was selected as-of but its *full retest summary* was used.
2. **Priors are INITIAL tests only** (`test_type='NT'`). No retest of a prior episode
   contributes, so no post-target information can enter through a retest.
3. **Only the prior test's own items.** No target-side item touches any column.
4. **The target's own test_id is never in its own prior set** (strict `<` on date, and
   `test_id != tgt_id` asserted).
5. **Gate**: for a random 10,000-row sample, assert every contributing prior `test_date` is
   strictly less than `tgt_date` and every contributing `test_id != tgt_id`. Any violation
   → STOP.
6. **Planted-null control**: a column of pure noise appended alongside must NOT move AUROC
   beyond the measured refit nuisance. If it does, the harness is not resolving at this
   scale and the read is void.

## §5 Design and decision rule — bound before the fits

Paired, matched seeds {101, 202, 303, 404, 505}:

| arm | features |
|---|---|
| `bd.base` | the frozen 241 (identical to `sev.B3.lgbm`, already banked) |
| `bd.burden` | 241 + BURDEN block, appended last |

Primary estimand: **mean paired Δ AUROC on B3 across 5 seeds**, `bd.burden − bd.base` at
matched seed. LightGBM costs ~58 s/fit, so k=5 is affordable at ~5 minutes — the whole
reason the reference model was chosen.

Reference quantities, measured on this exact surface:

- LightGBM B3 five-seed baseline spread **2.6e-04** (0.798961 … 0.799218)
- measured refit nuisance on the B3 label: **6.87e-04** (a change of *nothing* read this)
- paired σ̂ for the B3-vs-control contrast: **1.83e-04**

| mean paired Δ | verdict |
|---|---|
| < +0.0007 | **NULL** — inside the measured refit nuisance; burden adds nothing |
| +0.0007 to +0.002 | detectable but small; report, do not adopt |
| ≥ +0.002 with **all 5 seeds positive** | **ADOPT** into the reference substrate |
| ≥ +0.010 | **HALT — leakage audit before any interpretation** |

**Sign consistency across all five seeds is required for adoption**, not just the mean: the
B7 precedent failed on sign, not on the mean. **"CI excludes zero" is NOT a gate condition**
— a pure refit produced a CI excluding zero on this very label.

The +0.010 halt band is deliberately tight for a feature experiment. The house precedent for
a too-good feature result is an as-of leak, and this block is exactly the shape that
produced it.

## §6 Out of scope

No hyperparameter search. No architecture change. No target change. No other feature
family (repair-state, advisory persistence, mileage). Adoption, if it fires, is into the
research reference substrate only — **not** a serving or product decision.
