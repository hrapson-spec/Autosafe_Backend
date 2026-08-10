"""Items ingest: raw test_item files -> items lake parquet.

Each defect row is classified era-aware at ingest (is_fail_item /
is_advisory / is_dangerous) and projected onto the 7 canonical component
categories via the RfR lookup mapping. Unregistered rfr_type_codes are
stored with NULL classification -- never guessed -- and gated by
checks.check_rfr_type_coverage.

The taxonomy era comes from the parent test's date, so ingesting items for
a year requires that year's results to be in the lake first (run_lake
orders this).
"""
import logging
from pathlib import Path
from typing import Dict, Optional

from . import schemas
from .manifest import LakeManifest
from .normalize import TAXONOMY_SWITCH_DATE
from .rfr_mapping import RFR_TYPE_BY_ERA, project_category

logger = logging.getLogger(__name__)

ITEMS_DATASET = "items"


def _classification_case(era_expr: str) -> Dict[str, str]:
    """Build the era-aware CASE expressions for is_fail_item, is_advisory,
    is_dangerous and rfr_class from RFR_TYPE_BY_ERA (single source)."""
    fail_branches, adv_branches, danger_branches, label_branches = [], [], [], []
    for era, table in RFR_TYPE_BY_ERA.items():
        for code, cls in table.items():
            cond = f"{era_expr} = '{era}' AND trim(rfr_type_code) = '{code}'"
            fail_branches.append(f"WHEN {cond} THEN {'true' if cls.is_fail_item else 'false'}")
            adv_branches.append(f"WHEN {cond} THEN {'true' if cls.is_advisory else 'false'}")
            danger_branches.append(f"WHEN {cond} THEN {'true' if cls.is_dangerous else 'false'}")
            label_branches.append(f"WHEN {cond} THEN '{cls.label}'")
    return {
        "is_fail_item": "CASE " + " ".join(fail_branches) + " ELSE NULL END",
        "is_advisory": "CASE " + " ".join(adv_branches) + " ELSE NULL END",
        "is_dangerous": "CASE " + " ".join(danger_branches) + " ELSE NULL END",
        "rfr_class": "CASE " + " ".join(label_branches) + " ELSE NULL END",
    }


def _read_csv_clause(path: Path, schema: schemas.SourceSchema) -> str:
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in schema.columns)
    return (
        f"read_csv('{path.as_posix()}', delim='{schema.delimiter}', header=true, "
        f"columns={{{cols}}}, quote='\"', escape='\"', "
        f"null_padding=true, strict_mode=false)"
    )


def register_rfr_category_table(con, rfr_map: Optional[Dict[str, str]]) -> None:
    """Register rfr_id -> (item_name, component_category) as a DuckDB
    relation. An absent map (lookup tables not downloaded) registers an
    empty table: items ingest still works, categories stay NULL, and
    check_category_coverage fails loudly instead of anything guessing."""
    rows = []
    for rfr_id, item_name in (rfr_map or {}).items():
        rows.append((str(rfr_id), item_name, project_category(item_name)))
    con.execute("DROP TABLE IF EXISTS rfr_category_map")
    con.execute(
        "CREATE TABLE rfr_category_map (rfr_id VARCHAR, item_name VARCHAR, "
        "component_category VARCHAR)"
    )
    if rows:
        con.executemany("INSERT INTO rfr_category_map VALUES (?, ?, ?)", rows)


def items_select_sql(path: Path, schema: schemas.SourceSchema,
                     results_relation: str) -> str:
    era_expr = (
        f"(CASE WHEN r.test_date >= DATE '{TAXONOMY_SWITCH_DATE.isoformat()}' "
        f"THEN 'post_2018' ELSE 'pre_2018' END)"
    )
    cls = _classification_case(era_expr)
    dangerous_col = (
        "trim(i.dangerous_mark)" if "dangerous_mark" in schema.columns else "NULL"
    )
    return f"""
SELECT
    CAST(regexp_replace(trim(i.test_id), '\\.0$', '') AS BIGINT) AS test_id,
    trim(i.rfr_id)                                   AS rfr_id,
    trim(i.rfr_type_code)                            AS rfr_type_code,
    trim(i.location_id)                              AS location_id,
    {dangerous_col}                                  AS dangerous_mark,
    {era_expr}                                       AS taxonomy_era,
    {cls['rfr_class']}                               AS rfr_class,
    {cls['is_fail_item']}                            AS is_fail_item,
    {cls['is_advisory']}                             AS is_advisory,
    {cls['is_dangerous']}                            AS is_dangerous,
    m.item_name                                      AS item_name,
    m.component_category                             AS component_category,
    year(r.test_date)                                AS test_year
FROM {_read_csv_clause(path, schema)} i
JOIN {results_relation} r
  ON r.test_id = CAST(regexp_replace(trim(i.test_id), '\\.0$', '') AS BIGINT)
LEFT JOIN rfr_category_map m
  ON m.rfr_id = trim(i.rfr_id)
"""


def ingest_items_file(con, path: Path, lake_dir: Path, results_relation: str,
                      manifest: LakeManifest, force: bool = False) -> Optional[int]:
    """Ingest one item source file (see ingest_results_file for the
    idempotency contract). Requires rfr_category_map to be registered and
    the covering results partitions to exist (the JOIN is the era source;
    orphan items -- test_id absent from results -- are excluded here and
    surface in the reconciliation gate, not silently as era guesses)."""
    state = manifest.source_state(path)
    if state == "unchanged" and not force:
        logger.info("skip unchanged source %s", path)
        return None
    if state == "changed" and not force:
        raise RuntimeError(
            f"source {path} changed content since it was ingested (sha256 "
            f"mismatch); --force only if the republication is intended."
        )

    schema = schemas.detect_schema_for_file(str(path), "items")
    out_dir = Path(lake_dir) / ITEMS_DATASET
    out_dir.mkdir(parents=True, exist_ok=True)
    select_sql = items_select_sql(path, schema, results_relation)
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
    logger.info("ingested %s: %s item rows (%s)", path, rows, schema.name)
    return rows
