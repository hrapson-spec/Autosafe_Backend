# AMENDMENT A3 — descriptive rule frozen from TRAIN, before EVAL is touched

**Parent:** `PREREG_ADVSTRUCT_2026_08_15.md` sha `35ee4828c47f4b88…`, §7.4 ("discovery on
TRAIN, rule frozen, confirmation out-of-time on EVAL").
**Prior amendment:** `AMENDMENT_ADVSTRUCT_A2_2026_08_15.md` sha `3579ed437b6674dc…`.

**No EVAL outcome-conditional quantity has been computed.** `advstruct_analyze.py` has been
run on `train_flat4y` only. The EVAL prior-side table exists (built in the same pass as
TRAIN, from prior-side data only) and the EVAL label prevalences were reproduced in the §7.1
correctness gate, but those were already banked in `out/SEVERITY_RESULT.json` before this
study began. No EVAL β, rate, or cell has been estimated.

---

## 1. What TRAIN showed

Source `out/ADVSTRUCT_DESCRIPTIVE_TRAIN.json`, 2000 vehicle-clustered bootstrap reps,
n = 923,604 with a prior day, 178,523 in the eligible count strata.

### Estimand A — β_breadth|count

| stratum | n | β | CI95 | CI clear |
|---|---:|---:|---|:--:|
| `c=2` | 104,178 | +0.3006 | [+0.2604, +0.3405] | ✓ |
| `c=3` | 44,368 | +0.2540 | [+0.2108, +0.2946] | ✓ |
| `c=4` | 17,927 | +0.1204 | [+0.0718, +0.1677] | ✓ |
| `c=5` | 7,231 | +0.0717 | [+0.0109, +0.1372] | ✓ |
| `c=6` | 3,024 | +0.0905 | [+0.0007, +0.1774] | ✓ |
| `c=7-8` | 1,795 | +0.0187 | [−0.0800, +0.1231] | ✗ |

**5 of 6** — meets the A2.3 bar. β **decays monotonically** with count: breadth is most
informative where count is least informative, and adds nothing by `c=7-8`.

### Estimand B — the system-composition falsifier

| quantity | value |
|---|---:|
| β(breadth \| total count only), matched comparator, same rows | **+0.2139** |
| β(breadth \| full 9-system composition) | **+0.0476**, CI [+0.0338, +0.0619] |
| **share of the gradient composition absorbs** | **77.7%** |
| β(items_per_system \| composition) | **−0.1220**, CI [−0.1488, −0.0953] |

⚠ **The TRAIN Estimand B is IN-SAMPLE** — the additive expectation is fitted and evaluated on
the same rows. +0.0476 is an upper bound. Its out-of-sample value is what EVAL measures.

Per-system additive coefficients (logit per advisory item), **frozen** for EVAL:

| system | coef | | system | coef |
|---|---:|---|---|---:|
| `steering` | +0.59691 | | `suspension` | +0.37449 |
| `body_structure` | +0.52152 | | `brakes` | +0.27660 |
| `noise_emissions` | +0.51658 | | `wheels_tyres` | +0.13976 |
| `lamps_electrical` | +0.51557 | | `visibility` | +0.10011 |
| `seatbelts_srs` | +0.42544 | | *intercept* | −2.40693 |

### The visible counterexample to naive breadth

At `c=2`, TRAIN: `suspension` alone (b=1, n=5,000) carries rate **0.1904**, while
`wheels_tyres+brakes` (b=2, n=24,902) carries **0.1206**. A narrower history in a worse system
outranks a wider history in benign ones. Risk spans 2.1× at identical count *and* identical
breadth (`wheels_tyres` alone 0.0907 → `suspension` alone 0.1904).

### Controls and sensitivities

| control | β_survival |
|---|---:|
| `target_year` | 0.996 |
| `make` | 1.019 |
| `postcode_area` | 1.187 |
| `prior_depth_band` | 0.659 |
| `age_quartile` | 0.595 |

Tied-day sensitivity (836,649 untied targets): `c=2` +0.3223, `c=3` +0.2499 — unchanged in
sign and magnitude. Day-union is not manufacturing the gradient.

---

## 2. The frozen prediction for EVAL

Confirmation requires **all four**, on eval2024, with the additive coefficients above used
**unchanged** and never refitted:

| # | Requirement |
|---|---|
| **C1** | β_breadth\|count > 0 with clustered CI clear of 0 in **≥5 of 6** count strata |
| **C2** | β(breadth \| composition) > 0 with clustered CI clear of 0, **out-of-sample** |
| **C3** | β_survival ≥ 0.50 against **both** `age_quartile` and `prior_depth_band` |
| **C4** | sign of β holds in **every** target-year stratum |

**Directional predictions recorded so they can fail:**

- β decays monotonically in `c`; `c=2` largest, `c=7-8` smallest and not CI-clear.
- Composition absorbs a **majority** of the count-conditional gradient — predicted surviving
  share **15-30%** out-of-sample, against 22.3% in-sample.
- `items_per_system | composition` is **negative** and CI-clear.
- `target_year` survival ≈ 1: the gradient is unmoved by era despite recording drift.

## 3. What FALSIFIES

Any of: C1 fails (≤4 of 6 strata); **C2 fails — β(breadth\|composition) CI includes zero
out-of-sample**, which would mean Estimand A was composition all along; C3 fails on either
control; C4 fails.

⚠ **C2 is the load-bearing one.** A pass on C1 with a failure on C2 is a FALSIFIED result for
the structural claim, not a partial success, and must be reported as such.

## 4. What may not happen after this point

- The additive coefficients may not be refitted on EVAL.
- The strata, the 500-row cell floor and the ≥5-of-6 bar may not move.
- `items_per_system` may not be substituted for breadth as the headline if breadth fails.
- No further control may be added to rescue a failed C3.

---

## 5. Methodological note — a defect found and corrected before freezing

The first TRAIN pass fitted Estimand B over **all 923,604 rows**, including `c=0` (530,977)
and `c=1` (213,711), where breadth is a deterministic function of count and has no free
variation. On that population `β(items_per_system | composition)` was **+0.0839**; on the
correct eligible population it is **−0.1220**. The coefficient **changed sign**.

Recorded because the direction of the error is instructive: including rows with no
within-stratum contrast does not merely dilute an estimate, it can invert one. The matched
count-only comparator (+0.2139) was added at the same time so the absorbed share is read from
two coefficients on identical rows rather than inferred across two populations.

---

**Frozen 2026-08-15. No EVAL outcome-conditional quantity seen.**
Parent sha `35ee4828c47f4b88`. A2 sha `3579ed437b6674dc`.
