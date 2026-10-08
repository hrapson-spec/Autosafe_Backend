"""
Significant-revision dates for public page <lastmod> values.
=============================================================

Search-engine guidance: sitemap ``<lastmod>`` is the date of the last
*significant* change to a page's main content — not boilerplate, footer,
markup, styling or analytics-script edits. Two things can significantly change
a public page here: the checked-in dataset artifact (``DATASET_ARTIFACT_REVISION``
in report_contract.py) and the main content of the source files that render it
(Jinja templates, static guide/legal HTML, the SPA shell). Neither a worker
restart nor the current date changes a page, so no clock is read at runtime.

``page_revisions.json`` records, for each tracked source file:

- ``sha256``                — hash of the file as last reviewed;
- ``significant_revision``  — date of its last significant main-content change;
- ``reason``                — what that significant change was;
- ``last_change``           — ``{date, significant, reason}`` for the most
  recent reviewed change, so non-significant edits are recorded too.

Tracked sources: the Vite shell and components actually rendered by the
homepage (listed explicitly below); seo_pages.py (Python-built
copy and link selection) plus seo_base.html and each template for the
server-rendered pages; the static guide and legal HTML files.

``tests/test_seo.py`` fails when a tracked file's hash no longer matches, which
forces the author to run ``scripts/update_page_revisions.py`` and state
explicitly whether the change was significant (``--significant "reason"``) or
not (``--non-significant "reason"``). Only significant revisions move lastmod.
A page's lastmod is the latest of the dataset revision (where the page renders
dataset figures) and the significant revisions of the sources it is built from.

See docs/acquisition/OA-006_EVIDENCE.md (item F) for the rule, the seeded
history and its limits.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "page_revisions.json"

# Keep this list aligned with HomePage and HeroForm's rendered dependencies.
# Lazy report/legal/guide routes do not render on / and must not advance its
# lastmod, even when a newly added report component receives its first revision.
HOMEPAGE_SOURCES: tuple[str, ...] = (
    "index.html",
    "App.tsx",
    "components/HeroForm.tsx",
    "components/Icons.tsx",
    "components/Logo.tsx",
    "components/ui/index.ts",
    "components/ui/Input.tsx",
    "components/ui/Button.tsx",
)


def homepage_sources() -> tuple[str, ...]:
    """Sources whose content reaches the homepage (/): the Vite shell plus the SPA body."""
    return HOMEPAGE_SOURCES


# Every source file whose content reaches a public, sitemap-listed page.
TRACKED_SOURCES: tuple[str, ...] = (
    *HOMEPAGE_SOURCES,
    "seo_pages.py",
    "templates/seo_base.html",
    "templates/seo_index.html",
    "templates/seo_pillar_k7.html",
    "templates/seo_component_hub.html",
    "templates/seo_make.html",
    "templates/seo_model.html",
    "templates/programme_model.html",
    "templates/seo_compare.html",
    "templates/pilot_corsa.html",
    "templates/pilot_c3.html",
    "templates/pilot_clio208.html",
    "templates/pilot_polofiesta.html",
    "templates/pilot_yarisjazz.html",
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

ENTRY_KEYS = ("sha256", "significant_revision", "reason", "last_change")


def sha256_of(relpath: str) -> str:
    return hashlib.sha256((REPO_ROOT / relpath).read_bytes()).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_MANIFEST: dict | None = None


def _files() -> dict[str, dict]:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = load_manifest()
    return _MANIFEST["files"]


def source_revision(*relpaths: str) -> str:
    """Latest significant revision date (ISO yyyy-mm-dd) across the given source files."""
    files = _files()
    dates = []
    for relpath in relpaths:
        try:
            dates.append(files[relpath]["significant_revision"])
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
        if sha256_of(relpath) != entry.get("sha256"):
            stale.append((relpath, "content changed since it was last reviewed"))
    for relpath in files:
        if relpath not in TRACKED_SOURCES:
            stale.append((relpath, "in manifest but not in TRACKED_SOURCES"))
    return stale
