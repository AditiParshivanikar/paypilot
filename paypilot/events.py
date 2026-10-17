"""Event bus: stores every business event, notifies the owner, and delivers signed outbound webhooks.

Outbound deliveries are HMAC-signed (``PayPilot-Signature: t=<ts>,v1=<hex>``), never follow redirects, and
only go to public https URLs (SSRF protection), re-checked at delivery time against DNS rebinding.
Owner notifications and subscriber deliveries never break the business flow: failures are recorded.
"""
from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import time
from urllib.parse import urlsplit

from .integrations.base import hmac_sha256_hex, safe_equal

EVENT_TYPES = (
    "action.proposed", "action.blocked", "action.approved", "action.rejected", "action.failed",
    "invoice.created", "invoice.paid", "invoice.refunded", "invoice.cancelled",
    "reminder.sent", "refund.completed",
)
DELIVERY_TIMEOUT = 5
MAX_ATTEMPTS = 5
SIGNATURE_TOLERANCE = 300
OWNER_SLACK_CHANNEL = "#payments"
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")

TITLES = {
    "action.proposed": "Waiting for your approval", "action.blocked": "Blocked by the safety gate",
    "action.approved": "Action approved", "action.rejected": "Action rejected", "action.failed": "Action failed",
    "invoice.created": "Invoice sent", "invoice.paid": "Invoice paid", "invoice.refunded": "Invoice refunded",
    "invoice.cancelled": "Invoice cancelled", "reminder.sent": "Reminder sent", "refund.completed": "Refund completed",
}


# ---------------------------------------------------------------------- signatures
def sign(secret: str, body: bytes | str, ts: int | None = None) -> str:
    """'t=<ts>,v1=<hex HMAC-SHA256(secret, "<ts>." + body)>'."""
    ts = int(time.time()) if ts is None else int(ts)
    raw = body.decode("utf-8") if isinstance(body, bytes) else body
    return f"t={ts},v1={hmac_sha256_hex(secret, f'{ts}.{raw}')}"


def verify_signature(secret: str, header: str | None, body: bytes | str, tolerance: int = SIGNATURE_TOLERANCE,
                     now: int | None = None) -> bool:
    """True only for a well-formed header whose timestamp is within ``tolerance`` seconds and one of whose v1
    signatures matches. (Receivers use this; it accepts several v1 values for secret rotation.)"""
    if not secret or not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in str(header).split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs((int(time.time()) if now is None else now) - ts) > tolerance:
        return False
    expected = sign(secret, body, ts).split("v1=", 1)[1]
    return any(safe_equal(candidate, expected) for candidate in parts.get("v1", []))


# --------------------------------------------------------------------- SSRF guard
def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or not ip.is_global)


def validate_webhook_url(url: str, *, allow_http: bool = False, allow_private: bool = False,
                         resolver=socket.getaddrinfo, resolve: bool = True) -> str:
    """Return the URL if it is safe to POST to; raise ValueError otherwise.

    https only; no credentials in the URL; no localhost/.local/.internal names; every address the host
    resolves to must be public (not private, loopback, link-local, reserved or multicast). The two overrides
    exist for local testing only (PAYPILOT_ALLOW_HTTP_WEBHOOKS / PAYPILOT_ALLOW_PRIVATE_WEBHOOKS)."""
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("Webhook URL is not a valid URL") from None
    allowed = ("https", "http") if allow_http else ("https",)
    if parts.scheme.lower() not in allowed:
        raise ValueError(f"Webhook URL must use {' or '.join(allowed)}")
    if parts.username or parts.password:
        raise ValueError("Webhook URL must not contain credentials")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("Webhook URL has no host")
    if allow_private:
        return url
    if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise ValueError(f"Webhook host {host!r} is a local or internal name")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _ip_is_public(literal):
            raise ValueError(f"Webhook host {host} is not a public address")
        return url
    if not resolve:  # demo mode: deliveries are simulated and never leave the server, so DNS is not consulted
        return url
    try:
        infos = resolver(host, port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        raise ValueError(f"Webhook host {host!r} could not be resolved") from None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ValueError(f"Webhook host {host!r} could not be resolved")
    for address in addresses:
        if not _ip_is_public(ipaddress.ip_address(address.split("%")[0])):
            raise ValueError(f"Webhook host {host!r} resolves to a non-public address ({address})")
    return url


# ------------------------------------------------------------------------ event bus
class EventBus:
    def __init__(self, db, hub=None, settings=None, session=None, resolver=socket.getaddrinfo):
        from .config import Settings

        self.db = db
        self.hub = hub
        self.settings = settings or getattr(hub, "settings", None) or Settings()
        self._session = session
        self._resolver = resolver

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    # ------------------------------------------------------------------- emit
    def emit(self, type: str, data: dict) -> dict:
        """Store the event, notify the owner as the policy says, then deliver to subscribers."""
        if type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type {type!r}")
        event = self.db.add_event(type, data)
        self._notify_owner(event)
        for sub in self.db.subscriptions_for(type):
            self._deliver(event, sub, attempt=1)
        return event

    # ------------------------------------------------------- owner notifications
    def _notifier(self, channel: str):
        notifier = (getattr(self.hub, "notifiers", None) or {}).get(channel)
        if notifier is None:
            from .integrations.notifiers import SimulatedNotifier

            notifier = SimulatedNotifier(self.db, channel)
        return notifier

    def _notify_owner(self, event: dict) -> None:
        channels = (self.db.get_policy().get("owner_notifications") or {}).get(event["type"], [])
        subject, body = describe(event)
        for channel in channels:
            if channel == "slack":
                to = OWNER_SLACK_CHANNEL
            elif channel == "email":
                to = self.settings.owner_email
                if not to:
                    self.db.log("system", "owner email notification skipped", {"event": event["id"],
                                                                                "reason": "PAYPILOT_OWNER_EMAIL is not set"})
                    continue
            else:
                self.db.log("system", "owner notification skipped", {"event": event["id"], "channel": channel})
                continue
            try:
                self._notifier(channel).send(to, subject, body)
            except Exception as e:  # noqa: BLE001 - an owner notification must never break the flow
                self.db.log("system", "owner notification failed", {"event": event["id"], "channel": channel,
                                                                     "error": str(e)})

    # --------------------------------------------------------------- delivery
    def _deliver(self, event: dict, sub: dict, attempt: int) -> bool:
        body = json.dumps({"id": event["id"], "type": event["type"], "created": event["created"], "data": event["data"]},
                          separators=(",", ":"), ensure_ascii=False, default=str)
        if self.settings.webhook_transport == "simulate":
            self.db.add_delivery(event["id"], sub["id"], attempt, True)  # demo: recorded, nothing sent
            return True
        headers = {"Content-Type": "application/json", "User-Agent": "PayPilot-Webhooks/1.0",
                   "PayPilot-Event": event["type"], "PayPilot-Delivery": "dlv_" + secrets.token_hex(10),
                   "PayPilot-Signature": sign(sub["secret"], body)}
        try:
            validate_webhook_url(sub["url"], allow_http=self.settings.allow_http_webhooks,
                                 allow_private=self.settings.allow_private_webhooks, resolver=self._resolver)
            resp = self.session.post(sub["url"], data=body.encode("utf-8"), headers=headers,
                                     timeout=DELIVERY_TIMEOUT, allow_redirects=False)
        except ValueError as e:  # failed the SSRF re-check (e.g. DNS now points somewhere private)
            self.db.add_delivery(event["id"], sub["id"], attempt, False, None, f"Blocked: {e}")
            return False
        except Exception as e:  # noqa: BLE001 - network errors are recorded, not raised
            self.db.add_delivery(event["id"], sub["id"], attempt, False, None, type(e).__name__)
            return False
        ok = 200 <= resp.status_code < 300
        error = None if ok else (f"HTTP {resp.status_code} (redirects are not followed)"
                                 if 300 <= resp.status_code < 400 else f"HTTP {resp.status_code}")
        self.db.add_delivery(event["id"], sub["id"], attempt, ok, resp.status_code, error)
        return ok

    def retry_failed(self, max_attempts: int = MAX_ATTEMPTS) -> dict:
        """Re-deliver every (event, subscription) pair that has never succeeded, up to max_attempts in total."""
        retried = succeeded = 0
        for row in self.db.failed_deliveries(max_attempts):
            event = self.db.get_event(row["event_id"])
            sub = self.db.get_subscription(row["subscription_id"], with_secret=True)
            if not event or not sub:
                continue
            retried += 1
            succeeded += self._deliver(event, sub, attempt=row["attempts"] + 1)
        return {"retried": retried, "succeeded": succeeded}

    # ---------------------------------------------------------- subscriptions
    def subscribe(self, url: str, events: list[str], description: str = "") -> dict:
        """Register an outbound webhook. The signing secret is returned ONLY here."""
        events = list(dict.fromkeys(events or []))
        if not events:
            raise ValueError("Choose at least one event type")
        if unknown := [e for e in events if e != "*" and e not in EVENT_TYPES]:
            raise ValueError(f"Unknown event type(s): {', '.join(unknown)}")
        url = validate_webhook_url(url, allow_http=self.settings.allow_http_webhooks,
                                   allow_private=self.settings.allow_private_webhooks, resolver=self._resolver,
                                   resolve=self.settings.webhook_transport == "http")
        secret = "whsec_" + secrets.token_urlsafe(24)
        sub_id = self.db.add_subscription(url, events, (description or "").strip()[:200], secret)
        self.db.log("user", "added webhook subscription", {"id": sub_id, "url": url, "events": events})
        return {**self.db.get_subscription(sub_id), "secret": secret}

    def unsubscribe(self, sub_id: str) -> bool:
        done = self.db.deactivate_subscription(sub_id)
        if done:
            self.db.log("user", "removed webhook subscription", {"id": sub_id})
        return done


def describe(event: dict) -> tuple[str, str]:
    """A short, human subject and body for owner notifications."""
    d = event.get("data") or {}
    subject = TITLES.get(event["type"], event["type"])
    lines = []
    if d.get("action"):
        lines.append(f"Action: {d['action'].replace('_', ' ')}" + (f" (#{d['action_id']})" if d.get("action_id") else ""))
    for k, v in (d.get("preview") or {}).items():
        lines.append(f"{k}: {v}")
    inv = d.get("invoice") or {}
    if inv:
        lines.append(f"Invoice #{inv.get('number')} for {inv.get('customer_name')}: "
                     f"{inv.get('currency')} {float(inv.get('amount') or 0):,.2f}")
    for key in ("amount", "channel", "error"):
        if d.get(key) is not None and not inv:
            lines.append(f"{key.title()}: {d[key]}")
    for reason in d.get("reasons") or []:
        lines.append(f"- {reason}")
    return subject, "\n".join(lines) or subject
