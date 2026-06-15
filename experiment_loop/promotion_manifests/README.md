# Promotion manifests (durable, content-addressed)

Each ratified promotion writes a `<manifest_id>.json` here, **committed** so every
`promotions.tsv` row resolves to its evidence in any fresh clone (review pt 1).
`manifest_id` is the sha256 of the manifest's deterministic payload only (metrics,
cohort fingerprint, snapshot id, seeds, controls, verdict — rounded for replay
stability); volatile run metadata (timestamps, run_id, paths) is recorded inside but
excluded from the hash, so a replay reproduces the id (review pt 10).

Dev-grade run artifacts do NOT live here — they go to the gitignored `runs/` dir.
