# Phase 1 sizing — measured 2026-08-11 (HTTP content-length, zero-transfer probes)

Source: data.dft.gov.uk via data.gov.uk dataset e3939ef8-30c7-4ca8-9c7c-ad9475cc9b2f.
Raw per-resource bytes: NUMBERS_raw.json / scratchpad sizes3.tsv.

## Measured (compressed archives)

| Set | Size | Notes |
|---|---|---|
| Results 2005–2023 (19 files) | **19.43 GB** | ~1.0–1.2 GB/yr steady; 2005 partial (0.21) |
| Failure items 2005–2023 (19 files) | **6.78 GB** | 0.05–0.63 GB/yr |
| Lookup archive | 0.25 MB | required for items ingest (RfR tables) |
| **Total data** | **26.22 GB** | runbook's "80–150 GB" ≈ the UNCOMPRESSED total, never held under per-year staging |

## Release coverage — MATERIAL FLAG

Latest full data year = **2023** (CKAN catalogue, live data.gov.uk page, and direct
URL probes for 2024/2025 all agree — no newer archives exist). Consequences,
escalated to owner + remote session, NOT decided locally:
- D3 "latest 5 full calendar years" → **2019-01-01 → 2023-12-31**, not the
  handover §4c's assumed 2021→2025.
- "Full depth 2005→present" is really 2005→2023; Phase-3 training data would end
  2023-12-31 while serving sees histories through today — a temporal-gap variant
  of the skew v58 exists to fix. Remote session must reason about it.

## Lake projection (planning ratio gz→parquet-zstd = 1.0; measured at first year)

results ~21 GB + items ~8 GB + cycles ~2 GB ≈ **~31 GB full-depth lake**.

## Peak-disk budget (co-residency is at items-ingest/checks step)

lake 31 + items-join & cycles spill budget ~12 + per-year transient txt ~6
+ headroom 8 ≈ **~45 GB free target** before Phase 3 GO.
Sequencing: full results → continuity gate → cycles → items (join needs full
results co-resident) → checks → §4b probes. Rung-B relief (park/drop pre-2018
results partitions, ~12 GB) is valid only AFTER full items ingest.

## Gate arithmetic (pre-registered)

- Achievable free ≈ 12 now + ~19.8 offload (fresh_2025 6.8, canonical_spine 6.8,
  bakeoff_2026 2.2, test_items lakes 2.6, Downloads 1.4) + ~2.5 caches/Trash
  + swap reboot ≈ **~40 GB (+ ratio upside)**.
- GO rule: recompute after first-year measured ratio; proceed while
  projected peak ≤ free; abort floor 10 GiB mid-run. If ratio ≥ 1.15 → STOP,
  re-price rungs, present options.
