# Training-frame factory — locked artifact contract (v2, owner-authored 2026-08-12)

## v2 AMENDMENT (owner, 2026-08-12 ~17:45 — first-principles restructure, Henri-approved)

1. **B0 is REMOVED from this factory's remit.** The 104 serving features come from
   the proven packets→feature_engineering_v55-module path (parity-gated instrument,
   banked). The factory emits a PACKETS view (per event: prior-test tuples + prior
   items in the module's expected shape) instead of computing B0 itself. B0-PC (the
   42 production-common features per out/serve_view_classes.json) is a COLUMN
   SUBSET selected downstream — no separate computation.
2. **Panel-first builds.** The factory's primary target is the existing 1/100 panel
   substrate (results shards + items_panel parquet paths injected as the input
   relations) — full-population builds are a LATER owner-run mode of the same code
   (no design change, just input paths + the eval-slice/confirmation modes).
3. Everything else in v1 stands: atoms, corrected severity axes, D13 discipline,
   B1–B6 blocks, salted-hash sampling/nesting, emission fences, the 10 falsifiers
   (falsifier 9's AST gate now also whitelists nothing in the packets emitter),
   fixtures-only testing, boundaries. Falsifiers referencing B0 columns apply to
   the packets view instead (as-of + D13 invariance of packet contents).

Consumer: A1 (factory & blocks builder). Deviations ONLY with an attached failing
test demonstrating why the contract is wrong (deviate-with-test). The factory is
CODE + FIXTURE-TESTED FALSIFIERS; it never runs against the real lake in A1's
hands — the owner executes real builds.

## Inputs (read-only relations, injected as duckdb-readable paths)

- results: hive parquet, 19 cols (test_id BIGINT, vehicle_id BIGINT, test_date DATE,
  test_class_id, test_type, outcome ∈ {PASS,FAIL,PRS,ABANDONED,ABORTED,ABORTED_VE,
  REFUSED}, test_mileage BIGINT, postcode_area, make, model, model_id, colour,
  fuel_type, cylinder_capacity INT, first_use_date DATE, age_source, age_at_test
  DOUBLE, taxonomy_era ∈ {pre_2018,post_2018}, schema_epoch) + test_year hive key.
  Years 2005–2025 (2005 partial; 2005–2014 may be Drive-parked — builds take an
  explicit year list and FAIL LOUD on missing years, never silently skip).
- items: hive parquet, 12 cols incl. rfr_id VARCHAR, rfr_type_code (case-sensitive),
  location_id, dangerous_mark, taxonomy_era, and DERIVED severity cols that are
  KNOWN-WRONG post-2018 (F-22) — the factory NEVER reads is_fail_item/is_advisory/
  is_dangerous/rfr_class; it derives severity itself (below).
- lookup: item_detail.csv/item_group.csv (2023-03 vintage = current; class-4 scoped
  map rfr_id → top-level section → 7 categories, DISTINCT-mapped, fan-out guarded).
- completed_ts sidecar (2024–2025 only): test_id, completed_at TIMESTAMP. OPTIONAL
  input; no factory feature may REQUIRE it (pre-2024 has none); it feeds only
  explicitly-labelled diagnostics.

## Severity/disposition derivation (F-22-corrected, the ONLY allowed rule)

- disposition: A=advisory, M=minor(post-2018 only), F=fail-bearing-unrectified,
  P=fail-bearing-rectified-at-station. Case-sensitive post-2018; pre-2018
  case-insensitive {F,P,A}.
- severity (post-2018 only): dangerous ⇔ trim(dangerous_mark)='D'; major ⇔
  disposition∈{F,P} AND NOT dangerous; minor ⇔ M. Pre-2018: severity =
  UNOBSERVABLE — emitted as status='pre2018_ungraded', never zero.
- fail-bearing set = {F,P} both eras. Counts must be emittable on BOTH bases
  (initial=F+P, final-unrectified=F) with explicit column names (_initial/_final).
- GATE: builds refuse to run unless out/p4_crosstab_certification.json exists with
  verdict PASS (owner produces it in Stage 0).

## Atoms

1. item_test_atom (grain test_id): per-section (26 canonical top-level names → 7
   categories + 'other') presence/count by disposition×severity axes; n_items;
   n_dangerous; positional summary (location_id nullable section-grain counts,
   research-only tagged); catalogue-miss count (rfr_id not in class-4 map).
2. vehicle_day_atom (grain vehicle_id × test_date): UNORDERED multiset —
   n_tests_day, n_initial_day (test_type='NT' AND outcome∈{PASS,FAIL,PRS}),
   has_pass/has_fail/has_prs/has_nonresult flags, n distinct outcomes,
   max_valid_mileage_day + n_valid_mileage_day + mileage_conflict flag (readings
   differing >1%), day-level union of item_test_atom aggregates, ambiguity flag
   (same-stratum FAIL + definitive pass per D13 cluster semantics — reuse
   pipeline/lake/cycles.py semantics, imported not reimplemented).
3. as_of_state (per vehicle, updated day-by-day): running aggregates the emit step
   reads BEFORE update (emit-before-update; priors = strictly earlier calendar
   days ONLY). No within-day ordering anywhere (D13): any use of test_id for
   ordering = contract violation; determinism via (vehicle_id, test_date) alone.

## Feature blocks (each column tagged block, deployability_class from
serve_view_classes.json, era_observability status)

- B0: the 104 serving features, serving-faithful per the REPAIRED vocabulary
  (D1 repair semantics), computed from as_of_state. Mileage-parity caveat column.
- B1: depth/density/censoring — n_prior_tests/initials/final_fails, first/last
  prior dates, history_years, observable_years (max(first_use, 2005-01-01)),
  opportunity-adjusted density, history-coverage grade, left-censor flags.
- B2: per-section day-grain presence/counts/recency/recurrence (consecutive
  observed test-days), persistence, distinct-section breadth.
- B3: severity/disposition — dangerous/major/minor counts+recency (post-2018,
  with n_days_fine_severity_observable denominator), PRS-rectification history
  (both eras), initial vs final fail-item counts.
- B4: trajectory — advisory→fail transition per section (advisory at day t, fail
  same section at any later day), recurrence-after-repair (fail section s at t,
  PASS day later, fail s again), burden deltas (n_items_day diffs across last 3
  observed days), burden×age, burden×mileage-band (unit-robust: band from
  last-trusted mileage only), deterioration slope (items/year over observed span).
- B5: same-day multiset features + ambiguity burden count + gap features
  (days since prior, annual-band flag, COVID-extension-straddle indicator
  2020-03-30..2020-08-01) + n prior AMBIGUOUS days.
- B6: positional (research-only): per-position-group prior counts (location_id →
  coarse groups via mdr_rfr_location lateral/vertical), section-grain.
- Cap: total new columns B1–B6 ≤ 150. Every column has a one-line definition in
  the emitted FEATURE_DICTIONARY.md.

## Emission

- events = NT+definitive rows (target_population.initial_test_sql semantics —
  import the module, never re-implement).
- labels: y_final = outcome='FAIL'; y_initial = outcome∈{FAIL,PRS}. Both always.
- nested samples: deterministic inclusion by salted hash
  h = hash(vehicle_id || 'mp2026s1') (salt literal fixed in code; NOT the bare
  duckdb hash(vehicle_id) family — measured 6.37× selection correlation); rungs
  by threshold so 250k ⊂ 500k ⊂ 1M ⊂ 2M within each window recipe; enrichment
  strata (≤25%) computed AS-OF the row's target date; inclusion_weight column.
- eval slices: 2024-selection and 2025H1-drift = deterministic 4M-row slices
  (same salted-hash rule, separate salt 'mp2026eval'); 2025-H2 confirmation slice
  = DEFINITION emitted (salt 'mp2026conf' + exact predicate) but NEVER built by
  the factory default path (owner-only flag --build-confirmation, refuses unless
  CONFIRMATION_PREREG sha present).
- window recipes parameterised: target date fences inclusive-exclusive, exact
  dates in BUILD_MANIFEST; training emission REFUSES any target ≥ 2024-01-01
  unless --eval-slice mode.
- outputs: parquet per (recipe, rung) + BUILD_MANIFEST.json (input year list +
  row counts + shas + salt literals + code sha + contract version).

## Falsifier suite (fixtures only; ALL must fail if the defect is introduced)

1. as-of violation: planting a future-dated item for a vehicle must NOT change
   any emitted feature for earlier targets (probe by mutation).
2. D13 permutation: shuffling same-day row order AND swapping test_id values
   within a day leaves every emitted feature bit-identical.
3. severity cross-tab: fixture with all era×code×mark combinations maps exactly
   per the derivation table; lowercase 'm'/unknown codes RAISE.
4. emit-before-update: a vehicle with two consecutive test days — day-2 features
   must reflect day-1 exactly and not day-2.
5. censoring: first_use 1998 vs 2010 fixtures produce correct observable_years
   and status fields (never zero-filled).
6. enrichment as-of: a vehicle becoming 'deep history' only in 2025 must NOT be
   enrichment-eligible for a 2022 target row.
7. nesting: 250k sample ⊆ 500k ⊆ 1M for a synthetic population.
8. leakage property test (honest denominator): n_prior mismatch checked only
   over rows with priors; tolerance 0.
9. no-bare-test_id AST gate over the factory package (ORDER BY/min/max on
   test_id outside determinism-comment-free whitelist = FAIL).
10. year-list fail-loud: requesting a build including 2013 when 2013 is absent
    raises before any output is written.

## Boundaries

Python 3.11 + duckdb 1.5.5 pinned; memory_limit/temp_directory/max_temp are
CONSTRUCTOR ARGS (owner sets at run time); no network; no git; code under
docs/v58/model_programme_2026_08/factory/ + tests under .../factory/tests/;
pytest green = your stopping point, then owner review + adversarial pass.
