"""The event bus: event sequence, owner notifications, signed deliveries with retries, the SSRF guard, and
customer reminders that only go out after approval. No network: DNS and HTTP are faked."""
import json
import socket
import time
from types import SimpleNamespace as NS

import pytest

from conftest import days_from_today, inv_by_number
from paypilot.app_context import build_context
from paypilot.config import Settings
from paypilot.events import EVENT_TYPES, EventBus, sign, validate_webhook_url, verify_signature
from paypilot.integrations.base import NotificationError
from paypilot.integrations.hub import IntegrationHub
from paypilot.integrations.notifiers import SlackWebhookNotifier

PUBLIC_IP = "93.184.216.34"


def resolver(mapping=None):
    """A fake getaddrinfo: hooks.example.com -> a public IP unless mapped otherwise."""
    mapping = {"hooks.example.com": [PUBLIC_IP], **(mapping or {})}

    def getaddrinfo(host, port, *args, **kw):
        if host not in mapping:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    return getaddrinfo


def invoice(**over):
    return {"customer": "Rahul", "amount": 4000, "currency": "INR", "description": "Logo design",
            "due_date": days_from_today(7), "rationale": "test", **over}


@pytest.fixture
def ctx():
    return build_context(Settings.from_env({"PAYPILOT_OWNER_EMAIL": "asha@studio.example"}), db_path=":memory:")


def types(db):
    return [e["type"] for e in reversed(db.events())]


# ===================================================================== sequence
def test_propose_then_approve_emits_the_event_sequence(ctx):
    svc = ctx.service
    res = svc.propose("propose_invoice", invoice(), source="ui")
    out = svc.approve(res["action_id"])
    assert out["ok"]
    assert types(ctx.db) == ["action.proposed", "action.approved", "invoice.created"]
    proposed, approved, created = reversed(ctx.db.events())
    assert proposed["data"]["action_id"] == res["action_id"] and proposed["data"]["source"] == "ui"
    assert proposed["data"]["preview"]["To"] == "Rahul Mehta <rahul.mehta@example.com>"
    assert approved["data"] == {"action_id": res["action_id"], "action": "create_invoice", "by": "user"}
    assert created["data"]["invoice"]["number"] == "0006" and created["id"].startswith("evt_")
    assert ctx.db.get_action(res["action_id"])["source"] == "ui"


def test_other_event_types(ctx):
    svc = ctx.service
    svc.propose("propose_invoice", invoice(amount=90000))  # blocked
    rej = svc.propose("propose_invoice", invoice())
    svc.reject(rej["action_id"])
    rem = svc.propose("propose_reminder", {"invoice_id": "0002", "note": "Hi"})
    svc.approve(rem["action_id"])
    ref = svc.propose("propose_refund", {"invoice_id": "0001", "amount": 2000, "reason": "cut short"})
    svc.approve(ref["action_id"])
    late = svc.propose("propose_invoice", invoice(description="Late"))
    ctx.db.upsert_customer("Rahul Mehta", "rahul.mehta@example.com", approved=False)
    svc.approve(late["action_id"])
    assert types(ctx.db) == [
        "action.blocked", "action.proposed", "action.rejected",
        "action.proposed", "action.approved", "reminder.sent",
        "action.proposed", "action.approved", "refund.completed",
        "action.proposed", "action.approved", "action.failed"]
    refund = ctx.db.events(type="refund.completed")[0]["data"]
    assert (refund["number"], refund["amount"], refund["currency"]) == ("0001", 2000.0, "INR")
    reminder = ctx.db.events(type="reminder.sent")[0]["data"]
    assert reminder["channel"] == "provider" and "to" not in reminder  # no customer addresses in events


def test_source_is_validated_and_recorded(ctx):
    for source in ("chat", "ui", "mcp", "api:zapier"):
        res = ctx.service.propose("propose_invoice", invoice(description=f"From {source}"), source=source)
        assert ctx.db.get_action(res["action_id"])["source"] == source
    with pytest.raises(ValueError, match="Invalid source"):
        ctx.service.propose("propose_invoice", invoice(), source="evil source")
    assert ctx.db.get_action(ctx.service.propose("propose_invoice", invoice(description="d"))["action_id"])["source"] == "chat"


def test_unknown_event_types_are_rejected(ctx):
    with pytest.raises(ValueError, match="Unknown event type"):
        ctx.hub.events.emit("invoice.exploded", {})
    with pytest.raises(ValueError, match="Unknown event type"):
        EventBus(ctx.db, ctx.hub, ctx.settings, resolver=resolver()).subscribe("https://hooks.example.com/x", ["nope"])
    with pytest.raises(ValueError, match="at least one"):
        EventBus(ctx.db, ctx.hub, ctx.settings, resolver=resolver()).subscribe("https://hooks.example.com/x", [])
    assert len(EVENT_TYPES) == 11 and ctx.db.events() == []


# ============================================================ owner notifications
def test_owner_gets_a_slack_notification(ctx):
    res = ctx.service.propose("propose_invoice", invoice())
    [slack] = ctx.db.notifications()
    assert (slack["channel"], slack["recipient"], slack["status"]) == ("slack", "#payments", "simulated")
    assert slack["subject"] == "Waiting for your approval" and "Rahul Mehta" in slack["body"]
    ref = ctx.service.propose("propose_refund", {"invoice_id": "0001", "amount": 2000, "reason": "cut short"})
    ctx.service.approve(ref["action_id"])  # refund.completed -> slack + email to the owner
    latest = ctx.db.notifications()[:2]
    assert {(n["channel"], n["recipient"], n["subject"]) for n in latest} == \
        {("slack", "#payments", "Refund completed"), ("email", "asha@studio.example", "Refund completed")}
    assert res["status"] == "awaiting_user_approval"


def test_owner_email_is_skipped_without_an_address():
    ctx = build_context(Settings.from_env({}), db_path=":memory:")
    ref = ctx.service.propose("propose_refund", {"invoice_id": "0001", "amount": 2000, "reason": "x"})
    ctx.service.approve(ref["action_id"])
    assert not any(n["channel"] == "email" for n in ctx.db.notifications())
    assert any(e["event"] == "owner email notification skipped" for e in ctx.db.audit())


def test_a_failing_owner_notification_never_breaks_the_flow(ctx):
    broken = NS(post=lambda *a, **kw: NS(status_code=500, content=b""))
    ctx.hub.notifiers["slack"] = SlackWebhookNotifier(ctx.db, "https://hooks.slack.com/services/T/B/secretpath",
                                                      session=broken)
    res = ctx.service.propose("propose_invoice", invoice())
    assert res["status"] == "awaiting_user_approval" and ctx.service.approve(res["action_id"])["ok"]
    assert ctx.db.notifications()[-1]["status"] == "failed"
    failures = [e for e in ctx.db.audit() if e["event"] == "owner notification failed"]
    assert failures and "secretpath" not in json.dumps(failures)


# ======================================================================= signing
def test_signature_verifies_with_the_right_secret_only():
    body = '{"id":"evt_1","type":"invoice.paid"}'
    now = int(time.time())
    header = sign("whsec_right", body, now)
    assert header.startswith(f"t={now},v1=") and len(header.split("v1=")[1]) == 64
    assert verify_signature("whsec_right", header, body)
    assert verify_signature("whsec_right", header, body.encode())  # bytes or str
    assert not verify_signature("whsec_wrong", header, body)
    assert not verify_signature("whsec_right", header, body.replace("paid", "refunded"))  # tampered
    assert not verify_signature("whsec_right", sign("whsec_right", body, now - 301), body)  # stale
    assert verify_signature("whsec_right", sign("whsec_right", body, now - 200), body)
    assert not verify_signature("whsec_right", sign("whsec_right", body, now + 400), body)  # from the future
    assert verify_signature("whsec_right", sign("whsec_right", body, now - 600), body, tolerance=900)
    rotated = f"t={now},v1={'0' * 64},v1={header.split('v1=')[1]}"
    assert verify_signature("whsec_right", rotated, body)
    for bad in (None, "", "garbage", "t=abc,v1=x", f"v1={header.split('v1=')[1]}"):
        assert not verify_signature("whsec_right", bad, body)


# ===================================================================== deliveries
class FakeSession:
    def __init__(self, *codes):
        self.codes, self.calls = list(codes), []

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(NS(url=url, data=data, headers=headers, timeout=timeout, allow_redirects=allow_redirects))
        code = self.codes.pop(0) if self.codes else 200
        if isinstance(code, Exception):
            raise code
        return NS(status_code=code)


def live_bus(db, session, **settings):
    """An event bus in 'http' transport (demo mode off) with faked DNS."""
    return EventBus(db, None, Settings(demo_mode_override=False, **settings), session=session, resolver=resolver())


def test_delivery_is_signed_and_a_failed_one_succeeds_on_retry(db):
    session = FakeSession(500, 200)
    bus = live_bus(db, session)
    sub = bus.subscribe("https://hooks.example.com/paypilot", ["invoice.paid", "refund.completed"], "Zapier")
    assert sub["secret"].startswith("whsec_") and sub["active"] is True
    assert "secret" not in db.subscriptions()[0]  # shown once, never listed
    event = bus.emit("invoice.paid", {"invoice_id": "INV2-X", "amount": 3500.0})
    first = session.calls[0]
    assert first.url == "https://hooks.example.com/paypilot" and first.timeout == 5 and first.allow_redirects is False
    assert first.headers["PayPilot-Event"] == "invoice.paid" and first.headers["PayPilot-Delivery"].startswith("dlv_")
    assert json.loads(first.data) == {"id": event["id"], "type": "invoice.paid", "created": event["created"],
                                      "data": {"invoice_id": "INV2-X", "amount": 3500.0}}
    assert verify_signature(sub["secret"], first.headers["PayPilot-Signature"], first.data)
    assert not verify_signature("whsec_wrong", first.headers["PayPilot-Signature"], first.data)
    assert [(d["attempt"], d["ok"], d["status_code"]) for d in db.deliveries()] == [(1, False, 500)]
    assert bus.retry_failed() == {"retried": 1, "succeeded": 1}
    assert [(d["attempt"], d["ok"], d["status_code"]) for d in db.deliveries()] == [(1, False, 500), (2, True, 200)]
    assert bus.retry_failed() == {"retried": 0, "succeeded": 0}  # nothing left to retry


def test_retries_stop_after_five_attempts_and_redirects_are_not_followed(db):
    session = FakeSession(*([503] * 10))
    bus = live_bus(db, session)
    everything = bus.subscribe("https://hooks.example.com/x", ["*"])
    bus.emit("invoice.created", {"invoice": {"number": "0006"}})
    for _ in range(6):
        bus.retry_failed()
    assert [d["attempt"] for d in db.deliveries()] == [1, 2, 3, 4, 5]
    bus.unsubscribe(everything["id"])
    redirect = live_bus(db, FakeSession(302))
    sub = redirect.subscribe("https://hooks.example.com/y", ["reminder.sent"])
    redirect.emit("reminder.sent", {"number": "0002"})
    last = db.deliveries(subscription_id=sub["id"])[-1]
    assert not last["ok"] and "redirects are not followed" in last["error"]


def test_only_matching_and_active_subscriptions_receive(db):
    session = FakeSession()
    bus = live_bus(db, session)
    paid = bus.subscribe("https://hooks.example.com/paid", ["invoice.paid"])
    gone = bus.subscribe("https://hooks.example.com/all", ["*"])
    bus.unsubscribe(gone["id"])
    bus.emit("invoice.paid", {})
    bus.emit("refund.completed", {})
    assert [c.url for c in session.calls] == ["https://hooks.example.com/paid"]
    assert [d["subscription_id"] for d in db.deliveries()] == [paid["id"]]


def test_network_errors_and_dns_rebinding_are_recorded_not_raised(db):
    bus = live_bus(db, FakeSession(ConnectionError("boom")))
    sub = bus.subscribe("https://hooks.example.com/x", ["invoice.paid"])
    bus.emit("invoice.paid", {})
    assert db.deliveries()[-1]["error"] == "ConnectionError"
    bus._resolver = resolver({"hooks.example.com": ["10.0.0.7"]})  # DNS now points inside the network
    bus.retry_failed()
    last = db.deliveries(subscription_id=sub["id"])[-1]
    assert (last["attempt"], last["ok"]) == (2, False) and last["error"].startswith("Blocked:")


def test_simulate_transport_records_success_without_network(db):
    class NoNetwork:
        def post(self, *a, **kw):
            raise AssertionError("no network in demo mode")
    bus = EventBus(db, None, Settings(), session=NoNetwork(), resolver=resolver())
    bus.subscribe("https://hooks.example.com/x", ["invoice.paid"])
    bus.emit("invoice.paid", {})
    assert [(d["attempt"], d["ok"]) for d in db.deliveries()] == [(1, True)]


# ========================================================================= SSRF
@pytest.mark.parametrize("url", [
    "http://hooks.example.com/x",            # not https
    "ftp://hooks.example.com/x",
    "https://localhost/hook",
    "https://app.localhost/hook",
    "https://127.0.0.1/hook",
    "https://10.0.0.5/hook",
    "https://192.168.1.20/hook",
    "https://172.16.0.1/hook",
    "https://169.254.169.254/latest/meta-data",  # cloud metadata
    "https://metadata.google.internal/x",
    "https://db.internal/x",
    "https://printer.local/x",
    "https://[::1]/hook",
    "https://[::ffff:127.0.0.1]/hook",
    "https://0.0.0.0/hook",
    "https://224.0.0.1/hook",                 # multicast
    "https://user:pass@hooks.example.com/x",  # credentials in the URL
    "https://sneaky.example.com/x",           # resolves to a private address
    "https://nowhere.example.com/x",          # does not resolve
    "not a url",
    "",
])
def test_ssrf_guard_rejects(url):
    with pytest.raises(ValueError):
        validate_webhook_url(url, resolver=resolver({"sneaky.example.com": [PUBLIC_IP, "10.1.2.3"]}))


def test_ssrf_guard_accepts_public_https_and_local_overrides():
    assert validate_webhook_url("https://hooks.example.com/paypilot", resolver=resolver()) == \
        "https://hooks.example.com/paypilot"
    assert validate_webhook_url(f"https://{PUBLIC_IP}:8443/x", resolver=resolver())
    with pytest.raises(ValueError):
        validate_webhook_url("http://hooks.example.com/x", resolver=resolver())
    assert validate_webhook_url("http://hooks.example.com/x", allow_http=True, resolver=resolver())
    assert validate_webhook_url("https://localhost:9000/x", allow_private=True, resolver=resolver())
    s = Settings.from_env({"PAYPILOT_ALLOW_HTTP_WEBHOOKS": "1", "PAYPILOT_ALLOW_PRIVATE_WEBHOOKS": "true"})
    assert s.allow_http_webhooks and s.allow_private_webhooks
    assert not Settings.from_env({}).allow_http_webhooks


def test_subscribe_refuses_unsafe_urls(db):
    bus = live_bus(db, FakeSession())
    with pytest.raises(ValueError):
        bus.subscribe("https://169.254.169.254/latest/meta-data", ["invoice.paid"])
    assert db.subscriptions() == []
    local = live_bus(db, FakeSession(), allow_private_webhooks=True, allow_http_webhooks=True)
    assert local.subscribe("http://localhost:8080/hook", ["invoice.paid"])["url"] == "http://localhost:8080/hook"


# ============================================================ customer reminders
def test_whatsapp_reminder_is_sent_only_after_approval_to_the_phone_on_file(ctx):
    res = ctx.service.propose("propose_reminder", {"invoice_id": "0002", "note": "Hi Kabir, a friendly reminder.",
                                                   "channel": "whatsapp"})
    whatsapp = lambda: [n for n in ctx.db.notifications() if n["channel"] == "whatsapp"]  # noqa: E731
    assert res["status"] == "awaiting_user_approval" and whatsapp() == []  # nothing sent at proposal
    ctx.db.upsert_customer("Kabir Singh", "kabir@cafe-kabir.example", approved=True, phone="+919899999999")
    out = ctx.service.approve(res["action_id"])
    assert out["ok"] and out["result"]["to"] == "+919899999999"  # the registry at execution time
    [sent] = whatsapp()
    assert (sent["recipient"], sent["body"], sent["status"]) == ("+919899999999", "Hi Kabir, a friendly reminder.",
                                                                 "simulated")
    assert types(ctx.db)[-1] == "reminder.sent"


def test_rejected_reminder_sends_nothing(ctx):
    res = ctx.service.propose("propose_reminder", {"invoice_id": "0002", "note": "Hi", "channel": "email"})
    ctx.service.reject(res["action_id"])
    assert not [n for n in ctx.db.notifications() if n["channel"] == "email"]


# =========================================================================== db
def test_receipts_and_api_keys(db):
    assert db.record_receipt("razorpay", "evt_1") is True
    assert db.record_receipt("razorpay", "evt_1") is False
    assert db.record_receipt("paypal", "evt_1") is True  # per provider
    key_id = db.add_api_key("zapier", "pp_live_ab", "hash-1")
    assert db.find_api_key("hash-1")["name"] == "zapier"
    listed = db.api_keys()[0]
    assert "key_hash" not in listed and listed["last_used"] and listed["revoked"] is False
    assert db.revoke_api_key(key_id) and not db.revoke_api_key(key_id)
    assert db.find_api_key("hash-1") is None


def test_hub_events_bus_uses_hub_settings(db):
    hub = IntegrationHub(Settings.from_env({}), db)
    assert hub.events is hub.events and hub.events.settings is hub.settings and hub.events.hub is hub
    with pytest.raises(NotificationError):
        raise NotificationError("sanity")  # the error type callers catch
    assert inv_by_number(hub.payments, "0001")["customer_name"] == "Priya Nair"


def test_demo_mode_skips_dns_but_keeps_the_other_ssrf_checks(db):
    """Simulated deliveries never leave the server, so an unresolvable demo URL is fine; unsafe ones are not."""
    demo = EventBus(db, None, Settings(), resolver=resolver())  # hooks.example.org is unknown to this resolver
    sub = demo.subscribe("https://hooks.example.org/paypilot", ["invoice.paid"])
    assert sub["url"] == "https://hooks.example.org/paypilot"
    for unsafe in ("http://hooks.example.org/x", "https://localhost/x", "https://10.0.0.1/x", "https://db.internal/x",
                   "https://169.254.169.254/latest", "https://user:pw@hooks.example.org/x"):
        with pytest.raises(ValueError):
            demo.subscribe(unsafe, ["invoice.paid"])
    live = live_bus(db, FakeSession())  # http transport: DNS is checked
    with pytest.raises(ValueError, match="could not be resolved"):
        live.subscribe("https://hooks.example.org/paypilot", ["invoice.paid"])
