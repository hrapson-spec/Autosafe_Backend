"""
Public HTTP niceties for crawlers and browsers (OA-006 H).

``HeadMethodMiddleware``
    FastAPI registers ``@app.get`` routes for GET only, so every public page
    answered HEAD with 405. This pure-ASGI middleware routes a HEAD request as
    GET and discards the response body while keeping the headers (including
    the GET Content-Length, which HEAD is allowed to report).

    It does so ONLY for an explicit allowlist of public, side-effect-free
    surfaces: server-rendered SEO pages, guides, legal pages, sitemaps,
    robots.txt, the SPA shell, static files and hashed assets, and the health
    probes. Everything else — in particular ``/api/*`` — is passed through
    untouched and keeps answering HEAD with 405, because some GET API handlers
    have side effects (``/api/garage/outcome/{id}?result=`` records an outcome;
    ``/api/risk*`` and ``/api/vehicle`` trigger upstream lookups).

``HashedAssetFiles``
    Vite emits content-hashed filenames under static/assets (``index-DskXiKVN.js``).
    Such a file can never change behind its name, so it is safe to cache for a
    year as immutable. Files whose names do not carry the hash are served with
    Starlette's default headers. This assumes /assets holds only Vite output.
"""
from __future__ import annotations

import os
import re

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

# Vite's default: <name>-<8-char base64url hash>.<ext>
HASHED_ASSET_RE = re.compile(r"^[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8}\.(?:js|css|png|jpg|jpeg|svg|webp|woff2?|ico|map)$")
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Paths whose GET handlers are read-only public documents. HEAD is routed as GET
# only here. Anything not matched (notably /api/...) is left alone.
HEAD_AS_GET_PATHS = re.compile(
    r"^(?:"
    r"/"                                   # SPA shell / homepage
    r"|/app(?:/.*)?"                       # SPA shell routes (incl. /app/report/<token>)
    r"|/robots\.txt|/sitemap[A-Za-z0-9_-]*\.xml|/indexnow-key\.txt"
    r"|/privacy|/terms"
    r"|/will-my-car-pass-mot/"
    r"|/health|/ready"
    r"|/(?:mot-check|guides|insights|local-mot|static|assets)/.*"
    r")$"
)


def is_hashed_asset_name(filename: str) -> bool:
    return bool(HASHED_ASSET_RE.match(os.path.basename(filename)))


def head_is_routed_as_get(path: str) -> bool:
    return bool(HEAD_AS_GET_PATHS.match(path))


class HeadMethodMiddleware:
    """Answer HEAD on allowlisted public paths by running the GET handler and dropping the body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "HEAD"
            or not head_is_routed_as_get(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        body_sent = False

        async def send_without_body(message) -> None:
            nonlocal body_sent
            if message["type"] == "http.response.body":
                if body_sent:  # later chunks of a streamed body: nothing more to say
                    return
                body_sent = True
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


class HashedAssetFiles(StaticFiles):
    """StaticFiles that marks content-hashed filenames as immutable for a year."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code=status_code)
        if is_hashed_asset_name(str(full_path)):
            response.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
        return response
