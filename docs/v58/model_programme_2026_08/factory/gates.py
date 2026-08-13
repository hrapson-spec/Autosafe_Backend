"""Build gates. Each raises BEFORE any output is written.

- P4 certification: the corrected-severity cross-tab must be certified PASS by
  the owner (Stage 0) or the factory refuses to run at all. Every severity axis
  in B3/B4 and the packets defect payload is only meaningful under it.
- Target fence: training emission refuses any target date >= 2024-01-01 unless
  --eval-slice; the 2025-H2 confirmation slice refuses unless a prereg sha is
  supplied.
- duckdb pin: the salted-hash sampling and every measured selection share are
  pinned to duckdb 1.5.5; a different build would silently reshard the world.
"""
import json
import os
from datetime import date
from typing import Optional

import duckdb

from . import EXPECTED_DUCKDB

#: Training emission fence (contract, Emission).
TRAINING_TARGET_FENCE = date(2024, 1, 1)
P4_CERTIFICATION_FILENAME = "p4_crosstab_certification.json"


class GateFailure(RuntimeError):
    """A build gate refused the build."""


def assert_duckdb_pin(observed: Optional[str] = None) -> str:
    version = observed if observed is not None else duckdb.__version__
    if version != EXPECTED_DUCKDB:
        raise GateFailure(
            f"duckdb {version} != pinned {EXPECTED_DUCKDB}: the salted-hash "
            f"sample membership is a function of the hash implementation, so an "
            f"unpinned build silently reshards every rung."
        )
    return version


def assert_p4_certified(cert_path: str) -> dict:
    """Refuse to build unless the P4 cross-tab certification exists and PASSes."""
    if not os.path.exists(cert_path):
        raise GateFailure(
            f"P4 certification absent ({cert_path}). The corrected severity "
            f"semantics (post-2018 dangerous = dangerous_mark 'D'; 'M' = MINOR, "
            f"non-fail; fail-bearing = {{F,P}}) are UNCERTIFIED, so every severity "
            f"axis this factory emits would be unverified. Owner produces it in "
            f"Stage 0."
        )
    with open(cert_path, "r", encoding="utf-8") as fh:
        cert = json.load(fh)
    verdict = str(cert.get("verdict", "")).strip().upper()
    if verdict != "PASS":
        raise GateFailure(
            f"P4 certification verdict is {verdict or '(absent)'}, not PASS "
            f"({cert_path}). Refusing to build."
        )
    return cert


def assert_target_fence(window_start: date, window_end: date,
                        eval_slice: bool, build_confirmation: bool) -> None:
    """Training emission refuses targets on/after 2024-01-01 (contract)."""
    if window_end <= window_start:
        raise GateFailure(
            f"window [{window_start}, {window_end}) is empty or inverted; fences "
            f"are inclusive-exclusive."
        )
    if eval_slice or build_confirmation:
        return
    if window_end > TRAINING_TARGET_FENCE:
        raise GateFailure(
            f"training emission refuses target dates >= {TRAINING_TARGET_FENCE} "
            f"(requested window ends {window_end}). 2024 is the selection year and "
            f"2025-H2 is the sealed confirmation slice: use --eval-slice."
        )


def assert_confirmation_prereg(prereg_sha: Optional[str], prereg_path: Optional[str]) -> str:
    """--build-confirmation refuses unless a CONFIRMATION_PREREG sha is present."""
    if not prereg_sha:
        raise GateFailure(
            "--build-confirmation requires --confirmation-prereg-sha: the 2025-H2 "
            "slice is sealed and may only be built against a registered "
            "pre-registration. The factory emits its DEFINITION by default and "
            "never builds it."
        )
    if prereg_path and not os.path.exists(prereg_path):
        raise GateFailure(f"confirmation prereg file not found: {prereg_path}")
    if prereg_path:
        import hashlib

        digest = hashlib.sha256()
        with open(prereg_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != prereg_sha:
            raise GateFailure(
                f"confirmation prereg sha mismatch: file {actual} != supplied "
                f"{prereg_sha}"
            )
        return actual
    return prereg_sha


def assert_no_unknown_dispositions(n_unknown: int, sample_codes: Optional[list] = None) -> None:
    """Fail closed on any rfr_type_code outside the contract's era table."""
    if n_unknown:
        raise GateFailure(
            f"{n_unknown:,} item row(s) carry an rfr_type_code outside the "
            f"contract's era table (examples: {sample_codes or 'n/a'}). "
            f"Post-2018 is case-sensitive: lowercase 'm' is NOT minor here. "
            f"Classify a new publication code deliberately in factory/severity.py."
        )


def assert_coverage_ledger_consistent(n_dark_rows_with_items: int,
                                      ledger_path: Optional[str] = None) -> None:
    """Refuse a coverage ledger that contradicts the lake.

    Cell rules outrank the join by design (availability must not be inferred
    from whether a row joined), so a wrong rule would silently DISCARD real
    defect data -- the mirror image of INC-2026-08-13. Any results row inside a
    declared-dark cell that actually carries item rows is a refusal.
    """
    if n_dark_rows_with_items:
        raise GateFailure(
            f"{n_dark_rows_with_items:,} results row(s) inside a cell the "
            f"item-coverage ledger declares DARK do carry item rows "
            f"({ledger_path or 'ledger'}). A cell rule outranks the join, so "
            f"building would throw those defects away and emit NULL in their "
            f"place. Narrow the rule to the cell that is genuinely dark."
        )


def assert_no_unknown_vocabulary(n_unknown: int) -> None:
    """Fail closed on test_type/outcome outside the DVSA vocabularies."""
    if n_unknown:
        raise GateFailure(
            f"{n_unknown:,} results row(s) carry a test_type or outcome outside "
            f"the DVSA vocabulary (pipeline.lake.target_population). Refusing to "
            f"build a target population: defaulting either way silently changes it."
        )
