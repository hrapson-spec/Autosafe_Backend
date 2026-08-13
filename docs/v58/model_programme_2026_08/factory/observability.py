"""Item-observability index (PREREG_CUBE_v2 §4; INC-2026-08-13 remediation).

The lake's `results` relation is complete; its `items` relation is not. A test
row that carries no item row means ONE of three different things, and the
factory used to render all three as the integer 0:

    populated            the test's defect items are staged and non-empty
    empty/zero + status  the test's defect items ARE observable and there are none
    NULL + status        the test's defect detail is NOT observable at all

Contract rule (PREREG §4): **availability is determined from source, partition
and ingestion coverage -- never inferred from whether a row joined.** No feature
emitter may infer "no defect" from a NULL `defects_json` or from a failed join.

Hence the two layers here:

1. a **coverage ledger** (owner-produced, gated like the P4 certification): an
   explicit declaration of which (test_date x schema_epoch x outcome_class)
   cells are dark and why. Cell rules NEVER consult the join.
2. a **row-grain decidable instrument**: a fail-bearing test (FAIL/PRS) with
   zero items is a definitional impossibility, so its items are provably absent.
   Measured lake-wide this fires on 187 rows outside the one dark day -- it is
   an evidence rule, not a heuristic, and it holds with or without a ledger.

Everything else -- a PASS with zero items inside a covered cell -- keeps its
zero. Nulling those would destroy 179,704,847 genuine observations to repair
552,806 (a 325:1 damage ratio, measured); `ITEMS_PRESENT_ZERO_DEFECTS` is
49.9%-62.1% of all passes. The repair distinguishes states; it does not blanket
-null zeros.

The distinction between a zero that is CERTIFIED observable and a zero that is
merely ASSUMED observable is carried explicitly, per row and per packet set, so
a consumer can require certification instead of inheriting an assumption.
"""
import csv
import hashlib
import os
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

# --- the row-grain state vocabulary -----------------------------------------

#: items staged for this test and non-empty.
PRESENT_WITH_DEFECTS = "present_with_defects"
#: zero items, and the cell is CERTIFIED covered by the ledger -> a real zero.
PRESENT_ZERO_DEFECTS = "present_zero_defects"
#: zero items, cell coverage UNDECLARED (no ledger rule) -> a zero taken on
#: assumption. Value-identical to PRESENT_ZERO_DEFECTS by deliberate design
#: (the 325:1 damage ratio), but never certified, and separable downstream.
ASSUMED_ZERO_DEFECTS = "assumed_zero_defects"
#: cell declared structurally dark by the publisher (e.g. non-definitive
#: outcomes under results_extracts, 2024-25). Counts are NULL.
UNAVAILABLE = "unavailable"
#: evidence says items SHOULD be here and they are not (dark day inside a
#: covered partition; fail-bearing test with zero items). Counts are NULL.
EXPECTED_MISSING = "expected_missing"

#: States in which a zero is a real observation.
OBSERVED_STATES: Tuple[str, ...] = (PRESENT_WITH_DEFECTS, PRESENT_ZERO_DEFECTS,
                                    ASSUMED_ZERO_DEFECTS)
#: States in which the quantity is UNKNOWN and must be emitted NULL + status.
UNOBSERVED_STATES: Tuple[str, ...] = (UNAVAILABLE, EXPECTED_MISSING)
ALL_STATES: Tuple[str, ...] = OBSERVED_STATES + UNOBSERVED_STATES

#: Packet-set level: how much the builder can vouch for its own zeros.
COVERAGE_CERTIFIED = "certified"
COVERAGE_ASSUMED = "assumed_covered"

# --- the cell ledger ---------------------------------------------------------

CELL_COVERED = "covered"
CELL_UNAVAILABLE = "unavailable"
CELL_EXPECTED_MISSING = "expected_missing"
CELL_COVERAGES = (CELL_COVERED, CELL_UNAVAILABLE, CELL_EXPECTED_MISSING)

#: Expectation and ATTRIBUTION are separate fields (PREREG §4 amendment): the
#: 2024-12-31 gap is byte-proven NOT ours, so "expected missing" cannot mean
#: "our bug". Attribution is a property of the CELL, recorded in the ledger and
#: the manifest; expectation is the per-row state above.
ATTRIBUTION_PUBLICATION_SHORT = "publication_short"
ATTRIBUTION_INGEST_LOSS = "ingest_loss"
ATTRIBUTION_UNKNOWN = "unknown"
ATTRIBUTIONS = (ATTRIBUTION_PUBLICATION_SHORT, ATTRIBUTION_INGEST_LOSS,
                ATTRIBUTION_UNKNOWN)

OUTCOME_DEFINITIVE = "definitive"
OUTCOME_NON_DEFINITIVE = "non_definitive"

#: Outcomes DVSA counts as a completed test with a result (mirrors atoms.py).
_DEFINITIVE = ("PASS", "FAIL", "PRS")
#: A FAIL or PRS cannot have zero reasons-for-rejection.
_FAIL_BEARING = ("FAIL", "PRS")

LEDGER_HEADER = ("test_date", "schema_epoch", "outcome_class", "coverage",
                 "attribution", "note")


class LedgerError(ValueError):
    """The item-coverage ledger is malformed or ambiguous."""


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class CoverageRule:
    """One declared cell. A NULL field is a wildcard ('any')."""

    coverage: str
    test_date: Optional[date] = None
    schema_epoch: Optional[str] = None
    outcome_class: Optional[str] = None
    attribution: str = ATTRIBUTION_UNKNOWN
    note: str = ""

    def __post_init__(self) -> None:
        if self.coverage not in CELL_COVERAGES:
            raise LedgerError(
                f"coverage {self.coverage!r} is not one of {CELL_COVERAGES}. A "
                f"cell is declared covered, unavailable or expected_missing -- "
                f"there is no implicit fourth option.")
        if self.attribution not in ATTRIBUTIONS:
            raise LedgerError(
                f"attribution {self.attribution!r} is not one of {ATTRIBUTIONS}")
        if (self.outcome_class is not None
                and self.outcome_class not in (OUTCOME_DEFINITIVE, OUTCOME_NON_DEFINITIVE)):
            raise LedgerError(
                f"outcome_class {self.outcome_class!r} is not "
                f"{OUTCOME_DEFINITIVE!r} or {OUTCOME_NON_DEFINITIVE!r}")

    @property
    def specificity(self) -> int:
        return sum(1 for f in (self.test_date, self.schema_epoch, self.outcome_class)
                   if f is not None)

    @property
    def is_default(self) -> bool:
        """The all-wildcard rule: what an otherwise unmatched cell resolves to."""
        return self.specificity == 0

    def overlaps(self, other: "CoverageRule") -> bool:
        """True when some cell could match BOTH rules."""
        for mine, theirs in ((self.test_date, other.test_date),
                             (self.schema_epoch, other.schema_epoch),
                             (self.outcome_class, other.outcome_class)):
            if mine is not None and theirs is not None and mine != theirs:
                return False
        return True

    def predicate_sql(self, alias: str = "r") -> str:
        terms: List[str] = []
        if self.test_date is not None:
            terms.append(f"{alias}.test_date = DATE '{self.test_date.isoformat()}'")
        if self.schema_epoch is not None:
            terms.append(f"{alias}.schema_epoch = {_sql_str(self.schema_epoch)}")
        if self.outcome_class is not None:
            terms.append(f"{outcome_class_sql(alias)} = {_sql_str(self.outcome_class)}")
        return " AND ".join(terms) if terms else "TRUE"

    def as_manifest(self) -> dict:
        return {"coverage": self.coverage,
                "test_date": self.test_date.isoformat() if self.test_date else None,
                "schema_epoch": self.schema_epoch,
                "outcome_class": self.outcome_class,
                "attribution": self.attribution,
                "specificity": self.specificity,
                "note": self.note}


def outcome_class_sql(alias: str = "r") -> str:
    definitive = ", ".join(_sql_str(o) for o in _DEFINITIVE)
    return (f"CASE WHEN {alias}.outcome IN ({definitive}) THEN "
            f"{_sql_str(OUTCOME_DEFINITIVE)} ELSE {_sql_str(OUTCOME_NON_DEFINITIVE)} END")


def fail_bearing_sql(alias: str = "r") -> str:
    vals = ", ".join(_sql_str(o) for o in _FAIL_BEARING)
    return f"({alias}.outcome IN ({vals}))"


class CoverageLedger:
    """The declared item-coverage cells. Empty ledger == nothing declared."""

    def __init__(self, rules: Sequence[CoverageRule] = (),
                 path: Optional[str] = None, sha256: Optional[str] = None):
        self.rules: Tuple[CoverageRule, ...] = tuple(rules)
        self.path = path
        self.sha256 = sha256
        self._assert_unambiguous()

    def _assert_unambiguous(self) -> None:
        """Equal-specificity rules that could match the same cell must agree.

        Without this, the resolution order would be an accident of file order --
        exactly the kind of implicit default this remediation exists to remove.
        """
        for i, first in enumerate(self.rules):
            for second in self.rules[i + 1:]:
                if first.specificity != second.specificity:
                    continue
                if first.overlaps(second) and first.coverage != second.coverage:
                    raise LedgerError(
                        f"ambiguous item-coverage ledger: {first.as_manifest()} and "
                        f"{second.as_manifest()} are equally specific, can match the "
                        f"same cell, and disagree. Declare the intended cell "
                        f"explicitly -- resolution must never depend on row order.")

    # --- properties -------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return not self.rules

    @property
    def has_default(self) -> bool:
        return any(r.is_default for r in self.rules)

    @property
    def coverage_mode(self) -> str:
        """certified only when EVERY cell resolves to a declared coverage."""
        return COVERAGE_CERTIFIED if self.has_default else COVERAGE_ASSUMED

    @property
    def dark_rules(self) -> Tuple[CoverageRule, ...]:
        return tuple(r for r in self.rules if r.coverage != CELL_COVERED)

    # --- SQL --------------------------------------------------------------

    def coverage_case_sql(self, alias: str = "r") -> str:
        """Cell coverage for one results row; NULL when nothing is declared.

        Rules are emitted most-specific-first. Equal-specificity overlaps are
        rejected at construction, so the emitted order is immaterial to the
        result -- it is fixed here only so the rendered SQL is deterministic.
        """
        if not self.rules:
            return "CAST(NULL AS VARCHAR)"
        ordered = sorted(
            self.rules,
            key=lambda r: (-r.specificity, str(r.test_date), str(r.schema_epoch),
                           str(r.outcome_class), r.coverage))
        whens = "".join(f" WHEN {r.predicate_sql(alias)} THEN {_sql_str(r.coverage)}"
                        for r in ordered)
        return f"CASE{whens} ELSE CAST(NULL AS VARCHAR) END"

    def dark_cell_predicate_sql(self, alias: str = "r") -> Optional[str]:
        """Predicate selecting rows the ledger declares dark (or None)."""
        dark = self.dark_rules
        if not dark:
            return None
        return "(" + " OR ".join(f"({r.predicate_sql(alias)})" for r in dark) + ")"

    def as_manifest(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "coverage_mode": self.coverage_mode,
            "n_rules": len(self.rules),
            "has_default_rule": self.has_default,
            "rules": [r.as_manifest() for r in self.rules],
            "resolution_order": (
                "cell rules first (most specific wins; they NEVER consult the "
                "join), then join, then the fail-bearing definitional "
                "impossibility, then the covered-cell residual."),
        }


def observability_case_sql(ledger: CoverageLedger, alias: str = "r",
                           item_alias: str = "a") -> str:
    """Per-test items_observability, resolved in the contract's rule order.

    1-2. the cell ledger (never consults the join)
    3.   the test has >=1 item row                     -> present_with_defects
    4.   zero items on a FAIL/PRS                      -> expected_missing
    5.   zero items in a CERTIFIED covered cell        -> present_zero_defects
    5'.  zero items in an UNDECLARED cell              -> assumed_zero_defects
    """
    cell = ledger.coverage_case_sql(alias)
    return (
        "CASE"
        f" WHEN ({cell}) = {_sql_str(CELL_UNAVAILABLE)} THEN {_sql_str(UNAVAILABLE)}"
        f" WHEN ({cell}) = {_sql_str(CELL_EXPECTED_MISSING)} THEN {_sql_str(EXPECTED_MISSING)}"
        f" WHEN {item_alias}.test_id IS NOT NULL THEN {_sql_str(PRESENT_WITH_DEFECTS)}"
        f" WHEN {fail_bearing_sql(alias)} THEN {_sql_str(EXPECTED_MISSING)}"
        f" WHEN ({cell}) = {_sql_str(CELL_COVERED)} THEN {_sql_str(PRESENT_ZERO_DEFECTS)}"
        f" ELSE {_sql_str(ASSUMED_ZERO_DEFECTS)} END"
    )


def observed_predicate_sql(column: str) -> str:
    vals = ", ".join(_sql_str(s) for s in OBSERVED_STATES)
    return f"({column} IN ({vals}))"


def is_observed(state: Optional[str]) -> bool:
    """True when a zero from this row is a real observation."""
    return state in OBSERVED_STATES


# --- loading -----------------------------------------------------------------

def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def parse_ledger_rows(rows) -> List[CoverageRule]:
    out: List[CoverageRule] = []
    for lineno, raw in enumerate(rows, start=2):
        if not raw or all(not (v or "").strip() for v in raw.values()):
            continue
        unknown = set(raw) - set(LEDGER_HEADER)
        if unknown:
            raise LedgerError(
                f"item-coverage ledger line {lineno}: unknown column(s) "
                f"{sorted(unknown)}; header is {'|'.join(LEDGER_HEADER)}")
        day = _clean(raw.get("test_date"))
        try:
            out.append(CoverageRule(
                coverage=(_clean(raw.get("coverage")) or ""),
                test_date=date.fromisoformat(day) if day else None,
                schema_epoch=_clean(raw.get("schema_epoch")),
                outcome_class=_clean(raw.get("outcome_class")),
                attribution=(_clean(raw.get("attribution")) or ATTRIBUTION_UNKNOWN),
                note=(_clean(raw.get("note")) or "")))
        except LedgerError as exc:
            raise LedgerError(f"item-coverage ledger line {lineno}: {exc}") from exc
    return out


def load_coverage_ledger(path: Optional[str]) -> CoverageLedger:
    """Read the pipe-delimited cell ledger. Absent path -> empty ledger.

    An EMPTY ledger is not an error: it means coverage is undeclared, the packet
    set is marked `assumed_covered`, and a consumer may refuse it. It never
    means "everything is covered".
    """
    if not path:
        return CoverageLedger()
    if not os.path.exists(path):
        raise LedgerError(
            f"item-coverage ledger not found: {path}. Pass no --item-coverage-csv "
            f"to build in assumed_covered mode deliberately; a missing file is "
            f"never silently treated as 'everything is covered'.")
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="|")
        rules = parse_ledger_rows(reader)
    return CoverageLedger(rules, path=path, sha256=digest.hexdigest())


# --- day-grain rollup --------------------------------------------------------

#: Extra day-atom columns the SQL emits so the frame can always reconstruct the
#: denominator it was scored on (ITEM_OBSERVABILITY_DESIGN §3.2).
DAY_COUNT_COLUMNS: Tuple[str, ...] = (
    "n_tests_items_observed", "n_tests_items_unobserved",
    "n_tests_items_with_defects", "n_tests_items_zero_defects",
    "n_tests_items_unavailable", "n_tests_items_expected_missing",
)

DAY_PARTIAL = "partial"


def day_rollup(n_observed: int, n_unobserved: int, n_with_defects: int,
               n_unavailable: int, n_expected_missing: int) -> str:
    """One day's item-observability status from its per-test counts."""
    if n_observed and n_unobserved:
        return DAY_PARTIAL
    if not n_observed:
        if n_expected_missing:
            return EXPECTED_MISSING
        if n_unavailable:
            return UNAVAILABLE
        return UNAVAILABLE
    return PRESENT_WITH_DEFECTS if n_with_defects else PRESENT_ZERO_DEFECTS


#: A vehicle with no prior test-days at all: its item history is EMPTY, not
#: unobserved. Kept distinct from 'none' so a reader can never confuse "we saw
#: nothing" with "there was nothing to see".
NO_PRIORS = "no_priors"


def history_status(n_days_observed: int, n_days_unobserved: int,
                   n_prior_days: Optional[int] = None) -> str:
    """no_priors / none / partial / full across a vehicle's prior test-days."""
    if n_prior_days is not None and n_prior_days == 0:
        return NO_PRIORS
    if n_days_observed == 0:
        return "none"
    return "full" if n_days_unobserved == 0 else "partial"


def counts_manifest(counts: Dict[str, int]) -> dict:
    """Normalise a staged-atom count dict for the BUILD_MANIFEST."""
    total = sum(int(counts.get(k, 0) or 0)
                for k in ("n_tests_items_observed", "n_tests_items_unobserved"))
    observed = int(counts.get("n_tests_items_observed", 0) or 0)
    return {
        "tests": total,
        "items_observed": observed,
        "items_unobserved": int(counts.get("n_tests_items_unobserved", 0) or 0),
        "items_with_defects": int(counts.get("n_tests_items_with_defects", 0) or 0),
        "items_zero_defects": int(counts.get("n_tests_items_zero_defects", 0) or 0),
        "items_unavailable": int(counts.get("n_tests_items_unavailable", 0) or 0),
        "items_expected_missing": int(counts.get("n_tests_items_expected_missing", 0) or 0),
        "item_join_rate": (int(counts.get("n_tests_items_with_defects", 0) or 0) / total
                           if total else None),
        "item_observed_rate": (observed / total) if total else None,
    }
