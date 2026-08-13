# PREREG_CUBE_v2 — aggregate-parity feature cube, post-incident

Supersedes `PREREG_CUBE_v1.md` (file sha256 `30371fc3bbc47c9b…`), which predates the
2026-08-13 fullpop incident and the owner rulings that followed it. v1 is retained unmodified for
lineage; it is **not** approved and must not be cited as the governing contract.

Programme: `docs/v58/model_programme_2026_08`. Authored 2026-08-13.

**Status: awaiting owner `.APPROVED`. No outcome-driven cube fit, final-model fit or sealed
evaluation may run under this document until that approval is attached to its commit.**

---

## 1. Target (owner ruling)

```
PRIMARY    y_initial = outcome IN ('FAIL','PRS')     factory/atoms.py:276
SECONDARY  y_final   = outcome = 'FAIL'              factory/atoms.py:275
```

A PRS vehicle failed as presented and required immediate rectification; scoring it negative labels
a vehicle containing an MOT defect as a pass. `y_final` is retained explicitly as the **legacy
continuity target**, as a secondary outcome, and to separate initial failure from failure remaining
after permitted rectification.

**Every banked figure is a legacy-target result** and is labelled `target=y_final (legacy)` at every
point of quotation: `s3.lgbm.adopted.1m` 0.714110 · `s3.realmlp.adopted.1m` 0.714121 ·
`s3.realmlp.ref.1m` 0.709739 · `s3.cb_inc.adopted.250k` 0.710728 · `s3.cb_inc.ref.250k` 0.706868 ·
the B-ladder +0.003637 · RealMLP adopted−ref +0.004382.

**No `y_initial` AUROC is compared to any banked `y_final` AUROC, in any table.** Anchors are
re-banked on `y_initial` before any cube lift is assessed.

## 2. Column caps (owner ruling)

```
CANDIDATE_CAP       = 400   # temporary screening frames ONLY
ADOPTED_HISTORY_CAP = 150   # B- and C-family columns entering the serving model
```

Supersedes `FACTORY_CONTRACT.md:99` and `factory/blocks.py:221 NEW_COLUMN_CAP = 150`.

Candidate pool: generated and tested **blockwise**; exists **only** in screening frames; never the
default training matrix and never any serving matrix; `CANDIDATE_CAP` counts **every actual
model-input column including support and coverage columns**; **shared support measures and
denominators emitted once**, enforced structurally by a `denominator_ref` (a cell inlining its own
copy of a shared denominator is inadmissible).

Winners fit inside `ADOPTED_HISTORY_CAP` by **replacing** weaker, redundant or unusable B-features.
The cap does not rise without measured predictive value, stability and production-cost evidence.

## 3. Controls

| Control | Definition | Permitted use |
|---|---|---|
| `legacy_241` | exact historical matrix, byte-faithful, `y_final` | reconciliation with banked results ONLY — kept reproducible |
| `safe_core_v1` | verified structural constants and exact duplicates removed; item-unavailable vs no-defect conflation repaired; unsafe mileage repaired or quarantined; all D13 order-dependent features repaired; `y_initial` primary, `y_final` secondary | **every cube adoption decision** |

`safe_core_v1` is banked fresh before any cube comparison. It may score below `legacy_241`; that is
a reportable finding, published either way.

## 4. Item observability (owner ruling; amended after measurement)

Three states must be preserved end-to-end. **No feature emitter may infer "no defect" from a NULL
`defects_json` or from a failed join.**

| State | Meaning |
|---|---|
| unavailable / unobserved defect detail | **NULL + explicit status** |
| observed test, no defect items | empty/zero **with observed status** |
| observed test, defect items present | populated |

**Amendment to v1 §3b — expectation and attribution are separate fields.** v1 defined
`ITEMS_EXPECTED_MISSING` as "a defect in OUR pipeline". That is falsified: the ingested
`dft_test_item_extract_202412.csv` is **279,587,213 bytes, byte-exact with DVSA's published
member** — ingestion lost nothing; the publication is short. A packet therefore carries
*expectation* (should items exist for this test?) and *attribution* (if absent, whose gap?)
independently. Only expectation is decidable from our side in the general case.

Availability is determined from **source, partition and ingestion coverage — never inferred from
whether a row joined**.

**Measured constraints on the repair (whole-population):**
- Every partition 2015–2025 has items; join rate rises monotonically 0.5574→0.6118 with no cliff and
  zero orphan item `test_id`s. Partition-level absence is **not** the generator of unavailability.
- The decidable instrument is **fail-bearing tests with zero items** (a FAIL cannot have zero
  reasons-for-rejection).
- **2024-12-31 is entirely dark**: 0 of 41,349 tests carry items vs 0.6268 on 2024-12-30;
  `completed_ts` carries all 41,349 rows.
- **Non-definitive outcomes lose items at the `results_extracts` boundary**: 7.94%→14.98% carried
  items 2015–2023; **2024: 0 of 255,216; 2025: 0 of 256,054.**
- **Nulling all zeros is catastrophic**: it would destroy 179,704,847 genuine observations to
  repair 552,806 — a **325:1 damage ratio**. `ITEMS_PRESENT_ZERO_DEFECTS` is 49.9–62.1% of all
  passes.
- **Irreducible residual**: inside a covered cell, a PASS with zero items is undecidable, bounded by
  the same-cell fail-bearing miss rate (≤1.6e-5). Recorded in `BUILD_MANIFEST`, never papered over.

**Blast radius: 78 of 137 B1–B6 columns value-change (56.9%), including all 50 of B2.** Four
mechanisms: `atoms.py:140`, `:144`, `:240`, and **`state.py:102-104` (`DayAtom.item(default=0)`) —
a second independent coalesce that would silently undo a SQL-only fix**; carried into packets via
`atoms.py:167,168,174` → `state.py:118` and thence all 104 serving features.

## 5. Builder/consumer capability contract (owner ruling — fix the contract, not the flag)

Packet metadata must express, explicitly: defect-payload mode · defect-item observability ·
expected source/partition availability · successful item join · schema/publisher version · build
configuration and source hashes.

A **dependency assertion stops incompatible builder/consumer combinations before packet creation or
fitting**. A consumer requesting `--defect-text-source section` over a packet set without defect
payload **fails at preflight**. It may never proceed by converting unavailable defect data into
zero. Removing or changing a single flag is explicitly *not* an acceptable remedy.

## 6. Fullpop rebuild and reconciliation gates (owner ruling)

Incident: `INC-2026-08-13-fullpop-defect-payload`. `queue.txt:167` was the only build passing
`--defect-detail counts` → `build.py:112` → `emit.py:549 include_defects=False` →
`packets.py:163` wrote `defects_json = None`; `queue.txt:168` then ran the B0 module with
`--defect-text-source section` over those nulls and **emitted zeros**. Measured: fullpop r1m
**0 of 5,560,040** non-null vs ~50% in every comparator. 53 columns constant in training while live
at eval; `dominant_mechanism`'s `LEAK` level absent in training but on 9.809% of eval rows.
Lineage verified clean — no fit, prediction, package or downstream artifact consumed it.

Rebuild B20 from canonical source with the defect capability the downstream B0 section features
require. **Preserved relative to the defective build:** target-row membership · vehicle-hash/rung
membership · labels · dates · all non-defect source fields · deterministic ordering and checksums.
Only defect payloads, observability state and derived features may change.

**Acceptance is reconciliation, not a rate.** "~50% non-null" is explicitly *not* the criterion.
Each target test is reconciled to the canonical defect-item source and its expected state derived —
source observable + items present · source observable + no items · source unavailable · expected
record failed to join — with agreement reported **by calendar year, outcome, publisher source and
target cohort**.

**Gates before any fit:** exact reconciliation of eligible target rows and vehicles ·
expected-vs-emitted observability agreement · source-to-packet defect-count reconciliation · no
unintended row multiplication or loss · matched-history parity between corrected fullpop and
comparator construction routes · NULL/zero/positive distributions for every affected feature ·
train/eval category-level coverage · **no feature constant in training but materially variable in
evaluation unless explicitly excluded from modelling**.

A categorical level appearing in 9.8% of evaluation rows but never in training is an **automatic
failure**. `dominant_mechanism` and every affected categorical must have valid training support or a
**prospectively defined** UNKNOWN/OTHER treatment.

**Deliverable: a before/after table for all 53 affected columns** — old and corrected training
cardinality, evaluation cardinality, NULL/zero rates, category support, reason for change, final
disposition.

## 7. KILL-25 (owner ruling — quarantine, do not delete)

Immediate quarantine via a **versioned feature-exclusion manifest**; generation is **not** deleted.
Partitioned into:

1. structurally constant or exact-duplicate across **valid comparator** frames;
2. constant **only because of** the defective fullpop build;
3. uncertain, requiring the corrected rebuild.

The **6 exact identities** may be removed only after equality is verified under **common dtype and
NULL semantics** across corrected training *and* evaluation frames, retaining the better-defined,
safer canonical member. The **19 constants must be reassessed after the corrected rebuild** — a
feature made constant by this incident may become live and useful after repair, and must not be
killed merely because it could not have affected the defective model.

The manifest preserves lineage, the retained canonical feature, and the evidence per decision.

## 8. Mileage (owner ruling)

The historical unit is **not recoverable from an explicit unit field** — no unit column exists in
the lake, packets, or DVSA's publication. This is stated as unrecoverable, not "unmeasured".

Immediately: **stop hardcoding all odometers as miles**; quarantine per-1,000-mile, miles-since and
mileage-slope features whose validity depends on an unidentified unit; rename any retained measure
to be unit-agnostic; emit mileage availability and longitudinal-consistency status; **never infer a
unit to maximise outcome performance.**

Three leakage-safe sensitivity representations, compared on chronological development data:
(1) no mileage-derived history; (2) conservatively cleaned, within-vehicle-consistent odometer
change; (3) post-correction-period mileage where semantics can be justified.

Exposure asymmetry is **73.2% of training rows vs 0.47% of eval rows** drawing mileage from
pre-2022 (154×). Mileage features therefore **may not enter `safe_core_v1` merely because they were
previously predictive.** The cube's mileage-exposure and `miles_since` axes stay **inactive** until
this ruling is satisfied.

## 9. D13 within-day order (owner ruling)

Blast radius is **18 columns**, affecting **9.415%** of flat4y targets (≥2 priors on the top day).
Measured P(`test_id`(NT) < `test_id`(RT)) = **0.4978** — indistinguishable from chance.

Enumerate every feature depending on `tests[0]` or another position after a **date-only** sort.
Replace each with either a **permutation-invariant same-day multiset statistic**, or **NULL plus
ambiguous status** where the quantity is not identified. Add **permutation tests** that reorder all
prior tests sharing a date and require **identical emitted features**. This is a serving-critical
correction, not an edge-case clean-up.

## 10. Duplicate and redundancy analysis (owner ruling)

R6's signature-first sweep is an explicit **lower bound** (it failed to find
`n_prior_tests` ≡ `b1_n_prior_tests` without a targeted test). A second pass must: canonicalise
numeric dtypes before comparison · apply identical NULL/NaN semantics · detect float/int equality ·
explicitly test known cross-block identities · test exact equality **across multiple chronological
frames**, not matching ranges · report near-duplicates separately with appropriate numeric and
categorical measures.

**Do not auto-remove on correlation > 0.999.** Retention is chosen on semantic clarity, temporal
safety, coverage and stability.

R6's corrections to R1 are recorded as the consumed-matrix gate working as designed, **not** as an
audit failure.

## 11. Statistical treatment

**Paired within-architecture contrast**, identical seed, split and evaluation rows:

```
Δ(seed) = AUC(safe_core_v1 + candidate, seed) − AUC(safe_core_v1, seed)
MDE(k)  = 2.8016 · σ_Δ / √k        k_req = ceil((2.8016 · σ_Δ / δ)²)
```

σ_Δ is **not** σ_AUC: paired arms share seed/split/eval rows, so
`Var(Δ) = Var(A)+Var(B)−2Cov(A,B)` and covariance is large. Seed counts derived from
absolute-AUROC dispersion, or from the cross-architecture tie requirement (≥19 seeds/arch,
`PREREG_STAGE3.md:110-111`), are the **wrong instrument** and are not used.

**Registered detection target δ = 5.0e-4.** The B-ladder's individual steps are +1.11e-3, +1.70e-3,
+3.18e-4, +1.15e-4, +4.03e-4, −1.60e-5 — mostly **below** the 1.78e-3 Stage-2 floor, which is why
all six resolved NEAR-MISS-CARRY or TIE. Screening at that floor could not resolve blocks of the
size this programme produces.

**Noise floor: width-matched planted null.** `Δ_null(seed) = AUC(safe_core_v1 + noise_block_w) −
AUC(safe_core_v1)`, one per width class. This measures σ_Δ *and* controls the capacity/regularisation
confound of widening a frozen recipe (`HistGradientBoostingClassifier` runs `early_stopping=False`
with no column subsampling and cannot adapt at all). It also makes blocks of different width
mutually comparable. **A LightGBM paired noise floor is established directly** — 18 s/seed makes
20 seeds ≈ 6 minutes, so `NO-FLOOR / UNCHARACTERISED` is not justified for the paired estimand.

**`cb_inc` is the primary 250k screening instrument** (MDE 4.34e-04) **but not the sole gate.** At
least one theoretically coherent full or neural-targeted package is carried into RealMLP **even if
its constituent CatBoost blocks are individually weak** — no block in this programme has ever been
screened on both a tree ensemble and a neural model, so tree-weakness is not evidence of
neural-weakness.

**H1 remains untouched final evaluation.** 250k and 1M are **nested**, so a 1M result is not
independent of the 250k screen that selected into it. A **locked later development confirmation
period** — or equivalent chronological confirmation design — is defined and registered **before**
outcome-driven selection begins. Selection performed before that lock is void.

## 12. Memory and materialisation

Blocks generated and persisted **on demand**; the full pool is never materialised per architecture.
Registered per rung before it runs: dtype (build and score dtype **asserted equal**) · frame width ·
storage · expected peak RSS · sparse/dense · **fail-fast threshold: peak RSS > 4.5 GB aborts**.

float32 projections: `safe_core_v1`+one block ≈ 0.26 GB @250k, ≈ 1.04 GB @1M; +full pool ≈ 2.32 GB
@1M. The box has already failed three times today (FTT_D swap-thrash, TabICL SIGKILL rc=137,
TabDPT projection guard); 4.5 GB sits below the observed cliff.

## 13. Data acquisition boundaries (owner rulings)

**DVSA 2023 publisher reconciliation — APPROVED.** Download and preserve both official DVSA 2023
files (results, failure items) plus lookup tables and user guide; retain the existing DfT 2023
versions alongside. Record source URLs, download timestamps, byte sizes, SHA-256. **Both sources are
immutable inputs.** Parse each independently into the same canonical schema and compare: raw and
deduplicated row counts · distinct test IDs and vehicles · eligible Class 4 coverage · outcome and
PRS values · mileage values · defect-item keys and counts · advisory/failure classification · code,
section and severity mapping · duplicate and missing-test behaviour · final emitted features for
matched histories.

This is **reconciliation, not permission to replace the canonical source. No source becomes
authoritative merely because it is newer or larger.** The overlap can isolate publisher/parser
effects; **it cannot resolve the pre-2022 mileage-unit ambiguity and must not be claimed to.**

**`test_item_202412.csv` — QUARANTINED FORENSIC VALIDATION ONLY.** Frozen read-only. Not approved
for canonical ingestion, training or evaluation merely because it holds 14.7% more rows. Record
provenance, original filename and timestamps, size, SHA-256, schema, encoding, date range, row count
and distinct canonical keys. Decompose the excess into: exact duplicates · duplicate tests with
schema-dependent row expansion · genuinely additional defect items · additional tests · different
advisory/failure inclusion · different date coverage · revisions or conflicting values · unjoinable
or malformed records. Build an explicit **schema crosswalk** and compare shared records **after
canonicalisation** — never by row counts or column ranges alone.

Promotion requires **all** of: credible provenance · the schema fully understood · shared records
reconciling on all safety-relevant semantics · extra records corresponding to genuine missing
coverage rather than duplication or changed inclusion rules · dark-day restoration traceable **by
test ID** · no unexplained conflict overwriting a canonical record · red-team fixtures and
temporal-leakage controls passing.

If accepted, it is a **provenance-preserving overlay**: original canonical records retained · only
validated missing records added · every recovered record tagged with its source · conflicts
**quarantined, never silently resolved** · recovered coverage distinguished from ordinary coverage ·
**items from the target MOT never used to predict that same MOT.** A return to the neighbouring-day
rate is a reasonableness check, **not proof**.

## 14. Held cells

These remain `# HELD-OWNER-SELECTION` until the owner explicitly releases them following validation:
`B21_FINAL_S101/S202/S303` (`queue.txt:174-176`) · `B22B_DRIFT_SCORE` (`:183`) ·
`B23_SEALED_READ` (`:188`) · `B24_COHORT_RANKING_READ` (`:191`) · `B24B_SHAP` (`:194`).

## 15. Re-entry criteria (owner ruling)

Cube fitting and final-model work resume **only** when all of the following hold:

1. corrected fullpop packets pass the reconciliation gates (§6);
2. `safe_core_v1` is built and banked (§3);
3. the 53-column train/eval skew is resolved (§6);
4. item observability is explicit throughout the pipeline (§4, §5);
5. KILL-25 has been reclassified against the corrected substrate (§7);
6. the mileage and D13 unsafe families are repaired or quarantined (§8, §9);
7. R4 and R6 issue PASS on the relevant blocking controls.

Then the cube resumes against `safe_core_v1`, **beginning with non-mileage features**. The final
selected model is rebuilt and fitted **only after the cube decision is complete**.

---

## Change control

Any change after the first candidate screen requires an attached failing test demonstrating the
prior semantic was wrong (deviate-with-test, per `FACTORY_CONTRACT.md`), recorded as an amendment
with its own commit — never edited in place.
