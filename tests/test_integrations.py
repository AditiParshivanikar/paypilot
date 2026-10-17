"""Ports and adapters: Razorpay over faked HTTP, webhook signatures, the PayPal verify call, simulators,
provider selection, notifiers and the rule that the core never imports a vendor SDK. No network is used."""
import ast
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests

from conftest import days_from_today, inv_by_number
from paypilot.backends import (BackendError, MockBackend, SandboxBackend, make_backend, parse_paypal_event,
                               paypal_invoice_json)
from paypilot.config import DEV_SIM_WEBHOOK_SECRET, Settings
from paypilot.db import Database
from paypilot.integrations.base import (AccountingExporter, InboundEvent, Notifier, WebhookVerificationError,
                                        hmac_sha256_hex, safe_equal)
from paypilot.integrations.razorpay import (BASE_URL, RazorpayBackend, expire_by, from_paise, normalize,
                                            parse_razorpay_event, to_paise)

KEY_ID, KEY_SECRET, HOOK_SECRET = "rzp_test_KEYID123", "rzpsecret-do-not-leak", "whsec-razorpay-456"


# ================================================================ Razorpay HTTP
class FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body = status_code, body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    """Stands in for requests.Session: records every call and answers from a small in-memory Razorpay."""

    def __init__(self):
        self.calls, self.invoices, self.payments = [], {}, {}
        self.fail_with = None

    def request(self, method, url, auth=None, params=None, json=None, timeout=None):
        self.calls.append(NS(method=method, url=url, auth=auth, params=params, json=json, timeout=timeout))
        if self.fail_with:
            return self.fail_with
        path = url.removeprefix(BASE_URL)
        if (method, path) == ("POST", "/invoices"):
            inv = {"id": "inv_TEST0001", "entity": "invoice", "invoice_number": "INV-001", "status": "issued",
                   "customer_details": {"name": json["customer"]["name"], "email": json["customer"]["email"]},
                   "line_items": json["line_items"], "amount": json["line_items"][0]["amount"], "amount_paid": 0,
                   "currency": json["currency"], "expire_by": json["expire_by"], "payment_id": None,
                   "notes": json.get("notes", []), "issued_at": int(datetime.now().timestamp())}
            self.invoices[inv["id"]] = inv
            return FakeResp(200, inv)
        if (method, path) == ("GET", "/invoices"):
            return FakeResp(200, {"entity": "collection", "count": len(self.invoices), "items": list(self.invoices.values())})
        if method == "GET" and path.startswith("/invoices/"):
            inv = self.invoices.get(path.split("/")[2])
            return FakeResp(200, inv) if inv else FakeResp(400, {"error": {"code": "BAD_REQUEST_ERROR",
                                                                         "description": "The id provided does not exist"}})
        if method == "POST" and path.endswith("/notify_by/email"):
            return FakeResp(200, {"success": True})
        if method == "GET" and path.startswith("/payments/"):
            return FakeResp(200, self.payments[path.split("/")[2]])
        if method == "POST" and path.startswith("/payments/") and path.endswith("/refund"):
            return FakeResp(200, {"id": "rfnd_TEST01", "entity": "refund", "amount": json["amount"], "status": "processed"})
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def rzp():
    session = FakeSession()
    return NS(session=session, backend=RazorpayBackend(KEY_ID, KEY_SECRET, HOOK_SECRET, "Asha Design Studio",
                                                       session=session))


def paid_invoice(session, paid=1200000, refunded=0):
    session.invoices["inv_PAID0001"] = {
        "id": "inv_PAID0001", "invoice_number": "INV-009", "status": "paid", "amount": 1200000, "amount_paid": paid,
        "currency": "INR", "customer_details": {"name": "Priya Nair", "email": "priya.nair@example.com"},
        "line_items": [{"name": "Website redesign", "amount": 1200000}], "payment_id": "pay_ABC123",
        "expire_by": expire_by(days_from_today(-5)), "issued_at": 1760000000}
    session.payments["pay_ABC123"] = {"id": "pay_ABC123", "amount": paid, "amount_refunded": refunded}


def test_razorpay_create_sends_paise_basic_auth_and_expiry(rzp):
    due = days_from_today(5)
    inv = rzp.backend.create_and_send_invoice("Rahul Mehta", "rahul.mehta@example.com", 4000, "INR", "Logo design",
                                              due, "Thanks!")
    call = rzp.session.calls[0]
    assert (call.method, call.url) == ("POST", "https://api.razorpay.com/v1/invoices")
    assert call.auth == (KEY_ID, KEY_SECRET) and call.timeout
    body = call.json
    assert body["type"] == "invoice" and body["currency"] == "INR" and body["email_notify"] == 1
    assert body["customer"] == {"name": "Rahul Mehta", "email": "rahul.mehta@example.com"}
    assert body["line_items"] == [{"name": "Logo design", "amount": 400000, "currency": "INR", "quantity": 1}]  # paise
    d = datetime.fromisoformat(due)
    assert body["expire_by"] == int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    assert body["notes"] == {"note": "Thanks!"}
    assert (inv["id"], inv["status"], inv["amount"], inv["due_date"], inv["provider"]) == \
        ("inv_TEST0001", "SENT", 4000.0, due, "razorpay")


def test_paise_conversion_is_exact():
    assert to_paise(1234.56) == 123456 and to_paise("0.1") == 10 and to_paise(0.29) == 29
    assert from_paise(123456) == 1234.56 and from_paise(None) == 0.0


@pytest.mark.parametrize("raw_status,expected", [("issued", "SENT"), ("paid", "PAID"), ("partially_paid", "PARTIALLY_PAID"),
                                                 ("cancelled", "CANCELLED"), ("expired", "CANCELLED"), ("draft", "DRAFT")])
def test_razorpay_normalize_statuses(raw_status, expected):
    inv = normalize({"id": "inv_X", "status": raw_status, "amount": 350000, "amount_paid": 150000, "currency": "INR",
                     "customer_details": {"name": "Kabir Singh", "email": "kabir@cafe-kabir.example"},
                     "line_items": [{"name": "Menu card design"}], "expire_by": expire_by("2026-11-02"),
                     "notes": {"note": "hello"}})
    assert inv["status"] == expected
    assert (inv["amount"], inv["paid_amount"], inv["due_date"], inv["description"], inv["note"]) == \
        (3500.0, 1500.0, "2026-11-02", "Menu card design", "hello")
    assert set(inv) == {"id", "number", "status", "customer_name", "customer_email", "amount", "currency",
                        "description", "invoice_date", "due_date", "paid_amount", "refunded_amount", "payment_id",
                        "note", "provider"}


def test_razorpay_list_get_and_remind(rzp):
    rzp.backend.create_and_send_invoice("Rahul Mehta", "rahul.mehta@example.com", 4000, "INR", "Logo", days_from_today(5))
    assert [i["id"] for i in rzp.backend.list_invoices()] == ["inv_TEST0001"]
    listing = rzp.session.calls[-1]
    assert (listing.method, listing.url, listing.params) == ("GET", f"{BASE_URL}/invoices", {"type": "invoice", "count": 100})
    assert rzp.backend.get_invoice("inv_TEST0001")["number"] == "INV-001"
    assert rzp.backend.send_reminder("inv_TEST0001", "note")["reminded"]
    assert (rzp.session.calls[-1].method, rzp.session.calls[-1].url) == \
        ("POST", f"{BASE_URL}/invoices/inv_TEST0001/notify_by/email")


def test_razorpay_get_fills_refunds_from_the_payment(rzp):
    paid_invoice(rzp.session, refunded=50000)
    inv = rzp.backend.get_invoice("inv_PAID0001")
    assert (inv["paid_amount"], inv["refunded_amount"], inv["status"]) == (12000.0, 500.0, "PARTIALLY_REFUNDED")


def test_razorpay_refund_url_and_amount_in_paise(rzp):
    paid_invoice(rzp.session)
    res = rzp.backend.refund("inv_PAID0001", 2000, "project cut short")
    call = rzp.session.calls[-1]
    assert (call.method, call.url) == ("POST", f"{BASE_URL}/payments/pay_ABC123/refund")
    assert call.json == {"amount": 200000, "notes": {"reason": "project cut short"}}
    assert res == {"invoice_id": "inv_PAID0001", "refund_id": "rfnd_TEST01", "amount": 2000.0, "status": "processed",
                   "to": "original payer"}
    n = len(rzp.session.calls)
    with pytest.raises(BackendError, match="refundable balance"):
        rzp.backend.refund("inv_PAID0001", 12000.01, "too much")
    assert not any(c.url.endswith("/refund") for c in rzp.session.calls[n:])  # refused before calling Razorpay


def test_razorpay_errors_are_readable_and_scrubbed(rzp):
    rzp.session.fail_with = FakeResp(401, {"error": {"code": "BAD_REQUEST_ERROR",
                                                     "description": f"Authentication failed for {KEY_ID}"}})
    with pytest.raises(BackendError) as e:
        rzp.backend.list_invoices()
    assert "HTTP 401" in str(e.value) and "Authentication failed" in str(e.value)
    assert KEY_ID not in str(e.value) and KEY_SECRET not in str(e.value)

    class Down:
        def request(self, *a, **kw):
            raise requests.ConnectionError(f"cannot reach host with {KEY_SECRET}")
    with pytest.raises(BackendError) as e:
        RazorpayBackend(KEY_ID, KEY_SECRET, HOOK_SECRET, "x", session=Down()).list_invoices()
    assert "ConnectionError" in str(e.value) and KEY_SECRET not in str(e.value)
    assert KEY_SECRET not in repr(rzp.backend) and HOOK_SECRET not in repr(rzp.backend)
    with pytest.raises(BackendError, match="cannot be marked as paid"):
        rzp.backend.record_payment("inv_X")


# =============================================================== webhooks
def razorpay_paid_payload(invoice_id="inv_PAID0001", amount=1200000):
    return {"entity": "event", "event": "invoice.paid", "created_at": 1760000000, "payload": {
        "payment": {"entity": {"id": "pay_NEW1", "amount": amount, "currency": "INR", "invoice_id": invoice_id}},
        "invoice": {"entity": {"id": invoice_id, "status": "paid", "amount_paid": amount, "currency": "INR"}}}}


def test_razorpay_webhook_signatures(rzp):
    body = json.dumps(razorpay_paid_payload()).encode()
    good = hmac.new(HOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    rzp.backend.verify_webhook({"X-Razorpay-Signature": good}, body)  # header name is case-insensitive
    for headers, raw in [({"x-razorpay-signature": "0" * 64}, body), ({}, body),
                         ({"x-razorpay-signature": good}, body.replace(b"1200000", b"9900000"))]:
        with pytest.raises(WebhookVerificationError):
            rzp.backend.verify_webhook(headers, raw)
    with pytest.raises(WebhookVerificationError, match="RAZORPAY_WEBHOOK_SECRET is not set"):
        RazorpayBackend(KEY_ID, KEY_SECRET, "", "x", session=rzp.session).verify_webhook({"x-razorpay-signature": good}, body)


def test_parse_razorpay_events():
    e = parse_razorpay_event(razorpay_paid_payload())
    assert (e.provider, e.type, e.invoice_id, e.amount, e.raw_type) == ("razorpay", "invoice.paid", "inv_PAID0001",
                                                                        12000.0, "invoice.paid")
    assert e.extra["payment_id"] == "pay_NEW1" and e.event_id
    partial = parse_razorpay_event({**razorpay_paid_payload(amount=500000), "event": "invoice.partially_paid"})
    assert (partial.type, partial.amount) == ("invoice.paid", 5000.0)
    refund = parse_razorpay_event({"event": "refund.processed", "payload": {
        "refund": {"entity": {"id": "rfnd_1", "amount": 200000, "payment_id": "pay_NEW1"}},
        "payment": {"entity": {"id": "pay_NEW1", "invoice_id": "inv_PAID0001"}}}})
    assert (refund.type, refund.invoice_id, refund.amount, refund.extra["refund_id"]) == \
        ("invoice.refunded", "inv_PAID0001", 2000.0, "rfnd_1")
    for ev in ("invoice.cancelled", "invoice.expired"):
        assert parse_razorpay_event({"event": ev, "payload": {"invoice": {"entity": {"id": "inv_1"}}}}).type == "invoice.cancelled"
    assert parse_razorpay_event({"event": "payment.authorized", "payload": {}}).type == "ignored"


@pytest.mark.parametrize("event_type,expected", [
    ("INVOICING.INVOICE.PAID", "invoice.paid"), ("INVOICING.INVOICE.REFUNDED", "invoice.refunded"),
    ("INVOICING.INVOICE.CANCELLED", "invoice.cancelled"), ("INVOICING.INVOICE.CREATED", "ignored"),
    ("CHECKOUT.ORDER.APPROVED", "ignored"),
])
def test_parse_paypal_invoicing_events(mock, event_type, expected):
    inv = {**inv_by_number(mock, "0001")}
    e = parse_paypal_event({"id": "WH-1", "event_type": event_type, "resource": {"invoice": paypal_invoice_json(inv)}})
    assert (e.provider, e.event_id, e.type, e.invoice_id, e.raw_type) == ("paypal", "WH-1", expected, inv["id"], event_type)
    if expected == "invoice.paid":
        assert e.amount == 12000.0 and e.extra["payment_id"] == inv["payment_id"]


def test_parse_paypal_capture_refunded():
    e = parse_paypal_event({"id": "WH-2", "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {
        "id": "RF-9", "invoice_id": "INV2-AAAA-BBBB-CCCC-DDDD", "amount": {"currency_code": "INR", "value": "1500.00"}}})
    assert (e.type, e.invoice_id, e.amount, e.extra) == ("invoice.refunded", "INV2-AAAA-BBBB-CCCC-DDDD", 1500.0,
                                                         {"refund_id": "RF-9", "currency": "INR"})


# ===================================================== simulators + webhooks
def test_razorpay_simulator_webhook_round_trip(db):
    sim = MockBackend(db, provider="razorpay")
    neha = inv_by_number(sim, "0003")
    assert neha["id"].startswith("inv_") and neha["provider"] == "razorpay"
    headers, body = sim.simulate_webhook(neha["id"])
    assert headers["x-razorpay-signature"] == hmac_sha256_hex(DEV_SIM_WEBHOOK_SECRET, body)
    assert headers["x-razorpay-event-id"].startswith("evt_")
    assert sim.get_invoice(neha["id"])["status"] == "SENT"  # producing the webhook changes nothing
    sim.verify_webhook(headers, body)
    payload = json.loads(body)
    assert payload["event"] == "invoice.paid" and payload["payload"]["payment"]["entity"]["amount"] == 800000
    event = sim.parse_webhook(payload)
    assert (event.type, event.invoice_id, event.amount) == ("invoice.paid", neha["id"], 8000.0)
    after = sim.apply_inbound(event)
    assert (after["status"], after["paid_amount"]) == ("PAID", 8000.0) and after["payment_id"].startswith("pay_")
    assert sim.apply_inbound(event) == after  # idempotent per event id
    with pytest.raises(WebhookVerificationError):
        sim.verify_webhook(headers, body.replace(b"800000", b"100"))
    with pytest.raises(WebhookVerificationError):
        MockBackend(db, provider="razorpay", webhook_secret="other").verify_webhook(headers, body)


def test_paypal_simulator_webhook_round_trip(mock):
    kabir = inv_by_number(mock, "0002")
    headers, body = mock.simulate_webhook(kabir["id"])
    assert headers["x-paypilot-simulator-signature"] == hmac_sha256_hex(DEV_SIM_WEBHOOK_SECRET, body)
    mock.verify_webhook(headers, body)
    payload = json.loads(body)
    assert payload["event_type"] == "INVOICING.INVOICE.PAID" and payload["id"].startswith("WH-")
    assert SandboxBackend.normalize(payload["resource"]["invoice"])["id"] == kabir["id"]  # real PayPal shape
    event = mock.parse_webhook(payload)
    assert (event.type, event.amount) == ("invoice.paid", 3500.0)
    assert mock.apply_inbound(event)["status"] == "PAID"
    with pytest.raises(WebhookVerificationError, match="x-paypilot-simulator-signature"):
        mock.verify_webhook({"x-razorpay-signature": headers["x-paypilot-simulator-signature"]}, body)


def test_simulator_records_refunds_cancellations_and_ignores_the_rest(mock):
    priya, neha = inv_by_number(mock, "0001"), inv_by_number(mock, "0003")
    refunded = mock.apply_inbound(InboundEvent("paypal", "WH-R1", "invoice.refunded", priya["id"], 2000.0))
    assert (refunded["status"], refunded["refunded_amount"]) == ("PARTIALLY_REFUNDED", 2000.0)
    assert mock.apply_inbound(InboundEvent("paypal", "WH-C1", "invoice.cancelled", neha["id"]))["status"] == "CANCELLED"
    assert mock.apply_inbound(InboundEvent("paypal", "WH-X", "ignored", neha["id"])) is None
    assert mock.apply_inbound(InboundEvent("razorpay", "e", "invoice.paid", neha["id"])) is None  # other provider
    assert mock.apply_inbound(InboundEvent("paypal", "e", "invoice.paid", "INV2-NOPE")) is None


def test_razorpay_simulator_is_isolated_from_paypal_invoices(db, mock):
    rzp = MockBackend(db, seed=False, provider="razorpay")
    assert rzp.list_invoices() == [] and len(mock.list_invoices()) == 5
    inv = rzp.create_and_send_invoice("Rahul Mehta", "rahul.mehta@example.com", 4000, "INR", "Logo design",
                                      days_from_today(5))
    assert inv["id"].startswith("inv_") and inv["number"] == "0001" and inv["provider"] == "razorpay"
    assert [i["id"] for i in rzp.list_invoices()] == [inv["id"]]
    assert len(mock.list_invoices()) == 5 and all(i["provider"] == "paypal" for i in mock.list_invoices())
    with pytest.raises(BackendError):
        rzp.get_invoice(inv_by_number(mock, "0001")["id"])
    with pytest.raises(BackendError):
        mock.get_invoice(inv["id"])
    seeded = MockBackend(Database(":memory:"), provider="razorpay")
    assert all(i["payment_id"].startswith("pay_") for i in seeded.list_invoices() if i["status"] == "PAID")


# ============================================================ PayPal verify call
class ToolkitResp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


PAYPAL_HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert.pem",
                  "PAYPAL-TRANSMISSION-ID": "tid-1", "PAYPAL-TRANSMISSION-SIG": "sig==",
                  "PAYPAL-TRANSMISSION-TIME": "2026-10-13T10:00:00Z"}


@pytest.fixture
def paypal_verify(monkeypatch):
    import paypal_agent_toolkit.shared.paypal_client as pc

    calls, answer = [], {"status": "SUCCESS", "code": 200}

    def post(url, headers=None, json=None, data=None, auth=None):
        calls.append(NS(url=url, json=json, headers=headers or {}))
        if url.endswith("/v1/oauth2/token"):
            return ToolkitResp(200, {"access_token": "tok"})
        if answer["code"] != 200:
            return ToolkitResp(answer["code"], {"name": "INVALID_REQUEST", "message": "bad"})
        return ToolkitResp(200, {"verification_status": answer["status"]})

    monkeypatch.setattr(pc.requests, "post", post)
    return NS(calls=calls, answer=answer,
              backend=SandboxBackend("cid-1", "csecret-2", "Asha", "a@b.example", webhook_id="WH-ID-123"))


def test_paypal_webhook_verification_call(paypal_verify):
    body = json.dumps({"id": "WH-1", "event_type": "INVOICING.INVOICE.PAID", "resource": {}}).encode()
    paypal_verify.backend.verify_webhook(PAYPAL_HEADERS, body)
    call = paypal_verify.calls[-1]
    assert call.url == "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature"
    assert call.headers["Authorization"] == "Bearer tok"
    assert call.json == {"auth_algo": "SHA256withRSA", "cert_url": "https://api.sandbox.paypal.com/cert.pem",
                         "transmission_id": "tid-1", "transmission_sig": "sig==",
                         "transmission_time": "2026-10-13T10:00:00Z", "webhook_id": "WH-ID-123",
                         "webhook_event": json.loads(body)}
    paypal_verify.answer["status"] = "FAILURE"
    with pytest.raises(WebhookVerificationError, match="FAILURE"):
        paypal_verify.backend.verify_webhook(PAYPAL_HEADERS, body)
    paypal_verify.answer["code"] = 400
    with pytest.raises(WebhookVerificationError) as e:
        paypal_verify.backend.verify_webhook(PAYPAL_HEADERS, body)
    assert "csecret-2" not in str(e.value)


def test_paypal_webhook_verification_preconditions(paypal_verify):
    body = b'{"id": "WH-1"}'
    n = len(paypal_verify.calls)
    with pytest.raises(WebhookVerificationError, match="paypal-transmission-sig"):
        paypal_verify.backend.verify_webhook({k: v for k, v in PAYPAL_HEADERS.items() if "SIG" not in k}, body)
    with pytest.raises(WebhookVerificationError, match="not valid JSON"):
        paypal_verify.backend.verify_webhook(PAYPAL_HEADERS, b"not json")
    with pytest.raises(WebhookVerificationError, match="PAYPAL_WEBHOOK_ID is not set"):
        SandboxBackend("cid", "sec", "Asha", "a@b.example").verify_webhook(PAYPAL_HEADERS, body)
    assert len(paypal_verify.calls) == n  # none of these reached PayPal


# ================================================================ selection
def test_make_backend_uses_real_adapters_only_with_keys_and_demo_off(db):
    rzp_keys = {"RAZORPAY_KEY_ID": KEY_ID, "RAZORPAY_KEY_SECRET": KEY_SECRET}
    pp_keys = {"PAYPAL_CLIENT_ID": "cid", "PAYPAL_CLIENT_SECRET": "sec"}
    assert isinstance(make_backend(Settings.from_env({}), db), MockBackend)
    offline_rzp = make_backend(Settings.from_env({"PAYPILOT_PAYMENT_PROVIDER": "razorpay"}), db)
    assert isinstance(offline_rzp, MockBackend) and offline_rzp.provider == "razorpay"
    assert isinstance(make_backend(Settings.from_env({**rzp_keys, "PAYPILOT_PAYMENT_PROVIDER": "razorpay"}), db),
                      RazorpayBackend)
    demo = Settings.from_env({**rzp_keys, "PAYPILOT_PAYMENT_PROVIDER": "razorpay", "PAYPILOT_DEMO_MODE": "true"})
    assert isinstance(make_backend(demo, db), MockBackend)
    assert isinstance(make_backend(Settings.from_env(pp_keys), db), SandboxBackend)
    assert isinstance(make_backend(Settings.from_env(pp_keys), db, provider="razorpay"), MockBackend)  # no Razorpay keys
    fresh = Database(":memory:")
    assert make_backend(Settings.from_env({}), fresh, provider="razorpay", seed=False).list_invoices() == []
    assert len(offline_rzp.list_invoices()) == 5  # the seeded Razorpay simulator above
    with pytest.raises(ValueError, match="PAYPILOT_PAYMENT_PROVIDER"):
        Settings.from_env({"PAYPILOT_PAYMENT_PROVIDER": "stripe"})


def test_status_reports_mode(db, rzp):
    assert MockBackend(db).status() == {"provider": "paypal", "name": "mock", "simulated": True, "mode": "simulator",
                                        "label": "PayPal simulator"}
    assert rzp.backend.status()["label"] == "Razorpay test mode"


def test_settings_access_transport_and_secret_hiding():
    demo = Settings.from_env({})
    assert demo.open_access and demo.webhook_transport == "simulate"
    live = Settings.from_env({"RAZORPAY_KEY_ID": KEY_ID, "RAZORPAY_KEY_SECRET": KEY_SECRET})
    assert not live.demo_mode and not live.open_access and live.webhook_transport == "http"
    assert Settings.from_env({"PAYPILOT_OPEN_ACCESS": "yes", "RAZORPAY_KEY_ID": "k"}).open_access
    secrets = {"RAZORPAY_KEY_SECRET": KEY_SECRET, "RAZORPAY_WEBHOOK_SECRET": HOOK_SECRET, "SMTP_PASSWORD": "smtp-pw-1",
               "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/xyz", "TWILIO_AUTH_TOKEN": "tw-token-1",
               "PAYPILOT_SIM_WEBHOOK_SECRET": "sim-secret-1"}
    s = Settings.from_env({**secrets, "SMTP_PORT": "2525", "PAYPILOT_OWNER_EMAIL": "asha@studio.example"})
    assert s.smtp_port == 2525 and s.owner_email == "asha@studio.example" and s.sim_webhook_secret == "sim-secret-1"
    assert not any(v in repr(s) for v in secrets.values())


# ============================================================ notifiers etc.
def test_hmac_helpers():
    assert hmac_sha256_hex("k", b"body") == hmac.new(b"k", b"body", hashlib.sha256).hexdigest() == hmac_sha256_hex("k", "body")
    assert safe_equal("abc", "abc") and not safe_equal("abc", "abd") and not safe_equal(None, None) and not safe_equal("", "")


class FakeNotifier(Notifier):
    channel, adapter, simulated = "email", "test", False

    def __init__(self, db, fail=False):
        super().__init__(db)
        self.fail = fail

    def _deliver(self, to, subject, body):
        if self.fail:
            raise ConnectionError("SMTP server unreachable")
        return {"message_id": "m-1"}


def test_notifier_always_records_the_attempt(db):
    ok = FakeNotifier(db).send("a@b.example", "Hi", "Body")
    assert ok == {"channel": "email", "adapter": "test", "to": "a@b.example", "status": "sent", "message_id": "m-1"}
    from paypilot.integrations.base import NotificationError

    with pytest.raises(NotificationError, match="SMTP server unreachable"):  # recorded, then raised
        FakeNotifier(db, fail=True).send("a@b.example", "Hi", "Body")

    class Sim(FakeNotifier):
        simulated, adapter = True, "simulator"
    assert Sim(db).send("+91980", "Hi", "Body")["status"] == "simulated"
    rows = db.notifications()
    assert [r["status"] for r in rows] == ["simulated", "failed", "sent"]
    assert rows[2]["detail"] == {"message_id": "m-1"} and rows[1]["recipient"] == "a@b.example"
    assert FakeNotifier(db).status() == {"channel": "email", "adapter": "test", "simulated": False}


def test_accounting_exporter_is_an_abstract_port():
    with pytest.raises(TypeError):
        AccountingExporter()

    class CSV(AccountingExporter):
        format, mime, extension, label = "csv", "text/csv", "csv", "CSV"

        def export(self, invoices, business_name):
            return "number\n" + "\n".join(i["number"] for i in invoices)
    assert CSV().export([{"number": "0001"}], "Asha") == "number\n0001"


def test_old_database_gets_the_phone_column(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
                "email TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 1)")
    con.execute("INSERT INTO customers(name, email) VALUES ('Old Client', 'old@example.com')")
    con.commit()
    con.close()
    db = Database(str(path))
    assert db.find_customer("Old Client") == {"id": 1, "name": "Old Client", "email": "old@example.com",
                                              "approved": True, "phone": None}
    db.upsert_customer("Old Client", "old@example.com", phone="+919811111111")
    assert db.find_customer("old")["phone"] == "+919811111111"
    db.close()


def test_core_never_imports_a_vendor_sdk():
    vendors = {"requests", "httpx", "anthropic", "paypal_agent_toolkit", "razorpay", "twilio", "smtplib"}
    for module in ("service", "policy", "agent", "parsing"):
        tree = ast.parse((Path(__file__).resolve().parent.parent / "paypilot" / f"{module}.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
        names |= {(n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom) and n.level == 0}
        assert not names & vendors, f"paypilot/{module}.py imports {names & vendors}"
