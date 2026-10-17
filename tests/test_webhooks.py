"""Inbound webhooks: verify, de-duplicate, record the fact, emit the event, and never create or run an action."""
import ast
import json
from pathlib import Path

import pytest

from conftest import days_from_today, inv_by_number
from paypilot.app_context import build_context
from paypilot.backends import paypal_invoice_json
from paypilot.config import DEV_SIM_WEBHOOK_SECRET, Settings
from paypilot.integrations.base import hmac_sha256_hex
from paypilot.webhooks import MAX_BODY_BYTES, WebhookRejected, handle_inbound


@pytest.fixture
def ctx():
    return build_context(Settings.from_env({}), db_path=":memory:")


def signed_paypal(payload: dict) -> tuple[dict, bytes]:
    """A PayPal-simulator webhook with a valid signature over an arbitrary payload."""
    body = json.dumps(payload).encode()
    return {"x-paypilot-simulator-signature": hmac_sha256_hex(DEV_SIM_WEBHOOK_SECRET, body)}, body


def rejected(fn, status):
    with pytest.raises(WebhookRejected) as e:
        fn()
    assert e.value.status == status, e.value.message
    return e.value


def assert_no_actions(ctx):
    assert ctx.db.actions() == []  # nothing proposed, queued or executed


# ================================================================ happy path
def test_simulated_paid_webhook_marks_paid_and_emits_invoice_paid(ctx):
    kabir = inv_by_number(ctx.hub.payments, "0002")
    headers, body = ctx.hub.payments.simulate_webhook(kabir["id"])
    res = handle_inbound(ctx.hub, "paypal", headers, body)
    assert res["status"] == "processed" and res["event"] == "invoice.paid" and res["invoice_status"] == "PAID"
    inv = ctx.hub.payments.get_invoice(kabir["id"])
    assert (inv["status"], inv["paid_amount"]) == ("PAID", 3500.0)
    [event] = ctx.db.events(type="invoice.paid")
    assert event["data"] == {"provider": "paypal", "invoice_id": kabir["id"], "number": "0002",
                             "customer": "Kabir Singh", "currency": "INR", "amount": 3500.0, "status": "PAID",
                             "provider_event_id": json.loads(body)["id"]}
    assert ctx.db.notifications()[0]["subject"] == "Invoice paid"  # owner Slack ping per policy
    assert any(e["actor"] == "webhook" and e["event"] == "recorded invoice.paid" for e in ctx.db.audit())
    assert "0002" not in [r["number"] for r in ctx.service.unpaid()]
    assert_no_actions(ctx)


def test_replay_is_a_duplicate(ctx):
    neha = inv_by_number(ctx.hub.payments, "0003")
    headers, body = ctx.hub.payments.simulate_webhook(neha["id"])
    assert handle_inbound(ctx.hub, "paypal", headers, body)["status"] == "processed"
    assert handle_inbound(ctx.hub, "paypal", headers, body)["status"] == "duplicate"
    assert len(ctx.db.events(type="invoice.paid")) == 1
    assert ctx.hub.payments.get_invoice(neha["id"])["paid_amount"] == 8000.0  # not paid twice
    assert any(e["event"] == "duplicate webhook" for e in ctx.db.audit())
    assert_no_actions(ctx)


def test_razorpay_webhook_uses_the_header_event_id():
    ctx = build_context(Settings.from_env({"PAYPILOT_PAYMENT_PROVIDER": "razorpay"}), db_path=":memory:")
    rzp = ctx.hub.payments
    inv = inv_by_number(rzp, "0002")
    headers, body = rzp.simulate_webhook(inv["id"])
    res = handle_inbound(ctx.hub, "razorpay", headers, body)
    assert res["event_id"] == headers["x-razorpay-event-id"] and res["invoice_status"] == "PAID"
    assert handle_inbound(ctx.hub, "razorpay", headers, body)["status"] == "duplicate"
    assert not ctx.db.claim_receipt("razorpay", headers["x-razorpay-event-id"])
    assert_no_actions(ctx)


def test_webhook_for_the_other_provider_goes_to_its_own_adapter(ctx):
    rzp = ctx.hub.provider("razorpay")  # unseeded; payments in this workspace are PayPal
    inv = rzp.create_and_send_invoice("Rahul Mehta", "rahul.mehta@example.com", 4000, "INR", "Logo", days_from_today(5))
    headers, body = rzp.simulate_webhook(inv["id"])
    assert handle_inbound(ctx.hub, "razorpay", headers, body)["invoice_status"] == "PAID"
    assert len(ctx.hub.payments.list_invoices()) == 5  # PayPal invoices untouched


def test_refunded_and_cancelled_facts(ctx):
    priya, neha = inv_by_number(ctx.hub.payments, "0001"), inv_by_number(ctx.hub.payments, "0003")
    refunded = {**priya, "status": "PARTIALLY_REFUNDED", "refunded_amount": 2000.0}
    resource = paypal_invoice_json(refunded)
    resource["refunds"]["transactions"] = [{"amount": {"currency_code": "INR", "value": "2000.00"}}]
    res = handle_inbound(ctx.hub, "paypal", *signed_paypal(
        {"id": "WH-R1", "event_type": "INVOICING.INVOICE.REFUNDED", "resource": {"invoice": resource}}))
    assert res["invoice_status"] == "PARTIALLY_REFUNDED"
    assert ctx.db.events(type="invoice.refunded")[0]["data"]["amount"] == 2000.0
    res = handle_inbound(ctx.hub, "paypal", *signed_paypal(
        {"id": "WH-C1", "event_type": "INVOICING.INVOICE.CANCELLED", "resource": {"invoice": paypal_invoice_json(neha)}}))
    assert res["invoice_status"] == "CANCELLED" and ctx.db.events(type="invoice.cancelled")
    assert_no_actions(ctx)


# =================================================================== refused
def test_tampered_body_is_401_and_the_invoice_is_unchanged(ctx):
    kabir = inv_by_number(ctx.hub.payments, "0002")
    headers, body = ctx.hub.payments.simulate_webhook(kabir["id"])
    tampered = body.replace(b'"value":"3500.00"', b'"value":"1.00"')
    assert tampered != body
    err = rejected(lambda: handle_inbound(ctx.hub, "paypal", headers, tampered), 401)
    assert err.message == "Signature verification failed"
    assert ctx.hub.payments.get_invoice(kabir["id"]) == kabir
    assert ctx.db.events() == [] and any(e["event"] == "rejected webhook" for e in ctx.db.audit())
    assert_no_actions(ctx)


def test_missing_or_wrong_signature_is_401(ctx):
    kabir = inv_by_number(ctx.hub.payments, "0002")
    headers, body = ctx.hub.payments.simulate_webhook(kabir["id"])
    for bad in ({}, {"x-paypilot-simulator-signature": "0" * 64},
                {"x-razorpay-signature": headers["x-paypilot-simulator-signature"]}):
        rejected(lambda: handle_inbound(ctx.hub, "paypal", bad, body), 401)
    assert ctx.hub.payments.get_invoice(kabir["id"])["status"] == "SENT"
    assert ctx.db.claim_receipt("paypal", json.loads(body)["id"])  # a rejected webhook claims nothing


def test_unknown_provider_is_404_and_huge_bodies_413(ctx):
    rejected(lambda: handle_inbound(ctx.hub, "stripe", {}, b"{}"), 404)
    rejected(lambda: handle_inbound(ctx.hub, "", {}, b"{}"), 404)
    big = b"{" + b" " * MAX_BODY_BYTES + b"}"
    rejected(lambda: handle_inbound(ctx.hub, "paypal", {}, big), 413)  # refused before verification


def test_signed_but_malformed_payloads_are_400(ctx):
    bad_json = b"not json"
    rejected(lambda: handle_inbound(ctx.hub, "paypal", {"x-paypilot-simulator-signature":
                                                        hmac_sha256_hex(DEV_SIM_WEBHOOK_SECRET, bad_json)}, bad_json), 400)
    rejected(lambda: handle_inbound(ctx.hub, "paypal", *signed_paypal(["a", "list"])), 400)
    rejected(lambda: handle_inbound(ctx.hub, "paypal", *signed_paypal(
        {"event_type": "INVOICING.INVOICE.PAID", "resource": {}})), 400)  # no event id


# ========================================================= ignored/unmatched
def test_ignored_event_types(ctx):
    res = handle_inbound(ctx.hub, "paypal", *signed_paypal({"id": "WH-X", "event_type": "INVOICING.INVOICE.CREATED"}))
    assert res == {"status": "ignored", "event_id": "WH-X", "raw_type": "INVOICING.INVOICE.CREATED"}
    assert ctx.db.events() == []
    assert handle_inbound(ctx.hub, "paypal", *signed_paypal({"id": "WH-X", "event_type": "x"}))["status"] == "duplicate"


def test_unknown_invoice_is_unmatched(ctx):
    ghost = {**inv_by_number(ctx.hub.payments, "0002"), "id": "INV2-GHST-GHST-GHST-GHST"}
    res = handle_inbound(ctx.hub, "paypal", *signed_paypal(
        {"id": "WH-G", "event_type": "INVOICING.INVOICE.PAID", "resource": {"invoice": paypal_invoice_json(ghost)}}))
    assert res == {"status": "unmatched", "event_id": "WH-G", "invoice_id": "INV2-GHST-GHST-GHST-GHST"}
    assert ctx.db.events() == []
    assert_no_actions(ctx)


def test_processing_failure_releases_the_claim_so_a_retry_works(ctx, monkeypatch):
    kabir = inv_by_number(ctx.hub.payments, "0002")
    headers, body = ctx.hub.payments.simulate_webhook(kabir["id"])
    real = ctx.hub.payments.apply_inbound
    monkeypatch.setattr(ctx.hub.payments, "apply_inbound", lambda e: (_ for _ in ()).throw(RuntimeError("db down")))
    rejected(lambda: handle_inbound(ctx.hub, "paypal", headers, body), 500)
    monkeypatch.setattr(ctx.hub.payments, "apply_inbound", real)
    assert handle_inbound(ctx.hub, "paypal", headers, body)["status"] == "processed"  # the provider's retry


# ================================================================ guarantees
def test_webhooks_never_create_or_execute_actions(ctx, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("a webhook must never propose, approve or execute")
    for name in ("propose", "approve", "reject", "_execute"):
        monkeypatch.setattr(ctx.service, name, forbidden)
    for fn in ("send_reminder", "refund", "create_and_send_invoice"):
        monkeypatch.setattr(ctx.hub.payments, fn, forbidden)
    for number in ("0002", "0003", "0005"):  # 0005 carries the prompt-injection note
        headers, body = ctx.hub.payments.simulate_webhook(inv_by_number(ctx.hub.payments, number)["id"])
        assert handle_inbound(ctx.hub, "paypal", headers, body)["status"] == "processed"
    assert_no_actions(ctx)
    tree = ast.parse((Path(__file__).resolve().parent.parent / "paypilot" / "webhooks.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"service", "policy", "agent", ".service", ".policy", ".agent"}
    assert not any(isinstance(n, ast.ImportFrom) and n.module in ("service", "policy", "agent") for n in ast.walk(tree))
