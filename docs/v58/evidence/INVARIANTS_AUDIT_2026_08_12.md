# AutoSafe targeted invariants audit — final report

Date: 2026-08-12. Lead: invariants-audit session (Opus). Team: 7 Sonnet sub-agents
(2 reconnaissance + workstreams A–F) + lead-executed falsifiers. Product-repo
citations against `90a6cb0`/`7754b10` (evidence-only commits landed mid-audit; no
audited code file changed). Research-repo citations against `publication-metrics-v1`.
Coordination: live message exchange with the `d7-cycles-dvsa-test-type` session
throughout; two of this audit's findings were adopted there mid-audit (Defect #18
registration; 2022 root-cause correction) and one was measured on real data at this
audit's request.

---

## 1. Headline verdict

**MATERIAL SIBLINGS FOUND.**

At least one additional defect of the same class as Defect #16 was found, proven on
real data, and quantified (Defect #18: same-day retest mis-ordering, both
implementations, D7 rate −1.26pp). Several sibling hazards were demonstrated at the
mechanism level. None of them, on current evidence, invalidates a previously
*published* scientific conclusion — largely because (a) nothing downstream of the
defective product-repo cycles code was ever shipped, (b) the pre-2026-07-31 research
absolutes were already quarantined by the standing benchmark freeze, and (c) the D7
target replacement (this morning, commits `5510d7f`..`a70cc64`) removed the largest
exposure by construction.

This verdict was chosen against a discriminator pre-registered before the falsifiers
ran (§10). The single open path to SCIENTIFIC VALIDITY AT RISK is falsifier F7
(unexecuted, machine-constrained): whether the unexamined same-day tie rule in the
research repo's canonical fulldepth substrate moves any reported metric beyond the
0.002052 materiality floor. Until F7 runs, fulldepth-substrate *absolute* AUCs carry
a bounded caveat; fulldepth-substrate *comparisons* (TabM vs CB vs RealMLP deltas) do
not, because the substrate is deterministic and every arm saw the same realization.

## 2. Executive assessment

Defect #16 was not an isolated implementation mistake. It is one instance of a
repo-spanning pattern: **within-day event order is unknowable from `test_id`, and
both repos repeatedly resolved it with an id tie-break without ever deciding to.**
The audit found the same hidden assumption in the canonical research substrate
(unexamined `p_test_id DESC`), in the serving path (no tie-break at all — DVSA JSON
order decides), in the rule of record itself (`assign_cycles` — proven, Defect #18),
and in the legacy lineages (documented as DQ-19 and the 2026-07-29 freeze defect).
The repos had already measured the enabling fact twice (49.92% / 49.91% = chance)
without connecting it to the code that assumes otherwise.

The second theme is **guards that cannot fire**: a leakage test that compares an
expression with itself; a leakage invariant relaxed to 20% to accommodate the very
ambiguity under audit; a year-volume gate never wired into the runner and
structurally blind to a missing year; a twin-equivalence "test" with hardcoded
friendly expectations; a parity gate that uses the same tie key as the builder it
checks; an idle-gate that excludes the very processes it should count; seeded
sampling that is only reproducible single-threaded. Individually minor; jointly they
explain how #16-class defects survived a 0.997-continuity, delta-0, 100.0000%-parity
evidence trail.

Third: **conservation is asserted nowhere.** No input-vs-output row count crosses any
COPY; the manifest's partition-record mechanism exists but was never populated; a
substring-match resume predicate silently skipped an entire calendar year that was
already downloaded to disk.

What kept this from being worse: the v58 lake's cycles/aggregates were never built or
shipped; the benchmark freeze already quarantined the contaminated era; the episode
rebuild had already invented the correct order-invariant pattern; and the owner
replaced the D7 target with a per-row definition immune to the whole class.

## 3. Team allocation and what each independently established

| Agent | Charge | Independently established |
|---|---|---|
| Recon-1 (product) | pipeline map | Inventories A–E: identifier/window/config/conservation/fixture maps with file:line |
| Recon-2 (research) | feature/label map | `p_test_id DESC` chain; vacuous tests; PRS divergence; lineage inventory; DQ-19/freeze prior art |
| Recon-3 (serving) | serving path | Tie-break-free sort; no PRS handling; D1–D4 record; no model provenance; unsorted display path |
| A | unaudited product files | Trainer/`build_db`/`hierarchical_make_adjustment` clean in-file; D1–D3 absent from origin/main (ref-proven) |
| B | second reader | 8/8 ordering claims CONFIRMED; fan-out NOT absorbed; REPEATABLE threads>1 non-reproducible (doc-cited); preserve_insertion_order = order-only per docs |
| C | research adjudication | `p_test_id DESC` UNEXAMINED; lineage table; PRS = known pre-06-18 bug; no reliance on vacuous tests; bakeoff_2026 deleted with live import dependency |
| D | twins & fixtures | `outcome_expr` whitespace gap (the real twin divergence); band twins clean and well-tested; mileage-pin adjudication; D4 records coherent |
| E | conservation | 2022-skip root cause (log-traced); zip on disk unextracted; year gate never wired; no duplication defense; minimal invariant set |
| F | isolation | stage4↔items unguarded shared uncapped spill (live #17 mechanism); wait_idle self-exclusion; research spill hygiene; good patterns catalogue |
| Lead | falsifiers, cross-exam, coordination | F6 executed (→ Defect #18); Wilson power computation; modulo partition-vs-sample adjudication; all cross-session verification |

Every material claim was verified by the lead or a second agent reading the cited
lines; peer-session claims were verified against commits (`5510d7f`, `bf8b3a7`,
`a70cc64` read directly).

## 4. Complete inventory — `test_id` (and identifier) chronology assumptions

**DEFECT (proven or repaired):**
- `pipeline/lake/cycles.py` pre-`5996f53`: `min/max(test_id)` cycle selection — Defect #16, REPAIRED. 6.10% of subsample cycle rows flipped, 100% FAIL→PASS/PRS.
- `assign_cycles` + repaired SQL twin, within-day `(test_date, test_id)` tie-break — **Defect #18, PROVEN, OPEN** (decision routed to trainer-design review). Same-day FAIL+definitive pairs: id order agrees with FAIL-first only 49.91% (chance); mis-ordered half opens a spurious FAIL cycle (fixture-proven 1/1-vs-1/2 aggregate contribution); D7 basis: denominator +4.55%, rate −1.26pp (2019 C3&4). Repair shape identified: `ORDER BY test_date, (NT before RT via test_type), test_id` — `test_type` has 0 nulls lake-wide.
- Research legacy cycle-first spines (4 sites: `build_cycle_index_duckdb.py:262-276`, `build_canonical_spine_v2.py:573-585`, `fresh_s2_struct_ext.py:101-113`, `build_component_struct_v2_fix.py:70-80`): same `(test_date, test_id)` within-day ordinal — the mechanism behind the 2026-07-29 freeze (6.4% of eval rows, 67.2%-vs-22.6% fabricated signature). Already quarantined.
- `icloud_snapshot/v57_history_recompute.py:204-209`: `ORDER BY test_date DESC, test_id DESC` prior chain — direct MAX(test_id) analogue in the v57 lag-feature family.

**NEEDS REVIEW (unexamined assumption, materiality pending F7):**
- `build_packets_fulldepth.py:633-634` `ORDER BY tgt_id, p_date DESC, p_test_id DESC` — canonical fulldepth substrate; UNEXAMINED (no decision record; builder untracked in git); reaches ~30 serving-module features via stable re-sort. All post-freeze fulldepth results (FD twin 0.716389, RealMLP 0.716733, TabM, TabPFN ladder) carry it.
- `fresh_s5_assemble_fix.py:264` `ORDER BY sp.test_date DESC` no tiebreak → `lag1..5_*` nondeterministic on 11.97% of targets (DQ-19, CONFIRMED, register-recorded).
- `fresh_s5_assemble_fix.py:232-239` `arg_max(expr, test_date)` — ties arbitrary.
- Serving: `feature_engineering_v55.py:197` sort with no tie-break — same-day order = DVSA JSON array order; diverges from training's tie rule on the same population (train picks id-DESC, serving picks API order). No fixture with >1 same-day test exists.
- `dvsa_client.py:90-95` `latest_test = mot_tests[0]` on an unverified "newest-first" comment; `report_service.resolve_odometer` and `_build_mot`, `main.py:1018`, `report_routes.py:566` scan the UNSORTED list — display/persistence and model can describe different tests.

**SAFE (verified, with the checks that prove it):**
- Repaired `build_cycles_sql` windows (B: 8/8 confirmed); `checks.py:72-73` continuity lag; `stream_cycles.py` ORDER BY; aggregates GROUP BY (order-free); trainer (`drop_duplicates` = set-membership only; splits by YEAR(test_date); CatBoost `has_time` unset); `build_db.py` (no ordering at all); `bayesian_model.py:65` (deterministic derived value); research `vehicle_id % N` uses = full partitioning, not sampling; episode rebuild history features (order-invariant day-level MAX by design — the model repair should copy).
- REPLACE-D7 target population (`test_type='NT'` per-row) — immune by construction, pinned by non-monotone-id and shuffle tests.

## 5. Temporal/order-sensitive code findings (beyond §4)

- `cycles.py:156-161` self-join fan-out on duplicate `test_id` within a cycle is **not absorbed**: the whole cycle's row count multiplies in `with_outcome` and survives to the final SELECT; the `cycle_level` DISTINCT collapses only the summary grain (B, confirmed). Python twin silently emits duplicate-id rows instead (F6-Q3) — the twins disagree about duplicates, untested by any fixture.
- `stream_cycles.py:147` `years[test_id]` dict collapses duplicate ids → potential wrong-year partition routing (B, confirmed).
- Continuity gate sampling: `REPEATABLE (42)` not reproducible at `threads>1` (DuckDB docs, B); gate at n=10,000 cannot resolve 0.0088 vs the 0.01 bar (Wilson 95% [0.00715, 0.01083]); n=50,000 is the first size whose upper bound clears. Conflict share moved 0.0000→0.0088 exactly as 2018+ releases landed — cross-release duplication vs organic first_use corrections is UNRESOLVED (E's queries written, unexecuted).
- `EXCEPT`-based equivalence checks are duplicate-blind; count equality is the only multiplicity guard (B).
- Sampling bias (peer-found, adopted): raw `vehicle_id % 20` residues are fail-heavy and oversized; hash residues match truth. All research modulo uses are full-partitioning (lead-verified) — operational imbalance only.

## 6. Feature-layer findings

- **The exposed surface is cycle-derived history features, in both repos** — everything downstream of `assign_cycles`/spine ordering (`prev_cycle_outcome`, `days_since_prev_cycle`, lag families, streaks, recency). The REPLACE-D7 target is immune; the features are not, and remain unvalidated until the within-day ordering decision lands (peer session's explicit position, matching this audit's analysis).
- Serving computes NO cycle collapse, NO dedup, NO PRS branch (`'PRS'` absent from the entire serving path); training collapses chains and rules PRS. A same-day fail→retest pair inflates `n_prior_tests` and `n_prior_fails` at serving. This is the mechanism behind recorded defect D4 — root-caused and closed at substrate level 2026-08-09 (`history_tests_observed` was cycle-grain ROW_NUMBER), open for the deployed model until retrain (D, records coherent).
- Serving/training tie rules differ on the same population (train `test_id DESC`, serve API order) — a train/serve divergence named nowhere in the D1–D4 record.
- Identity-vs-equality asymmetry inside `feature_engineering_v55.py`: advisory family keys on `id(test)`, failure family on dataclass value-equality — duplicate/same-day-identical records collide in one family and not the other.
- `eb_unified_prior` (#2 feature, +0.0214): deployed v55's priors were built under the pre-2026-06-18 PRS=FAIL `is_failure` bug (icloud EB builders, Jan 2026; v55 created 2026-01-16). Known bug; live V2 prior lineage uses the canonical label; replacement scheduled at retrain. NOT a new defect — but the deployed model still carries it.
- D1–D3 serving-parity fixes (`4ad24f5`, `d537050`) are **not ancestors of origin/main** (A, ref-proven; squash-merge caveat noted) — production serves without them.
- DQ-10: the shared checkout's `MISSING_TEST_MILEAGE_DEFAULT = 50000` is the register-CONFIRMED defect (model learned a surface around 0); v58's `==0` pin matches the trained model; neither tree has the intended NaN fix (D).

## 7. Twin / reference-implementation findings

- **Twin agreement was weak evidence exactly as the brief warned**: the #16 repair made SQL equal Python while both share the within-day id assumption (Defect #18 lives in BOTH); `parity_assert.py:186` uses the builder's own tie key (order-blind by construction) and its 100.0000%×104 gate is scoped to phase B — phase A has leakage/coverage gates but no independent parity re-derivation (erratum, C-verified; "invariant-6" end-to-end gate designed but never run).
- `normalize.py` twins (D): `outcome_expr` = `upper(trim(col))` with NO whitespace normalization — a tab/newline-contaminated `test_result` silently becomes `'UNKNOWN'` in SQL where Python returns `'FAIL'`; embedded newlines are PROVEN present in this dataset (commit `a9bac73`). STRONG HAZARD: silent reclassification drops rows from DEFINITIVE_OUTCOMES. `clean_id_expr` diverges loudly on empty/blank ids (Python→None, SQL→CAST error = ingest crash). `model_id_expr` genuinely closes the whitespace gap via `\s+` (except `\v`/non-ASCII whitespace — RE2 ASCII class). `first_use_expr` clean. `age_years` has no named SQL twin (inlined `ingest_results.py:53-55`, structurally identical, untested).
- `test_sql_twins_match_python` is not a twin test: hardcoded friendly expectations, never calls the Python twins; `model_id_expr`/`first_use_expr`/`taxonomy_era_expr` have zero SQL-side test executions anywhere.
- Positive control: `TestBandSqlTwins` is a genuine boundary-complete equivalence loop (ages/mileages from both sides of every cutpoint incl. 500000/500001) — the pattern the normalize tests should copy.
- Fixture power: before `5996f53` no fixture had non-monotone ids; still today no fixture anywhere has a same-`(vehicle_id,test_date)` pair or a duplicate `test_id`; no serving test shuffles input; no fixture >1 same-day test.

## 8. Row-conservation and determinism findings

- **No input-vs-output row assertion crosses any COPY** (ingest counts input only; `run_lake` build-cycles counts output only — inverse gaps). A 2.8%-class loss is recorded as success. `PartitionRecord` exists, never populated ("partitions": {} always); RUNBOOK claims otherwise.
- **2022-skip, PROVEN with full causal trace (E):** substring predicate `f"test_result_{Y}" in path` false-positived on the 2021 zip's internal generation-timestamp names (`test_result_20220531131730_*`) → `year 2022 already in manifest — skip` → `dft_test_result_2022.zip` (1.16 GB, prefetched, `.ok` present) sits UNEXTRACTED on disk. Hole is inside the intended 2021→2025 aggregate window; locally recoverable. Side effect: 2023 prefetch never started.
- `check_year_volumes` NEVER ran (runner calls `--gate continuity` only) and is structurally blind to a missing year.
- Failed gate attempts are invisible: the 22:48:59 OOM crashed before `record_check`/`save` — manifest still ends at the 21:55 PASS.
- No duplication defense: no distinct-test_id or (vehicle,date)-multiplicity check; `source_state()` keys on exact path (same content, new path = re-ingest); `schemas.py:65-66` documents that full-depth republications are indistinguishable from single-year files.
- `manifest.save()` non-atomic (`write_text`, `os.replace` absent repo-wide); `.done_$Y` markers written after source deletion without verifying the parquet; cycles COPY has no FILENAME_PATTERN (stale-file duplication on re-runs, mitigated only by untracked `rm -rf` in runners).
- Row-loss hazard status: DuckDB docs describe `preserve_insertion_order` as an order-only knob with no documented loss interaction (B); no prior loss incident in either repo (F); classification stays PROVISIONAL, spill collision under concurrency the leading candidate. Note the research repo sets it `false` in 170+ places — if the mechanism is ever proven preserve-linked, blast radius is near-total; if spill-linked, the fix is isolation (§9).

## 9. Concurrency / spill / artifact-isolation findings

- **STRONG HAZARD (live): stage4 ↔ items-runners share the uncapped `$WT/.tmp` spill with no mutual exclusion in either direction** (`sharded_cycles.py:35` hardcodes it; bare `run_lake` inherits it by cwd; `items1023_runner.sh` has no double-launch guard). Defect #17's exact mechanism, still armed. Tracked pipeline code sets no `temp_directory`/`max_temp_directory_size` anywhere.
- `wait_idle()` (`stage3_results_runner.sh:34`) excludes every `autosafe-v58` process from its busy count — the serialization guard cannot see the jobs that matter.
- `stream_cycles.py` (per-PID + 6GiB cap) is the correct committed pattern but no orchestrator invokes it.
- Research repo: shared literal spill dirs are the norm (12-file, 4-file, 7-file groups sharing hardcoded paths; one genuinely per-PID file repo-wide); mostly PLAUSIBLE RISK (nominally serial), STRONG HAZARD within same-program sibling groups.
- items_parking sentinel: dead detached watcher leaves the sentinel forever, but stage4's bounded wait fails closed (40 min → ESCALATE) — blocks, doesn't corrupt.
- Positive patterns to copy: research `p1_chain.sh` (sentinel only after rc==0, machine-wide RUN_LOCK, overwrite refusal), `instrumentation.py` (PID-tagged tmp + `os.replace`), `build_spine.py` (computed temp cap + refusal floor).
- Process finding: `bakeoff_2026/` was deleted despite an ACTIVE-DEPENDENCY flag; `canon.py:44-47` still imports the canonical label constants from it → the V2 EB-prior rebuild (BEST-pointer lineage) is not re-runnable; no git recovery possible (never tracked).

## 10. Empirical falsifier results

Pre-registered discriminator (set before any falsifier ran): SVAR iff (i) F7 shows
tied-population order-dependence moving a reported metric > 0.002052, or (ii) a
published claim relied on a vacuous leakage control, or (iii) a conservation defect
touches a shipped artifact's window. Anti-inflation: findings inside the 07-29
freeze's quarantine add no marginal damage.

| F# | Status | Result |
|---|---|---|
| F6 (pure-Python adversarial vs `assign_cycles`) | **EXECUTED** (lead) | Order-invariance PASS (0/200 shuffles); 45-day boundary PASS; same-day FAIL+definitive = id-dependent (→ Defect #18); duplicate-id behaviour asymmetric between twins |
| F4-equivalent (tie prevalence, real lake) | **EXECUTED** (peer, at this audit's request; unbiased hash sample, isolated spill) | 9.2% same-day vehicle-days; 151,298 FAIL+definitive mixed groups; **49.91% id-order agreement = chance**; ~3.0M exposed vehicle-days/yr |
| F1 (2022 zero-rows) | Superseded by E's log trace + partition listing (proven without execution) | — |
| F2 (aggregate invariance to #16) | RETIRED by REPLACE-D7 (target no longer cycle-derived) | — |
| F3 (gate power) | Computation done (n=50k threshold); empirical rerun deferred, folded into the owner-lane gate re-verification | — |
| F5 (order-invariance sweep) | DEFERRED (needs DuckDB; box constrained) | — |
| **F7 (fulldepth tie-rule materiality)** | **DEFERRED — the decisive one.** Re-stream a bounded packet sample with `p_test_id ASC`, recompute 104 features, diff | Open path to SVAR |
| F8 (repair vacuous time_travel_test, run) | Repair diff specified; run deferred (fixtures scan DEV_SET via DuckDB) | Downgraded in urgency by C-iv (no reliance) |

Discriminator outcome: (i) unmeasured — not triggered; (ii) refuted by C-iv; (iii)
refuted (nothing shipped from the affected window). → MATERIAL SIBLINGS FOUND.

## 11. Classification summary

**PROVEN DEFECTS (observed):** #16 (repaired); #18 (open, quantified, both
implementations); 2022 skip-predicate + unextracted zip; `check_year_volumes` never
wired; D1–D3 absent from origin/main (ref-level); DQ-10 50000 sentinel in the shared
tree (register-adjudicated); DQ-19 lag nondeterminism (register-confirmed, 11.97%);
freeze-defect spine displacement (already quarantined); failed-gate invisibility in
manifest (observed 22:48:59).

**STRONG HAZARDS (mechanism demonstrated, effect unquantified):** `p_test_id DESC`
unexamined tie rule on the canonical substrate (F7 pending); `outcome_expr`
whitespace reclassification (newlines proven in-corpus); stage4↔items shared
uncapped spill with no interlock; wait_idle self-exclusion; gate REPEATABLE
non-reproducibility + n=10k underpower; serving/training tie-rule divergence;
duplicate-id fan-out non-absorption; no-duplication-defense (jump 0.0000→0.0088
unattributed).

**PLAUSIBLE RISKS:** clean_id_expr blank-id crash divergence; `\v`/non-ASCII
whitespace in model_id; `+`-signed ids; taxonomy_era NULL divergence; `age_years`
untested inline twin; build_db no unique index; research shared spill dirs (serial
use); manifest non-atomic write; `.done` after delete; per-PID-less items workdir.

## 12. Previously reported results — regeneration assessment

| Result | Status |
|---|---|
| Shipped product artifact (26.9139…% era) | No regeneration from this audit — predates the v58 pipeline entirely; now historical-diagnostic under D11 anyway |
| v55 deployed model | No NEW regeneration demand — but it carries three KNOWN, recorded issues (pre-06-18 PRS EB priors; D4 old grain; D1–D3 unfixed on main); all already routed to the retrain/promotion track. This audit adds: the retrain must also resolve the tie-rule and same-day semantics (Defect #18 decision) before its history features are trustworthy |
| Pre-2026-07-31 research absolutes (incl. honest-0.750) | Already VOID under the standing freeze — no change |
| Frozen CB BEST 0.715846/0.715894 | Stands (post-clearance, non-fulldepth lineage); caveat: promotion lineage carries DQ-19 lag nondeterminism on 11.97% of targets — recorded, register-adjudicated |
| Fulldepth-substrate results (FD twin 0.716389, RealMLP 0.716733, TabM, TabPFN ladder) | **Comparisons stand** (deterministic shared substrate). **Absolutes carry a bounded caveat pending F7**; do not publish externally as history-consistent until F7 clears or the tie rule is repaired and re-streamed |
| V2 EB-prior lineage | Not re-runnable (bakeoff_2026 import broken) — reproducibility, not validity; must be restored before any regeneration claim |
| v58 lake (results 2005–2023) | Not invalidated; must backfill 2022 + re-run gates at n≥50k before first aggregate/training use. Cycles: rebuild only after the Defect #18 ordering decision |

**Nothing already published must be retracted on this audit's evidence. The
regeneration obligations are forward-looking gates, not retroactive corrections.**

## 13. Minimal repair and regression-test plan (dependency order)

1. **Decide Defect #18 ordering** (owner decision — changes rule-of-record
   semantics): adopt `ORDER BY test_date, (NT before RT via test_type), test_id` in
   `assign_cycles` + SQL twin, or adopt the episode rebuild's order-invariant
   day-level aggregation for history features. Regression: fixtures with
   same-(vehicle,date) FAIL+PASS both id-orders; duplicate-test_id fixture asserting
   identical twin behaviour (currently asymmetric).
2. **Conservation minimum set (E-5)**: post-COPY output-vs-input count in
   `ingest_results.py` (+ inverse in build-cycles); EXPECTED_YEARS assertion in
   `check_year_volumes` AND wire the runner to call it; `count(*)` vs
   `count(DISTINCT test_id)` gate; populate `PartitionRecord` (min/max test_date per
   source). Then: fix the year-skip predicate (year-boundary or content-date match),
   extract/ingest the on-disk 2022 zip, re-run gates.
3. **Isolation invariant into tracked code**: `run_lake._connect` gets per-PID
   `temp_directory` + `max_temp_directory_size` (copy `stream_cycles.py`); point
   `sharded_cycles.py` at per-PID; add mutual pidfile checks stage4↔items; fix
   `wait_idle` to match specific interpreter paths/pidfiles; double-launch guard in
   `items1023_runner.sh`; atomic manifest write (`tmp + os.replace`); record failed
   gate attempts (try/finally around `record_check`).
4. **Gate power**: continuity `sample_size ≥ 50,000`; document that REPEATABLE is
   advisory at threads>1; attribute the conflict-share jump (E's duplicate queries)
   before trusting any re-verified PASS.
5. **Twin tests that can fail**: real SQL-vs-Python parametrized equivalence for all
   5 normalize pairs on the adversarial set (empty/blank ids, tab/newline outcomes,
   `\v`, signed ids, NULL dates), modeled on `TestBandSqlTwins`; add whitespace
   normalization to `outcome_expr` (regexp `\s+` strip, as `model_id_expr` does).
6. **Research substrate**: run F7; on failure, re-stream packets under the decided
   tie rule and restate fulldepth absolutes; either way, put
   `feature_repr_review_v1/` under version control and record the tie-rule decision;
   fix the phase-A gap by running the designed invariant-6 end-to-end gate once.
7. **Reproducibility**: vendor the label constants out of the deleted
   `bakeoff_2026/` import (`canon.py`); repair the vacuous `time_travel_test`
   (use `all_prior`), and either fix the 20% leakage tolerance via a same-day-aware
   recomputation or replace it with the episode-grain check.
8. **Serving (retrain track, already routed)**: D1–D3 to main; tie-rule alignment
   with training; PRS branch; provenance manifest per `models/v57/README.md`.

## 14. Residual uncertainty after repair

- F7 outcome (the only item that can change the verdict).
- The 2.8% row-loss mechanism — unreproduced; minimal reproducer still queued.
  Conservation checks (repair 2) convert it from silent to loud regardless.
- Whether `dft_test_result_2022.zip` truly contains calendar-2022 (high confidence;
  one `unzip -l` + min/max(test_date) check pending).
- Conflict-share jump attribution (duplication vs organic corrections).
- DuckDB window-function determinism under threads>1 (docs silent; low residual
  risk given unique keys, but unproven).
- Whether within-day chronology is recoverable beyond NT/RT (`completed_ts` absent
  pre-2024) — bounds how "correct" any tie rule can ever be; the order-invariant
  feature design is the only ceiling-proof repair.
- D1–D3-on-main verified from local refs only (squash-merge false-negative
  possible; one fetch resolves).

## 15. Can we still trust the canonical historical features and previously reported model results?

**Features:** the canonical *target/population* is now trustworthy by construction
(REPLACE-D7). The canonical *cycle-derived history features* are NOT yet
trustworthy in either repo: they rest on a within-day ordering assumption that is
proven wrong-at-chance (product, Defect #18) and unexamined (research,
`p_test_id DESC`), on a population of ~3M vehicle-days/year. They need the repair-1
decision plus regeneration before any new training run treats them as
history-consistent.

**Previously reported results:** no published result must be retracted. Pre-freeze
absolutes were already void; post-freeze fulldepth *comparisons* stand on a
deterministic substrate; the BEST pointer stands on a separate lineage with a
recorded, bounded nondeterminism. Fulldepth *absolute* levels carry one bounded
caveat that F7 will either clear or quantify. The deployed v55 model's known
serving-side defects (D1–D4, PRS priors) predate this audit and remain the retrain
track's obligation — this audit adds Defect #18 alignment to that obligation.

**The 0.997/0.88%/362d/delta-0 evidence trail** that opened this brief should no
longer be cited as validation: the continuity PASS is underpowered at its margin and
non-reproducible as configured; the delta-0 cross-checks counted inputs, not
outputs; and the parity gates share assumptions with what they check. The evidence
standard, not the data, is what failed — and repairs 2, 4 and 5 rebuild it.

## Addendum at commit time (canonical branch 6ef316f)

Between report drafting and commit, the fix sessions advanced the repair queue:
- The 2022 skip-predicate defect (§8) is now registered and **REPAIRED** as
  **Defect #19** (`6ef316f`, "2022 skip false-positive, repaired + inline-gate
  near-miss record") — repair step 3's predicate fix is done; the 2022 backfill,
  gate wiring and n≥50k re-verification remain open.
- The d7 merge (`aea99fd`/`a70cc64`) is absorbed into the canonical branch; this
  report's product citations were taken at `90a6cb0`/`7754b10` and remain valid
  (evidence-only commits since).
- **F7 status:** script written and armed (`scratchpad/f7_tie_rule_materiality.py`);
  execution blocked at commit time by the live items ingest + 0 GB free disk. A
  watcher triggers it when the box frees (no `run_lake` process and ≥10 GiB free).
  Design note: F7 measures ASC-vs-DESC tie-order feature diffs under ONE module
  version (self-differential), deliberately not a diff against the banked frame —
  module drift would confound that; the tie-rule effect is isolated by construction.

## 16. What the main engineer should do next (dependency order)

1. Free the box; run **F7** (bounded, per-PID spill, one attempt) — it resolves the
   last verdict-relevant unknown.
2. Make the **Defect #18 ordering decision** (owner-level; NT-before-RT tie-break vs
   order-invariant features) — everything cycle-derived waits on it.
3. Land the **conservation minimum set + year-skip fix**, then **backfill 2022**
   from the on-disk zip and re-run gates at n≥50k with attribution of the
   conflict-share jump.
4. Move the **isolation invariant into tracked code** before any two pipeline jobs
   run concurrently again.
5. Rebuild **cycles** under the decided ordering; only then regenerate aggregates
   (now diagnostic-only under D11).
6. Research: version-control the substrate builder, vendor the bakeoff constants,
   fix the vacuous tests, run invariant-6 once; restate fulldepth absolutes only if
   F7 fails.
7. Retrain track (existing): D1–D3 + tie-rule + PRS + provenance manifest.
