"""Synthetic lake generator: hive results/items parquet + class-4 lookup CSVs.

Everything the factory's tests read is produced here. The generator writes the
lake's real column set, INCLUDING the derived item columns that are known-wrong
post-2018 (is_fail_item / is_advisory / is_dangerous / rfr_class): the fixture
deliberately populates them with the F-22 defect so a test can prove the factory
never reads them (test_severity.py::test_factory_ignores_stored_derived_columns).
"""
import json
import os
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from ..severity import TAXONOMY_SWITCH_ISO
from ..sources import Inputs

TAXONOMY_SWITCH = date.fromisoformat(TAXONOMY_SWITCH_ISO)

RESULTS_SCHEMA = pa.schema([
    ("test_id", pa.int64()), ("vehicle_id", pa.int64()), ("test_date", pa.date32()),
    ("test_class_id", pa.string()), ("test_type", pa.string()), ("outcome", pa.string()),
    ("test_mileage", pa.int64()), ("postcode_area", pa.string()), ("make", pa.string()),
    ("model", pa.string()), ("model_id", pa.string()), ("colour", pa.string()),
    ("fuel_type", pa.string()), ("cylinder_capacity", pa.int32()),
    ("first_use_date", pa.date32()), ("age_source", pa.string()),
    ("age_at_test", pa.float64()), ("taxonomy_era", pa.string()),
    ("schema_epoch", pa.string()),
])

ITEMS_SCHEMA = pa.schema([
    ("test_id", pa.int64()), ("rfr_id", pa.string()), ("rfr_type_code", pa.string()),
    ("location_id", pa.string()), ("dangerous_mark", pa.string()),
    ("taxonomy_era", pa.string()), ("rfr_class", pa.string()),
    ("is_fail_item", pa.bool_()), ("is_advisory", pa.bool_()),
    ("is_dangerous", pa.bool_()), ("item_name", pa.string()),
    ("component_category", pa.string()),
])

COMPLETED_TS_SCHEMA = pa.schema([("test_id", pa.int64()),
                                 ("completed_at", pa.timestamp("us"))])

#: rfr_id -> (section_id, item_name). Two disjoint code trees, as in the real
#: publication (legacy 5xxx / post-2018 2xxxx; DATA_ASSESSMENT §5).
CATALOGUE: Dict[str, tuple] = {
    "5001": ("501", "Brakes"),
    "5002": ("502", "Suspension"),
    "5003": ("503", "Tyres"),
    "5004": ("504", "Steering"),
    "5005": ("505", "Body structure and attachments"),
    "5006": ("506", "Visibility"),
    "5007": ("507", "Lamps Reflectors and Electrical Equipment"),
    "5008": ("508", "Exhaust Emissions"),
    "5009": ("509", "Road Wheels"),
    "20001": ("2001", "Brakes"),
    "20002": ("2002", "Suspension"),
    "20003": ("2003", "Tyres"),
    "20004": ("2004", "Steering"),
    "20005": ("2005", "Body structure and general items"),
    "20006": ("2006", "Visibility"),
    "20007": ("2007", "Lamps Reflectors and Electrical Equipment"),
    "20008": ("2008", "Exhaust fuel and emissions"),
}
#: An rfr_id deliberately absent from the class-4 catalogue (catalogue miss).
UNCATALOGUED_RFR = "99999"

#: Shaped exactly like the real lookup: pipe-delimited, header
#: `id|lateral|longitudinal|vertical`, values such as '31|Nearside|Front|Lower'.
#: Empty strings are real and common (row '1|||').
LOCATIONS = {
    "1": ("", "", ""),
    "31": ("Nearside", "Front", "Lower"),
    "32": ("Offside", "Rear", "Upper"),
    "33": ("Centre", "", ""),
    "34": ("Nearside Inner", "Front", "Inner"),
    "35": ("Outer", "Rear", "Outer"),
}


@dataclass
class TestRow:
    __test__ = False        # not a pytest collection target

    test_id: int
    vehicle_id: int
    test_date: date
    test_type: str = "NT"
    outcome: str = "PASS"
    test_mileage: Optional[int] = 40_000
    test_class_id: str = "4"
    make: str = "FORD"
    model: str = "FOCUS"
    colour: str = "BLUE"
    fuel_type: str = "PE"
    cylinder_capacity: Optional[int] = 1600
    first_use_date: Optional[date] = date(2010, 3, 1)
    postcode_area: str = "SW"
    #: publisher epoch of the source extract. Variable so a fixture can express
    #: the measured 2024-25 `results_extracts` item cliff (PREREG_CUBE_v2 §4).
    schema_epoch: str = "results_mts"

    def as_dict(self) -> dict:
        era = "post_2018" if self.test_date >= TAXONOMY_SWITCH else "pre_2018"
        age = ((self.test_date - self.first_use_date).days / 365.25
               if self.first_use_date else None)
        return {
            "test_id": self.test_id, "vehicle_id": self.vehicle_id,
            "test_date": self.test_date, "test_class_id": self.test_class_id,
            "test_type": self.test_type, "outcome": self.outcome,
            "test_mileage": self.test_mileage, "postcode_area": self.postcode_area,
            "make": self.make, "model": self.model,
            "model_id": f"{self.make} {self.model}", "colour": self.colour,
            "fuel_type": self.fuel_type, "cylinder_capacity": self.cylinder_capacity,
            "first_use_date": self.first_use_date,
            "age_source": "first_use_date" if self.first_use_date else "missing",
            "age_at_test": age, "taxonomy_era": era,
            "schema_epoch": self.schema_epoch,
        }


@dataclass
class ItemRow:
    test_id: int
    rfr_id: str
    rfr_type_code: str
    location_id: Optional[str] = "1"
    dangerous_mark: Optional[str] = None
    #: era is resolved from the parent test at write time
    _era: str = field(default="", repr=False)

    def as_dict(self, era: str) -> dict:
        name = CATALOGUE.get(self.rfr_id, (None, None))[1]
        # DELIBERATELY WRONG post-2018 derived columns (F-22): 'M' recorded as
        # major/fail-item, so any factory read of them is detectable.
        wrong_class = {"F": "fail", "P": "prs", "A": "advisory",
                       "M": "major", "D": "dangerous"}.get(
                           (self.rfr_type_code or "").strip(), None)
        return {
            "test_id": self.test_id, "rfr_id": self.rfr_id,
            "rfr_type_code": self.rfr_type_code, "location_id": self.location_id,
            "dangerous_mark": self.dangerous_mark, "taxonomy_era": era,
            "rfr_class": wrong_class,
            "is_fail_item": wrong_class in ("fail", "prs", "major", "dangerous"),
            "is_advisory": wrong_class == "advisory",
            "is_dangerous": wrong_class == "dangerous",
            "item_name": name, "component_category": None,
        }


class FixtureLake:
    """Build a synthetic lake under `root` and hand back an `Inputs`."""

    def __init__(self, root: str):
        self.root = str(root)
        self.tests: List[TestRow] = []
        self.items: List[ItemRow] = []
        self.completed: List[tuple] = []
        os.makedirs(self.root, exist_ok=True)

    # --- population -----------------------------------------------------

    def add_test(self, *args, **kwargs) -> TestRow:
        row = args[0] if (args and isinstance(args[0], TestRow)) else TestRow(*args, **kwargs)
        self.tests.append(row)
        return row

    def add_item(self, *args, **kwargs) -> ItemRow:
        row = args[0] if (args and isinstance(args[0], ItemRow)) else ItemRow(*args, **kwargs)
        self.items.append(row)
        return row

    def add_completed_ts(self, test_id: int, when) -> None:
        self.completed.append((test_id, when))

    # --- write ----------------------------------------------------------

    def _write_partitioned(self, rows: List[dict], schema: pa.Schema,
                           dataset: str, years: Sequence[int]) -> None:
        by_year: Dict[int, List[dict]] = {}
        for row in rows:
            by_year.setdefault(row.pop("__year"), []).append(row)
        for year in sorted(set(list(by_year) + list(years))):
            part_dir = os.path.join(self.root, dataset, f"test_year={year}")
            os.makedirs(part_dir, exist_ok=True)
            chunk = by_year.get(year, [])
            columns = {name: [r.get(name) for r in chunk] for name in schema.names}
            pq.write_table(pa.Table.from_pydict(columns, schema=schema),
                           os.path.join(part_dir, "part_0.parquet"))

    def write(self, years: Optional[Sequence[int]] = None,
              with_location_csv: bool = False,
              with_completed_ts: bool = False) -> Inputs:
        era_by_test = {t.test_id: ("post_2018" if t.test_date >= TAXONOMY_SWITCH
                                   else "pre_2018") for t in self.tests}
        year_by_test = {t.test_id: t.test_date.year for t in self.tests}

        result_rows = []
        for test in self.tests:
            row = test.as_dict()
            row["__year"] = test.test_date.year
            result_rows.append(row)
        item_rows = []
        for item in self.items:
            if item.test_id not in era_by_test:
                raise ValueError(f"item references unknown test_id {item.test_id}")
            row = item.as_dict(era_by_test[item.test_id])
            row["__year"] = year_by_test[item.test_id]
            item_rows.append(row)

        years = years or sorted({t.test_date.year for t in self.tests})
        self._write_partitioned(result_rows, RESULTS_SCHEMA, "results", years)
        self._write_partitioned(item_rows, ITEMS_SCHEMA, "items", years)

        detail = os.path.join(self.root, "item_detail.csv")
        group = os.path.join(self.root, "item_group.csv")
        with open(detail, "w", encoding="utf-8") as fh:
            fh.write("rfr_id|test_class_id|test_item_set_section_id\n")
            for rfr_id, (section, _name) in CATALOGUE.items():
                fh.write(f"{rfr_id}|4|{section}\n")
                fh.write(f"{rfr_id}|7|{section}\n")   # cross-class reuse (guard)
        with open(group, "w", encoding="utf-8") as fh:
            fh.write("test_item_id|test_class_id|item_name\n")
            seen = set()
            for _rfr_id, (section, name) in CATALOGUE.items():
                if section in seen:
                    continue
                seen.add(section)
                fh.write(f"{section}|4|{name}\n")
                fh.write(f"{section}|7|OTHER CLASS NAME\n")

        location_csv = None
        if with_location_csv:
            location_csv = os.path.join(self.root, "mdr_rfr_location.csv")
            with open(location_csv, "w", encoding="utf-8") as fh:
                fh.write("id|lateral|longitudinal|vertical\n")
                for key, (lat, lon, vert) in LOCATIONS.items():
                    fh.write(f"{key}|{lat}|{lon}|{vert}\n")

        completed_path = None
        if with_completed_ts and self.completed:
            completed_path = os.path.join(self.root, "completed_ts")
            for test_id, when in self.completed:
                year = year_by_test[test_id]
                part = os.path.join(completed_path, f"test_year={year}")
                os.makedirs(part, exist_ok=True)
            by_year: Dict[int, List[tuple]] = {}
            for test_id, when in self.completed:
                by_year.setdefault(year_by_test[test_id], []).append((test_id, when))
            for year, rows in by_year.items():
                table = pa.Table.from_pydict(
                    {"test_id": [r[0] for r in rows],
                     "completed_at": [r[1] for r in rows]}, schema=COMPLETED_TS_SCHEMA)
                pq.write_table(table, os.path.join(
                    completed_path, f"test_year={year}", "part_0.parquet"))

        return Inputs(
            results=os.path.join(self.root, "results", "**", "*.parquet"),
            items=os.path.join(self.root, "items", "**", "*.parquet"),
            item_detail_csv=detail, item_group_csv=group,
            completed_ts=(os.path.join(completed_path, "**", "*.parquet")
                          if completed_path else None),
            location_csv=location_csv)


def write_item_coverage_ledger(path: str, rows: Sequence[Sequence[str]]) -> str:
    """Write an item-coverage cell ledger (pipe-delimited, as the owner ships it).

    Each row is (test_date, schema_epoch, outcome_class, coverage, attribution,
    note); an empty field is a wildcard. A row with all three keys empty is the
    DEFAULT rule and is what makes a build `certified` rather than
    `assumed_covered`.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("test_date|schema_epoch|outcome_class|coverage|attribution|note\n")
        for row in rows:
            cells = list(row) + [""] * (6 - len(row))
            fh.write("|".join(str(c) for c in cells) + "\n")
    return path


def write_p4_certification(path: str, verdict: str = "PASS") -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"verdict": verdict, "generated": "fixture",
                   "rule": "post-2018 dangerous = dangerous_mark 'D'; M = MINOR; "
                           "fail-bearing = {F,P}"}, fh)
    return path


def default_population(root: str, n_vehicles: int = 400, seed: int = 7,
                       start_year: int = 2015, n_years: int = 8) -> FixtureLake:
    """A small multi-year population for sampling / nesting / property tests."""
    rng = random.Random(seed)
    lake = FixtureLake(root)
    test_id = 1_000_000
    for vehicle in range(1, n_vehicles + 1):
        first_use = date(rng.randint(2000, 2014), rng.randint(1, 12), rng.randint(1, 28))
        mileage = rng.randint(5_000, 60_000)
        base_day = date(start_year, rng.randint(1, 12), rng.randint(1, 28))
        for offset in range(n_years):
            test_id += 1
            day = base_day + timedelta(days=365 * offset + rng.randint(-20, 20))
            outcome = rng.choices(["PASS", "FAIL", "PRS"], weights=[70, 25, 5])[0]
            mileage += rng.randint(3_000, 14_000)
            lake.add_test(TestRow(test_id=test_id, vehicle_id=vehicle, test_date=day,
                                  outcome=outcome, test_mileage=mileage,
                                  first_use_date=first_use))
            post = day >= TAXONOMY_SWITCH
            n_items = 0
            for _ in range(rng.randint(0, 3)):
                rfr = rng.choice([k for k in CATALOGUE if (k.startswith("2") if post
                                                           else k.startswith("5"))])
                code = rng.choice(["A", "F", "P"] + (["M"] if post else []))
                mark = "D" if (post and code == "F" and rng.random() < 0.1) else None
                lake.add_item(ItemRow(test_id=test_id, rfr_id=rfr, rfr_type_code=code,
                                      location_id=rng.choice(list(LOCATIONS)),
                                      dangerous_mark=mark))
                n_items += 1
            if outcome in ("FAIL", "PRS") and n_items == 0:
                # A FAIL or PRS with zero reasons-for-rejection is a
                # DEFINITIONALLY IMPOSSIBLE state (ITEM_OBSERVABILITY_DESIGN
                # §1.2) -- it is the lake's strongest detector of missing items,
                # so a synthetic population must not manufacture it by accident.
                # Deliberately draws NO random numbers: the rest of the
                # population stays bit-identical to earlier fixture vintages.
                lake.add_item(ItemRow(test_id=test_id,
                                      rfr_id=("20001" if post else "5001"),
                                      rfr_type_code="F", location_id="1"))
            if outcome == "FAIL" and rng.random() < 0.35:
                test_id += 1
                lake.add_test(TestRow(test_id=test_id, vehicle_id=vehicle,
                                      test_date=day, test_type="RT", outcome="PASS",
                                      test_mileage=mileage, first_use_date=first_use))
    return lake
