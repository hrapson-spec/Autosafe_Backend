"""
Refresh page_revisions.json after editing a public-page source file.

    python scripts/update_page_revisions.py                 # stamp changed files with today's date
    python scripts/update_page_revisions.py --date 2026-10-01
    python scripts/update_page_revisions.py --check         # exit 1 if any tracked file is stale

Only files whose content hash changed (or that are newly tracked) receive the
date; unchanged files keep their recorded revision. The date is the day the
source was changed by its author (a commit-time fact), never a runtime clock:
seo_pages.py reads the recorded dates and does no date arithmetic of its own.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import page_revisions as pr  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=date.today().isoformat(), help="ISO date to record for changed files")
    parser.add_argument("--check", action="store_true", help="report stale files and exit non-zero; write nothing")
    args = parser.parse_args()
    date.fromisoformat(args.date)  # validate

    manifest = pr.load_manifest() if pr.MANIFEST_PATH.exists() else {"files": {}}
    stale = pr.stale_sources(manifest)
    if args.check:
        for relpath, reason in stale:
            print(f"STALE {relpath}: {reason}")
        print("page_revisions.json is current" if not stale else f"{len(stale)} stale entries")
        return 1 if stale else 0

    files = manifest["files"]
    changed = []
    for relpath in pr.TRACKED_SOURCES:
        digest = pr.sha256_of(relpath)
        entry = files.get(relpath)
        if entry is None or entry.get("sha256") != digest:
            files[relpath] = {"sha256": digest, "revised": args.date}
            changed.append(relpath)
    for relpath in [p for p in files if p not in pr.TRACKED_SOURCES]:
        del files[relpath]
        changed.append(f"-{relpath}")

    manifest["files"] = {k: files[k] for k in pr.TRACKED_SOURCES}
    manifest["_rule"] = (
        "revised = date the file's content last changed (set by this script when the sha256 differs); "
        "sitemap lastmod = max(dataset artifact revision where applicable, revised dates of the page's sources). "
        "No runtime clock is used."
    )
    with open(pr.MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=False)
        fh.write("\n")
    for relpath in changed:
        print(f"updated {relpath} -> {args.date}")
    print("no changes" if not changed else f"{len(changed)} entries updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
