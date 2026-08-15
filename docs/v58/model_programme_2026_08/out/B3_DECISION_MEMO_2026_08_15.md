# Decision memo — should B3 become AutoSafe's reference target?

**2026-08-15.** Governing question: how high can performance on high-defect-burden vehicles
be pushed on the **existing feature substrate**, and does B3 warrant promotion?

Feature substrate held fixed throughout. No new features. Only the target and the learner
were varied, so attribution is clean.

---

## 1. Integrity verdict on the four-seed B3 result — **PASS**

Audited by `scripts/analysis/sev_b3_result_packet.py`, which writes nothing if any check
fails. Packet: `out/B3_RESULT_PACKET.json`.

| check | result |
|---|---|
| config diff between arms | **exactly one key: `label`** |
| training row sets, treatment vs control | **identical (set equality) at all 4 seeds** |
| train/eval overlap | 0 |
| `covid_hole_rows` / `era_pre2018_share` | 0 / 0.0 |
| eval row set across all arms | identical |
| features / train rows / eval rows | 241 / 755,389 / 330,665 (315,300 vehicles) |
| B3 prevalence | 0.0952 eval · 0.0983 train |

Per-seed Δ: 101 +0.006849 · 202 +0.006444 · 303 +0.006748 · 404 +0.006804.
**Mean +0.006711 · sd 1.828e-04 · spread 4.048e-04 · 4/4 positive.**

> **Status: CONFIRMED-AT-K4.** The pre-registered gate specified k=5 with sign consistency.
> Seed 505 was **never run** (stopped early by owner decision). Strongly replicated, **not
> formally k=5-confirmed**. σ̂ is **LOW-K-PROVISIONAL** (`mde.FLOOR_MEASURED_MIN_K = 5`).
> Recorded as-run; **not to be altered retrospectively.**

The only intended causal difference between arms is the training objective. Confirmed.

---

## 2. Model leaderboard for B3 — matched evaluation, matched features

Each family's own `y_final`-trained banked score is included, so the **within-family** lift
isolates what targeting B3 buys *that learner* rather than mixing in baseline differences.

| family | grade | y_final→B3 | **B3-trained** | within-family Δ | vs CatBoost B3 | sec/fit |
|---|---|---:|---:|---:|---:|---:|
| CatBoost | screen | 0.790623 | 0.797472 | +0.006849 | — | 475 |
| **LightGBM** | full | 0.791011 | **0.799042** | +0.008031 | **+0.001571** | **58** |
| **RealMLP** | **screen** | 0.790231 | 0.798556 | **+0.008325** | **+0.001084** | 877 |

**CatBoost is not the best learner for this target.** The key row is RealMLP: at **the same
`screen` grade as CatBoost** it beats it by **+0.001084**, so the gap is **model class, not
capacity**. LightGBM takes the highest absolute score but at `full` grade, so its margin
over RealMLP (+0.000486) is not capacity-controlled.

Both challengers extract **more from targeting B3** than CatBoost does (+0.0083 / +0.0080 vs
+0.0068) — the advantage is in B3-specific learning, not in a better starting point.

Nothing crosses 0.800 (highest 0.7990). Per the brief, that is not treated as a threshold.

Scale for judging +0.001084: 1.6× the measured refit nuisance (6.87e-04), 5.9× the paired
σ̂ (1.83e-04), and 16% of the B3-vs-failure-target effect itself. Detectable and real, but
an order of magnitude smaller than the target change.

---

## 3. Product lift — the commercially meaningful statement

Full untouched evaluation population, 330,665 rows, B3 prevalence 0.0952.

**LightGBM B3 (best model):**

| cut | n selected | precision | recall | lift | % all initial failures | **% all M/D defects** |
|---:|---:|---:|---:|---:|---:|---:|
| 1% | 3,307 | 0.5113 | 5.4% | 5.37× | 2.5% | 4.7% |
| 5% | 16,533 | 0.3895 | 20.5% | 4.09× | 11.0% | 17.7% |
| 10% | 33,066 | 0.3317 | 34.9% | 3.49× | 20.5% | **30.0%** |
| 20% | 66,133 | 0.2668 | 56.1% | 2.80× | 36.8% | 48.8% |
| 30% | 99,200 | 0.2246 | 70.8% | 2.36× | 50.6% | 62.6% |

> **Selecting the highest-risk 10% of vehicles identifies 34.9% of B3 vehicles and 30.0% of
> all major/dangerous defect burden.** At 30%: 70.8% and 62.6%.

**⚠ The modelling gain is worth ~1 percentage point.** Today's failure-trained score already
delivers 33.8% recall and 29.0% burden capture at the top decile. The entire journey from
+0.0067 (target) plus +0.0016 (learner) buys roughly **+1.1 pp of recall and +1.0 pp of
burden capture**. The AUROC story is far more impressive than the product story, and the
product story is the one that should be quoted externally.

Gain curves banked at `out/PRODUCT_LIFT_*.json` (100-point percentile grid).

---

## 4. B3 versus M1, including overlap

| | B3 (≥3 M/D items) | M1 (≥2 DVSA sections) |
|---|---:|---:|
| prevalence | 0.0952 | 0.1201 |
| best AUROC | **0.799042** | 0.787285 |
| direct-training Δ | **+0.006711** (k=4) | +0.003992 (k=1, sub-trigger) |
| precision @10% | 0.3317 | 0.3826 |
| recall @10% | **34.9%** | 31.9% |
| lift @10% | **3.49×** | 3.19× |
| % all M/D defects @10% | **30.0%** | 29.7% |

**Overlap:** B3 ∩ M1 28,318 (8.56%) · B3-only 3,145 (0.95%) · M1-only 11,389 (3.44%) ·
neither 287,813 (87.04%). **Jaccard 0.661; 90.0% of B3 vehicles are also M1.**

They are **largely the same population**, but the residuals differ in kind:

| segment | mean M/D items | mean sections | mean dangerous |
|---|---:|---:|---:|
| B3 only | **3.44** | 1.00 | 0.61 |
| M1 only | 2.00 | 2.00 | 0.30 |
| both | 4.65 | 2.89 | 0.67 |

B3-only vehicles carry **deep damage concentrated in one system**; M1-only vehicles are the
**shallow-breadth minimum** (exactly two defects in two sections) and carry half the
dangerous-defect rate. M1's higher precision is purely its higher prevalence — on lift,
recall and burden capture B3 matches or beats it.

**B3 is the better target.** Same serious-defect burden captured, better lift, lower
prevalence (a tighter, more actionable selection), and the vehicles it uniquely finds are
more damaged than the ones M1 uniquely finds.

---

## 5. Decision — the new reference baseline

> **Target: B3. Model: LightGBM. Recommended as AutoSafe's reference baseline for
> model development.**

Reasons, in order of weight:

1. **B3 is decisively more learnable than broad failure** and the result is integrity-clean
   and replicated 4/4 with a spread 6% of the effect.
2. **B3 beats M1** on every decision-relevant axis while capturing the same defect burden.
3. **LightGBM leads on absolute score** and, with RealMLP, establishes that CatBoost is not
   the right learner here.
4. **Iteration speed is the underrated argument: 58 s/fit versus CatBoost's 475 s and
   RealMLP's 877 s.** A 5-seed confirmation costs ~5 minutes instead of ~40. For a
   programme that has just learned its k=2 reads are unreliable, that changes what is
   routinely affordable — and it is the single biggest practical consequence of this work.

### Caveats attached to that recommendation

- **LightGBM's margin over RealMLP is not capacity-controlled** (+0.000486, full vs screen).
  **RealMLP at full grade is untested and could lead.** If the model choice ever becomes
  load-bearing, run it.
- **LightGBM has no established seed-variance floor** (`mde.BANKED_SIGMA["lightgbm"] = None`,
  NO-FLOOR by design). Adopting it as the reference means deriving one.
- **Promotion to the *product's* primary prediction target is NOT recommended on this
  evidence.** The product case is ~1 pp of concentration, and switching changes what the
  score means to users (from "will it fail" to "will it fail badly"). That is a product
  decision requiring its own framing, not a modelling consequence.
- **Calibration is the larger practical win and it is nearly free**: out-of-fold Platt
  recalibration of the *existing frozen score* recovers **96.95%** of the Brier gap with no
  retraining at all (`out/SEV_RECALIB_PROBE.json`). If probability quality is what the
  product needs, that is available today.

---

## 6. Next experiment — the single highest-value hypothesis

> **Prior major/dangerous defect BURDEN and its trajectory.**

**Hypothesis.** A vehicle's prior *defect burden* — how many major/dangerous items it
carried at each previous initial test, and whether that count is rising — predicts future
burden materially better than the prior *pass/fail events* the current substrate encodes.

**Why this one, ahead of everything else:**

- The B1–B6 substrate was designed for **broad failure**. It encodes prior failure counts,
  rates and recency. The NY audit found the burden channel is **effectively absent** — one
  column, `b4_burden_mean_last3`, and it measures the wrong quantity.
- Stage 1 established that the **target-side burden axis is where the signal lives**. The
  history side has **no matching representation**. That is a representation gap sitting
  directly opposite a proven-learnable quantity.
- It is the **history-side mirror of the exact target that just worked** — the cleanest
  possible causal test.
- It is **cheap and already tooled**: computable from the same item lake with
  `severity_collect.py`, which is built, validated and now agrees bit-for-bit with the
  repaired `factory/severity.py`.
- It is **falsifiable and attribution-clean**: if prior-burden columns do not move B3, then
  the +0.008 is objective-shaping rather than a missing representation — itself a
  publishable answer.

**Natural companion inside the same block:** *per-section persistence* — does this vehicle
keep failing the **same** DVSA section? B3-only vehicles are precisely the deep-in-one-system
population (3.44 items, 1.00 sections), so section-level recurrence targets the segment B3
uniquely selects.

**Do not start it until the baseline above is frozen**, so the feature effect is not
confounded with the target and learner changes measured here.

---

# ADDENDUM — the next experiment ran, and it is a NULL

`prereg/PREREG_B3_BURDEN_2026_08_15.md` (frozen before any feature was built) ·
`out/B3_BURDEN_RESULT.json`

Prior major/dangerous **burden** block: 12 columns appended to the frozen 241 —
count/mean/max/sum of M/D items per prior initial test, last-3 mean, trend, section
counts, section persistence, same-section repeat. Strictly as-of, LightGBM, paired at 5
matched seeds on identical frames.

| seed | base (241) | burden (253) | Δ |
|---:|---:|---:|---:|
| 101 | 0.799014 | 0.799052 | +0.000038 |
| 202 | 0.799081 | 0.798963 | **−0.000118** |
| 303 | 0.799025 | 0.799346 | +0.000321 |
| 404 | 0.798916 | 0.799386 | +0.000469 |
| 505 | 0.799048 | 0.799176 | +0.000128 |

**mean +0.000168 · sd 2.315e-04 · one seed negative → pre-registered verdict NULL**

4× below the +0.0007 null band and inside LightGBM's own measured refit nuisance
(1.74e-04, established by refitting the baseline on the modified frames).

## What the null establishes

> **The +0.0067 from direct B3 training is OBJECTIVE-SHAPING, not a missing
> representation.**

The substrate already encodes whatever prior-burden information is extractable — presumably
through its existing prior-failure counts, rates and advisory columns. Building a history
channel that *mirrors the target* does not help, even though the target-side burden axis is
demonstrably where the signal lives. Targeting B3 works because it changes **what the model
optimises**, not because it unlocks history the model could not previously see.

This is worth more than an adopted block would have been: it closes off the whole class of
"mirror the proven-learnable target on the history side" hypotheses, which is the default
direction the programme would otherwise have taken next.

**Scope:** LightGBM, B3, flat4y r1m, eval2024. Not tested on other learners or rungs.
Leakage gates were clean (0 strict-date, 0 self-reference over 4.53M train and 1.96M eval
target-prior pairs), so this is a null about *information*, not a broken instrument.

## Revised next step

Do **not** pursue further history-representation features against B3. The remaining live
directions, in order:

1. **Capacity** — `PREREG_SEVERITY_STAGE3` (grade rung). All CatBoost arms were
   capacity-censored at 591–598 of 600 iterations, and that question is untouched by this
   null. Needs seed 505 of the confirmation run, or a prereg amendment, before it is
   admissible.
2. **Calibration into the product** — already measured as ~97% recoverable with no
   retraining at all. The highest ratio of value to compute anywhere in this programme.
3. **Genuinely new information** — external signals or channels the lake does not contain.
   Not more re-encodings of what it does.

---

# CORRECTION — the "free calibration win" is NOT a live product item

`out/RECALIB_T0_PROBE.json`

An earlier reading of this work called calibration "the most actionable item, available
today". **That was wrong, and the error was mine.** The miscalibration measured (slope
1.4145, intercept −0.7913) was the frozen score scored against **B3** — a target it was
never trained for, at 9.5% prevalence instead of 22.9%. Over-prediction there is arithmetic,
not a defect.

Measured on **its own** target, the same score is already well calibrated:

| arm | Brier | cal intercept | cal slope |
|---|---:|---:|---:|
| frozen raw, on T0 | 0.157797 | −0.0060 | **0.9863** |
| frozen + Platt OOF, on T0 | 0.157795 | −0.0001 | 0.9999 |

Recalibration buys a Brier improvement of **0.000002** (0.001% relative).

Decile reliability confirms it is not a global average masking local damage:

| | T0 (own target) | B3 |
|---|---|---|
| decile ratio range (observed/predicted) | **0.990 – 1.041** | 0.127 – 0.626 |
| max deviation | **4.1%** | 87.3% |
| top decile (where the product acts) | 0.5143 pred / 0.5104 obs → **0.992** | 0.5143 / 0.3220 → 0.626 |

**Conclusion: there is no calibration work to do on the current product.** The ~97%-free
recalibration finding remains true and useful, but it is strictly *conditional on adopting
B3* — a change already recommended against on ~1pp of commercial value. It is not an
independent win and should not be cited as one.

## Consequence: this lane is closed

With the burden null and this correction, the existing substrate has been exhausted on this
target:

| lever | result |
|---|---|
| change the target (T0 → B3) | **+0.0067**, replicated 4/4 — the real effect |
| change the learner (CatBoost → LightGBM) | +0.0016, ~4× smaller |
| add mirrored history features | **NULL** (+1.7e-04, one seed negative) |
| recalibrate | **no defect to fix** on the product's own target |
| product concentration gained, end to end | **~1 percentage point** |

Every remaining direction requires something this programme cannot supply from the current
substrate: a **product decision** (adopt B3 and accept what it changes for users),
**compute that is currently blocked** (Stage 3 capacity needs seed 505 or a prereg
amendment), or **genuinely new information** — an external channel, not another re-encoding
of the lake.

Recommendation: **stop optimising here.** The honest summary is that high defect burden is
substantially more learnable than generic MOT failure, that this is an objective effect
rather than a representation gap, and that it is worth about one percentage point of
real-world concentration.
