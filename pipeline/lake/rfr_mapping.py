"""RfR (reason-for-rejection) mapping: defect rows -> canonical semantics.

Two independent mappings live here:

1. load_rfr_mapping -- rfr_id -> top-level test-item name, rebuilt from the
   DVSA lookup tables exactly as the never-committed process_defects.py did
   (contract recovered from the retired tests/test_defects.py): two
   pipe-delimited tables joined within a test class, filtered to class "4",
   with the "Road Wheels" -> "Wheels" normalization.

2. RFR type classification -- era-aware semantics of rfr_type_code. The EU
   roadworthiness directive replaced the fail/PRS/advisory item classes with
   dangerous/major/minor/advisory on 20 May 2018, and MINOR DOES NOT FAIL
   THE TEST: counting post-2018 defect rows as failures naively inflates
   component failure rates. Unknown codes raise -- the workstation run
   surfaces them (checks.check_rfr_type_coverage) and this table is extended
   deliberately, never coerced.

3. project_category -- top-level item name -> the 7 canonical Risk_*
   component categories of the comparison artifact. Wheels project into
   Tyres (the training substrate's 9-cat labels combined tyres+wheels, and
   the artifact has no wheels column). Names that belong to no category
   (e.g. exhaust, seatbelts) project to None and are counted, not dropped.
"""
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

# The 7 canonical component categories = the artifact's Risk_* columns.
COMPONENT_CATEGORIES = (
    "Brakes",
    "Suspension",
    "Tyres",
    "Steering",
    "Visibility",
    "Lamps_Reflectors_And_Electrical_Equipment",
    "Body_Chassis_Structure",
)

# Top-level test-item names -> canonical category. Keys are normalized via
# _norm_item_name (lowercase, punctuation stripped). This table is expected
# to be EXTENDED during the workstation run as real top-level names surface;
# unmapped names are reported with volumes by checks.check_category_coverage.
_SECTION_TO_CATEGORY: Dict[str, Optional[str]] = {
    "brakes": "Brakes",
    "suspension": "Suspension",
    "tyres": "Tyres",
    # 9-cat precedent: tyres and wheels were one label; artifact has no wheels column.
    "wheels": "Tyres",
    "road wheels": "Tyres",
    "steering": "Steering",
    "visibility": "Visibility",
    "driver s view of the road": "Visibility",
    "lamps reflectors and electrical equipment": "Lamps_Reflectors_And_Electrical_Equipment",
    "lamps reflectors electrical equipment": "Lamps_Reflectors_And_Electrical_Equipment",
    "body chassis structure": "Body_Chassis_Structure",
    "body structure and attachments": "Body_Chassis_Structure",
    # Known out-of-scope top-level sections (explicit None = deliberate).
    "exhaust emissions": None,
    "fuel and exhaust": None,
    "noise emissions and leaks": None,
    "seat belts": None,
    "seat belt installation checks": None,
    "registration plates and vin": None,
    "identification of the vehicle": None,
    "supplementary restraint systems": None,
    "speedometer and speed limiter": None,
}


@dataclass(frozen=True)
class RfrClass:
    """Era-resolved meaning of one rfr_type_code."""

    label: str            # canonical label, e.g. "major"
    is_fail_item: bool    # does this item class fail the test?
    is_advisory: bool
    is_dangerous: bool = False


class UnknownRfrType(KeyError):
    """An rfr_type_code with no registered era semantics."""


# Era-aware item-class tables. PRS items are recorded failures that were
# rectified at the station: fail-signal for defect features (decision D7).
RFR_TYPE_BY_ERA: Dict[str, Dict[str, RfrClass]] = {
    "pre_2018": {
        "F": RfrClass("fail", True, False),
        "P": RfrClass("prs", True, False),
        "A": RfrClass("advisory", False, True),
    },
    "post_2018": {
        "D": RfrClass("dangerous", True, False, is_dangerous=True),
        "M": RfrClass("major", True, False),
        # Minor defects DO NOT fail the test post-2018.
        "m": RfrClass("minor", False, False),
        "A": RfrClass("advisory", False, True),
        "P": RfrClass("prs", True, False),
        "F": RfrClass("fail", True, False),
    },
}


def classify_rfr_type(code: str, era: str) -> RfrClass:
    """Resolve an rfr_type_code within its taxonomy era. Case-sensitive for
    post-2018 ('M' major vs 'm' minor); raises UnknownRfrType on anything
    unregistered -- extend the table deliberately, never coerce."""
    table = RFR_TYPE_BY_ERA.get(era)
    if table is None:
        raise UnknownRfrType(f"unknown taxonomy era {era!r}")
    stripped = (code or "").strip()
    if stripped in table:
        return table[stripped]
    # Pre-2018 files are case-insensitive (no m/M distinction exists there).
    if era == "pre_2018" and stripped.upper() in table:
        return table[stripped.upper()]
    raise UnknownRfrType(f"unregistered rfr_type_code {code!r} for era {era!r}")


def _norm_item_name(name: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c.isspace()) else " " for c in name.lower())
    return " ".join(cleaned.split())


def project_category(item_name: Optional[str]) -> Optional[str]:
    """Top-level item name -> canonical 7-category, or None when the item is
    outside the comparison categories or unrecognized (checks report the
    unrecognized share so the table gets extended, not guessed)."""
    if not item_name:
        return None
    return _SECTION_TO_CATEGORY.get(_norm_item_name(item_name))


def load_rfr_mapping(detail_csv: str, group_csv: str,
                     test_class: str) -> Optional[Dict[str, str]]:
    """rfr_id -> top-level item name, from the DVSA lookup tables.

    Recovered contract (tests/test_defects.py, GF-17):
    - both tables are pipe-delimited;
    - detail: rfr_id|test_class_id|test_item_set_section_id
      group:  test_item_id|test_class_id|item_name
    - join detail.test_item_set_section_id = group.test_item_id WITHIN the
      same test_class_id, filtered to `test_class` (cars = "4");
    - "Road Wheels" is normalized to "Wheels";
    - returns None (not raises) when an input file is missing, string keys.
    """
    try:
        detail = pd.read_csv(detail_csv, sep="|", dtype=str)
        group = pd.read_csv(group_csv, sep="|", dtype=str)
    except FileNotFoundError:
        return None

    for frame, required in ((detail, {"rfr_id", "test_class_id", "test_item_set_section_id"}),
                            (group, {"test_item_id", "test_class_id", "item_name"})):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"RfR lookup table missing columns {sorted(missing)}")

    detail = detail[detail["test_class_id"].str.strip() == test_class]
    group = group[group["test_class_id"].str.strip() == test_class]

    joined = detail.merge(
        group,
        left_on=["test_item_set_section_id", "test_class_id"],
        right_on=["test_item_id", "test_class_id"],
        how="inner",
    )
    item_names = joined["item_name"].str.strip().replace({"Road Wheels": "Wheels"})
    return dict(zip(joined["rfr_id"].str.strip(), item_names))
