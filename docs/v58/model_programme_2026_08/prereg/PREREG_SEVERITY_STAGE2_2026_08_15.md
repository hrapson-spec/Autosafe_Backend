# PREREG — Severity Stage 2: train directly on B3

**Frozen 2026-08-15, before any Stage-2 fit exists.** Plan of record:
`~/.claude/plans/engineer-brief-robust-floyd.md` (approved 2026-08-15).

Stage 1 (`out/SEVERITY_RESULT_2026_08_15.md`) is closed. Its `T1_NY_LIKE` gate **FAILED**
(+0.0148 < +0.02) and remains failed. This is a **new experiment**, not a rescue.

## §1 Question

Stage 1 showed the frozen `y_final`-trained score already ranks B3 at **0.7910** without
ever being trained for it. The open question is narrower:

> Does the existing feature substrate contain **additional B3-specific ranking information
> that a model optimised for ordinary failure is leaving on the table?**

**Change only the target.** Benchmark = the banked frozen vector, seed 101:
**B3 0.791310** (k=2 mean 0.791014); secondary **M1 0.783293**.

## §2 Targets (validated in Stage 1, not redefined)

```
y_b3 = n_major_or_dangerous >= 3        -- eval 31,463 (9.515%) / train 74,259 (9.831%)
y_m1 = n_sections_with_md   >= 2        -- eval 39,707 / train(755,389) 93,638
```

Built by `scripts/analysis/severity_collect.py`, the Stage-1 reference implementation,
generalised only in its target-year argument. **Fail-gated** `n_dangerous`
(`disp IN ('F','P') AND dangerous_mark='D'`) — `factory/severity.py:severity_expr` is NOT
used, because it tests `dangerous_mark` before disposition and so grades D-marked
advisories as dangerous (252 such items on training targets, 91 on eval).
`factory/severity.py` is **not modified**; its repair is a separate task.

## §3 What is held fixed

Same v1 frames (`recipe=flat4y/rung=r1m` train, `recipe=eval2024/rung=all` eval), same
241-feature substrate, same `row_filter` (COVID hole), same `valid_fraction`, same
`--borders out/fits/s2/borders_r1m.tsv`, same `--thread-count 4`, same seed 101, same
`grade: screen`. Config diff vs `s2.D.cum.b0-6.json` is **exactly one key**: `label`.

Label-independence verified by inspection: row eligibility is `test_type`/`outcome`;
`assert_fences` reads `tgt_date`/`covid_hole_rows`; `row_filter` is a date predicate;
`split_validation` uses `vehicle_id`+seed and never reads `frame.y`; sampling weights are
functions of `vehicle_id` and prior state.

Shared-code change: **2 lines**, additive — `LABELS` widened, and `fit_contract.py:313`
projects a non-`KEY_COLUMNS` label only when asked. `KEY_COLUMNS` is unchanged, so no
existing caller gains a mandatory column. Covered by
`scripts/analysis/test_severity_stage2.py` (10 tests) and the factory suite
(**199 passed, 2 skipped** on the pinned `~/autosafe-v58/.venv`, duckdb 1.5.5).

## §4 Gates

**G1 — null-change control.** Fit `sev.CTRL` (`label: y_final`, config dict-identical to
canonical) on the **copied** frames, seed 101, same borders/threads. Must reproduce
`s2.D.cum.b0-6.seed101` AUROC **0.7133041237871912**, and its
`convergence_state.quantization` must equal the benchmark's `"unavailable:CatBoostError"`.
Bit-exact expected. **|Δ| ≥ 1e-4 → STOP**; no B3 number is produced.

The benchmark did **not** reuse borders: `borders_r1m.tsv` is a 104-feature file applied to
a 241-feature pool; `fit_runner.py:155-168` swallows the exception and CatBoost quantises
internally. The new arm reproduces those conditions rather than "fixing" them.

**G2 — label integrity.** Eval B3/M1 reproduce Stage 1 exactly (31,463 / 39,707) · zero
passing target tests carry a counted M/D item (measured 0 on both train and eval) · no
D-marked advisory reaches `n_dangerous` · items↔results reconciliation retained (train
mismatch 2/755,389; eval 86/330,665) · zero training targets before 2018-05-20 (measured 0,
min `tgt_date` 2020-01-02) · sha256 of label vectors and of train/eval row-id sets, with
`train ∩ eval = 0`.

**Frame-copy fidelity** (already verified): row order identical per part, original columns
preserved as an exact prefix, exactly `[y_b3, y_m1]` appended, key values identical.

## §5 Escalation rule — bound before the fit

Noise in Δ comes from the **new fit's retrain variance**; the frozen arm is a fixed banked
vector contributing zero. Admissible σ̂ are the programme's **on-surface paired-delta**
measurements: **8.61e-4** (B7, `out/B7_K5_RESULT.json`, 95% CI [5.16e-4, 2.47e-3]) and
2.70e-4 (CAL). The banked `σ = 2.19e-4` is **inadmissible** (wrong rung, wrong surface,
single-arm; re-measured 3.9× optimistic on this surface). `FLOOR_F = 1.78e-3` inherits from
it and is suspended. 0.002052 is retired and unit-test-enforced never to be hardcoded.

Conservative σ̂ = 8.61e-4 in `mde.py:100` at **k=1**, SE_Δ ≈ 7.2e-5 → **MDE(k=1) ≈ 2.42e-3**.

**Trigger: Δ ≥ +0.005** (≈2.1× MDE). Power: ≥80% only for true Δ ≳ **+0.0057**; P(read ≥
+0.005) is 1% at Δ=+0.003, 12% at +0.004, 50% at +0.005, 88% at +0.006. **This screen will
miss moderate effects** — the accepted cost of one fit.

| Δ vs 0.791310 | verdict |
|---|---|
| < +0.0024 | null — below MDE(k=1) |
| +0.0024 to +0.005 | detectable but sub-trigger — reported, does not escalate |
| ≥ +0.005 | escalate to multi-seed confirmation |
| ≥ +0.02 | **HALT** — no interpretation, mandatory leakage audit first |

**The CI is NOT a gate condition.** `STAGE2_INFERENCE_RULE_FINAL_2026_08_15.md:227`: *"The
bootstrap CI excludes zero even at k=5. It conditions on the fitted seeds and cannot see
refit variability. Any rule keyed to 'CI excludes zero' would have declared this a positive
finding."* At k=1 that hazard is maximal. The CI is reported as a statement about
vehicle-level stability only.

Δ computed with `bootstrap_from_paired_parquet` (`ablation_tables.py:321`; 1000 reps, seed
20260812), one `y`, two prediction vectors, after explicit row/label identity assertion.
Bootstrap covers the **AUROC delta only**; Brier, AUPRC and top-k are point values.

## §6 Reported quantities

B3 AUROC · Δ vs benchmark + paired CI · AUPRC · AUPRC ÷ prevalence · Brier · logistic
recalibration intercept/slope (IRLS — **ECE does not exist in this programme** and
`out/calib/` is a misnomer holding sampler thresholds) · top-k capture and lift at
k ∈ {5,10,20}% · best iteration · runtime.

Both arms are compared at a **fixed iteration budget**: the benchmark stopped at iteration
598 of 600, near its cap, so neither arm is at its own optimum.

**Stage A′ transfer (free):** the B3-trained predictions evaluated, without retraining,
against B3, M1, T0 and S1. A pattern of `B3↑↑ M1↑↑ T0~ S1~` would be *consistent with* a
shared deterioration state — **interpretation, not proof of a latent variable.**

**Stage B:** only after B3 — identical single fit on `y_m1`, benchmark 0.783293, same rule.
B3 remains primary. A failed primary is never rescued by a secondary.

## §7 Freeze evidence

sha256 of this prereg, the collection/relabel/analysis scripts, the three configs, the two
label tables and the relabelled frames, plus proof that zero Stage-2 fit artifacts existed
at freeze time, are recorded in `PREREG_SEVERITY_STAGE2_2026_08_15.sha256`.
