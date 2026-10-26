"""Deterministic, fully authored application fixtures. No inputs, network or research imports.

Run python -m garage.api.generate_synthetic_fixtures, then export_briefing_fixtures.
These invented histories test rendering/parser behavior; they establish no empirical coverage.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent / "fixtures" / "dvsa"


def defect(kind: str, text: str) -> dict:
    return {"type": kind, "text": text, "dangerous": kind == "DANGEROUS"}


def vehicle(index: int, registration: str | None = None) -> dict:
    reg = registration or f"ZZ{index:02d}FXT"
    scenario = index % 10
    first_year = 2010 if scenario == 9 else 2019
    tests = []
    for year_index in range(8):
        year = first_year + year_index
        at = datetime(year, 2 + index % 7, 3 + index % 20, 10, 15, tzinfo=timezone.utc)
        if scenario == 1 and year_index == 7:
            at = at.replace(year=year - 1) + timedelta(days=7)  # authored fail then pass inside fourteen days
        odo = 12000 + index * 137 + year_index * 7100
        unit = "KM" if scenario == 4 else "MI"
        if scenario == 3 and year_index == 7:
            odo = None
        if scenario == 6 and year_index == 7:
            unit = None
        if scenario == 7 and year_index == 7:
            odo = 9000  # authored odometer-drop case
        defects = [defect("ADVISORY", "Example front tyre tread observation")] if year_index in (6, 7) and scenario in (0, 4, 9) else []
        result = "PASSED"
        if scenario == 1 and year_index == 6:
            result, defects = "FAILED", [defect("MAJOR", "Example rear lamp did not illuminate")]
        if scenario == 2 and year_index == 7:
            result, defects = "FAILED", [defect("PRS", "Example washer reservoir replenished at the test")]
        if scenario == 5 and year_index == 7:
            result, defects = "FAILED", [defect("DANGEROUS", "Example tyre cord visible at test")]
        if scenario == 8 and year_index == 7:
            at = at.replace(year=2024)  # synthetic stale/misordered input history
        test = {"completedDate": at.isoformat().replace("+00:00", "Z"), "testResult": result,
                "motTestNumber": f"SYN{index:02d}{year_index:02d}0", "registrationAtTimeOfTest": reg,
                "odometerValue": str(odo) if odo is not None else None, "odometerUnit": unit,
                "expiryDate": (at.date() + timedelta(days=365)).isoformat() if result == "PASSED" else None,
                "defects": defects}
        tests.append(test)
        if scenario == 2 and year_index == 7:
            tests.append({**test, "completedDate": (at + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                          "motTestNumber": f"SYN{index:02d}{year_index:02d}1", "testResult": "PASSED",
                          "expiryDate": (at.date() + timedelta(days=365)).isoformat(), "defects": []})
    return {"registration": reg, "make": "EXAMPLE MOTORS", "model": f"FIXTURE VARIANT {index:02d}",
            "fuelType": "Petrol" if index % 2 else "Diesel", "primaryColour": "BLUE",
            "firstUsedDate": f"{first_year - 3}-01-01", "registrationDate": f"{first_year - 3}-01-01",
            "manufactureDate": f"{first_year - 3}-01-01", "engineSize": "1400", "motTests": tests}


def fixtures() -> dict[str, dict]:
    out = {f"ZZ{i:02d}FXT": vehicle(i) for i in range(50)}
    out["AA19AAA"] = vehicle(50, "AA19AAA")
    out["BB20BBB"] = vehicle(51, "BB20BBB")
    out["CC21CCC"] = {**vehicle(52, "CC21CCC"), "motTests": []}
    out["DD22DDD"] = {"__error": "transient"}
    out["EE23EEE"] = {"__error": "permanent"}
    return out


def render(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main() -> None:
    for reg, value in fixtures().items():
        (OUT / f"{reg}.json").write_text(render(value))
    print("wrote 55 fully synthetic fixture files; no source records read")


if __name__ == "__main__":
    main()
