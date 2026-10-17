"""Inbound provider webhooks: verify, de-duplicate, record the fact, emit a domain event. Nothing else.

Brief, principle 7: an inbound webhook only records that an invoice was paid, refunded or cancelled. This module
never proposes, approves or executes an action, and it never hands provider text to the agent; it does not even
import the service, policy or agent modules.
"""
from __future__ import annotations

import json

from .backends import PROVIDER_LABELS
from .integrations.base import WebhookVerificationError, header

MAX_BODY_BYTES = 256 * 1024
# Providers that send the event id in a header rather than the body.
EVENT_ID_HEADERS = {"razorpay": "x-razorpay-event-id"}


class WebhookRejected(Exception):
    """The webhook was refused. ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def handle_inbound(hub, provider: str, headers, body: bytes) -> dict:
    """Process one inbound webhook. Returns a result (answer HTTP 200) or raises WebhookRejected."""
    db = hub.db
    provider = (provider or "").lower()
    if provider not in PROVIDER_LABELS:
        raise WebhookRejected(404, f"Unknown provider {provider!r}")
    body = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        db.log("webhook", "rejected webhook", {"provider": provider, "reason": "body too large", "bytes": len(body)})
        raise WebhookRejected(413, f"Body larger than {MAX_BODY_BYTES // 1024} KB")

    backend = hub.provider(provider)
    try:
        backend.verify_webhook(headers, body)
    except WebhookVerificationError as e:
        db.log("webhook", "rejected webhook", {"provider": provider, "reason": str(e)})
        raise WebhookRejected(401, "Signature verification failed") from None

    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        db.log("webhook", "rejected webhook", {"provider": provider, "reason": "body is not a JSON object"})
        raise WebhookRejected(400, "Body is not a JSON object") from None
    event = backend.parse_webhook(payload)
    if provider in EVENT_ID_HEADERS and (hid := header(headers, EVENT_ID_HEADERS[provider])):
        event.event_id = str(hid)
    if not event.event_id:
        db.log("webhook", "rejected webhook", {"provider": provider, "reason": "no event id", "raw_type": event.raw_type})
        raise WebhookRejected(400, "Webhook has no event id")

    if not db.claim_receipt(provider, event.event_id):
        db.log("webhook", "duplicate webhook", {"provider": provider, "event_id": event.event_id})
        return {"status": "duplicate", "event_id": event.event_id}
    summary = {"provider": provider, "event_id": event.event_id, "raw_type": event.raw_type, "type": event.type,
               "invoice_id": event.invoice_id}
    if event.type == "ignored":
        db.log("webhook", "ignored webhook", summary)
        return {"status": "ignored", "event_id": event.event_id, "raw_type": event.raw_type}

    try:
        invoice = backend.apply_inbound(event)
    except Exception:  # noqa: BLE001 - release the claim so the provider's retry is processed
        db.release_receipt(provider, event.event_id)
        db.log("webhook", "webhook processing failed", summary)
        raise WebhookRejected(500, "Webhook processing failed; the provider may retry") from None
    if invoice is None:
        db.log("webhook", "unmatched webhook", summary)
        return {"status": "unmatched", "event_id": event.event_id, "invoice_id": event.invoice_id}

    amount = event.amount
    if amount is None:
        amount = {"invoice.paid": invoice["paid_amount"], "invoice.refunded": invoice["refunded_amount"]}.get(
            event.type, invoice["amount"])
    data = {"provider": provider, "invoice_id": invoice["id"], "number": invoice["number"],
            "customer": invoice["customer_name"], "currency": invoice["currency"], "amount": amount,
            "status": invoice["status"], "provider_event_id": event.event_id}
    db.log("webhook", f"recorded {event.type}", {**summary, "status": invoice["status"], "amount": amount})
    hub.events.emit(event.type, data)
    return {"status": "processed", "event": event.type, "event_id": event.event_id, "invoice_id": invoice["id"],
            "invoice_status": invoice["status"]}
