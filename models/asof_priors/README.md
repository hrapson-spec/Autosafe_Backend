# models/asof_priors/ — V2 as-of prior tables (adoption-gate 4, 2026-07-04)

Live-serving artifacts for `asof_priors.AsOfPriorStore`. Loaded at startup by
`model_v55.load_model()` behind the `AUTOSAFE_ASOF_PRIORS` feature flag
(default OFF). See `asof_priors.py`'s module docstring for the full lookup
semantics (backoff order, month clip, canon normalisation).

## What's here

Five channel tables + their meta sidecars:

| stem        | table file                          | meta file             | grain                              |
|-------------|--------------------------------------|------------------------|-------------------------------------|
| make        | `make_fail_rate_asof.parquet`        | `make_meta.parquet`        | make                                |
| segment     | `segment_fail_rate_asof.parquet`     | `segment_meta.parquet`     | make, age_band, mileage_band        |
| model       | `model_fail_rate_asof.parquet`       | `model_meta.parquet`       | model_id                            |
| make_age    | `make_age_fail_rate_asof.parquet`    | `make_age_meta.parquet`    | make, age_band                      |
| model_age   | `model_age_fail_rate_asof.parquet`   | `model_age_meta.parquet`   | model_id, age_band                  |

**These table files are PRE-SLICED to their `max_asof` month only** (currently
`2025-06-01`), not the full multi-year history. Two independent reasons:

1. Live serving's own clip formula (`eff_asof = LEAST(serving_month,
   max_asof)`) can only ever resolve to `max_asof` in production, since
   `serving_month` is always "now" and `max_asof` is always a frozen past
   publication month — the rest of the history is simply never read.
2. The full `model_age` table alone is 134MB / 13.9M rows, which exceeds
   GitHub's 100MB hard per-file push limit. The pre-sliced files total
   ~4.2MB.

The full, unsliced tables (every month back to 2016) live in the research
repo at `work/goal_0750/prior_rebuild_v1/artifacts/tables_v2/` — that is a
**separate git repo** this product repo does not import from or depend on at
deploy time (see root `CLAUDE.md`: "work/ is a separate git repo"). Treat it
as the source of truth for rebuilding this directory; do not hand-edit these
files.

`AsOfPriorStore.load()` still applies its own `asof_month == max_asof` filter
at read time as a defensive no-op against these already-sliced files (and as
the actual size-reduction mechanism for anyone who points it at the full
research tables with `restrict_to_max_asof=True`, e.g. for a local repro).

## Caveat (RT-PRIORS.md §Caveats, item 5, verbatim)

> 5. **Serving is documented-not-implemented**; retiring static 0.28/0.2471
>    constants is open; live freshness is bounded by DVSA publication lag,
>    not the evaluated month−1.

This PR closes the "documented-not-implemented" and "retiring static
0.28/0.2471" halves of that caveat (this store IS the live-API wiring, and
`AsOfPriorStore.health()["global_rates"]` reports each channel's own
`_meta.global_rate_at_max_asof` — currently ≈0.1926 for all five channels —
in place of the old `0.28`/`0.2471` constants). The **publication-lag
freshness** half is NOT closed by this PR and cannot be closed by code: the
tables were built from data available through 2025-06 (the evaluated
"month−1" window in RESULT_FINAL.md), but every day that passes after that
build without a refresh, the true DVSA-publication lag facing live traffic
grows past what was evaluated. Per RT-PRIORS.md A5: "the trustworthy
fallback-elimination component is largely lag-robust (needs *some* recent
cohort data, not month−1 specifically)" — so staleness degrades gracefully,
it does not silently break — but the broad value-refresh component is more
lag-sensitive (R42 caveat, RESULT_FINAL.md item 3). Do not let this table set
age indefinitely; see the update procedure below.

## Update procedure (RESULT_FINAL.md decision D-E, 2026-07-04)

There is **no standing scheduled refresh job** — Henri's decision (D-E) was
that DVSA data arrives at irregular publication events, not on a monthly
cadence, so a calendar-triggered job would either run against stale data or
need its own freshness-of-the-trigger logic. Instead, **rebuilding this
directory is a mandatory checklist step** of:

1. every adoption retrain that touches the prior channels, and
2. every next-window prereg activation (per `PREREG_NEXTWINDOW_PRIORS.md`),

executed **before** training/scoring, rebuilding the as-of tables through the
newest available DVSA publication. Skipping this step silently reintroduces
staleness with no error signal — the checklist placement (not tooling) is
the control, so whoever runs either of those events owns re-running the
steps below.

**To regenerate this directory from a freshly-rebuilt `tables_v2`:**

```python
# From a repo/session with access to the research repo's tables_v2 output
# (adjust SRC to wherever the new build landed):
from pathlib import Path
import duckdb

SRC = Path("/path/to/prior_rebuild_v1/artifacts/tables_v2")
DST = Path("models/asof_priors")  # this directory, from the product repo root
STEMS = ["make", "segment", "model", "make_age", "model_age"]

con = duckdb.connect()
for stem in STEMS:
    table_path, meta_path = SRC / f"{stem}_fail_rate_asof.parquet", SRC / f"{stem}_meta.parquet"
    (DST / f"{stem}_meta.parquet").write_bytes(meta_path.read_bytes())
    con.execute(f"""
        COPY (
            SELECT * FROM read_parquet('{table_path}')
            WHERE asof_month = (SELECT MAX(asof_month) FROM read_parquet('{table_path}'))
        ) TO '{DST / f"{stem}_fail_rate_asof.parquet"}' (FORMAT PARQUET)
    """)
```

Then verify before committing:
- Each channel's meta `max_asof` matches the intended new publication month
  (all five must agree — `AsOfPriorStore` hard-asserts this at load time).
- Each sliced table has exactly one distinct `asof_month` value.
- `du -sh models/asof_priors/*.parquet` stays well under GitHub's 100MB
  per-file limit (the `model_age` slice is the one to watch as the
  population grows).
- Run `pytest tests/test_asof_priors.py -v` — the golden cross-check will
  catch a mis-sliced or mis-keyed table.
- Bump the DVSA-publication-lag caveat above if the freshness story changes
  materially (e.g. a genuinely scheduled refresh is introduced later).

## Feature flag

`AUTOSAFE_ASOF_PRIORS=1` (env var) + this directory existing with all 10
files present (`asof_priors.tables_present()`) are both required before
`model_v55.load_model()` will construct a store; `AsOfPriorStore` also
hard-asserts all five channels report the same `max_asof` at construction
time (a build that only refreshed some channels fails loudly at startup
instead of silently serving mismatched months).
