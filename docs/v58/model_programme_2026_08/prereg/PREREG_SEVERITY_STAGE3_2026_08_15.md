# PREREG — Severity Stage 3: the next B3 optimisation rung (full grade)

**Drafted 2026-08-15. CONDITIONAL — freezes and executes ONLY if the Stage-2 confirmation
run holds.** Written before the confirmation seeds landed, so the rung is chosen on
mechanism, not on the confirmed number.

## §1 Trigger

Execute **only if** the 4-seed paired confirmation (`sev.B3` vs `sev.CTRL`, seeds
{202,303,404,505}) yields a **k=5 mean paired Δ ≥ +0.005** with the **sign consistent
across all five seeds**. Sign consistency is required because the B7 precedent failed
exactly there: +7.754e-04 at k=2 → +2.765e-04 at k=5 with one seed negative.

If the mean holds but a seed flips sign → **STOP and report**, do not proceed to Stage 3.
If Δ falls below +0.005 → Stage 2 is downgraded to sub-trigger and Stage 3 is **void**.

## §2 The rung, and why this one

Both Stage-2 arms are **capacity-censored, measured**: `best_iteration` was 595 (B3), 591
(control) and 598 (the banked benchmark) against `iterations: 600`. All three were still
descending at the cap. `PREREG_STAGE2.md:288` requires exactly this reading —
CAPACITY-CENSORED, not CAPACITY-BOUNDED.

So the next rung is **grade, not scale**: `preset/grade: screen → full`
(`fit_runner.py:46-60`) — iterations 600 → 2000, learning_rate 0.06 → 0.03, border_count
128 → 254, od_wait 60 → 200. Everything else unchanged.

**Scale is not available.** The 2M rung was removed by [OWNER-AMEND-1]
(`PREREG_STAGE2.md:35-39`); r1m is already the top of the ladder, and r250k is the wrong
direction. **Feature work is deliberately excluded** — no new features, no feature
selection, no architecture comparison, no representation change.

## §3 Design — the control must also move up

Grade and target would confound if a full-grade B3 arm were compared against a
screen-grade control. Therefore **four fits**:

| cell | grade | label |
|---|---|---|
| `sev.B3.full` | full | `y_b3` |
| `sev.CTRL.full` | full | `y_final` |
| (banked) `sev.B3` | screen | `y_b3` |
| (banked) `sev.CTRL` | screen | `y_final` |

Seeds {101, 202} first (k=2). Primary estimand is the **paired Δ at full grade**
(`sev.B3.full` − `sev.CTRL.full`), compared with the screen-grade Δ already measured. That
yields the quantity of interest: **does extra capacity buy more for the B3 target than for
the ordinary-failure target?**

The programme's measured grade bias on deltas is small (`stage2_grade_bias`
`[b0-6 vs b0] = −2.90e-05`) but rests on n=2 observations with no slope and no per-block
transfer claimed — it is **not** used as a correction, only as context.

Config diff from the Stage-2 configs: `preset` and `grade` only. The explicit 241-column
pin (deviation D-S2-1) carries over unchanged, since `blocks.py` still resolves to 247.

## §4 Cost — measured, not estimated

Full grade at 755,389 rows × 241 features: **1,327 s / 1,280 s** (`logs/queue_ledger.tsv`,
`B10_ANCHOR_B0_6`). Four fits ≈ **88 minutes** serial. Screen-grade equivalents were 475 s.

## §5 Gates

Same as Stage 2, plus one:

- **G1-full**: `sev.CTRL.full` must reproduce the banked full-grade anchor
  `s2.D.anchor.b0-6.seed101` within the same <1e-4 band, with its `quantization` string
  matching. This is the full-grade twin of the Stage-2 null-change control.
- Label integrity, row/label identity fences, and the frozen-benchmark reproduction check
  carry over unchanged.
- **Measured refit nuisance applies**: on the B3 label a pure refit read −6.87e-04 with a
  CI excluding zero. **The CI is not a gate condition** at any stage.

## §6 Decision rule — bound before the fits

Let `Δ_screen` = the confirmed Stage-2 paired delta, `Δ_full` = the Stage-3 paired delta.

| outcome | verdict |
|---|---|
| `Δ_full ≥ Δ_screen + 0.002`, sign consistent | capacity is a live lever for B3 — proceed to a further capacity read |
| `Δ_full` within ±0.002 of `Δ_screen` | capacity is **exhausted for the target contrast**; the B3 advantage is real but does not grow with budget. **STOP the capacity ladder.** |
| `Δ_full < Δ_screen − 0.002` | the screen-grade advantage was partly a capacity artefact — report and re-examine |

±0.002 is ~2.3× the measured refit nuisance and ~0.8× MDE(k=1); at k=2 the MDE tightens to
≈1.7e-3, so the band is resolvable at the planned seed count.

**A null here is a real result**: it would mean the +0.006 B3 advantage is an *information*
property of the target, not a capacity artefact — which is the more publishable reading and
would close the capacity question rather than leaving it open.

## §7 Explicitly out of scope

Broad-failure feature hunting (owner instruction). Architecture comparison. Hyperparameter
search beyond the two pinned presets. Ensembling. Serving/adoption work.

**The natural question AFTER this one** — not preregistered here — is B3-targeted *feature*
work: the B1–B6 substrate was designed for broad failure, and Stage 1 showed burden is what
carries signal. Prior-burden counts and burden trajectory are the obvious candidates. That
is a feature programme with its own prereg, and it should not start until the capacity
question above is closed either way.
