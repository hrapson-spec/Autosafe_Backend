#!/usr/bin/env python3
"""Instrument proof: reproduce/cite the certified lake anchors before any new claim.

REPRODUCE (parquet footers, metadata only — no data scan):
  R1 local results rows (test_year=2015..2023)  == 354,057,034
  R2 local items rows   (test_year=2005..2023)  == 1,289,329,470
  R3 results test_year=2022 rows                == 41,632,878

CITE (lake_manifest.json — parked years are not locally reproducible):
  C1 manifest results rows_ingested total       == 681,724,337
  C2 parked = C1 - R1                           == 327,667,303
  C3 manifest items rows_ingested total         == R2
  C4 year_volumes (class-4) recorded detail sums== 644,727,311 across 19 years

Exit 0 only if every anchor holds. Any mismatch = STOP the assessment.
"""
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

LAKE = Path("/Users/henrirapson/autosafe/autosafe_lake")
OUT = Path(__file__).resolve().parent.parent / "out" / "anchors.json"

EXPECT = {
    "local_results": 354_057_034,
    "local_items": 1_289_329_470,
    "results_2022": 41_632_878,
    "manifest_results_total": 681_724_337,
    "parked_results": 327_667_303,
    "year_volumes_class4_total": 644_727_311,
}


def footer_rows(dataset_dir: Path) -> dict[str, int]:
    per_year: dict[str, int] = {}
    for ydir in sorted(dataset_dir.glob("test_year=*")):
        n = 0
        for f in sorted(ydir.rglob("*.parquet")):
            n += pq.ParquetFile(f).metadata.num_rows
        per_year[ydir.name.split("=")[1]] = n
    return per_year


def main() -> int:
    res = footer_rows(LAKE / "results")
    items = footer_rows(LAKE / "items")

    manifest = json.loads((LAKE / "lake_manifest.json").read_text())
    man_results = sum(
        s["rows_ingested"] for s in manifest["sources"] if s["schema"].startswith("results")
    )
    man_items = sum(
        s["rows_ingested"] for s in manifest["sources"] if s["schema"].startswith("items")
    )

    yv_detail = None
    for chk in manifest.get("checks", []):
        if chk.get("name") == "year_volumes":
            yv_detail = chk  # keep the LAST recorded run
    yv_total = None
    yv_table = None
    if yv_detail is not None:
        detail = yv_detail.get("detail") or yv_detail.get("details") or ""
        if isinstance(detail, dict):
            yv_table = detail
            yv_total = sum(int(v) for v in detail.values())
        else:
            import re

            pairs = re.findall(r"(20\d\d)\D+?([\d,]{4,})", str(detail))
            yv_table = {y: int(v.replace(",", "")) for y, v in pairs}
            yv_total = sum(yv_table.values()) if yv_table else None

    got = {
        "local_results": sum(res.values()),
        "local_items": sum(items.values()),
        "results_2022": res.get("2022", -1),
        "manifest_results_total": man_results,
        "parked_results": man_results - sum(res.values()),
        "manifest_items_total": man_items,
        "year_volumes_class4_total": yv_total,
    }

    checks = {}
    ok = True
    for key, want in EXPECT.items():
        have = got.get(key)
        passed = have == want
        checks[key] = {"expected": want, "observed": have, "pass": passed}
        ok &= passed
    # items manifest must equal items footers (not a separate literal)
    items_match = man_items == got["local_items"]
    checks["manifest_items_equals_footers"] = {
        "expected": got["local_items"],
        "observed": man_items,
        "pass": items_match,
    }
    ok &= items_match

    payload = {
        "anchors": checks,
        "per_year_results_local_footer": res,
        "per_year_items_local_footer": items,
        "year_volumes_class4_recorded": yv_table,
        "manifest_tool_git_sha": manifest.get("tool_git_sha"),
        "verdict": "PASS" if ok else "FAIL",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(json.dumps(checks, indent=2, sort_keys=True))
    print("VERDICT:", payload["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
