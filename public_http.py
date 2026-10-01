"""
Public HTTP niceties for crawlers and browsers (OA-006 H).

``HeadMethodMiddleware``
    FastAPI registers ``@app.get`` routes for GET only, so every public page
    answered HEAD with 405. This pure-ASGI middleware routes a HEAD request as
    GET and discards the response body while keeping the headers (including
    the GET Content-Length, which HEAD is allowed to report). Routes without a
    GET handler still answer 405, so POST-only endpoints gain nothing.

``HashedAssetFiles``
    Vite emits content-hashed filenames under static/assets (``index-DskXiKVN.js``).
    Such a file can never change behind its name, so it is safe to cache for a
    year as immutable. Files whose names do not carry the hash are served with
    Starlette's default headers.
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


def is_hashed_asset_name(filename: str) -> bool:
    return bool(HASHED_ASSET_RE.match(os.path.basename(filename)))


class HeadMethodMiddleware:
    """Answer HEAD by running the GET handler and dropping the body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "HEAD":
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
