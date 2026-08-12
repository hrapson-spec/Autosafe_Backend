# AutoSafe 2005–present data assessment

2026-08-12. Owner-commissioned (Henri). Analysis log + preregistered scripts + outputs:
`docs/v58/assessment_2026_08/`. Every material number below is either (a) reproduced
from parquet footers/recorded checks this session, (b) computed by a sha-registered
script over the lake (outputs in `out/`), or (c) cited to a committed artifact with
path. Certified gate PASSes are never cited as semantic validation.

**Authority note.** The #18 charter's lake-token protocol was owner-overridden for this
assessment (Henri's ruling, 2026-08-12, recorded here per his instruction — no separate
authority artifact). At override time the token sat with the seat-6 census mission
(d7-branch OPEN_QUEUE grant ~12:2x); this assessment ran read-only scans under the
one-scanner discipline (pre-flight process checks before every batch) and created no
lake datasets — the G2/G4 holds (no cycles build, no canonical regeneration) were
honoured throughout. The d7-side record should note this override when next reconciled.

---

## Material findings (read first)

1. **The dataset is 2005–2023, not 2005–present** — ⚠ **CORRECTED 2026-08-12 ~16:00,
   see Addendum A below.** As originally written: the DVSA anonymised release ends at
   full-year 2023 (CKAN + live page + URL probes, evidence `NUMBERS.md` /
   `docs/v58/evidence/NOTES.md:26-41`); no 2024 or 2025 in the new lake; recency
   bridges = `fresh_frame_2025h1` (local), 2024-Q4 cal frame (Drive-only), deltas
   through 2026-08-08, and the live API. **The statement was true of the DfT-published
   dataset probed, but false of the world: DVSA moved publication to its own portal in
   January 2025 and 2024+2025 anonymised extracts exist there.** The in-lake facts and
   every other number in this assessment are unaffected; the recency implications
   (Q3.1, Q5.1) are superseded by Addendum A.
2. **F-22 (decided today, measured verdict): the post-2018 item severity mapping is
   INVERTED in the lake's derived columns.** Post-2018 vocabulary is {A,F,M,P}; `M` =
   Minor per DVSA guide v5.1; `rfr_mapping.py` reads it as Major-fail. 31,748,964
   items (18.3% of what the pipeline treats as post-2018 failing) are minors counted
   as fails; `is_dangerous` is identically FALSE lake-wide (derived from a code that
   never occurs). The certified `taxonomy_step` PASS is semantically vacuous (satisfied
   by `M` alone) and the certified category-coverage denominator (594,200,636 failing
   items) contains the 31.7M minors. The raw columns are correct; only the derived
   severity columns are wrong. Fix is HELD for the post-G2 commit
   (`#18_F22_DECIDER.md:144-181`); this assessment uses a corrected overlay throughout
   and dual-labels every affected number.
3. **The dangerous signal exists after all** — in the stored-but-unconsumed
   `dangerous_mark` column: 22,292,034 post-2018 items carry `D` (4.85% of post-2018
   items; the only other value is null/blank). With `rfr_type_code`, the full post-2018
   ladder **dangerous / major / minor / advisory / PRS-item** is reconstructible.
   Pre-2018 has no dangerous class (source layout lacks the column by design).
4. **The tie-rule scare is closed at metric level.** F7a proved ~24% of the fulldepth
   frame's feature vectors flip under within-day tie reversal (feature-level, real);
   F7b (executed today, gate-clean) measured the metric consequence at ΔAUC −0.0000005 /
   +0.0000002 vs the ±0.002052 floor — **null, FINAL**; banked fulldepth absolutes
   survive. D13 chronology semantics proceed for integrity, not rescue
   (`INVARIANTS_AUDIT_2026_08_12.md` Addendum 3).
5. **Single point of failure:** the raw corpus was deleted after ingest (by design;
   Drive park holds 2005–2014 results), but the RfR lookup CSVs
   (`~/autosafe_raw/lookup/` — the entire taxonomy: catalogue, hierarchy, vocabularies)
   are in neither git nor Drive. Losing that directory would make items re-ingest
   impossible without re-downloading from DVSA. Recommend: park a copy to Drive.
6. **test_id spaces are IDENTICAL across old and new substrates — verified.**
   1,237,152 research-lake test_ids matched in the panel with **100.000000%
   test_date agreement** (zero disagreements) — same tests, same id space
   (`out/panel_identity_check.json` + the date-agreement verification). The match
   rate ran 6.37× the uniform expectation because the research lake's vehicle
   selection correlates with the duckdb hash residue family the panel uses (matches
   span 5.5% of its 2,296,105 vehicles) — a provenance quirk, not a defect.
   vehicle_id spaces remain non-comparable across vintages; all old↔new
   reconciliation must go through test_id — and now verifiably can.

---

## Q1 — What do we now have?

One certified, canonical, full-fleet lake in the product repo (`~/autosafe/autosafe_lake`,
~14 GB parquet), two datasets plus a taxonomy:

| Object | Grain | Rows | Span | Where |
|---|---|---|---|---|
| results | one row = one MOT test record | **681,724,337** (zero duplicate test_ids, verified on the full 19-year lake at 09:13 BST today) | 2005-01 – 2023-12 | 2015–2023 local (354,057,034 at footers); 2005–2014 parked on Drive (327,667,303), rclone-hash-verified, restorable per year |
| items | one row = one defect/advisory observation on a test | **1,289,329,470** (all local, footer-exact) | same | items/test_year=2005..2023 |
| lookup | RfR taxonomy: 21,069 catalogue rows (7,505 distinct rfr_id; 4,432 class-4), section hierarchy, code vocabularies | — | single 2023-vintage cumulative snapshot | `~/autosafe_raw/lookup/` (unversioned — finding 5) |

Count vocabulary (use these words):
- **MOT test records**: 681,724,337 (all classes, all test types, all outcomes).
- **Prediction events** (rule of record `target_population.py`: `test_type='NT'` AND
  outcome ∈ {PASS, FAIL, PRS}): **542,542,126** all-class 19-year EXACT; C3&4 =
  **511,982,988** EXACT (both from full-population rule-of-record scans, this
  assessment; reconciles the D12 brief's 18-year 506,425,834 + its pinned 2005
  component 5,557,154 to the row). Label of record = final-basis FAIL-only (D7);
  D12 (FAIL+PRS migration) briefed, deferred.
- **Unique vehicles**: **72,037,010** exact (merged per-year distincts, all 19
  years); 66,042,023 (91.7%) appear in ≥2 calendar years. 2015–2023 alone:
  52,101,209.
- **Defect observations** (item rows): 1,289,329,470 — advisory 695,128,834 (53.9%),
  corrected fail-bearing 562,451,672 (43.6%), post-2018 minors 31,748,964 (2.5%).
- **Individual defect items** (catalogue entries): 7,505 distinct rfr_id all-class;
  the class-4 space is 4,432 (2,511 pre-EU + 1,921 post-EU, disjoint).

Test coverage by year: class-4 volumes run 7,113,089 (2005, partial year —
computerisation completed 2006-04 per DVSA's own guide) → 30,302,568 (2006) →
39,834,324 (2023), recorded per year in the manifest's `year_volumes` check (sums
644,727,311 = 94.6% of all rows; the remainder is other classes). Defect-record
coverage by year: items run 9.8M (2005) → 89.3M (2023) rows; tests-with-items
2,880,088 (2005) → 25,506,643 (2023); items-per-test-with-items stable at p50 2–3,
p99 13–14 across all 19 years (`out/items_per_test.json`).

Storage/practicality: the whole lake is ~14 GB zstd parquet; full-year scans run in
seconds-to-tens-of-seconds on the 8 GB M3 Air under a 3.5 GB duckdb memory limit
(measured: 631-cell census over 354M local rows ~20 s; full 1.29B-item census ~40 s;
the entire Phase-2 profile suite ~100 s). The binding local constraints are disk
(14–16 GiB free; 10 GiB floor) and RAM, not scan speed.

Canonicality: this lake supersedes the research-repo substrate as the data source of
record — certified (0 dup test_ids; continuity 0.998/0.0078/361d at n=10k, and now
**0.9978 / 0.00888 / 361d at n=628,177** multi-test vehicles from this assessment's
panel — closing the chartered n≥50k re-verification: the conflict share is
confidently below the 0.01 bar at this n; external validation vs DVSA MOT-01 median
|Δpp| 0.004 over 8 gated FYs). Two parallel objects
remain relevant and are NOT superseded: (a) the research frames/surfaces for 2024–25
evaluation (underivable here — finding 1); (b) `cycles/` is EMPTY by governance (G2/G4
hold) — cycle semantics live in code (D13), not data.

## Q2 — What is genuinely new?

Quantified against the substrate the existing model and the recent challenger
experiments (TabPFN/TabM/RealMLP, Stage-3 782,055×215 frame; fulldepth 1,048,500×104
H1 frame) actually used:

1. **Full-depth defect items: ~875.4M item rows for 2005–2018 that no evaluated model
   has ever seen.** Old item coverage was exactly 2019–2023 (413,955,229 rows,
   `PROVENANCE_AUDIT_test_items_loc_lake.md`; the new lake's same years agree to
   within −175 rows). This is category (2) information — genuinely richer per-vehicle
   history, not more rows of the same: component families were documented item-blind
   pre-2019, and DQ-01 records 70/215 contract columns fabricated as all-zero for
   20.8% of the s60 training population because struct coverage began in 2021. The new
   lake repairs that entire defect class at source.
2. **Full-fleet scale.** The research lake was target-scoped: 24,346,087 test rows on
   2,296,105 vehicles. The new lake is the fleet: 681.7M rows on 72,037,010
   vehicles — 28× the rows, 31× the vehicles. This is category (1)
   more-rows AND category (2): EB priors, cohort rates and rare-segment estimates can
   now be fitted on population data instead of a scoped sample.
3. **Explicit population semantics**: per-row `test_type` (0 nulls; NT/RT/ES + EI-2023)
   replaced cycle inference as the population rule (REPLACE-D7; MOT-01 gate PASS,
   8 FYs, median |Δpp| 0.004). The D7 label basis and the population are now direct
   observations, not reconstructions.
4. **Severity ladder + position**: post-2018 dangerous/major/minor per item
   (dangerous_mark + rfr_type_code, finding 3), and `location_id` (100.00% populated,
   all 1.29B rows, ~60–90 distinct positional codes/yr) — both previously absent from
   modelling substrates and currently consumed by nothing.
5. **What is NOT new**: 2024–2025 (lost relative to the old substrate — finding 1);
   defect free text (the lake has none; text lives only in the catalogue — the
   closed-vocabulary text conclusions from local_text_value_v1 are unaffected);
   station identity (unavailable in the anonymised release by design, k-anonymised).

## Q3 — What are the major limitations?

Ranked by modelling consequence:

1. **Recency cliff at 2023-12-31** (finding 1). Any model trained purely on this lake
   is ≥2 calendar years stale at serving; the previous programme's evaluation surfaces
   (2024-Q4 cal, 2025-H1) cannot be rebuilt from it.
2. **The 2018-05-20 taxonomy discontinuity.** Class-4 rfr_id code spaces are fully
   disjoint across the boundary (2,511 pre / 1,921 post; overlap 0) → **no code-level
   longitudinal continuity**; the defensible cross-era grain is the section/category
   level (26 top-level names → 7 component categories; two parallel trees verified).
   Severity classes also change (no pre-2018 dangerous/minor). Post-2018-era rows
   additionally carry BOTH code spaces in transition (2,538 class-4 codes observed
   post-2018 vs 1,921 in the post-EU tree) — era must come from the parent test date
   (as ingested), never from the code space.
3. **F-22** (finding 2) until the post-G2 fix lands: every `is_fail_item`-derived
   number is inflated ~18% post-2018; corrected overlay mandatory.
4. **Mileage is unit-ambiguous before 2022 by DVSA's own documentation**: pre-2022
   `test_mileage` "sometimes holds a value that is actually kilometres"; DVSA applied
   the km→miles correction only from the 2022 dataset. There is no unit column. Any
   longitudinal mileage feature carries this; day-grain mileage regressions in the
   panel run at 0.04%–3.0% of events by year (1.79% in 2023) and conflate unit
   flips, corrections, and clocking.
5. **Serving asymmetry** (§10): the live API exposes neither `test_type`, `rfr_id`,
   `location_id`, `test_class_id` nor `postcode_area`; PRS has no confirmed live-API
   representation (OpenAPI enum omits it — UNKNOWN/CONFLICTING per the source
   contract); the lake conversely has no defect text and no timestamps (date grain
   only). Features built on lake-only fields need explicit serving bridges or are
   serving-dead by construction.
6. **Join-integrity bounds**: items were inner-joined to results at ingest, so
   items↔results coverage is 100% BY CONSTRUCTION and orphan volume for 2005–2018 is
   unrecoverable (raw deleted). The only orphan bound is Δ−175 vs the independent old
   2019–2023 ingest. First_use conflict share (0.00888) appeared exactly with the
   2018+ releases (schema_epoch `results_csv` years) — cross-release duplication vs
   organic corrections is UNRESOLVED as attribution; it now sits confidently below
   the 0.01 bar at n=628,177.
7. **2005 is a partial year** (7.5M rows vs 32.0M in 2006; DVSA: computerisation
   completed 2006-04) and pre-FY2013 rates have **no external comparator** (MOT-01
   published table starts FY2013-14; the 8-FY gate covers FY2013–FY2022 minus COVID
   carve-outs). Depth metrics are left-censored at the 2005 boundary — treated in §3
   via the first_use≥2005 clean cohort and observable-window conditioning.

## Q4 — What modelling opportunities does the new dataset create?

Named as information classes (no feature designs here), each tagged with the kill-type
status of its nearest dead relative (kill-crosswalk in §11):

1. **Full-depth per-component defect history (2005+)** — the current model is blind to
   all item-grain information before 2019, and DQ-01 fabricated zeros where coverage
   was missing. The component/advisory families that exist were trained on ≤5 years of
   item signal; they now have 19. (Prior kills: tyre item-state = info-bound on the
   OLD window — premise changed; mechanism family R62 = enumeration-falsifier kill —
   partially reopened by deeper data, falsifier still mandatory.)
2. **Severity-graded defect history** — dangerous/major/minor split post-2018 (new
   direct observation; nothing shipped has ever seen a dangerous flag: is_dangerous
   was identically false in training and real at serving).
3. **Positional defect information** — location_id at 100% coverage, never consumed
   (e.g. corrosion side/position persistence). Entirely unexplored class.
4. **Population-scale priors and cohorts** — EB priors, make/model/age rates, and
   coverage cohorts computable on the fleet rather than a 2.3M-vehicle scoped sample;
   rare-segment support ~28× larger.
5. **Longitudinal depth for the veteran cohort** — of ~34.8M 2023 prediction events,
   **~18.83M [18.74M, 18.92M] carry ≥6 prior initial tests** and ~10.26M [10.19M,
   10.32M] carry ≥10 (×100 vehicle-bootstrap 95% CIs); **~18.40M [18.31M, 18.48M]
   carry ≥3 prior defect-bearing test-days**. The failure-rate gradient across depth
   is steep (9.1% at 0 priors → 48.1% at 21+, 2019+ events). The veteran-depth
   screens (S-DH2→S-DH1) and the fulldepth arm's deep-history targeting evidence now
   have a full-population substrate.
6. **Regime-aware training** — schema_epoch/taxonomy_era/COVID structure is now
   explicit and clean enough to model deliberately (indicator/weighting decisions in
   §9) rather than leak implicitly.

## Q5 — What should this change about the modelling programme?

The principal questions the next experiments must resolve, in dependency order:

1. **Recency strategy before anything else.** Decide the training/eval window design
   given data ends 2023: (a) train ≤2023 + serve-now with drift monitoring, (b) bridge
   with API/delta-derived recent data (fresh_2025-style), (c) both. The honest-0750
   contract's confirmation-surface crisis (2025-H2 dead, S-2026H1 unbuilt) and this
   lake's 2023 end are ONE decision, not two.
2. **Land the F-22 fix + rebuild the severity columns** (post-G2), then re-derive any
   item-rate features on the corrected ladder including dangerous_mark. Until then
   every item experiment must use the corrected overlay.
3. **Value-of-information ladder for the three new information classes** (full-depth
   items, severity ladder, position), run under instrument-first discipline: floor
   RE-DERIVED on the new substrate/surfaces (0.002052 is a GBM-s60-surface quantity;
   the MDE constant is already known 2.5–3.3× optimistic for neural archs), planted
   controls, kill-type recorded per verdict.
4. **Population/label decisions**: D12 (FAIL vs FAIL+PRS) explicitly deferred to the
   v59 boundary; the era-stable population rule is in place; any 2005–2023 training
   frame must define depth features on the clean cohort/observable-window basis (§3)
   to avoid manufacturing a calendar-time signal from left-censoring.
5. **Serving-bridge decisions once, up front** (§10): which new-information features
   are deployable (text→severity words + dangerous flag exist live; rfr_id/location/
   test_type do not), so the bake-off doesn't optimise serving-dead families (R56
   lesson).
6. **Hard gates before training** (§12): parked-year completeness + zero-dup re-verify
   on any rebuilt full lake; as-of attribute joins; strict-date priors; target-event
   exclusion; replace the two vacuous leakage guards; parity phase-A closure; D13-safe
   (day-grain) history definitions.

The dataset is sufficiently understood and trustworthy to design the bake-off — with
the two blocking caveats that are not yet closed: the F-22 fix is HELD (use the
overlay), and the recency decision (Q5.1) precedes any surface design.

---

# Appendix — 14-section evidence base

## §1 Overall dataset

(Head answered in Q1.) Grain notes: results = raw test-record grain — retests, appeals
and aborted/abandoned/refused records are separate rows (outcome vocabulary PASS/FAIL/
PRS/ABANDONED/ABORTED/ABORTED_VE/REFUSED; UNKNOWN = 0.00000 lake-wide); items = one
row per defect observation, keyed test_id → parent test (`ingest_items.py:98-100`
inner join; era stamped from parent test date). The modelling table for the next
programme does not exist yet by design (cycles empty, frames held behind G2/G4);
prediction-event grain is defined by `target_population.py` and counted above.
Per-year row counts: `out/anchors.json` (footers, local), `download_record.md`
(parked, sums exactly to 327,667,303), `year_volumes` recorded detail (class-4, all
19 years). MOT tests ≠ prediction events ≠ vehicles ≠ defect observations ≠ catalogue
items — the five counts differ by construction and are separately stated in Q1.

## §2 What has materially changed

| Axis | Old substrate | New lake | Delta |
|---|---|---|---|
| Test rows | 24,346,087 (target-scoped) | 681,724,337 | 28× |
| Vehicles | 2,296,105 | 72,037,010 | 31× |
| Item rows | 413,955,229 (2019–2023 only) | 1,289,329,470 (2005–2023) | +875,374,416 pre-2019 (+211%) |
| Item fields | 5 (no severity ladder use, no position use) | + corrected severity ladder + dangerous_mark + location_id | new classes |
| test_type | NULL for 2016–2018 canonical rows; absent pre-2016 | 0 nulls, all rows | population rule direct |
| 2024–2025 | cal 2024-Q4 + H1 2025-H1 surfaces | ABSENT | −2 years recency |
| Provenance | multi-vintage composition, 422,070 dups removed, identity gate | single-release, 0 dup test_ids, external MOT-01 validation | canonical |

More rows vs richer information: items pre-2019 + severity + position + fleet-scale
priors are category-(2) richer information; the 28× row expansion is category-(1)
except where it lifts rare-cohort support. The recency loss is a category-(2) REGRESSION.
test_id-space compatibility: VERIFIED IDENTICAL (1,237,152 matched ids, 100.000000%
test_date agreement — material finding 6). Vehicle-level linkage across substrates
remains out of bounds (vintage-incompatible vehicle_id); test_id-level joins are the
sanctioned reconciliation path.

## §3 Longitudinal depth

Source: 1/100 vehicle-hash panel (abs(hash(vehicle_id))%100==0, duckdb 1.5.5 pinned),
all 19 years: 6,815,649 test rows / 719,951 vehicles / 5,424,902 NT+definitive
events; validated per year against exact anchors within the preregistered ±0.8% band
(`out/panel_validation.json` — ALL PASS, max per-year deviation 0.27%). History
metrics are D13-safe: priors = strictly earlier calendar days; same-day co-tests are
flagged (8.5–10.8% of panel events sit on multi-test days, peaking 2010–2015;
exact full-year vehicle-day shares decline 9.34%→7.76% over 2015–2023), never ordered.

Priors available at prediction events, by calendar year of event (panel):

| Event year | events (panel) | mean priors | p50 | p90 | p99 | mean history-years | clean-cohort share |
|---|---|---|---|---|---|---|---|
| 2006 | 253,198 | 0.29 | 0 | 1 | 2 | 0.9 | 0.1% |
| 2010 | 280,695 | 3.86 | 4 | 7 | 9 | 3.4 | 29.0% |
| 2015 | 296,546 | 6.78 | 7 | 12 | 16 | 6.0 | 62.4% |
| 2019 | 318,178 | 7.37 | 7 | 15 | 20 | 6.7 | 83.6% |
| 2023 | 347,849 | 8.20 | 7 | 17 | 24 | 7.3 | 92.7% |

2023 depth mix (all events / clean cohort): 0 priors 5.9%/6.3% · 1 6.9%/7.4% ·
2 7.1%/7.6% · 3–5 21.5%/23.0% · 6–10 26.2%/28.0% · 11–20 28.8%/26.6% · 21+
3.7%/1.1%. The 21+ tail outside the clean cohort is partly pre-2005-registered
vehicles with long windows — depth claims for very deep histories should quote the
clean-cohort column. Inter-test gaps at 2023 events: p50 364 days, 86.8% in the
annual band [300, 430]; prior-mileage ladder p50 = 7 readings (unit caveat §9).
Defect-history depth at events: mean prior items 5.6 (2010) → 10.4 (2019) → 12.3
(2023), p90 33 (2023); share with ANY prior defect-bearing test-day 71.4% (2010) →
80.6% (2023); with ≥3 such days 29.0% → 52.9%. Full tables:
`out/panel_depth_by_year.json`, `out/panel_defect_depth.json`,
`out/panel_gaps_mileage.json`.

Left-censoring: depth at fixed calendar year is bounded by the 2005 window (a 2007
event can see ≤2 years; a 2023 event ≤18) — the 2006 row above is structural, not
behavioural. Structural depth is therefore reported (a) by event year, (b)
conditional on observable window, (c) on the clean cohort first_use_date ≥ 2005
(92.7% of 2023 events). 2005 itself is a partial year and excluded from trends.

Present-day deep-history population (the §3 headline, ×100 with vehicle-bootstrap
95% CIs): of ~34.78M [34.66M, 34.91M] 2023 prediction events — **≥6 prior initial
tests: 18.83M [18.74M, 18.92M] (54.1%)**; ≥10: 10.26M [10.19M, 10.32M] (29.5%);
**≥3 prior defect-bearing test-days: 18.40M [18.31M, 18.48M] (52.9%)**. The
substantial multi-test defect-history population Henri asked about is therefore
roughly half of all present-day prediction events.

## §4 Defect data

A defect observation = one item row: (test_id, rfr_id, rfr_type_code, location_id,
dangerous_mark) + 7 derived columns. No free text; no timestamps beyond the parent
test date; no tester identity. Identifiers: rfr_id joins the catalogue (100.000% of
all 1.29B rows join the cumulative 2023-vintage catalogue at any-class level;
98.6–99.1% at class-4 level); catalogue text fields (rfr_desc, insp manual desc,
advisory text) exist but are NOT loaded by the pipeline.

Historical availability by field:

| Field | Reliable from | Notes |
|---|---|---|
| rfr_id, rfr_type_code, location_id | 2005 (items exist from lake start; 2005 partial) | code SPACE changes 2018-05-20 (disjoint) |
| rfr_type_code semantics | two regimes | pre: F/P/A (case-insensitive); post: F/P/A/M with **M = Minor (F-22)**; D and lowercase m never occur |
| dangerous_mark | 2018-05-20 | pre-2018 layout has no column (items_legacy); post: 'D' on 22,292,034 rows (4.85%), else null/blank |
| severity ladder | post-2018 only | dangerous = mark='D'; major = F not D-marked; minor = M; PRS-item = P; advisory = A. Pre-2018 ladder = F/P/A only |
| component_category (derived) | both eras | class-4 map only; corrected-fail coverage pre 88.3% / post 91.0% (certified post 92.6% under inverted mapping) — gap = non-class-4 rfr_ids + 11 deliberately unmapped sections; genuinely-unknown names = 0 |

Prevalence vs recording: advisory volumes rise strongly through 2005–2014 (2.9M →
39.7M/yr) while fail items are ~flat (~34–35M/yr) — advisory growth is at least partly
recording behaviour, not vehicle behaviour; the 2018 step introduces minors
(2.9M→6.6M/yr 2018→2023) and the corrected fail series drops ~18% relative to the
certified one from 2018 (exact per-year table: `out/items_census.json`). Treat any
cross-2018 defect-rate trend as regime-confounded unless computed on the corrected
ladder within era, or at category level across eras.

## §5 Defect taxonomy and continuity

- Codes are NOT stable across 2018-05-20: class-4 spaces disjoint (2,511 pre-EU /
  1,921 post-EU, overlap 0 — verified from the catalogue; `out/catalogue_guards.json`).
  An rfr_id therefore identifies its own era; but post-2018-dated tests carry codes
  from BOTH trees (2,538 class-4 codes observed on post-2018 rows), so era attribution
  must come from the parent test date (as ingested), never the code space.
- Same code changing meaning: none observed (spaces disjoint; within class-4 each
  rfr_id maps to exactly one deficiency category — `rfr_id_multi_deficiency_within_
  class4 = 0`). Across classes, rfr_id is REUSED (5,768 multi-class ids) — any
  catalogue join must be class-scoped or DISTINCT-mapped (guards enforced here).
- Multiple historical codes → one modern concept: yes, via the section hierarchy. Two
  parallel top-level trees (legacy 5xxx / post-2018 2xxxx) resolve to 26 top-level
  names → 7 component categories (`rfr_mapping._SECTION_TO_CATEGORY` carries both
  name-sets; "Road Wheels"→"Wheels" normalisation).
- New categories from regulation: dangerous/major/minor exist only post-2018 (EU
  roadworthiness directive). Category-level prevalence changes at the boundary are
  recording-regime, not vehicle behaviour (§4).
- **Defensible stable hierarchy**: raw rfr_id → deficiency class (era-aware, corrected
  per F-22) → top-level section (item_name) → 7 component categories. Code-level
  longitudinal features must be era-scoped; category-level features may span 2005–2023;
  severity-graded features are post-2018-only by construction. A validated cross-time
  code map does NOT exist and cannot (disjoint spaces); the section/category level is
  the correct aggregation and is already exact (zero unmapped names, deliberate
  exclusions enumerated in the guards file).

## §6 Outcomes and prevalence

Definition is era-stable: identical NT/RT/PL/PV/ES + P/F/PRS/ABA/ABR/ABRVE vocabulary
in guide v4 (archived, sha-pinned) and v5.1; `OUTCOME_MAP` has no era branch; UNKNOWN =
0 lake-wide; PL/PV never occur; EI = 335 rows, 2023 only. The 2018 boundary changes
ITEM taxonomy, not test outcomes — for the label this is terminology-stable with a
real distributional drift, not a definitional break.

C3&4 initial (F+PRS) and final (F) rates by year — exact, full-population, via the
rule-of-record SQL twins (2015–2023: `out/results_rates.json`; 2005–2014:
`out/parked_year_profiles.json`):

| Year | NT+definitive | Final % | Initial % | External comparator |
|---|---|---|---|---|
| 2005 | 5,557,154 | 28.83 | 34.72 | none (partial year; MOT-01 starts FY2013-14) |
| 2006 | 23,906,955 | 28.01 | 35.33 | none |
| 2007 | 24,880,793 | 29.27 | 37.96 | none |
| 2008 | 25,371,862 | 29.79 | 39.50 | none |
| 2009 | 25,868,653 | 30.91 | 40.61 | none — peak failure era |
| 2010 | 26,440,984 | 30.42 | 39.86 | none |
| 2011 | 26,889,404 | 30.42 | 39.97 | none |
| 2012 | 26,949,835 | 30.20 | 39.79 | none |
| 2013 | 27,307,001 | 30.46 | 39.89 | gated from FY2013-14 |
| 2014 | 27,565,640 | 29.57 | 38.79 | gated FY window |
| 2015 | 27,943,257 | 28.13 | 37.12 | gated FY window |
| 2016 | 28,397,176 | 27.16 | 35.82 | gated FY window |
| 2017 | 28,832,081 | 26.36 | 34.65 | gated FY window |
| 2018 | 29,359,810 | 26.25 | 33.91 | gated FY window |
| 2019 | 30,114,194 | 24.93 | 32.15 | COVID carve-out (reported, not gated) |
| 2020 | 30,039,081 | 23.14 | 29.79 | COVID carve-out |
| 2021 | 31,311,275 | 23.21 | 29.46 | gated FY window |
| 2022 | 32,435,945 | 22.52 | 28.54 | gated FY window |
| 2023 | 32,811,888 | 22.82 | 28.55 | FY2023 incomplete in lake (Q4 outside) |

The failure-rate arc is real and large: final-basis 28.8% (2005) rises to a 30.9%
peak (2009), then declines monotonically to ~22.5–22.8% (2022–23) — a 8pp secular
swing any 19-year model must confront (era weighting / recency decisions, Q5.1). Composition: PRS flip rate 8.14% all-time (peak 9.71% 2008 →
5.73% 2023) with the decisive age gradient (0–2yr: PRS = 38.19% of initial failures vs
16.60% at 15+) — D12 brief. Modelling implication: final-basis label is consistent
2005–2023; the initial-basis alternative changes the target most where evidence is
thinnest (young vehicles); pre-FY2013 rates carry no external corroboration and 2005
is partial — flag, don't weight, unless §9 says otherwise.

## §7 Join integrity

- test_id: 0 duplicates over 681,724,337 (authoritative re-verify, full 19-year lake,
  today 09:13 BST — `logs/gate_reverify.log`; manifest timestamp is the same event in
  UTC). Duplicate test_id now raises at ingest (D13-era hardening).
- items→results: inner-joined at ingest with year-scoped hints; therefore match rate
  is 100% by construction and *not evidence about the source*. Orphan volume for
  2005–2018 is unrecoverable (raw deleted). Only independent bound: the old 2019–2023
  item ingest differs by −175 rows total (−5/2021, −167/2022, −3/2023) ≈ 4×10⁻⁷ —
  consistent with dup-handling differences, not systematic loss.
- one-to-many shape: items-per-test-with-items p50 2–3 / p99 13–14 / max 49–69,
  stable across 19 years; share of tests with ≥1 item rises 53.5% (2010) → 59.2%
  (2019) → 60.4% (2023) — advisory-recording growth, §4.
- Same-day glossary (four different denominators — do not conflate):
  (a) multi-test **vehicle-days**: 9.34%→7.76% of vehicle-days (2015→2023, exact);
  (b) F7a **tie-exposed targets**: 37.24% of fulldepth targets-with-priors have a tie
  anywhere in history; (c) **~24%** = share of frame feature-vectors that flip under
  tie reversal (F7a scaled; metric-null per F7b); (d) panel **events on multi-test
  days**: 8.5–10.8% by year. All are real; they measure different things.
- Vehicle-sequence plausibility: continuity (pinned checks.py definition) on ALL
  panel multi-test (≥3) vehicles — **n=628,177: multiyear_share 0.9978,
  first_use_conflict_share 0.00888, median gap 361d** — closes the chartered n≥50k
  re-verification (the certified n=10k run could not resolve 0.0078 against the 0.01
  bar; Wilson upper 0.01083; at n=628k the share is confidently below the bar). The
  conflict share's appearance with 2018+ releases (schema_epoch results_csv) remains
  attributed-not-resolved: cross-release duplication vs organic first_use corrections.
- Old↔new identity: VERIFIED via test_id (finding 6) — 1,237,152 matches, 100.000000%
  date agreement; the 6.37× vehicle-overlap enrichment is a research-side sampling
  correlation, documented in `out/panel_identity_check.json`.

## §8 Missingness and information availability

Field-level missingness is NEAR-ZERO for most results columns across all 19 years
(colour/fuel_type: 6 nulls total in 354M local rows; make/model/postcode_area: 0
nulls and 0 blanks; panel-based parked-year completeness matches —
`out/panel_completeness.json`): the dominant missingness structure is *regime*, not
noise:

| Field | Missing pattern | Class |
|---|---|---|
| test_mileage | ~0.8–1.1%/yr null + 2020 spike (1,007,292 = 2.6%, COVID) + zero-values | genuinely unknown (aborted/unreadable) + period effect |
| cylinder_capacity | nulls RISING 42.5k (2015) → 279,982 (2023) | structurally not-applicable (EV growth) — informative missingness, keep |
| first_use_date | ~0.001%/yr + 2021 spike 7,251; sanitised at ingest (pre-1900 + future-dated → null, `age_source` records it) | mixed; conflicts are a JOIN artifact (§7) |
| dangerous_mark | 100% pre-2018 (column absent in source layout) | structurally unavailable — era marker |
| items pre-2019 in TRAINING FRAMES | DQ-01 fabricated zeros (old substrate) | pipeline artifact — REPAIRED by this lake |
| 999/10000/−1 sentinels | FRAME-level encodings (old research frames), not lake values | do not attribute to the lake |

Can a model infer the period/regime from missingness? **Yes, trivially** — this needs
no classifier: schema_epoch is a stored column (mts 2015–17 / csv 2018–21 / mts
2022–23 exactly); dangerous_mark presence fingerprints the 2018 boundary;
cylinder_capacity nulls trend with EV share; the mileage-null 2020 spike marks COVID.
Attribution table above = which masks fingerprint which boundary. Implication for
training: regime information will enter any model fitted across 2005–2023 whether or
not it is given explicitly; §9 recommends making it explicit (indicator) rather than
laundering it through missingness — and excluding schema_epoch/taxonomy_era from
features unless deliberately chosen.

## §9 Temporal consistency and regime change

| Discontinuity | Evidence | Modelling implication |
|---|---|---|
| 2005–2006Q1 computerisation ramp (2005 = 7.5M rows) | DVSA guide (both versions); year_volumes | historical data COMPARABLE from 2006-04; exclude/flag 2005 in trends; harmless for features (depth conditioning handles it) |
| 2018-05-20 defect taxonomy (codes disjoint, severity classes new, dangerous_mark appears) | §5 | FEATURE MAPPING REQUIRED (category level across eras; severity post-only); era indicator justified |
| F-22 inverted severity (derived cols, post-2018) | finding 2 | corrected overlay until post-G2 fix; never train on is_fail_item/is_dangerous as stored |
| Pre-2022 mileage km contamination; corrected upstream from 2022 dataset | DVSA guide caveat (source contract §D5) | mileage features need unit-robust design; 2022 step is UPSTREAM correction, not behaviour; investigate before using longitudinal mileage deltas |
| Fuel-type Prius/hybrid mis-coding corrected from 2022 | source contract §D8 | fuel_type is vintage-scoped pre-2022 for hybrids; minor, flag |
| COVID 2020 (Q2 volume −47%; 6-month extensions) | published gate quarterly decomposition | distributional shift, definition unchanged; carve-out for eval windows; weighting decision for training (open) |
| Release-vintage epochs (gz/mts vs csv escape dialects; schema_epoch column) | §8 | pipeline artifact, fully explicit; EXCLUDE from features; harmless otherwise |
| EI test type 2023-only (335 rows) | census | harmless (excluded from population by rule) |
| location_id coding granularity 90 codes (2005–12) → 114 (2015) → 60 (2017+) | `out/items_location.json` | recording-practice regime; positional features must use a code MAP stable across the granularity change, or era-scope |
| model_id cardinality 111k→66k (2015→2023) | `out/results_entity_coverage.json` | upstream make/model consolidation; model-grain EB priors are cleaner recent-years |
| Test fee/frequency regulation (2018 40-year exemption etc.) | NOT documented in-repo | investigate only if age-cohort composition anomalies appear |
| Failure-rate secular decline (C3&4 final 28.1%→22.8% over 2015–23) | §6 | real drift; era weighting/recency decisions belong to the programme (Q5.1) |

## §10 Information available at prediction time

Lake ↔ live-API field matrix (source contract + dvsa_client.py field-capture audit):

| Information | In lake | At serving (live API) | Status |
|---|---|---|---|
| completedDate | date only | date + time (completed_at parsed on the UNDEPLOYED D13 tier-2 branch; origin/main truncates to date) | serving richer; deploy pending |
| test outcome | 7-value vocabulary incl. PRS + aborts | testResult PASSED/FAILED only | **PRS not confirmed recoverable live** (OpenAPI defect enum omits PRS; bulk prose contradicts; UNKNOWN/CONFLICTING). Aborted/abandoned presumed absent live |
| test_type (NT/RT/…) | all rows | ABSENT | population rule unavailable at serving; serving must assume "upcoming initial test" |
| test_class_id, postcode_area | yes | ABSENT (postcode is user-supplied, different concept) | lake-only |
| mileage | value, no unit, pre-2022 km contamination | odometerValue + odometerUnit + odometerResultType | serving strictly richer |
| vehicle identity | anonymised vehicle_id | VRM (queried), no vehicle_id | no shared key |
| test key | test_id | motTestNumber | no documented mapping (source contract D1) |
| defects | rfr_id + type code + location_id + dangerous_mark; NO text | free text + type words (DANGEROUS/MAJOR/MINOR/ADVISORY/…) + dangerous bool (currently discarded by parser); NO ids | disjoint encodings; bridge = severity-word + dangerous-flag level (exists) or reverse-catalogue text match (unbuilt, feasible in principle — closed vocabulary) |
| first_use_date | yes (sanitised) | registrationDate / manufactureDate (different fields) | age semantics differ train/serve |
| engineSize/colour/fuelType | yes | received, unused by features | available both |

Deployable-now information: defect severity WORDS + dangerous boolean + counts +
dates + odometer(+unit). Analytically-valuable but serving-blocked without a bridge:
rfr_id-grain recurrence, location_id, test_type-derived, class/area. This section
deliberately decides nothing — it makes the boundary visible (bridges are Q5.5).

## §11 Relationship to the existing feature set

By family (104 serving / 215 research contract; COVERAGE_MAP importance shares):

| Family (imp%) | New-substrate effect |
|---|---|
| eb_prior_override (26.9), prior_test_agg (16.5), lag_sequence (11.7), history_window (2.8) | REPRODUCED + completeness-improved (full-depth priors for the whole fleet; depth 2.32→7.89 already banked at frame level); D13 day-grain definitions supersede tie-dependent ones (metric-null per F7b) |
| mileage_usage (8.9) | CALCULATION CAVEAT: pre-2022 unit ambiguity now documented; better direct measure NOT available in lake (no unit col) — serving has more |
| defect_section (5.9) + defect_cumulative (4.6) + prior_defect_agg (1.9) | REPAIRED + EXTENDED: DQ-01 fabricated zeros eliminated; 14 extra years of items; corrected severity mandatory; sec*/cs_* read raw codes (unaffected by F-22) but cs_major/cs_dangerous key on rfr_deficiency_category (era-bound — needs the era-scoped rule) |
| advisory_component (1.3) + advisory_summary (1.6) + failure_component (0.3) | DEEPER HISTORY (pre-2019 items) + train/serve issue stands: training used flag tables, serving keyword-matches text; suspension+steering conflated in training — unchanged by the lake, needs the D1-repaired vocabulary + a single component rule |
| station_strictness (5.0) | OBSOLETE-at-source: station identity unavailable in the anonymised release by design (k-anonymised); postcode_area is VTS region, not keeper |
| text_nlp (0.6) / 17 prod text feats | unchanged verdict (closed-vocabulary; −0.000044 shipped value); lake adds NO text |
| geo_external (2.8), vehicle_spec (3.2), timing_seasonality (2.3), negligence (0.7), mech_decay (0.7), mechanism_summary (0.9) | reproduced; spec fields complete (colour/fuel ~0 nulls); cylinder_capacity nulls = EV signal (keep as informative) |

Blind spots — information in the lake the current feature set does not represent AT
ALL: (1) pre-2019 item-grain history (875M rows); (2) severity ladder incl.
dangerous_mark; (3) location_id; (4) rfr_id-grain recurrence (same-defect-repeats);
(5) test_type-conditional history (e.g. prior-retest patterns — with the serving
caveat); (6) fleet-scale priors. Kill-crosswalk: R56 item family = serving-dead kill →
partially reopened at the severity-word/dangerous level ONLY (rfr_id grain stays
serving-blocked); tyre item-state = info-bound on 2019+ window → premise changed,
re-test justified; mechanism R62 + EF-1 + adv-resolution = design/leak kills → stand
unless the new information class changes the mechanism, falsifiers mandatory
(`index_autosafe_closed` consulted).

## §12 Potential information leakage → proposed hard gates

Risk register (all pre-training gates, none blocking this assessment):

1. **As-of attribute joins** — mutable attributes (postcode_area, fuel, model_id)
   must join as-of the event date (fulldepth pipeline already does; keep as gate).
2. **Target-event exclusion** — every history aggregate strictly excludes the event
   day (D13 day-grain: priors = dates < event date; same-day co-tests are AMBIGUOUS,
   never priors). This assessment's §3 metrics follow that rule.
3. **Strict-date EB priors** — refit priors inside folds/eras (prior_rebuild lesson);
   never reuse full-period aggregates.
4. **Vacuous guards must be replaced before they are relied on**: time_travel_test
   compares a prior with itself (can never fail); property_tests leakage tolerance
   0.20 with a diluted denominator; the product no-bare-test_id gate is
   comment-satisfiable; published_stats_gate is wired into no runner (ad-hoc only).
5. **Parity phase-A gap** — lake→packets is unverified by the standing parity gate
   (scope erratum); close before any new training frame is trusted.
6. **Parked-year completeness gate** — any full-depth training build must first
   restore 2005–2014, re-verify zero-dup + continuity on the assembled lake, and
   record the manifest state (this assessment's rotation left end-state = start-state).
7. **Tie-rule/F7b** — closed (metric-null, FINAL); D13 semantics remain mandatory for
   any rebuilt cycles/features (value-counterfactuals per F-19 if re-audited).
8. **Regime fingerprints** — schema_epoch/taxonomy_era excluded from features unless
   deliberately included as regime indicators (§8/§9).
9. **Retrospective fields** — first_use_date is sanitised at ingest (future-dated →
   null) — do not re-derive age from raw sources; dangerous_mark and rfr codes are
   recorded at test time (no post-test update mechanism exists in the anonymised
   release; amendments/deletions semantics live only API-side per source contract A10).

## §13 Population coverage

Panel cohorts, 2019+ events (full tables `out/panel_cohorts.json`; min-cell rule:
cells <500 panel events flagged, no claims — flagged: age `unknown`, six rare fuel
codes LP/GB/GD/FC/CN/GA/LN):

| Cohort | n (panel) | final-fail rate | mean priors |
|---|---|---|---|
| age 0–2 | 110,022 | 0.081 | 0.1 |
| age 3–5 | 397,310 | 0.123 | 2.0 |
| age 6–9 | 454,890 | 0.212 | 5.8 |
| age 10–14 | 428,428 | 0.319 | 11.4 |
| age 15+ | 266,546 | 0.344 | 16.4 |
| depth 0 priors | 135,773 | 0.091 | — |
| depth 6–10 | 410,676 | 0.248 | — |
| depth 21+ | 36,297 | 0.481 | — |
| FORD | 234,425 | 0.253 | — |
| VAUXHALL | 161,301 | 0.276 | — |
| BMW | 87,999 | 0.178 | — |
| petrol (PE) | 879,992 | 0.225 | — |
| diesel (DI) | 739,248 | 0.245 | — |
| hybrid (HY) | 29,318 | 0.094 | — |
| electric (EL) | 6,250 | 0.134 | — |

Well-represented: mainstream makes, ages 3–15+, depths 0–20 — all at ≥10⁵ panel
events (≥10⁷ population). Sparse but usable: EVs (6,250 panel ≈ 625k population,
2019+), 21+ depth (36,297 ≈ 3.6M). Structurally weak: age 0–2 has near-zero history
by construction (first MOT at 3 years — the true-first cohort); rare fuels flagged;
non-class-4 classes carry no class-scoped category map (§4); model-grain cohorting
is feasible recent-years (model_id cardinality consolidated 111k→66k over 2015–2023,
an upstream recording change) but noisy early. Known-in-advance sparse/absent populations: pre-2006Q1 tests (partial);
non-class-4 classes are present in results (5.4% of rows) but the component-category
map is class-4-only (items for other classes categorise at any-class catalogue level
only); station-level anything (absent by design); NI (GB release only); vehicles ≤3
years old appear only via early presentations (first MOT at 3 years) — the true-first
cohort's depth is structurally 0.

## §14 Scale and computational implications

Intrinsic: 681.7M results × 19 cols + 1.29B items × 12 cols ≈ 14 GB zstd parquet;
candidate training-row count = the prediction-event population (542,542,126
all-class 19y; 511,982,988 C3&4) — two-plus orders of magnitude beyond every frame
used to date (782k–2.1M). Feature cardinalities: make ~9–11k raw strings/yr,
model_id 66k–111k/yr (consolidating), rfr_id 7,505 (4,432 class-4), categories 7,
location_id ≤130 (90→60 over time). Event-table size for item-grain longitudinal
work: 1.29B rows keyed by 660M+ tests.
Local (this machine): 8 GB RAM / ~15 GiB disk means full-population training is
out of reach locally at classical gradient-boosting scale without sampling; the s60
pattern (~780k rows) used ~4 GB. duckdb scans/aggregations of the full lake are
CHEAP (seconds); the binding local costs are joins at items grain (>7 GiB spill
unscoped — use year-scoped joins) and any per-vehicle global sort. These are
machine constraints, not dataset properties: do not let 8 GB decide the architecture
question; decide sample-size-vs-capacity on the science (Stage-5-style nested-sample
curves), then choose hardware.

---

*Prepared under the approved plan (establish-what-we-now-swirling-fox); analysis log,
prereg shas and deviations in ANALYSIS_LOG.md; all outputs in out/. Panel scratch
artifacts (shards, items join, running-distinct) live in the session scratchpad;
the lake's end-state equals its pre-assessment state (2005–2014 parked, sentinel
untouched, no lake writes).*

---

# Addendum A — 2024 and 2025 anonymised extracts EXIST (correction, 2026-08-12 ~16:00)

Triggered by an external technical review and verified directly this session:

- DVSA split publication in **January 2025**: the DfT dataset this lake was built
  from (data.gov.uk id e3939ef8…, data.dft.gov.uk URLs) is the HISTORICAL series and
  genuinely ends at full-year 2023 — the v58 probe of 2026-08-11 measured that
  dataset correctly. New years are published under DVSA's own portal
  (`open.data.dvsa.gov.uk/mot-anonymised/`) and a separate data.gov.uk dataset entry.
- Verified inventory on the DVSA portal (fetched 2026-08-12): **"MOT testing data
  results extracts (2025)"** and **"failure item extracts (2025)"**, both added
  **22 June 2026**; **TWO 2024 versions** — "results (2024)"/"failure item (2024)"
  added 07 March 2025, and newer "results extracts (2024)"/"failure item extracts
  (2024)" added 22 June 2026 (supersession UNRESOLVED — must be reconciled by hash +
  row counts + schema before any ingest); refreshed user guides + lookup tables
  (12 May 2025) and six supporting lookup CSVs (22 June 2026).

Consequences:
1. The recency cliff shrinks from ~2.5 years to ~7 months. Q3.1's framing and
   Q5.1's option set are superseded: the primary lane is now RECONCILE + EXTEND
   the lake to 2005–2025 through the existing pipeline (schema detection is
   fail-loud set-equality — a changed 2024/2025 layout will surface immediately;
   the MOT-01 comparator needs a refresh for FY2023-24/FY2024-25 before those
   gates can bind; the two-2024-versions question is a hard prerequisite).
2. New full-population 2024/2025 evaluation surfaces become buildable. Their
   exposure status (this programme has repeatedly read fresh-sample 2024-Q4 and
   2025-H1/H2 surfaces covering the same periods) is a LEDGER adjudication, not a
   default: period-level adaptation is real even where vehicles differ.
3. The 22-June-2026 lookup CSVs partially mitigate the lookup single-point-of-
   failure (finding 5): upstream copies exist again; the local-vintage (2023-03)
   vs new-vintage reconciliation is still required before mixing.
4. Nothing in-lake changes: every count, rate, and structural finding in this
   assessment describes the certified 2005–2023 lake and stands.

Lesson recorded: "the release ends at 2023" was verified against one publisher
surface and generalised to the world. Existence claims about external data need a
publisher-migration check (who publishes NOW, not just where it was last found).
