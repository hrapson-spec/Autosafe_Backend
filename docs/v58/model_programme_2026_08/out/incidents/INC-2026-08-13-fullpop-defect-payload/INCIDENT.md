# INC-2026-08-13 — fullpop packet substrate built with no defect payload

**Severity:** P0 (substrate for the final full-grade model)
**Status:** CONTAINED — detected before any fit. Gate 3 REJECT/HOLD.
**Detected by:** R6 (consumed-matrix reconciliation, Gate 3), 2026-08-13.
**Verified independently by:** programme lead, same day, measurements reproduced below.

---

## 1. What happened

`queue.txt:167` (`B20_BUILD_FULLPOP_1M`) is the **only** build in the programme passing
`--defect-detail counts`. That routes through `build.py:112` (default is `rows`) to
`emit.py:549` `include_defects=False`, and `packets.py:163` then writes `defects_json = None`.

`queue.txt:168` (`B20B_B0_MODULE_FULLPOP`) subsequently ran the B0 module with
`--defect-text-source section` over those null packets. **It did not fail. It emitted zeros.**

So the fullpop packet set carries no defect payload at all, and the downstream consumer
silently reinterpreted "defect detail unavailable" as "this vehicle had no defects."

This is the same failure mode as the item-availability conflation identified separately
(`atoms.py:140,240` coalesce-to-zero) — an unavailable observation rendered as a confident zero —
occurring at a different layer.

## 2. Measured evidence (reproduced independently by the lead)

`defects_json` non-null rate, by packet set:

| packet set | rows | non-null | rate |
|---|---:|---:|---|
| **frames_fullpop / recipe=fullpop / rung=r1m** | 5,560,040 | **0** | **0.0000%** |
| frames / recipe=flat4y / rung=r1m | 7,845,433 | 3,894,170 | 49.6361% |
| frames / recipe=flat4y / rung=r250k | 1,966,034 | 977,749 | 49.7320% |
| frames_eval / recipe=eval2024 / rung=all | 2,866,158 | 1,459,838 | 50.9336% |

The fullpop set is uniquely and completely empty; every comparator runs ~50%.

**Consequence (R6):** 53 columns are constant in fullpop training while live at the paired eval
frame — 51 collapse to a single value; `dominant_mechanism` loses its `LEAK` level entirely in
training while it is present on **9.809%** of eval rows; `advisory_trend` also loses levels.
**Only 23 of the 53 fall inside R1's disputed 67 — R1 passed the other 30 as usable**, because R1
audited declarations rather than the consumed matrix.

## 3. Lineage — nothing downstream consumed it

Verified by the lead, 2026-08-13:

| Check | Result |
|---|---|
| Fit outputs referencing `fullpop` (`grep -rl out/fits/`) | **none** |
| `s2.D.confirm.final` artifacts in `out/fits/s2/` | **none** |
| Saved model files anywhere under `out/` (`*.cbm`, `*.model`, `*model*.pkl`) | **none** |
| `B21_FINAL_S101/S202/S303` | `# HELD-OWNER-SELECTION` (`queue.txt:174-176`) |
| `B22B_DRIFT_SCORE` | `# HELD-OWNER-SELECTION` (`:183`) |
| `B23_SEALED_READ` | `# HELD-OWNER-SELECTION` (`:188`) |
| `B24_COHORT_RANKING_READ` | `# HELD-OWNER-SELECTION` (`:191`) |
| `B24B_SHAP` | `# HELD-OWNER-SELECTION` (`:194`) |

**No fitted model, prediction file, selected feature package or downstream analytical artifact used
these packets.** The owner's hold on final-model selection is what prevented contamination — the
defective substrate was built 2026-08-13 09:36–09:55 and the hold predates any fit against it.

At detection the live queue was fitting `as.ftt.retry.250k` against **flat4y** frames, i.e. not the
affected substrate.

## 4. Evidence preserved

Under this directory, originals untouched:

- `manifests/` — `b0_fullpop.parquet.manifest.json`, `b0_fullpop_eb.parquet.manifest.json`,
  `b0_eval2024_eb_fullpop.parquet.manifest.json`, `BUILD_MANIFEST.json` (carries `code_sha256`)
- `queue_lines_167_168.txt` — the defective build and consumer commands, verbatim
- `logs/` — 10 build logs: `B20PRE_FULLPOP_MAXU`, `B20PRE2_RESTRICT_RESULTS`,
  `B20_BUILD_FULLPOP_1M` (×3), `B20B_B0_MODULE_FULLPOP` (×2), `B20C_EB_SWAP_FULLPOP`,
  `B20D_EB_SWAP_EVAL_FULLPOP`, `B20E_EB_SWAP_CONFIRM`
- `defective_packets_frame.sha256` — **136 sha256 lines**: all 112 packet parquets, all frame
  parquets, and the three `b0_*fullpop*` artifacts. This is the byte-exact before-state for
  rebuild reconciliation.

Nothing has been deleted or overwritten.

## 5. Root cause — a capability mismatch, not a bad flag

The flag is the trigger; the defect is that **builder and consumer capabilities are not expressed
or asserted**. A consumer requesting `--defect-text-source section` over a packet set built without
defect payload should fail at preflight. Instead the contract permitted an implicit, silent
downgrade from "unavailable" to "zero".

Removing or changing the flag alone would leave the same trap armed for the next combination.

## 6. Remediation (owner-directed, 2026-08-13)

1. **Contract fix before rebuild.** Explicit packet metadata for defect-payload mode, defect-item
   observability, expected source/partition availability, successful item join, schema/publisher
   version, and build configuration + source hashes. A dependency assertion must stop incompatible
   builder/consumer combinations *before* packet creation or fitting. Three states preserved
   throughout: unavailable → NULL + status; observed-with-no-items → zero + observed status;
   observed-with-items → populated. **No feature emitter may infer "no defect" from a NULL
   `defects_json`.**
2. **Rebuild B20** with the defect capability the downstream B0 section features require,
   preserving target-row membership, vehicle-hash/rung membership, labels, dates, all non-defect
   source fields, and deterministic ordering/checksums. Only defect payloads, observability state
   and derived features may change.
3. **Acceptance is reconciliation, not a rate.** "~50% non-null" is explicitly *not* the criterion.
   Each target test is reconciled to the canonical defect-item source and its expected state
   derived (source observable + items present / source observable + no items / source unavailable /
   expected record failed to join), with agreement reported by calendar year, outcome, publisher
   source and target cohort.
4. **Before/after table for all 53 affected columns** — old and corrected training cardinality,
   eval cardinality, NULL/zero rates, category support, reason for change, final disposition. A
   level present on 9.8% of eval rows and absent from training is an automatic failure.
5. **Held cells stay held** until the owner explicitly releases them following validation.

## 7. Related open defects (separate, not caused by this incident)

- Item-unavailable vs no-defect conflation at `atoms.py:140,144,240`, `state.py:102-104`
  (`DayAtom.item(default=0)` — a second independent coalesce that would silently undo a SQL-only
  fix), and `atoms.py:167,168,174` → `state.py:118` into packets. **78 of 137 B1–B6 columns
  value-change (56.9%), including all 50 of B2.**
- Mileage unit unidentifiable — no unit field exists in the lake, packets, or DVSA's publication.
  Exposure asymmetry across the 2022 km correction: **73.2% of training rows vs 0.47% of eval
  rows** draw mileage from pre-2022.
- D13 within-day order dependence — **18 columns**, affecting **9.415%** of flat4y targets
  (≥2 priors on the top day).

## 8. Independent corroboration of the publisher-side gaps

Measured by the lead against the lake, confirming these are publication gaps rather than ingestion
loss:

- **2024-12-31 is entirely dark**: 0 of 41,349 tests carry items, against 0.6268 on 2024-12-30 —
  a one-day blackout inside a covered partition.
- **Non-definitive outcomes lose items entirely at the `results_extracts` boundary**:
  7.94%→14.98% carried items across 2015–2023; **2024: 0 of 255,216; 2025: 0 of 256,054.**

The ingested `dft_test_item_extract_202412.csv` is byte-exact with DVSA's published member
(279,587,213 bytes) — our ingestion lost nothing. **Expectation and attribution are therefore
distinct fields**, and the observability state model is being amended accordingly.
