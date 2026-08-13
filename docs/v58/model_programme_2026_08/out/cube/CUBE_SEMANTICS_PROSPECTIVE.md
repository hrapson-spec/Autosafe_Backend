# CUBE_SEMANTICS_PROSPECTIVE — discretionary semantics frozen before any outcome

Governed by `prereg/PREREG_CUBE_v1.md` (sha `30371fc3bbc47c9b…`). Authored 2026-08-13, **before any
cube cell has been fitted or any candidate AUROC observed.**

Purpose: every choice below is a researcher degree of freedom. Left open, each becomes a lever that
can be tuned against outcomes after the fact. Fixed here, with the reasoning that fixed it, they
become falsifiable commitments. Anything not written down here is not defined, and a cell depending
on an undefined semantic is inadmissible.

Where a value is genuinely arbitrary, **two pre-registered variants** are named. Variants are
reported together; the better-scoring one is never selected post hoc.

---

## 1. Three algebraic collapses that constrain everything below

Before choosing minimum-observation rules, note what is identically true at small *n*. For a series
of *n* observations:

| n | Identity | Consequence |
|---|---|---|
| 2 | `range = \|latest − earliest\| = \|amplitude\|` | range carries **no information** beyond amplitude |
| 2 | `sd = range / √2` | sd is a rescaling of range, hence of amplitude |
| 3 | OLS slope is exactly determined, 1 residual df | slope exists but carries no residual signal |

So at n=2 the three "volatility/direction" statistics collapse to one number wearing three names.
Emitting all three would manufacture two duplicate columns against the 400-column budget and inflate
apparent block width for no information. This drives §3.

**Rule.** `amplitude` requires n ≥ 2. `range` and `sd` require **n ≥ 3**. This is stricter than the
v3 draft (which said ≥2) and the change is algebraic, not empirical.

---

## 2. EWM half-lives

Two EWM bases are registered, one per index. **No sweep. No third value.**

| Basis | Half-life | Reasoning |
|---|---|---|
| test-indexed | **2 tests** | Full-depth mean observable depth is 7.89 priors; item-window depth is far shallower. A 2-test half-life puts ~50% of weight on the two most recent tests while still reading depth, and matches the `t3` spine window's intent without duplicating it. |
| time-indexed | **730 days** | Two MOT cycles; aligns with the `24m` spine window so the EWM and the hard window answer the same question two ways rather than two unrelated questions. |

Half-lives are expressed in the manifest as decay `λ = ln2 / halflife`, computed in the declared
index unit. A cell must declare which basis it uses; a cell that does not is inadmissible.

**Why not more values.** Each additional half-life is a near-duplicate column and a free parameter.
Two bases with a stated rationale are defensible; a grid tuned against AUROC is not.

---

## 3. Slope basis — decided per statistic, never globally

A single global choice would be wrong for at least one family. The basis is fixed by what the
quantity physically accrues in:

| Statistic family | Slope basis | Reasoning |
|---|---|---|
| defect / advisory **counts** | **test position** | The count is generated per test. Regressing on elapsed time confounds the trend with test cadence — and cadence is already measured independently in C2. Using time here would double-count it and make C2's incremental value untestable. |
| **burden / severity** | **test position** | Same generating process as counts. |
| **mileage** | **elapsed time** | Mileage accrues continuously in time; the slope is a physical rate (miles/day → annualised). Test position is not the generating variable. |
| **rate** statistics (per-year, per-1000mi) | **elapsed time** | Already per-unit-time by construction; a per-test slope of a per-time rate has no clean interpretation. |

**Minimum observations for a slope: n ≥ 3**, with the support column mandatory. Slopes at exactly
n=3 are exactly determined (§1) and are flagged `slope_exact=true` so downstream analysis can
separate them from slopes carrying residual information. Slopes are **not** emitted at n < 3 — they
are NULL, never 0.

---

## 4. Minimum observations, consolidated

| Statistic | Minimum n | Basis |
|---|---|---|
| presence, count, latest | 1 | — |
| rate | denominator ≥ 1 | else NULL, never 0 |
| amplitude (`latest − earliest`) | 2 | a difference needs two points |
| range (`max − min`) | **3** | at n=2 identical to \|amplitude\| (§1) |
| sd | **3** | at n=2 equals range/√2 (§1) |
| slope | **3** | identifiability; exactness flagged at n=3 |
| EWM | 2 | below 2 it equals `latest` |
| streak (current, longest) | 1 | — |
| `latest − life_mean` | 2 | else identically 0 |
| `recent_rate − life_rate` | both windows' denominators ≥ 1 | else NULL |

Every statistic below its minimum emits **NULL plus its support column**. No statistic ever emits 0
to mean "not computable" — that is the exact conflation this programme is repairing elsewhere.

---

## 5. Short-interval pass — identified by type first, interval second

A "failure followed by a short-interval pass or PRS" is a repair proxy in C5. It must not be
inferred from interval alone, because `test_type` coverage is asymmetric by era (the factory
enriches `test_type` from lookup sources for vintages lacking it).

**Rule, in priority order:**

1. If `test_type` is present and reliable for that record → a retest is `test_type = 'RT'`. Use it.
2. If `test_type` is absent → fall back to **interval ≤ 28 days**.
3. Emit `short_pass_rule_fired ∈ {type, interval, unavailable}` alongside every C5 repair-proxy
   column, so an era-driven shift in which rule fires is visible rather than silent.

**Why 28 days.** DVSA permits a free partial retest within 10 working days at the same station; 28
calendar days covers that window plus rescheduling, while sitting far below the shortest ordinary
annual interval (v55's `gap_band` classifies anything under 300 days as "early"). The gap between
28 and 300 days is wide enough that the threshold is not sensitive to its exact value.

**Pre-registered sensitivity variants: 14 and 42 days.** Reported alongside 28. Not selected on
outcome.

---

## 6. Recurrence and apparent resolution

Both are **administrative proxies**. The data never establish that a repair occurred, and no column
name, dictionary entry or report may assert that it did.

**Apparently resolved.** A defect family present at observable test *t* and absent at the next
observable test *t+1*, where *t+1* qualifies as observable **only if its item-observability state is
`ITEMS_PRESENT_ZERO_DEFECTS` or `ITEMS_PRESENT_WITH_DEFECTS`**. If *t+1* is `ITEMS_UNAVAILABLE` or
`ITEMS_EXPECTED_MISSING`, the resolution state is **UNKNOWN** — never "resolved".

This is the load-bearing link to Gate 4. Without the observability index, "the defect disappeared"
and "we cannot see whether the defect disappeared" are the same observation, and every C5 column
would inherit the conflation the programme is repairing.

**Recurred.** A family that was apparently resolved at *t+1* and is present again at some *t+k*,
k ≥ 2. A family present at consecutive tests is **persistent**, not recurrent — the two are separate
columns and must not be summed.

**Same-day.** A fail followed by a same-day retest is **not** a resolution event. Within-day
ordering is unidentified (measured P(`test_id`(NT) < `test_id`(RT)) = 0.4978, indistinguishable from
chance), so same-day records collapse to a single day-grain observation before any transition is
evaluated. Transitions are evaluated between *days*, never within one.

---

## 7. Invalid and discontinuous mileage

Thresholds inherited from `/Users/henrirapson/autosafe/work/usage_guardrails.py`
(`MAX_IMPLAUSIBLE_MPD = 500`, `MAX_PLAUSIBLE_MPD = 200`, `MIN_DAYS_BETWEEN = 1`).

**A reading is rejected if any of:** null · exactly 0 (0.15% of rows — a default, not an odometer) ·
exactly 999,999 (the field's digit-width ceiling, not a reading) · negative.

**An interval is rejected if any of:** either endpoint rejected · negative delta (rollback:
replacement or tamper) · elapsed days < 1 · implied rate > 500 mi/day.

**An interval is capped-and-flagged if:** implied rate is in (200, 500] mi/day → capped at 200,
`mileage_capped = true`.

**Unit ambiguity is fatal, not convertible.** DVSA issued a km correction affecting 2022. Any
interval spanning the 2021→2022 boundary without a resolved unit flag is **REJECTED**, not
converted — a wrong conversion is worse than a missing value because it is invisible. This
implements R4's K-8.

**Consequence.** A rejected interval makes every mileage-dependent statistic on it **NULL** — never
0, never imputed, never back-filled from a cohort mean. The existing
`min(8000, test_mileage // n_prior_tests)` construction is the precise anti-pattern this rule
forbids: it fabricates a plausible-looking value from no evidence.

---

## 8. Support and shrinkage

**Raw numerator and denominator always ship, beside every ratio.** Non-negotiable — it is what
distinguishes one failure in one test from five in twenty, and it means no shrinkage choice can
conceal the underlying evidence.

**Shrinkage is additive, never substitutive.** Where a rate has a thin denominator, an
empirical-Bayes shrunk variant may be emitted *in addition to* the raw triple, never in place of it.

- Direction of shrinkage: toward the immediate hierarchy parent — `code → family → section →
  global`. Never toward a global mean directly from a code.
- Strength: **m = 10 pseudo-counts** at family level, m = 25 at section level. Rationale: at m=10 a
  family rate needs ~10 observations before the observed rate carries half the weight, which is
  roughly one vehicle's full observable history at current depth.
- **Priors are fitted on the development partition only** and never on evaluation rows.
- Prior fitting must be weighted by `inclusion_weight`. R4 measured `inclusion_weight ≡ 1.0` across
  the current `r1m` frame, meaning the weighting is presently inert; that fact is stated wherever a
  shrunk column is reported, so an enriched future build does not silently change the semantics.

**Pre-registered variant: m = 25 at family level.** Reported alongside m = 10. Not selected on
outcome.

---

## 9. Window support — what "observable" means

Every window emits its own support set once (shared via `denominator_ref`, per
`PREREG_CUBE_v1.md` §1.4), never duplicated per feature:

`n_obs_tests` · `span_days` · `miles_exposed` · `item_cov` · `left_cens`

- `item_cov` is the fraction of the window's tests in an `ITEMS_PRESENT_*` observability state — not
  the fraction that produced a join.
- `left_cens` is true when the window's start precedes the vehicle's first observable record, i.e.
  the window is truncated rather than empty.
- An item-axis window whose lifetime bound is the item-data floor is named `since_item_floor`,
  never `life`. A truncated window may not carry a name implying completeness.

---

## 10. What is deliberately NOT fixed here

These are decided by measurement, not by fiat, and are named so their openness is explicit:

- `k_req` per architecture — fixed from measured σ_Δ (`PREREG_CUBE_v1.md` §4.2), after Gate 5.
- Which exact `rfr_id` codes earn wide columns — judgement over support, stability, compute and
  overfitting risk; the 0.5% / 10,000-vehicle rule is **advisory, not binding**, and rare codes may
  be carried by hierarchical backoff or another compact representation rather than excluded.
- Whether any given coverage field is admitted as a *predictive* input — tested separately from its
  *interpretive* role, and rejected if its gain is principally an era or publisher shortcut.
- The final adopted set — governed by `ADOPTED_HISTORY_CAP = 150` and displacement evidence.

---

## Change control

This document is hashed alongside `PREREG_CUBE_v1.md`. Any change after the first candidate screen
requires an attached failing test demonstrating the prior semantic was wrong (deviate-with-test, per
`FACTORY_CONTRACT.md`), and is recorded as an amendment with its own hash — never edited in place.
