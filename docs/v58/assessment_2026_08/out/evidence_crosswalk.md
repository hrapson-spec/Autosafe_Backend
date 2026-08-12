# Evidence crosswalk — 14 assessment sections → primary sources (Phase 1)

All paths relative to the two branch checkouts: `v58` = `~/autosafe-v58` (branch
claude/autosave-defects-history-xqutcw @ 1a2b75a), `d7` = `~/autosafe-d7replace`
(branch d7/dvsa-initial-test-population @ 31d77d4), `res` = `~/autosafe` research
repo (history-coverage-rebuild-20260706 @ 38203e4, read-only). Vintage labels:
[19y] = post-2022-backfill (authoritative), [18y] = pre-backfill (superseded),
[research-window] = old substrate scope, not the new lake.

Facts verified first-hand this session (not agent-relayed): anchors (out/anchors.json,
all PASS); F-22 verdict + census (d7 evidence/n18/#18_F22_DECIDER.md:144-181 — census
sums to 1,289,329,470 exactly; certified failing denominator 594,200,636 reproduces:
pre 420,457,262 + post 173,743,374 incl. 31,748,964 'M'); F7b Addendum 3 (v58
evidence/INVARIANTS_AUDIT_2026_08_12.md — ΔAUC −5e-7/+2e-7, FINAL, absolutes survive;
bakeoff_2026 recoverable on Drive); era×test_type census [18y] header confirms
pre-backfill vintage; D12 brief §1 All row = 506,425,834 NT / 41,221,950 PRS / 8.14%;
continuity timestamp discrepancy RESOLVED (manifest 08:13:41Z = log 09:13:41 BST,
one event, includes 2022 — input 681,724,337 rows).

| § | Section | Primary evidence |
|---|---|---|
| 1 | Overall dataset | anchors.json (footer sums); lake_manifest.json sources+checks; v58 evidence/download_record.md (parked per-year); D12 brief [19y] (NT counts); Phase-2 census [19y] supersedes tp.py:36-40 literals [18y] + era_testtype census [18y] |
| 2 | What changed | res substrate map: test_grain_history_v2 (24.3M rows / 2.30M vehicles) vs lake; old items = PROVENANCE_AUDIT_test_items_loc_lake.md (413,955,229, 2019–2023); Δ−175 vs new same-years; DQ-01 (res DATA_QUALITY_REGISTER.md); windows: FULLDEPTH_DUMP_MANIFEST + valspine_v1.json; recency: NOTES.md:26-41 release-ends-2023 |
| 3 | Longitudinal depth | Phase-3/4 panel (1/100 hash, duckdb 1.5.5 pinned); left-censoring via first_use≥2005 cohort; D4_HISTORY_DEPTH_ASSESSMENT [research-window] for the OLD depth (0.819 cycles/yr, cap-5y) |
| 4 | Defect data | v58 pipeline/lake/schemas.py:54 (5 source cols) + ingest_items.py (7 derived); lookup CSVs (~/autosafe_raw/lookup — SPOF); F-22 verdict [19y]; dangerous_mark: old audit 94.6-95.6% null [research-window], Phase-2 census [19y]; DVSA source contract §D5 mileage km caveat, §C7 2005-06Q1 coverage |
| 5 | Taxonomy continuity | rfr_mapping.py; item_detail disjoint rfr_id spaces (2,511 pre / 1,921 post, overlap 0); two parallel section trees; F-22 corrected severity (post-2018 fail = F+P only; D/M inside F undistinguishable except dangerous_mark); OPEN_QUEUE scope corrections (sec*/cs_* raw-code reads; cs_major/cs_dangerous key on rfr_deficiency_category) |
| 6 | Outcomes/prevalence | published_stats_gate.py + dvsa_mot01_reference.py (FY2013+ comparator ONLY; 8 FYs gated; COVID reported-not-gated); D12 brief [19y]; Phase-2 rates [19y]; pre-FY2013 = externally UNCORROBORATED |
| 7 | Join integrity | gate_reverify.log (0 dup test_ids, 19y, 09:13 BST); items inner-joined at ingest → coverage 100% BY CONSTRUCTION, orphan volume 2005–2018 unrecoverable (raw deleted); Δ−175 (2019–23 bound); continuity 0.998/0.0078/361d n=10k (underpowered vs 0.01 bar — Wilson upper 0.01083; panel closes at n≥50k); F-22 census cross-sums exact |
| 8 | Missingness | Phase-2 per-column × year; schema_epoch census; DVSA-documented field regimes: mileage km pre-2022, Prius/hybrid fuel mis-coding pre-2022, UNCLASSIFIED make/model; 999/10000/−1 sentinels are FRAME-level (res CENSUS_NOTES), not lake-level — keep separate |
| 9 | Regime change | 2018-05-20 taxonomy (disjoint codes; dangerous_mark only post; M=minor); 2022 mileage unit correction; 2005–06Q1 computerisation ramp; COVID (2020-Q2 5.36M vs Q1 10.1M); EI 2023-only (335); gz→zip + escape dialect epochs (schema_epoch); first_use conflict 0→0.0078 at 2018+ releases (attribution open) |
| 10 | Prediction-time availability | source contract §§A11,B3-B5,C7,D1-D8: no shared test key (test_id↔motTestNumber UNKNOWN), no shared defect key (rfr_id absent from API; text absent from lake), PRS at serving UNKNOWN/CONFLICTING, station unavailable by design, live API richer on mileage (unit+resultType); dvsa_client.py field-capture audit; D13 tier-2 NOT deployed |
| 11 | Feature mapping | res COVERAGE_MAP.md (20 families × importance), contract_215.json, feature_engineering_v55.py:43 (104), CENSUS_NOTES sentinels + duplicates; DQ-01 repair; kill-crosswalk vs index_autosafe_closed |
| 12 | Leakage risks | vacuous time_travel_test (as_of_validation.py:186-198); property_tests <0.20; comment-satisfiable test_id gate; published_stats_gate unwired; F7a/F7b arc + F-19 (value-counterfactuals, not order shuffles); EB priors strict-date lesson; parity phase-A gap |
| 13 | Population coverage | Phase-4 cohorts; class mix 0.946; make UNCLASSIFIED; 2005 partial |
| 14 | Scale | sizing (26.22GB corpus, ~14GB lake); measured query times (13-40s/yr scoped; F-22 full census 10.9s at 2GB); 8GB box = LOCAL constraint; 681.7M×19 + 1.29B×12 = intrinsic |

Key corrected-severity overlay (from F-22 verdict, used everywhere post-2018):
fail-bearing = {F, P}; M = minor (non-fail); D absent (dangerous carried inside F;
only signal = dangerous_mark). Certified failing 594,200,636 → corrected 562,451,672.
