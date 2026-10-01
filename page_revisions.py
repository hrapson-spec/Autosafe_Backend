"""
Source-file revision dates for public page <lastmod> values.
=============================================================

Sitemap ``<lastmod>`` must state when a page last changed. Two things change a
public page here: the checked-in dataset artifact (``DATASET_ARTIFACT_REVISION``
in report_contract.py) and the source files that render it (Jinja templates,
static guide/legal HTML, the SPA shell). Neither a worker restart nor the
current date changes a page, so no clock is read at runtime.

``page_revisions.json`` records, for each tracked source file, its SHA-256 and
the date it was last revised. ``tests/test_seo.py`` fails when a tracked file's
hash no longer matches, which forces the date to be updated through
``scripts/update_page_revisions.py`` as part of the same change. The page's
lastmod is then the latest of the dataset revision (where the page renders
dataset figures) and the revision dates of every source file it is built from.

See docs/acquisition/OA-006_EVIDENCE.md (item F) for the rule and its limits.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "page_revisions.json"

# Every source file whose content reaches a public, sitemap-listed page.
TRACKED_SOURCES: tuple[str, ...] = (
    "index.html",
    "templates/seo_base.html",
    "templates/seo_index.html",
    "templates/seo_pillar_k7.html",
    "templates/seo_component_hub.html",
    "templates/seo_make.html",
    "templates/seo_model.html",
    "templates/seo_compare.html",
    "templates/seo_model_age.html",
    "templates/seo_component.html",
    "static/terms.html",
    "static/privacy.html",
    "static/guides/mot-checklist.html",
    "static/guides/common-mot-failures.html",
    "static/guides/when-is-mot-due.html",
    "static/guides/mot-failure-rates-by-car.html",
    "static/guides/mot-rules-2026.html",
    "static/guides/mot-defect-categories.html",
    "static/guides/mot-cost.html",
    "static/guides/mot-history-check.html",
    "static/guides/first-mot-guide.html",
)


def sha256_of(relpath: str) -> str:
    return hashlib.sha256((REPO_ROOT / relpath).read_bytes()).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_MANIFEST: dict | None = None


def _files() -> dict[str, dict[str, str]]:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = load_manifest()
    return _MANIFEST["files"]


def source_revision(*relpaths: str) -> str:
    """Latest recorded revision date (ISO yyyy-mm-dd) across the given source files."""
    files = _files()
    dates = []
    for relpath in relpaths:
        try:
            dates.append(files[relpath]["revised"])
        except KeyError as exc:
            raise KeyError(f"{relpath} is not tracked in {MANIFEST_PATH.name}") from exc
    return max(dates)


def page_lastmod(*relpaths: str, dataset_revision: str | None = None) -> str:
    """lastmod for a page built from ``relpaths`` and, optionally, the dataset artifact."""
    candidates = [source_revision(*relpaths)]
    if dataset_revision is not None:
        candidates.append(dataset_revision)
    return max(candidates)


def stale_sources(manifest: dict | None = None) -> list[tuple[str, str]]:
    """Tracked files whose current hash differs from the manifest, as (path, reason)."""
    files = (manifest or load_manifest())["files"]
    stale = []
    for relpath in TRACKED_SOURCES:
        entry = files.get(relpath)
        if entry is None:
            stale.append((relpath, "missing from manifest"))
            continue
        current = sha256_of(relpath)
        if current != entry.get("sha256"):
            stale.append((relpath, "content changed since its recorded revision"))
    for relpath in files:
        if relpath not in TRACKED_SOURCES:
            stale.append((relpath, "in manifest but not in TRACKED_SOURCES"))
    return stale
