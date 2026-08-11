"""Artifact export: aggregate frame -> prod_data_clean.csv.gz + provenance.

One canonical artifact for BOTH stores (the historic Postgres-from-
FINAL_MOT_REPORT.csv vs SQLite-from-.gz split is retired: see
upload_to_postgres.py, which now reads this same file). Formatting is
pinned to what build_db.py/claim_sweep.py already parse:

- 13 columns, exact order (positional typing downstream);
- csv.QUOTE_MINIMAL (model_ids containing commas get quoted, like the
  ~247 such rows in the current artifact);
- risk values round(6) then str() (matches the observed '0.069449' /
  '5e-05' formatting);
- gzip, utf-8, '\\n' line endings.

Alongside the artifact a provenance sidecar (prod_data_provenance.json)
records the coverage window, config, totals, and source-lake manifest
hash, and the exact constants block for report_contract.py is printed --
those constants, the artifact, public copy, and claim_sweep must all move
in ONE commit (the P2 atomic landing).
"""
import csv
import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .build_aggregates import ARTIFACT_COLUMNS, AggregateConfig, coverage_totals


def format_risk(value: float) -> str:
    return str(round(float(value), 6))


def render_artifact_bytes(frame: pd.DataFrame) -> bytes:
    """Deterministic uncompressed CSV bytes for the artifact frame."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(ARTIFACT_COLUMNS)
    for row in frame.itertuples(index=False):
        writer.writerow([
            row.model_id, row.age_band, row.mileage_band,
            int(row.Total_Tests), int(row.Total_Failures),
            format_risk(row.Failure_Risk),
            *[format_risk(getattr(row, c)) for c in ARTIFACT_COLUMNS[6:]],
        ])
    return buffer.getvalue().encode("utf-8")


def export_artifact(frame: pd.DataFrame, out_path: Path,
                    config: AggregateConfig,
                    lake_manifest_sha256: Optional[str] = None) -> Dict:
    payload = render_artifact_bytes(frame)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 AND filename="" keep the .gz byte-stable for identical input:
    # GzipFile otherwise embeds the output path and the current time in the
    # header, so a no-op regeneration would produce a spurious diff on a
    # checked-in artifact.
    with open(out_path, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(payload)

    totals = coverage_totals(frame)
    provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_start": config.coverage_start.isoformat(),
        "coverage_end": config.coverage_end.isoformat(),
        "k_global": config.k_global,
        "k_segment": config.k_segment,
        "test_class": config.test_class,
        "rows": totals["rows"],
        "dataset_total_tests": totals["DATASET_TOTAL_TESTS"],
        "dataset_total_failures": totals["DATASET_TOTAL_FAILURES"],
        "dropped_long_model_ids": config.dropped_long_model_ids_logged,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "lake_manifest_sha256": lake_manifest_sha256,
    }
    sidecar = out_path.with_name("prod_data_provenance.json")
    sidecar.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    return provenance


def report_contract_constants(provenance: Dict) -> str:
    """The block to paste into report_contract.py in the atomic commit."""
    rate = provenance["dataset_total_failures"] / provenance["dataset_total_tests"]
    return (
        f"DATASET_TOTAL_TESTS = {provenance['dataset_total_tests']:_}\n"
        f"DATASET_TOTAL_FAILURES = {provenance['dataset_total_failures']:_}\n"
        f"DATASET_ARTIFACT_REVISION = \"<set to the commit date>\"\n"
        f"DATASET_COVERAGE_START = \"{provenance['coverage_start']}\"\n"
        f"DATASET_COVERAGE_END = \"{provenance['coverage_end']}\"\n"
        f"# dataset-wide rate: {rate!r}\n"
    )
