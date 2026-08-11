"""Results ingest: raw test_result files -> results lake parquet.

Every column is read as VARCHAR (never type-inferred: the recorded
float-contamination defect) and cast explicitly in the SELECT. Output is
zstd parquet, hive-partitioned by test_year, one COPY per source file with
per-file unique names so re-runs of other files never clobber earlier
output. Idempotency lives in the manifest (sha256 per source).
"""
import logging
from pathlib import Path
from typing import Optional

from . import normalize, schemas
from .manifest import LakeManifest

logger = logging.getLogger(__name__)

RESULTS_DATASET = "results"


def _read_csv_clause(path: Path, schema: schemas.SourceSchema) -> str:
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in schema.columns)
    return (
        f"read_csv('{path.as_posix()}', delim='{schema.delimiter}', header=true, "
        f"columns={{{cols}}}, quote='\"', escape='{schema.escape}', "
        f"null_padding=true, strict_mode=false, auto_type_candidates=['VARCHAR'], "
        f"parallel=false)"
    )


def results_select_sql(path: Path, schema: schemas.SourceSchema) -> str:
    """The normalization SELECT for one source file (canonical lake schema)."""
    first_use_raw = "try_cast(first_use_date AS DATE)"
    return f"""
SELECT
    {normalize.clean_id_expr('test_id')}                       AS test_id,
    {normalize.clean_id_expr('vehicle_id')}                    AS vehicle_id,
    CAST(test_date AS DATE)                                    AS test_date,
    trim(test_class_id)                                        AS test_class_id,
    upper(trim(test_type))                                     AS test_type,
    {normalize.outcome_expr('test_result')}                    AS outcome,
    try_cast(regexp_replace(trim(test_mileage), '\\.0$', '') AS BIGINT) AS test_mileage,
    upper(trim(postcode_area))                                 AS postcode_area,
    upper(trim(make))                                          AS make,
    upper(trim(model))                                         AS model,
    {normalize.model_id_expr('make', 'model')}                 AS model_id,
    upper(trim(colour))                                        AS colour,
    upper(trim(fuel_type))                                     AS fuel_type,
    try_cast(cylinder_capacity AS INTEGER)                     AS cylinder_capacity,
    {normalize.first_use_expr(first_use_raw, 'CAST(test_date AS DATE)')} AS first_use_date,
    CASE WHEN {normalize.first_use_expr(first_use_raw, 'CAST(test_date AS DATE)')} IS NULL
         THEN 'missing' ELSE 'first_use_date' END              AS age_source,
    date_diff('day',
              {normalize.first_use_expr(first_use_raw, 'CAST(test_date AS DATE)')},
              CAST(test_date AS DATE)) / 365.25                AS age_at_test,
    {normalize.taxonomy_era_expr('CAST(test_date AS DATE)')}   AS taxonomy_era,
    '{schema.name}'                                            AS schema_epoch,
    year(CAST(test_date AS DATE))                              AS test_year
FROM {_read_csv_clause(path, schema)}
"""


def ingest_results_file(con, path: Path, lake_dir: Path,
                        manifest: LakeManifest, force: bool = False) -> Optional[int]:
    """Ingest one source file into the results lake. Returns rows ingested,
    or None when skipped as unchanged. A changed file under an already-
    ingested name raises unless force -- that is an upstream incident."""
    state = manifest.source_state(path)
    if state == "unchanged" and not force:
        logger.info("skip unchanged source %s", path)
        return None
    if state == "changed" and not force:
        raise RuntimeError(
            f"source {path} changed content since it was ingested "
            f"(sha256 mismatch). Re-run with --force only if the upstream "
            f"republication is understood and intended."
        )

    schema = schemas.detect_schema_for_file(str(path), "results")
    out_dir = Path(lake_dir) / RESULTS_DATASET
    out_dir.mkdir(parents=True, exist_ok=True)
    select_sql = results_select_sql(path, schema)
    stem = path.stem.replace("'", "")
    con.execute(f"""
        COPY ({select_sql})
        TO '{out_dir.as_posix()}'
        (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (test_year),
         OVERWRITE_OR_IGNORE true,
         FILENAME_PATTERN 'src_{stem}_{{i}}')
    """)
    rows = con.execute(f"SELECT count(*) FROM ({select_sql})").fetchone()[0]
    manifest.record_source(path, schema.name, rows)
    logger.info("ingested %s: %s rows (%s)", path, rows, schema.name)
    return rows
