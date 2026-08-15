# Stage 2 — training directly on B3. Verdict: **ESCALATE**

**2026-08-15.** Prereg `prereg/PREREG_SEVERITY_STAGE2_2026_08_15.md`, frozen with zero
Stage-2 fit artifacts on disk. One CatBoost fit, seed 101, r1m, `grade: screen`.
**Only the target changed.**

---

## Headline

| | AUROC on B3 | Δ vs frozen | 95% CI | verdict |
|---|---:|---:|---|---|
| Frozen `y_final`-trained (banked) | 0.791310 | — | — | benchmark |
| **B3-trained** | **0.797472** | **+0.006162** | [+0.005401, +0.006969] | **ESCALATE** |
| B3-trained vs matched control | — | +0.006849 | [+0.006092, +0.007644] | ESCALATE |
| *Null control: refit only, no target change* | 0.790623 | −0.000687 | [−0.000989, −0.000376] | *nuisance* |

Secondary target M1 (benchmark 0.783293):

| | AUROC on M1 | Δ vs frozen | 95% CI | verdict |
|---|---:|---:|---|---|
| **M1-trained** | **0.787285** | **+0.003992** | [+0.003355, +0.004601] | **DETECTABLE-SUB-TRIGGER** |
| M1-trained vs matched control | — | +0.004228 | [+0.003572, +0.004841] | sub-trigger |

M1 clears the MDE(k=1) ≈ 0.00242 detectability bound but **not** the +0.005 trigger. Per the
pre-registered three-band rule that is **reported, not escalated** — a sub-trigger result is
not a null, and materiality ≠ detectability.

Pre-registered trigger was **Δ ≥ +0.005** (bound before the fit). Δ = +0.006162 clears it,
sits **~7.2× the on-surface σ̂ (8.61e-4)** and **~9× the directly measured refit nuisance**,
and is far below the +0.02 HALT band. **The substrate does hold additional B3-specific
ranking information that the ordinary-failure model was leaving on the table.**

---

## The largest gain is calibration, not ranking

| metric | frozen | B3-trained | change |
|---|---:|---:|---|
| AUROC | 0.791310 | 0.797472 | +0.006162 |
| AUPRC | 0.288967 | 0.296930 | +0.007963 |
| AUPRC ÷ prevalence | 3.037 | 3.121 | +0.084 |
| **Brier** | 0.095782 | **0.075774** | **−20.9%** |
| **calibration slope** | 1.414519 | **1.020722** | → ~1.00 |
| **calibration intercept** | −0.791305 | **+0.058930** | → ~0.00 |
| top-10% capture | 0.338429 | 0.347710 | +0.93 pp |
| top-10% lift | 3.384 | 3.477 | +0.093 |

The frozen score ranks B3 well but is **badly miscalibrated** for it — unsurprising, since it
was fitted to a 0.229-prevalence target and applied to a 0.095-prevalence one. Direct
training fixes that almost exactly (slope 1.41 → 1.02, intercept −0.79 → +0.06, Brier −21%).
For any product use involving thresholds or stated probabilities, **that is worth far more
than the +0.006 AUROC.**

---

## Transfer — the causal hypothesis is half confirmed and half refuted

The B3-trained score, applied without retraining to every Stage-1 outcome. Right-hand
column subtracts the measured null-control nuisance, so it isolates the target effect:

| outcome | frozen | B3-trained | Δ | Δ (nuisance-adjusted) |
|---|---:|---:|---:|---:|
| **B3** ≥3 M/D | 0.791310 | 0.797472 | +0.006162 | **+0.006849** |
| **M1** ≥2 sections | 0.783293 | 0.786343 | +0.003051 | **+0.003286** |
| T0 any failure | 0.713304 | 0.706044 | −0.007260 | **−0.007284** |
| S1 ≥1 dangerous | 0.654708 | 0.645334 | −0.009375 | **−0.009449** |

Predicted pattern was `B3 ↑↑ · M1 ↑↑ · T0 ~ · S1 ~`. Measured:

- ✅ **B3 up substantially** (+0.0068).
- ✅ **M1 up substantially** (+0.0033, ≈ half the B3 gain) — genuine positive transfer
  between two burden/breadth measurements that were never jointly optimised. This is
  consistent with B3 and M1 being downstream measurements of a shared state.
- ❌ **T0 does not hold — it falls** (−0.0073).
- ❌ **S1 does not hold — it falls furthest** (−0.0094).

So this is **specialisation, not a universally better deterioration score.** Optimising for
high burden reallocates capacity toward the high-burden end and costs ordinary failure and
dangerous-defect ranking. The shared-latent-state reading survives *for the burden/breadth
pair*; it is not supported as a single latent quantity underlying all four outcomes.

That S1 degrades **most** is the sharpest version of Stage 1's finding: whatever drives
dangerous defects is not what drives accumulated burden, and moving further toward burden
moves further away from it.

### The M1 fit turns this from suggestive into strong

Train on M1 instead, and the resulting model is **better at B3 than at its own target**:

| M1-trained score, applied to | AUROC | Δ (nuisance-adjusted) |
|---|---:|---:|
| **B3** | **0.797181** | **+0.006558** |
| M1 (its own target) | 0.787285 | +0.004228 |
| T0 | 0.707908 | −0.005420 |
| S1 | 0.647658 | −0.007125 |

**0.797181 vs the B3-trained model's own 0.797472 — a difference of 0.00029.** Training on
a *breadth* target recovers essentially all of the B3 gain available from training on the
*count* target directly. The two are near-interchangeable as training signals.

Prediction-level correlation confirms it:

| pair | Pearson | **Spearman** |
|---|---:|---:|
| **B3-trained vs M1-trained** | 0.98264 | **0.99201** |
| B3-trained vs T0-trained | 0.93266 | 0.96374 |
| M1-trained vs T0-trained | 0.95489 | 0.97244 |

Two targets defined differently — a **count** of major/dangerous items versus a **breadth**
across DVSA sections — optimised independently, converge on nearly the same ranking function
(ρ = 0.992), and both sit measurably apart from the ordinary-failure model. That is the
signature the causal hypothesis predicts: B3 and M1 behave as two noisy measurements of one
underlying quantity, which is **not** the quantity that drives T0 or S1.

**Still interpretation, not proof of a latent variable** — convergence is consistent with a
shared state but does not establish one, and both targets are computed from the same item
rows, so they are not independent measurements in the strict sense.

---

## Gates

| gate | result |
|---|---|
| **G1 null-change control** | AUROC 0.7133278601 vs banked 0.7133041238 — **abs diff 2.37e-05**, inside the pre-registered <1e-4 band. `quantization` string, `n_features` 241 and `rung_rows` 755,389 all match the benchmark exactly. |
| Frozen benchmark reproduction | **abs diff 0.0** (bit-exact) in every analysis run |
| Row/label identity fences | rows match · id sets identical · vehicle_id agrees |
| G2 label integrity | eval B3 31,463 / M1 39,707 reproduce Stage 1 exactly; zero passing target tests carry a counted M/D item; train↔results mismatch 2/755,389; zero training targets before 2018-05-20 (min `tgt_date` 2020-01-02) |
| Train positive rate | 0.098306 — the intended B3 target, not `y_final` |
| Factory suite after the 2-line contract patch | **199 passed, 2 skipped, 0 failed** |
| Analysis self-check | same preds both arms → Δ = 0.0 exactly, CI [0, 0] |

### G1 earned its place twice

**First run failed outright.** `resolve_featureset(B1..B6)` on today's `blocks.py` returns
**247** columns, not the 241 the benchmark used — the B2 item-observability index was added
after the canonical fit ran on 2026-08-12, and the v1 training frame does not carry those 6
columns. **The canonical `s2.D.cum.b0-6` fit is no longer reproducible from its own config.**
This independently confirms NY audit finding S-1 and uses its prescribed recovery
(intersect the resolved set with the frame schema), which returns exactly 241. Without a
control fit, the B3 arm would have silently trained on a 247-column substrate and been
compared against a 241-column benchmark.

**Second, it turned into a planted null control.** The control model is *materially*
different from the banked one — correlation 0.9937, max |Δp| 0.265, mean |Δp| 0.0096,
**zero identical predictions of 330,665** — yet scores within 2.4e-05 on T0. A nominally
identical refit produces a different model with equivalent ranking.

Measured refit nuisance by label: T0 +2.37e-05 · **B3 −6.87e-04** · M1 −2.36e-04 ·
S1 +7.47e-05. **The B3 nuisance CI [−9.89e-04, −3.76e-04] excludes zero.**

> A change of *nothing at all* produces a "statistically significant" B3 effect.

That is `STAGE2_INFERENCE_RULE_FINAL_2026_08_15.md:227` demonstrated first-hand rather than
cited, and it is why the pre-registered gate keys on effect size, not CI exclusion. It also
validates the imported σ̂: measured nuisance is 0.8× the on-surface 8.61e-4 used for
MDE(k=1) ≈ 2.42e-3.

---

## The seven questions

1. **Does direct B3 training beat the 0.791 benchmark?** **Yes.** 0.797472.
2. **By how much, with uncertainty?** **+0.006162** [+0.005401, +0.006969] against the
   banked benchmark; **+0.006849** [+0.006092, +0.007644] against the matched control,
   which is the better-controlled figure because the target is then the only difference.
   ~7.2σ̂ and ~9× the measured refit nuisance.
3. **Does M1 move similarly?** **Partly.** The dedicated M1 fit gains **+0.003992**
   [+0.003355, +0.004601] — detectable, but **below the +0.005 trigger**, so M1 is reported
   and not escalated. Its calibration gain mirrors B3's: Brier 0.104795 → 0.092044 (−12.2%),
   slope 1.373 → 1.014, intercept −0.525 → +0.031.
4. **Does a B3-trained score transfer to M1?** **Yes — and the reverse is stronger.**
   B3-training lifts M1 by +0.0033 without ever targeting it; M1-training lifts B3 by
   +0.0066, landing within **0.00029** of training on B3 directly. The two trained scores
   correlate at **Spearman 0.992**, versus 0.964/0.972 against the T0-trained control. This
   is the strongest evidence in either stage for a shared burden/breadth state.
5. **Does dangerous prediction remain weak?** **It gets worse** — 0.6547 → 0.6453, the
   largest movement of any outcome, and negative.
6. **Does this justify multi-seed confirmation?** **Yes — the gate fired.** See below.
7. **Residual numerical gap to NY CatBoost's 0.832?** T0 0.7132 → gap 0.1188. Frozen on B3
   → 0.0407. B3-trained → **0.0345**. Direct optimisation recovered a further 0.0062.
   **A numerical gap only — matched prevalence does not make the two AUCs causally
   comparable**, and NY's ~0.108 base rate is itself a derived figure.

---

## What happens next, and the honest caveat

The gate fired, so **multi-seed confirmation is justified and is the next step**. It has
**not** been launched: the brief says do not escalate automatically, and that stands.

The k=1 hazard is real and this house has been burned by it: B7 read +7.754e-04 at k=2 and
+2.765e-04 at k=5 with one seed negative, because the first two seeds run were the two most
favourable of five. Two things make this case materially stronger than that one:

- B7's k=2 point estimate was ≈0.9σ̂. This is **≈7.2σ̂**.
- A refit nuisance control was *measured here* (−6.87e-04) and the effect is ~9× it.

But it remains **one seed**, and the pre-registered screen has ≥80% power only for true
Δ ≳ +0.0057 — so this reading is near the power boundary and a multi-seed run could move it.
Recommended confirmation: seeds {202, 303, 404, 505} on both `sev.B3` and `sev.CTRL`, so the
paired delta is measured against a matched control at every seed rather than against a
single banked vector.

Both arms stopped near the iteration cap (benchmark 598/600, control 591, B3 595), so this
is a comparison at a **fixed budget**, not at either arm's optimum.

## Recorded deviations

- **D-S2-1** — config diff is not "exactly one key": the 241-column set had to be pinned
  explicitly because `blocks.py` now resolves to 247. The three Stage-2 configs still differ
  from *each other* only in `label`.
- **Fail-gated `n_dangerous`** carried over from Stage 1; `factory/severity.py` unmodified.
- **2-line shared-code patch** to `fit_contract.py` (`LABELS` widened; label projection made
  additive so `KEY_COLUMNS` gains no mandatory column). 10 dedicated tests; factory suite green.
- G1 is not bit-exact (2.37e-05); cause identified as genuine refit nondeterminism, quantified.

---

# CONFIRMATION RUN — CONFIRMED AT k=4 (stopped early, owner decision)

Paired design: every seed's B3 arm against **its own matched control at the same seed**, so
the k=1 refit nuisance (B3 label: −6.87e-04, CI excluding zero) is differenced out rather
than assumed away.

| seed | B3-trained | control | Δ |
|---:|---:|---:|---:|
| 101 | 0.797472 | 0.790623 | +0.006849 |
| 202 | 0.797370 | 0.790926 | +0.006444 |
| 303 | 0.797674 | 0.790925 | +0.006748 |
| 404 | 0.797210 | 0.790407 | +0.006804 |

**mean paired Δ = +0.006711 · 4/4 seeds positive · spread 0.000405**
on-surface σ̂ (paired, this contrast) = **1.828e-04** · MDE(k=4) = 1.084e-03
seed-mean bootstrap CI [+0.006003, +0.007476] — reported, **not a gate**

The effect is ~37× the measured σ̂ for this contrast, and σ̂ tightened monotonically as seeds
arrived (2.86e-04 → 2.11e-04 → 1.83e-04). Contrast the B7 collapse this design was built to
guard against: there one seed of five read −1.202e-03 against a mean of +2.765e-04, i.e. a
seed-to-seed spread several times its own effect. Here the spread is **6% of the effect**.

## Qualification — read this before quoting the result

The pre-registered gate specified **k=5** with sign consistency. The run was **stopped after
four pairs on owner decision**; seed 505 was never fitted. Therefore:

- The **verdict conditions are met** (mean ≥ +0.005, all seeds positive) — but at k=4, not
  the k=5 that was registered. Recorded as **CONFIRMED-AT-K4**, not "confirmed".
- **σ̂ = 1.828e-04 is LOW-K-PROVISIONAL**, not MEASURED: `mde.py:77` sets
  `FLOOR_MEASURED_MIN_K = 5`, below which an on-surface floor carries that status
  (`mde.py:176`). It is **not admissible** as the measured floor Stage 3's ±0.002 decision
  band was justified against.
- **Consequence for Stage 3**: `PREREG_SEVERITY_STAGE3_2026_08_15.md` §6 keys its band to a
  measured on-surface σ̂. Executing Stage 3 requires either fitting seed 505 to reach k=5, or
  amending that prereg to justify its band from the LOW-K-PROVISIONAL value. **Stage 3 is
  not unblocked as written.**

Nothing about the +0.006711 delta is in doubt at this precision — a reversal would have
required the remaining seed to land ~30σ from the observed four. The qualification is about
what the run is *licensed to be cited as*, not about whether the effect is real.
