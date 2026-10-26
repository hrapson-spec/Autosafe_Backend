"""FastAPI app for /api/garage/v1.

Run: uvicorn --factory garage.api.app:create_app   (env: GARAGE_DATABASE_URL, GARAGE_BASE_URL, ...)
Every private route depends on `get_scope`; owner-only routes on `require_owner`.
"""
from __future__ import annotations

import html
import logging
import os
import re
import uuid
from datetime import date
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import auth_store, errors, service
from .config import Settings
from .context import Ctx
from .db import Database
from .errors import ApiError, not_found
from .scope import Scope
from .timeutil import london_iso

log = logging.getLogger("garage.api")
PREFIX = "/api/garage/v1"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


# ------------------------------------------------------------------ models
class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _email(v: str) -> str:
    v = v.strip().lower()
    if len(v) > 254 or not EMAIL_RE.match(v):
        raise ValueError("invalid email address")
    return v


class SignupReq(_Req):
    invite_code: str = Field(max_length=64)
    workshop_name: str = Field(min_length=1, max_length=120)
    email: str

    _e = field_validator("email")(_email)

    @field_validator("workshop_name")
    @classmethod
    def _n(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v


class LoginLinkReq(_Req):
    email: str
    workshop_id: Optional[uuid.UUID] = None

    _e = field_validator("email")(_email)


class StaffReq(_Req):
    email: str

    _e = field_validator("email")(_email)


class WorkshopPatch(_Req):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class RowReq(_Req):
    registration: str = Field(max_length=20)
    date: str = Field(max_length=20)
    type: str = Field(max_length=40)
    time: Optional[str] = Field(default=None, max_length=10)     # optional appointment time, 24-hour, London


class ImportReq(_Req):
    format: Literal["paste", "csv", "manual"]
    content: Optional[str] = Field(default=None, max_length=200_000)
    rows: Optional[list[RowReq]] = Field(default=None, max_length=1000)


class BookingPatch(_Req):
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = Field(default=None, max_length=10)   # null clears it
    status: Optional[Literal["cancelled"]] = None


FeedbackReason = Literal["inaccurate_records", "not_relevant", "too_long", "missing_information", "other"]


class FeedbackReq(_Req):
    value: Literal["helpful", "not_helpful"]
    reason_code: Optional[FeedbackReason] = None
    briefing_version: Optional[str] = Field(default=None, max_length=80)


class DecisionReq(_Req):
    decision_type: Literal["check_repair_history", "offer_inspection", "adjust_booking_plan", "proceed_as_booked"]
    note: Optional[str] = Field(default=None, max_length=500)


# -------------------------------------------------------------- dependencies
def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def csrf_guard(request: Request, ctx: Ctx = Depends(get_ctx)) -> None:
    """Unsafe browser requests must be same-origin. Requests with no Origin/Sec-Fetch-Site (non-browser) pass."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    site = request.headers.get("sec-fetch-site")
    if origin is not None:
        ok = origin.rstrip("/") in ctx.settings.allowed_origins
    elif site is not None:
        ok = site in ("same-origin", "none")
    else:
        ok = True
    if not ok:
        raise ApiError(403, "csrf_blocked", "Cross-site request blocked")


def get_scope(request: Request, ctx: Ctx = Depends(get_ctx)) -> Scope:
    """THE enforced dependency: no session, no scope, no private data."""
    tok = request.cookies.get(ctx.settings.cookie_name)
    if not tok:
        raise ApiError(401, "unauthenticated", "Sign in required")
    with ctx.db.tx() as cur:
        row = auth_store.resolve_session(cur, tok)
    if row is None:
        raise ApiError(401, "unauthenticated", "Session invalid, expired or revoked")
    return Scope(workshop_id=row["workshop_id"], membership_id=row["membership_id"], user_id=row["user_id"],
                 email=row["email"], role=row["role"], session_id=row["session_id"])


def require_owner(scope: Scope = Depends(get_scope)) -> Scope:
    if not scope.is_owner:
        raise ApiError(403, "forbidden", "Only the workshop owner can do this")
    return scope


ScopeDep = Annotated[Scope, Depends(get_scope)]
OwnerDep = Annotated[Scope, Depends(require_owner)]
CtxDep = Annotated[Ctx, Depends(get_ctx)]
UuidPath = Annotated[uuid.UUID, Path()]


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width, initial-scale=1">'
           f'<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">'
           f'<title>{html.escape(title)} | AutoSafe Garage</title>'
           f'<style>body{{font:18px/1.6 system-ui,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1.5rem}}'
           f'button{{font:inherit;padding:.65rem 1rem;cursor:pointer}}</style></head>'
           f'<body><main>{body}</main></body></html>')
    return HTMLResponse(doc, status_code=status, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


def _me(ctx: Ctx, scope: Scope, workshop_name: str) -> dict:
    with ctx.db.tx() as cur:
        ms = auth_store.memberships_of_user(cur, scope.user_id)
    return {"user": {"id": scope.user_id, "email": scope.email},
            "workshop": {"id": scope.workshop_id, "name": workshop_name}, "role": scope.role,
            "membership_id": scope.membership_id,
            "memberships": [{"workshop_id": m["workshop_id"], "workshop_name": m["workshop_name"], "role": m["role"]}
                            for m in ms]}


# -------------------------------------------------------------------- routes
def build_router() -> APIRouter:
    r = APIRouter(prefix=PREFIX, dependencies=[Depends(csrf_guard)])

    @r.get("/health")
    def health(ctx: CtxDep):
        try:
            with ctx.db.tx() as cur:
                cur.execute("SELECT 1")
        except Exception:
            raise ApiError(503, "upstream_unavailable", "database unavailable")
        return {"status": "ok"}

    # ---- auth (public)
    @r.post("/auth/signup", status_code=202)
    def signup(body: SignupReq, ctx: CtxDep):
        with ctx.db.tx() as cur:
            token = auth_store.signup(cur, body.invite_code, body.workshop_name, body.email,
                                      ctx.settings.link_ttl_minutes)
        if token is None:
            raise ApiError(403, "invalid_invite", "Invite code is not valid")
        service.send_link(ctx, body.email, token, "Finish setting up AutoSafe Garage")
        return {"status": "accepted"}

    @r.post("/auth/login-links", status_code=202)
    def login_links(body: LoginLinkReq, ctx: CtxDep):
        with ctx.db.tx() as cur:
            token = auth_store.request_login_token(cur, body.email, str(body.workshop_id) if body.workshop_id else None,
                                                   ctx.settings.link_ttl_minutes, ctx.settings.login_links_per_hour)
        if token:
            service.send_link(ctx, body.email, token)
        return {"status": "accepted"}   # identical whether or not an account exists

    @r.get("/auth/links/{token}", response_class=HTMLResponse)
    def show_link(token: str, ctx: CtxDep):
        """Read-only. Mail scanners / prefetchers hitting this URL cannot burn the link."""
        ok = bool(TOKEN_RE.match(token))
        if ok:
            with ctx.db.tx() as cur:
                ok = auth_store.peek_token(cur, token)
        if not ok:
            return _page("Link not valid", "<h1>This sign-in link has expired or was already used</h1>"
                         "<p>Request a new link from the sign-in page.</p>", 410)
        return _page("Confirm sign-in",
                     "<h1>Sign in to AutoSafe Garage</h1><p>Opening this page does not sign you in. "
                     "Press the button to continue.</p>"
                     f'<form method="post" action="{PREFIX}/auth/links/{html.escape(token)}/consume">'
                     "<button type=\"submit\">Sign in</button></form>")

    @r.post("/auth/links/{token}/consume")
    def consume_link(token: str, request: Request, ctx: CtxDep):
        res = None
        if TOKEN_RE.match(token):
            with ctx.db.tx() as cur:
                res = auth_store.consume_token(cur, token, ctx.settings.session_ttl_days)
        if res is None:
            raise ApiError(410, "link_invalid", "This link has expired or was already used")
        stok, sid = res
        with ctx.db.tx() as cur:
            cur.execute(
                """SELECT s.user_id, s.workshop_id, s.membership_id, m.role, u.email, w.name AS workshop_name
                     FROM sessions s JOIN memberships m ON m.workshop_id = s.workshop_id AND m.id = s.membership_id
                     JOIN users u ON u.id = s.user_id JOIN workshops w ON w.id = s.workshop_id
                    WHERE s.id = %s""", (sid,))
            row = cur.fetchone()
        scope = Scope(row["workshop_id"], row["membership_id"], row["user_id"], row["email"], row["role"], sid)
        if "application/json" in request.headers.get("accept", ""):
            resp: Response = JSONResponse(_me(ctx, scope, row["workshop_name"]))
        else:
            resp = RedirectResponse(ctx.settings.post_signin_url, status_code=303)
        resp.set_cookie(ctx.settings.cookie_name, stok, max_age=ctx.settings.session_ttl_days * 86400,
                        httponly=True, secure=True, samesite="lax", path="/")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @r.post("/auth/logout", status_code=204)
    def logout(request: Request, ctx: CtxDep, scope: ScopeDep):
        with ctx.db.tx() as cur:
            auth_store.revoke_session_token(cur, request.cookies[ctx.settings.cookie_name])
        resp = Response(status_code=204)
        resp.delete_cookie(ctx.settings.cookie_name, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    # ---- account
    @r.get("/me")
    def me(ctx: CtxDep, scope: ScopeDep):
        with ctx.scoped(scope) as s:
            name = s.get_workshop()["name"]
        return _me(ctx, scope, name)

    @r.get("/sessions")
    def sessions(ctx: CtxDep, scope: ScopeDep):
        with ctx.scoped(scope) as s:
            rows = s.list_sessions(scope.is_owner)
        return {"items": [{"id": x["id"], "membership_id": x["membership_id"], "email": x["email"],
                           "created_at": london_iso(x["created_at"]), "last_seen_at": london_iso(x["last_seen_at"]),
                           "expires_at": london_iso(x["expires_at"]), "current": x["id"] == scope.session_id}
                          for x in rows]}

    @r.delete("/sessions/{session_id}", status_code=204)
    def revoke(session_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        with ctx.scoped(scope) as s:
            if not s.revoke_session(str(session_id), scope.is_owner):
                raise not_found("session")
        return Response(status_code=204)

    def _workshop(ctx: Ctx, scope: Scope) -> dict:
        with ctx.scoped(scope) as s:
            w = s.get_workshop()
            return {"id": w["id"], "name": w["name"], "usage": service.usage_json(ctx, s, ctx.clock())}

    @r.get("/workshop")
    def workshop(ctx: CtxDep, scope: ScopeDep):
        return _workshop(ctx, scope)

    @r.patch("/workshop")
    def patch_workshop(body: WorkshopPatch, ctx: CtxDep, scope: OwnerDep):
        if body.name is not None:
            with ctx.scoped(scope) as s:
                s.rename_workshop(body.name.strip())
        return _workshop(ctx, scope)

    @r.get("/staff")
    def staff(ctx: CtxDep, scope: ScopeDep):
        return service.list_members(ctx, scope)

    @r.post("/staff", status_code=201)
    def add_staff(body: StaffReq, ctx: CtxDep, scope: OwnerDep):
        return service.add_staff(ctx, scope, body.email)

    @r.delete("/staff/{membership_id}", status_code=204)
    def remove_staff(membership_id: UuidPath, ctx: CtxDep, scope: OwnerDep):
        service.remove_member(ctx, scope, str(membership_id))
        return Response(status_code=204)

    @r.get("/usage")
    def usage(ctx: CtxDep, scope: ScopeDep):
        with ctx.scoped(scope) as s:
            return service.usage_json(ctx, s, ctx.clock())

    # ---- imports
    @r.post("/imports/preview")
    def preview(body: ImportReq, ctx: CtxDep, scope: ScopeDep):
        rows = [x.model_dump() for x in body.rows] if body.rows is not None else None
        return service.preview_import(ctx, scope, body.format, body.content, rows)

    @r.post("/imports/{import_id}/commit", status_code=202)
    def commit(import_id: UuidPath, response: Response, ctx: CtxDep, scope: ScopeDep):
        rec, status = service.commit_import(ctx, scope, str(import_id))
        response.status_code = status
        return rec

    @r.get("/imports/{import_id}")
    def get_import(import_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.get_import(ctx, scope, str(import_id))

    # ---- bookings
    @r.get("/bookings")
    def bookings(ctx: CtxDep, scope: ScopeDep,
                 from_: Annotated[Optional[date], Query(alias="from")] = None,
                 to: Optional[date] = None,
                 booking_type: Optional[Literal["mot", "service", "retest", "service_mot"]] = None,
                 assessment_state: Optional[Literal["pending", "available", "unavailable", "unsupported",
                                                    "failed"]] = None,
                 status: Optional[Literal["active", "cancelled"]] = None,
                 limit: Annotated[int, Query(ge=1, le=200)] = 50, cursor: Optional[str] = None):
        return service.list_bookings(ctx, scope, date_from=from_, date_to=to, booking_type=booking_type,
                                     status=status, assessment_state=assessment_state, limit=limit, cursor=cursor)

    @r.post("/bookings", status_code=201)
    def create_booking(body: RowReq, response: Response, ctx: CtxDep, scope: ScopeDep):
        b, created = service.create_booking(ctx, scope, body.registration, body.date, body.type, body.time)
        if not created:
            response.status_code = 200
        return b

    @r.get("/bookings/{booking_id}")
    def booking(booking_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.get_booking(ctx, scope, str(booking_id))

    @r.patch("/bookings/{booking_id}")
    def patch_booking(booking_id: UuidPath, body: BookingPatch, ctx: CtxDep, scope: ScopeDep):
        if body.appointment_date is None and body.status is None and "appointment_time" not in body.model_fields_set:
            raise ApiError(422, "validation_error", "nothing to update")
        return service.update_booking(ctx, scope, str(booking_id), body.appointment_date, body.status == "cancelled",
                                      body.appointment_time if "appointment_time" in body.model_fields_set
                                      else service._UNSET)

    @r.get("/bookings/{booking_id}/assessments")
    def assessments(booking_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.list_assessments(ctx, scope, str(booking_id))

    @r.post("/bookings/{booking_id}/assessments", status_code=202)
    def refresh(booking_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.refresh_assessment(ctx, scope, str(booking_id))

    @r.get("/bookings/{booking_id}/briefing")
    def briefing(booking_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.get_briefing(ctx, scope, str(booking_id))

    @r.put("/bookings/{booking_id}/feedback")
    def feedback(booking_id: UuidPath, body: FeedbackReq, ctx: CtxDep, scope: ScopeDep):
        return service.put_feedback(ctx, scope, str(booking_id), body.value, body.reason_code, body.briefing_version)

    @r.get("/bookings/{booking_id}/decisions")
    def decisions(booking_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.list_decisions(ctx, scope, str(booking_id))

    @r.post("/bookings/{booking_id}/decisions", status_code=201)
    def decide(booking_id: UuidPath, body: DecisionReq, ctx: CtxDep, scope: ScopeDep):
        return service.record_decision(ctx, scope, str(booking_id), body.decision_type, body.note)

    @r.get("/bookings/{booking_id}/outcomes")
    def outcomes(booking_id: UuidPath, ctx: CtxDep, scope: ScopeDep):
        return service.list_outcomes(ctx, scope, str(booking_id))

    return r


# public routes: the only ones allowed to omit get_scope (checked by a test)
PUBLIC_PATHS = {
    f"{PREFIX}/health", f"{PREFIX}/auth/signup", f"{PREFIX}/auth/login-links",
    f"{PREFIX}/auth/links/{{token}}", f"{PREFIX}/auth/links/{{token}}/consume",
}


def create_app(ctx: Ctx | None = None) -> FastAPI:
    ctx = ctx or build_ctx_from_env()
    app = FastAPI(title="AutoSafe Garage API", version="1.0.0-draft.1", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.ctx = ctx
    errors.install(app)
    router = build_router()
    app.state.garage_router = router      # kept so tests can enumerate routes and their dependencies
    app.include_router(router)
    return app


def build_ctx_from_env() -> Ctx:
    from . import mailer
    from .dvsa import DbThrottle, FakeDvsaClient, HttpDvsaClient, ThrottledDvsa
    from .provider import StubProvider
    settings = Settings.from_env()
    db = Database(settings.database_url)
    if os.environ.get("GARAGE_AUTO_MIGRATE") == "1":
        from .db import migrate
        migrate(settings.database_url)
    inner = FakeDvsaClient() if os.environ.get("GARAGE_DVSA_BACKEND", "http") == "fake" else HttpDvsaClient.from_env()
    backend = os.environ.get("GARAGE_EMAIL_BACKEND", "resend")
    sender = {"resend": mailer.ResendEmailSender, "console": mailer.ConsoleEmailSender}[backend]()
    return Ctx(db=db, settings=settings, dvsa=ThrottledDvsa(inner, DbThrottle(db, settings.dvsa_max_rps)),
               provider=StubProvider(), mailer=sender)
