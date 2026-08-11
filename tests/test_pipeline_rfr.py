"""RfR mapping contract tests (v58 lake pipeline).

Successor to the retired tests/test_defects.py: that file tested the
never-committed process_defects.py via importorskip (i.e. it tested
nothing). The recovered contract it documented is now implemented in
pipeline/lake/rfr_mapping.py and pinned HERE for real:
- pipe-delimited lookup tables, joined within test_class_id, class filter;
- "Road Wheels" -> "Wheels" normalization;
- None (not an exception) for missing files; string keys.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.lake.rfr_mapping import (  # noqa: E402
    COMPONENT_CATEGORIES,
    RFR_TYPE_BY_ERA,
    UnknownRfrType,
    classify_rfr_type,
    load_rfr_mapping,
    project_category,
)

DETAIL = """rfr_id|test_class_id|test_item_set_section_id
1001|4|50
1002|1|50
1003|4|51
"""

GROUP = """test_item_id|test_class_id|item_name
50|4|Brakes
50|1|Brakes
51|4|Road Wheels
"""


@pytest.fixture()
def lookup_tables(tmp_path):
    detail = tmp_path / "detail.csv"
    group = tmp_path / "group.csv"
    detail.write_text(DETAIL)
    group.write_text(GROUP)
    return str(detail), str(group)


class TestLoadRfrMapping:
    def test_recovered_contract(self, lookup_tables):
        detail, group = lookup_tables
        mapping = load_rfr_mapping(detail, group, "4")
        assert mapping is not None
        # class-4 join lands on Brakes
        assert mapping["1001"] == "Brakes"
        # class-1 rows are excluded entirely
        assert "1002" not in mapping
        # the one proven normalization: Road Wheels -> Wheels
        assert mapping["1003"] == "Wheels"

    def test_missing_file_returns_none(self, lookup_tables):
        _, group = lookup_tables
        assert load_rfr_mapping("does_not_exist.csv", group, "4") is None

    def test_drifted_lookup_schema_fails_loud(self, tmp_path, lookup_tables):
        _, group = lookup_tables
        bad = tmp_path / "bad_detail.csv"
        bad.write_text("rfr_id|klass|section\n1|4|50\n")
        with pytest.raises(ValueError, match="missing columns"):
            load_rfr_mapping(str(bad), group, "4")


class TestEraClassification:
    def test_pre_2018_codes(self):
        assert classify_rfr_type("F", "pre_2018").is_fail_item is True
        assert classify_rfr_type("P", "pre_2018").is_fail_item is True  # PRS: rectified failure
        adv = classify_rfr_type("A", "pre_2018")
        assert adv.is_fail_item is False and adv.is_advisory is True
        # pre-2018 has no case distinction
        assert classify_rfr_type("f", "pre_2018").label == "fail"

    def test_post_2018_codes(self):
        assert classify_rfr_type("D", "post_2018").is_dangerous is True
        assert classify_rfr_type("M", "post_2018").is_fail_item is True
        # THE trap the era split exists for: minor does not fail the test.
        minor = classify_rfr_type("m", "post_2018")
        assert minor.is_fail_item is False and minor.is_advisory is False
        assert classify_rfr_type("A", "post_2018").is_advisory is True

    def test_unknown_code_raises_never_coerces(self):
        with pytest.raises(UnknownRfrType):
            classify_rfr_type("X", "post_2018")
        with pytest.raises(UnknownRfrType):
            classify_rfr_type("F", "not_an_era")

    def test_tables_are_internally_consistent(self):
        for era, table in RFR_TYPE_BY_ERA.items():
            for code, cls in table.items():
                # advisory and fail are mutually exclusive semantics
                assert not (cls.is_fail_item and cls.is_advisory), (era, code)
                # dangerous implies fail
                assert cls.is_fail_item or not cls.is_dangerous, (era, code)


class TestCategoryProjection:
    def test_seven_canonical_categories_match_artifact_columns(self):
        # Mirrors prod_data_clean.csv.gz's Risk_* columns exactly.
        assert COMPONENT_CATEGORIES == (
            "Brakes", "Suspension", "Tyres", "Steering", "Visibility",
            "Lamps_Reflectors_And_Electrical_Equipment", "Body_Chassis_Structure",
        )

    @pytest.mark.parametrize("name,expected", [
        ("Brakes", "Brakes"),
        ("Road Wheels", "Tyres"),          # 9-cat combined tyres+wheels precedent
        ("Wheels", "Tyres"),
        ("Tyres", "Tyres"),
        ("Steering", "Steering"),
        ("Lamps, Reflectors and Electrical Equipment",
         "Lamps_Reflectors_And_Electrical_Equipment"),
        ("Body, Chassis, Structure", "Body_Chassis_Structure"),
        # 2026-08-11 release names (real item_group.csv top-level sections)
        ("Body, Structure and General Items", "Body_Chassis_Structure"),
        ("Brake Performance", "Brakes"),
        ("Motor Tricycles and Quadricycles", None),
        ("Seat Belts and Supplementary Restraint Systems", None),
        ("Exhaust, Fuel and Emissions", None),
        ("Towbars", None),
        ("Buses and Coaches Supplementary Tests", None),
        ("Seat Belt Installation Check", None),
        ("Non-Component Advisories", None),
        ("Driving Controls and Speed Limiters", None),
        ("Items Not Tested", None),
        ("Seat Belts", None),              # explicit out-of-scope
        ("Never Heard Of It", None),       # unmapped -> None, counted by checks
        (None, None),
        ("", None),
    ])
    def test_projection(self, name, expected):
        assert project_category(name) == expected
