"""Defect taxonomy: rfr_id -> top-level section -> 7 canonical categories.

The section hierarchy is the exact, verified aggregation level (DATA_ASSESSMENT
§5: code spaces are disjoint across 2018-05-20, so only section/category-level
features may span eras). This module:

- loads the class-4 scoped map by IMPORTING pipeline.lake.rfr_mapping
  (`load_rfr_mapping` + `project_category`), never re-implementing it;
- adds the contract's FAN-OUT GUARD: an rfr_id that resolves to more than one
  distinct top-level item_name within class 4 would be silently collapsed by
  the dict-returning loader, so we re-check it in SQL and raise;
- projects the 7 canonical categories plus an explicit 'other' bucket (items
  whose section is deliberately out of scope, e.g. exhaust/seat belts) and
  counts catalogue misses (rfr_id absent from the class-4 map) separately;
- maps the 7 categories onto the FIVE serving component names used by
  feature_engineering_v55 (brakes/tyres/suspension/steering/structure) so B0
  can be computed from the same atom.

Positional groups (B6, research-only) come from `mdr_rfr_location.csv` when the
owner supplies it; absent, the B6 columns are emitted NULL with an explicit
status rather than zero.
"""
import os
from typing import Dict, List, Optional, Tuple

from pipeline.lake.rfr_mapping import COMPONENT_CATEGORIES, load_rfr_mapping, project_category

CLASS_4 = "4"
OTHER_CATEGORY = "other"

#: Column-safe category keys in a fixed, frozen order (drives column names).
CATEGORY_KEYS: Tuple[str, ...] = tuple(c.lower() for c in COMPONENT_CATEGORIES) + (OTHER_CATEGORY,)
#: The 7 CANONICAL categories, excluding the deliberate 'other' bucket. Used by
#: the depth-capped B2 variants, which the owner scoped to the 7 (2026-08-12).
CANONICAL_CATEGORY_KEYS: Tuple[str, ...] = tuple(c.lower() for c in COMPONENT_CATEGORIES)

CATEGORY_BY_KEY: Dict[str, Optional[str]] = {
    **{c.lower(): c for c in COMPONENT_CATEGORIES},
    OTHER_CATEGORY: None,
}

#: 7 lake categories -> the 5 serving component names (feature_engineering_v55
#: COMPONENT_CATEGORIES). Visibility and lamps have no serving component.
SERVING_COMPONENTS: Tuple[str, ...] = ("brakes", "tyres", "suspension", "steering", "structure")
SERVING_COMPONENT_BY_CATEGORY: Dict[str, str] = {
    "Brakes": "brakes",
    "Tyres": "tyres",              # lake Tyres already absorbs Wheels/Road Wheels
    "Suspension": "suspension",
    "Steering": "steering",
    "Body_Chassis_Structure": "structure",
}

#: Coarse positional groups (B6), wired from mdr_rfr_location's own `lateral`
#: and `vertical` columns (owner ruling 2026-08-12). Observed vocabularies in
#: the 130-row lookup:
#:   lateral    '' Central Centre Inner Nearside 'Nearside Inner'
#:              'Nearside Outer' Offside 'Offside Inner' 'Offside Outer' Outer
#:   vertical   '' Inner Lower Outer Upper
#:   longitudinal  '' Front Rear
#: All three axes are wired (owner rulings 2026-08-12), each resolved BY COLUMN
#: NAME so a publication reorder cannot silently swap them.
LATERAL_GROUPS: Tuple[str, ...] = ("nearside", "offside", "centre", "inner_outer", "unknown")
VERTICAL_GROUPS: Tuple[str, ...] = ("upper", "lower", "inner_outer", "unknown")
LONGITUDINAL_GROUPS: Tuple[str, ...] = ("front", "rear", "unknown")

#: Ordered: a side always wins over an inner/outer qualifier, so
#: 'Nearside Inner' groups as nearside rather than inner_outer.
_LATERAL_MATCH = (
    ("nearside", ("nearside", "near side", "n/s")),
    ("offside", ("offside", "off side", "o/s")),
    ("centre", ("centre", "center", "central", "middle")),
    ("inner_outer", ("inner", "outer")),
)
_VERTICAL_MATCH = (
    ("upper", ("upper", "top")),
    ("lower", ("lower", "bottom")),
    ("inner_outer", ("inner", "outer")),
)
_LONGITUDINAL_MATCH = (
    ("front", ("front", "forward")),
    ("rear", ("rear", "back")),
)


class TaxonomyFanOut(ValueError):
    """An rfr_id resolves to >1 top-level section within class 4."""


class TaxonomyUnavailable(FileNotFoundError):
    """The class-4 lookup tables were not found where the build points."""


def load_section_map(detail_csv: str, group_csv: str,
                     test_class: str = CLASS_4) -> Dict[str, str]:
    """rfr_id -> top-level section name, class-scoped. Raises when absent."""
    mapping = load_rfr_mapping(detail_csv, group_csv, test_class)
    if mapping is None:
        raise TaxonomyUnavailable(
            f"class-{test_class} lookup tables not found: {detail_csv} / {group_csv}. "
            f"The factory refuses to build with an empty taxonomy (every rfr_id "
            f"would silently become a catalogue miss)."
        )
    return mapping


def assert_no_fanout(con, detail_csv: str, group_csv: str,
                     test_class: str = CLASS_4) -> int:
    """Fan-out guard: the dict loader keeps the LAST item_name per rfr_id, so a
    one-to-many join would be silently resolved. Re-check in SQL and raise.

    Returns the number of distinct class-scoped rfr_ids for the manifest.
    """
    for path in (detail_csv, group_csv):
        if not os.path.exists(path):
            raise TaxonomyUnavailable(f"lookup table missing: {path}")
    rows = con.execute(f"""
        WITH d AS (
            SELECT trim(rfr_id) AS rfr_id, trim(test_item_set_section_id) AS sect
            FROM read_csv('{detail_csv}', delim='|', header=true, all_varchar=true)
            WHERE trim(test_class_id) = '{test_class}'
        ), g AS (
            SELECT trim(test_item_id) AS test_item_id, trim(item_name) AS item_name
            FROM read_csv('{group_csv}', delim='|', header=true, all_varchar=true)
            WHERE trim(test_class_id) = '{test_class}'
        )
        SELECT count(*) AS n_rfr,
               count(*) FILTER (WHERE n_names > 1) AS n_fanout,
               coalesce(max(n_names), 0) AS max_names
        FROM (SELECT d.rfr_id, count(DISTINCT g.item_name) AS n_names
              FROM d JOIN g ON g.test_item_id = d.sect
              GROUP BY d.rfr_id)
    """).fetchone()
    n_rfr, n_fanout, max_names = int(rows[0]), int(rows[1]), int(rows[2])
    if n_fanout:
        raise TaxonomyFanOut(
            f"{n_fanout} class-{test_class} rfr_id(s) resolve to up to {max_names} "
            f"distinct top-level sections; the DISTINCT-mapped contract is violated "
            f"and item counts would be double-counted."
        )
    return n_rfr


def category_key(rfr_id: Optional[str], section_map: Dict[str, str]) -> Optional[str]:
    """rfr_id -> category key, or None for a CATALOGUE MISS (not 'other').

    'other' means "mapped to a section that is deliberately outside the 7
    categories"; None means "this rfr_id is not in the class-4 catalogue at
    all" -- the two are counted separately, never merged.
    """
    if rfr_id is None:
        return None
    section = section_map.get(str(rfr_id).strip())
    if section is None:
        return None
    category = project_category(section)
    return OTHER_CATEGORY if category is None else category.lower()


def category_map_rows(section_map: Dict[str, str]) -> List[Tuple[str, str, str, Optional[str]]]:
    """(rfr_id, section, category_key, serving_component) rows for registration."""
    out: List[Tuple[str, str, str, Optional[str]]] = []
    for rfr_id, section in section_map.items():
        category = project_category(section)
        key = OTHER_CATEGORY if category is None else category.lower()
        out.append((str(rfr_id), section, key,
                    SERVING_COMPONENT_BY_CATEGORY.get(category) if category else None))
    return out


def register_category_table(con, section_map: Dict[str, str]) -> None:
    """Register rfr_id -> (section, category_key, serving_component) in duckdb."""
    con.execute("DROP TABLE IF EXISTS factory_rfr_category")
    con.execute("CREATE TABLE factory_rfr_category ("
                "rfr_id VARCHAR, section VARCHAR, category_key VARCHAR, "
                "serving_component VARCHAR)")
    rows = category_map_rows(section_map)
    if rows:
        con.executemany("INSERT INTO factory_rfr_category VALUES (?, ?, ?, ?)", rows)


# --- Positional groups (B6, research-only) ----------------------------------

def _match(value: Optional[str], table) -> str:
    """First matching coarse group, else 'unknown' -- never silently bucketed."""
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    for group, needles in table:
        if any(n in text for n in needles):
            return group
    return "unknown"


def lateral_group(value: Optional[str]) -> str:
    return _match(value, _LATERAL_MATCH)


def vertical_group(value: Optional[str]) -> str:
    return _match(value, _VERTICAL_MATCH)


def longitudinal_group(value: Optional[str]) -> str:
    return _match(value, _LONGITUDINAL_MATCH)


def load_location_groups(path: Optional[str]) -> Optional[Dict[str, Tuple[str, str, str]]]:
    """location_id -> (lateral, longitudinal, vertical) groups, from
    mdr_rfr_location.csv.

    Expected layout (verified against the 130-row lookup): pipe-delimited,
    header `id|lateral|longitudinal|vertical`. The reader resolves ALL THREE
    axes BY NAME and raises when one is absent -- a positional read would
    silently swap the axes if the publication reorders them.

    Returns None when the file is absent: B6 then emits NULLs with status
    'absent' rather than zeros (a zero would assert "no defect in this
    position", which is not what an unavailable lookup means).
    """
    if not path or not os.path.exists(path):
        return None
    import csv

    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        sample = fh.readline()
        delimiter = "|" if "|" in sample else ","
        fh.seek(0)
        reader = csv.DictReader(fh, delimiter=delimiter)
        fields = [f.strip().lower() for f in (reader.fieldnames or [])]
        id_col = next((f for f in fields if f == "id" or f.endswith("_id")
                       or f.endswith("id")), None)
        missing = [c for c in ("lateral", "longitudinal", "vertical")
                   if c not in fields]
        if id_col is None or missing:
            raise ValueError(
                f"cannot read {path}: expected an id column plus 'lateral', "
                f"'longitudinal' and 'vertical', observed {fields} (missing "
                f"{missing}). Positional grouping is research-only; fix the file "
                f"or omit it (B6 then emits NULL + status)."
            )
        groups: Dict[str, Tuple[str, str, str]] = {}
        for raw in reader:
            row = {(k or "").strip().lower(): v for k, v in raw.items()}
            key = (row.get(id_col) or "").strip()
            if not key:
                continue
            groups[key] = (lateral_group(row.get("lateral")),
                           longitudinal_group(row.get("longitudinal")),
                           vertical_group(row.get("vertical")))
    return groups
