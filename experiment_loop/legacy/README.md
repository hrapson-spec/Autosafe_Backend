# Legacy diagnostic ledger

`ledger_2026-06-13_diagnostic.tsv` is the original `ledger.tsv` (schema 0), preserved
verbatim. These three rows are **diagnostic-grade only** — they predate the evaluator
hardening and were produced under the nondeterministic sampler (F1) and the F3
seed-stability bug. In particular the `12:24 keep` is a known lucky-subsample artifact,
not a reproducible promotion.

Kept (tracked) because it is the evidence of *why* the evaluator was hardened — not
deleted on schema migration (review pt 9). It is never read by `manifest.read_ledger`,
which only serves the schema-1 ledger and hides diagnostic rows by default.
