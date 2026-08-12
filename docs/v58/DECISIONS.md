# v58 program — pinned decisions

Decision record for the full-depth defect-data integration and retrain
(approved 2026-08-10). Each entry states the decision, the rationale, and the
tripwire that invalidates it. Scope and sequencing live in the approved plan;
this file is the durable in-repo record.

## D1. History window: `WINDOW_START_V58 = 2005-01-01`

Full-depth. Matches the start of digital MOT records and therefore what the
DVSA MOT History API serves, so the training and serving observation windows
are structurally identical. The window-cap function remains in BOTH paths
(trainer matrix build and serving feature engineering) as a guard, even though
it is a no-op for API histories — the contract's `history_window` rule stays
literally true.

*Tripwire:* Phase-1 day-1 `check_vehicle_continuity` — if the current bulk
publication does not provide one consistent `vehicle_id` space across
2005→present, this window is renegotiated before any further work.

## D2. Keep the 35 `*_observed` renames and the 5 coverage features

Contract continuity with the v57 decision table. Coverage-feature semantics
are re-documented for the 2005 boundary: `has_left_truncated_history` means
"MOT-liable before 2005" (pre-2002 registrations); `window_days_available`
becomes near-constant and is expected to carry ~zero model weight — kept for
contract shape stability, documented, not fought.

## D3. Aggregate comparison window: latest 5 full calendar years

The public comparison denominator (prod_data_clean.csv.gz / mot_risk) is
rebuilt from cycle-first class-4 tests in the most recent five complete
calendar years — NOT 2005+. Full depth exists for per-vehicle HISTORY
features; mixing 2005-era failure regimes into the comparison rate would
misstate current fleet risk. Pinned as `DATASET_COVERAGE_START/END` in
`report_contract.py` and stated factually in public copy, closing the
"artifact revision date is not a coverage claim" hole.

## D4. New enum value `prediction_source = "model_v58"`

Honest provenance over zero-churn reuse of `model_v55`. Expand/contract:
the value ships end-to-end (backend enum + validators, OpenAPI snapshot,
`types.ts`, SPA runtime validator, exhaustiveness tests) at least one release
BEFORE anything can emit it — cached SPAs hard-reject unknown sources.
`model_v55` remains a readable value permanently (90-day TTL of persisted
payloads replays through `ReportResponse.model_validate`).

## D5. Veterans-only model scope retained

The serving gate (DVSA source AND ≥1 recorded MOT test) already means every
predicted vehicle has history; zero-history vehicles fall back to the
comparison assessment. The three-cohort ensemble described in CLAUDE.md was
never in this repo's code and stays out of scope; CLAUDE.md is corrected at
v55 retirement (P7).

## D6. Categorical features: 10, matching serving emission

Settles the trainer-10 vs v57-test-8 discrepancy (`test_month`,
`day_of_week`). v58 trains THROUGH the serving feature engineering, so
serving's dtypes are ground truth by construction.

## D7. Target population: DVSA initial-test semantics (REVISED 2026-08-12)

**Target population.** A row is a prediction event iff DVSA recorded it as an
initial test with a result: `test_type = 'NT'` and `test_result ∈ {P, F, PRS}`.
Retests (`RT`, `PL`, `PV`), appeals (`ES`, `EI`) and non-result outcomes
(`ABA`, `ABR`, `ABRVE`, `R`) are excluded — the population DVSA itself uses for
published statistics (MOT testing data user guide v5.1, "Comparison with DVSA
published statistics"). Implemented in `pipeline/lake/target_population.py`;
unknown vocabulary **fails closed**.

**Target label.** Unchanged: `outcome == 'FAIL'`. PRS remains the pass it was
recorded as. This is DVSA's *final* failure basis. The previous wording of this
decision, and `build_aggregates.py`, called it an "initial-failure basis" — that
was wrong: DVSA's initial basis counts FAIL+PRS. See open decision D12.

**Cycles.** Retired as the population authority. They may remain relevant to
longitudinal/history features, but no cycle-derived value is required to admit a
row as a training example, and none may be trusted until the chronology defect
(#18) is repaired. Legacy reproduction is preserved: passing `cycles_relation`
to `build_segment_counts` restores the retired population exactly.

*Tripwire (replaces the 26.9139638817903% reconciliation):* per-financial-year
Class 3 & 4 initial and final failure rates must reconcile to DVSA table MOT-01
within 0.25% on volume and 0.10pp on rates, with median |Δ| ≤ 0.03pp across
gated years. The gate **fails closed** if the comparator is absent.
Implementation: `pipeline/aggregates/published_stats_gate.py`.

### Why the earlier ruling was superseded

The 2026-08-12 07:25 ruling made D7 reconciliation a hard pre-training gate.
It was superseded the same day by measurement:

1. **Cycles never defined the label**, only which rows counted. Every historical
   trainer labelled the selected row from its own outcome
   (`build_v7_features.py:175`, `train_catboost_streaming.py:67`,
   `train_catboost_production_v55.py:1487`); no trainer has ever read
   `cycle_outcome`. So cycles were only ever an estimator of `test_type='NT'`.
2. **The DVSA definition reproduces published national statistics**: six
   complete financial years within ±0.05% volume and ±0.04pp on both rates;
   median |final Δpp| = 0.005. The entire residual is confined to the COVID
   MOT-exemption quarters (2019-20 Q4, 2020-21 Q1–Q2), which are reported, not
   gated. Cost: ~40s, zero temporary disk — versus the 100–160 GiB spill and
   three ENOSPC failures of the cycles build.
3. **`gap_days = 45` has no derivation.** No commit, doc or analysis derives it;
   no test asserts the boundary. The legacy pipeline that generated production
   data used **120** (`build_cycle_index_duckdb.py:18`), so the retired tripwire
   asked a 45-day rule to reproduce a number a 120-day rule produced.
   Measured on an unbiased 1/20 hash sample of 2019 Class 3&4, the 45-day rule
   deletes 15,228 genuine initial tests (1.22%), of which 8,185 sat 15–45 days
   after a failure — outside the statutory 10-working-day retest entitlement, so
   necessarily new full-fee tests — and admits 8,661 aborted/abandoned rows DVSA
   excludes.
4. **The cycle implementation has an unrepaired chronology defect** (#18):
   `test_id` is not chronological within a day, so 43,403/43,791 orphaned
   retests sorted *before* the failure that caused them, inflating the
   denominator 4.55% and depressing the rate 1.26pp.
5. **The 26.9139638817903% artifact cannot adjudicate.** Its window, counting
   basis and vehicle classes were never recorded (`HANDOVER_LOCAL_AGENT.md:135-144`),
   its generator was never committed, and the retired gate returned PASS when no
   expectation was supplied. It is retained as a historical diagnostic in D11 —
   not deleted, and not an oracle.

## D11. The 26.9139638817903% artifact: historical diagnostic only

Retained, with its limitations recorded, and explicitly **not** ground truth for
the target definition:

- source coverage window never recorded;
- counting basis (all tests / cycle-first / cycle outcome) never recorded;
- vehicle classes never established (class 4 was assumed, not shown);
- legacy production appears to have used 120-day cycles, the proposed D7 used
  45-day cycles;
- it is below every published Class 3 & 4 *initial* failure rate and sits
  between the 2016 (27.16%) and 2017 (26.36%) *final* rates, so it is a
  final-basis number from a window centred ~2016–17.

Therefore reproducing it, or failing to, cannot determine the correct target
population. Do not delete it and do not conceal the discrepancy.

## D12. OPEN: should AutoSafe predict FAIL, or FAIL+PRS?

Unresolved, and now the load-bearing scientific gate. `outcome == 'FAIL'`
(DVSA final basis) is preserved for this change so historical AUCs stay
comparable. Adopting DVSA's *initial* basis (FAIL+PRS) would flip ~7.22% of 2019
labels and require re-running prior experiments. Evidence required before
deciding: DVSA initial-vs-final semantics; PRS prevalence by year and class;
exact label-flip rate per modelling era; which user question each target
answers; class-balance effect; comparability with historical results; whether
PRS is consistently observable across the pre/post-2018 regimes. Do not resolve
it from naming conventions or legacy behaviour.

## D8. JSON-only v58 bundle

`models/v58/` contains only JSON artifacts plus `model.cbm` — no pickles.
Extends the pickle-free calibrator remediation to every fitted lookup
(`feature_artifacts/*.json`), keeps the `.railwayignore` exception list short
and provable, and makes every artifact hash-verifiable at load time.

## D9. Shadow before promotion; both bundles in the image

v58 serves shadow-only (log sink `shadow_predictions`, never the wire) for a
2–4 week soak; promotion is an env flip (`MODEL_VERSION=v58`), rollback is
the reverse flip — instant, no redeploy, because both bundles ship in the
image. `PREDICTIONS_ENABLED=false` remains the global kill switch.

## D10. vocab_shim deletion deferred to v55 retirement (P7)

Rollback-to-v55 requires the v55 slot fully functional, and v55 needs the
shim. The v58 path never imports it; it is deleted only after the
post-promotion soak, together with the `tests/test_feature_path.py`
source-string pin rewrite.
