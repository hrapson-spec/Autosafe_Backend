# PREREG — Severity/burden outcome study (Stage 1)

**Frozen 2026-08-15, before any result file exists.**
Commissioned brief: "Does AutoSafe discriminate more severe/high-burden MOT failures
substantially better than the broad failure outcome it currently predicts?"

Plan of record: `~/.claude/plans/engineer-brief-robust-floyd.md` (approved 2026-08-15).

---

## §1 Question and estimand

The NY comparison audit (`out/NY_COMPARISON_AUDIT_2026_08_15.md`) concluded the AUROC gap
vs Cai & Tsai 2026 is task difference, not a missing feature block. This study tests the
remaining structural explanation: that the NY outcome (`cri>=2 OR unc>0`) is a **more
extreme adverse state** than AutoSafe's broad failure target.

**No model is trained.** The frozen score vector is held fixed; only the label changes.
Therefore this study carries **zero retrain/seed noise in the score**, and the house
detectability floor `0.002052` — a seed-variance quantity — **does not apply**. The only
uncertainty is sampling over rows.

## §2 Substrate (pinned before measurement)

| | |
|---|---|
| Primary score | `out/fits/s2/preds/s2.D.cum.b0-6.seed{101,202}.parquet` |
| Cell | `s2.D.cum.b0-6`, 241 features, `label: y_final`, `surface: panel2024s` |
| Banked AUROC | seed101 `0.7133041237871912` · seed202 `0.713151476693443` · k=2 mean `0.7132278002403172` |
| Rows | 330,665 · 315,300 vehicles · 330,665 distinct `test_id` |
| Targets | `tgt_date` 2024-01-01 → 2024-12-31 (100% post-2018-05-20) |
| Train cutoff | `max_train_target_date` 2023-12-31 |
| Eval frame | `out/frames_eval/recipe=eval2024/rung=all/frame/part_*.parquet` |
| Items | `~/autosafe/autosafe_lake/items/test_year=2024/` |
| Results | `~/autosafe/autosafe_lake/results/test_year=2024/` |
| Catalogue | `~/autosafe_raw/lookup/item_detail.csv`, `item_group.csv` |

Robustness scores (secondary, same 330,665 rows): `b7.R0.seed{101..505}`, and the
4-architecture panel in `out/tables/hetero_2026_08/analysis_frame.parquet`
(`p_lgbm_*`, `p_realmlp_*`, `p_catboost_*`, `p_xgboost_*`).

⚠ `out/fits/s2/PREREG_SHA.json` records `frozen_at_queue_start: false`. The *predictions*
are frozen and reproduce; s2's own prereg pinning is not clean. Recorded, not concealed.

## §3 Severity semantics (rule of record)

Per `factory/severity.py:98-110`. Only the RAW lake columns `rfr_type_code` and
`dangerous_mark` are read. The derived columns `rfr_class`, `is_fail_item`, `is_advisory`,
`is_dangerous` are **known-wrong (F-22)** and are refused by gate G0.9.
`item_detail.rfr_deficiency_category` is a per-CODE catalogue attribute that overstates
dangerous ~7.5× against observation; it is used for **section mapping only**, never as
observed severity.

Counts per target test, post-2018 era (all targets qualify by construction):

```
n_major_or_dangerous = count(disp in ('F','P'))                                  -- F+P initial basis
n_dangerous          = count(disp in ('F','P') AND trim(dangerous_mark)='D')     -- FAIL-GATED
n_major              = n_major_or_dangerous - n_dangerous
n_minor              = count(disp='M')
n_advisory           = count(disp='A')
n_prs                = count(disp='P')
n_dangerous_advisory = count(disp='A' AND trim(dangerous_mark)='D")              -- reported only
```

**Deviation from `severity_expr`, recorded:** that expression tests `dangerous_mark`
before disposition, so a D-marked ADVISORY grades `dangerous` (9,868 such rows in 2023).
`n_dangerous` here is therefore **fail-gated**, and `n_major_or_dangerous` is the F+P
count, **not** `n_major + n_dangerous`. Using the ungated form would inflate S1 and the
primary T1_NY_LIKE with advisories.

## §4 Outcomes (fixed before any performance is inspected)

| Outcome | Definition |
|---|---|
| `T0_AUTOSAFE` | `y_final` = `outcome='FAIL'` (banked) |
| `T0_DVSA_INITIAL` | `y_initial` = `outcome IN ('FAIL','PRS')` (banked) |
| `B1` | `n_major_or_dangerous >= 1` |
| `B2` | `n_major_or_dangerous >= 2` |
| `B3` | `n_major_or_dangerous >= 3` |
| **`T1_NY_LIKE`** | `n_dangerous >= 1 OR n_major >= 2` |
| `S1` | `n_dangerous >= 1` |
| `M1` | `n_sections_with_md >= 2` |
| `ONLY_MINOR` | `n_minor >= 1 AND n_major_or_dangerous == 0` (exploratory control) |
| `ADVISORY_ONLY` | `n_advisory >= 1 AND n_major_or_dangerous == 0` (exploratory control) |

Rows with no item rows take all-zero counts and are correctly negative on every
item-derived outcome.

**Component outcomes (primary grain = DVSA section).** One binary label per DVSA section
present in the class-4 evaluation data: positive iff the target initial MOT contains
>=1 major-or-dangerous item in that section. Thin sections are **preserved and reported
with N, prevalence and wide CIs — never merged post hoc.** Mapping is
`rfr_id -> item_detail.test_item_set_section_id -> item_group.item_name`, class-scoped
(5,768 rfr_ids appear in multiple classes).

Secondary cross-check only: the same calculation over the 7-category
`factory_rfr_category` taxonomy. **Not the result of record**; it exists to connect to
prior AutoSafe cause-specific results.

## §5 Metrics

Computed via `factory/runners/metrics.py` on `as_stored(p)` (float32 round-trip; float64
moves AUROC beyond the 1e-6 reproduction tolerance).

Per outcome: N, positive N, prevalence, AUROC, 95% CI, Δ vs `T0_AUTOSAFE`, AUPRC,
AUPRC ÷ prevalence, share of `T0_DVSA_INITIAL` positives, share of `T0_AUTOSAFE` positives.

### Held-fixed-negative decomposition (mandatory)

Raising the burden threshold moves demoted mild failures into the negative class, so a
flat pooled gradient is consistent with both "no severity signal" and "signal offset by
negative-class contamination". Reported alongside every outcome:

```
N_clean    = { rows : n_major_or_dangerous == 0 }
A_clean(O) = AUROC(P_O vs N_clean)                  -- negative pool HELD FIXED
A_mild(O)  = AUROC(P_O vs (negatives_O \ N_clean))
identity   : AUROC(O) == (|Nc|*A_clean + |Nm|*A_mild) / (|Nc| + |Nm|)
```

`A_clean` is the quantity that answers "are severe failures more predictable?".

### Uncertainty

Vehicle-clustered bootstrap (315,300 vehicles), multinomial counts converted to row
weights, percentile CI. **`BOOTSTRAP_REPS = 1000`, `BOOTSTRAP_SEED = 20260812`** (house
constants, `scripts/analysis/ablation_tables.py:71-73`). **One shared resample per
replicate across all outcomes**, so paired deltas are correctly correlated. Ties use the
0.5 convention throughout.

The sibling `PREREG_NYCOHORT_2026_08_15.md` uses 2000/20260815; the two preregs are
independent and the constants are deliberately not harmonised after the fact.

## §6 Gate 0 — refuses to compute any outcome number until all pass

| # | Check | Bar |
|---|---|---|
| G0.1 | AUROC(`y_final`) recomputed from banked preds vs fit JSON | <= 1e-6 |
| G0.2 | `tgt_id` ∩ `train_ids` | == 0 |
| G0.3 | Unknown disposition among target items | == 0 (STOP) |
| G0.4 | Target items resolving to `post_2018` era | 100% |
| G0.5 | D-marked non-failure items on targets | report (justifies fail-gating) |
| G0.6 | Class-scoped catalogue miss | report share |
| G0.7 | Legacy `rfr_id < 10000` among target failure items | report share |
| G0.8 | Mixture identity reconstructs pooled AUROC | <= 1e-9 |
| G0.9 | Collection SQL references no known-wrong derived column | grep == 0 |
| G0.10 | §11 reconciliation 2x2 `y_initial` (results) x `B1` (items) | both off-diagonals investigated first |

G0.10 is non-vacuous only because the two sides come from **different tables**.

## §7 Primary estimands and decision rule

Primary: (a) the B1→B2→B3 gradient in **both** pooled AUROC and `A_clean`;
(b) `AUROC(T1_NY_LIKE)` vs `AUROC(T0_AUTOSAFE)` and vs `AUROC(T0_DVSA_INITIAL)`.
Component outcomes and the two minor/advisory controls are **exploratory**; CIs
unadjusted but labelled as such. A failed primary is never rescued by a secondary.

### Stage-2 gate — fixed now, before numbers

> **YES** iff `A_clean` rises monotonically across B1→B2→B3 **AND**
> `AUROC(T1_NY_LIKE) − AUROC(T0_AUTOSAFE) >= +0.02` with the paired bootstrap CI
> excluding zero. Otherwise **NO**.

If YES: exactly one CatBoost fit on `target = T1_NY_LIKE`, with feature substrate,
temporal logic, model family, hyperparameters and preprocessing all held fixed; one
standard seed; no tuning; no architecture search; no seed escalation.

Per the brief §19, a low `T1_NY_LIKE` AUROC is **not** evidence that the severe target is
intrinsically hard — the score was trained on `y_final`. `A_clean` separates those readings.

## §8 Freeze evidence

Analysis code is committed before outcomes exist:
`scripts/analysis/severity_collect.py` (collection; computes no comparisons) and
`scripts/analysis/severity_analyze.py` (metrics only). Their sha256 and the proof that
zero result files existed at freeze time are recorded in
`prereg/PREREG_SEVERITY_2026_08_15.sha256`.
