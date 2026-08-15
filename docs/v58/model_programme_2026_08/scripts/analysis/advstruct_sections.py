"""ADVSTRUCT_ONTOLOGY_V1 -- the canonical physical-system crosswalk, and its census.

PREREG_ADVSTRUCT_2026_08_15.md (sha 35ee4828c47f4b88) sections 4.1-4.5.

Raw `sect` strings in the packet payload are NOT canonical: the DVSA catalogue has
been republished under several vintages, so the same physical system appears under
several spellings and casings ("Tyres"/"Wheels", "Body, chassis, structure" /
"Body, Structure and General Items"). Counting DISTINCT raw sect therefore inflates
breadth as a function of catalogue vintage -- which is a function of era -- and would
manufacture exactly the gradient section 10 exists to detect.

This module is the single source of truth for that folding. It is a VERSIONED
ONTOLOGY, not an implementation detail: any change to the mapping increments
ONTOLOGY_VERSION and invalidates prior results rather than silently re-mapping them.

FAIL CLOSED (prereg 4.3): a non-null raw section absent from the ontology RAISES.
It is never bucketed to 'other', never dropped, never fuzzy-matched. A new catalogue
vintage is a reason to version the ontology, not to guess. NULL sect is a distinct,
expected state (catalogue miss) and is counted, not raised on.

Normalisation is delegated to pipeline.lake.rfr_mapping._norm_item_name. This module
never defines its own normaliser -- two normalisers is how the programme ends up with
two incompatible section vocabularies again.

Usage:
    python scripts/analysis/advstruct_sections.py --out out/ADVSTRUCT_TAXONOMY.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent.parent))  # autosafe-v58 root, for `pipeline`

from pipeline.lake.rfr_mapping import _norm_item_name, project_category  # noqa: E402

ONTOLOGY_VERSION = "ADVSTRUCT_ONTOLOGY_V1"

#: Canonical physical systems. A vehicle's advisory BREADTH is the number of
#: distinct entries of this set advised on the day(s) in question.
#:
#: `wheels_tyres` is NOT named `tyres`: the lake folds "Road Wheels" -> "Wheels",
#: and "Wheels" folds in here, so a label naming only tyres would name a proper
#: subset of its own contents (prereg 4.4).
SYSTEMS: Tuple[str, ...] = (
    "wheels_tyres",
    "brakes",
    "suspension",
    "steering",
    "body_structure",
    "lamps_electrical",
    "visibility",
    "noise_emissions",
    "seatbelts_srs",
)

#: Buckets that are NOT vehicle systems. Excluded from breadth, retained as
#: ADV_AUDIT exposure columns so the exclusion is auditable and its own
#: predictive value remains measurable (prereg 4.4, 5.4).
EXCLUDED: Tuple[str, ...] = (
    "noncomponent",     # free-text advisories with no component
    "identification",   # plates, VIN, vehicle identity -- paperwork, not deterioration
    "not_tested",       # items not tested / out-of-scope supplementary tests
)

#: normalised raw section -> canonical system, or an EXCLUDED bucket.
#: Keys are the output of _norm_item_name (lowercase, punctuation -> space,
#: whitespace collapsed), so case and punctuation variants collapse before lookup.
_CROSSWALK: Dict[str, str] = {
    # --- wheels & tyres (two catalogue vintages for the same physical system)
    "tyres": "wheels_tyres",
    "wheels": "wheels_tyres",
    "road wheels": "wheels_tyres",
    # --- brakes
    "brakes": "brakes",
    "brake performance": "brakes",
    # --- suspension / steering
    "suspension": "suspension",
    "steering": "steering",
    # --- body & structure
    "body chassis structure": "body_structure",
    "body structure and attachments": "body_structure",
    "body structure and general items": "body_structure",
    "towbars": "body_structure",
    # --- lamps & electrical
    "lamps reflectors and electrical equipment": "lamps_electrical",
    "lamps reflectors electrical equipment": "lamps_electrical",
    # --- visibility
    "visibility": "visibility",
    "driver s view of the road": "visibility",
    # --- noise, emissions, exhaust, fuel
    "noise emissions and leaks": "noise_emissions",
    "exhaust emissions": "noise_emissions",
    "exhaust fuel and emissions": "noise_emissions",
    "fuel and exhaust": "noise_emissions",
    # --- restraint systems
    "seat belts": "seatbelts_srs",
    "seat belts and supplementary restraint systems": "seatbelts_srs",
    "seat belt installation check": "seatbelts_srs",
    "seat belt installation checks": "seatbelts_srs",
    "supplementary restraint systems": "seatbelts_srs",
    # --- NOT vehicle systems: excluded from breadth, counted as audit exposures
    "non component advisories": "noncomponent",
    "identification of the vehicle": "identification",
    "registration plates and vin": "identification",
    "items not tested": "not_tested",
    "motor tricycles and quadricycles": "not_tested",
    "buses and coaches supplementary tests": "not_tested",
    "driving controls and speed limiters": "not_tested",
    "speedometer and speed limiter": "not_tested",
}

CATALOGUE_MISS = "__catalogue_miss__"


class UnknownSection(KeyError):
    """A non-null raw section absent from ADVSTRUCT_ONTOLOGY_V1.

    Deliberately fatal. See the module docstring: a new catalogue vintage is a
    reason to version the ontology, not to guess a mapping.
    """


def canonical_system(raw_sect: Optional[str]) -> str:
    """Raw packet `sect` -> canonical system, EXCLUDED bucket, or CATALOGUE_MISS.

    Raises UnknownSection for any non-null value not in the ontology.
    """
    if raw_sect is None or not str(raw_sect).strip():
        return CATALOGUE_MISS
    key = _norm_item_name(str(raw_sect))
    if key not in _CROSSWALK:
        raise UnknownSection(
            f"{ONTOLOGY_VERSION} has no entry for section {raw_sect!r} "
            f"(normalised {key!r}). Fail-closed per PREREG_ADVSTRUCT 4.3: extend and "
            f"version the ontology, do not bucket to 'other'."
        )
    return _CROSSWALK[key]


def is_system(canon: str) -> bool:
    """Does this canonical value count towards BREADTH?"""
    return canon in SYSTEMS


def sql_case_expr(raw_col: str) -> str:
    """The crosswalk as a duckdb CASE over a raw sect column.

    Emitted rather than applied row-by-row in Python so the census and the feature
    build share one definition and cannot drift. Unknown values map to NULL here;
    the caller MUST assert the unknown count is zero (fail-closed happens at the
    call site, not silently inside the expression).
    """
    norm = (f"trim(regexp_replace(lower({raw_col}), '[^a-z0-9]+', ' ', 'g'))")
    whens = "\n    ".join(
        f"WHEN {norm} = '{k}' THEN '{v}'" for k, v in sorted(_CROSSWALK.items())
    )
    return (f"CASE\n    WHEN {raw_col} IS NULL OR trim({raw_col}) = '' "
            f"THEN '{CATALOGUE_MISS}'\n    {whens}\n    ELSE NULL END")


# ------------------------------------------------------------------ census

PACKET_SETS = {
    "train_flat4y": "out/frames_v2_flat4y/recipe=flat4y/rung=r1m/packets/*.parquet",
    "eval2024": "out/frames_eval_v2/recipe=eval2024/rung=all/packets/*.parquet",
}


def census(con, packets_glob: str) -> dict:
    """Volumes per raw section per year over the FULL eligible advisory history.

    Prereg 4.2: the ontology may not be built from one frame's most-recent day.
    """
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE adv AS
        SELECT tgt_id, p_date, year(p_date) AS p_year,
               json_extract_string(j, '$.sect') AS sect,
               json_extract_string(j, '$.rfr')  AS rfr,
               json_extract_string(j, '$.cat')  AS cat
        FROM read_parquet('{packets_glob}', union_by_name=true),
             unnest(json_extract(defects_json, '$[*]')) AS t(j)
        WHERE p_date IS NOT NULL
          AND defects_json IS NOT NULL AND defects_json <> '[]'
          AND json_extract_string(j, '$.disp') = 'A'
    """)
    rows = con.execute(
        "SELECT sect, p_year, count(*) FROM adv GROUP BY 1,2 ORDER BY 1,2").fetchall()

    by_raw: Dict[str, dict] = {}
    unknown: Dict[str, int] = {}
    for sect, year, n in rows:
        try:
            canon = canonical_system(sect)
        except UnknownSection:
            unknown[str(sect)] = unknown.get(str(sect), 0) + int(n)
            continue
        key = str(sect) if sect is not None else "__NULL__"
        rec = by_raw.setdefault(key, {"canonical": canon, "n": 0, "by_year": {}})
        rec["n"] += int(n)
        rec["by_year"][str(year)] = rec["by_year"].get(str(year), 0) + int(n)

    per_system: Dict[str, int] = {}
    for rec in by_raw.values():
        per_system[rec["canonical"]] = per_system.get(rec["canonical"], 0) + rec["n"]

    return {
        "n_advisory_items": sum(r["n"] for r in by_raw.values()) + sum(unknown.values()),
        "n_distinct_raw_sections": len(by_raw),
        "raw_sections": dict(sorted(by_raw.items(), key=lambda kv: -kv[1]["n"])),
        "per_canonical": dict(sorted(per_system.items(), key=lambda kv: -kv[1])),
        "unknown_sections": unknown,
    }


def folding_effect(con, packets_glob: str) -> dict:
    """Folded vs UNFOLDED breadth on the primary window (most recent iod).

    Publishes the size of the vintage artefact the folding removes (prereg 4.4).
    Deduplicated per prereg 5.6 before counting.
    """
    case = sql_case_expr("sect")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE mr AS
        SELECT * FROM (
            SELECT tgt_id, p_date, defects_json,
                   dense_rank() OVER (PARTITION BY tgt_id ORDER BY p_date DESC) rk
            FROM read_parquet('{packets_glob}', union_by_name=true)
            WHERE p_date IS NOT NULL)
        WHERE rk = 1
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE it AS
        SELECT DISTINCT tgt_id, p_date,
               json_extract_string(j,'$.sect') AS sect,
               {case} AS canon,
               coalesce(json_extract_string(j,'$.rfr'),
                        json_extract_string(j,'$.sect')) AS item_key
        FROM mr, unnest(json_extract(defects_json,'$[*]')) AS t(j)
        WHERE defects_json IS NOT NULL AND defects_json <> '[]'
          AND json_extract_string(j,'$.disp') = 'A'
    """)
    # Two DISTINCT corrections, decomposed rather than conflated:
    #   raw    -> all_canon : VINTAGE folding only (Tyres/Wheels, case variants)
    #   all_canon -> systems: EXCLUSION of non-system buckets (noncomponent etc.)
    row = con.execute(f"""
        SELECT count(*)                                          AS n_targets,
               avg(b_raw)                                        AS mean_raw,
               avg(b_all_canon)                                  AS mean_all_canon,
               avg(b_systems)                                    AS mean_systems,
               avg(b_raw - b_all_canon)                          AS mean_vintage_infl,
               sum(CASE WHEN b_raw > b_all_canon THEN 1 ELSE 0 END) AS n_vintage_infl,
               max(b_raw - b_all_canon)                          AS max_vintage_infl,
               avg(b_all_canon - b_systems)                      AS mean_exclusion_drop,
               sum(CASE WHEN b_all_canon > b_systems THEN 1 ELSE 0 END) AS n_exclusion_drop,
               max(b_all_canon - b_systems)                      AS max_exclusion_drop
        FROM (
            SELECT tgt_id,
                   count(DISTINCT sect)  AS b_raw,
                   count(DISTINCT CASE WHEN canon <> '{CATALOGUE_MISS}' THEN canon END)
                       AS b_all_canon,
                   count(DISTINCT CASE WHEN canon IN {SYSTEMS} THEN canon END)
                       AS b_systems
            FROM it GROUP BY 1)
    """).fetchone()
    n_unknown = con.execute(
        "SELECT count(*) FROM it WHERE canon IS NULL").fetchone()[0]
    return {
        "n_targets_with_advisory_on_last_day": int(row[0]),
        "mean_breadth_raw_sect": round(float(row[1]), 6),
        "mean_breadth_all_canonical": round(float(row[2]), 6),
        "mean_breadth_systems_only": round(float(row[3]), 6),
        "vintage_folding": {
            "mean_inflation": round(float(row[4]), 6),
            "n_targets_inflated": int(row[5]),
            "max_inflation": int(row[6]),
        },
        "nonsystem_exclusion": {
            "mean_drop": round(float(row[7]), 6),
            "n_targets_affected": int(row[8]),
            "max_drop": int(row[9]),
        },
        "n_unknown_section_rows": int(n_unknown),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/ADVSTRUCT_TAXONOMY.json")
    ap.add_argument("--memory-limit", default="2GB")
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()

    import duckdb
    os.chdir(ROOT)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.memory_limit}'")
    con.execute(f"SET threads={a.threads}")
    con.execute(f"SET temp_directory='{ROOT}/out/_tmp_advstruct_{os.getpid()}'")
    con.execute("INSTALL json; LOAD json;")

    out = {
        "artifact": "ADVSTRUCT canonical-system ontology and census",
        "prereg": "prereg/PREREG_ADVSTRUCT_2026_08_15.md",
        "prereg_sha256_16": "35ee4828c47f4b88",
        "ontology_version": ONTOLOGY_VERSION,
        "systems": list(SYSTEMS),
        "excluded_buckets": list(EXCLUDED),
        "crosswalk_normalised": _CROSSWALK,
        "secondary_grain_category_projection": {
            s: project_category(s) for s in sorted(_CROSSWALK)},
        "census": {},
        "folding_effect": {},
    }

    fatal = {}
    for name, glob in PACKET_SETS.items():
        c = census(con, glob)
        out["census"][name] = c
        if c["unknown_sections"]:
            fatal[name] = c["unknown_sections"]
        out["folding_effect"][name] = folding_effect(con, glob)

    out["gates"] = {
        "fail_closed_unknown_sections": {k: v for k, v in fatal.items()},
        "passed": not fatal,
    }

    if fatal:
        print("FATAL: unknown non-null sections (prereg 4.3 fail-closed):",
              json.dumps(fatal, indent=1), file=sys.stderr)
        Path(a.out).write_text(json.dumps(out, indent=1))
        return 2

    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"wrote {a.out}")
    for name in PACKET_SETS:
        c, f = out["census"][name], out["folding_effect"][name]
        print(f"\n[{name}] {c['n_advisory_items']:,} advisory items, "
              f"{c['n_distinct_raw_sections']} distinct raw sections")
        for k, v in c["per_canonical"].items():
            print(f"    {k:<22}{v:>14,}")
        v, x = f["vintage_folding"], f["nonsystem_exclusion"]
        print(f"  mean breadth: raw {f['mean_breadth_raw_sect']} -> "
              f"canonical {f['mean_breadth_all_canonical']} -> "
              f"systems {f['mean_breadth_systems_only']}")
        print(f"    VINTAGE folding  : +{v['mean_inflation']} mean, "
              f"{v['n_targets_inflated']:,} targets inflated, max +{v['max_inflation']}")
        print(f"    NON-SYSTEM excl. : -{x['mean_drop']} mean, "
              f"{x['n_targets_affected']:,} targets affected, max -{x['max_drop']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
