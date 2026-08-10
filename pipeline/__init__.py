"""AutoSafe v58 data/training pipeline (workstation tooling, never deployed).

Subpackages:
    lake        -- DVSA anonymised-bulk ingest -> canonical parquet lake
                   (results, items, cycles, vehicles), 2005 -> present.
    aggregates  -- comparison-artifact regeneration (prod_data_clean.csv.gz
                   + Postgres mot_risk) from the lake, with audit gates.
    train       -- v58 matrix build (through serving feature engineering),
                   as-of encoder fits, training, bundle packaging.

This package is excluded from the Railway build context (.railwayignore) and
its third-party dependencies live in requirements-train.txt, NOT
requirements.txt: nothing under pipeline/ may be imported by serving code.
Modules import duckdb/pyarrow lazily so that importing `pipeline` itself is
safe in any environment.

Design decisions and their rationale: docs/v58/DECISIONS.md.
Operating instructions: docs/v58/RUNBOOK_DATA.md.
"""
