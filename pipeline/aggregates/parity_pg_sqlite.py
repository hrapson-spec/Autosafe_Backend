"""Store parity gate: the artifact, the SQLite `risks` table, and the
Postgres `mot_risk` table must carry the SAME row set.

Historically they could not be proven equal: Postgres was loaded from
FINAL_MOT_REPORT.csv while SQLite was built from prod_data_clean.csv.gz --
two different files with an unrecorded relationship. With one canonical
artifact (upload_to_postgres.py now reads the .gz too) this module proves
row-set equality by canonical hash.

Canonicalization: (model_id, age_band, mileage_band, tests, failures,
risks rounded to 6dp) -- risk floats survive REAL-column storage at 6dp on
both engines... which is exactly what the artifact encodes.
"""
import csv
import gzip
import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

Row = Tuple  # (model_id, age_band, mileage_band, tests, failures, *risks)


def canonical_hash(rows: Iterable[Row]) -> str:
    """Order-independent sha256 over canonicalized rows."""
    lines = []
    for row in rows:
        model_id, age_band, mileage_band, tests, failures, *risks = row
        lines.append("|".join([
            str(model_id), str(age_band), str(mileage_band),
            str(int(tests)), str(int(failures)),
            *[f"{float(r):.6f}" for r in risks],
        ]))
    digest = hashlib.sha256()
    for line in sorted(lines):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def rows_from_artifact(path: Path) -> List[Row]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header
        return [
            (r[0], r[1], r[2], int(round(float(r[3]))), int(round(float(r[4]))),
             *[float(v) for v in r[5:]])
            for r in reader
        ]


def rows_from_sqlite(db_path: Path, table: str = "risks") -> List[Row]:
    con = sqlite3.connect(db_path)
    try:
        return [tuple(r) for r in con.execute(f"SELECT * FROM {table}")]
    finally:
        con.close()


def rows_from_postgres(database_url: str, table: str = "mot_risk") -> List[Row]:
    import psycopg2  # lazy: serving requirement, present where this runs

    conn = psycopg2.connect(database_url.replace("postgres://", "postgresql://"))
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {table}")
            return [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()


def compare(label_a: str, rows_a: List[Row],
            label_b: str, rows_b: List[Row]) -> Tuple[bool, str]:
    hash_a, hash_b = canonical_hash(rows_a), canonical_hash(rows_b)
    if hash_a == hash_b:
        return True, f"{label_a} == {label_b} ({len(rows_a)} rows, {hash_a[:12]}...)"
    return False, (
        f"{label_a} ({len(rows_a)} rows, {hash_a[:12]}...) != "
        f"{label_b} ({len(rows_b)} rows, {hash_b[:12]}...)"
    )


def run_parity(artifact_path: Path,
               sqlite_path: Optional[Path] = None,
               database_url: Optional[str] = None) -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []
    artifact_rows = rows_from_artifact(artifact_path)
    if sqlite_path is not None:
        ok, detail = compare("artifact", artifact_rows, "sqlite",
                             rows_from_sqlite(Path(sqlite_path)))
        results.append(("artifact_vs_sqlite", ok, detail))
    if database_url:
        ok, detail = compare("artifact", artifact_rows, "postgres",
                             rows_from_postgres(database_url))
        results.append(("artifact_vs_postgres", ok, detail))
    return results
