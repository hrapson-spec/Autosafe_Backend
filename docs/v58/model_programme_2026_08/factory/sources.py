"""Injected input relations + the fail-loud year gate.

Every input is a duckdb-readable path/glob supplied by the owner at run time
(contract v2 §2: the primary target is the 1/100 panel substrate -- results
shards glob + items panel parquet -- and full-population is the SAME code with
different paths). Nothing here hardcodes a lake location.

Year discipline (contract, Inputs): builds take an EXPLICIT year list and fail
loud on a missing year -- never silently skip. The gate reads the observed
years from the relation itself (year(test_date)), so it is correct for both the
hive lake and a flat shard glob, and it runs BEFORE any output is written.
"""
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set

_SQL_PREFIXES = ("read_", "select", "(", "with ")


class MissingYears(RuntimeError):
    """The requested year list is not fully present in the results relation."""


class MissingInput(FileNotFoundError):
    """An injected input path does not exist."""


def as_relation(path_or_sql: str, hive: bool = True) -> str:
    """Turn an injected path/glob into a FROM-able duckdb expression.

    A string that already looks like SQL (read_parquet(...), a SELECT, a
    parenthesised subquery) is passed through untouched, so the owner can inject
    an arbitrary relation (e.g. a pre-filtered panel view).
    """
    stripped = path_or_sql.strip()
    if stripped.lower().startswith(_SQL_PREFIXES):
        return stripped
    escaped = stripped.replace("'", "''")
    hive_opt = ", hive_partitioning=true" if hive else ""
    return f"read_parquet('{escaped}', union_by_name=true{hive_opt})"


@dataclass(frozen=True)
class Inputs:
    """Read-only input relations. Paths are injected; none are defaulted."""

    results: str
    items: str
    item_detail_csv: str
    item_group_csv: str
    #: 2024-2025 only; OPTIONAL. No feature may REQUIRE it -- diagnostics only.
    completed_ts: Optional[str] = None
    #: mdr_rfr_location.csv for B6 coarse position groups; absent -> B6 NULLs.
    location_csv: Optional[str] = None

    @property
    def results_rel(self) -> str:
        return as_relation(self.results)

    @property
    def items_rel(self) -> str:
        return as_relation(self.items)

    @property
    def completed_ts_rel(self) -> Optional[str]:
        return as_relation(self.completed_ts) if self.completed_ts else None

    def assert_lookup_present(self) -> None:
        for path in (self.item_detail_csv, self.item_group_csv):
            if not os.path.exists(path):
                raise MissingInput(
                    f"lookup table missing: {path}. The class-4 taxonomy is a hard "
                    f"input -- without it every rfr_id becomes a catalogue miss."
                )


def observed_years(con, results_relation: str) -> Set[int]:
    """Distinct calendar years present in a results relation.

    Derived from test_date, not from a hive key: a flat panel shard has no
    test_year column, and a hive key that disagrees with test_date would be a
    partitioning defect we must not inherit.
    """
    rows = con.execute(
        f"SELECT DISTINCT year(test_date) AS y FROM {results_relation} "
        f"WHERE test_date IS NOT NULL"
    ).fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


def assert_years_present(con, results_relation: str, years: Sequence[int]) -> List[int]:
    """FAIL LOUD before any output is written when a requested year is absent."""
    requested = sorted({int(y) for y in years})
    if not requested:
        raise MissingYears("empty year list: a build must name its input years explicitly")
    present = observed_years(con, results_relation)
    missing = [y for y in requested if y not in present]
    if missing:
        raise MissingYears(
            f"requested year(s) {missing} are absent from the results relation "
            f"(observed {sorted(present)}). 2005-2014 may be Drive-parked: restore "
            f"them or drop them from --years deliberately -- the build will not "
            f"silently skip a year, because a missing history year silently "
            f"truncates every prior-history feature."
        )
    return requested


def year_filter_sql(alias: str, years: Iterable[int]) -> str:
    """Restrict a results/items-joined relation to the explicit year list."""
    ys = ", ".join(str(int(y)) for y in sorted(set(years)))
    return f"(year({alias}.test_date) IN ({ys}))"


def test_class_filter_sql(alias: str, classes: Optional[Sequence[str]]) -> str:
    """Optional test-class restriction (the class-4 taxonomy is class-scoped)."""
    if not classes:
        return "TRUE"
    vals = ", ".join(f"'{str(c).strip()}'" for c in classes)
    return f"(trim({alias}.test_class_id) IN ({vals}))"
