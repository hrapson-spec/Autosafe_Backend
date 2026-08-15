# Are severe MOT failures more predictable than broad failure? — Stage 1 result

**2026-08-15.** Prereg `prereg/PREREG_SEVERITY_2026_08_15.md` (frozen with zero result
files on disk; sha sidecar). Analysis code committed before the labels existed.
No model was trained. The score vector is fixed; only the label changes.

Substrate: `s2.D.cum.b0-6` seeds {101,202}, 241 features, banked keyed preds,
n=330,665 targets / 315,300 vehicles, `tgt_date` 2024-01-01→2024-12-31 (100%
post-2018-05-20), `test_type='NT'` only. Items from `autosafe_lake/items/test_year=2024`.

---

## Headline

**Burden predicts. Severity does not.** Discrimination rises steeply with the *number*
of major/dangerous defects, but the outcome defined by DVSA's *highest severity class*
is markedly **worse** than broad failure.

| Outcome | Positive N | Prevalence | AUROC | 95% CI | Δ vs T0 | AUPRC | PR lift | % initial failures | **A_clean** | A_mild |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| T0 AutoSafe (`y_final`) | 75,647 | 0.2288 | 0.7132 | [0.7113, 0.7155] | — | 0.4242 | 1.85 | 80.6 | 0.7238 | 0.5756 |
| T0 DVSA initial (`y_initial`) | 93,882 | 0.2839 | 0.7118 | [0.7099, 0.7139] | −0.0014 | 0.4898 | 1.72 | 100.0 | 0.7118 | — |
| B1 ≥1 M/D | 93,796 | 0.2837 | 0.7117 | [0.7098, 0.7138] | −0.0015 | 0.4894 | 1.73 | 99.9 | 0.7117 | — |
| B2 ≥2 M/D | 55,039 | 0.1664 | 0.7498 | [0.7477, 0.7520] | +0.0366 | 0.3768 | 2.26 | 58.6 | 0.7656 | 0.6529 |
| B3 ≥3 M/D | 31,463 | 0.0952 | **0.7910** | [0.7889, 0.7937] | +0.0778 | 0.2891 | 3.04 | 33.5 | **0.8171** | 0.6922 |
| **T1 NY-like** | 62,013 | 0.1875 | **0.7280** | [0.7259, 0.7303] | **+0.0148** | 0.3855 | 2.05 | 66.1 | 0.7435 | 0.6132 |
| S1 ≥1 dangerous | 26,113 | 0.0790 | **0.6545** | [0.6512, 0.6581] | **−0.0587** | 0.1367 | 1.73 | 27.8 | 0.7019 | 0.4889 |
| M1 ≥2 sections | 39,707 | 0.1201 | 0.7831 | [0.7812, 0.7856] | +0.0699 | 0.3305 | 2.75 | 42.3 | 0.8045 | 0.6898 |
| *ONLY_MINOR* (control) | 20,797 | 0.0629 | 0.6347 | [0.6309, 0.6379] | −0.0785 | 0.0889 | 1.41 | 0.0 | 0.7082 | 0.4654 |
| *ADVISORY_ONLY* (control) | 103,954 | 0.3144 | 0.5436 | [0.5413, 0.5454] | −0.1696 | 0.3273 | 1.04 | 0.0 | 0.6666 | 0.3693 |

AUROC/A_clean/AUPRC/prevalence are k=2 seed means; CIs are seed-101 vehicle-clustered
percentile bootstrap (1000 reps, seed 20260812, one shared resample across all outcomes).
`A_mild` is undefined for B1/T0-DVSA-initial because their mild-negative set is empty by
construction. Controls are exploratory, not failure outcomes.

**The gradient is not a negative-class artifact.** With the negative pool **held fixed**
at clean passes, the ladder is *steeper* than pooled: 0.7117 → 0.7656 → 0.8171
(+0.0539, +0.0515). Pooled AUROC understates the burden effect because demoted mild
failures contaminate the negative class.

---

## The two axes separate cleanly

Your §9 insistence on keeping burden and severity apart is what made this legible:

- **Burden** (`n_major_or_dangerous` 1→2→3): 0.7117 → 0.7498 → **0.7910**, strictly
  monotone, no saturation at ≥3.
- **Severity** (`n_dangerous ≥ 1`): **0.6545**, i.e. **0.059 WORSE** than broad failure,
  and still below B1 even on held-fixed negatives (0.7019 vs 0.7117).

Dangerous defects are the least predictable severe outcome measured. A plausible
mechanism — untested here — is that dangerous defects are acute events (a component that
has suddenly failed) whereas history encodes cumulative deterioration. This is a
falsifiable follow-up, not a finding.

`T1_NY_LIKE` sits between the two because it mixes them: it is dominated by its
`n_major ≥ 2` leg (burden) and diluted by its `n_dangerous ≥ 1` leg (severity).

---

## Component decomposition — DVSA section grain (primary)

Positive iff the target initial MOT contains ≥1 major/dangerous item in that section.
Thin sections preserved, not merged.

| DVSA section | Positive N | Prevalence | AUROC | 95% CI | AUPRC | PR lift | A_clean |
|---|---:|---:|---:|---|---:|---:|---:|
| Speedometer and speed limiter | 13 | 0.00004 | 0.8680 | [0.8229, 0.9241] | 0.0004 | 10.93 | 0.9123 |
| Body, chassis, structure | 11,273 | 0.0341 | 0.7944 | [0.7904, 0.7982] | 0.1201 | 3.52 | 0.8364 |
| Steering | 6,388 | 0.0193 | 0.7928 | [0.7879, 0.7975] | 0.0721 | 3.73 | 0.8385 |
| Suspension | 28,845 | 0.0872 | 0.7688 | [0.7662, 0.7715] | 0.2254 | 2.58 | 0.8024 |
| Seat belts and SRS | 3,283 | 0.0099 | 0.7437 | [0.7351, 0.7516] | 0.0324 | 3.27 | 0.7931 |
| Noise, emissions and leaks | 10,527 | 0.0318 | 0.7435 | [0.7392, 0.7479] | 0.0860 | 2.70 | 0.7909 |
| Seat belt installation check | 20 | 0.00006 | 0.7376 | [0.6298, 0.8396] | 0.0002 | 3.13 | 0.7927 |
| Lamps, reflectors and electrical | 36,503 | 0.1104 | 0.7363 | [0.7339, 0.7391] | 0.2467 | 2.23 | 0.7682 |
| Brakes | 22,522 | 0.0681 | 0.7232 | [0.7199, 0.7266] | 0.1609 | 2.36 | 0.7650 |
| Buses and coaches supplementary | 8 | 0.00002 | 0.6754 | [0.5527, 0.8133] | 0.0000 | 2.02 | 0.7462 |
| Road Wheels | 1,281 | 0.0039 | 0.6610 | [0.6465, 0.6753] | 0.0068 | 1.76 | 0.7212 |
| Identification of the vehicle | 1,141 | 0.0035 | 0.6499 | [0.6333, 0.6655] | 0.0069 | 2.01 | 0.7052 |
| Visibility | 15,341 | 0.0464 | 0.6400 | [0.6351, 0.6443] | 0.0836 | 1.80 | 0.6905 |
| **Tyres** | 21,416 | 0.0648 | **0.6031** | [0.5990, 0.6073] | 0.0927 | 1.43 | 0.6564 |

Exploratory; CIs unadjusted for multiplicity. The top three rows by AUROC include two
sections with **N=13 and N=20** — not interpretable, reported per the no-post-hoc-merging
instruction. Among sections with adequate counts the spread is **0.603 (Tyres) → 0.794
(Body/chassis/structure)**, a 0.19 range on the same score.

**This independently replicates the 2026-07 tyre finding** (tyres least detectable at
0.61). Ordering is preserved: suspension > brakes > tyres. Structure and steering — both
corrosion/wear-driven and cumulative — are the most predictable; tyres, a
consumable that is replaced rather than degraded monotonically, the least.

Secondary 7-category cross-check: 6 of these 14 sections map to `None` under
`rfr_mapping._SECTION_TO_CATEGORY` (Noise/emissions, Seat belts ×2, Speedometer,
Identification, Buses), so that taxonomy cannot express this table. Section grain was the
right call.

---

## Robustness: the gradient is a property of the task, not the model

All 12 banked score vectors — **4 architectures × up to 5 seeds** — reproduce it, monotone
in every case:

| Architecture | B1 | B2 | B3 (A_clean) | S1 |
|---|---:|---:|---:|---:|
| CatBoost (2 seeds) | 0.7120 | 0.7656–0.7658 | 0.8168 | 0.6549–0.6550 |
| LightGBM (5 seeds) | 0.7124–0.7125 | 0.7660–0.7666 | 0.8168–0.8179 | 0.6562–0.6566 |
| RealMLP (3 seeds) | 0.7122–0.7123 | 0.7657–0.7660 | 0.8166–0.8169 | 0.6554–0.6563 |
| XGBoost (2 seeds) | 0.7116 | 0.7653 | 0.8163–0.8164 | 0.6557–0.6558 |

Total spread on B3 `A_clean` across all architectures and seeds: **0.0016**.

---

## Gate 0 — all passed before any outcome number was computed

| Check | Result |
|---|---|
| G0.1 AUROC reproduction vs banked fit | **abs diff = 0.0** (bit-exact; bar 1e-6) |
| G0.2 train/eval `test_id` overlap | 0 |
| Label agreement (`y_final` vs banked `y`) | 0 disagreements / 330,665 |
| G0.3 unknown disposition on target items | 0 |
| G0.4 target items not post-2018 | 0 |
| G0.5 D-marked **non-failure** items | **91** — confirms fail-gating was required |
| G0.6 class-scoped catalogue miss | 139 / 737,315 items (0.019%) |
| G0.7 legacy `rfr_id < 10000` failure items | 0 |
| G0.8 mixture identity | max abs err **1.11e-16** over 22 outcomes |
| G0.9 forbidden derived columns in executed SQL | 0 over 26 statements |

### G0.10 reconciliation — results table vs items table

| | B1 = 1 | B1 = 0 |
|---|---:|---:|
| `y_initial` = 1 | 93,796 | **86** |
| `y_initial` = 0 | **0** | 236,783 |

Investigated as required. **All 86 have zero item rows in the lake** (69 FAIL, 17 PRS) —
a DVSA publication completeness gap of 0.092% of initial failures, not a decoding error.
The reverse cell is exactly **0**: no passing test carries a major/dangerous item. The two
tables agree to 99.91%.

---

## The nine questions

1. **Does the frozen score discriminate higher-burden failures better?** Yes, decisively.
   0.7117 → 0.7498 → 0.7910 pooled; 0.7117 → 0.7656 → 0.8171 on held-fixed negatives.
2. **Monotone gradient?** Yes, strictly, on both axes of measurement, with non-overlapping
   CIs and no saturation by ≥3.
3. **T1_NY_LIKE AUROC?** **0.7280** [0.7259, 0.7303]; `A_clean` 0.7435.
4. **vs the two baselines?** +0.0148 vs T0 AutoSafe (CI [0.0134, 0.0165], excludes 0);
   +0.0162 vs T0 DVSA-initial. Real but small.
5. **Dangerous specifically?** **0.6545** — 0.059 *worse* than broad failure. n=26,113, so
   this is precision-adequate, not a thin-data artifact.
6. **Most/least predictable components?** Most: body/chassis/structure 0.794, steering
   0.793, suspension 0.769. Least: **tyres 0.603**, visibility 0.640, road wheels 0.661.
7. **% of initial failures inside the NY-like outcome?** **66.1%** (B2 58.6%, B3 33.5%).
8. **Does this support the severity-threshold hypothesis?** **Partially, and on the burden
   axis only.** At *comparable prevalence* — B3 at 0.0952 vs NY's **~0.108** (a derived
   figure, daggered in `NY_COMPARISON_AUDIT_2026_08_15.md:81`; the published GitHub subset
   is 0.1032) — AutoSafe reaches 0.7910 against NY CatBoost's 0.832. The gap narrows from
   0.119 to **0.041**, so roughly two-thirds is attributable to outcome definition and
   about one-third is not. PR lift tells the same story: 1.85 at T0 → **3.04** at B3,
   against NY CatBoost's ~3.7. This directly extends the audit's own observation that the
   two tasks run "at a different base rate (~10.8% vs 22.9%)" (`:482`) — that base-rate
   difference is now quantified as roughly two-thirds of the gap. But the *specific*
   NY-like threshold structure earns only +0.0148, and the severity axis is negative. Your
   §18 "moderate evidence" band is the right reading, with the correction that it is
   **burden, not severity**, that carries it.
9. **Is one dedicated NY-like CatBoost fit justified?** **NO** — see below.

---

## Stage-2 verdict: NO

Pre-registered rule (fixed before any number was visible):

> YES iff `A_clean` rises monotonically across B1→B2→B3 **AND**
> `AUROC(T1_NY_LIKE) − AUROC(T0_AUTOSAFE) ≥ +0.02` with the paired CI excluding zero.

| Condition | Value | Met |
|---|---|---|
| A_clean monotone | 0.7117 → 0.7656 → 0.8171 | ✅ |
| Paired CI excludes zero | [+0.0134, +0.0165] | ✅ |
| Δ ≥ +0.02 | **+0.0148** | ❌ |

**The gate does not fire.** Reported as it stands; a failed primary is not rescued by a
secondary. B3 (+0.0778) and M1 (+0.0699) clear the numeric bar comfortably, but they are
**not the pre-registered target** — retargeting Stage 2 at a burden-defined outcome is a
new question needing its own prereg, not a rescue of this one. That is a decision for
Henri, and the case for it is now quantified rather than speculative.

---

## Caveats

- `out/fits/s2/PREREG_SHA.json` records `frozen_at_queue_start: false`. The *predictions*
  are frozen and reproduce bit-exactly; s2's own prereg pinning is not clean. The estimand
  here — how one fixed score ranks different labels — does not depend on it.
- The house floor 0.002052 is a seed/retrain quantity and **does not apply**: the score is
  fixed, so there is no retrain noise. All deltas above are far outside sampling noise.
- Stage 1 cannot bound how well a model *trained on* a severe target would do. A low
  T1_NY_LIKE reading is not evidence the severe target is intrinsically hard; `A_clean` is
  what separates those readings, and it is high (0.7435) — the information is there.
- Component CIs are unadjusted for multiplicity and three sections have N < 25.

## Recorded deviations

1. `n_dangerous` is **fail-gated** (`disp ∈ {F,P} AND dangerous_mark='D'`), unlike
   `factory/severity.py:severity_expr`, which tests `dangerous_mark` first and so grades
   D-marked advisories as dangerous. 91 such items exist on target tests. `severity.py`
   was not modified.
2. G0.9 originally scanned this file's own source and self-matched on its
   `FORBIDDEN_COLUMNS` declaration. Rewritten to scan the SQL actually executed — a
   strictly stronger check. Found and fixed before any result existed.
