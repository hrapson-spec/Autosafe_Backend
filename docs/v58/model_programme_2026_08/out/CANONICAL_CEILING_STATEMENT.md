# Canonical statement — what AutoSafe's ceiling means

**Ratified 2026-08-15.** Use this wording when asked how good the model is, whether it can
be improved, or whether it works as a product. Supersedes ad-hoc phrasings.

---

## The statement

> **The model is information-bound, not broken.**
>
> Half the top 1% of vehicles genuinely have ≥3 major/dangerous defects (precision 0.511),
> and the top decile holds **30% of all serious defect burden at 3.5× lift**. That is a
> real, usable concentration for triage, ranking and prioritisation.
>
> What is ruled out is a different claim: that more modelling reaches 0.85 AUROC, or picks
> individual vehicles reliably. On MOT history alone, it will not.
>
> So the product question is not "is 0.80 good enough in the abstract" — it is whether
> **3.5× lift beats what the user has today**, which is usually nothing. For ranking and
> triage it does. For anything phrased as an exact per-vehicle prediction it does not, and
> the existing public-claim boundary already says so.

## Why "information-bound" is the right word

Measured 2026-08-15, holding the feature substrate fixed:

| lever | effect |
|---|---|
| change the target (broad failure → ≥3 major/dangerous) | **+0.0067**, replicated 4/4 |
| change the learner (CatBoost → LightGBM) | +0.0016 |
| add history features mirroring the target | **NULL** (+1.7e-04, one seed negative) |
| recalibrate | **no defect to fix** (own-target slope 0.9863, max decile deviation 4.1%) |
| **product concentration gained, end to end** | **~1 percentage point** |

The binding constraint is neither the objective nor the representation. It is the
information in MOT records. The tyre programme reached the same conclusion independently:
the bottleneck is **continuous component state** (tread depth in mm), which MOT records
never contain — and tyres remain the least predictable section (0.603 vs 0.794 for
body/chassis/structure).

## The conversion rate to quote

**+0.0083 of AUROC bought ~1 percentage point of product concentration.** Use this before
authorising further model work: it is the number that makes most AUROC-chasing
uneconomic on this substrate.

## What this does NOT license

- It does not say the model is inadequate. 3.5× lift is the product.
- It does not close the capacity question (Stage 3 is untested) — it says capacity cannot
  pay for itself at the conversion rate above.
- It does not judge commercial viability. That depends on the offer, not the model, and is
  a separate question this programme has not measured.
