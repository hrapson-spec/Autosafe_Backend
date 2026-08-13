"""Unit tests: registry/cap/dictionary, taxonomy guards, gates, packets, CLI.

Fixtures only.
"""
import json
import os
from datetime import date, datetime

import duckdb
import pytest

from conftest import make_config, run_factory
from factory import (atoms, blocks, build, emit, gates, packets, sampling,
                     serve_view, severity, sources, taxonomy)
from factory import state as fstate
from factory.fixtures import FixtureLake, ItemRow, TestRow, write_p4_certification

RECIPE = emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))
DICTIONARY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "FEATURE_DICTIONARY.md")


# --- column registry --------------------------------------------------------

def test_new_column_count_is_within_the_contract_cap():
    assert blocks.n_new_columns() <= blocks.NEW_COLUMN_CAP


def test_column_names_are_unique_and_block_prefixed():
    names = [c.name for c in blocks.ALL_COLUMNS]
    assert len(names) == len(set(names))
    for spec in blocks.ALL_COLUMNS:
        if spec.block == "meta":
            continue
        assert spec.name.startswith(spec.block.lower() + "_"), spec.name
        assert spec.definition.strip().endswith("."), f"{spec.name}: definition"


def test_every_column_has_a_dictionary_line():
    assert os.path.exists(DICTIONARY), "FEATURE_DICTIONARY.md must be shipped"
    text = open(DICTIONARY, encoding="utf-8").read()
    missing = [c.name for c in blocks.ALL_COLUMNS if f"`{c.name}`" not in text]
    assert not missing, f"columns absent from FEATURE_DICTIONARY.md: {missing}"


def test_emitted_rows_carry_exactly_the_registered_columns(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    assert set(rows[0]) == set(blocks.COLUMN_NAMES)


# --- taxonomy ---------------------------------------------------------------

def test_catalogue_miss_is_not_folded_into_other(tmp_path):
    from factory.fixtures.generate import UNCATALOGUED_RFR

    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_item(ItemRow(test_id=1, rfr_id=UNCATALOGUED_RFR, rfr_type_code="F"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20008", rfr_type_code="A"))  # out-of-scope section
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))

    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 2][0]
    assert row["b2_n_catalogue_miss_items"] == 1
    assert row["b2_other_n_days"] == 1
    assert row["b2_n_items_total"] == 2
    assert row["b2_breadth_categories"] == 1


def test_fanout_guard_raises(tmp_path):
    detail = tmp_path / "item_detail.csv"
    group = tmp_path / "item_group.csv"
    detail.write_text("rfr_id|test_class_id|test_item_set_section_id\n1|4|10\n")
    group.write_text("test_item_id|test_class_id|item_name\n10|4|Brakes\n10|4|Tyres\n")
    con = duckdb.connect()
    with pytest.raises(taxonomy.TaxonomyFanOut):
        taxonomy.assert_no_fanout(con, str(detail), str(group))


def test_missing_lookup_refuses(tmp_path):
    inputs = sources.Inputs(results="x", items="y",
                            item_detail_csv=str(tmp_path / "nope.csv"),
                            item_group_csv=str(tmp_path / "nope2.csv"))
    with pytest.raises(sources.MissingInput):
        inputs.assert_lookup_present()


def test_position_group_mapping():
    """Vocabularies verified against the real 130-row mdr_rfr_location lookup."""
    for value, expected in [("Nearside", "nearside"), ("Nearside Inner", "nearside"),
                            ("Nearside Outer", "nearside"), ("Offside", "offside"),
                            ("Offside Inner", "offside"), ("Centre", "centre"),
                            ("Central", "centre"), ("Inner", "inner_outer"),
                            ("Outer", "inner_outer"), ("", "unknown"),
                            (None, "unknown"), ("something else", "unknown")]:
        assert taxonomy.lateral_group(value) == expected, value
    for value, expected in [("Upper", "upper"), ("Lower", "lower"),
                            ("Inner", "inner_outer"), ("Outer", "inner_outer"),
                            ("", "unknown"), (None, "unknown")]:
        assert taxonomy.vertical_group(value) == expected, value
    for value, expected in [("Front", "front"), ("Rear", "rear"),
                            ("", "unknown"), (None, "unknown")]:
        assert taxonomy.longitudinal_group(value) == expected, value


def test_location_loader_reads_the_real_layout(tmp_path):
    path = tmp_path / "mdr_rfr_location.csv"
    path.write_text("id|lateral|longitudinal|vertical\n1|||\n"
                    "31|Nearside|Front|Lower\n32|Offside|Rear|Upper\n")
    groups = taxonomy.load_location_groups(str(path))
    assert groups == {"1": ("unknown", "unknown", "unknown"),
                      "31": ("nearside", "front", "lower"),
                      "32": ("offside", "rear", "upper")}
    assert taxonomy.load_location_groups(None) is None

    bad = tmp_path / "bad.csv"
    bad.write_text("id|name\n1|Nearside Front\n")
    with pytest.raises(ValueError, match="lateral"):
        taxonomy.load_location_groups(str(bad))


def test_b6_is_null_without_a_location_map(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F", location_id="31"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 2][0]
    assert row["b6_location_map_status"] == "absent"
    assert row["b6_lat_nearside_n"] is None, "absent map must be NULL, never zero"
    assert row["b6_long_front_n"] is None
    assert row["b6_vert_lower_n"] is None
    assert row["b6_pos_n_total"] is None


def test_b6_counts_lateral_and_vertical_with_a_location_map(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    # 31 = Nearside/Front/Lower · 32 = Offside/Rear/Upper · 1 = all blank
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="F", location_id="31"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20002", rfr_type_code="A", location_id="32"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20003", rfr_type_code="A", location_id="1"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))
    _, rows, packet_rows = run_factory(lake, tmp_path / "run", [RECIPE],
                                       write_kwargs={"with_location_csv": True})
    row = [r for r in rows if r["tgt_id"] == 2][0]
    assert row["b6_location_map_status"] == "present"
    assert row["b6_lat_nearside_n"] == 1
    assert row["b6_lat_offside_n"] == 1
    assert row["b6_lat_unknown_n"] == 1
    assert row["b6_vert_lower_n"] == 1
    assert row["b6_vert_upper_n"] == 1
    assert row["b6_vert_unknown_n"] == 1
    assert row["b6_long_front_n"] == 1
    assert row["b6_long_rear_n"] == 1
    assert row["b6_long_unknown_n"] == 1
    assert row["b6_pos_n_total"] == 3, "denominator = items with a RESOLVED location"
    # all three axes partition the same item set
    assert sum(row[f"b6_lat_{g}_n"] for g in taxonomy.LATERAL_GROUPS) == 3
    assert sum(row[f"b6_long_{g}_n"] for g in taxonomy.LONGITUDINAL_GROUPS) == 3
    assert sum(row[f"b6_vert_{g}_n"] for g in taxonomy.VERTICAL_GROUPS) == 3

    payload = json.loads([p for p in packet_rows if p["tgt_id"] == 2][0]["defects_json"])
    assert {d["pos"] for d in payload} == {"nearside/front/lower", "offside/rear/upper",
                                           "unknown/unknown/unknown"}


# --- severity: the stored derived columns are never read --------------------

def test_factory_ignores_stored_derived_columns(tmp_path):
    """The fixture stores the F-22 DEFECT in is_fail_item/rfr_class ('M' = major).

    The factory must classify 'M' as MINOR (non-fail) regardless.
    """
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="M"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20002", rfr_type_code="F"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20003", rfr_type_code="P"))
    lake.add_item(ItemRow(test_id=1, rfr_id="20004", rfr_type_code="F",
                          dangerous_mark="D"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))

    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 2][0]
    assert row["b3_n_minor_items"] == 1
    assert row["b3_n_dangerous_items"] == 1
    assert row["b3_n_major_items"] == 2, "F (not dangerous) + P are major"
    assert row["b3_n_fail_items_final"] == 2, "F only"
    assert row["b3_n_fail_items_initial"] == 3, "F + P"
    assert row["b3_n_prs_items"] == 1
    assert row["b3_severity_observability_status"] == "full"


def test_pre_2018_severity_is_ungraded_not_zero(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2015, 1, 5),
                          outcome="FAIL", first_use_date=date(2010, 1, 1)))
    lake.add_item(ItemRow(test_id=1, rfr_id="5001", rfr_type_code="F"))
    lake.add_item(ItemRow(test_id=1, rfr_id="5002", rfr_type_code="a"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2016, 1, 5),
                          first_use_date=date(2010, 1, 1)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 2][0]
    assert row["b3_severity_observability_status"] == "none"
    assert row["b3_n_dangerous_items"] is None, "pre-2018 severity must be NULL"
    assert row["b3_n_major_items"] is None
    assert row["b3_n_minor_items"] is None
    assert row["b3_n_days_fine_severity_observable"] == 0
    # dispositions ARE observable pre-2018 (case-insensitive)
    assert row["b3_n_fail_items_final"] == 1
    assert row["b3_n_advisory_items"] == 1


def test_mixed_era_history_is_partially_observable(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2017, 1, 5)))
    lake.add_item(ItemRow(test_id=1, rfr_id="5001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_item(ItemRow(test_id=2, rfr_id="20001", rfr_type_code="M"))
    lake.add_test(TestRow(test_id=3, vehicle_id=1, test_date=date(2020, 1, 5)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 3][0]
    assert row["b3_severity_observability_status"] == "partial"
    assert row["b3_n_days_fine_severity_observable"] == 1
    assert row["b3_n_minor_items"] == 1


# --- state / blocks semantics ----------------------------------------------

def test_trajectory_advisory_to_fail_and_recurrence(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    # 2016: brakes ADVISORY -> 2017: brakes FAIL (transition)
    lake.add_test(TestRow(test_id=1, vehicle_id=9, test_date=date(2016, 1, 5)))
    lake.add_item(ItemRow(test_id=1, rfr_id="5001", rfr_type_code="A"))
    lake.add_test(TestRow(test_id=2, vehicle_id=9, test_date=date(2017, 1, 5),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=2, rfr_id="5001", rfr_type_code="F"))
    # 2018: clean PASS (repair) -> 2019: brakes FAIL again (recurrence)
    lake.add_test(TestRow(test_id=3, vehicle_id=9, test_date=date(2018, 1, 5)))
    lake.add_test(TestRow(test_id=4, vehicle_id=9, test_date=date(2019, 1, 5),
                          outcome="FAIL"))
    lake.add_item(ItemRow(test_id=4, rfr_id="20001", rfr_type_code="F"))
    lake.add_test(TestRow(test_id=5, vehicle_id=9, test_date=date(2020, 1, 5)))

    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    by_id = {r["tgt_id"]: r for r in rows}
    assert by_id[3]["b4_n_adv_to_fail_transitions"] == 1
    assert by_id[3]["b4_adv_to_fail_categories"] == 1
    assert by_id[3]["b4_n_recurrence_after_repair"] == 0
    assert by_id[5]["b4_n_recurrence_after_repair"] == 1
    assert by_id[5]["b4_recurrence_categories"] == 1
    assert by_id[5]["b2_brakes_n_days"] == 3
    assert by_id[5]["b2_brakes_max_run"] == 2
    assert by_id[5]["b2_brakes_days_since"] == (date(2020, 1, 5) - date(2019, 1, 5)).days


def test_burden_and_mileage_band(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    for idx, (year, n_items, miles) in enumerate(
            [(2017, 1, 20_000), (2018, 3, 45_000), (2019, 6, 70_000)], start=1):
        lake.add_test(TestRow(test_id=idx, vehicle_id=4, test_date=date(year, 2, 1),
                              test_mileage=miles))
        for k in range(n_items):
            lake.add_item(ItemRow(test_id=idx, rfr_id="20001" if year >= 2019 else "5001",
                                  rfr_type_code="A"))
    lake.add_test(TestRow(test_id=9, vehicle_id=4, test_date=date(2020, 2, 1),
                          test_mileage=90_000))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 9][0]
    assert row["b4_burden_delta_1"] == 3
    assert row["b4_burden_delta_2"] == 2
    assert row["b4_burden_mean_last3"] == pytest.approx((1 + 3 + 6) / 3)
    assert row["b4_mileage_band"] == "60k-100k", "band uses the LAST TRUSTED prior reading"
    assert row["b4_deterioration_slope"] is not None
    assert row["b4_deterioration_slope_n_days"] == 3


def test_ambiguous_day_is_counted_not_invented(tmp_path):
    """Same-stratum FAIL + definitive PASS: AMBIGUOUS, and it does not become a pass."""
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=6, test_date=date(2019, 3, 1),
                          test_type="NT", outcome="FAIL"))
    lake.add_test(TestRow(test_id=2, vehicle_id=6, test_date=date(2019, 3, 1),
                          test_type="NT", outcome="PASS"))
    lake.add_test(TestRow(test_id=3, vehicle_id=6, test_date=date(2020, 3, 1)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 3][0]
    assert row["b5_n_prior_ambiguous_days"] == 1, "strict: same-stratum FAIL + definitive pass"
    assert row["b5_n_prior_nondefinitive_days"] == 1, "cycles-faithful superset"
    assert row["b5_n_prior_days_pass_and_fail"] == 1
    assert row["b1_n_prior_test_days"] == 1
    assert row["b1_n_prior_tests"] == 2
    assert row["b1_n_prior_final_fails"] == 1
    assert row["b1_n_prior_initial_fails"] == 1


def test_covid_straddle_and_gap_band(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=8, test_date=date(2020, 2, 1)))
    lake.add_test(TestRow(test_id=2, vehicle_id=8, test_date=date(2021, 2, 1)))
    lake.add_test(TestRow(test_id=3, vehicle_id=8, test_date=date(2022, 2, 1)))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    by_id = {r["tgt_id"]: r for r in rows}
    assert by_id[2]["b5_covid_straddle_flag"] is True
    assert by_id[3]["b5_covid_straddle_flag"] is False
    assert by_id[2]["b5_gap_annual_band_flag"] is True
    assert by_id[1]["b5_days_since_prior_day"] is None
    assert by_id[1]["b5_gap_annual_band_flag"] is None


def test_mileage_conflict_flag(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=2, test_date=date(2019, 1, 1),
                          test_mileage=50_000))
    lake.add_test(TestRow(test_id=2, vehicle_id=2, test_date=date(2019, 1, 1),
                          test_type="RT", test_mileage=90_000))
    lake.add_test(TestRow(test_id=3, vehicle_id=2, test_date=date(2020, 1, 1),
                          test_mileage=95_000))
    _, rows, _ = run_factory(lake, tmp_path / "run", [RECIPE])
    row = [r for r in rows if r["tgt_id"] == 3][0]
    assert row["b5_n_prior_mileage_conflict_days"] == 1
    assert row["b4_mileage_band"] == "60k-100k", "max valid reading on the day"


# --- packets ----------------------------------------------------------------

def test_packet_shape_and_defect_payload(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=5, test_date=date(2019, 1, 5),
                          outcome="PRS", test_mileage=11_000))
    lake.add_item(ItemRow(test_id=1, rfr_id="20001", rfr_type_code="P",
                          location_id="1"))
    lake.add_test(TestRow(test_id=2, vehicle_id=5, test_date=date(2020, 1, 5)))
    _, rows, packet_rows = run_factory(lake, tmp_path / "run", [RECIPE])

    assert set(packet_rows[0]) == set(packets.PACKET_COLUMNS)
    first = [p for p in packet_rows if p["tgt_id"] == 1][0]
    assert first["p_test_id"] is None and first["n_priors"] == 0

    second = [p for p in packet_rows if p["tgt_id"] == 2]
    assert len(second) == 1
    payload = json.loads(second[0]["defects_json"])
    assert set(payload[0]) == set(packets.DEFECT_KEYS)
    assert "text" not in payload[0] and "x" not in payload[0]
    assert payload[0]["disp"] == "P"
    assert payload[0]["sev"] == severity.SEVERITY_MAJOR
    assert payload[0]["cat"] == "brakes"
    assert payload[0]["comp"] == "brakes"
    assert second[0]["p_result"] == "PASSED", "PRS folds to PASSED (final basis)"
    assert second[0]["p_outcome"] == "PRS"
    assert second[0]["p_miles"] == 11_000


def test_packet_result_map_is_total():
    for outcome in ("PASS", "FAIL", "PRS", "ABANDONED", "ABORTED", "ABORTED_VE",
                    "REFUSED"):
        assert packets.map_result(outcome) in ("PASSED", "FAILED", "ABANDONED")
    assert packets.map_result(None) is None


def test_packet_pathology_bound_raises():
    priors = [fstate.PriorTest(test_id=i, test_date=date(2019, 1, 1),
                               test_type="NT", outcome="PASS", mileage=1,
                               n_items=0, defects=None) for i in range(90)]
    with pytest.raises(packets.PacketPathology):
        packets.emit_packet_rows({"tgt_id": 1, "vehicle_id": 1,
                                  "tgt_date": date(2020, 1, 1)}, priors)


def test_packet_max_priors_trims_whole_days():
    priors = ([fstate.PriorTest(test_id=1, test_date=date(2018, 1, 1), test_type="NT",
                                outcome="PASS", mileage=1, n_items=0, defects=None)]
              + [fstate.PriorTest(test_id=i, test_date=date(2019, 1, 1),
                                  test_type="NT", outcome="PASS", mileage=1,
                                  n_items=0, defects=None) for i in (2, 3)])
    rows = packets.emit_packet_rows(
        {"tgt_id": 1, "vehicle_id": 1, "tgt_date": date(2020, 1, 1)},
        priors, max_priors=2)
    assert {r["p_date"] for r in rows} == {date(2019, 1, 1)}
    assert len(rows) == 2, "a same-day group is kept whole or dropped whole"


# --- gates ------------------------------------------------------------------

def test_p4_gate_refuses_when_absent_or_not_pass(tmp_path):
    with pytest.raises(gates.GateFailure, match="P4 certification absent"):
        gates.assert_p4_certified(str(tmp_path / "nothing.json"))
    path = write_p4_certification(str(tmp_path / "cert.json"), verdict="FAIL")
    with pytest.raises(gates.GateFailure, match="not PASS"):
        gates.assert_p4_certified(path)
    ok = write_p4_certification(str(tmp_path / "ok.json"))
    assert gates.assert_p4_certified(ok)["verdict"] == "PASS"


def test_training_fence_refuses_2024_targets():
    with pytest.raises(gates.GateFailure, match="2024"):
        gates.assert_target_fence(date(2023, 1, 1), date(2024, 6, 1),
                                  eval_slice=False, build_confirmation=False)
    gates.assert_target_fence(date(2023, 1, 1), date(2024, 1, 1), False, False)
    gates.assert_target_fence(date(2024, 1, 1), date(2025, 1, 1), True, False)


def test_confirmation_requires_a_prereg_sha(tmp_path):
    with pytest.raises(gates.GateFailure, match="prereg"):
        gates.assert_confirmation_prereg(None, None)
    prereg = tmp_path / "prereg.md"
    prereg.write_text("sealed 2025-H2 analysis plan")
    import hashlib
    sha = hashlib.sha256(prereg.read_bytes()).hexdigest()
    assert gates.assert_confirmation_prereg(sha, str(prereg)) == sha
    with pytest.raises(gates.GateFailure, match="sha mismatch"):
        gates.assert_confirmation_prereg("0" * 64, str(prereg))


def test_duckdb_pin():
    gates.assert_duckdb_pin()
    with pytest.raises(gates.GateFailure):
        gates.assert_duckdb_pin("1.4.0")


def test_unknown_vocabulary_gate_fires(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5),
                          test_type="ZZ"))
    with pytest.raises(gates.GateFailure, match="DVSA vocabulary"):
        run_factory(lake, tmp_path / "run", [RECIPE])


# --- serve_view -------------------------------------------------------------

def test_serve_view_absent_is_tolerated():
    view = serve_view.load_serve_view(None)
    assert view.available is False
    assert view.class_for_column("b1_n_prior_tests", "B1") == serve_view.UNCLASSIFIED
    assert view.manifest_entry()["available"] is False


def test_serve_view_validates_against_the_shipped_schema(tmp_path):
    instance = {
        "version": "1.0",
        "classes": [{"code": "PC", "name": "production-common"},
                    {"code": "RO", "name": "research-only"}],
        "features": {"b1_n_prior_tests": {"class": "PC", "flags": ["D13"]}},
        "blocks": {"B1": {"class": "PC"}, "B6": {"class": "RO"}},
    }
    path = tmp_path / "serve_view_classes.json"
    path.write_text(json.dumps(instance))
    view = serve_view.load_serve_view(str(path))
    assert view.available and view.class_codes == ["PC", "RO"]
    assert view.class_for_column("b1_n_prior_tests", "B1") == "PC"
    assert view.class_for_column("b1_history_years", "B1") == "PC"     # block fallback
    assert view.class_for_column("b6_pos_n_total", "B6") == "RO"
    assert view.flags_for_column("b1_n_prior_tests", "B1") == ["D13"]


def test_serve_view_rejects_undeclared_class_and_bad_shape(tmp_path):
    bad_class = tmp_path / "a.json"
    bad_class.write_text(json.dumps({
        "version": "1", "classes": [{"code": "PC"}],
        "blocks": {"B1": {"class": "XX"}}}))
    with pytest.raises(serve_view.SchemaViolation, match="not declared"):
        serve_view.load_serve_view(str(bad_class))

    bad_shape = tmp_path / "b.json"
    bad_shape.write_text(json.dumps({"version": "1", "features": "not-an-object"}))
    with pytest.raises(serve_view.SchemaViolation, match="expected type"):
        serve_view.load_serve_view(str(bad_shape))

    classifies_nothing = tmp_path / "b2.json"
    classifies_nothing.write_text(json.dumps({"classes": [{"code": "PC"}]}))
    with pytest.raises(serve_view.SchemaViolation, match="classifies nothing"):
        serve_view.load_serve_view(str(classifies_nothing))

    bad_entry = tmp_path / "c.json"
    bad_entry.write_text(json.dumps({
        "version": "1", "classes": [{"code": "PC"}],
        "features": {"x": {"flags": []}}}))
    with pytest.raises(serve_view.SchemaViolation, match="class"):
        serve_view.load_serve_view(str(bad_entry))


def test_serve_view_derives_the_vocabulary_when_classes_are_not_declared(tmp_path):
    """A2's real file has no `version` and no `classes` array -- it must load."""
    path = tmp_path / "serve_view_classes.json"
    path.write_text(json.dumps({
        "features": {"gap_band": {"class": "production-common",
                                  "inputs": ["motTests[0].completedDate"],
                                  "evidence": "FE58:238-243"}},
        "blocks": {"B6": {"class": "research-only",
                          "paths": {"lake_only": "location_id is not on the API"}}},
        "serve_view_columns": ["registration", "make"],
    }))
    view = serve_view.load_serve_view(str(path))
    assert view.available and view.classes_declared is False
    assert view.class_codes == ["production-common", "research-only"]
    assert view.class_for_column("b6_pos_n_total", "B6") == "research-only"
    assert view.version is None


def test_serve_view_classes_file_if_present_validates():
    """If A2 has landed out/serve_view_classes.json, it must match the schema."""
    candidate = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "out", "serve_view_classes.json")
    if not os.path.exists(candidate):
        pytest.skip("serve_view_classes.json not produced yet")
    view = serve_view.load_serve_view(candidate)
    assert view.available and view.class_codes
    # every emitted B1-B6 column must resolve to a class (feature or block level)
    unresolved = [c.name for c in blocks.ALL_COLUMNS if c.block != "meta"
                  and view.class_for_column(c.name, c.block) == serve_view.UNCLASSIFIED]
    assert not unresolved, f"blocks missing from serve_view_classes.json: {unresolved[:5]}"


# --- sampling ---------------------------------------------------------------

def test_inclusion_weight_is_horvitz_thompson():
    """weight = base / P(selected), a function of the DESIGN CELL only.

    The cells that AGREE with the pre-fix bug are the last two; the first two
    are the cells the shipped test used to miss (adversarial review B-1).
    """
    rung = sampling.Rung("r", base=0.10, enriched=0.20)
    # eligible AND u < base -- the cell that used to return 1.0
    assert rung.inclusion_weight(0.05, "deep_history") == pytest.approx(0.5)
    assert rung.inclusion_weight(0.05, "dangerous_prior") == pytest.approx(0.5)
    # cells that always agreed
    assert rung.inclusion_weight(0.05, "none") == 1.0
    assert rung.inclusion_weight(0.15, "deep_history") == pytest.approx(0.5)
    # selection itself is unchanged
    assert rung.selects(0.15, "none") is False
    assert rung.selects(0.15, "deep_history") is True
    assert rung.selects(0.05, "none") is True


def test_enrichment_share_cap_is_reported():
    ok, share = sampling.check_enrichment_share(20, 100)
    assert ok and share == pytest.approx(0.20)
    ok, share = sampling.check_enrichment_share(40, 100)
    assert not ok and share == pytest.approx(0.40)


def test_buckets_are_independent_of_sample_membership():
    con = duckdb.connect()
    vehicles = list(range(1, 5001))
    us = sampling.unit_hash(con, vehicles, sampling.SAMPLE_SALT)
    buckets = [int(r[0]) for r in con.execute(
        f"SELECT {sampling.bucket_sql('v', 8)} FROM "
        f"(VALUES {', '.join(f'({v})' for v in vehicles)}) t(v)").fetchall()]
    in_sample = [u < 0.25 for u in us]
    by_bucket = {}
    for bucket, selected in zip(buckets, in_sample):
        stats = by_bucket.setdefault(bucket, [0, 0])
        stats[0] += 1
        stats[1] += int(selected)
    shares = [hits / total for total, hits in by_bucket.values()]
    assert max(shares) - min(shares) < 0.10, "bucket correlates with rung membership"


# --- end-to-end build + manifest -------------------------------------------

def test_full_build_writes_frames_packets_and_manifest(tmp_path):
    from factory.fixtures import default_population

    lake = default_population(str(tmp_path / "lake"), n_vehicles=50, seed=5,
                              start_year=2016, n_years=5)
    inputs = lake.write()
    config = make_config(tmp_path)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = sorted({t.test_date.year for t in lake.tests})
    recipes = [emit.WindowRecipe("train", date(2017, 1, 1), date(2024, 1, 1))]
    preflight = factory.preflight(years, recipes)
    prepare = factory.prepare(years, recipes)
    ladder = sampling.ladder_from_fractions({"small": (0.30, 0.35), "big": (1.0, 1.0)})
    manifest = factory.build(years, recipes, ladder, preflight, prepare)

    rungs = manifest["results"]["recipes"]["train"]["rungs"]
    assert rungs["small"]["rows"] < rungs["big"]["rows"]
    assert rungs["big"]["packet_rows"] > rungs["big"]["rows"]
    assert manifest["contract_version"].startswith("factory-contract-v")
    assert manifest["salts"]["sample"] == "mp2026s1"
    assert manifest["columns"]["n_new_b1_b6"] == blocks.n_new_columns()
    assert manifest["packets"]["no_free_text"] is True
    assert manifest["confirmation_slice_definition"]["status"].startswith("DEFINITION")
    assert os.path.exists(os.path.join(config.output_dir, "BUILD_MANIFEST.json"))

    con = duckdb.connect()
    frame_glob = os.path.join(config.output_dir, "recipe=train", "rung=big",
                              "frame", "*.parquet")
    n_rows, n_cols = con.execute(
        f"SELECT count(*), (SELECT count(*) FROM (DESCRIBE SELECT * FROM "
        f"read_parquet('{frame_glob}'))) FROM read_parquet('{frame_glob}')").fetchone()
    assert n_rows == rungs["big"]["rows"]
    assert n_cols == len(blocks.COLUMN_NAMES)
    leak = con.execute(
        f"SELECT count(*) FROM read_parquet('{frame_glob}') "
        f"WHERE b1_last_prior_date >= tgt_date").fetchone()[0]
    assert leak == 0

    packets_glob = os.path.join(config.output_dir, "recipe=train", "rung=big",
                                "packets", "*.parquet")
    p_leak = con.execute(
        f"SELECT count(*) FROM read_parquet('{packets_glob}') "
        f"WHERE p_date >= tgt_date").fetchone()[0]
    assert p_leak == 0


def test_build_writes_design_cell_weights_not_realised_u_weights(tmp_path):
    """m-9 + B-1: assert build()'s ACTUAL writer path, not just scan().

    Every emitted row's inclusion_weight must equal base/enriched for a
    stratum-eligible row and 1.0 otherwise -- independent of its sample_u --
    and the weighted stratum total must recover base * N_stratum.
    """
    from factory.fixtures import default_population

    lake = default_population(str(tmp_path / "lake"), n_vehicles=200, seed=13,
                              start_year=2016, n_years=6)
    inputs = lake.write()
    config = make_config(tmp_path)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = sorted({t.test_date.year for t in lake.tests})
    recipes = [emit.WindowRecipe("train", date(2017, 1, 1), date(2024, 1, 1))]
    preflight = factory.preflight(years, recipes)
    prepare = factory.prepare(years, recipes)
    ladder = sampling.ladder_from_fractions({"enr": (0.30, 0.90)})
    factory.build(years, recipes, ladder, preflight, prepare)

    con = duckdb.connect()
    frame = ("read_parquet('" + os.path.join(config.output_dir, "recipe=train",
                                             "rung=enr", "frame", "*.parquet") + "')")
    rows = con.execute(
        f"SELECT enrichment_stratum, sample_u, inclusion_weight FROM {frame}"
    ).fetchall()
    assert rows, "the writer path emitted nothing"
    expected_enriched = 0.30 / 0.90
    n_eligible_selected = 0
    for stratum, u, weight in rows:
        if stratum == sampling.NO_STRATUM:
            assert weight == 1.0
            assert u < 0.30, "a non-stratum row above base was selected"
        else:
            n_eligible_selected += 1
            assert weight == pytest.approx(expected_enriched), (
                f"stratum row at u={u:.4f} carries weight {weight} -- the weight "
                f"branched on the realised u, not the design cell")
    assert n_eligible_selected > 0, "no enriched rows: the assertion would be vacuous"

    # unbiasedness: weighted stratum total recovers base * N_stratum
    total_eligible = sum(1 for row, _pk in factory.scan(recipes[0])
                         if row["enrichment_stratum"] != sampling.NO_STRATUM)
    weighted = sum(w for s, _u, w in rows if s != sampling.NO_STRATUM)
    assert weighted == pytest.approx(0.30 * total_eligible, rel=0.25), (
        f"weighted stratum total {weighted:.1f} vs unbiased "
        f"{0.30 * total_eligible:.1f}")


def test_calibrate_reports_thresholds(tmp_path):
    from factory.fixtures import default_population

    lake = default_population(str(tmp_path / "lake"), n_vehicles=120, seed=2,
                              start_year=2018, n_years=4)
    inputs = lake.write()
    factory = emit.Factory(inputs, make_config(tmp_path))
    factory.connect()
    years = sorted({t.test_date.year for t in lake.tests})
    recipe = emit.WindowRecipe("train", date(2019, 1, 1), date(2024, 1, 1))
    factory.preflight(years, [recipe])
    factory.prepare(years, [recipe])
    report = factory.calibrate(recipe, targets={"tiny": 20, "small": 100})
    assert report["events_in_window"] > 0
    assert report["thresholds"]["tiny"]["base"] <= report["thresholds"]["small"]["base"]
    assert report["thresholds"]["tiny"]["realised_rows"] <= \
        report["thresholds"]["small"]["realised_rows"]


def test_completed_ts_is_optional_and_diagnostic_only(tmp_path):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))
    lake.add_completed_ts(1, datetime(2019, 1, 5, 11, 30))
    _, with_ts, _ = run_factory(lake, tmp_path / "a", [RECIPE],
                                write_kwargs={"with_completed_ts": True})
    lake2 = FixtureLake(str(tmp_path / "lake2"))
    lake2.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake2.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))
    _, without_ts, _ = run_factory(lake2, tmp_path / "b", [RECIPE])
    strip = lambda rows: sorted(tuple(sorted((k, str(v)) for k, v in r.items()))
                                for r in rows)
    assert strip(with_ts) == strip(without_ts), \
        "the sidecar changed a feature: it is diagnostics-only by contract"
    assert atoms.completed_ts_diagnostic_sql(
        sources.Inputs(results="a", items="b", item_detail_csv="c",
                       item_group_csv="d")) is None


# --- CLI --------------------------------------------------------------------

def test_dry_run_prints_a_manifest_and_writes_nothing(tmp_path, capsys):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2019, 1, 5)))
    lake.add_test(TestRow(test_id=2, vehicle_id=1, test_date=date(2020, 1, 5)))
    inputs = lake.write()
    cert = write_p4_certification(str(tmp_path / "cert.json"))
    out_dir = tmp_path / "out"
    argv = [
        "--results", inputs.results, "--items", inputs.items,
        "--item-detail-csv", inputs.item_detail_csv,
        "--item-group-csv", inputs.item_group_csv,
        "--years", "2019-2020",
        "--recipe", "train:2019-01-01:2024-01-01",
        "--rung", "r250k:0.5", "--rung", "r500k:0.9:0.95",
        "--staging-dir", str(tmp_path / "stage"), "--output-dir", str(out_dir),
        "--p4-certification", cert, "--n-buckets", "2", "--dry-run",
    ]
    assert build.main(argv) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["dry_run"] is True
    assert manifest["input_years"] == [2019, 2020]
    assert [r["name"] for r in manifest["rungs"]] == ["r250k", "r500k"]
    assert manifest["recipes"][0]["target_end_exclusive"] == "2024-01-01"
    assert not os.path.exists(out_dir)
    assert not os.path.exists(tmp_path / "stage")


def test_cli_refuses_a_2024_training_target(tmp_path, capsys):
    lake = FixtureLake(str(tmp_path / "lake"))
    lake.add_test(TestRow(test_id=1, vehicle_id=1, test_date=date(2024, 1, 5)))
    inputs = lake.write()
    argv = [
        "--results", inputs.results, "--items", inputs.items,
        "--item-detail-csv", inputs.item_detail_csv,
        "--item-group-csv", inputs.item_group_csv,
        "--years", "2024", "--recipe", "bad:2024-01-01:2025-01-01",
        "--staging-dir", str(tmp_path / "s"), "--output-dir", str(tmp_path / "o"),
        "--p4-certification", write_p4_certification(str(tmp_path / "c.json")),
        "--dry-run",
    ]
    assert build.main(argv) == 3
    assert "WOULD REFUSE" in capsys.readouterr().err


def test_parse_helpers():
    assert build.parse_years("2005-2008,2015") == [2005, 2006, 2007, 2008, 2015]
    recipe = build.parse_recipe("w:2015-01-01:2016-01-01")
    assert recipe.start == date(2015, 1, 1) and recipe.end == date(2016, 1, 1)
    assert build.parse_rung("r:0.1:0.2") == ("r", 0.1, 0.2)
    assert build.parse_rung("r:0.1") == ("r", 0.1, 0.1)
