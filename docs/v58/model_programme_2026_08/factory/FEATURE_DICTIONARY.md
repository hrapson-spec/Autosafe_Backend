# FEATURE_DICTIONARY — training-frame factory (contract v2)

Every column the factory emits, one line each. Generated from the registry in
`factory/blocks.py`; `factory/tests/test_units.py::test_every_column_has_a_dictionary_line`
fails if a column is added without a line here.

**Counts.** meta 26 · B1 26 · B2 56 · B3 18 · B4 14 · B5 15 · B6 14 · B7D 35 · B7R 34 →
**212 candidate columns across B1, B2, B3, B4, B5, B6, B7D, B7R**, against the PHYSICAL candidate cap of 220 (8 spare).

**Adoption is a separate budget.** 157 of these are `serve_class=deployable`, against an UNCHANGED adopted cap of 150 — so 7 deployable candidates must be pruned before adoption. Research-only columns never consumed that budget, which is why opening B7 does not raise the cap.

*(137 → 143 on 2026-08-13: the six B2 item-observability columns required by
PREREG_CUBE_v2 §4. See `out/cube/CONTRACT_FIX_NOTES.md`.)*

## Reading conventions (these govern every line below)

1. **As-of.** Every feature is computed from `AsOfState` as it stands BEFORE the
   target day is folded in. "Prior" always means *strictly earlier calendar
   days*. Nothing reads the target day's own multiset, items or outcome — not
   even the target row's own mileage (`tgt_miles` is carried for audit only and
   is explicitly not a feature).
2. **Day grain (D13).** History is counted in test-DAYS, and a day is an
   unordered multiset. Where a serving-side concept would need "the previous
   test record", the factory uses "the most recent prior test-DAY" and its
   cluster outcome (`pipeline.lake.cycles` semantics, imported not
   re-implemented). Nothing depends on within-day order or on `test_id`.
3. **Record counts are still emitted** where they are order-free set functions
   (`b1_n_prior_tests`, `b1_n_prior_initials`, item counts). A count of a
   multiset carries no ordering claim.
4. **NULL is not zero.** An unobservable quantity is NULL and carries an
   explicit status column: pre-2018 severity (`b3_severity_observability_status`),
   an absent location map (`b6_location_map_status`), missing `first_use_date`
   (`b1_first_use_missing_flag`, `b1_observable_years_status`). Zero would
   assert "observed, and none" — which is exactly the DQ-01 fabricated-zeros
   defect this lake repaired.
5. **Severity is F-22-corrected and derived in-package.** The lake's stored
   `is_fail_item` / `is_advisory` / `is_dangerous` / `rfr_class` columns are
   never read. Disposition: `A` advisory, `M` minor (post-2018 only, NON-fail),
   `F` fail-bearing unrectified, `P` fail-bearing rectified at station;
   case-sensitive post-2018, case-insensitive pre-2018. Severity (post-2018
   only): dangerous ⇔ `dangerous_mark='D'`; major ⇔ disposition ∈ {F,P} and not
   dangerous; minor ⇔ `M`. Anything else RAISES at the preflight gate.
6. **Both fail bases are always available.** `_initial` = F+P (condition as
   presented), `_final` = F only (unrectified). The B2/B4 per-category ladders
   use the basis named by `BuildConfig.fail_basis` (default `final` = the
   repaired serving vocabulary), recorded in BUILD_MANIFEST — see open question
   Q1.
7. **Category grain, not 26-section grain.** The contract's atom resolves the 26
   canonical top-level sections; the emitted B2/B4 columns aggregate them to the
   7 canonical categories + `other` so the block fits the 150-column cap. The
   full 26-section resolution is present in the atom and in the packets defect
   payload (`sect`), so a section-grain block can be added later without a
   re-scan of semantics. `other` = mapped to a deliberately out-of-scope section
   (exhaust, seat belts, …); a catalogue MISS (rfr_id absent from the class-4
   map) is counted separately in `b2_n_catalogue_miss_items` and never folded in.
8. **Target classes and history classes are SEPARATE knobs** (owner ruling
   2026-08-12). `target_classes` defaults to `('3','4')` — the D7 population
   rule — and applies to EVENTS only. `history_classes` defaults to `None` =
   **UNFILTERED**: a vehicle's class-3/5/7 prior tests are still its history and
   count in B1 depth, B4 mileage/burden and the packets view. Items on those
   priors keep their `rfr_id`; the class-4 catalogue resolves what it can, and
   what it cannot is counted in `b2_n_catalogue_miss_items` — rows are never
   dropped. Pinned by
   `test_falsifiers.py::test_f11_class3_priors_count_as_history_for_a_class4_target`.
9. **Trailing depth caps are emitted alongside the uncapped values** (`_cap2y`
   / `_cap5y`), computed in the SAME scan from bounded per-vehicle date lists —
   never by a second pass. A cap is a TRAILING window measured back from
   `tgt_date`; the strictly-earlier-day rule still applies inside it. Note
   `b1_history_years_capNy` is the span to the earliest **in-window** prior, NOT
   `min(b1_history_years, N)`: a vehicle with priors at 1y and 3y has a 2y-capped
   span of 1.0, not 2.0. Pinned by falsifier F12.
10. **Deployability class is attached at build time** from
   `out/serve_view_classes.json` (schema `factory/serve_view_schema.json`), never
   hardcoded here. When that file is absent every column is tagged
   `UNCLASSIFIED` and BUILD_MANIFEST records the gap. The block-level fallback
   may only classify a column DOWN: a column this dictionary marks
   `research_only_input` never inherits its block's deployable class (it resolves
   to `UNCLASSIFIED_RESEARCH_ONLY_INPUT`), because it consumes `test_type`-in-history
   or PRS visibility that serving does not have. An explicit per-feature entry in
   A2's file is a deliberate ruling and still wins; `preflight` records any such
   entry that contradicts `research_only_input` in
   `serve_view_research_only_conflicts`. The `era observability` column below is a
   different axis: what the DATA can support, not what serving can consume.

## Emitted columns

### Identity, labels and sampling (not counted against the B1-B6 cap) — 26 columns

| column | type | era observability | definition |
|---|---|---|---|
| `recipe` | VARCHAR | calendar_derived | Window-recipe name this row was emitted under. |
| `rung` | VARCHAR | calendar_derived | Nested-sample rung this shard belongs to. |
| `tgt_id` | BIGINT | all_eras | test_id of the prediction event (identifier; never ordered on). |
| `vehicle_id` | BIGINT | all_eras | Lake vehicle identifier; the history key and the sampling/bucketing key. |
| `tgt_date` | DATE | all_eras | Target test date. All priors are strictly earlier calendar days. |
| `tgt_year` | INTEGER | all_eras | Calendar year of tgt_date. |
| `tgt_outcome` | VARCHAR | all_eras | Canonical lake outcome of the target row (PASS/FAIL/PRS). |
| `y_final` | BOOLEAN | all_eras | Label, final basis: outcome = 'FAIL' (the preserved AutoSafe label). |
| `y_initial` | BOOLEAN | all_eras | Label, initial basis: outcome IN ('FAIL','PRS') (DVSA initial-failure basis, D12). |
| `tgt_test_class_id` | VARCHAR | all_eras | DVSA test class of the target row. |
| `tgt_test_type` | VARCHAR | all_eras | DVSA test type of the target row (always 'NT' by population rule). |
| `tgt_miles` | BIGINT | all_eras | Odometer reading recorded ON the target test. NOT a feature: unavailable at serving time; carried for audit/mileage-parity work only. |
| `tgt_make` | VARCHAR | all_eras | Vehicle make as recorded on the target row. |
| `tgt_model` | VARCHAR | all_eras | Vehicle model as recorded on the target row. |
| `tgt_model_id` | VARCHAR | all_eras | Lake 'MAKE MODEL' key (normalize.build_model_id). |
| `tgt_fuel` | VARCHAR | all_eras | Fuel type as recorded on the target row. |
| `tgt_colour` | VARCHAR | all_eras | Colour as recorded on the target row. |
| `tgt_cc` | INTEGER | all_eras | Cylinder capacity; NULL is informative missingness (EV growth). |
| `tgt_fud` | DATE | all_eras | first_use_date, ingest-sanitised (pre-1900 and post-test values nulled). |
| `tgt_pc` | VARCHAR | research_only_input | postcode_area of the testing station. Research-only input (not API-observable). |
| `tgt_age_at_test` | DOUBLE | all_eras | Lake age_at_test on the target row (years). |
| `tgt_taxonomy_era` | VARCHAR | all_eras | Taxonomy era of the target date: pre_2018 / post_2018. |
| `sample_u` | DOUBLE | all_eras | Salted unit hash u = hash(vehicle_id || 'mp2026s1') / 2^64 -- the sample-membership coordinate. |
| `sample_bucket` | INTEGER | all_eras | Memory-management bucket (separate salt 'mp2026bucket'); carries no sample meaning. |
| `enrichment_stratum` | VARCHAR | all_eras | As-of enrichment stratum at tgt_date: dangerous_prior / recent_fail / deep_history / none. |
| `inclusion_weight` | DOUBLE | all_eras | Horvitz-Thompson weight base/p(selected): 1.0 for base rows, <1 for enrichment-selected rows. |

### B1 — depth / density / censoring — 26 columns

| column | type | era observability | definition |
|---|---|---|---|
| `b1_n_prior_test_days` | BIGINT | all_eras | Prior test-DAYS (strictly earlier calendar days with >=1 test). The D13-safe depth measure. |
| `b1_n_prior_tests` | BIGINT | all_eras | Prior test RECORDS, all outcomes (retests included, undifferentiated). |
| `b1_n_prior_initials` | BIGINT | research_only_input | Prior DVSA initial tests (test_type='NT' and a recorded result). Consumes test_type-in-history: research-only input. |
| `b1_n_prior_final_fails` | BIGINT | research_only_input | Prior initial tests with outcome FAIL (final basis). |
| `b1_n_prior_initial_fails` | BIGINT | research_only_input | Prior initial tests with outcome FAIL or PRS (initial basis). |
| `b1_first_prior_date` | DATE | all_eras | Earliest observed prior test-day; NULL when there are no priors. |
| `b1_last_prior_date` | DATE | all_eras | Most recent prior test-day; NULL when there are no priors. |
| `b1_history_years` | DOUBLE | all_eras | (tgt_date - b1_first_prior_date) / 365.25; NULL without priors. Observed span, not vehicle age. |
| `b1_observable_years` | DOUBLE | all_eras | (tgt_date - max(first_use_date, 2005-01-01)) / 365.25: the window in which history COULD have been recorded. |
| `b1_observable_years_status` | VARCHAR | all_eras | How b1_observable_years was derived: observed / left_censored_2005 / first_use_missing. |
| `b1_history_coverage_grade` | VARCHAR | all_eras | none / left_censored / partial / full -- coverage of the observable window by recorded history. |
| `b1_left_censor_flag` | BOOLEAN | all_eras | TRUE when first_use_date precedes the 2005 digital-records floor (history is truncated by the publication, not by the vehicle). |
| `b1_first_use_missing_flag` | BOOLEAN | all_eras | TRUE when first_use_date is NULL after ingest sanitisation. |
| `b1_age_at_target_years` | DOUBLE | all_eras | (tgt_date - first_use_date) / 365.25; NULL when first_use_date is missing (never zero-filled). |
| `b1_density_per_observable_year` | DOUBLE | all_eras | b1_n_prior_test_days / b1_observable_years; NULL when the window is non-positive. |
| `b1_opportunity_adjusted_density` | DOUBLE | all_eras | b1_n_prior_test_days / testable years, where testable years excludes the pre-first-MOT 3 years and the 2020 COVID extension overlap. |
| `b1_n_prior_years_observed` | BIGINT | all_eras | Distinct calendar years carrying >=1 prior test-day. |
| `b1_max_gap_days` | BIGINT | all_eras | Longest gap in days between consecutive prior test-days; NULL with <2 priors. |
| `b1_mean_gap_days` | DOUBLE | all_eras | Mean gap in days between consecutive prior test-days; NULL with <2 priors. |
| `b1_n_prior_nonresult_days` | BIGINT | all_eras | Prior test-days carrying no definitive outcome at all (abandoned/aborted/refused only). Distinct from b5_n_prior_nondefinitive_days, which is a CLUSTER-OUTCOME property. |
| `b1_n_prior_test_days_cap2y` | BIGINT | all_eras | b1_n_prior_test_days restricted to the trailing 2-year window before tgt_date (strictly-earlier-day rule still applies inside it). |
| `b1_n_prior_initials_cap2y` | BIGINT | research_only_input | b1_n_prior_initials restricted to the trailing 2-year window before tgt_date. |
| `b1_history_years_cap2y` | DOUBLE | all_eras | Observed span INSIDE the trailing 2-year window: (tgt_date - earliest in-window prior test-day) / 365.25; NULL when no prior falls in the window. NOT min(b1_history_years, 2). |
| `b1_n_prior_test_days_cap5y` | BIGINT | all_eras | b1_n_prior_test_days restricted to the trailing 5-year window before tgt_date (strictly-earlier-day rule still applies inside it). |
| `b1_n_prior_initials_cap5y` | BIGINT | research_only_input | b1_n_prior_initials restricted to the trailing 5-year window before tgt_date. |
| `b1_history_years_cap5y` | DOUBLE | all_eras | Observed span INSIDE the trailing 5-year window: (tgt_date - earliest in-window prior test-day) / 365.25; NULL when no prior falls in the window. NOT min(b1_history_years, 5). |

### B2 — per-section (category-grain) defect history — 56 columns

| column | type | era observability | definition |
|---|---|---|---|
| `b2_brakes_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'brakes' (any disposition). |
| `b2_brakes_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'brakes' item; NULL when never observed. |
| `b2_brakes_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'brakes' item (recurrence). |
| `b2_brakes_persistence` | DOUBLE | all_eras | b2_brakes_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'brakes'; NULL without priors. |
| `b2_suspension_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'suspension' (any disposition). |
| `b2_suspension_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'suspension' item; NULL when never observed. |
| `b2_suspension_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'suspension' item (recurrence). |
| `b2_suspension_persistence` | DOUBLE | all_eras | b2_suspension_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'suspension'; NULL without priors. |
| `b2_tyres_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'tyres' (any disposition). |
| `b2_tyres_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'tyres' item; NULL when never observed. |
| `b2_tyres_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'tyres' item (recurrence). |
| `b2_tyres_persistence` | DOUBLE | all_eras | b2_tyres_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'tyres'; NULL without priors. |
| `b2_steering_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'steering' (any disposition). |
| `b2_steering_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'steering' item; NULL when never observed. |
| `b2_steering_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'steering' item (recurrence). |
| `b2_steering_persistence` | DOUBLE | all_eras | b2_steering_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'steering'; NULL without priors. |
| `b2_visibility_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'visibility' (any disposition). |
| `b2_visibility_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'visibility' item; NULL when never observed. |
| `b2_visibility_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'visibility' item (recurrence). |
| `b2_visibility_persistence` | DOUBLE | all_eras | b2_visibility_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'visibility'; NULL without priors. |
| `b2_lamps_reflectors_and_electrical_equipment_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'lamps reflectors and electrical equipment' (any disposition). |
| `b2_lamps_reflectors_and_electrical_equipment_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'lamps reflectors and electrical equipment' item; NULL when never observed. |
| `b2_lamps_reflectors_and_electrical_equipment_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'lamps reflectors and electrical equipment' item (recurrence). |
| `b2_lamps_reflectors_and_electrical_equipment_persistence` | DOUBLE | all_eras | b2_lamps_reflectors_and_electrical_equipment_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'lamps reflectors and electrical equipment'; NULL without priors. |
| `b2_body_chassis_structure_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'body chassis structure' (any disposition). |
| `b2_body_chassis_structure_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'body chassis structure' item; NULL when never observed. |
| `b2_body_chassis_structure_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'body chassis structure' item (recurrence). |
| `b2_body_chassis_structure_persistence` | DOUBLE | all_eras | b2_body_chassis_structure_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'body chassis structure'; NULL without priors. |
| `b2_other_n_days` | BIGINT | all_eras | Prior test-days carrying >=1 defect item in section-category 'other' (any disposition). |
| `b2_other_days_since` | BIGINT | all_eras | Days since the most recent prior test-day with a 'other' item; NULL when never observed. |
| `b2_other_max_run` | BIGINT | all_eras | Longest run of CONSECUTIVE observed prior test-days carrying a 'other' item (recurrence). |
| `b2_other_persistence` | DOUBLE | all_eras | b2_other_n_days / b1_n_prior_test_days -- share of observed test-days carrying 'other'; NULL without priors. |
| `b2_breadth_categories` | BIGINT | all_eras | Distinct section-categories ever seen on an ITEM-OBSERVABLE prior test-day (breadth of defect history); NULL when no prior day is item-observable. |
| `b2_last_day_n_categories` | BIGINT | all_eras | Distinct section-categories present on the most recent prior test-day; NULL without priors AND when that day's defect detail is unobservable (unknown, never 0). |
| `b2_n_items_total` | BIGINT | all_eras | Total prior defect items across ITEM-OBSERVABLE prior test-days (all dispositions); NULL when none is observable. |
| `b2_n_catalogue_miss_items` | BIGINT | all_eras | Prior item rows whose rfr_id is absent from the class-4 catalogue (counted, never folded into 'other'); NULL when no prior day is item-observable. |
| `b2_brakes_n_days_cap2y` | BIGINT | all_eras | b2_brakes_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_suspension_n_days_cap2y` | BIGINT | all_eras | b2_suspension_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_tyres_n_days_cap2y` | BIGINT | all_eras | b2_tyres_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_steering_n_days_cap2y` | BIGINT | all_eras | b2_steering_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_visibility_n_days_cap2y` | BIGINT | all_eras | b2_visibility_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_lamps_reflectors_and_electrical_equipment_n_days_cap2y` | BIGINT | all_eras | b2_lamps_reflectors_and_electrical_equipment_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_body_chassis_structure_n_days_cap2y` | BIGINT | all_eras | b2_body_chassis_structure_n_days restricted to the trailing 2-year window before tgt_date. |
| `b2_brakes_n_days_cap5y` | BIGINT | all_eras | b2_brakes_n_days restricted to the trailing 5-year window before tgt_date. |
| `b2_suspension_n_days_cap5y` | BIGINT | all_eras | b2_suspension_n_days restricted to the trailing 5-year window before tgt_date. |
| `b2_tyres_n_days_cap5y` | BIGINT | all_eras | b2_tyres_n_days restricted to the trailing 5-year window before tgt_date. |
| `b2_steering_n_days_cap5y` | BIGINT | all_eras | b2_steering_n_days restricted to the trailing 5-year window before tgt_date. |
| `b2_visibility_n_days_cap5y` | BIGINT | all_eras | b2_visibility_n_days restricted to the trailing 5-year window before tgt_date. |
| `b2_lamps_reflectors_and_electrical_equipment_n_days_cap5y` | BIGINT | all_eras | b2_lamps_reflectors_and_electrical_equipment_n_days restricted to the trailing 5-year window before tgt_date. |
| `b2_body_chassis_structure_n_days_cap5y` | BIGINT | all_eras | b2_body_chassis_structure_n_days restricted to the trailing 5-year window before tgt_date. |

#### B2 item-observability index — 6 columns (PREREG_CUBE_v2 §4, added 2026-08-13)

Every item-derived column in B2/B3/B4 is a sum over **item-observable prior
days only**. These five carry the denominator and the reason, so a NULL is
always interpretable and a 0 is always falsifiable. See `out/cube/CONTRACT_FIX_NOTES.md`.

| column | type | era observability | definition |
|---|---|---|---|
| `b2_item_observability_status` | VARCHAR | all_eras | no_priors / none / partial / full -- item-observability across this vehicle's prior test-days. 'none' means EVERY item-derived column in B2/B3/B4 is NULL because the defect detail was unavailable, NOT because the vehicle was clean; 'no_priors' means there was nothing to observe and the zeros are certain. |
| `b2_n_prior_days_items_observed` | BIGINT | all_eras | Prior test-days carrying >=1 test whose defect detail is observable -- the honest denominator for every b2_*_persistence and item count. |
| `b2_n_prior_days_items_unobserved` | BIGINT | all_eras | Prior test-days carrying >=1 test whose defect detail is NOT observable. Overlaps b2_n_prior_days_items_observed on partially-dark days (both are 'days with >=1 such test'). |
| `b2_n_prior_days_items_zero_defects` | BIGINT | all_eras | Item-observable prior test-days that carried NO defect items -- the honest-zero population (49.9-62.1% of passes, measured). Its complement within b2_n_prior_days_items_observed is the days that did carry items. |
| `b2_n_prior_days_items_unavailable` | BIGINT | all_eras | Prior test-days wholly unobservable because the source/partition cell is declared structurally dark (publisher schema change). |
| `b2_n_prior_days_items_expected_missing` | BIGINT | all_eras | Prior test-days wholly unobservable although the evidence says items should exist (dark day inside a covered partition, or a fail-bearing test with zero items). Expectation only -- attribution is a per-cell field in the ledger/manifest, not a per-row claim. |

### B3 — severity / disposition axes (F-22-corrected) — 18 columns

| column | type | era observability | definition |
|---|---|---|---|
| `b3_n_days_fine_severity_observable` | BIGINT | post_2018_only | Prior test-days on/after 2018-05-20 -- the DENOMINATOR for every dangerous/major/minor count. |
| `b3_severity_observability_status` | VARCHAR | post_2018_only | none / partial / full: whether the fine severity ladder was observable across this vehicle's prior history. |
| `b3_n_dangerous_items` | BIGINT | post_2018_only | Prior items with dangerous_mark='D' (post-2018 only); NULL when no prior day is severity-observable. |
| `b3_n_dangerous_days` | BIGINT | post_2018_only | Prior test-days carrying >=1 dangerous item; NULL when unobservable. |
| `b3_days_since_dangerous` | BIGINT | post_2018_only | Days since the most recent prior dangerous item; NULL when never/unobservable. |
| `b3_n_major_items` | BIGINT | post_2018_only | Prior MAJOR items: disposition in {F,P} and NOT dangerous-marked (post-2018 only). |
| `b3_n_major_days` | BIGINT | post_2018_only | Prior test-days carrying >=1 major item; NULL when unobservable. |
| `b3_days_since_major` | BIGINT | post_2018_only | Days since the most recent prior major item; NULL when never/unobservable. |
| `b3_n_minor_items` | BIGINT | post_2018_only | Prior MINOR items (rfr_type_code 'M', F-22-corrected: minor does NOT fail the test); post-2018 only. |
| `b3_n_minor_days` | BIGINT | post_2018_only | Prior test-days carrying >=1 minor item; NULL when unobservable. |
| `b3_days_since_minor` | BIGINT | post_2018_only | Days since the most recent prior minor item; NULL when never/unobservable. |
| `b3_n_prs_items` | BIGINT | all_eras | Prior items with disposition 'P' -- fail-bearing, rectified at the station. Both eras. |
| `b3_n_prs_item_days` | BIGINT | all_eras | Prior test-days carrying >=1 PRS-rectified item. |
| `b3_days_since_prs_item` | BIGINT | all_eras | Days since the most recent prior PRS-rectified item; NULL when never. |
| `b3_n_fail_items_initial` | BIGINT | all_eras | Prior fail-bearing items on the INITIAL basis (disposition F + P). Both eras. |
| `b3_n_fail_items_final` | BIGINT | all_eras | Prior fail-bearing items on the FINAL-unrectified basis (disposition F only). Both eras. |
| `b3_n_advisory_items` | BIGINT | all_eras | Prior advisory items (disposition A). Both eras; volumes are recording-regime-confounded pre-2015 (DATA_ASSESSMENT §4). |
| `b3_fail_item_rectified_share` | DOUBLE | all_eras | b3_n_prs_items / b3_n_fail_items_initial -- share of fail-bearing items rectified at the station; NULL when no fail-bearing items. |

### B4 — trajectory — 14 columns

| column | type | era observability | definition |
|---|---|---|---|
| `b4_n_adv_to_fail_transitions` | BIGINT | all_eras | Count of (category, day) pairs where a fail-bearing item appeared in a category that had carried an ADVISORY on some strictly earlier day. |
| `b4_adv_to_fail_categories` | BIGINT | all_eras | Distinct categories with >=1 advisory->fail transition. |
| `b4_days_since_adv_to_fail` | BIGINT | all_eras | Days since the most recent advisory->fail transition; NULL when never. |
| `b4_n_recurrence_after_repair` | BIGINT | all_eras | Count of (category, day) pairs where a category failed, a later day passed, and the category failed again. |
| `b4_recurrence_categories` | BIGINT | all_eras | Distinct categories with >=1 recurrence-after-repair. |
| `b4_days_since_recurrence` | BIGINT | all_eras | Days since the most recent recurrence-after-repair; NULL when never. |
| `b4_burden_delta_1` | BIGINT | all_eras | n_items on the most recent prior test-day minus the one before it; NULL with <2 priors. |
| `b4_burden_delta_2` | BIGINT | all_eras | n_items on the 2nd most recent prior test-day minus the 3rd; NULL with <3 priors. |
| `b4_burden_mean_last3` | DOUBLE | all_eras | Mean n_items over the last up-to-3 prior test-days; NULL without priors. |
| `b4_burden_x_age` | DOUBLE | all_eras | b4_burden_mean_last3 * b1_age_at_target_years; NULL when either input is NULL. |
| `b4_mileage_band` | VARCHAR | all_eras | Band of the LAST TRUSTED prior odometer reading: 0-30k / 30k-60k / 60k-100k / 100k+ / unknown. Unit-robust: never mixes a km reading with a mi reading because only one reading is used. |
| `b4_burden_x_mileage_band_ord` | DOUBLE | all_eras | b4_burden_mean_last3 * mileage-band ordinal (1..4); NULL when the band is unknown. |
| `b4_deterioration_slope` | DOUBLE | all_eras | Least-squares slope of items-per-test-day against years since the first prior day (items/year); NULL with <2 priors or a degenerate span. |
| `b4_deterioration_slope_n_days` | BIGINT | all_eras | Number of ITEM-OBSERVABLE prior test-days the slope was fitted on (its honest denominator). Dark days contribute neither a point nor a count -- previously they contributed a false 0 to the regression AND inflated this denominator. |

### B5 — same-day multiset, ambiguity and gaps — 15 columns

| column | type | era observability | definition |
|---|---|---|---|
| `b5_n_prior_multi_test_days` | BIGINT | all_eras | Prior test-days carrying more than one test record (same-day retest pairs). |
| `b5_max_tests_on_a_prior_day` | BIGINT | all_eras | Largest number of test records on any single prior test-day. |
| `b5_n_prior_days_pass_and_fail` | BIGINT | all_eras | Prior test-days carrying BOTH a PASS and a FAIL record (the tie-exposed population). |
| `b5_n_prior_nondefinitive_days` | BIGINT | all_eras | Prior test-days whose D13 cluster outcome is AMBIGUOUS under cycles.py semantics -- which is EITHER a same-stratum FAIL + definitive pass OR a day of mixed non-definitive outcomes (e.g. ABANDONED + ABORTED). Superset of b5_n_prior_ambiguous_days. |
| `b5_n_prior_ambiguous_days` | BIGINT | all_eras | STRICT variant: prior test-days that are AMBIGUOUS *and* carry a definitive outcome -- same-stratum FAIL + definitive pass only. The sequence is unidentified and is never invented. |
| `b5_n_prior_mileage_conflict_days` | BIGINT | all_eras | Prior test-days whose same-day odometer readings differ by more than 1%. |
| `b5_last_day_n_tests` | BIGINT | all_eras | Test records on the most recent prior test-day; NULL without priors. |
| `b5_last_day_n_distinct_outcomes` | BIGINT | all_eras | Distinct outcomes on the most recent prior test-day; NULL without priors. |
| `b5_last_day_has_pass` | BOOLEAN | all_eras | Most recent prior test-day contained a PASS record; NULL without priors. |
| `b5_last_day_has_fail` | BOOLEAN | all_eras | Most recent prior test-day contained a FAIL record; NULL without priors. |
| `b5_last_day_has_prs` | BOOLEAN | research_only_input | Most recent prior test-day contained a PRS record; NULL without priors. PRS has no confirmed live representation (SERVE_VIEW invariant 3). |
| `b5_last_day_has_nonresult` | BOOLEAN | all_eras | Most recent prior test-day contained an abandoned/aborted/refused record; NULL without priors. |
| `b5_days_since_prior_day` | BIGINT | all_eras | tgt_date minus the most recent prior test-day, in days; NULL without priors. |
| `b5_gap_annual_band_flag` | BOOLEAN | all_eras | b5_days_since_prior_day falls in the annual band [300, 430]; NULL without priors. |
| `b5_covid_straddle_flag` | BOOLEAN | calendar_derived | The interval [last prior test-day, tgt_date] intersects the COVID MOT-extension window 2020-03-30..2020-08-01. |

### B6 — positional (research-only) — 14 columns

| column | type | era observability | definition |
|---|---|---|---|
| `b6_lat_nearside_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LATERAL group 'nearside' (mdr_rfr_location.lateral); NULL when the location map is absent. |
| `b6_lat_offside_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LATERAL group 'offside' (mdr_rfr_location.lateral); NULL when the location map is absent. |
| `b6_lat_centre_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LATERAL group 'centre' (mdr_rfr_location.lateral); NULL when the location map is absent. |
| `b6_lat_inner_outer_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LATERAL group 'inner_outer' (mdr_rfr_location.lateral); NULL when the location map is absent. |
| `b6_lat_unknown_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LATERAL group 'unknown' (mdr_rfr_location.lateral); NULL when the location map is absent. |
| `b6_long_front_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LONGITUDINAL group 'front' (mdr_rfr_location.longitudinal); NULL when the location map is absent. |
| `b6_long_rear_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LONGITUDINAL group 'rear' (mdr_rfr_location.longitudinal); NULL when the location map is absent. |
| `b6_long_unknown_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse LONGITUDINAL group 'unknown' (mdr_rfr_location.longitudinal); NULL when the location map is absent. |
| `b6_vert_upper_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse VERTICAL group 'upper' (mdr_rfr_location.vertical); NULL when the location map is absent. |
| `b6_vert_lower_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse VERTICAL group 'lower' (mdr_rfr_location.vertical); NULL when the location map is absent. |
| `b6_vert_inner_outer_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse VERTICAL group 'inner_outer' (mdr_rfr_location.vertical); NULL when the location map is absent. |
| `b6_vert_unknown_n` | BIGINT | research_only_input | Prior defect items whose location_id has coarse VERTICAL group 'unknown' (mdr_rfr_location.vertical); NULL when the location map is absent. |
| `b6_pos_n_total` | BIGINT | research_only_input | Prior defect items whose location_id RESOLVED against mdr_rfr_location (the honest denominator for both axes); NULL when the location map is absent. |
| `b6_location_map_status` | VARCHAR | research_only_input | present / absent -- whether mdr_rfr_location was supplied. Absent means the B6 counts are NULL, not zero. |

### B7D — deployable day-grain history (Lane D) — 35 columns

Day grain THROUGHOUT, under the five-state contract in `factory/day_outcomes.py`:
`S_d in {PASS, PRS, FAIL, AMBIGUOUS, UNAVAILABLE}`. Every proportion here counts
`k = #(S_d = FAIL)` over `n = #(S_d in {PASS, PRS, FAIL})` — AMBIGUOUS and
UNAVAILABLE days are excluded from **both** numerator and denominator, and their
exposure is emitted so the exclusion is auditable. Excluding them from the
numerator alone would assert "this day was not a failure", which is the
unavailable-to-zero conflation this lake was repaired to remove.

**Day grain is not test grain and these names do not pretend it is.** There is
deliberately no `major_days_per_valid_test`: that is a test-grain estimand Lane D
cannot honestly reconstruct, because 35.09% of targets carry at least one prior
day whose initial/retest split is unidentifiable (measured, `flat4y` r1m panel,
n = 999,999).

| column | type | era observability | serve class | definition |
|---|---|---|---|---|
| `b7d_prev_day_outcome` | VARCHAR | all_eras | deployable | Five-state outcome of the most recent prior test-day: PASS / PRS / FAIL / AMBIGUOUS / NO_HISTORY. The deployable analogue of the immediately-preceding-event term. |
| `b7d_n_prior_fail_days` | BIGINT | all_eras | deployable | Prior test-days whose five-state outcome is FAIL. AMBIGUOUS days are excluded, never counted as non-failures. |
| `b7d_n_prior_pass_days` | BIGINT | all_eras | deployable | Prior test-days whose five-state outcome is PASS (PRS counted separately). |
| `b7d_n_prior_prs_days` | BIGINT | research_only_input | research_only | Prior test-days whose five-state outcome is PRS -- a pass whose defects were real. Tagged research-only pending confirmation that PRS is visible at serving (SERVE_VIEW invariant 3). |
| `b7d_n_prior_outcome_observable_days` | BIGINT | all_eras | deployable | Prior test-days whose outcome is identified (PASS + PRS + FAIL) -- the honest denominator for every b7d proportion. |
| `b7d_n_prior_outcome_ambiguous_days` | BIGINT | all_eras | deployable | Prior test-days carrying a definitive outcome that is NOT identified (same-stratum FAIL + pass). The excluded exposure, made visible. |
| `b7d_days_since_fail_day` | BIGINT | all_eras | deployable | Days since the most recent prior test-day whose outcome is FAIL; NULL when never. |
| `b7d_last_day_n_fail_items` | BIGINT | all_eras | deployable | Fail-bearing items on the most recent prior test-day; NULL when that day's detail is unobservable. |
| `b7d_last_day_max_severity_ord` | BIGINT | post_2018_only | deployable | Max severity rung on the most recent prior test-day: 0 clean / 1 advisory / 2 minor / 3 major / 4 dangerous; NULL when unobservable. |
| `b7d_last_day_n_major` | BIGINT | post_2018_only | deployable | Major items on the most recent prior test-day; NULL when severity is unobservable. |
| `b7d_last_day_n_dangerous` | BIGINT | post_2018_only | deployable | Dangerous items on the most recent prior test-day; NULL when severity is unobservable. |
| `b7d_last_fail_day_n_items` | BIGINT | all_eras | deployable | Fail-bearing items on the most recent prior test-day that actually FAILED; NULL when never or unobservable. |
| `b7d_last_fail_day_n_categories` | BIGINT | all_eras | deployable | Distinct categories carrying a fail-bearing item on the most recent prior FAIL day -- the breadth of the last failure; NULL when never or unobservable. |
| `b7d_last_fail_day_max_severity_ord` | BIGINT | post_2018_only | deployable | Max severity rung on the most recent prior FAIL day; NULL when never or unobservable. |
| `b7d_fail_days_per_outcome_observable_day` | DOUBLE | all_eras | deployable | Beta-binomial smoothed share of outcome-observable prior test-days that failed; NULL without an observable day. |
| `b7d_fail_days_per_result_observable_year` | DOUBLE | all_eras | deployable | Gamma-Poisson smoothed FAIL days per year of result-observable exposure; NULL when the window is non-positive. |
| `b7d_major_days_per_severity_observable_day` | DOUBLE | post_2018_only | deployable | Beta-binomial smoothed share of severity-observable prior test-days carrying a major item; NULL when severity was never observable. |
| `b7d_major_days_per_severity_observable_year` | DOUBLE | post_2018_only | deployable | Gamma-Poisson smoothed major days per year of severity-observable exposure; NULL when that window is non-positive. |
| `b7d_dangerous_days_per_severity_observable_day` | DOUBLE | post_2018_only | deployable | Beta-binomial smoothed share of severity-observable prior test-days carrying a dangerous item; NULL when severity was never observable. |
| `b7d_advisory_days_per_item_observable_day` | DOUBLE | all_eras | deployable | Beta-binomial smoothed share of item-observable prior test-days carrying an advisory; NULL when no prior day is item-observable. |
| `b7d_n_fail_days_cap2y` | BIGINT | all_eras | deployable | b7d_n_prior_fail_days restricted to the trailing 2-year window before tgt_date. |
| `b7d_n_major_days_cap2y` | BIGINT | post_2018_only | deployable | Prior test-days carrying a major item, restricted to the trailing 2-year window; NULL when severity was never observable. |
| `b7d_n_dangerous_days_cap2y` | BIGINT | post_2018_only | deployable | Prior test-days carrying a dangerous item, restricted to the trailing 2-year window; NULL when severity was never observable. |
| `b7d_last3day_fail_items_sum` | BIGINT | all_eras | deployable | Fail-bearing items summed over the last up-to-3 item-observable prior test-days; NULL when none is observable. |
| `b7d_last3day_n_outcome_observed` | BIGINT | all_eras | deployable | How many of the last up-to-3 prior test-days had an identified outcome -- the explicit bounded denominator for the outcome channel. |
| `b7d_last3day_n_detail_observed` | BIGINT | all_eras | deployable | How many of the last up-to-3 prior test-days had observable defect detail -- a SEPARATE denominator from the outcome channel, never reused across the two. |
| `b7d_recent3day_minus_earlier_burden` | DOUBLE | all_eras | deployable | Mean fail-bearing items over the last up-to-3 item-observable days minus the mean over all earlier ones; NULL until an earlier period exists. |
| `b7d_n_adv_to_minor_transitions` | BIGINT | post_2018_only | deployable | Categories escalating from advisory to minor on a strictly later day; NULL when severity was never observable. |
| `b7d_n_minor_to_major_transitions` | BIGINT | post_2018_only | deployable | Categories escalating to major from a strictly lower rung observed earlier; NULL when severity was never observable. |
| `b7d_n_major_to_dangerous_transitions` | BIGINT | post_2018_only | deployable | Categories escalating to dangerous from a strictly lower rung observed earlier; NULL when severity was never observable. |
| `b7d_n_severity_transition_opportunities` | BIGINT | post_2018_only | deployable | Category-days where a rung was already on record and could therefore have escalated -- the honest denominator; NULL when severity was never observable. |
| `b7d_severity_escalation_share` | DOUBLE | post_2018_only | deployable | Beta-binomial smoothed share of transition opportunities that escalated; NULL without an opportunity. |
| `b7d_days_since_severity_escalation` | BIGINT | post_2018_only | deployable | Days since the most recent category severity escalation; NULL when never. |
| `b7d_outcome_history_status` | VARCHAR | all_eras | deployable | no_priors / none / partial / full -- outcome identifiability across this vehicle's prior test-days. |
| `b7d_severity_transition_status` | VARCHAR | all_eras | deployable | observed / unobserved -- whether any prior day could support a category severity rung. |

### B7R — initial-presentation history (Lane R, research-only) — 34 columns

**Every column here consumes `test_type`-in-history and can never be served**
(SERVE_VIEW invariant 2). The block measures an information CEILING — what
correctly reconstructed initial-presentation state would be worth if it could be
served — and no adoption decision follows from it. It consumes no adopted-cap
headroom.

Grain is the initial-presentation DAY: a prior day carrying at least one
definitive NT, resolved by the same five-state rule restricted to NT rows. All NT
rows share a type rank, so an NT-FAIL + NT-PASS day resolves to AMBIGUOUS rather
than inventing a sequence. The record-count twin is the existing
`b1_n_prior_initials` — reused, never re-emitted.

| column | type | era observability | serve class | definition |
|---|---|---|---|---|
| `b7r_prev_initial_outcome` | VARCHAR | research_only_input | research_only | Five-state outcome of the most recent prior initial-presentation day: PASS / PRS / FAIL / NO_HISTORY / UNAVAILABLE. |
| `b7r_days_since_prev_initial` | BIGINT | research_only_input | research_only | Days since the most recent prior initial-presentation day with an identified outcome; NULL when never. |
| `b7r_days_since_prev_initial_fail` | BIGINT | research_only_input | research_only | Days since the most recent prior initial presentation that FAILED; NULL when never. |
| `b7r_n_prior_initial_days` | BIGINT | research_only_input | research_only | Prior initial-presentation days with an identified outcome -- the denominator for every b7r share. |
| `b7r_n_prior_initial_fail_days` | BIGINT | research_only_input | research_only | Prior initial-presentation days whose outcome is FAIL. |
| `b7r_n_prior_initial_prs_days` | BIGINT | research_only_input | research_only | Prior initial-presentation days whose outcome is PRS. |
| `b7r_n_prior_initial_ambiguous_days` | BIGINT | research_only_input | research_only | Prior initial-presentation days whose outcome is not identified -- excluded exposure, made visible. |
| `b7r_initial_fail_share` | DOUBLE | research_only_input | research_only | Beta-binomial smoothed share of identified prior initial presentations that FAILED; NULL without one. |
| `b7r_initial_adverse_share` | DOUBLE | research_only_input | research_only | Beta-binomial smoothed share of identified prior initial presentations that were FAIL or PRS; NULL without one. |
| `b7r_initial_fail_days_per_result_observable_year` | DOUBLE | research_only_input | research_only | Gamma-Poisson smoothed initial-presentation failures per year of result-observable exposure; NULL when the window is non-positive. |
| `b7r_last3_n_initial_observed` | BIGINT | research_only_input | research_only | How many of the last up-to-3 initial presentations are on record -- 1 or 2 when that is all there is, never 3. |
| `b7r_last3_n_initial_fail` | BIGINT | research_only_input | research_only | Failures among the last up-to-3 identified initial presentations. |
| `b7r_last3_n_initial_adverse` | BIGINT | research_only_input | research_only | FAIL or PRS outcomes among the last up-to-3 identified initial presentations. |
| `b7r_recent3_minus_earlier_fail_share` | DOUBLE | research_only_input | research_only | Fail share over the last up-to-3 initial presentations minus the share over all earlier ones; NULL until an earlier period exists. |
| `b7r_current_initial_fail_streak` | BIGINT | research_only_input | research_only | Consecutive most-recent initial presentations that failed. A retest pass does NOT break it. |
| `b7r_current_initial_pass_streak` | BIGINT | research_only_input | research_only | Consecutive most-recent initial presentations that did not fail. |
| `b7r_max_initial_fail_streak` | BIGINT | research_only_input | research_only | Longest run of consecutive failing initial presentations ever observed. |
| `b7r_initial_fail_decay_num_hl1y` | DOUBLE | research_only_input | research_only | Recency-weighted count of failing initial presentations, half-life 1 year; NULL without history. |
| `b7r_initial_opportunity_decay_den_hl1y` | DOUBLE | research_only_input | research_only | Recency-weighted count of initial presentations (the opportunities), half-life 1 year. Emitted so a 0.5 rate over 0.5 weighted presentations is distinguishable from 0.5 over 8. |
| `b7r_initial_fail_decay_rate_hl1y` | DOUBLE | research_only_input | research_only | Recency-weighted proportion of initial presentations that failed, half-life 1 year; NULL without history. |
| `b7r_initial_fail_decay_num_hl3y` | DOUBLE | research_only_input | research_only | Recency-weighted count of failing initial presentations, half-life 3 years; NULL without history. |
| `b7r_initial_opportunity_decay_den_hl3y` | DOUBLE | research_only_input | research_only | Recency-weighted count of initial presentations, half-life 3 years. |
| `b7r_initial_fail_decay_rate_hl3y` | DOUBLE | research_only_input | research_only | Recency-weighted proportion of initial presentations that failed, half-life 3 years; NULL without history. |
| `b7r_prev_initial_n_fail_items` | BIGINT | research_only_input | research_only | Fail-bearing items on the most recent identified prior initial presentation; NULL when its detail is unobservable. |
| `b7r_prev_initial_n_categories` | BIGINT | research_only_input | research_only | Distinct categories carrying a fail-bearing item on the most recent identified prior initial presentation; NULL when unobservable. |
| `b7r_prev_initial_max_severity_ord` | BIGINT | research_only_input | research_only | Max severity rung on the most recent identified prior initial presentation; NULL when unobservable. |
| `b7r_prev_initial_n_major` | BIGINT | research_only_input | research_only | Major items on the most recent identified prior initial presentation; NULL when severity is unobservable. |
| `b7r_prev_initial_n_dangerous` | BIGINT | research_only_input | research_only | Dangerous items on the most recent identified prior initial presentation; NULL when severity is unobservable. |
| `b7r_last3_initial_fail_items_sum` | BIGINT | research_only_input | research_only | Fail-bearing items summed over the last up-to-3 detail-observable initial presentations; NULL when none is observable. |
| `b7r_last3_initial_categories_sum` | BIGINT | research_only_input | research_only | Distinct fail categories summed over the last up-to-3 detail-observable initial presentations; NULL when none is observable. |
| `b7r_recent3_minus_earlier_burden` | DOUBLE | research_only_input | research_only | Mean fail-bearing items over the last up-to-3 detail-observable initial presentations minus the mean over all earlier ones; NULL until an earlier period exists. |
| `b7r_fail_items_per_initial_event` | DOUBLE | research_only_input | research_only | Gamma-Poisson smoothed fail-bearing items per detail-observable initial presentation; NULL when none is observable. |
| `b7r_initial_outcome_history_status` | VARCHAR | research_only_input | research_only | no_priors / none / partial / full -- outcome identifiability across this vehicle's prior initial presentations. |
| `b7r_initial_detail_history_status` | VARCHAR | research_only_input | research_only | no_priors / none / partial / full -- defect-detail observability across this vehicle's prior initial presentations. |

## The packets view (contract v2 §1) — 19 columns (packet schema v2)

The 104 serving features are **not** computed by this factory. They come from
the parity-gated packets → `feature_engineering_v55` path. The factory emits the
packets that path consumes, mirroring the banked fulldepth shape
(`FULLDEPTH_SUBSTRATE_V1.md`, `artifacts/fulldepth_packets.parquet`): LONG form,
one row per (target × prior); a zero-prior target emits exactly one row with
NULL `p_*` fields, so every target appears exactly once.

| column | definition |
|---|---|
| `tgt_id` | test_id of the prediction event. |
| `vehicle_id` | Lake vehicle identifier. |
| `tgt_date` | Target test date; every `p_date` is strictly earlier (gated). |
| `tgt_make` / `tgt_model` / `tgt_fuel` / `tgt_cc` / `tgt_fud` / `tgt_pc` | Target-row vehicle attributes, as the module's `VehicleHistory` expects. |
| `n_priors` | Prior test RECORDS for this target (before any `--max-priors` trim). |
| `p_test_id` | Prior test id (identifier only; never ordered on). |
| `p_date` | Prior test date. |
| `p_result` | Module vocabulary: PASS/PRS → `PASSED`, FAIL → `FAILED`, everything else → `ABANDONED` (r2b `RESULT_MAP`). |
| `p_outcome` | The canonical LAKE outcome, unmapped — so the PRS-fold is reversible downstream. |
| `p_miles` | Prior odometer reading as recorded (unit caveat [UNIT] applies pre-2022). |
| `p_ttype` | Prior DVSA test type (NT/RT/ES/EI). |
| `p_n_items` | Defect items on that prior test. **NULL when that test's defect detail is not observable — never 0** (PREREG_CUBE_v2 §4). |
| `p_items_observability` | `present_with_defects` / `present_zero_defects` / `assumed_zero_defects` / `unavailable` / `expected_missing`. The state that decides how `defects_json` and `p_n_items` must be read. Added at packet schema v2. |
| `defects_json` | JSON array of defect objects, keys `rfr, code, disp, sev, sect, cat, comp, dang, loc, pos`. **THREE STATES**: a JSON array = observed with those items; `[]` = observed and carrying no defect items; NULL = the detail is unavailable, or the builder emitted no payload (`--defect-detail counts/none`). **No consumer may read NULL as "no defect"** — that is INC-2026-08-13. |

**Capability sidecar.** Every packets directory carries `PACKET_CAPABILITY.json`
declaring `defect_payload_mode`, the observability states, `items_coverage_mode`
(certified / assumed_covered), the measured item join and per-year source
availability, `packet_schema_version`, publisher schema epochs, the build
configuration and the source hashes. A consumer asserts against it before it
runs; a legacy packet set with no sidecar is MEASURED, never assumed
(`factory/capability.py`).

**The one deliberate difference from the banked shape: there is no defect text.**
The v58 lake carries none (DATA_ASSESSMENT §4: "No free text"), so
`defects_json` has no `x` key. It carries the code-grain and severity-axis
fields instead: `rfr` (rfr_id), `code` (raw `rfr_type_code`, case preserved),
`disp` (F-22-corrected disposition), `sev` (dangerous/major/minor/advisory/
`pre2018_ungraded`), `sect` (top-level section name), `cat` (category key; NULL =
catalogue miss), `comp` (serving component name: brakes/tyres/suspension/
steering/structure), `dang` (`dangerous_mark='D'`), `loc` (location_id),
`pos` (coarse position group; NULL without the location map).

A downstream adapter must therefore build the module's `defects[].text` itself
(the catalogue-text join, exactly as `r2b_build_v57.py:70-72` did) or consume
`cat`/`comp` directly. The factory deliberately does **not** synthesise an empty
text field: an empty string makes the serving keyword parser return "no
component" for every defect, which is the D1-class defect this programme is
repairing.

## Conventions recorded because they were judgement calls

- **`b4_mileage_band` is unit-robust by construction**: it bands the *single*
  last-trusted prior reading, so no arithmetic ever mixes a km reading with a mi
  reading. Same-day readings differing by >1% are flagged
  (`b5_n_prior_mileage_conflict_days`) but the day's maximum is still used —
  matching the fulldepth substrate's convention.
- **`b1_opportunity_adjusted_density`** divides by *testable* years =
  observable years, minus the first 3 years of vehicle life (no MOT is due), minus
  the overlap with the COVID MOT-extension window 2020-03-30…2020-08-01.
- **`b4_deterioration_slope`** is an ordinary least-squares slope of
  `n_items_day` against years since the first prior day — order-free, and it
  ships its own denominator (`b4_deterioration_slope_n_days`).
- **`b2_*_max_run`** counts consecutive *observed test-days*, not consecutive
  calendar years: a vehicle that skips a year has no run break.
- **Advisory→fail transitions** use the advisory memory as it stood BEFORE the
  failing day, so an advisory and a fail on the SAME day is not a transition.
- **Recurrence-after-repair** requires an intervening day whose cluster outcome
  is PASS/PRS; an AMBIGUOUS day neither repairs nor recurs.
- **`b5_*` "same-day multiset" features describe PRIOR days.** The target day's
  own multiset is unknowable at prediction time (you cannot know a retest will
  follow later that day), so using it would be a leak. Falsifier 4 pins this.
- **`b1_n_prior_initials` / `_final_fails` / `_initial_fails` consume
  `test_type` in history**, which the live API does not expose (SERVE_VIEW
  invariant 2). They are emitted and tagged `research_only_input`; the
  production-common twins are the test-day counts.

## Owner rulings (2026-08-12) — all five questions closed

1. **[P4] P-out stands.** `BuildConfig.fail_basis` = `final` (F only), matching
   the repaired serving vocabulary `{DANGEROUS,MAJOR,FAIL,F}` (FEr:101) and the
   frozen target ruling. B3's dual emission (`_initial` = F+P, `_final` = F)
   covers the F+PRS secondary, so nothing is lost.
2. **Class knob SPLIT — implemented.** `target_classes=('3','4')` (D7 population
   rule) applies to events; `history_classes=None` = UNFILTERED. See reading
   convention 8 and falsifier F11.
3. **`cycles._cluster_outcome` private import: accepted as-is.** Kept with the
   note in `state.py`; a public alias in `pipeline/lake/cycles.py` remains the
   tidier long-term shape.
4. **`mdr_rfr_location.csv` located and wired**
   (`/Users/henrirapson/autosafe_raw/lookup/mdr_rfr_location.csv`, pipe-delimited,
   header `id|lateral|longitudinal|vertical`, 130 rows). **All three axes are
   wired** (second ruling, same day): `lateral` → nearside / offside / centre /
   inner_outer / unknown; `longitudinal` → front / rear / unknown; `vertical` →
   upper / lower / inner_outer / unknown. A side always beats an inner/outer
   qualifier, so `Nearside Inner` groups as nearside; bare `Inner`/`Outer` (which
   appear in BOTH the lateral and vertical columns) get their own `inner_outer`
   group rather than being folded into `unknown`. `b6_pos_n_total` is the honest
   denominator (items whose location_id RESOLVED against the lookup);
   `b6_location_map_status` is retained for absent-map builds, which emit NULL
   rather than zero. The loader resolves all three columns BY NAME and raises if
   one is missing. Packet defect payloads carry `pos` as `lateral/longitudinal/vertical`.
   The 90→114→60 code-granularity regime (DATA_ASSESSMENT §9) still needs an
   era-stable map before B6 is interpretable ACROSS eras.
5. **Rung calibration: owner runs `--calibrate`.** It measures the u-thresholds
   that hit 250k/500k/1M/2M on the injected relation; the owner pins them with
   `--rung`. The enrichment ≤25% cap is measured and reported per rung
   (`enriched_share_within_cap`), never silently enforced.
6. **Depth caps in the single scan** (schedule-risk ruling H3). B1 emits
   `n_prior_test_days` / `n_prior_initials` / `history_years` at {2y, 5y}; B2
   emits per-canonical-category prior defect-day counts at {2y, 5y}. **The B2
   capped days-since-last variants were DROPPED** (the ruling's stated first cut
   if the cap were exceeded): with them the total was 151 vs the cap of 150 — and
   they carry no information, being exactly `b2_{c}_days_since` censored at the
   cap. **Cut CONFIRMED by the owner 2026-08-12.** Reconstruct downstream with
   this one-liner (`cap_days` = `round(cap_years * 365.25)`, i.e. **731** for
   `cap2y` and **1826** for `cap5y`):

   ```sql
   -- b2_<cat>_days_since_cap2y, for any category <cat>:
   CASE WHEN b2_<cat>_days_since < 731 THEN b2_<cat>_days_since END   -- cap5y: 1826
   ```
   ```python
   # pandas / numpy equivalent
   df[f"b2_{cat}_days_since_cap2y"] = df[f"b2_{cat}_days_since"].where(
       df[f"b2_{cat}_days_since"] < 731)          # cap5y: < 1826
   ```

   It is exact, not an approximation: the most recent prior day carrying a
   category IS the most recent one inside a trailing window whenever it falls in
   that window, and NULL otherwise — which is what the strict `<` encodes
   (`n_days_cap == 0` ⟺ `days_since_cap IS NULL`). Dropping the 14 columns costs
   nothing and leaves 13 columns of headroom.
7. **`b5_n_prior_ambiguous_days` renamed and split** (M-4). The cycles-faithful
   superset — `_cluster_outcome == AMBIGUOUS`, which also fires on a day of mixed
   non-definitive outcomes — is now `b5_n_prior_nondefinitive_days`. The strict
   same-stratum-FAIL-plus-definitive-pass case keeps the name
   `b5_n_prior_ambiguous_days`; it dropped out of the existing accumulator for
   free. Beware the near-namesake `b1_n_prior_nonresult_days`, which is a
   different denominator (days with NO definitive outcome at all, unanimous or
   not); a mixed non-result day counts in both, by construction.

## Adversarial-review fixes carried into this dictionary (out/FACTORY_REVIEW.md)

- **B-1 `inclusion_weight`** is now a function of the DESIGN CELL only:
  `base/enriched` for a stratum-eligible row whatever its realised `u`, else 1.0.
  The pre-fix rule inflated every weighted enriched-stratum total by
  `2 − base/enriched` (up to 2×). Pinned by probes P1/P1b/P1c and by an
  end-to-end assertion on `build()`'s writer path.
- **B-2 staging** is wiped per build (`prepare` deletes and recreates
  `vehicle_day/` and each `events/recipe=*/`), and the manifest records staged
  file counts. Pinned by probes P4 (ghost vehicle) and P4b (shrink-safe rerun).
- **M-3 fallback** classifies DOWN (reading convention 10).
