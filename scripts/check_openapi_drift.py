#!/usr/bin/env python3
"""Detect drift between main.app's live OpenAPI schema and the committed
openapi.json snapshot.

Run: python scripts/check_openapi_drift.py          (exit 0 = clean, 1 = drift)
     python scripts/check_openapi_drift.py --write   (regenerate the snapshot)

Wired into CI as the `contract-drift` job: a hard gate against the HTTP
contract (legacy routes + the v2 report API) silently changing shape
without a reviewed, committed schema update.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "openapi.json"


def generate_schema() -> dict:
    """Import main.app and generate its current OpenAPI schema.

    Importing main.py has no heavy *side effects* for this purpose: model
    loading, the Postgres pool, and the DVSA client are all initialized in
    main.py's lifespan() context manager, not at import time. `from main
    import app` alone takes a couple of seconds (dominated by importing
    already-heavy third-party libraries main.py itself imports at module
    scope, e.g. catboost/paddleocr), but performs no network calls, no
    model-weight loading, and no database connection attempt. FastAPI
    computes app.openapi_schema lazily on the first .openapi() call
    (caching it on the app instance afterwards), the same computation
    that would run for a real request to /openapi.json -- this script
    doesn't need the app "started" (lifespan run) to introspect its
    routes/models.
    """
    sys.path.insert(0, str(ROOT))
    from main import app  # noqa: E402 - sys.path must be set up first
    return app.openapi()


def render(schema: dict) -> str:
    return json.dumps(schema, sort_keys=True, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="Regenerate the committed snapshot instead of checking it.",
    )
    args = parser.parse_args()

    rendered = render(generate_schema())

    if args.write:
        SNAPSHOT_PATH.write_text(rendered)
        print("Wrote {}".format(SNAPSHOT_PATH))
        return 0

    if not SNAPSHOT_PATH.exists():
        print("No committed snapshot at {} -- run with --write first.".format(SNAPSHOT_PATH))
        return 1

    committed = SNAPSHOT_PATH.read_text()
    if committed == rendered:
        print("OpenAPI schema matches committed snapshot.")
        return 0

    import difflib
    diff_lines = list(difflib.unified_diff(
        committed.splitlines(keepends=True),
        rendered.splitlines(keepends=True),
        fromfile="openapi.json (committed)",
        tofile="openapi.json (live)",
    ))
    print("OpenAPI SCHEMA DRIFT DETECTED: {} diff line(s). Head:\n".format(len(diff_lines)))
    for line in diff_lines[:60]:
        print(line, end="")
    print("\n\nRun `python scripts/check_openapi_drift.py --write` to regenerate the snapshot, then commit it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
