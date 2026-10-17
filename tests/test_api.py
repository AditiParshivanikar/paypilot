"""The REST API end to end (FastAPI TestClient): demo workspaces, auth and scopes, the approval flow, webhooks,
exports and settings. Each test gets its own temp directory for databases; no network is used."""
import csv
import io
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.index import SID_COOKIE, create_app
from conftest import days_from_today
from paypilot.auth import (OWNER_COOKIE, SESSION_TTL, check_owner_password, create_api_key, hash_key,
                           issue_owner_session, resolve_api_key, valid_owner_session)
from paypilot.config import DEFAULT_POLICY, Settings
from paypilot.db import Database

PDF_EXAMPLE = "Invoice Rahul for ₹4,000 for logo design, due Friday"
PUBLIC_HOOK = "https://93.184.216.34/paypilot"  # a public IP literal: passes the SSRF guard without DNS


def invoice_args(**over):
    return {"customer": "Rahul", "amount": 4000, "currency": "INR", "description": "Logo design",
            "due_date": days_from_today(7), "rationale": "test", **over}


@pytest.fixture
def demo_app(tmp_path):
    return create_app(Settings.from_env({"PAYPILOT_DB_DIR": str(tmp_path)}))


@pytest.fixture
def client(demo_app):
    with TestClient(demo_app) as c:
        yield c


@pytest.fixture
def live_app(tmp_path):
    """Demo mode off, no open access, owner login configured (provider adapters stay on simulators: no keys)."""
    return create_app(Settings.from_env({
        "PAYPILOT_DEMO_MODE": "false", "PAYPILOT_DB": str(tmp_path / "live.db"),
        "PAYPILOT_OWNER_PASSWORD": "correct horse battery staple", "CRON_SECRET": "cron-secret-1"}))


def propose(c, **over):
    return c.post("/api/v1/actions", json={"tool": "propose_invoice", "args": invoice_args(**over)})


# ================================================================ health/demo
def test_health_sets_the_workspace_cookie(client, tmp_path):
    r = client.get("/api/v1/health")
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["demo_mode"] is True
    assert body["agent"] == "rules" and body["payments"]["label"] == "PayPal simulator"
    assert (body["business"], body["currency"]) == ("Asha Design Studio", "INR")
    sid = client.cookies.get(SID_COOKIE)
    assert sid and len(sid) == 32 and int(sid, 16) >= 0
    cookie_header = r.headers["set-cookie"].lower()
    assert "httponly" in cookie_header and "samesite=lax" in cookie_header
    assert (tmp_path / f"paypilot-demo-{sid}.db").exists()
    assert "set-cookie" not in client.get("/api/v1/health").headers  # same visitor, same workspace
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


def test_a_malformed_workspace_cookie_is_replaced(demo_app, tmp_path):
    with TestClient(demo_app) as c:
        r = c.get("/api/v1/health", headers={"Cookie": f"{SID_COOKIE}=../../etc/passwd"})
        new_sid = r.cookies.get(SID_COOKIE)
        assert new_sid and len(new_sid) == 32 and new_sid != "../../etc/passwd"
    assert not any("passwd" in p.name for p in Path(tmp_path).iterdir())


def test_two_visitors_are_isolated(demo_app):
    with TestClient(demo_app) as a, TestClient(demo_app) as b:
        assert propose(a).status_code == 201
        assert len(a.get("/api/v1/actions").json()["actions"]) == 1
        assert b.get("/api/v1/actions").json()["actions"] == []
        assert a.cookies.get(SID_COOKIE) != b.cookies.get(SID_COOKIE)
        a.post(f"/api/v1/simulate/payment/0002")
        statuses = lambda c: {i["number"]: i["status"] for i in c.get("/api/v1/invoices").json()["invoices"]}  # noqa: E731
        assert statuses(a)["0002"] == "PAID" and statuses(b)["0002"] == "SENT"


def test_demo_reset(client):
    propose(client)
    assert client.post("/api/v1/demo/reset").json() == {"ok": True}
    assert client.get("/api/v1/actions").json()["actions"] == []
    assert len(client.get("/api/v1/invoices").json()["invoices"]) == 5  # fresh demo data


# ============================================================== approval flow
def test_pdf_example_chat_then_approve(client):
    r = client.post("/api/v1/chat", json={"message": PDF_EXAMPLE, "history": []})
    body = r.json()
    assert r.status_code == 200 and "Approval inbox" in body["text"] and body["tainted"] is False
    [action] = body["actions"]
    assert action["status"] == "pending" and action["source"] == "chat"
    assert action["params"]["customer_email"] == "rahul.mehta@example.com" and action["params"]["amount"] == 4000.0
    out = client.post(f"/api/v1/actions/{action['id']}/approve").json()
    assert out["ok"] and out["action"]["status"] == "executed"
    invoices = client.get("/api/v1/invoices").json()["invoices"]
    assert any(i["description"] == "Logo design" and i["status"] == "SENT" for i in invoices)
    [created] = client.get("/api/v1/events", params={"type": "invoice.created"}).json()["events"]
    assert created["data"]["invoice"]["customer_name"] == "Rahul Mehta"
    second = client.post(f"/api/v1/actions/{action['id']}/approve")
    assert second.status_code == 409 and "not pending" in second.json()["detail"]
    assert client.post("/api/v1/actions/9999/approve").status_code == 404


def test_reject_and_status_filter(client):
    action_id = propose(client).json()["action_id"]
    assert client.post(f"/api/v1/actions/{action_id}/reject").json()["action"]["status"] == "rejected"
    assert [a["id"] for a in client.get("/api/v1/actions", params={"status": "rejected"}).json()["actions"]] == [action_id]
    assert client.get("/api/v1/actions", params={"status": "bogus"}).status_code == 422


def test_blocked_proposal_is_422(client):
    r = propose(client, amount=90000)
    assert r.status_code == 422
    assert r.json()["detail"]["status"] == "blocked"
    assert any("exceeds the invoice cap" in reason for reason in r.json()["detail"]["reasons"])
    assert client.get("/api/v1/actions").json()["actions"] == []
    assert client.post("/api/v1/actions", json={"tool": "execute_refund", "args": {}}).status_code == 422


def test_invoices_summary_and_security_flags(client):
    body = client.get("/api/v1/invoices").json()
    flags = {i["number"]: i["security_flags"] for i in body["invoices"]}
    assert flags["0005"] and all(not flags[n] for n in ("0001", "0002", "0003", "0004"))
    assert body["outstanding_totals"] == {"INR": 17500.0} and body["collected_totals"] == {"INR": 14500.0}
    assert body["overdue_count"] == 1 and {r["number"] for r in body["unpaid"]} == {"0002", "0003", "0005"}
    assert next(i for i in body["invoices"] if i["number"] == "0002")["outstanding"] == 3500.0


# ================================================================== API keys
def test_api_key_can_propose_but_not_approve_or_change_policy(client):
    created = client.post("/api/v1/keys", json={"name": "zapier"})
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("pp_") and created.json()["scopes"] == ["read", "propose"]
    assert "key" not in client.get("/api/v1/keys").json()["keys"][0]  # shown once
    headers = {"X-API-Key": key}
    r = client.post("/api/v1/actions", json={"tool": "propose_invoice", "args": invoice_args()}, headers=headers)
    assert r.status_code == 201 and r.json()["action"]["source"] == "api:zapier"
    assert client.post(f"/api/v1/actions/{r.json()['action_id']}/approve", headers=headers).status_code == 403
    assert client.put("/api/v1/policy", json=DEFAULT_POLICY, headers=headers).status_code == 403
    assert client.post("/api/v1/keys", json={"name": "x"}, headers=headers).status_code == 403
    assert client.get("/api/v1/invoices", headers=headers).status_code == 200
    assert client.get("/api/v1/session", headers=headers).json()["scopes"] == ["propose", "read"]


def test_bad_or_revoked_keys_are_401(client):
    for bad in ("nope", "pp_1234abcd_" + "x" * 30, ""):
        assert client.get("/api/v1/invoices", headers={"X-API-Key": bad}).status_code == 401
    made = client.post("/api/v1/keys", json={"name": "temp", "scopes": ["read"]}).json()
    assert client.get("/api/v1/invoices", headers={"X-API-Key": made["key"]}).status_code == 200
    assert client.delete(f"/api/v1/keys/{made['id']}").json() == {"ok": True}
    assert client.get("/api/v1/invoices", headers={"X-API-Key": made["key"]}).status_code == 401
    assert client.post("/api/v1/keys", json={"name": "bad name!"}).status_code == 422
    assert client.post("/api/v1/keys", json={"name": "x", "scopes": ["god"]}).status_code == 422
    admin_key = client.post("/api/v1/keys", json={"name": "ops", "scopes": ["admin"]}).json()["key"]
    assert client.put("/api/v1/policy", json=DEFAULT_POLICY, headers={"X-API-Key": admin_key}).status_code == 200


# =============================================================== open access
def test_without_open_access_everything_but_health_needs_auth(live_app):
    with TestClient(live_app) as c:
        assert c.get("/api/v1/health").status_code == 200
        assert SID_COOKIE not in c.cookies  # no demo workspaces outside demo mode
        assert c.get("/api/v1/session").json() == {"authenticated": False, "kind": "anonymous", "scopes": [],
                                                   "login_available": True, "open_access": False}
        for method, path in [("get", "/api/v1/invoices"), ("get", "/api/v1/actions"), ("post", "/api/v1/chat"),
                             ("get", "/api/v1/policy"), ("get", "/api/v1/audit"), ("get", "/api/v1/integrations"),
                             ("get", "/api/v1/exports/csv"), ("get", "/api/v1/keys"), ("post", "/api/v1/actions/1/approve"),
                             ("post", "/api/v1/simulate/payment/0002"), ("get", "/api/v1/events/dispatch")]:
            kwargs = {"json": {"message": "hi"}} if path.endswith("chat") else {}
            assert getattr(c, method)(path, **kwargs).status_code == 401, path
        assert c.post("/api/v1/demo/reset").status_code == 403


def test_owner_login(live_app):
    with TestClient(live_app) as c:
        assert c.post("/api/v1/login", json={"password": "wrong"}).status_code == 401
        r = c.post("/api/v1/login", json={"password": "correct horse battery staple"})
        assert r.status_code == 200 and OWNER_COOKIE in c.cookies
        assert "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/v1/session").json()["authenticated"] is True
        assert c.get("/api/v1/actions").status_code == 200
        assert propose(c).status_code == 201
        assert c.post("/api/v1/logout").status_code == 200
        c.cookies.clear()
        assert c.get("/api/v1/actions").status_code == 401
    with TestClient(live_app) as forged:
        forged.cookies.set(OWNER_COOKIE, f"{int(time.time()) + 3600}.{'0' * 64}")
        assert forged.get("/api/v1/actions").status_code == 401
        forged.cookies.set(OWNER_COOKIE, "not-a-token")
        assert forged.get("/api/v1/actions").status_code == 401


def test_login_unavailable_without_a_password(client):
    assert client.post("/api/v1/login", json={"password": "x"}).status_code == 404


def test_cron_dispatch_with_bearer_secret(live_app):
    with TestClient(live_app) as c:
        assert c.get("/api/v1/events/dispatch", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/api/v1/events/dispatch", headers={"Authorization": "Bearer cron-secret-1"})
        assert ok.status_code == 200 and ok.json() == {"retried": 0, "succeeded": 0}
        assert c.post("/api/v1/events/dispatch", headers={"Authorization": "Bearer cron-secret-1"}).status_code == 200


# =================================================================== webhooks
def test_simulated_payment_marks_paid_and_emits_invoice_paid(client):
    r = client.post("/api/v1/simulate/payment/0002")
    assert r.status_code == 200 and r.json()["invoice_status"] == "PAID"
    assert next(i for i in client.get("/api/v1/invoices").json()["invoices"] if i["number"] == "0002")["status"] == "PAID"
    [paid] = client.get("/api/v1/events", params={"type": "invoice.paid"}).json()["events"]
    assert paid["data"]["number"] == "0002" and paid["data"]["amount"] == 3500.0
    assert client.post("/api/v1/simulate/payment/9999").status_code == 404
    assert client.get("/api/v1/actions").json()["actions"] == []  # a payment creates no action


def test_inbound_webhook_signature_is_checked(client):
    body = json.dumps({"id": "WH-1", "event_type": "INVOICING.INVOICE.PAID", "resource": {}})
    r = client.post("/api/v1/webhooks/inbound/paypal", content=body,
                    headers={"x-paypilot-simulator-signature": "0" * 64, "content-type": "application/json"})
    assert r.status_code == 401 and r.json() == {"detail": "Signature verification failed"}
    assert client.post("/api/v1/webhooks/inbound/stripe", content=b"{}").status_code == 404
    assert client.get("/api/v1/events").json()["events"] == []


def test_subscriptions_and_deliveries(client):
    r = client.post("/api/v1/webhooks/subscriptions", json={"url": PUBLIC_HOOK, "events": ["action.proposed"],
                                                            "description": "Zapier"})
    assert r.status_code == 201 and r.json()["secret"].startswith("whsec_")
    secret, sub_id = r.json()["secret"], r.json()["id"]
    propose(client)
    [sub] = client.get("/api/v1/webhooks/subscriptions").json()["subscriptions"]
    assert sub["secret"] != secret and sub["secret"].startswith("whsec_•")  # masked
    assert [(d["attempt"], d["ok"]) for d in sub["recent_deliveries"]] == [(1, True)]  # simulate transport
    assert client.post("/api/v1/webhooks/subscriptions", json={"url": "https://169.254.169.254/x",
                                                               "events": ["invoice.paid"]}).status_code == 422
    assert client.post("/api/v1/webhooks/subscriptions", json={"url": PUBLIC_HOOK, "events": ["bogus"]}).status_code == 422
    assert client.delete(f"/api/v1/webhooks/subscriptions/{sub_id}").json() == {"ok": True}
    assert client.get("/api/v1/webhooks/subscriptions").json()["subscriptions"][0]["active"] is False
    assert client.delete("/api/v1/webhooks/subscriptions/sub_nope").status_code == 404


# =========================================================== exports/settings
def test_exports_download(client):
    r = client.get("/api/v1/exports/csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="paypilot-invoices-')
    assert len(list(csv.DictReader(io.StringIO(r.text)))) == 5
    assert len(ET.fromstring(client.get("/api/v1/exports/tally").text).findall(".//VOUCHER")) == 5
    assert json.loads(client.get("/api/v1/exports/json").text)["count"] == 5
    assert client.get("/api/v1/exports/pdf").status_code == 422


def test_policy_validation_and_reset(client):
    good = {**DEFAULT_POLICY}
    for bad in ({**good, "max_invoice_amount": -1}, {**good, "allowed_actions": ["create_invoice", "wire"]},
                {**good, "allowed_currencies": ["EURO"]}, {**good, "allowed_currencies": []},
                {**good, "surprise": True}, {**good, "max_reminders_per_invoice_per_day": 50},
                {**good, "owner_notifications": {"invoice.exploded": ["slack"]}},
                {**good, "reminder_channels": ["sms"]}, {k: v for k, v in good.items() if k != "max_refund_amount"}):
        assert client.put("/api/v1/policy", json=bad).status_code == 422, bad
    r = client.put("/api/v1/policy", json={**good, "max_invoice_amount": 3000, "allowed_currencies": ["inr", "usd"]})
    assert r.status_code == 200 and r.json()["max_invoice_amount"] == 3000 and r.json()["allowed_currencies"] == ["INR", "USD"]
    assert propose(client).status_code == 422  # ₹4,000 is now over the cap
    assert client.post("/api/v1/policy/reset").json() == DEFAULT_POLICY
    assert propose(client).status_code == 201
    assert any(e["event"] == "changed safety rules" for e in client.get("/api/v1/audit").json()["entries"])


def test_customer_validation(client):
    assert client.post("/api/v1/customers", json={"name": "Meera", "email": "not-an-email"}).status_code == 422
    assert client.post("/api/v1/customers", json={"name": "Meera", "email": "m@example.com",
                                                  "phone": "98000 00001"}).status_code == 422  # no country code
    r = client.post("/api/v1/customers", json={"name": "Meera Iyer", "email": "meera@example.com", "phone": "+91 98000-00009"})
    assert r.status_code == 200 and r.json()["phone"] == "+919800000009"
    assert any(c["name"] == "Meera Iyer" for c in client.get("/api/v1/customers").json()["customers"])
    assert client.post("/api/v1/chat", json={"message": "Invoice Meera for ₹2,000 for a flyer"}).json()["actions"]


def test_integrations_and_notifications(client):
    status = client.get("/api/v1/integrations").json()
    assert status["demo_mode"] and status["notifiers"]["slack"]["adapter"] == "simulator"
    propose(client)
    assert client.get("/api/v1/notifications").json()["notifications"][0]["recipient"] == "#payments"
    assert client.get("/api/v1/events", params={"type": "nope"}).status_code == 422


# ======================================================================= root
def test_root_json_and_redirect(client, tmp_path):
    root = client.get("/", follow_redirects=False)
    assert root.json()["name"] == "PayPilot API" and root.json()["docs"] == "/api/docs"
    assert client.get("/api/docs").status_code == 200 and client.get("/api/openapi.json").json()["info"]["title"] == "PayPilot API"
    redirecting = create_app(Settings.from_env({"PAYPILOT_DB_DIR": str(tmp_path),
                                                "PAYPILOT_FRONTEND_URL": "https://paypilot.example.com"}))
    with TestClient(redirecting) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "https://paypilot.example.com"
    insecure = create_app(Settings.from_env({"PAYPILOT_DB_DIR": str(tmp_path), "PAYPILOT_FRONTEND_URL": "http://x.example"}))
    with TestClient(insecure) as c:
        assert c.get("/", follow_redirects=False).json()["name"] == "PayPilot API"


# ======================================================================= auth
def test_api_keys_are_stored_as_hashes(db):
    made = create_api_key(db, "zapier")
    assert made["key"].startswith(made["prefix"] + "_") and len(made["prefix"]) == 11
    raw = json.dumps(db._all("SELECT * FROM api_keys"))
    assert made["key"] not in raw and hash_key(made["key"]) in raw
    assert resolve_api_key(db, made["key"])["scopes"] == ["read", "propose"]
    assert resolve_api_key(db, made["key"] + "x") is None and resolve_api_key(db, None) is None
    with pytest.raises(ValueError, match="already exists"):
        create_api_key(db, "zapier")


def test_owner_session_tokens():
    s = Settings.from_env({"PAYPILOT_OWNER_PASSWORD": "pw"})
    now = int(time.time())
    token = issue_owner_session(s, now)
    assert valid_owner_session(s, token, now) and valid_owner_session(s, token, now + SESSION_TTL - 1)
    assert not valid_owner_session(s, token, now + SESSION_TTL + 1)  # expired after 12 hours
    exp, mac = token.split(".")
    assert not valid_owner_session(s, f"{int(exp) + 100}.{mac}", now)  # extended expiry breaks the MAC
    assert not valid_owner_session(Settings.from_env({"PAYPILOT_OWNER_PASSWORD": "new pw"}), token, now)
    with_secret = Settings.from_env({"PAYPILOT_OWNER_PASSWORD": "pw", "PAYPILOT_SESSION_SECRET": "s3"})
    assert not valid_owner_session(with_secret, token, now) and valid_owner_session(with_secret, issue_owner_session(with_secret))
    assert not valid_owner_session(Settings.from_env({}), token, now)
    assert check_owner_password(s, "pw") and not check_owner_password(s, "PW") and not check_owner_password(s, None)
    assert not check_owner_password(Settings.from_env({}), "")
    assert "pw" not in repr(s) and "s3" not in repr(with_secret)


def test_existing_databases_get_the_scopes_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT "
                "NOT NULL UNIQUE, prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, last_used TEXT, "
                "revoked INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO api_keys(created_at, name, prefix, key_hash) VALUES ('t', 'old', 'pp_x', 'h')")
    con.commit()
    con.close()
    assert Database(str(path)).find_api_key("h")["scopes"] == ["read", "propose"]


def test_policy_save_without_owner_notifications_keeps_them(client):
    custom = {**DEFAULT_POLICY, "owner_notifications": {"invoice.paid": ["email"]}}
    assert client.put("/api/v1/policy", json=custom).status_code == 200
    website_save = {k: v for k, v in DEFAULT_POLICY.items() if k != "owner_notifications"}
    saved = client.put("/api/v1/policy", json={**website_save, "max_invoice_amount": 20000}).json()
    assert saved["max_invoice_amount"] == 20000 and saved["owner_notifications"] == {"invoice.paid": ["email"]}


def test_demo_webhook_to_an_unresolvable_host_is_accepted(client):
    r = client.post("/api/v1/webhooks/subscriptions", json={"url": "https://hooks.example.com/paypilot",
                                                            "events": ["invoice.paid"]})
    assert r.status_code == 201 and r.json()["secret"].startswith("whsec_")
    assert client.post("/api/v1/webhooks/subscriptions", json={"url": "https://localhost/x",
                                                               "events": ["invoice.paid"]}).status_code == 422
