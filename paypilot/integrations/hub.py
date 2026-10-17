"""The integration hub: chooses a real adapter or its simulator for every slot (payments, notifiers, exporters).

A real adapter is used only when its credentials are set AND demo mode is off. ``status()`` describes each
slot for the UI and never includes a credential.
"""
from __future__ import annotations

from ..backends import PROVIDER_LABELS, PaymentsBackend, make_backend
from .accounting import EXPORTERS
from .base import AccountingExporter, Notifier
from .notifiers import SimulatedNotifier, SlackWebhookNotifier, SmtpEmailNotifier, TwilioWhatsAppNotifier

NOTIFIER_CHANNELS = ("email", "slack", "whatsapp")


class IntegrationHub:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.payments: PaymentsBackend = make_backend(settings, db)
        self._providers: dict[str, PaymentsBackend] = {self.payments.provider: self.payments}
        self.notifiers: dict[str, Notifier] = self._build_notifiers()
        self.exporters: dict[str, AccountingExporter] = dict(EXPORTERS)
        self._events = None

    # ------------------------------------------------------------------ slots
    def provider(self, name: str) -> PaymentsBackend:
        """The adapter for a payment provider. Other providers' adapters are unseeded: they exist to verify and
        record inbound webhooks, not to show demo data."""
        name = name.lower()
        if name not in PROVIDER_LABELS:
            raise ValueError(f"Unknown payment provider {name!r}")
        if name not in self._providers:
            self._providers[name] = make_backend(self.settings, self.db, provider=name, seed=False)
        return self._providers[name]

    @property
    def events(self):
        """The event bus, created on first use (paypilot/events.py)."""
        if self._events is None:
            from ..events import EventBus

            self._events = EventBus(self.db, self, self.settings)
        return self._events

    def _build_notifiers(self) -> dict[str, Notifier]:
        s, live = self.settings, not self.settings.demo_mode
        email: Notifier = SimulatedNotifier(self.db, "email")
        if live and s.smtp_host and (s.smtp_from or s.smtp_user):
            email = SmtpEmailNotifier(self.db, s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password,
                                      s.smtp_from or s.smtp_user)
        slack: Notifier = SimulatedNotifier(self.db, "slack")
        if live and s.slack_webhook_url:
            slack = SlackWebhookNotifier(self.db, s.slack_webhook_url)
        whatsapp: Notifier = SimulatedNotifier(self.db, "whatsapp")
        if live and s.twilio_account_sid and s.twilio_auth_token and s.twilio_whatsapp_from:
            whatsapp = TwilioWhatsAppNotifier(self.db, s.twilio_account_sid, s.twilio_auth_token,
                                              s.twilio_whatsapp_from)
        return {"email": email, "slack": slack, "whatsapp": whatsapp}

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        """What each slot is connected to. Built only from non-secret fields."""
        s = self.settings
        providers = {}
        for name in PROVIDER_LABELS:
            if name in self._providers:
                providers[name] = self._providers[name].status()
            else:
                configured = s.has_paypal_keys if name == "paypal" else s.has_razorpay_keys
                real = configured and not s.demo_mode
                mode = "simulator" if not real else ("sandbox" if name == "paypal" else "test mode")
                providers[name] = {"provider": name, "simulated": not real, "mode": mode,
                                   "label": f"{PROVIDER_LABELS[name]} {mode}"}
        return {
            "demo_mode": s.demo_mode,
            "webhook_transport": s.webhook_transport,
            "payments": self.payments.status(),
            "providers": providers,
            "notifiers": {ch: self.notifiers[ch].status() for ch in NOTIFIER_CHANNELS},
            "exporters": [{"format": e.format, "label": e.label, "mime": e.mime, "extension": e.extension}
                          for e in self.exporters.values()],
            "owner_email_configured": bool(s.owner_email),
            "webhooks": {"paypal_verification": bool(s.paypal_webhook_id),
                         "razorpay_verification": bool(s.razorpay_webhook_secret)},
        }
