# factory/severity.py — dangerous is now FAIL-GATED (repair, 2026-08-15)

## The defect

`classify_severity` tested `dangerous_mark` **before** disposition, so any D-marked item
graded `dangerous` regardless of whether it failed. Under DVSA rules a dangerous defect is
a failure: only a fail-bearing disposition (`F`/`P`) can be dangerous. The SQL twin
`severity_expr` carried the identical ordering.

Found while building the severity Stage-1 study, which worked around it with a hand-written
fail-gated count rather than modifying shared code.

## Blast radius — measured, post-2018 lake-wide

| disposition | D-marked | total items |
|---|---:|---:|
| F (fail) | 28,216,146 | 165,451,612 |
| P (PRS) | 1,487,821 | 24,501,572 |
| **A (advisory)** | **73,814** | 407,934,807 |
| M (minor) | 0 | 46,007,934 |

Old rule graded 29,777,781 items dangerous (reconciles exactly with
`DATA_ASSESSMENT.md`); **73,814 of them — 0.248% — are advisories**, now correctly
`advisory`. `M`+`D` never occurs in the lake, so the only real-world effect is on `A`+`D`.

On the eval-2024 target population the effect is 91 items; on the 2020-2023 training
targets, 252.

## The repair

```python
if disposition in FAIL_BEARING:              # F, P
    return DANGEROUS if mark == 'D' else MAJOR
if disposition == MINOR:  return MINOR
return ADVISORY
```

`severity_expr` reordered identically. **`is_fail_bearing` is untouched**, so
`n_fail_items_initial` / `n_major_or_dangerous` (the F+P basis) do not move — only the
dangerous/major split within it, plus advisories that were wrongly promoted.

**Additive:** `is_anomalous_dangerous_mark()` + `anomalous_dangerous_mark_expr()` keep the
73,814 anomalous marks countable instead of silently absorbed into `advisory`.

## Verification

- New `factory/tests/test_severity_fail_gating.py` — **42 tests**: the regression itself,
  a fixture **proven able to fail** (reimplements the old ordering and asserts it differs),
  everything the repair must not change, the anomaly flag, and an **exhaustive
  Python-vs-SQL-twin equivalence grid** so the two can never diverge again.
- `factory/tests/test_falsifiers.py` F3 crosstab **amended**: its
  `("post_2018","M","D") -> DANGEROUS` row **pinned the defect**. Corrected to `MINOR`, and
  an `("post_2018","A","D") -> ADVISORY` row added — **A+D was never covered**, despite
  being the only case that occurs in the data.
- Full factory suite: **239 passed, 2 skipped, 0 failed** (2 pre-existing failures were the
  defect-pinning F3 rows, now corrected).
- **Loop closed against the experiment:** recomputing `n_dangerous` on the eval-2024
  targets through the repaired module gives 36,681 — identical to the hand-gated count used
  in Stage 1/2, **0 rows disagreeing** — and the new anomaly counter returns exactly the 91
  items Stage 1's G0.5 reported.

## What is NOT done

**No frame, feature or fit was rebuilt.** Banked artifacts retain their old values. Any
consumer of `n_dangerous`, `cat_*_dangerous`, `b7r_prev_initial_n_dangerous`,
`max_severity_ord` or the B7D severity-transition ladder will change **on next rebuild**.
Those columns are research-only and currently parked, so nothing in flight is invalidated;
the change should be noted at the next substrate bump rather than back-applied.
