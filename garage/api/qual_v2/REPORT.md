# qual_v2: qualification receipt and QualifiedBundle v2 (route-to-live step 7)

Written 2 Nov 2026 by an as-s55-high agent (claude-sonnet-5-5). The agent's Write call was blocked, so the lead transcribed its hand-back report here. Only synthetic and fixture bundles were used, and nothing outside qual_v2/ was touched.

Labels: MEASURED (the agent ran it), DERIVED (read from cited code or docs), JUDGEMENT (a design choice). Paths are relative to work/garage_mvp/.

The lead re-ran the tests on 2 Nov: 254 passed, 3 skipped. The lead also checked two files are unchanged: gate.py has sha 2a44c66a… (equal to T2c/FROZEN_MANIFEST.json) and rev3/spine/bundle.py has sha d9b7816c….

## 1. Design

### 1.1 A sidecar over an unchanged BundleV1 (JUDGEMENT)

A QualifiedBundle v2 is the assessed BundleV1 directory, byte for byte, plus two additions.
- The BundleV1 parts (manifest.json, scorer/ and model files) are copied unchanged. They still say qualified:false, shadow.
- `qualification/` holds `receipt.json` and 10–13 evidence files, all hash-listed.
- `QUALIFIED_V2.json` is the commit marker, written last. It is the only place "qualified":true appears.

Why a sidecar: the v1 writer, reader and scorer refuse qualified != false (bundle.py:56, :117; serve.py:158). A v2 manifest would stop the scorer, and changing the scorer would change the assessed packaging.

MEASURED: the real rev3 read_bundle_v1 reads a v2 directory as qualified False. The real write_bundle_v1(qualified=True) still raises bundle_qualified_refused and writes nothing.

Because the qualification carrier is separate from the model bytes, "serve the exact model assessed on 2024" becomes a hash equality. That is ruling 5 of the six design-review rulings in DECISIONS.md.

A missing or damaged sidecar means shadow, never qualified.

### 1.2 The receipt (GARAGE_QUALIFICATION_RECEIPT_V1)

The receipt holds:
- the subject: arm, model_version, freeze, model_identity_sha256 and assessed_base_manifest_sha256;
- the amendment hash;
- the display policy;
- one {path, sha256} per role, listed below.

Roles and what the verifier checks:
- **rules_approval:** the owner's pre-registration of every rule that has no default. It must predate READ_START, and it binds the model, the freeze and the amendment.
- **display_approval:** state (bands or percentage), ranking on or off, band edges, labels and policy version. The receipt's display must equal it exactly.
- **pseudo_future_2023 and usefulness_gate:** declared results. The verifier checks the subject, that the rule hash equals the registered one, that the result passed, and the ordering. The pseudo-future must come before READ_START; the usefulness gate's timing must match its registered partition.
- **assessment_2024_report:** the raw gate.py JSON. Every pass flag is recomputed from the report's own numbers using gate.py's frozen constants.
- **assessment_2024_provenance:** the code hash must be the frozen gate.py and the READ_END proof_sha256 must match. The base rate must be recorded before READ_START and equal the report's.
- **independent_recomputation:** must agree, with a different agent id and a different implementation hash.
- **live_qualification:** must have passed, with every owner-listed component present and passed, and must be completed before READ_START.
- **read_2024_entry_receipt plus the registry lines:**
  - exactly one READ_START and one READ_END, with the same entry_id;
  - both bind the entry receipt digest, and START comes before END;
  - no READ_FAIL or AUTHORITY_REVOKED;
  - the owner-registered entry requirements match.
  - The digest is either rev3 data._digest or the receipt's own content_sha256 (the step-6 style).
- **band_evidence (optional):** required when 2024 calibration fails, and checked against the owner's band rule.
- **g6_shadow (optional):** required for percentage display. It needs ≥500 pooled bookings and PSI ≤ 0.10 (Henri's G6 ruling), and must postdate the 2024 read. The percentage approval must also postdate it.
- **serving_parity (optional):** required when the served v1 manifest hash differs from the assessed one.

model_identity_sha256 covers only the fitted artefacts: arm, version, freeze, schema hash, DAG nodes and file hashes. It excludes the scorer, the source paths and the runtime. Step 8 can therefore repackage the bundle with a parity receipt and a re-issued receipt, without a new 2024 read.

### 1.3 What qualified:true requires

All of the following must hold. The first failed check raises QualError with a stable code.
- The files are valid against the schema and are strict JSON (no NaN, no duplicate keys).
- Every hash binding holds, with no unlisted file, no symlink and no path escape.
- The receipt describes the bundle actually being served.
- The rules predate READ_START.
- The registry binding holds.
- Both discrimination gates pass: the delta-AUC lower bound is above 0, and Brier is below the base rate. A vacuous pass is refused unless the owner has allowed it.
- The display rules hold:
  - percentage needs a 2024 CALIBRATION_PASS plus G6;
  - if calibration failed, only bands are allowed, with band evidence;
  - band edges must be increasing multiples of 5.
- The timestamps are in order.
- The out-of-band pin matches. pin_missing refuses unless allow_unpinned is set, which is for tests and lead-side use only.
- The bundle isn't synthetic. Synthetic bundles are refused unless allow_synthetic is set.

### 1.4 Trust anchor (JUDGEMENT)

There's no signature, because the code is stdlib only. Anyone who can write to the directory can recompute the hashes.

MEASURED: 67 forged bundles are refused by the semantic checks even with the pin waived, and the original pin refuses all of them.

The pin is the authority. Record the hash that write_bundle_v2 returns somewhere the bundle author can't write. Changing the pin revokes the bundle.

### 1.5 App contract for step 9

- read_bundle_v2(dir, pin=...) returns a QualifiedBundle or raises QualError.
- try_read_bundle_v2 returns (bundle, None) or (None, code). Any unexpected exception means not qualified.
- Use allow_synthetic=False.
- bundle.display_policy has exactly the keys of DisplayPolicy.to_json().

MEASURED: the DisplayPolicy class extracted by ast from wt-garage/garage/api/provider.py accepts it: problem() is None and to_json() is equal. It rejects provisional, unqualified and suppressed policies, rounding other than 5, and edges that don't increase.

To vendor, copy qual_v2/ together with the schema JSON. It needs Python ≥ 3.9 and no third-party imports.

## 2. Files (T2c/qual_v2/)

- `QUALIFIED_BUNDLE_V2.schema.json`: the sidecar schema plus 14 $defs.
- `common.py` and `minischema.py`: strict JSON, a canonical hash identical to rev3's, safe paths and a validator.
- `base_v1.py`: stdlib v1 integrity checks and model_identity_of.
- `gate_py_v1.py`: recomputes results from the report, with constants pinned to gate.py's sha.
- `verify.py`: verify_receipt.
- `bundle_v2.py`:
  - build_receipt;
  - write_bundle_v2, which stages, fully verifies, then renames, and leaves nothing behind on refusal;
  - read_bundle_v2 and try_read_bundle_v2.
- `display_ref.py`: reference rounding only.
- `__main__.py`: `python3 -m qual_v2 <dir> --pin <sha> [--registry PATH]`.
- `tests/`: 9 test files, synth.py, two checked-in synthetic sample bundles, SAMPLE_PINS.json and make_sample.py.

## 3. Tests (MEASURED)

Run on Python 3.9.6 with pytest 8.4.2, from T2c:

    PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -p no:cacheprovider qual_v2/tests

Result: 254 passed, 3 skipped.

| Test file | Tests | What it covers |
|---|---|---|
| test_defects | 156 | 73 defects through the writer; 67 through a hash-recomputing forger; serving parity; changed model bytes |
| test_integrity | 32 + 3 skipped | Integrity checks; the 3 skips are optional roles |
| test_gate_py | 18 | Constants and sha equal the real gate.py (by ast); agrees with a real gate.run_gate output; 12 doctored values caught |
| test_happy | 12 | Valid bundles are accepted |
| test_display_ref | 12 | Reference rounding |
| test_crosscheck | 8 | See below |
| test_schema | 7 | Schema validation |
| test_registry | 6 | Synthetic registry only |
| test_sample | 3 | The checked-in sample bundles |

test_crosscheck covers:
- hash equality with rev3;
- v1 behaviour being unchanged;
- rev3's reader and this reader agreeing on 6 directories, including the real fixture_bundle_BundleV1, which qualified as a synthetic artefact without modification;
- the app's DisplayPolicy accepting the output.

Mutation check: 10 checks were disabled one at a time on a scratch copy. One survived at first (the receipt subject fields); after a test was added, 10 of 10 are killed.

## 4. Findings

1. **gate.py is not the amendment's gate (DERIVED).**
   - Its age bands (gate.py:43: <3, 3-6, 6-10, 10-15, 15+) differ from the amendment's (AMENDMENT_DRAFT.md:53: ≤5, >5-10, >10-15, >15).
   - It has no Brier-vs-baseline, top-20% or band-monotonicity code.
   - The verifier can only evaluate the frozen gate.py. An amended gate needs a new evaluator and a new code hash.
2. **gate.py passes vacuously.** all([]) is true, but the amendment says low-support groups are "not qualified" (AMENDMENT_DRAFT.md:53). MEASURED: an 800-row report passes subgroup_bias and display_bands with zero gated groups. The verifier refuses this unless the owner sets allow_vacuous_gates.
3. **The 5-pp band gate is fragile at the tail (MEASURED, synthetic).**
   - On 12 draws of perfectly calibrated data (60,000 rows), only 4 passed every gate.
   - All 8 failures were display_bands, from sparse tail bins whose sampling error exceeds ±2.5 pp.
   - A correct real model could fail this gate by chance. That's worth knowing before the single 2024 read.
4. **"Ties-to-even" is under-specified (MEASURED over 20 printed ties).**
   - The float expression 5*round(100*p/5) and the exact-decimal reading differ at 0.575 (55 vs 60).
   - The exact binary value differs from the float expression at 9 of the 20 ties. These include p=.325 (30, medium, vs 35, high, at 20/35 edges) and p=.175 (20 vs 15).
   - The amendment asks for tests at .175 and .325 but doesn't say which reading to use.
5. **Repackaging for step 8 changes the manifest hash,** because the scorer hash and source closure are bound (challenger_bundle.py:27-35). This is handled by the split between model identity and packaging, plus the required serving-parity receipt.
6. **Parallel drafts are not yet integrated (DERIVED).**
   - T2c/v2024_prep/authority.py (step 6) mints GARAGE_V2024_ASSESSMENT_AUTHORITY_V1, with the digest being its own content_sha256 (authority.py:61). It plans two receipts, one for the cohort and one for outcomes.
   - This entry binding supports that digest style but binds only one entry. Extend it to a list if both scopes get READ lines.
   - eval_ext_v1/usefulness_gate.py has its own report format and needs an adapter into the declaredGate envelope.
7. **Registry line shape,** observed in registry/DATA_ACCESS_REGISTRY.jsonl lines 8738-8739: entry_id, entry_receipt_content_sha256, event, note, programme, proof_sha256, timestamp_utc, verified_by.

## 5. Open decisions for Henri

These are all required fields with no defaults.

| | Decision |
|---|---|
| O1 | Usefulness gate: which data decides it (role 5, 2023 or 2024), the rule and its hash. The 2 pp margin is only "proposed" |
| O2 | The 2023 pseudo-future pass rule. No code for it exists yet |
| O3 | Assessment rule set: the frozen gate.py, or an amended gate (which needs a new evaluator) |
| O4 | Which live-qualification components must pass |
| O5 | Whether to allow vacuous gate passes. Recommendation: no |
| O6 | What the 2024 READ entry receipt must contain, and whether both step-6 receipts are bound |
| O7 | Bands-only rule parameters. The amendment proposes n≥200, strictly increasing, adjacent lower bound > 0 |
| O8 | Display policy: bands or percentage, ranking on or off, edges (20/35 is provisional), labels, policy version |
| O9 | The exact rounding implementation (finding 4) |
| O10 | Trust anchor: pin or signature, and who holds the pin and where |
| O11 | Serving-parity tolerance. 0.0 means bit-identical |
| O12 | What counts as independent recomputation. Today it's a different id string and implementation hash |

## 6. Not verified

- No real data or real receipt was used.
- The declared gate results (2023, usefulness, live) are checked for binding, rule hash, order and pass, but never recomputed.
- The approval quote and the approver are plain strings, so their authenticity rests on the pin.
- Revocation detection in the issuance-level registry check is a conservative text search for the entry id or digest. rev3 data.py revocation_for was not read in full. The registry check was tested on synthetic registries only.
- Single use of the 2024 read isn't enforced here; the registry and step 6 own that.
- Recomputation independence is nominal only.
- Evidence, receipt and base manifest are read once and verified from the same bytes. But model files and the scorer are hashed in place and read again later by the scorer, so the bundle directory should be read-only in deployment.
- Everything ran on Python 3.9.6 only. The app image is 3.11 and the model runtime is 3.12.
- App integration (bundle_provider.py, handlers.py) wasn't touched or run. Step 9 still needs its own rounding and rank_score tests (handlers.py:159-186).
- The fixture's scorer_sources and source_root are absolute rev3 paths, which v2 doesn't verify. Step 8 removes that dependency.

## 7. 3 Nov 2026: integration changes (two-scope READ binding, self-contained base)

Written by the integration stage (claude-sonnet-5-5). Labels: MEASURED (run here), DERIVED (read from cited text), JUDGEMENT. Paths relative to work/garage_mvp/T2c. Full context: integration_v1/REPORT.md.

### 7.1 What changed (this is the re-vendoring list)

Runtime files changed (the five that the app vendors; all other runtime files are byte-identical to before):

| File | sha256 (first 16) | Change |
|---|---|---|
| QUALIFIED_BUNDLE_V2.schema.json | e96175014d58c4be | `rules.read_2024_entry_requirements` removed; required `rules.read_2024` added; receipt.evidence gains optional `read_2024_entry_receipt_2..4` and `read_2024_registry_records_2..4` |
| verify.py | f2b3b91ff1c1ad7a | `_read_entry` replaced by `_read_one_entry` + `_read_entries` (verify.py:71-176); timing checks re-keyed (see 7.2) |
| bundle_v2.py | 773a44f4d5233689 | 6 new optional roles (ROLE_FILES / OPTIONAL_ROLES, bundle_v2.py:24-36); the writer copies the whole self-contained inventory (bundle_v2.py:94) |
| base_v1.py | 40eff651f9758667 | reads and checks `selfcontained.inventory` (base_v1.py:42-57); returns `inventory` |
| common.py | 7d48c3b78a651f12 | `_SEGMENT` allows a leading underscore (so `__init__.py` is a legal bundle path); a leading dot or `..` is still refused (common.py:11) |

Test-side files changed: tests/synth.py, tests/helpers.py, tests/test_defects.py (3 new defects, 1 re-pointed), tests/SAMPLE_PINS.json and both checked-in sample bundles (regenerated with tests/make_sample.py; new pins bands 34894ee5c67b56a8..., percentage_ranking 8e5a09edeb15722e...). New: tests/test_multi_read.py (101 tests), tests/test_selfcontained_base.py (11 tests).

MEASURED: 372 passed, 9 skipped (was 254 passed, 3 skipped). The 6 extra skips are the optional-role hash-binding test for roles that the single-scope fixtures do not carry; the two-scope bundles test them in test_multi_read.py. Python 3.9.6 for the suite; the same code also ran under Python 3.12.15 inside the app's interpreter (integration_v1 golden path).

Breaking change, on purpose: old receipts and the old sample bundles no longer validate (the new rules field is required and has no default). MEASURED: the app's vendored copy (commit e6c0e47) refuses a new bundle with `schema_invalid` and serves shadow, and the app test `test_qual_v2_vendor.py::test_vendored_files_equal_the_t2c_originals_when_present` now fails (49 passed, 1 failed) until the five files are re-vendored and `VENDOR_SHA256.json` plus `tests/qual_v2_fixtures/*` are refreshed.

### 7.2 Two-scope 2024 READ binding (finding 6 closed)

`rules.read_2024` is a required object with no default:
- `start_policy`: `one_read_start` or `two_read_starts` (owner decision, v2024_prep ambiguity 9: AMENDMENT_DRAFT.md:71 vs :13). The number of `entries` must equal it (1 or 2); the receipt must bind exactly that many entry receipts and registry-record files, in order (`read_entry_policy_mismatch`, `read_entry_count_mismatch`).
- `entries[i]`: `label`, `requirements` (exact key/values, non-empty, as before), `follows` (null for the first; for later entries the label of an earlier entry) and `cohort_summary_field` (null, or the entry-receipt field that must equal `assessment_2024_provenance.cohort_sha256`, i.e. the frozen cohort summary hash; v2024_prep authority.py:141-146).
- `assessment_entry`: the label whose READ_START bounds the assessment, the recomputation and a 2024-partition usefulness gate (the 2024 outcomes cannot exist earlier), and whose READ_END `proof_sha256` the gate.py provenance must carry.

Each entry has its own READ_START and READ_END (exactly two registry lines, same entry_id, both bound to the entry receipt digest; the digest is the receipt's own `content_sha256` for step-6 style receipts, authority.py:61). Ordering (JUDGEMENT, derived from AMENDMENT_DRAFT.md:13): a later entry's READ_START must be strictly after the READ_END of the entry it `follows`. Everything pre-registered or pre-2024 (rules, base rate, 2023 diagnostic, live qualification, a non-2024 usefulness gate) must precede the EARLIEST READ_START; G6 must follow the LATEST READ_END. Entry ids and digests must be distinct. In a one-entry bundle every check reduces to the previous behaviour.

MEASURED: 18 of 18 mutations of the new checks and of the base/inventory logic were killed (ordering, summary binding, count and policy mismatch, assessment-entry timing, duplicates, label and follows validation, inventory hashing and copying, underscore paths); the unmutated control passed.

Open (owner): `one_read_start` cannot be produced by authority.py today: it accepts a receipt whose scopes are exactly the cohort set or exactly the outcome set (authority.py:121-122), so a single receipt cannot cover both. Choosing one READ_START needs a v2024_prep ruling (ambiguity 12).

### 7.3 Self-contained base

A SelfContainedV1 manifest lists every bundle file in `selfcontained.inventory` (vendored sources, tz data, runtime pins). base_v1 now checks that inventory (files entries and scorer must be in it; every file hash is verified on read) and the writer copies all of it. Without this the qualified directory would have been missing src/, tz/ and runtime/ and the launcher would refuse it. model_identity_sha256 is unchanged by packaging (MEASURED: equal for the fixture bundle and its self-contained package).
