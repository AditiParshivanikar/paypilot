"""Ports shared by every adapter: inbound webhook events, signature helpers, notifiers and exporters.

Rules (brief, principles 4 and 7): adapters hold their own credentials and never put them in a result, event,
log line or error; inbound webhooks become ``InboundEvent`` facts and never create or execute actions.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

INBOUND_TYPES = ("invoice.paid", "invoice.refunded", "invoice.cancelled", "ignored")
# requests/urllib3 quote the request URL in their errors ("Max retries exceeded with url: /services/T/B/token",
# "404 Client Error: Not Found for url: https://..."), and some credentials live in the URL path.
URL_IN_ERROR = re.compile(r"(?i)\b((?:with|for) url: )\S+")


def redact(text: str, secrets) -> str:
    """Replace every known credential with *** and drop any URL an HTTP library quoted in the text."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    return URL_IN_ERROR.sub(r"\1***", text)


class WebhookVerificationError(Exception):
    """An inbound webhook failed signature verification. The message never contains a secret."""


class NotificationError(Exception):
    """A notification could not be delivered. Raised after the failed attempt has been recorded."""


@dataclass
class InboundEvent:
    provider: str                 # paypal | razorpay
    event_id: str                 # provider's event id (used for de-duplication)
    type: str                     # one of INBOUND_TYPES
    invoice_id: str | None        # provider invoice id the fact is about
    amount: float | None = None   # amount paid/refunded in this event, in currency units (None = unknown/full)
    raw_type: str = ""            # provider's own event name, e.g. INVOICING.INVOICE.PAID
    extra: dict = field(default_factory=dict)  # e.g. payment_id, currency; never credentials


def hmac_sha256_hex(secret: str, body: bytes | str) -> str:
    data = body.encode("utf-8") if isinstance(body, str) else body
    return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


def safe_equal(a: str | None, b: str | None) -> bool:
    """Constant-time comparison; False if either side is missing."""
    if not a or not b:
        return False
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def header(headers, name: str) -> str | None:
    """Case-insensitive header lookup for plain dicts and framework header objects alike."""
    if headers is None:
        return None
    for k, v in dict(headers).items():
        if str(k).lower() == name.lower():
            return v
    return None


# ------------------------------------------------------------------------ notifiers
class Notifier(ABC):
    """Sends one message on one channel (email, WhatsApp, Slack...). Every attempt is recorded."""

    channel: str = "base"      # email | whatsapp | slack
    adapter: str = "base"      # e.g. smtp, twilio, slack-webhook, simulator
    simulated: bool = True

    def __init__(self, db):
        self.db = db
        self._redact: tuple[str, ...] = ()  # credentials to scrub from any error text

    def status(self) -> dict:
        return {"channel": self.channel, "adapter": self.adapter, "simulated": self.simulated}

    def send(self, to: str, subject: str, body: str) -> dict:
        """Deliver (or simulate) and ALWAYS record the attempt. On failure the attempt is recorded as 'failed'
        and NotificationError is raised with a credential-free message."""
        try:
            detail = self._deliver(to, subject, body) or {}
        except Exception as e:  # noqa: BLE001 - record every failure, then surface it
            error = self._safe_error(e)
            self.db.add_notification(self.channel, self.adapter, to, subject, body, "failed", {"error": error})
            raise NotificationError(f"{self.channel} via {self.adapter} failed: {error}") from None
        status = "simulated" if self.simulated else "sent"
        self.db.add_notification(self.channel, self.adapter, to, subject, body, status, detail or None)
        return {"channel": self.channel, "adapter": self.adapter, "to": to, "status": status, **detail}

    @abstractmethod
    def _deliver(self, to: str, subject: str, body: str) -> dict | None:
        """Actually send. Raise on failure (including non-2xx). Return extra non-secret details (e.g. a message id)."""

    def _safe_error(self, exc: Exception) -> str:
        """The error type and message with every known credential replaced by ***."""
        return redact(type(exc).__name__ + (f": {exc}" if str(exc) else ""), self._redact)


# ------------------------------------------------------------------------ exporters
class AccountingExporter(ABC):
    """Turns normalized invoices into a file an accounting tool can import."""

    format: str = "base"       # e.g. csv, tally-xml
    mime: str = "text/plain"
    extension: str = "txt"
    label: str = "Base"

    @abstractmethod
    def export(self, invoices: list[dict], business_name: str) -> str: ...
