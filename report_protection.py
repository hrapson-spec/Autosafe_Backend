"""Explicit protections for bearer report routes (OA-003).

A report link is a bearer capability: whoever holds ``/app/report/<token>``
can read that report. These helpers make the response side of that model
explicit, without adding accounts or changing the sharing model:

* every response on a bearer report route (the SPA shell under
  ``/app/report`` and the JSON API under ``/api/v2/reports``) carries
  ``X-Robots-Tag: noindex, nofollow``, ``Cache-Control: no-store`` and
  ``Referrer-Policy: no-referrer``, on success and on every error path;
* the HTML shell served for ``/app/report/*`` is derived from the built
  ``static/index.html`` with the public homepage metadata (title, canonical,
  description, Open Graph, Twitter, WebSite/Organization JSON-LD and the
  homepage no-JavaScript copy) replaced by a neutral title and a robots
  noindex directive. Scripts, stylesheets and the analytics bootstrap are
  left byte-for-byte as built.

Public landing routes are untouched: nothing here runs for them.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BEARER_REPORT_HEADERS: Dict[str, str] = {
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}

NEUTRAL_REPORT_TITLE = "AutoSafe report"
ROBOTS_META = '<meta name="robots" content="noindex, nofollow" />'

_BEARER_PATH_RE = re.compile(r"^/+(?:app/report|api/v2/reports)(?:/|$)", re.IGNORECASE)


def is_bearer_report_path(path: str) -> bool:
    """True for the report SPA shell routes and the v2 report API routes.

    Case-insensitive: react-router matches client routes case-insensitively,
    so /app/Report/<token> renders the report in the browser and must receive
    the same controls as /app/report/<token>."""
    return bool(_BEARER_PATH_RE.match(path or ""))


def apply_bearer_report_headers(path: str, response) -> None:
    """Set the three protection headers on ``response`` when ``path`` is a
    bearer report route. Overrides any value already present."""
    if is_bearer_report_path(path):
        for name, value in BEARER_REPORT_HEADERS.items():
            response.headers[name] = value


# ---------------------------------------------------------------------------
# Report HTML shell
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title\b[^>]*>.*?</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(r"[ \t]*<meta\b[^>]*>[ \t]*\n?", re.IGNORECASE)
_LINK_RE = re.compile(r"[ \t]*<link\b[^>]*>[ \t]*\n?", re.IGNORECASE)
_LDJSON_RE = re.compile(
    r"[ \t]*<script\b[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>.*?</script>[ \t]*\n?",
    re.IGNORECASE | re.DOTALL,
)
_STRUCTURED_DATA_COMMENT_RE = re.compile(r"[ \t]*<!--\s*Structured Data\s*-->[ \t]*\n?", re.IGNORECASE)
_NOSCRIPT_RE = re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = r"""\b{attr}\s*=\s*["']([^"']*)["']"""

_NEUTRAL_NOSCRIPT = (
    "<noscript>"
    '<div style="max-width:800px;margin:2rem auto;padding:0 1.5rem;color:#334155;">'
    "<p>This AutoSafe report needs JavaScript to display.</p>"
    "</div></noscript>"
)


def _attr(tag: str, attr: str) -> Optional[str]:
    m = re.search(_ATTR_RE.format(attr=attr), tag, re.IGNORECASE)
    return m.group(1) if m else None


def _is_homepage_meta(tag: str) -> bool:
    name = (_attr(tag, "name") or "").lower()
    prop = (_attr(tag, "property") or "").lower()
    return (
        name in ("description", "robots")
        or name.startswith("twitter:")
        or prop.startswith("og:")
    )


def _is_canonical_link(tag: str) -> bool:
    return "canonical" in (_attr(tag, "rel") or "").lower().split()


def build_report_shell(index_html: str) -> str:
    """Return the report-route variant of the built SPA shell."""
    html = _LDJSON_RE.sub("", index_html)
    html = _STRUCTURED_DATA_COMMENT_RE.sub("", html)
    html = _META_RE.sub(lambda m: "" if _is_homepage_meta(m.group(0)) else m.group(0), html)
    html = _LINK_RE.sub(lambda m: "" if _is_canonical_link(m.group(0)) else m.group(0), html)
    html = _NOSCRIPT_RE.sub(lambda m: _NEUTRAL_NOSCRIPT, html)
    title = f"<title>{NEUTRAL_REPORT_TITLE}</title>\n    {ROBOTS_META}"
    html, n = _TITLE_RE.subn(lambda m: title, html, count=1)
    if n == 0:
        html = re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + "\n    " + title, html, count=1, flags=re.IGNORECASE)
    return html


def leftover_homepage_metadata(html: str) -> List[str]:
    """Names of homepage metadata still present in ``html`` (empty = clean).
    Used as a runtime post-condition and by tests."""
    left: List[str] = []
    if any(_is_canonical_link(t) for t in _LINK_RE.findall(html)):
        left.append("canonical")
    for tag in _META_RE.findall(html):
        name = (_attr(tag, "name") or "").lower()
        prop = (_attr(tag, "property") or "").lower()
        if name == "description":
            left.append("description")
        elif name.startswith("twitter:"):
            left.append(name)
        elif prop.startswith("og:"):
            left.append(prop)
    if _LDJSON_RE.search(html):
        left.append("ld+json")
    return left


_shell_cache: Dict[str, Tuple[Tuple[int, int], str]] = {}


def report_shell_html(index_path: str) -> Optional[str]:
    """Report-route HTML derived from the built shell at ``index_path``.

    Transformed once per (mtime, size) of the built file and cached, so a
    rebuilt bundle is picked up without a restart. Returns None when the
    built shell does not exist (tests / API-only checkouts); the caller then
    behaves as it did before.
    """
    try:
        st = os.stat(index_path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    cached = _shell_cache.get(index_path)
    if cached and cached[0] == key:
        return cached[1]
    with open(index_path, "r", encoding="utf-8") as fh:
        built = fh.read()
    html = build_report_shell(built)
    left = leftover_homepage_metadata(html)
    if left:
        logger.error("report_shell_homepage_metadata_remaining items=%s", ",".join(left))
    _shell_cache[index_path] = (key, html)
    return html
