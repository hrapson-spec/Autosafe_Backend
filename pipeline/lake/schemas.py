"""Schema-epoch registry for the DVSA anonymised-bulk source files.

The publication's file formats changed across its 20-year span (legacy annual
extracts vs the post-2017 MTS-era files: different delimiters and, for test
items, an added dangerous-mark column). Every reader resolves its schema HERE,
by exact header match, and refuses unknown layouts loudly -- silent coercion
of a drifted header is how ID columns get parsed as floats and truncated
(the recorded `check_match.py` defect).

The column sets below are the publication layouts known to this repo's
history. If the downloaded release differs, ingest fails with the observed
header printed; add the new layout HERE (one registry entry), never by
loosening a reader.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


class SchemaDetectionError(ValueError):
    """Raised when a source file's header matches no registered schema."""


@dataclass(frozen=True)
class SourceSchema:
    """One known source-file layout.

    columns maps SOURCE header name -> CANONICAL column name. Detection is
    by exact, order-insensitive equality of the source-column set, so a new
    or renamed column in a future release can never be silently dropped.
    """

    name: str                      # e.g. "results_mts"
    kind: str                      # "results" | "items"
    delimiter: str
    columns: Dict[str, str]
    notes: str = ""
    escape: str = '"'              # 2018+ MTS chunk exports use backslash escapes

    @property
    def source_columns(self) -> frozenset:
        return frozenset(self.columns)


# ---------------------------------------------------------------------------
# Canonical column names (lake-side). IDs are BIGINT, read as VARCHAR first
# and cast by normalize.clean_id_expr (never inferred -> never float).
# ---------------------------------------------------------------------------
RESULTS_CANONICAL = [
    "test_id", "vehicle_id", "test_date", "test_class_id", "test_type",
    "test_result", "test_mileage", "postcode_area", "make", "model",
    "colour", "fuel_type", "cylinder_capacity", "first_use_date",
]

ITEMS_CANONICAL = ["test_id", "rfr_id", "rfr_type_code", "location_id", "dangerous_mark"]


_IDENTITY_RESULTS = {c: c for c in RESULTS_CANONICAL}

REGISTRY: List[SourceSchema] = [
    SourceSchema(
        name="results_mts",
        kind="results",
        delimiter="|",
        columns=dict(_IDENTITY_RESULTS),
        notes="MTS-era pipe-delimited test_result files (2017+ releases; "
              "full-depth republications use this layout for all years).",
    ),
    SourceSchema(
        name="results_csv",
        kind="results",
        delimiter=",",
        columns=dict(_IDENTITY_RESULTS),
        notes="Comma-delimited variant (the frozen research tree's "
              "'MOT Test Results 24/test_result_*.csv' shape).",
    
        escape="\\",
    ),
    SourceSchema(
        name="items_mts",
        kind="items",
        delimiter="|",
        columns={c: c for c in ITEMS_CANONICAL},
        notes="MTS-era item files, with the post-2018 dangerous mark.",
    ),
    SourceSchema(
        name="items_legacy",
        kind="items",
        delimiter="|",
        columns={c: c for c in ITEMS_CANONICAL if c != "dangerous_mark"},
        notes="Legacy item files without the dangerous-mark column "
              "(pre-May-2018 taxonomy has no dangerous class).",
    ),
    SourceSchema(
        name="items_csv",
        kind="items",
        delimiter=",",
        columns={c: c for c in ITEMS_CANONICAL},
        notes="Comma-delimited item variant.",
    
        escape="\\",
    ),
]


def sniff_header(first_line: str) -> Tuple[str, List[str]]:
    """Split a raw header line on the more plausible delimiter.

    Returns (delimiter, fields). Prefers '|' when both appear (legacy files
    quote nothing, so a comma inside a value is possible; a pipe is not).
    """
    line = first_line.rstrip("\r\n")
    if "|" in line:
        return "|", [f.strip().strip('"') for f in line.split("|")]
    return ",", [f.strip().strip('"') for f in line.split(",")]


def detect_schema(header_fields: List[str], kind: str,
                  delimiter: Optional[str] = None) -> SourceSchema:
    """Resolve the registered schema for a source file's header.

    Exact set-match on lowercased source columns; raises
    SchemaDetectionError with the observed header and the near-misses when
    nothing matches, so a drifted release is a loud day-1 failure.
    """
    observed = frozenset(f.lower() for f in header_fields)
    candidates = [s for s in REGISTRY if s.kind == kind]
    for schema in candidates:
        if schema.source_columns == observed and (delimiter is None or schema.delimiter == delimiter):
            return schema
    detail = "; ".join(
        f"{s.name}: missing={sorted(s.source_columns - observed)[:4]} "
        f"extra={sorted(observed - s.source_columns)[:4]}"
        for s in candidates
    )
    raise SchemaDetectionError(
        f"no registered {kind} schema matches header {sorted(observed)}; "
        f"near-misses: {detail}. If the publication layout changed, add a "
        f"registry entry in pipeline/lake/schemas.py -- do not loosen a reader."
    )


def detect_schema_for_file(path: str, kind: str) -> SourceSchema:
    """Detect the schema from a file's first line."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline()
    delimiter, fields = sniff_header(first_line)
    return detect_schema(fields, kind, delimiter)


def sniff_escape(path: str, default: str = '"', sample_bytes: int = 4_000_000) -> str:
    """Per-file escape-convention detection (2026-08-11): the nominal
    comma epoch spans at least two real conventions — 2018-2021 chunk
    exports use backslash-escaped quotes, 2022+ timestamped exports use
    standard doubled quotes. Headers are identical, so the registry cannot
    discriminate statically; the file's own bytes decide, falling back to
    the registry default when neither marker appears."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(sample_bytes)
    except OSError:
        return default
    if data.count(rb'\"') > 0:
        return "\\"
    if data.count(b'""') > 0:
        return '"'
    return default
