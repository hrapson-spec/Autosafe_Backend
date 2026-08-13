"""Gate-4 fixtures: the item-observability conflation (R3, 2026-08-13).

Run:
    cd docs/v58/model_programme_2026_08
    ../../../.venv/bin/python -m pytest out/cube/test_item_observability_fixtures.py -v

These fixtures do NOT modify factory/*.py. They are executable evidence for
ITEM_OBSERVABILITY_DESIGN.md, in two layers:

  * `test_conflation_*`  PASS TODAY. They assert the defect: item-absence and
    zero-defects produce identical emitted values. They must FAIL once the
    repair lands -- that is the point.
  * `test_repaired_*`    xfail(strict=True). They assert the repaired
    semantics. They fail today (expected). The moment the repair lands they
    XPASS, and strict=True turns the suite RED so the owner must promote them
    to plain tests. That is the ratchet.

The lake shapes below are the ones MEASURED in the real lake (see the design
doc's coverage table), not invented:

  COVERED_DAY   a day inside an items-published partition        (all years)
  DARK_DAY      a day inside a published partition carrying no
                item rows at all -- the measured 2024-12-31 gap
                (0 of 41,349 tests have items; neighbours ~0.63)
  DARK_YEAR     a whole partition with results but zero item
                rows -- the measured 2024/2025 non-definitive
                outcome class (511,270 tests, 0 with items)
"""
import importlib.util
import os
import sys
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROGRAMME = os.path.dirname(os.path.dirname(_HERE))          # .../model_programme_2026_08
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_PROGRAMME)))  # repo root
for _p in (_PROGRAMME, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from factory import emit, sampling                                    # noqa: E402
from factory.blocks import BLOCK_COLUMNS                              # noqa: E402
from factory.fixtures import FixtureLake, write_p4_certification      # noqa: E402
from factory.fixtures.generate import ItemRow, TestRow                # noqa: E402

# ---------------------------------------------------------------------------
# lake geometry (mirrors the measured lake)
# ---------------------------------------------------------------------------
PRIOR_YEAR = 2019
DARK_YEAR = 2020
TARGET_YEAR = 2021
COVERED_DAY = date(PRIOR_YEAR, 6, 3)
DARK_DAY = date(PRIOR_YEAR, 12, 31)        # the measured 2024-12-31 shape
DARK_YEAR_DAY = date(DARK_YEAR, 6, 3)
TARGET_DAY = date(TARGET_YEAR, 6, 1)
YEARS = (PRIOR_YEAR, DARK_YEAR, TARGET_YEAR)

FIRST_USE = date(2012, 4, 1)
MILEAGE_PRIOR = 60_000
MILEAGE_TARGET = 80_000

#: post-2018 brake rfr_id from the fixture catalogue.
BRAKES = "20001"

# Vehicles. The comment on each is its TRUE physical defect history -- which is
# exactly the thing the emitted frame is supposed to represent.
V_WITNESS = 900        # covered-day witness: proves COVERED_DAY carries items
V_CLEAN = 901          # PASS, genuinely zero defects, covered day
V_DAYDARK = 902        # PASS, 3 real brake defects, DARK_DAY -> unpublished
V_DEFECTS = 903        # PASS, 3 real brake defects, covered day -> published
V_FAILDARK = 904       # FAIL, real defects, zero item rows  -> impossible state
V_ABORTED = 905        # ABORTED, zero item rows -> publisher class
V_YEARDARK = 906       # PASS, 3 real brake defects, DARK_YEAR -> unpublished
V_PAIR_A = 910         # PASS, genuinely zero defects, covered day
V_PAIR_B = 911         # PASS, 3 real brake defects, covered day -> dropped


def _target(lake: FixtureLake, vehicle: int, test_id: int) -> None:
    lake.add_test(TestRow(test_id=test_id, vehicle_id=vehicle, test_date=TARGET_DAY,
                          test_type="NT", outcome="PASS",
                          test_mileage=MILEAGE_TARGET, first_use_date=FIRST_USE))


def build_lake(root: str) -> FixtureLake:
    lake = FixtureLake(root)

    def prior(vehicle: int, test_id: int, day: date, outcome: str,
              n_brake_items: int = 0) -> None:
        lake.add_test(TestRow(test_id=test_id, vehicle_id=vehicle, test_date=day,
                              test_type="NT", outcome=outcome,
                              test_mileage=MILEAGE_PRIOR, first_use_date=FIRST_USE))
        for _ in range(n_brake_items):
            lake.add_item(ItemRow(test_id=test_id, rfr_id=BRAKES,
                                  rfr_type_code="F", location_id="31"))

    # The witness makes COVERED_DAY a covered cell: some test on that day has
    # items. Without it, COVERED_DAY would itself be dark and the whole
    # experiment would be confounded.
    prior(V_WITNESS, 90_000, COVERED_DAY, "FAIL", n_brake_items=2)

    prior(V_CLEAN, 90_101, COVERED_DAY, "PASS", n_brake_items=0)
    prior(V_DAYDARK, 90_201, DARK_DAY, "PASS", n_brake_items=0)
    prior(V_DEFECTS, 90_301, COVERED_DAY, "PASS", n_brake_items=3)
    prior(V_FAILDARK, 90_401, COVERED_DAY, "FAIL", n_brake_items=0)
    prior(V_ABORTED, 90_501, COVERED_DAY, "ABORTED", n_brake_items=0)
    prior(V_YEARDARK, 90_601, DARK_YEAR_DAY, "PASS", n_brake_items=0)
    prior(V_PAIR_A, 91_001, COVERED_DAY, "PASS", n_brake_items=0)
    prior(V_PAIR_B, 91_101, COVERED_DAY, "PASS", n_brake_items=0)

    for vehicle, tid in ((V_CLEAN, 91_500), (V_DAYDARK, 91_501), (V_DEFECTS, 91_502),
                         (V_FAILDARK, 91_503), (V_ABORTED, 91_504),
                         (V_YEARDARK, 91_505), (V_PAIR_A, 91_506), (V_PAIR_B, 91_507)):
        _target(lake, vehicle, tid)
    return lake


def run(tmp_path) -> Tuple[List[dict], List[dict]]:
    """Write the fixture lake, run preflight+prepare+scan, return (frames, packets)."""
    lake = build_lake(str(tmp_path / "lake"))
    inputs = lake.write(years=YEARS)
    cert = write_p4_certification(str(tmp_path / "cert" / "p4_crosstab_certification.json"))
    config = emit.BuildConfig(
        staging_dir=str(tmp_path / "stage"), output_dir=str(tmp_path / "out"),
        memory_limit="1GB", temp_directory=str(tmp_path / "duck_tmp"),
        max_temp_directory_size="512MiB", n_buckets=2, write_batch_rows=1000,
        p4_certification_path=cert)
    recipe = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))
    factory = emit.Factory(inputs, config)
    factory.connect()
    factory.preflight(list(YEARS), [recipe])
    factory.prepare(list(YEARS), [recipe])
    frames, packets = [], []
    for row, packet_rows in factory.scan(recipe):
        frames.append(row)
        packets.extend(packet_rows)
    return frames, packets


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    return run(tmp_path_factory.mktemp("obs"))


def by_vehicle(frames: Sequence[dict]) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    for row in frames:
        out[int(row["vehicle_id"])] = row
    return out


ITEM_DERIVED_PREFIXES = ("b2_", "b3_", "b4_", "b6_")


def item_derived_columns() -> List[str]:
    names = [c.name for block in ("B2", "B3", "B4", "B6")
             for c in BLOCK_COLUMNS[block]]
    return [n for n in names if n.startswith(ITEM_DERIVED_PREFIXES)]


def item_projection(row: dict) -> dict:
    return {name: row[name] for name in item_derived_columns()}


# ===========================================================================
# LAYER 1 -- the conflation. These PASS today and MUST fail after the repair.
# ===========================================================================

def test_conflation_positive_control_items_do_move_the_columns(emitted):
    """Sanity: when items ARE published the columns move. Without this, every
    'identical' assertion below would be vacuous."""
    frames, _ = emitted
    rows = by_vehicle(frames)
    clean, defects = rows[V_CLEAN], rows[V_DEFECTS]
    assert clean["b2_n_items_total"] == 0
    assert defects["b2_n_items_total"] == 3
    assert defects["b2_brakes_n_days"] == 1
    assert defects["b3_n_fail_items_final"] == 3
    assert item_projection(clean) != item_projection(defects)


def test_conflation_dark_day_is_indistinguishable_from_a_clean_pass(emitted):
    """THE DEFECT (day grain).

    V_CLEAN's prior was a genuinely clean PASS on a covered day.
    V_DAYDARK's prior carried 3 brake failures on a day whose items were never
    published (the measured 2024-12-31 shape).

    Every item-derived column is identical. The frame asserts 'no brake defect
    ever' for a vehicle that had three.
    """
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert item_projection(rows[V_CLEAN]) == item_projection(rows[V_DAYDARK])
    assert rows[V_DAYDARK]["b2_n_items_total"] == 0
    assert rows[V_DAYDARK]["b2_brakes_n_days"] == 0
    assert rows[V_DAYDARK]["b3_n_fail_items_final"] == 0
    assert rows[V_DAYDARK]["b4_burden_mean_last3"] == 0.0


def test_conflation_dark_partition_is_indistinguishable_from_a_clean_pass(emitted):
    """THE DEFECT (partition grain) -- the pair the Gate-4 brief asked for.

    V_CLEAN's prior sits in an items-PUBLISHED partition (2019).
    V_YEARDARK's prior sits in an items-ABSENT partition (2020: results present,
    zero item rows) -- the measured 2024/2025 non-definitive-outcome shape.
    Identical physical defect history is NOT the claim here; identical EMITTED
    item columns is, and that is what makes the two states unrepresentable.
    """
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert item_projection(rows[V_CLEAN]) == item_projection(rows[V_YEARDARK])
    assert rows[V_YEARDARK]["b2_n_items_total"] == 0
    assert rows[V_YEARDARK]["b3_n_advisory_items"] == 0
    # and nothing anywhere in the emitted row records that the partition was dark
    assert not [k for k in rows[V_YEARDARK]
                if "avail" in k or "observab" in k.lower() and k.startswith("b2")]


def test_conflation_fail_bearing_with_zero_items_emits_a_clean_history(emitted):
    """THE DECIDABLE CASE, currently undetected.

    A FAIL cannot have zero reasons-for-rejection. V_FAILDARK's prior is a FAIL
    with no item rows -- definitionally impossible, and the single strongest
    ITEMS_EXPECTED_MISSING detector available. Today it emits a clean history,
    identical to a genuinely clean PASS.
    """
    frames, _ = emitted
    rows = by_vehicle(frames)
    faildark = rows[V_FAILDARK]
    assert faildark["b1_n_prior_final_fails"] == 1      # results side knows it failed
    assert faildark["b2_n_items_total"] == 0            # item side says 'nothing wrong'
    assert faildark["b3_n_fail_items_final"] == 0
    assert faildark["b3_n_fail_items_initial"] == 0
    assert item_projection(faildark) == item_projection(rows[V_CLEAN])


def test_conflation_non_definitive_outcome_looks_like_zero_defects(emitted):
    """The measured 2024/2025 publisher class: ABANDONED/ABORTED/ABORTED_VE
    carry ZERO item rows by publication design (511,270 tests, 0 with items).
    Today that is emitted as 'this prior test found no defects'."""
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert item_projection(rows[V_ABORTED]) == item_projection(rows[V_CLEAN])
    assert rows[V_ABORTED]["b1_n_prior_nonresult_days"] == 1   # results side knows
    assert rows[V_ABORTED]["b2_n_items_total"] == 0            # item side does not


def test_conflation_is_undecidable_at_row_grain_inside_a_covered_cell(emitted):
    """THE IRREDUCIBLE RESIDUAL -- and a design boundary, not a bug to fix.

    V_PAIR_A and V_PAIR_B are byte-identical on the results side: same day,
    same outcome, same mileage, same first_use. A had no defects; B had three
    that were dropped. The emitted rows are identical in EVERY column.

    No join-derived rule can separate them. This is why the observability index
    must be built from source/partition/cell coverage facts, and why the design
    reports a bounded residual instead of claiming row-grain certainty.

    Identity is asserted over every emitted column except the four that are
    pure functions of the identifiers (tgt_id, vehicle_id) or of the salted
    vehicle hash (sample_u, sample_bucket). Those carry no defect information;
    159 of the 163 emitted columns are identical, including all 137 B1-B6
    features.
    """
    frames, _ = emitted
    rows = by_vehicle(frames)
    ignore = ("tgt_id", "vehicle_id", "sample_u", "sample_bucket")
    assert len(rows[V_PAIR_A]) == 163
    a = {k: v for k, v in rows[V_PAIR_A].items() if k not in ignore}
    b = {k: v for k, v in rows[V_PAIR_B].items() if k not in ignore}
    assert a == b
    assert len(a) == 159
    feature_names = [c.name for blk in ("B1", "B2", "B3", "B4", "B5", "B6")
                     for c in BLOCK_COLUMNS[blk]]
    assert len(feature_names) == 137
    assert all(a[n] == b[n] for n in feature_names)


def test_conflation_packets_are_internally_contradictory(emitted):
    """The packets view (which feeds the 104 serving features) says BOTH things
    at once for an item-dark prior: `p_n_items = 0` (atoms.py:174, a hard
    coalesce) and `defects_json = NULL` (packets.py:83-84, correctly NULL-
    preserving). A consumer that reads the count sees a clean test; one that
    reads the payload sees an unknown. Provable without any ground truth."""
    frames, packets = emitted
    dark = [p for p in packets if p["p_test_id"] == 90_201]
    assert len(dark) == 1
    assert dark[0]["p_n_items"] == 0            # asserts 'no defects'
    assert dark[0]["defects_json"] is None      # asserts 'payload unknown'


# ===========================================================================
# LAYER 2 -- the repaired semantics. xfail(strict=True): red the day it lands.
# ===========================================================================
_REPAIR = "repair not implemented (Gate 4 design stage)"


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_dark_day_is_not_a_clean_pass(emitted):
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert item_projection(rows[V_CLEAN]) != item_projection(rows[V_DAYDARK])
    assert rows[V_DAYDARK]["b2_n_items_total"] is None
    assert rows[V_DAYDARK]["b2_item_observability_status"] == "expected_missing"
    assert rows[V_DAYDARK]["b2_n_prior_days_items_expected_missing"] == 1


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_dark_partition_is_not_a_clean_pass(emitted):
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert item_projection(rows[V_CLEAN]) != item_projection(rows[V_YEARDARK])
    assert rows[V_YEARDARK]["b2_item_observability_status"] == "unavailable"
    assert rows[V_YEARDARK]["b2_n_prior_days_items_unavailable"] == 1
    assert rows[V_YEARDARK]["b2_n_items_total"] is None


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_fail_bearing_with_zero_items_is_flagged(emitted):
    frames, _ = emitted
    rows = by_vehicle(frames)
    faildark = rows[V_FAILDARK]
    assert faildark["b2_n_prior_days_items_expected_missing"] == 1
    assert faildark["b3_n_fail_items_final"] is None
    assert faildark["b2_item_observability_status"] == "expected_missing"


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_non_definitive_outcome_is_unavailable_not_zero(emitted):
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert rows[V_ABORTED]["b2_item_observability_status"] == "unavailable"
    assert rows[V_ABORTED]["b2_n_items_total"] is None


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_clean_pass_keeps_its_honest_zero(emitted):
    """The repair must NOT null-out the genuine population: ~16.4M PASS tests a
    year (49.9-62.1% of passes, measured) really do have zero items. Turning
    those into NULLs would destroy the signal rather than repair it."""
    frames, _ = emitted
    rows = by_vehicle(frames)
    clean = rows[V_CLEAN]
    assert clean["b2_n_items_total"] == 0
    assert clean["b2_item_observability_status"] == "present_zero_defects"
    assert clean["b2_n_prior_days_items_present"] == 1


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_packets_stop_contradicting_themselves(emitted):
    frames, packets = emitted
    dark = [p for p in packets if p["p_test_id"] == 90_201][0]
    assert dark["p_n_items"] is None
    assert dark["defects_json"] is None
    assert dark["p_items_observability"] == "expected_missing"


@pytest.mark.xfail(strict=True, reason=_REPAIR)
def test_repaired_denominators_exclude_dark_days(emitted):
    """b2_*_persistence and b3_n_days_fine_severity_observable are denominators.
    A dark day belongs in neither numerator nor denominator; it belongs in its
    own count."""
    frames, _ = emitted
    rows = by_vehicle(frames)
    assert rows[V_DAYDARK]["b2_brakes_persistence"] is None
    assert rows[V_DAYDARK]["b3_n_days_fine_severity_observable"] == 0
    assert rows[V_DAYDARK]["b1_n_prior_test_days"] == 1     # results depth unchanged
