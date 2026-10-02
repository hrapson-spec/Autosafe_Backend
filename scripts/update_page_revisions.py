"""
Review changed public-page sources and refresh page_revisions.json.

    python scripts/update_page_revisions.py --check
        Exit 1 and list every tracked file whose content changed since it was
        last reviewed; writes nothing.

    python scripts/update_page_revisions.py --significant "rewrote the age-band section"
    python scripts/update_page_revisions.py --non-significant "footer link targets" [--date YYYY-MM-DD]
        Record the changed files' new hashes. --significant also moves their
        significant_revision (the date the sitemap reports as <lastmod>) to
        --date (default: today); --non-significant leaves it untouched. The
        choice applies to every changed file in the run, so review one kind of
        change at a time. Running with changed files but neither flag is an
        error (exit 2) — the author must decide.

Significant = a change to the page's main content (titles, headings, body copy,
figures, FAQs). Not significant = boilerplate, footer, navigation links,
markup, styling, analytics/consent scripts, language tags. The date is a
commit-time fact entered by the author; seo_pages.py never reads a clock.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import page_revisions as pr  # noqa: E402

RULE = (
    "lastmod = max(dataset artifact revision where the page renders dataset figures, "
    "significant_revision of every source that renders the page). significant_revision moves only "
    "when the author records a change as --significant (main content); boilerplate, footer, markup, "
    "styling, analytics and language-tag edits are --non-significant. No runtime clock is used."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report stale files and exit non-zero; write nothing")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--significant", metavar="REASON", help="changed files alter main content; move lastmod")
    choice.add_argument("--non-significant", metavar="REASON", help="changed files are boilerplate/markup; keep lastmod")
    parser.add_argument("--date", default=date.today().isoformat(), help="ISO date of the change (default: today)")
    parser.add_argument("--manifest", default=str(pr.MANIFEST_PATH), help=argparse.SUPPRESS)
    args = parser.parse_args()
    date.fromisoformat(args.date)  # validate
    manifest_path = Path(args.manifest)

    manifest = pr.load_manifest(manifest_path) if manifest_path.exists() else {"files": {}}
    stale = pr.stale_sources(manifest)

    if args.check or (stale and not (args.significant or args.non_significant)):
        for relpath, why in stale:
            print(f"CHANGED {relpath}: {why}")
        if not stale:
            print("page_revisions.json is current")
            return 0
        if not args.check:
            print(
                f"\n{len(stale)} tracked file(s) changed. Decide and re-run with "
                '--significant "reason" (main content changed; lastmod moves) or '
                '--non-significant "reason" (boilerplate/markup; lastmod stays).',
                file=sys.stderr,
            )
            return 2
        return 1

    files = manifest["files"]
    significant = args.significant is not None
    reason = args.significant if significant else args.non_significant
    changed = []
    for relpath, _why in stale:
        if relpath not in pr.TRACKED_SOURCES:
            del files[relpath]
            changed.append(f"dropped {relpath}")
            continue
        entry = files.get(relpath) or {}
        entry["sha256"] = pr.sha256_of(relpath)
        if significant or "significant_revision" not in entry:
            entry["significant_revision"] = args.date
            entry["reason"] = reason
        entry["last_change"] = {"date": args.date, "significant": significant, "reason": reason}
        files[relpath] = entry
        changed.append(f"{relpath}: {'significant' if significant else 'non-significant'} -> lastmod {entry['significant_revision']}")

    manifest["_rule"] = RULE
    manifest["files"] = {k: files[k] for k in pr.TRACKED_SOURCES if k in files}
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    for line in changed:
        print(line)
    print("no changes" if not changed else f"{len(changed)} entries updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
