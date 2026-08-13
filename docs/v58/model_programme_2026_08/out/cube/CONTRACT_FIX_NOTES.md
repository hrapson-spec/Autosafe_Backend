# CONTRACT_FIX_NOTES — INC-2026-08-13 builder/consumer capability + item observability

**Author:** R3 (factory engineer) · **Date:** 2026-08-13
**Authority:** `PREREG_CUBE_v2.md` §4 + §5, commit `c8a76dd6b3d762a2a942e976a24e78a3c000ae86`,
owner approval `721f1cd49dcd91e5`.
**Scope:** `factory/*.py` + `factory/runners/b0_module_runner.py` + fixtures/tests.
**Not in scope, not done:** no model fit, no real build, no change to `queue.txt`.

---

## 0. What the fix is

The flag was the trigger; the missing declaration was the defect. Two independent
contracts were absent and are now present:

| | Contract | Where it is asserted |
|---|---|---|
| **C** | *builder/consumer capability* — what a packet set can serve vs what a consumer needs | `emit.Factory.preflight` (before packet creation) **and** `b0_module_runner.run` (before fitting) |
| **O** | *item observability* — three states, source-determined, never join-inferred | every item aggregate in SQL, in `AsOfState`, in the packets view, in B2/B3/B4 |

`--defect-detail` is unchanged and still accepts `rows|counts|none`. What changed
is that a build now **declares** what that produces and a consumer now **declares**
what it needs, and the pair is refused when incompatible.

---

## 1. Packet metadata (PREREG §5, deliverable 1)

Every packets directory now carries `PACKET_CAPABILITY.json`, written **inside**
the packets directory so it cannot be separated from the parquet it describes
(`emit.py:736-744`, `capability.py:41,110-115`).

| §5 requirement | Field | Produced at |
|---|---|---|
| defect-payload mode | `defect_payload_mode` | `emit.py:516` (from `BuildConfig.defect_detail`) |
| defect-item observability | `items_observability_states` | `observability.py:45-73`, `emit.py:519` |
| expected source/partition availability | `source_partition_availability` (per input year) | `emit.py:469-506`, `emit.py:523` |
| successful item join | `item_join` (measured, per year + total) | `emit.py:481-497`, `observability.py:402-418` |
| schema/publisher version | `packet_schema_version` (=2), `publisher_schema_epochs` | `capability.py:48`, `emit.py:301-307` |
| build configuration + source hashes | `build_config`, `source_sha256`, `code_sha256`, `duckdb_version` | `emit.py:525-532`, `emit.py:138-141` (`BuildConfig.config_sha256`) |

Also carried: `items_coverage_mode` (`certified` | `assumed_covered`) and the full
`item_coverage_ledger` with its sha256. The same block is mirrored into
`BUILD_MANIFEST.json` under `packets` and a new top-level `item_observability`
key (`emit.py:803-829`).

`item_join` is **measured off the staged atoms**, not off the raw lake, so it can
never describe a different population from the packets it ships with.

Test: `test_capability_contract.py::test_packet_capability_sidecar_records_every_required_field`
asserts all six §5 requirements are non-empty.

## 2. Preflight dependency assertion (PREREG §5, deliverable 2)

`capability.py` adds `CapabilityMismatch(gates.GateFailure)` — deliberately a
`GateFailure` subclass so it reuses the existing refusal discipline and
`build.py` still exits **rc=3** (`build.py:191-196`), exactly like the p4 gate.

**Builder side.** `--for-consumer SPEC` (repeatable, `build.py:122-128`) names
registered consumers the build promises to serve.
`emit.Factory.preflight` resolves them and calls `capability.assert_build_can_serve`
(`emit.py:262-275`) — placed immediately after the lookup check and **before**
`assert_years_present`, before any staging, before any output. The check is
configuration-only, so the refusal costs nothing.

**Consumer side.** `b0_module_runner.assert_consumer_capability`
(`b0_module_runner.py:216-235`) runs as the *first* statement of `run()`
(`b0_module_runner.py:314-321`) — before the module of record is imported and
before a row is read. `--defect-text-source {section,component,none}` all map to
requirements needing `defect_payload_mode='rows'` (`capability.py:156-186`):
even `none` reads each defect's TYPE, so it needs the payload too.

**Legacy packet sets have no sidecar, so they are MEASURED, never assumed**
(`capability.py:294-327`). A set with 0 non-null `defects_json` measures
`defect_payload_mode='none'` and is refused. This is the shape of the actual
defective artifact (fullpop r1m: 0 of 5,560,040), so re-running `B20B` against it
today fails instead of emitting zeros.

Registry: `capability.CONSUMER_REGISTRY` (`capability.py:156-186`). An
unregistered spec raises — a build may not promise to serve something nobody has
described.

Tests: `test_preflight_refuses_a_defect_reading_consumer_over_a_counts_build`
(asserts nothing was staged), `test_every_payload_free_mode_is_refused`,
`test_b0_runner_refuses_a_payload_free_packet_set`,
`test_a_legacy_packet_set_without_a_sidecar_is_measured_not_assumed`,
`test_the_same_consumer_is_admitted_over_a_rows_build` (the gate is not a blanket
refusal), `test_a_payload_free_build_is_still_allowed_when_nothing_needs_the_payload`.

## 3. Three states end-to-end (PREREG §4, deliverable 3)

Vocabulary (`observability.py:45-73`):

| PREREG §4 state | Emitted state | `defects_json` | `p_n_items` | B2/B3/B4 |
|---|---|---|---|---|
| observed, items present | `present_with_defects` | JSON array | count | counts |
| observed, no items (certified cell) | `present_zero_defects` | `[]` | `0` | `0` |
| observed, no items (cell undeclared) | `assumed_zero_defects` | `[]` | `0` | `0` |
| unavailable (cell declared dark) | `unavailable` | **NULL** | **NULL** | **NULL** + status |
| expected missing (evidence says items should exist) | `expected_missing` | **NULL** | **NULL** | **NULL** + status |

Five states, not three, because §4's amendment requires *expectation* and
*evidence grade* to be separable. They collapse onto §4's three exactly:
populated / empty-with-observed-status / NULL-with-explicit-status.

`assumed_zero_defects` exists because "no ledger rule covers this cell" is not the
same claim as "this cell is certified covered", and the difference must be
carriable without changing a single value (see §5). `items_coverage_mode` on the
packet set is the set-level twin, and a consumer may require `certified`
(`--require-certified-item-coverage`).

**Resolution order** (`observability.py:270-289`), contract order, cell rules
first and they never consult the join:

1. cell declared structurally dark → `unavailable`
2. cell declared dark with covered neighbours → `expected_missing`
3. the test has ≥1 item row → `present_with_defects`
4. zero items on a FAIL/PRS → `expected_missing` *(definitional impossibility; a
   FAIL cannot have zero reasons-for-rejection — this rule is evidence and fires
   with or without a ledger)*
5. zero items in a certified covered cell → `present_zero_defects`
6. zero items in an undeclared cell → `assumed_zero_defects`

**Availability is never inferred from the join.** Rules 1–2 come from an
owner-produced cell ledger (`--item-coverage-csv`, pipe-delimited
`test_date|schema_epoch|outcome_class|coverage|attribution|note`, wildcards
allowed, most specific wins). Its two shapes match the two measured anomalies:
`2024-12-31` (a dark day) and `results_extracts × non_definitive` (the 2024-25
publisher cliff).

**Expectation vs attribution are separate fields** (§4 amendment). Expectation is
per-row (`items_observability`); attribution (`publication_short` /
`ingest_loss` / `unknown`) is per-**cell**, carried in the ledger and reproduced
in the manifest and capability. It is deliberately not a per-row column: the
2024-12-31 gap is byte-proven not ours, and a per-row blame field would invite
exactly the "a defect in OUR pipeline" reading v1 got wrong.

**No feature emitter infers "no defect" from a NULL.** `packets.defects_json`
(`packets.py:98-137`) decides from `items_observability`, never from
`defects is None`; `b0_module_runner.decode_defects` (`b0_module_runner.py:113-166`)
replaces `payload = json.loads(raw) if raw else []` and refuses an unobserved
prior by default.

Tests: `test_three_states_are_distinguishable_in_the_packets_view`,
`test_fail_bearing_with_zero_items_is_expected_missing_without_any_ledger`,
`test_certified_and_assumed_zeros_are_separable`,
`test_a_structurally_dark_outcome_class_is_honoured`,
`test_defects_json_never_invents_an_observation`,
`test_an_observed_prior_with_a_null_payload_is_a_contract_violation`.

## 4. All four coalesce mechanisms (deliverable 4)

| # | Mechanism | Old site | New site | How it is closed |
|---|---|---|---|---|
| **M1** | `LEFT JOIN ita a` answering the availability question | `atoms.py:240` | `atoms.py:249-334` | the join still runs (it is how we learn a test *has* items) but no longer decides availability: `items_obs` is resolved once in the `res` stage from ledger + evidence, and every aggregate filters on it |
| **M2** | `coalesce(sum(x), 0)` over 10 scalars + 32 category columns | `atoms.py:140`, `:144` | `atoms.py:143-177` | `CASE WHEN count(*) FILTER (observed) > 0 THEN coalesce(sum(x) FILTER (observed), 0) ELSE NULL END`. The `observed` argument is **required, with no default**, so a caller cannot restore the coalesce by omission |
| **M3** | `DayAtom.item(name, default=0)` — the second, independent coalesce | `state.py:102-104` | `state.py:134-145` | the `default` parameter is **deleted**. `item()` is NULL-preserving; `cat_n/cat_adv/cat_fail` return `Optional[int]` (`state.py:146-157`); every item accumulator is inside `if day.items_observed:` (`state.py:458-571`) |
| **M4** | packet-payload coalesces → `n_items or 0` into packets | `atoms.py:167,168,174` → `state.py:118` | `atoms.py:201-243`, `state.py:159-167` | `n_items`, `n_adv`, `n_fail_final` are `CASE WHEN observed THEN coalesce(...,0) ELSE NULL END`; `PriorTest.n_items` is `Optional[int]` passed through verbatim; `p_items_observability` travels with it |

A fix that closed only the SQL side would have been silently undone by M3. The
falsifier for that is `test_day_atom_item_has_no_zero_default_any_more`, which
asserts on the **signature** (`inspect.signature(DayAtom.item).parameters ==
["self","name"]`), plus `test_an_unobservable_day_adds_nothing_to_the_running_state`
which asserts the state-level consequence (`slope_n == 0`, empty burden window,
`last_day_categories is None`, `n_severity_observable_days == 0`).

**`atoms.py:150` was the precedent, not the exception.** The positional columns
were already NULL-preserving (`sum()` without coalesce); the repair generalises
that discipline to every item column rather than inventing a new one. The
positional sums keep their plain `sum()` and simply gain the observed filter
(`atoms.py:174-177`).

**Two extra carriers found and closed while wiring M3:**

- `AsOfState.last_day_categories` was a `Set[str]`; on a dark day `len(set())`
  would have asserted "no defect categories on the most recent prior day". It is
  now `Optional[Set[str]]` and `None` on a dark day (`state.py:219`, `:573-577`),
  and `b2_last_day_n_categories` emits NULL (`blocks.py:369-371`).
- `state.day_atom_from_row` **raises** if a staged atom lacks the observability
  columns (`state.py:607-621`). A stale `--staging-dir` from a pre-fix build
  would otherwise have been read with zeros. Falsifier:
  `test_a_stale_staged_atom_without_the_observability_columns_raises`.

**Per-category ladders freeze on a dark day** rather than resetting a run to 0 or
marking a category repaired (`state.py:554-556`). "The category was absent" and
"we cannot see whether the category was present" are different claims and only
the first may break a run. Conservative by construction: it can under-count
recurrence, never invent it.

## 5. The damage-ratio guard (deliverable 5)

Nulling all zeros would destroy 179,704,847 genuine observations to repair
552,806 (325:1); `ITEMS_PRESENT_ZERO_DEFECTS` is 49.9–62.1% of all passes. Three
structural guards, each with a falsifier:

1. **The inner `coalesce` survives** (`atoms.py:169-173`). `sum()` over
   observed-but-item-less rows is NULL and that NULL *is* a genuine zero. Only
   the outer CASE — driven by observability, never by the join — produces NULL.
   → `test_a_clean_pass_keeps_its_honest_zero`
2. **`item_graded` is gated on `has_priors`** (`blocks.py:336-354`). A vehicle
   with no prior test-days has no prior defect items: that zero is a certainty,
   and nulling it would erase the whole zero-prior cohort for no repair at all.
   → `test_a_vehicle_with_no_priors_keeps_certain_zeros`
3. **An undeclared cell keeps its zero** as `assumed_zero_defects`
   (`observability.py:288`). Absence of a ledger is recorded as an assumption at
   packet-set level, not paid for by nulling half the corpus.
   → `test_certified_and_assumed_zeros_are_separable`

Population falsifier: `test_the_repair_nulls_only_the_unobservable_minority`
builds 14 vehicles of which 2 sit in a declared-dark cell and asserts **exactly
2** rows go NULL.

**Ledger integrity is gated in both directions.** A cell rule outranks the join,
so a *wrong* rule would silently discard real defect data — the mirror image of
the incident. `atoms.dark_cell_items_probe_sql` (`atoms.py:335-353`) counts rows
in declared-dark cells that carry items, and `gates.assert_coverage_ledger_consistent`
(`gates.py:119-137`, called at `emit.py:309-313`) refuses the build on any
non-zero count. Equal-specificity ledger rules that could match the same cell and
disagree are rejected at load (`observability.py:193-209`) so resolution can
never depend on file order. A missing `--item-coverage-csv` file **raises**; only
omitting the flag entirely selects `assumed_covered` (`observability.py:335-348`).
→ `test_a_ledger_that_contradicts_the_lake_refuses_the_build`,
`test_an_ambiguous_ledger_is_rejected_at_load`,
`test_a_missing_ledger_file_is_never_read_as_everything_is_covered`

**The irreducible residual is recorded, not papered over.** Inside a covered cell
a PASS with zero items stays undecidable, bounded by the same-cell fail-bearing
miss rate (≤1.6e-5, measured). `_measure_item_observability` writes that
statement into every BUILD_MANIFEST and capability (`emit.py:499-506`).

---

## 6. Column and schema changes

**B1–B6: 137 → 143 columns** (cap 150, 7 spare; `blocks.py:132-140`). Six B2
item-observability columns — the denominators every item column is scored on:
`b2_item_observability_status` (`no_priors|none|partial|full`),
`b2_n_prior_days_items_observed`, `_unobserved`, `_zero_defects`,
`_unavailable`, `_expected_missing`. `FEATURE_DICTIONARY.md` updated (a shipped-column test
enforces this).

**Packets: 18 → 19 columns; packet schema v1 → v2.**
`p_items_observability` added; `PACKET_ARROW_SCHEMA` (`emit.py:38-47`),
`PACKET_COLUMNS` (`packets.py:68-73`) and `d13_invariant_projection`
(`packets.py:234-241`) all carry it, so D13 permutation invariance now covers the
observability state too.

**Semantics changed (values move, deliberately):**

- `b3_n_days_fine_severity_observable` — was a *date* predicate only, so a
  post-2018 day with dark items counted as severity-observable. Now requires
  post-2018 **and** item-observable (`state.py:494-497`). This is documented in
  `ITEM_OBSERVABILITY_DESIGN.md` §2.3 as an interpretation-changed column.
- `b4_deterioration_slope_n_days` and the slope itself — dark days contributed a
  false 0 to `slope_sy` **and** inflated the "honest denominator". Both now count
  observable days only (`state.py:578-588`).
- `b2_*_persistence` — denominator moves from `state.n_days` to
  `n_days_items_observed` (`blocks.py:359,366`). Dark days previously inflated the
  denominator only, a systematic downward bias.
- Every B2/B3/B4 item count is NULL when a vehicle has priors but none of them is
  item-observable.

**Untouched by design:** all 26 meta, all 26 B1, all 15 B5, `b4_mileage_band`,
and B6's existing NULL-preserving behaviour. A vehicle's *test* history is fully
observed even when its *item* history is not, and conflating the two repairs
would destroy the depth features that currently work. Pinned by
`test_same_physical_history_in_covered_vs_dark_cells_emits_different_values`,
which asserts B1/B5 are **bit-identical** across the pair while the item columns
diverge.

---

## 7. Deviations from `FACTORY_CONTRACT.md` (deviate-with-test)

**None of the three below is a relaxation; each is a tightening the contract
already required and the code did not implement.**

| # | Contract text | What changed | Attached failing test |
|---|---|---|---|
| D-1 | §Severity: an unobservable quantity is "emitted as `status=...`, **never zero**"; §Feature blocks B3 requires an "`n_days_fine_severity_observable` denominator" | the same rule now binds ITEM availability, not only the pre-2018 severity era | `test_same_physical_history_in_covered_vs_dark_cells_emits_different_values` fails on the old code (both vehicles emit `b2_n_items_total == 0`) |
| D-2 | §Feature blocks: "Cap: total new columns B1–B6 ≤ 150" | 137 → 143. **Inside** the cap; also inside `PREREG_CUBE_v2` §2 `ADOPTED_HISTORY_CAP = 150` | `test_new_column_count_is_within_the_contract_cap` (existing, still green) |
| D-3 | §Emission: "outputs: parquet per (recipe, rung) + BUILD_MANIFEST.json" | adds `PACKET_CAPABILITY.json` per (recipe, rung) packets dir, and one new required packet column | `test_packet_capability_sidecar_records_every_required_field`; `test_b0_runner_refuses_a_payload_free_packet_set` fails without it |

**A genuine backward-incompatibility, stated plainly:** packet schema v1 sets
(everything built before today, including the defective fullpop set and the
existing `flat4y` / `eval2024` / `post2018` / `confirm` / `drift` packet sets)
are **refused** by any defect-reading consumer, because in v1 "no defect items"
and "defect detail unavailable" are the same NULL and cannot be told apart after
the fact. There is no flag to override this; the remedy is a rebuild. That is the
intended consequence of PREREG §6 ("Rebuild B20 from canonical source"), and it
is why the fix is a schema version rather than a patch.

`factory/DEVIATIONS.md` gains a single cross-reference to this section (its
header block); the deviations themselves are recorded here, with the incident.

---

## 8. Test results

Command: `python -m pytest factory/tests -q` (contract-pinned `.venv`,
Python 3.11 + duckdb 1.5.5). Fixtures only; no real build was run.

| | collected | passed | failed | skipped | wall |
|---|---|---|---|---|---|
| **Before** (baseline, unmodified tree) | 137 | 135 | 0 | 2 | 48 s |
| **After** | 177 | 175 | 0 | 2 | 120 s |

The 2 skips are pre-existing and unrelated:
`test_runners.py:149` skips two "library absent" refusal paths because `lightgbm`
and `pytabkit` are installed. **No pre-existing failure was found**, so nothing
was fixed silently.

Two existing tests were touched by the semantic change during development and
both were resolved in the *code*, not the test:

- `test_f4_emit_before_update` initially failed (`b2_n_items_total` NULL for a
  zero-prior target). That exposed a real defect in my first cut of
  `item_graded` — a vehicle with no priors was being treated as unobservable.
  Fixed at `blocks.py:349-352` (the `has_priors` guard, §5 guard 2). The test is
  unmodified.
- `test_every_column_has_a_dictionary_line` failed on the new columns; the
  dictionary was updated, the test is unmodified.

New file `factory/tests/test_capability_contract.py` — **40 tests**, all
fixtures. Notable falsifiers: the incident reproduced verbatim
(`test_incident_reproduces_on_the_old_consumer_expression` asserts
`(lambda raw: json.loads(raw) if raw else [])(None) == []`), the same pair
refused on the new path, the three states, the M3 signature guard, the
covered-vs-dark pair, and the population-level damage-ratio guard.

Fixture support added: `TestRow.schema_epoch` (variable publisher epoch, so the
measured `results_extracts` cliff is expressible),
`fixtures.write_item_coverage_ledger`, and one **fixture correction**:
`default_population` no longer emits a FAIL/PRS with zero item rows
(`fixtures/generate.py:315-333`). That state is definitionally impossible and is
the lake's strongest missing-item detector, so a synthetic population must not
manufacture it; the guard draws no random numbers, so every other fixture-derived
population is bit-identical to earlier vintages. Pinned by
`test_the_synthetic_population_contains_no_impossible_state`. This surfaced as a
genuine `test_b0_module_1k_scale_and_join` failure — the fail-closed consumer
policy correctly refused an impossible prior — and was fixed in the fixture, not
by weakening the default.

**Two CLI paths verified end to end** (fixture lake, `--dry-run`):
`--defect-detail counts --for-consumer b0_module:section` → **rc=3** with the
incident named in the refusal, nothing staged; `--defect-detail rows` with the
same consumer → rc=0 and a manifest carrying `packets.schema_version=2`,
`defect_payload_mode=rows`, the declared consumer, `coverage_mode`,
`publisher_schema_epochs` and `coverage_ledger_contradictions=0`.

---

## 8b. Reconciliation against the design-phase fixtures — the ratchet only half fired

`out/cube/test_item_observability_fixtures.py` (my own, written before the
repair) is deliberately a ratchet: 7 `test_conflation_*` that must **fail** once
the defect is gone, and 7 `test_repaired_*` marked `xfail(strict=True)` that
should **XPASS**. Run against the repaired tree (it is not part of
`factory/tests/`, so the suite above does not include it):

```
4 failed, 3 passed, 7 xfailed
```

**The 4 failures are the ratchet working** — the conflation they assert is gone:
dark vs clean now differ, a fail-bearing zero no longer emits a clean history,
row-grain vehicles are no longer 159-of-163 identical, and the packets no longer
contradict themselves (`defects_json` is `'[]'`, not NULL, for an observed zero).

**The 7 `test_repaired_*` did NOT xpass, and I did not make them.** Three
distinct reasons, and only the third is a judgement the owner may want to
overturn:

1. **Naming.** They assert `b2_n_prior_days_items_present`; I emit
   `b2_n_prior_days_items_observed`. Same quantity.
2. **Status vocabulary — the fixture contradicts its own design doc.** They
   assert `b2_item_observability_status == 'expected_missing' | 'unavailable' |
   'present_zero_defects'`. `ITEM_OBSERVABILITY_DESIGN.md` §3.2 specifies
   `∈ {full, partial, none} mirroring b3_severity_observability_status`, which is
   what I implemented (plus `no_priors`). The **reasons** are carried by the four
   per-day counts, and the per-**test** state names live on
   `p_items_observability` in the packets view, where the fixture's own
   `test_repaired_packets_stop_contradicting_themselves` asserts them and where
   my implementation matches exactly. I followed the design, not the fixture.
   Their disagreement is a defect in the fixture.
   *(Gap closed in passing: the design's fourth count,
   `n_days_items_present_zero_defects`, was missing from my first cut. Added as
   `b2_n_prior_days_items_zero_defects`, `blocks.py:137`. B1–B6 is now **143**.)*
3. **⚠ THE SUBSTANTIVE ONE — dark cells are DECLARED, not detected.** The
   fixtures build a lake with a day that has zero item rows and expect the
   factory to call it `expected_missing` on its own. It does not: without an
   `--item-coverage-csv` rule, that day is `assumed_zero_defects` and keeps its
   zero.

   That is deliberate and I believe it is correct: "every test on this day has
   no item row, therefore the day is dark" is **inference from the absence of a
   join**, which PREREG §4 forbids in terms — and it is unsafe in general,
   because a genuinely clean low-volume day is indistinguishable from a dark one
   (`ITEMS_PRESENT_ZERO_DEFECTS` is 49.9–62.1% of passes). What makes
   **2024-12-31** decisive is not that it has zero items but that it has 41,349
   tests, 9,105 of them fail-bearing, against 0.6268 coverage the day before —
   evidence that lives outside any single day's join. The fail-bearing rule
   (rule 4) is the part of that evidence which *is* row-decidable, and it fires
   automatically, with or without a ledger.

   **Consequence the owner must act on: the 2024-12-31 blackout and the
   2024–25 non-definitive cliff are NOT detected automatically.** Until the
   ledger ships, those rows emit `assumed_zero_defects` with a zero — the
   pre-fix value — carrying `items_coverage_mode='assumed_covered'` so at least
   nothing claims otherwise. The two ledger rows needed are in §9, item 2.

   If the owner prefers automatic day-grain detection, that is a contract change
   and needs its own prereg amendment: it trades a false-zero risk for a
   false-NULL risk, and the false-NULL side is the 325:1 one.

I have deliberately **not** edited
`out/cube/test_item_observability_fixtures.py`. It is the design-phase evidence
record; rewriting it to match the implementation would destroy the only
independent check on that implementation. Its 4 failures and 7 xfails are
diagnostic exactly as they stand.

---

## 9. What I could NOT close, and why

1. **The row-grain residual inside a covered cell is irreducible.** A PASS with
   zero items in a covered cell cannot be distinguished from a dropped row by any
   rule available to the factory. It is bounded (≤1.6e-5, the same-cell
   fail-bearing miss rate) and written into every manifest. Closing it needs a
   source-side recovery (§13's quarantined `test_item_202412.csv`), not code.
2. **`assumed_covered` is the default and will stay the default until the owner
   ships a ledger.** I did not make `--item-coverage-csv` mandatory: doing so
   would refuse every existing build path, and the honest alternative — nulling
   undeclared zeros — is the 325:1 catastrophe. The gap is machine-visible
   (`items_coverage_mode`) and refusable (`--require-certified-item-coverage`),
   which is the strongest position available without owner-produced evidence.
   **Owner action:** produce the ledger. Its two known rows are
   `2024-12-31|||expected_missing|publication_short|…` and
   `|results_extracts|non_definitive|unavailable|publication_short|…`, plus a
   default `||||covered|…` row; both are already measured in
   `ITEM_OBSERVABILITY_DESIGN.md` §1.3–§1.4.
3. **Existing packet sets are not migrated.** Nothing rewrites v1 packets; they
   are refused, not repaired. Rebuilds are owner-run (PREREG §6). I did not
   launch one.
4. **The 53-column before/after table (§6) is not in this document.** It requires
   the corrected fullpop rebuild to exist. This fix is its prerequisite.
5. **`--defect-detail counts` still produces a usable non-defect packet set.**
   I did not remove it, per the explicit instruction. It is now only reachable
   with either no declared consumer or `--for-consumer packets_only`.
6. **B6 positional columns keep their existing semantics.** They were already
   NULL-preserving; the dark-day count is exposed via the B2 columns rather than
   duplicated into B6. Their known interpretation gap
   (`b6_pos_n_total` is called "the honest denominator" and is not, per
   `ITEM_OBSERVABILITY_DESIGN.md` §2.3) is **still open** — it is a location-map
   question, not an item-availability one.
7. **Runtime is measured but not proven at full-population scale.** A/B on a
   26,162-row / 37,953-item fixture, same query with only the M2 aggregates
   reverted, duckdb 1.5.5, `memory_limit=1GB`, `threads=2`, best of 5:

   | | best | rendered SQL |
   |---|---|---|
   | M2 reverted (`coalesce(sum(x),0)`) | 0.019 s | 30,480 B |
   | M2 closed (observability-gated) | 0.020 s | 43,101 B |

   **1.08×** — duckdb evidently deduplicates the repeated
   `count(*) FILTER (WHERE observed)`. That is a *planning-cost* measurement on
   a small fixture, **not** a full-population result: nothing here proves peak
   RSS behaviour when the `res` stage carries `defect_rows` (list-of-struct) for
   the 440M-row corpus. **Owner action:** watch wall time and peak RSS on the
   first real `--defect-detail rows` rebuild against the fullpop lane's 4 GB
   `--memory-limit`; if it regresses, the cheap fix is to move the outer `CASE`
   into a second projection over the grouped result.

   (Suite wall time: an intermediate run took 624 s while the box was running a
   `fit_runner` at 154% CPU plus a DVSA-2023 `extract_to_parquet.py`, load
   average 11–18. The clean run above took 120 s against a 48 s baseline; ~40 of
   the extra seconds are the 40 new tests, the rest is contention.)
