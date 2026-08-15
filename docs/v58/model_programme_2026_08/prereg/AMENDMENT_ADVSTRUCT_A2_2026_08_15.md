# AMENDMENT A2 — count-stratum binning, frozen from the cell-count grid

**Parent:** `PREREG_ADVSTRUCT_2026_08_15.md`, sha256 `35ee4828c47f4b88…`, §7.2 and §7.5.

**No outcome rate, breadth gradient, β or any quantity conditional on an outcome had
been computed at the time of this amendment.** The cell-count grid is a marginal
distribution of the prior-side exposure only. The gate diagnostics
(`out/ADVSTRUCT_BUILD_DIAG.json`) contain label prevalences, which were already banked in
`out/SEVERITY_RESULT.json` before this study began and are reproduced there only as a
correctness check.

§7.2 of the parent requires the grid to be published **before** the binning is frozen, and
coarsening after seeing rates is prohibited. This is that freeze.

## The measured grid — `adv_n_last` × `adv_breadth_last`, TRAIN

Deduplicated per §5.6, most recent prior test-day, targets in state `observable` or
`observable_zero` (n = 923,605 with a prior day).

| c \ b | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7+ | **total** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 530,977 | | | | | | | | 530,977 |
| 1 | | 213,711 | | | | | | | 213,711 |
| 2 | | 28,057 | 76,121 | | | | | | 104,178 |
| 3 | | 2,451 | 23,489 | 18,428 | | | | | 44,368 |
| 4 | | 260 | 4,752 | 9,419 | 3,496 | | | | 17,927 |
| 5 | | 38 | 936 | 3,162 | 2,568 | 527 | | | 7,231 |
| 6 | | 10 | 203 | 981 | 1,275 | 509 | 46 | | 3,024 |
| 7 | | 0 | 45 | 300 | 535 | 338 | 71 | 5 | 1,294 |
| 8 | | 0 | 16 | 90 | 182 | 162 | 49 | 2 | 501 |
| 9 | | 0 | 4 | 34 | 78 | 78 | 32 | 4 | 230 |
| ≥10 | | | | | | | | | 163 |

EVAL has the same shape at ~1/3 scale (`out/ADVSTRUCT_BUILD_DIAG.json`).

## Amendments

### A2.1 — `c = 1` is excluded, as the parent already specified

`c = 1` admits only `b = 1` (213,711 train / 73,698 eval, zero off-diagonal). It carries no
within-count breadth contrast. Confirmed by measurement, not assumed.

### A2.2 — six count strata, not seven

| stratum | TRAIN n | EVAL n | TRAIN pos (≈10.0%) | EVAL pos (≈9.5%) |
|---|---:|---:|---:|---:|
| `c=2` | 104,178 | 38,366 | ~10,400 | ~3,650 |
| `c=3` | 44,368 | 16,977 | ~4,430 | ~1,620 |
| `c=4` | 17,927 | 7,224 | ~1,790 | ~690 |
| `c=5` | 7,231 | 3,073 | ~720 | ~290 |
| `c=6` | 3,024 | 1,399 | ~300 | ~130 |
| `c=7-8` **(pooled)** | 1,795 | 845 | ~180 | ~80 |

`c ≥ 9` is **excluded** — 393 TRAIN / 192 EVAL rows, below the parent's 500-row floor.
`c = 7` and `c = 8` are pooled for the same reason; separately they are 1,294 / 501 TRAIN and
449 / 396 EVAL, and `c=8` alone fails the floor on both frames.

Expected positive counts above are prevalence × stratum n, i.e. the marginal expectation
before any conditioning. Every stratum clears the parent's 50-positive floor; `c=7-8` on EVAL
is the binding one at ~80.

### A2.3 — the verdict bar rescales, and rescales *upward*

The parent required β > 0 with CI clear of 0 in **≥5 of 7** count strata — a proportion of
0.714. Preserving that proportion over six strata gives ⌈0.714 × 6⌉ = **5**.

> **SUPPORTED now requires ≥5 of 6 count strata**, not ≥4 of 6.

⌈⌉ is used rather than ⌊⌋ so the rescaling cannot weaken the bar. 5/6 = 0.833 is **stricter**
than the original 5/7 = 0.714. Chosen deliberately: a binning change made by the analyst must
not make their own hypothesis easier to support.

### A2.4 — breadth is NOT binned for β estimation

`β_breadth|count` is estimated with `b` **continuous** within each count stratum. Binning
breadth is required only for the descriptive rate table.

In the rate table, cells below 500 rows are **shown and flagged**, never pooled away and never
silently dropped. They are excluded from the ≥5-of-6 verdict count but remain visible, so a
reader can see the thin diagonal (`c=4,b=1` = 260; `c=5,b=1` = 38; `c=6,b=1` = 10) rather than
having it disappear into a neighbour.

### A2.5 — `c = 0` is a reported category, not a stratum

530,977 TRAIN / 170,385 EVAL targets are in state `observable_zero` — an observed prior day
with **zero** advisories. Per §5.5 this is a certain zero, not a missing value, and it is
reported as its own row. It contributes no within-count contrast and enters no stratum.

Separately, 76,394 TRAIN / 18,506 EVAL targets have **no prior test at all**, and 1 TRAIN / 0
EVAL have a prior day whose items are unobservable. Three distinct states, three distinct
rows, never merged.

---

## Unrelated finding recorded here because it was measured in the same build

**Same-day deduplication (§5.6) is materially larger than anticipated.** It reduced the
advisory count on **193,861 TRAIN targets (21.0% of those with a prior day)** and **69,900
EVAL targets (22.4%)**, with a maximum reduction of **−46** items on a single target.

This matters more than a cosmetic correction, because `adv_n_last` is the **conditioning
variable** of the entire primary estimand. Duplication arises from a fail and its retest on
the same day re-recording the same physical advisory, so it is concentrated in vehicles whose
prior presentation *failed* — which is itself associated with the outcome. Un-deduplicated
counts would therefore have produced count strata whose membership is confounded with prior
failure, and the within-count comparison would not have been within-count at all.

Breadth is unaffected by the dedup (distinct systems are invariant to duplicate items), so
the correction moves the stratifier, not the exposure.

---

**Amended 2026-08-15. No outcome-conditional quantity seen. Parent sha `35ee4828c47f4b88`.**
