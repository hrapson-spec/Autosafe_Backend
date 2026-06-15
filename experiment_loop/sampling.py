"""sampling.py — dev sample clause + deterministic promotion cohort + snapshot identity.
PROTECTED referee module.

Dev grade uses a seeded reservoir sample (deterministic only WITHIN a fixed data
snapshot). Promotion grade must be deterministic ACROSS snapshots/reorderings, so the
cohort is chosen by a stable per-id hash, never by row order. Provenance fingerprints
(`sample_fingerprint`, `data_snapshot_id`) go into the manifest so a changed frame is
detected and treated as a distinct experiment rather than silently compared.
"""
from __future__ import annotations

import hashlib
import json

# Dev-grade sample clause: seeded reservoir => deterministic within a fixed snapshot.
DEV_SAMPLE_CLAUSE = "USING SAMPLE {n} ROWS (reservoir, 42)"

_U64 = 2 ** 64


def canonical_id_hash(test_id) -> bytes:
    """sha256 of the canonical ascii integer form of a test_id. Portable and
    process-stable (NOT Python's per-process-salted str hash); normalises int/str/
    float-int to the same digest."""
    return hashlib.sha256(str(int(test_id)).encode("ascii")).digest()


def _id_u64(test_id) -> int:
    return int.from_bytes(canonical_id_hash(test_id)[:8], "big")


def in_cohort(test_id, fraction: float) -> bool:
    """Threshold membership: a test_id is in the promotion cohort iff its stable hash
    falls below `fraction` of the 64-bit space. Per-id independent — adding or reordering
    rows never shifts an existing id's membership."""
    threshold = int(float(fraction) * _U64)
    return _id_u64(test_id) < threshold


def select_cohort(test_ids, fraction: float) -> list:
    """The subset of `test_ids` in the promotion cohort (input order preserved)."""
    return [i for i in test_ids if in_cohort(i, fraction)]


def sample_fingerprint(test_ids) -> str:
    """Order-independent sha256 over the SET of canonical test_ids — identifies exactly
    which rows were scored, regardless of frame order."""
    canon = sorted(str(int(i)) for i in set(test_ids))
    return hashlib.sha256("\n".join(canon).encode("ascii")).hexdigest()


def data_snapshot_id(*, frame_path, row_count, schema_hash, file_content_sha256,
                     test_id_set_hash, min_date, max_date) -> str:
    """Composite snapshot identity — NOT a statistical fingerprint. Any change to the
    frame's path, row count, schema, bytes, id-set, or date range yields a new id, so a
    rebuilt frame is correctly a distinct experiment (review pt 5)."""
    payload = json.dumps({
        "frame_path": str(frame_path), "row_count": int(row_count),
        "schema_hash": str(schema_hash), "file_content_sha256": str(file_content_sha256),
        "test_id_set_hash": str(test_id_set_hash),
        "min_date": str(min_date), "max_date": str(max_date),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_data_snapshot_id(frame_path, *, id_col="test_id", date_col=None) -> str:
    """Read a parquet frame and compute its composite data_snapshot_id: row count, schema
    hash, streamed file-content hash, the test_id set hash, and (optionally) a date
    column's min/max. duckdb projections keep the big frames out of memory."""
    import duckdb
    p = str(frame_path)
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    row_count = con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
    desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()
    schema_hash = hashlib.sha256(
        "\n".join(sorted(f"{r[0]}:{r[1]}" for r in desc)).encode("ascii")).hexdigest()
    ids = con.execute(f"SELECT {id_col} FROM read_parquet('{p}')").df()[id_col].tolist()
    test_id_set_hash = sample_fingerprint(ids)
    if date_col:
        mn, mx = con.execute(
            f"SELECT min({date_col}), max({date_col}) FROM read_parquet('{p}')").fetchone()
    else:
        mn = mx = ""
    con.close()
    return data_snapshot_id(frame_path=p, row_count=row_count, schema_hash=schema_hash,
                            file_content_sha256=_file_sha256(p),
                            test_id_set_hash=test_id_set_hash, min_date=mn, max_date=mx)
