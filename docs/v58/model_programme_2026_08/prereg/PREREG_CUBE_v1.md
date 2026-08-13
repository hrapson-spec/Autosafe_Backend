# PREREG_CUBE_v1 — aggregate-parity feature cube

Programme: `docs/v58/model_programme_2026_08`. Authored 2026-08-13 under owner rulings of the same
date. Governs the C-family cube: specification, screening ladder, adoption.

Status on emission: **Gates 1, 2, 8 closed by this memo. Gates 3–7 open.** No outcome-driven cube
fit may run until all eight close (§6).

---

## 1. Contract amendment — column caps (GATE 1)

Amends `FACTORY_CONTRACT.md:99` (*"Cap: total new columns B1–B6 ≤ 150"*) and
`factory/blocks.py:221` `NEW_COLUMN_CAP = 150`, which are hereby superseded by two constants:

```
CANDIDATE_CAP        = 400   # temporary screening frames ONLY
ADOPTED_HISTORY_CAP  = 150   # B- and C-family columns entering the serving model
```

Binding conditions on the candidate pool:

1. Generated and tested **blockwise**.
2. Exists **only** in screening frames. It is never the default training matrix and never any
   serving matrix.
3. `CANDIDATE_CAP` counts **every actual model-input column**, including support and coverage
   columns. A support column fed to the learner is a model input.
4. **Shared support measures and denominators are emitted once**, not duplicated per feature. The
   manifest enforces this structurally: each cell carries a `denominator_ref` pointing at a shared
   support column. A cell that inlines its own copy of a shared denominator is inadmissible.

Adoption: cube winners fit inside `ADOPTED_HISTORY_CAP` by **replacing** weaker, redundant or
unusable B-features. `ADOPTED_HISTORY_CAP` does not rise without measured predictive value,
stability and production-cost evidence presented to the owner.

`legacy_241` remains reproducible for reconciliation. It is **not** the future serving contract.

---

## 2. Target ruling (GATE 2)

```
PRIMARY    y_initial = outcome IN ('FAIL','PRS')     factory/atoms.py:276
SECONDARY  y_final   = outcome = 'FAIL'              factory/atoms.py:275
```

A PRS vehicle failed as presented and required immediate rectification; scoring it negative labels
a vehicle containing an MOT defect as a pass, contrary to the product's purpose. `y_final` is
retained as legacy continuity, as a secondary outcome, and to separate initial failure from failure
remaining after permitted rectification.

**Binding consequence.** Every banked figure is a legacy-target result:

| Cell | Value | Status |
|---|---|---|
| `s3.lgbm.adopted.1m` | 0.714110 | LEGACY (`y_final`) |
| `s3.realmlp.adopted.1m` | 0.714121 | LEGACY |
| `s3.realmlp.ref.1m` | 0.709739 | LEGACY |
| `s3.cb_inc.adopted.250k` / `.ref.250k` | 0.710728 / 0.706868 | LEGACY |
| B1–B6 cumulative +0.003637; RealMLP adopted−ref +0.004382 | — | LEGACY |

**No `y_initial` AUROC is compared to any banked `y_final` AUROC, in any table, ever.** Baseline
anchors are re-banked on `y_initial` before any cube lift is assessed. Every historical result is
labelled `target=y_final (legacy)` at point of quotation.

Re-banking is the critical path: it gates all of Stage 5 and Stage 6.

---

## 3. Controls

| Control | Definition | Permitted use |
|---|---|---|
| `legacy_241` | exact historical matrix, byte-faithful, `y_final` | reconciliation with banked results ONLY |
| `safe_core_v1` | verified constants and exact duplicates removed; unsafe semantics repaired; mileage units resolved; item observability corrected; re-banked on `y_initial` | **every cube adoption decision** |

`safe_core_v1` may score below `legacy_241`. That is a reportable finding, not a failure, and is
published either way.

---

## 4. Statistical treatment (GATE 8, part 1)

### 4.1 Estimand

Feature lift is a **paired within-architecture contrast** on identical seed, split and evaluation
rows:

```
Δ(seed) = AUC(safe_core_v1 + candidate, seed) − AUC(safe_core_v1, seed)
```

Reported as mean Δ over seeds with the vehicle-clustered bootstrap CI. Absolute AUROC levels are
reported for context and are never the contrast.

### 4.2 Seed requirement — derived from paired-delta variance

Let σ_Δ be the standard deviation of Δ(seed) across seeds. Then

```
MDE(k) = 2.8016 · σ_Δ / √k          (two-sided α = 0.05, 80% power; house constant)
k_req  = ceil( (2.8016 · σ_Δ / δ)² )
```

**σ_Δ is NOT σ_AUC.** Paired arms share seed, split and eval rows, so
`Var(Δ) = Var(A) + Var(B) − 2·Cov(A,B)` and the covariance term is large. Seed counts derived from
absolute-AUROC dispersion, or from the cross-architecture tie requirement (≥19 seeds/arch,
`PREREG_STAGE3.md:110-111`), are **the wrong instrument for this estimand** and are not used here.

**Registered detection target: δ = 5.0e-4.** Justification: the existing B-ladder's individual
steps are +1.11e-3, +1.70e-3, +3.18e-4, +1.15e-4, +4.03e-4, −1.60e-5
(`out/tables/s2_pass2/stage2_decision_tables.md`). Block-scale effects in this programme occur
between ~1e-4 and ~1.7e-3, i.e. mostly **below** the Stage-2 floor of 1.78e-3 — which is precisely
why all six resolved as NEAR-MISS-CARRY or TIE. A cube screened at the old floor would be
structurally unable to resolve blocks of the size this programme actually produces. δ = 5.0e-4
makes the largest historical step resolvable with margin and the mid-range steps detectable.

`k_req` is fixed per architecture once σ_Δ is measured (§4.3), and is registered before any
candidate is screened.

### 4.3 Noise-floor instrument — width-matched planted null

σ_Δ is measured against a **planted-noise block of matched width and matched marginals**, not
against a re-run of the identical featureset:

```
Δ_null(seed) = AUC(safe_core_v1 + noise_block_w, seed) − AUC(safe_core_v1, seed)
```

This does double duty. It measures seed dispersion, and it controls the confound that adding *w*
columns to a frozen recipe changes effective capacity and regularisation independently of
information — `colsample_bytree`-style subsampling, fixed iteration counts, and (for
`HistGradientBoostingClassifier`) no column subsampling and `early_stopping=False`. A block is
therefore judged against **its own width-matched null**, which also makes blocks of different width
mutually comparable.

One null per width class present in the cube. Width classes registered with the manifest.

**LightGBM paired noise floor is established directly.** `AS_LGBM_ADOPTED_250K_S101` completed
rc=0 in **18 s** (`logs/queue_ledger.tsv`), so a 20-seed paired floor costs ≈ 6 minutes of serial
compute. Leaving LightGBM `NO-FLOOR / UNCHARACTERISED` (`PREREG_STAGE3.md:100-102`) is not
justified for the paired estimand and is superseded for cube work. The Stage-3 ruling stands
unchanged for *absolute* LightGBM claims.

### 4.4 Screening instrument and the neural carry-through

`cb_inc` is the primary 250k screening instrument — it is the only architecture with a tight
measured MDE at that rung (4.34e-04, `out/tables/s3_pass2/stage3_decision_tables.md:8,11`).

**It is not the sole gate.** At least one theoretically coherent full or neural-targeted package is
carried into RealMLP **even if its constituent CatBoost blocks are individually weak**. Rationale:
no feature block in this programme has ever been screened on both a tree ensemble and a neural
model, so tree-weakness is not evidence of neural-weakness; and the commission's near-miss
principle is already the empirically observed shape of the B-ladder.

### 4.5 Confirmation design

H1 remains **untouched final evaluation**. The 250k and 1M rungs are **nested**, so a 1M result is
not independent of the 250k screen that selected into it. A locked later development confirmation
period — or an equivalent chronological confirmation design — is defined and registered **before**
any outcome-driven selection begins. Selection performed before that lock is void.

---

## 5. Memory and materialisation plan (GATE 8, part 2)

Blocks are generated and persisted **on demand**. The full candidate pool is never materialised for
every architecture.

Registered per rung before it runs:

| Field | Requirement |
|---|---|
| dtype | declared in the manifest; build dtype and score dtype **asserted equal**, never assumed |
| frame width | declared column count, including support and coverage columns |
| storage | projected parquet bytes per frame |
| expected peak RSS | projected, and compared against the fail-fast threshold |
| representation | sparse or dense, declared per block |
| fail-fast threshold | **peak RSS > 4.5 GB aborts the job** |

Projections at float32:

| Configuration | Width | Rows | Matrix |
|---|---|---|---|
| `safe_core_v1` + one block | ~260 | 250,000 | ~0.26 GB |
| `safe_core_v1` + one block | ~260 | 1,000,000 | ~1.04 GB |
| `safe_core_v1` + full candidate pool | ~580 | 1,000,000 | ~2.32 GB |

The last row is why the pool is never materialised per architecture: at 8 GB total with a live
queue it approaches the region where this box has already failed three times today (FTT_D
swap-thrash, TabICL SIGKILL rc=137, TabDPT projection guard). The 4.5 GB threshold sits below the
observed 4.77 GB peak-RSS-plus-swap cliff.

Screening is blockwise on CatBoost/LightGBM at 250k. **Only promoted features reach the 1M RealMLP
work.**

---

## 6. Gates — outcome-driven cube fitting is HELD until all eight close

| # | Gate | Owner | Status |
|---|---|---|---|
| 1 | Cap amendment committed | lead | **CLOSED — §1** |
| 2 | `y_initial` established as primary label | lead | **CLOSED — §2** |
| 3 | Actual consumed feature matrix reconciled | R6 | open |
| 4 | Item observability repaired | R3 | open |
| 5 | `safe_core_v1` built and banked | R3 + R6 | open |
| 6 | Dual-publisher 2023 reconciliation complete | R5 | open |
| 7 | Blocking red-team controls pass | R4 | open |
| 8 | Paired-MDE and memory plans registered | lead | **CLOSED — §4, §5** |

Proceeding while gates are open: specification, prospective semantic definitions, fixtures, parser
reconciliation, substrate repair.
Held: every outcome-driven fit and every selection decision.

`k_req` per architecture is the one registered quantity in §4 not yet numeric; it is fixed by the
§4.3 measurement and appended here before the first candidate screen. That measurement is
instrument characterisation, not a selection decision, and is therefore permitted while Gates 3–7
remain open — but it runs only on `safe_core_v1`, so it is sequenced after Gate 5.

---

## 7. Falsification requirement (from the owner's coverage-era ruling)

Replaces any blanket era-invariance criterion:

1. Physical-history features remain invariant under translation **within the same coverage regime**.
2. Only **explicitly declared** calendar, regime or coverage features change **across** regime
   boundaries.
3. Matched source records produce the same canonical semantics after ingestion.
4. Coverage features do not win **principally** by identifying publisher or evaluation period.

Clause 2 is what makes this testable: era-variance is permitted where declared and forbidden where
not. `C6` splits into `C6a` (hierarchy and code granularity) and `C6b` (coverage, observability,
censoring); source-only, era-only and coverage-feature ablations run before either is accepted.

**Note on prior framing.** R4's fixture X1 (16 of 137 columns varying under rigid time-translation)
demonstrates that an era channel exists. It does **not** establish that the channel accounts for
any share of the +3.608e-3 B-ladder gain. The 1.78e-3 Stage-2 floor is an uncertainty threshold,
not an estimate of era-channel value. The contribution is quantified by ablation or it is not
quantified at all.
