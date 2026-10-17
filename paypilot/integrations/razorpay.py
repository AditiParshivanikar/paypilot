"""Razorpay (test mode) payments adapter over the REST API, https://api.razorpay.com/v1, with basic auth.

Amounts go to Razorpay in paise (integer, x100). An invoice's due date maps to ``expire_by`` at 23:59:59 UTC.
The key secret and webhook secret live only inside this object and never appear in results or errors.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import requests

from ..backends import BackendError, PaymentsBackend, refundable, round_money
from .base import InboundEvent, WebhookVerificationError, header, hmac_sha256_hex, safe_equal

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT = 20

STATUS_FROM_RAZORPAY = {"draft": "DRAFT", "issued": "SENT", "partially_paid": "PARTIALLY_PAID", "paid": "PAID",
                        "cancelled": "CANCELLED", "expired": "CANCELLED"}
STATUS_TO_RAZORPAY = {"DRAFT": "draft", "SENT": "issued", "UNPAID": "issued", "PARTIALLY_PAID": "partially_paid",
                      "PAID": "paid", "PARTIALLY_REFUNDED": "paid", "REFUNDED": "paid", "CANCELLED": "cancelled"}
RAZORPAY_EVENT_TYPES = {"invoice.paid": "invoice.paid", "invoice.partially_paid": "invoice.paid",
                        "invoice.cancelled": "invoice.cancelled", "invoice.expired": "invoice.cancelled",
                        "refund.processed": "invoice.refunded"}


def to_paise(amount) -> int:
    return int((Decimal(str(amount or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_paise(paise) -> float:
    return round_money(Decimal(str(paise or 0)) / 100)


def expire_by(due_date: str) -> int:
    """Due date -> unix time of 23:59:59 UTC that day."""
    d = date.fromisoformat(due_date)
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())


def _local_date(ts) -> str | None:
    return datetime.fromtimestamp(int(ts)).date().isoformat() if ts else None


def _utc_date(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat() if ts else None


def normalize(raw: dict) -> dict:
    """Razorpay invoice entity -> the normalized invoice dict (refunds are filled in by get_invoice)."""
    cust = raw.get("customer_details") or {}
    items = raw.get("line_items") or [{}]
    notes = raw.get("notes") if isinstance(raw.get("notes"), dict) else {}
    return {
        "id": raw.get("id"),
        "number": raw.get("invoice_number") or raw.get("receipt") or raw.get("id"),
        "status": STATUS_FROM_RAZORPAY.get(str(raw.get("status", "")).lower(), str(raw.get("status", "")).upper()),
        "customer_name": cust.get("name") or cust.get("customer_name") or "",
        "customer_email": cust.get("email") or cust.get("customer_email") or "",
        "amount": from_paise(raw.get("amount")),
        "currency": raw.get("currency") or "INR",
        "description": items[0].get("name") or raw.get("description") or "",
        "invoice_date": _local_date(raw.get("issued_at") or raw.get("created_at")),
        "due_date": _utc_date(raw.get("expire_by")),
        "paid_amount": from_paise(raw.get("amount_paid")),
        "refunded_amount": 0.0,
        "payment_id": raw.get("payment_id"),
        "note": notes.get("note", "") or "",
        "provider": "razorpay",
    }


def razorpay_invoice_json(inv: dict) -> dict:
    """A normalized invoice in Razorpay's invoice-entity shape (used by the simulator's webhooks)."""
    amount, paid = to_paise(inv["amount"]), to_paise(inv["paid_amount"])
    issued = datetime.fromisoformat(inv["invoice_date"]) if inv.get("invoice_date") else datetime.now()
    return {
        "id": inv["id"], "entity": "invoice", "type": "invoice", "invoice_number": inv["number"],
        "customer_details": {"name": inv["customer_name"], "email": inv["customer_email"]},
        "line_items": [{"name": inv["description"], "amount": amount, "currency": inv["currency"], "quantity": 1}],
        "description": inv["description"], "status": STATUS_TO_RAZORPAY.get(inv["status"], "issued"),
        "amount": amount, "amount_paid": paid, "amount_due": max(amount - paid, 0), "currency": inv["currency"],
        "payment_id": inv.get("payment_id"), "notes": {"note": inv["note"]} if inv.get("note") else [],
        "issued_at": int(issued.timestamp()), "expire_by": expire_by(inv["due_date"]) if inv.get("due_date") else None,
    }


def parse_razorpay_event(payload: dict) -> InboundEvent:
    """Razorpay webhook JSON -> InboundEvent. Unknown events become 'ignored'."""
    raw_type = str(payload.get("event") or "")
    kind = RAZORPAY_EVENT_TYPES.get(raw_type, "ignored")
    pl = payload.get("payload") or {}
    inv = (pl.get("invoice") or {}).get("entity") or {}
    pay = (pl.get("payment") or {}).get("entity") or {}
    ref = (pl.get("refund") or {}).get("entity") or {}
    amount = None
    if kind == "invoice.paid":
        amount = from_paise(pay["amount"]) if pay.get("amount") is not None else (
            from_paise(inv["amount_paid"]) if inv.get("amount_paid") is not None else None)
    elif kind == "invoice.refunded" and ref.get("amount") is not None:
        amount = from_paise(ref["amount"])
    subject = ref if kind == "invoice.refunded" else (pay if raw_type == "invoice.partially_paid" and pay else inv or pay)
    extra = {"payment_id": pay.get("id") or inv.get("payment_id") or ref.get("payment_id"),
             "refund_id": ref.get("id"), "currency": inv.get("currency") or pay.get("currency") or ref.get("currency")}
    return InboundEvent(
        provider="razorpay",
        # Razorpay sends the event id in the x-razorpay-event-id header, not the body; derive a stable one here.
        event_id=f"{raw_type}:{subject.get('id', '')}:{payload.get('created_at', '')}",
        type=kind, invoice_id=inv.get("id") or pay.get("invoice_id") or ref.get("invoice_id"), amount=amount,
        raw_type=raw_type, extra={k: v for k, v in extra.items() if v})


class RazorpayBackend(PaymentsBackend):
    name = "razorpay"
    provider = "razorpay"
    simulated = False

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str, business_name: str,
                 session: requests.Session | None = None):
        self._auth = (key_id, key_secret)
        self._webhook_secret = webhook_secret
        self._redact = tuple(s for s in (key_id, key_secret, webhook_secret) if s)
        self.business_name = business_name
        self.session = session or requests.Session()

    def __repr__(self) -> str:
        return f"RazorpayBackend(business_name={self.business_name!r})"

    # ---------------------------------------------------------------- internals
    def _scrub(self, text: str) -> str:
        for s in self._redact:
            text = text.replace(s, "***")
        return text

    def _request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None) -> dict:
        try:
            resp = self.session.request(method, BASE_URL + path, auth=self._auth, params=params, json=payload,
                                        timeout=TIMEOUT)
        except requests.RequestException as e:
            raise BackendError(self._scrub(f"Razorpay {method} {path} failed ({type(e).__name__})")) from None
        if resp.status_code >= 400:
            detail = f"HTTP {resp.status_code}"
            try:
                err = (resp.json() or {}).get("error") or {}
                detail += f" {err.get('code', '')}: {err.get('description', '')}".rstrip(": ")
            except ValueError:
                pass
            raise BackendError(self._scrub(f"Razorpay {method} {path} failed ({detail})"))
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    # ------------------------------------------------------------------ outbound
    def create_and_send_invoice(self, customer_name, customer_email, amount, currency, description, due_date,
                                note="") -> dict:
        paise = to_paise(amount)
        if paise <= 0:
            raise BackendError("Invoice amount must be greater than zero")
        payload = {
            "type": "invoice",
            "description": description[:2048],
            "customer": {"name": customer_name, "email": customer_email},
            "line_items": [{"name": description[:255], "amount": paise, "currency": currency.upper(), "quantity": 1}],
            "currency": currency.upper(),
            "expire_by": expire_by(due_date),
            "email_notify": 1,   # Razorpay emails the invoice to the customer on creation
            "sms_notify": 0,
        }
        if note:
            payload["notes"] = {"note": note[:255]}
        return normalize(self._request("POST", "/invoices", payload=payload))

    def list_invoices(self) -> list[dict]:
        data = self._request("GET", "/invoices", params={"type": "invoice", "count": 100})
        return [normalize(i) for i in data.get("items", [])]

    def get_invoice(self, invoice_id: str) -> dict:
        inv = normalize(self._request("GET", f"/invoices/{invoice_id}"))
        if inv["payment_id"] and inv["paid_amount"]:  # invoices don't carry refunds; the payment does
            payment = self._request("GET", f"/payments/{inv['payment_id']}")
            inv["refunded_amount"] = min(inv["paid_amount"], from_paise(payment.get("amount_refunded")))
            if inv["refunded_amount"]:
                inv["status"] = "REFUNDED" if inv["refunded_amount"] >= inv["paid_amount"] else "PARTIALLY_REFUNDED"
        return inv

    def send_reminder(self, invoice_id: str, note: str) -> dict:
        # Razorpay re-sends its own invoice email; it has no field for a custom note.
        self._request("POST", f"/invoices/{invoice_id}/notify_by/email")
        return {"invoice_id": invoice_id, "reminded": True, "channel": "provider"}

    def refund(self, invoice_id: str, amount: float, reason: str) -> dict:
        inv = self.get_invoice(invoice_id)
        if not inv["payment_id"]:
            raise BackendError(f"Invoice {inv['number']} has no Razorpay payment to refund")
        amount = round_money(amount)
        if amount <= 0 or amount > refundable(inv):
            raise BackendError(f"Refund {amount:.2f} exceeds the refundable balance {refundable(inv):.2f}")
        res = self._request("POST", f"/payments/{inv['payment_id']}/refund",
                            payload={"amount": to_paise(amount), "notes": {"reason": (reason or "Refund")[:255]}})
        return {"invoice_id": invoice_id, "refund_id": res.get("id"), "amount": amount,
                "status": res.get("status", "processed"), "to": "original payer"}

    def record_payment(self, invoice_id: str, amount: float | None = None) -> dict:
        raise BackendError("Razorpay invoices cannot be marked as paid through the API; "
                           "the customer pays through Razorpay's payment link")

    # ------------------------------------------------------------------- inbound
    def verify_webhook(self, headers, body: bytes) -> None:
        if not self._webhook_secret:
            raise WebhookVerificationError("RAZORPAY_WEBHOOK_SECRET is not set, so Razorpay webhooks cannot be verified")
        if not safe_equal(header(headers, "x-razorpay-signature"), hmac_sha256_hex(self._webhook_secret, body)):
            raise WebhookVerificationError("Missing or invalid x-razorpay-signature header")

    def parse_webhook(self, payload: dict) -> InboundEvent:
        return parse_razorpay_event(payload)


__all__ = ["RazorpayBackend", "normalize", "parse_razorpay_event", "razorpay_invoice_json", "to_paise", "from_paise",
           "expire_by", "BASE_URL"]
