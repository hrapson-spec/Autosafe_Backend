# DEVIATIONS — training-frame factory

**No deviations from FACTORY_CONTRACT.md v2.** Nothing in this package departs
from the locked contract, so there is no attached failing test (the
deviate-with-test procedure was not invoked).

> **2026-08-13 — INC-2026-08-13 remediation (PREREG_CUBE_v2 §4/§5).** Three
> contract-surface changes are recorded, with their attached failing tests, in
> **`out/cube/CONTRACT_FIX_NOTES.md` §7**: (D-1) the §Severity "never zero"
> rule now binds ITEM availability, not only the pre-2018 severity era;
> (D-2) B1–B6 137 → 142 columns, inside the 150 cap; (D-3) a
> `PACKET_CAPABILITY.json` sidecar and one new packet column
> (`p_items_observability`), packet schema v1 → v2. Each is a tightening the
> contract already required and the code did not implement, so none relaxes the
> contract — but **packet-schema-v1 sets are refused by defect-reading
> consumers and must be rebuilt**, which is a real backward incompatibility and
> is stated as such there.

Recorded below: the contract AMENDMENT, the owner rulings of 2026-08-12, and the
judgement calls the contract left open. None of these is a deviation; they are
written down so the owner's adversarial pass can attack them directly rather
than reverse-engineer them.

**Owner rulings applied 2026-08-12 (all open questions closed):** (1) P-out
stands — `fail_basis='final'`; (2) **class knob SPLIT** — `target_classes=('3','4')`,
history UNFILTERED, new falsifier F11; (3) private `cycles._cluster_outcome`
import accepted as-is; (4) `mdr_rfr_location.csv` located and B6 wired from
**all three axes** lateral+longitudinal+vertical (B6 9 → 14 columns);
(5) rung calibration runs owner-side via `--calibrate`; (6) **trailing depth caps
in the single scan** (B1 ×3 metrics, B2 ×7 categories, at {2y, 5y}), falsifier
F12; (7) `b5_n_prior_ambiguous_days` renamed/split (see J15).

**Adversarial review (out/FACTORY_REVIEW.md, NO-GO) — all findings closed in one
pass.** The reviewer's probe file is ADOPTED as
`factory/tests/test_adversarial_probes.py`, so every fix is pinned by the test
that found it:

| ID | Finding | Fix |
|---|---|---|
| **B-1** | `inclusion_weight` branched on the realised `u`, so stratum-eligible rows landing `u < base` got weight 1.0 — inflating every weighted enriched-stratum total by `2 − base/enriched` (up to 2×, measured +75%). | `sampling.Rung.inclusion_weight` is now a function of the DESIGN CELL only: `base/enriched` for any stratum-eligible row, else 1.0. Probes P1/P1b/P1c + `test_inclusion_weight_is_horvitz_thompson` extended to the disagreeing cells + a new end-to-end assertion on `build()`'s writer path (the shadow the bug survived in). |
| **B-2** | `prepare()` never cleaned staging: a re-run into the same `--staging-dir` scanned a ghost vehicle from the previous build; `FILENAME_PATTERN {i}` also made identical reruns shrink-unsafe. | `prepare()` deletes and recreates `vehicle_day/` and each `events/recipe=*/` before COPY, and records staged file counts in the manifest. Probes P4 + P4b. |
| **M-3** | Block-level fallback tagged four `research_only_input` columns (`b1_n_prior_initials`, `_final_fails`, `_initial_fails`, `b5_last_day_has_prs`) as production-common. | The fallback now classifies DOWN: such columns resolve to `UNCLASSIFIED_RESEARCH_ONLY_INPUT`, a code deliberately outside any instance vocabulary so no "production-common subset" filter can match it. An explicit per-feature ruling from A2 still wins, and `preflight` records contradictions in `serve_view_research_only_conflicts`. |
| **M-4** | The AMBIGUOUS column also counted mixed non-definitive days (cycles.py semantics), contradicting the dictionary. | Owner ruling: rename to `b5_n_prior_nondefinitive_days` (doc corrected) + add the strict `b5_n_prior_ambiguous_days`, which fell out of the accumulator for free. Probe P5. |
| m-5 | `defect_rows` had no canonical order (held empirically, not guaranteed). | Probe P2 adopted. Guarantee still rests on the `tests` `list_sort`; see J16. |
| m-6 | A `fail_basis` typo silently became `initial`. | `AsOfState.__post_init__` RAISES on anything but `final`/`initial`. |
| m-7 | The staging SQL executed twice (COPY, then `count(*)`). | COPY's returned row count is used; the most expensive query in the build runs once. |
| m-8 | `PacketPathology` could fire hours into a real scan. | Preflight counts vehicles over the bound (`GROUP BY vehicle_id HAVING count(*) > bound`) and refuses before any output. |
| m-9 | Nothing asserted `build()`'s writer path. | `test_build_writes_design_cell_weights_not_realised_u_weights` asserts weights, strata and HT unbiasedness on the written parquet. |
| m-10 | Frame files are not byte-reproducible run-to-run (no emitted VALUE changes). | Acknowledged, unchanged; no artefact claims byte-reproducibility. |

## 1. Contract amendment v1 → v2 (owner, 2026-08-12 ~17:45) — not a deviation

- **B0 removed from this factory's remit.** No B0 code was ever written, so
  nothing was deleted: the amendment landed while `severity.py` / `taxonomy.py`
  were being written and before any B0 emitter existed. `blocks.py` implements
  B1–B6 only; the 104 serving features come from the packets →
  `feature_engineering_v55` path.
- **Packets view added** (`packets.py`), mirroring the banked fulldepth shape.
- **Panel-first.** Input relations were already fully parameterised
  (`sources.Inputs`), so a panel build and a full-population build differ only in
  the injected paths. `sources.as_relation` accepts a bare glob, a hive root, or
  an arbitrary SQL relation expression.
- **Falsifiers 1, 2 and 4 now apply to packet contents** as well as to feature
  columns. Implemented in `tests/test_falsifiers.py` via
  `packets.d13_invariant_projection`.

## 2. Judgement calls the contract did not pin

| # | Call | Why | Where it is visible |
|---|---|---|---|
| J1 | **Falsifier 2 is asserted on a D13-INVARIANT PROJECTION, not on raw bytes.** | The falsifier explicitly swaps `test_id` values within a day. Identifier columns (`tgt_id`, `p_test_id`) therefore MUST change — asserting bit-identity on them would assert the opposite of D13. Every non-identifier column is asserted bit-identical, and the emitted row multiset is asserted equal. | `tests/test_falsifiers.py::IDENTIFIER_COLUMNS`, `packets.d13_invariant_projection` |
| J2 | **B2/B4 aggregate the 26 top-level sections to 7 categories + `other`.** | 26 sections × 4 columns = 104, which with B1+B3+B5+B6 would breach the 150-column cap. The atom and the packets defect payload both keep full section grain (`sect`), so a section-grain block can be added later without redoing semantics. | `taxonomy.CATEGORY_KEYS`, FEATURE_DICTIONARY convention 7 |
| J3 | **B5 "same-day multiset features" describe PRIOR days, not the target day.** | The target day's own multiset is not knowable at prediction time (you cannot know a same-day retest will follow), and falsifier 4 requires day-2 features to reflect day-1 only. Reading the target day would be a leak. | `blocks.emit_b5`, falsifier 4 |
| J4 | **The B0-shaped "day ladder" includes days with no definitive outcome.** | Serving's history list excludes abandoned/aborted tests ([ABA]); the factory keeps them as test-days and emits the divergence explicitly rather than silently filtering. | `b1_n_prior_nonresult_days` |
| J5 | **`fail_basis` defaults to `final` (F only).** | It matches the repaired serving vocabulary `{DANGEROUS,MAJOR,FAIL,F}` (FEr:101). B3 emits BOTH bases unconditionally, so the ruling only moves the B2/B4 per-category ladders and is reversible by a rebuild. | `BuildConfig.fail_basis`, open question Q1 |
| J6 | **~~`test_classes` defaults to `('4',)`~~ — SUPERSEDED by the owner ruling of 2026-08-12.** The knob is now SPLIT: `target_classes=('3','4')` (D7 population rule, events only) and `history_classes=None` = UNFILTERED. A class-3/5/7 prior is still this vehicle's history and counts in B1 depth, B4 mileage/burden and the packets view; its items resolve against the class-4 catalogue where they can and are counted as catalogue misses where they cannot — rows are never dropped. The filter knob still exists for deliberate use. | `BuildConfig.target_classes` / `.history_classes`; falsifier `test_f11_class3_priors_count_as_history_for_a_class4_target` |
| J13 | ~~B6 omits `longitudinal`~~ — **SUPERSEDED: all three axes are wired** (owner ruling, same day). B6 11 → 14 columns; packet `pos` is now `lateral/longitudinal/vertical`. | The lookup carries a real Front/Rear distinction; omitting it discarded signal. | `taxonomy.LONGITUDINAL_GROUPS`, `test_b6_counts_lateral_and_vertical_with_a_location_map` |
| J15 | **The B2 capped days-since-last variants were dropped**, per the ruling's own first-cut instruction. | With them the total was **151 vs the cap of 150** (the ruling's +34 estimate did not include the +3 longitudinal columns or the +1 strict-ambiguous column). They are also information-free: `b2_{c}_days_since_capNy` is exactly `b2_{c}_days_since` censored at the cap — the most recent prior day inside a trailing window IS the most recent prior day, when it falls in the window. Dropping the 14 columns loses nothing and leaves 13 columns of headroom. | `blocks.B2_COLUMNS`, FEATURE_DICTIONARY ruling 6 |
| J16 | **`b5_n_prior_nondefinitive_days` and `b1_n_prior_nonresult_days` deliberately overlap.** | They are different denominators — cluster-outcome AMBIGUOUS vs "no definitive outcome at all" — and a mixed non-result day counts in both. Named apart and cross-referenced in both dictionary lines rather than silently deduplicated. | `blocks.B1_COLUMNS` / `B5_COLUMNS` |
| J14 | **`lateral` values carry a side AND an inner/outer qualifier** (`Nearside Inner`, `Offside Outer`). The side always wins; bare `Inner`/`Outer` — which appear in BOTH the lateral and vertical columns — get their own `inner_outer` group rather than being folded into `unknown`. | Folding them into `unknown` would assert "position not recorded" for rows that do record one. | `taxonomy._LATERAL_MATCH`, `test_position_group_mapping` |
| J7 | **`cycles._cluster_outcome` is imported by its private name.** | The contract forbids re-implementing the D13 cluster rule and requires reuse. The public `assign_cycles` computes cycle chaining the factory does not need, and `build_cycles_sql` does not expose the cluster outcome separately. A public alias in `pipeline/lake/cycles.py` would remove the private dependency. | `state.py` imports, open question Q3 |
| J8 | **Exactly one determinism tiebreak involves `test_id`,** in the staged packet list sort (`list_sort` over a struct whose LAST field is `test_id`). It orders by CONTENT first, so it can only break ties between rows identical in type, outcome, mileage and defect payload. | `atoms.packet_struct_sql`; frozen in `tests/test_falsifiers.py::D13_ALLOWLIST` (one entry) |
| J9 | **Rung thresholds are measured, never guessed.** | The contract states row-count targets (250k/500k/1M/2M); those are not thresholds. `--calibrate` measures the u-quantiles that hit them on the injected relation; the owner pins the printed values with `--rung`. | `emit.Factory.calibrate` |
| J10 | **The ≤25% enrichment cap is measured and reported per rung, not silently enforced.** | Silently trimming rows to hit a cap would break the deterministic-inclusion property (a row's membership would depend on other rows). The manifest carries `enriched_share` and `enriched_share_within_cap`. | `sampling.check_enrichment_share` |
| J11 | **`--dry-run` still runs every gate** and exits 3 with `WOULD REFUSE: …` when one refuses. | A dry run that skipped the gates would advertise a build that cannot happen. | `build.main` |
| J12 | **Events are restricted to the explicit year list as well as the window.** | An event in a year that was never staged would have no vehicle_day atom and would be silently dropped by the scan. The intersection is recorded in `preflight.effective_target_years`. | `atoms.events_sql`, `emit.Factory.preflight` |

## 3. Things deliberately NOT done

- **No `neglect_score_*`, no text-derived features.** The v58 lake carries no
  defect free text; the factory never synthesises an empty `text` field for the
  packets payload (see FEATURE_DICTIONARY, packets section).
- **`completed_ts` feeds nothing.** It is accepted as an input and exposed only
  through `atoms.completed_ts_diagnostic_sql`. A test asserts that supplying it
  changes no emitted value.
- **The 2025-H2 confirmation slice is never built.** Its DEFINITION (salt +
  exact predicate) is emitted into every BUILD_MANIFEST;
  `--build-confirmation` refuses without a prereg sha.
- **No git, no network, no training, no read of the real lake.** All tests run
  on fixtures generated by `factory/fixtures/generate.py`.

## 4. Fit runners (`factory/runners/`, commissioned 2026-08-12 evening)

Three entry points, one output contract
(`scripts/analysis/ablation_tables.py:21-28`). Judgement calls recorded:

| # | Call | Why |
|---|---|---|
| R1 | **Every reported metric is computed from the FLOAT32 vector that lands in the parquet** (`metrics.as_stored`), never from the model's float64 output. | The harness recomputes AUROC from the keyed preds and refuses the WHOLE analysis (exit 4) if it disagrees by >1e-6. Rounding to float32 breaks and creates ties, which moves AUROC well above that tolerance. |
| R2 | **`metrics.auroc` is a line-for-line twin of `ablation_tables.weighted_auroc`**, not sklearn's. | sklearn's trapezoidal AUC disagrees with the harness's average-rank tie handling on tied scores. `test_auroc_twin_matches_the_harness` asserts equality against the harness itself, including an all-ties case. |
| R3 | **The date/COVID fences are enforced in `fit_contract.assert_fences` BEFORE a JSON is written.** | The harness's equivalents are exit-3 refusals of every cell in the directory; failing one fit early is cheaper than poisoning a whole read. |
| R4 | **Column typing comes from the ARROW SCHEMA, not the materialised numpy dtype.** | A boolean column with NULLs materialises as `object` and without NULLs as `bool`; a dtype rule would make a feature categorical in train and numeric in eval, which CatBoost rejects at Pool construction. Dates become numeric ordinals (splittable), strings become categorical with an explicit `__NA__` level. |
| R5 | **Validation split for early stopping is vehicle-clustered.** | A sibling row of the same vehicle on the other side of the split makes early stopping optimistic. |
| R6 | **LightGBM and RealMLP are not installed in this venv.** Both refuse with `LibraryUnavailable` naming the recipe of record; the presets are frozen constants so a config stays reviewable and testable without the library. CatBoost 1.2.10 is installed and carries the end-to-end proof. | |
| R7 | **`b0_module_runner` supplies the catalogue SECTION NAME as `defects[].text`.** | The lake has no free text and the module classifies components by keyword. This is the training-direction emulation `r2b_build_v57.py:70-72` used, NOT live tester text — recorded in every emitted manifest, since the text→taxonomy precision gate is the unbuilt bridge (SERVE_VIEW finding 1). `--defect-text-source none` emits the family honestly empty instead. |
| R8 | **`b0_module_runner` refuses to run against the UNREPAIRED module copy.** | It asserts `FAILURE_DEFECT_TYPES == {DANGEROUS,MAJOR,FAIL,F}` at import; the v58 copy tests `type=='FAIL'` only, so every post-2018 history would compute zero component failures (SERVE_VIEW finding 3). |
| R9 | **`stack_runner` PROVES fence 1 rather than assuming it**: `fit_runner` writes a `train_ids_path` parquet, and the stack refuses on any intersection with the stack partition or the eval slice. | "Disjoint by construction" is the claim the instrument's validity rests on; it is cheap to verify and expensive to get wrong. |
| R10 | **`stack_runner` carries its own licence text into every fit JSON** (queue-ordering only; never ADOPT/HARMFUL/LOO-REDUNDANT/K3) and refuses `--block b3\|b4` outright. | The B3/B4 exemption is the false-negative mode the instrument is structurally blind to (replan_designs §1.1). |

## 5. ADVSTRUCT — declining C1's literal falsification rule (2026-08-16)

**This IS a deviation from a sha-frozen prereg and is recorded as one.**
`AMENDMENT_ADVSTRUCT_A3_2026_08_15.md` §3 lists "C1 fails (≤4 of 6 strata)" as
falsifying. C1 returned 3 of 6 on eval2024. The literal rule says FALSIFIED. It
was adjudicated **INCONCLUSIVE** instead (owner ruling, 2026-08-16). Full record:
`out/ADVSTRUCT_RESULT_2026_08_15.{md,json}` §3.

**The attached evidence that made the deviation necessary** — computable from the
banked bootstrap CIs alone, using no information unavailable when A3 was frozen:

| stratum | n_EVAL | β_TRAIN | MDE₈₀ | power vs β_TRAIN | outcome |
|---|---:|---:|---:|---:|:--:|
| c=2 | 38,366 | +0.3006 | 0.0912 | 100% | ✓ |
| c=3 | 16,977 | +0.2540 | 0.0989 | 100% | ✓ |
| c=4 | 7,224 | +0.1204 | 0.1110 | 86% | ✓ |
| c=5 | 3,073 | +0.0717 | 0.1485 | 27% | ✗ |
| c=6 | 1,399 | +0.0905 | 0.1980 | 25% | ✗ |
| c=7-8 | 845 | +0.0187 | 0.2186 | 4% | ✗ |

Every stratum with ≥80% power passed; every failure was underpowered; there were
no genuine misses. Poisson-binomial over the six pass probabilities, assuming the
TRAIN effect is exactly true: E[passes] = 3.42, **P(≥5 of 6) = 7.26%**, modal
outcome = **3 of 6** — which is what was observed. The bar was unpassable by
construction, so its failure carries almost no evidence.

**Root cause (a class, not an instance).** The bar was set on 923,604 TRAIN rows
and applied to 312,159 EVAL rows. Precision scales with √n; nobody computed the
bar's power before freezing it. This applies to every "CI-clear in ≥k of N strata"
counting rule in this programme.

**Standing rule adopted — power-at-bar precondition.** No bar may be frozen
without publishing, in the same document, (a) the MDE at that bar on the
CONFIRMATION row counts and (b) P(bar passes | discovery effect exactly true).
Any bar below 80% is rewritten before freezing, never reinterpreted afterwards.
Binding on `PREREG_PERSIST_2026_08_16.md`.

**What was NOT done:** C1 was not re-run on pooled TRAIN+EVAL (A3 §4 bars moving
the strata; no override sought); the bar was not retrospectively lowered; and
`items_per_system` was not substituted for breadth as the headline. C2 — A3's own
load-bearing gate — passed out-of-sample on frozen coefficients (+0.046024,
CI [+0.024219, +0.068672]) and carries the verdict.
