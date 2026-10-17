"""PayPilot REST API (FastAPI). Also the Vercel serverless function: Vercel serves the ``app`` object.

Run locally:  uvicorn api.index:app --reload --port 8000      Docs: http://localhost:8000/api/docs

Who can do what:
* X-API-Key header -> that key's scopes (read / propose / approve / admin). An invalid key is a 401.
* Otherwise, open access (the default in demo mode) or a valid owner cookie -> every scope.
* Otherwise nothing but health, session and login.
Approving requires the 'approve' scope, which API keys do not get by default (brief, principle 1).

Demo mode: every visitor gets a private SQLite workspace, chosen by the pp_sid cookie (principle 8).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, for Vercel and uvicorn alike

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, field_validator  # noqa: E402

from paypilot import __version__  # noqa: E402
from paypilot.app_context import AppContext, build_context  # noqa: E402
from paypilot.auth import (OWNER_COOKIE, SCOPES, SESSION_TTL, check_owner_password, create_api_key,  # noqa: E402
                           effective_scopes, issue_owner_session, resolve_api_key, valid_owner_session)
from paypilot.backends import BackendError, outstanding, round_money  # noqa: E402
from paypilot.config import DEFAULT_POLICY, Settings  # noqa: E402
from paypilot.db import Database  # noqa: E402
from paypilot.events import EVENT_TYPES  # noqa: E402
from paypilot.integrations.base import safe_equal  # noqa: E402
from paypilot.policy import scan_for_injection  # noqa: E402
from paypilot.webhooks import WebhookRejected, handle_inbound  # noqa: E402

SID_COOKIE = "pp_sid"
SID_RE = re.compile(r"^[0-9a-f]{32}$")
SID_MAX_AGE = 30 * 24 * 60 * 60
ACTION_STATUSES = ("pending", "rejected", "approved", "executed", "failed")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
MASKED_SECRET = "whsec_" + "•" * 8


# ----------------------------------------------------------------------- schemas
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)


class ProposeIn(BaseModel):
    tool: Literal["propose_invoice", "propose_reminder", "propose_refund"]
    args: dict[str, Any] = Field(default_factory=dict)


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254)
    approved: bool = True
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        v = (v or "").replace(" ", "").replace("-", "") or None
        if v is not None and not E164_RE.match(v):
            raise ValueError("phone must be in E.164 format, e.g. +919800000001")
        return v


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_actions: list[Literal["create_invoice", "send_reminder", "refund"]]
    max_invoice_amount: float = Field(gt=0, le=10_000_000)
    max_refund_amount: float = Field(gt=0, le=10_000_000)
    daily_refund_limit: float = Field(gt=0, le=100_000_000)
    max_reminders_per_invoice_per_day: int = Field(ge=0, le=10)
    allowed_currencies: list[str] = Field(min_length=1, max_length=20)
    require_approved_customer: bool
    reminder_channels: list[Literal["provider", "email", "whatsapp"]]
    # Optional: when omitted (as the website does), the current owner notification settings are kept.
    owner_notifications: dict[str, list[Literal["slack", "email"]]] | None = None

    @field_validator("allowed_currencies")
    @classmethod
    def _currencies(cls, v: list[str]) -> list[str]:
        out = [c.strip().upper() for c in v]
        if bad := [c for c in out if not re.fullmatch(r"[A-Z]{3}", c)]:
            raise ValueError(f"currencies must be 3-letter ISO codes: {', '.join(bad)}")
        return list(dict.fromkeys(out))

    @field_validator("owner_notifications")
    @classmethod
    def _events(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if bad := [k for k in v if k not in EVENT_TYPES]:
            raise ValueError(f"unknown event type(s): {', '.join(bad)}")
        return v


class LoginIn(BaseModel):
    password: str = Field(max_length=512)


class SubscriptionIn(BaseModel):
    url: str = Field(max_length=2048)
    events: list[str] = Field(min_length=1, max_length=len(EVENT_TYPES) + 1)
    description: str = Field(default="", max_length=200)


class KeyIn(BaseModel):
    name: str = Field(max_length=64)
    scopes: list[str] | None = None


@dataclass
class Principal:
    kind: Literal["api_key", "owner", "open", "anonymous"]
    name: str
    scopes: set[str] = field(default_factory=set)

    @property
    def actor(self) -> str:
        """Who to record in the audit log and action sources."""
        return f"api:{self.name}" if self.kind == "api_key" else "user"


# ------------------------------------------------------------------------- app
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="PayPilot API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None, description="AI payments assistant: the AI proposes, a human approves.")
    app.state.settings = settings
    dbs: dict[str, Database] = {}
    db_lock = threading.Lock()

    if settings.frontend_url.startswith(("https://", "http://")):
        app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url.rstrip("/")], allow_credentials=True,
                           allow_methods=["*"], allow_headers=["Content-Type", "X-API-Key", "Authorization"])

    @app.middleware("http")
    async def workspace_and_headers(request: Request, call_next):
        sid, new = request.cookies.get(SID_COOKIE), False
        if settings.demo_mode and not (sid and SID_RE.match(sid)):
            sid, new = uuid.uuid4().hex, True
        request.state.sid = sid if settings.demo_mode else None
        response = await call_next(request)
        if new:
            response.set_cookie(SID_COOKIE, sid, max_age=SID_MAX_AGE, httponly=True, samesite="lax",
                                secure=request.url.scheme == "https", path="/")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/v1"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(BackendError)
    async def backend_error(request: Request, exc: BackendError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})  # messages are credential-free

    # ----------------------------------------------------------- dependencies
    def db_path(request: Request) -> str:
        if settings.demo_mode:
            return os.path.join(settings.db_dir or tempfile.gettempdir(), f"paypilot-demo-{request.state.sid}.db")
        return settings.db_path

    def get_db(request: Request) -> Database:
        path = db_path(request)
        with db_lock:
            if path not in dbs:
                dbs[path] = Database(path)
            return dbs[path]

    def get_ctx(request: Request) -> AppContext:
        return build_context(settings, db=get_db(request))

    def get_principal(request: Request, ctx: AppContext = Depends(get_ctx)) -> Principal:
        key = request.headers.get("x-api-key")
        if key is not None:
            record = resolve_api_key(ctx.db, key)
            if record is None:
                raise HTTPException(401, "Invalid or revoked API key")
            return Principal("api_key", record["name"], effective_scopes(record["scopes"]))
        if settings.open_access:
            return Principal("open", "visitor" if settings.demo_mode else "owner", set(SCOPES))
        if valid_owner_session(settings, request.cookies.get(OWNER_COOKIE)):
            return Principal("owner", "owner", set(SCOPES))
        return Principal("anonymous", "anonymous")

    def require(scope: str):
        def check(principal: Principal = Depends(get_principal)) -> Principal:
            if scope in principal.scopes:
                return principal
            if principal.kind == "anonymous":
                raise HTTPException(401, "Authentication required")
            raise HTTPException(403, f"This needs the '{scope}' scope")
        return check

    def cron_or_admin(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        auth = request.headers.get("authorization", "")
        if settings.cron_secret and auth.startswith("Bearer ") and safe_equal(auth[7:], settings.cron_secret):
            return Principal("api_key", "cron", {"admin"})
        return require("admin")(principal)

    ctx_dep, read, propose, approve, admin = (Depends(get_ctx), Depends(require("read")), Depends(require("propose")),
                                              Depends(require("approve")), Depends(require("admin")))

    def is_https(request: Request) -> bool:
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    # ------------------------------------------------------------------ root
    @app.get("/", include_in_schema=False)
    def root():
        if settings.frontend_url.startswith("https://"):
            return RedirectResponse(settings.frontend_url, status_code=307)
        return {"name": "PayPilot API", "version": __version__, "docs": "/api/docs",
                "openapi": "/api/openapi.json", "health": "/api/v1/health"}

    # ---------------------------------------------------------- health/auth
    @app.get("/api/v1/health")
    def health(ctx: AppContext = ctx_dep):
        return {"ok": True, "version": __version__, "demo_mode": settings.demo_mode, "agent": ctx.agent.name,
                "payments": ctx.hub.payments.status(), "business": settings.business_name,
                "currency": settings.currency}

    @app.get("/api/v1/session")
    def session(principal: Principal = Depends(get_principal)):
        return {"authenticated": principal.kind != "anonymous", "kind": principal.kind,
                "scopes": sorted(principal.scopes), "login_available": bool(settings.owner_password),
                "open_access": settings.open_access}

    @app.post("/api/v1/login")
    def login(body: LoginIn, request: Request, response: Response, ctx: AppContext = ctx_dep):
        if not settings.owner_password:
            raise HTTPException(404, "Owner login is not configured")
        if not check_owner_password(settings, body.password):
            ctx.db.log("system", "owner login failed")
            raise HTTPException(401, "Wrong password")
        response.set_cookie(OWNER_COOKIE, issue_owner_session(settings), max_age=SESSION_TTL, httponly=True,
                            samesite="lax", secure=is_https(request), path="/")
        ctx.db.log("user", "owner logged in")
        return {"ok": True, "scopes": list(SCOPES)}

    @app.post("/api/v1/logout")
    def logout(response: Response):
        response.delete_cookie(OWNER_COOKIE, path="/")
        return {"ok": True}

    # -------------------------------------------------------------- the core
    @app.get("/api/v1/integrations", dependencies=[read])
    def integrations(ctx: AppContext = ctx_dep):
        return ctx.hub.status()

    @app.post("/api/v1/chat", dependencies=[propose])
    def chat(body: ChatIn, ctx: AppContext = ctx_dep):
        reply = ctx.agent.respond(body.message, [t.model_dump() for t in body.history])
        ids = [c["result"]["action_id"] for c in reply.tool_calls
               if isinstance(c.get("result"), dict) and c["result"].get("action_id")]
        return {"text": reply.text, "table": reply.table, "tainted": reply.tainted,
                "actions": [ctx.db.get_action(i) for i in ids]}

    @app.get("/api/v1/actions", dependencies=[read])
    def list_actions(status: Literal[ACTION_STATUSES] | None = None, ctx: AppContext = ctx_dep):  # type: ignore[valid-type]
        return {"actions": ctx.db.actions(status)}

    @app.post("/api/v1/actions", status_code=201)
    def create_action(body: ProposeIn, principal: Principal = propose, ctx: AppContext = ctx_dep):
        source = principal.actor if principal.kind == "api_key" else "ui"
        res = ctx.service.propose(body.tool, body.args, source=source)
        if res["status"] == "blocked":
            raise HTTPException(422, {"status": "blocked", "reasons": res["reasons"]})
        if res["status"] != "awaiting_user_approval":
            raise HTTPException(400, res.get("error", "Could not propose this action"))
        return {**res, "action": ctx.db.get_action(res["action_id"])}

    def decide(verb: str, action_id: int, principal: Principal, ctx: AppContext):
        try:
            if verb == "approve":
                return ctx.service.approve(action_id, by=principal.actor)
            return {"ok": True, "action": ctx.service.reject(action_id, by=principal.actor)}
        except ValueError as e:
            raise HTTPException(404 if "does not exist" in str(e) else 409, str(e)) from None

    @app.post("/api/v1/actions/{action_id}/approve")
    def approve_action(action_id: int, principal: Principal = approve, ctx: AppContext = ctx_dep):
        return decide("approve", action_id, principal, ctx)

    @app.post("/api/v1/actions/{action_id}/reject")
    def reject_action(action_id: int, principal: Principal = approve, ctx: AppContext = ctx_dep):
        return decide("reject", action_id, principal, ctx)

    @app.get("/api/v1/invoices", dependencies=[read])
    def invoices(ctx: AppContext = ctx_dep):
        rows, collected, outstanding_totals = [], {}, {}
        for inv in ctx.service.backend.list_invoices():
            flags = scan_for_injection(inv.get("note")) + scan_for_injection(inv.get("description"))
            rows.append({**inv, "outstanding": outstanding(inv), "security_flags": flags})
            collected[inv["currency"]] = round_money(collected.get(inv["currency"], 0) + inv["paid_amount"]
                                                     - inv["refunded_amount"])
        unpaid = ctx.service.unpaid()
        for r in unpaid:
            outstanding_totals[r["currency"]] = round_money(outstanding_totals.get(r["currency"], 0) + r["outstanding"])
        return {"invoices": rows, "unpaid": unpaid, "outstanding_totals": outstanding_totals,
                "collected_totals": collected, "overdue_count": sum(r["overdue"] for r in unpaid)}

    # ------------------------------------------------------------- registry
    @app.get("/api/v1/customers", dependencies=[read])
    def customers(ctx: AppContext = ctx_dep):
        return {"customers": ctx.db.customers()}

    @app.post("/api/v1/customers", dependencies=[admin])
    def upsert_customer(body: CustomerIn, ctx: AppContext = ctx_dep):
        customer = ctx.db.upsert_customer(body.name, body.email, body.approved, body.phone)
        ctx.db.log("user", "updated customer", {"name": customer["name"], "approved": customer["approved"]})
        return customer

    @app.get("/api/v1/policy", dependencies=[read])
    def get_policy(ctx: AppContext = ctx_dep):
        return ctx.db.get_policy()

    @app.put("/api/v1/policy", dependencies=[admin])
    def put_policy(body: PolicyIn, ctx: AppContext = ctx_dep):
        old = ctx.db.get_policy()
        new = body.model_dump()
        if new["owner_notifications"] is None:
            new["owner_notifications"] = old.get("owner_notifications", DEFAULT_POLICY["owner_notifications"])
        ctx.db.set_setting("policy", new)
        ctx.db.log("user", "changed safety rules", {"old": old, "new": new})
        return ctx.db.get_policy()

    @app.post("/api/v1/policy/reset", dependencies=[admin])
    def reset_policy(ctx: AppContext = ctx_dep):
        ctx.db.set_setting("policy", DEFAULT_POLICY)
        ctx.db.log("user", "reset safety rules to defaults")
        return ctx.db.get_policy()

    # --------------------------------------------------------- observability
    @app.get("/api/v1/audit", dependencies=[read])
    def audit(limit: int = Query(200, ge=1, le=1000), ctx: AppContext = ctx_dep):
        return {"entries": ctx.db.audit(limit)}

    @app.get("/api/v1/events", dependencies=[read])
    def events(type: str | None = None, limit: int = Query(200, ge=1, le=1000), ctx: AppContext = ctx_dep):
        if type is not None and type not in EVENT_TYPES:
            raise HTTPException(422, f"Unknown event type {type!r}")
        return {"events": ctx.db.events(limit, type)}

    def dispatch(ctx: AppContext = ctx_dep):
        """Retry failed outbound webhook deliveries. Vercel Cron calls GET with 'Authorization: Bearer CRON_SECRET'."""
        return ctx.hub.events.retry_failed()

    for method in ("GET", "POST"):
        app.add_api_route("/api/v1/events/dispatch", dispatch, methods=[method], dependencies=[Depends(cron_or_admin)],
                          operation_id=f"dispatch_events_{method.lower()}", summary="Retry failed webhook deliveries")

    @app.get("/api/v1/notifications", dependencies=[read])
    def notifications(limit: int = Query(200, ge=1, le=1000), ctx: AppContext = ctx_dep):
        return {"notifications": ctx.db.notifications(limit)}

    @app.get("/api/v1/exports/{fmt}", dependencies=[read])
    def export(fmt: Literal["csv", "json", "tally"], ctx: AppContext = ctx_dep):
        exporter = ctx.hub.exporters[fmt]
        content = exporter.export(ctx.service.backend.list_invoices(), settings.business_name)
        filename = f"paypilot-invoices-{date.today().isoformat()}.{exporter.extension}"
        return Response(content, media_type=exporter.mime,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    # ------------------------------------------------------- outbound webhooks
    @app.get("/api/v1/webhooks/subscriptions", dependencies=[read])
    def list_subscriptions(ctx: AppContext = ctx_dep):
        return {"subscriptions": [{**s, "secret": MASKED_SECRET,
                                   "recent_deliveries": ctx.db.deliveries(subscription_id=s["id"])[-10:]}
                                  for s in ctx.db.subscriptions()]}

    @app.post("/api/v1/webhooks/subscriptions", status_code=201, dependencies=[admin])
    def create_subscription(body: SubscriptionIn, ctx: AppContext = ctx_dep):
        try:
            return ctx.hub.events.subscribe(body.url, body.events, body.description)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.delete("/api/v1/webhooks/subscriptions/{sub_id}", dependencies=[admin])
    def delete_subscription(sub_id: str, ctx: AppContext = ctx_dep):
        if not ctx.hub.events.unsubscribe(sub_id):
            raise HTTPException(404, "No such subscription")
        return {"ok": True}

    # -------------------------------------------------------- inbound webhooks
    @app.post("/api/v1/webhooks/inbound/{provider}")
    async def inbound(provider: str, request: Request, ctx: AppContext = ctx_dep):
        body = await request.body()
        try:
            return handle_inbound(ctx.hub, provider, request.headers, body)
        except WebhookRejected as e:
            return JSONResponse(status_code=e.status, content={"detail": e.message})

    @app.post("/api/v1/simulate/payment/{invoice_ref}", dependencies=[approve])
    def simulate_payment(invoice_ref: str, ctx: AppContext = ctx_dep):
        backend = ctx.hub.payments
        if not backend.simulated:
            raise HTTPException(409, "Simulated payments are only available with the payment simulator")
        try:
            headers, body = backend.simulate_webhook(ctx.service.resolve_invoice_id(invoice_ref))
        except BackendError:
            raise HTTPException(404, f"Invoice {invoice_ref!r} not found") from None
        try:
            return handle_inbound(ctx.hub, backend.provider, headers, body)
        except WebhookRejected as e:
            raise HTTPException(e.status, e.message) from None

    # ---------------------------------------------------------------- API keys
    @app.get("/api/v1/keys", dependencies=[admin])
    def list_keys(ctx: AppContext = ctx_dep):
        return {"keys": ctx.db.api_keys()}

    @app.post("/api/v1/keys", status_code=201, dependencies=[admin])
    def new_key(body: KeyIn, ctx: AppContext = ctx_dep):
        try:
            return create_api_key(ctx.db, body.name, body.scopes)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.delete("/api/v1/keys/{key_id}", dependencies=[admin])
    def revoke_key(key_id: int, ctx: AppContext = ctx_dep):
        if not ctx.db.revoke_api_key(key_id):
            raise HTTPException(404, "No such active key")
        ctx.db.log("user", "revoked API key", {"id": key_id})
        return {"ok": True}

    # -------------------------------------------------------------------- demo
    @app.post("/api/v1/demo/reset")
    def demo_reset(request: Request):
        if not settings.demo_mode:
            raise HTTPException(403, "Reset is only available in demo mode")
        path = db_path(request)
        with db_lock:
            if db := dbs.pop(path, None):
                db.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
        return {"ok": True}

    return app


app = create_app()
