"""Notifier adapters: email (SMTP), Slack (incoming webhook), WhatsApp (Twilio), and a simulator for each.

Each real adapter holds its own credentials, scrubs them from any error, and raises on a non-2xx answer.
``Notifier.send`` (base.py) records every attempt, including failures, in the notifications table.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

import requests
from urllib.parse import urlsplit

from .base import Notifier

TIMEOUT = 20


class SimulatedNotifier(Notifier):
    """Demo mode: records the message as 'simulated' and sends nothing."""

    adapter = "simulator"
    simulated = True

    def __init__(self, db, channel: str):
        super().__init__(db)
        self.channel = channel

    def _deliver(self, to: str, subject: str, body: str) -> dict:
        return {}


class SmtpEmailNotifier(Notifier):
    channel = "email"
    adapter = "smtp"
    simulated = False

    def __init__(self, db, host: str, port: int, user: str, password: str, sender: str, smtp_factory=None):
        super().__init__(db)
        self.host, self.port, self.user, self.sender = host, int(port), user, sender or user
        self._password = password
        self._smtp = smtp_factory or smtplib.SMTP
        self._redact = tuple(s for s in (password,) if s)

    def __repr__(self) -> str:
        return f"SmtpEmailNotifier(host={self.host!r}, sender={self.sender!r})"

    def status(self) -> dict:
        return {**super().status(), "host": self.host, "port": self.port, "sender": self.sender}

    def _deliver(self, to: str, subject: str, body: str) -> dict:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg["Message-ID"] = make_msgid(domain=self.sender.split("@")[-1] if "@" in self.sender else None)
        msg.set_content(body)
        with self._smtp(self.host, self.port, timeout=TIMEOUT) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self.user:
                smtp.login(self.user, self._password)
            smtp.send_message(msg)
        return {"message_id": msg["Message-ID"]}


class SlackWebhookNotifier(Notifier):
    """Posts to one Slack channel through an incoming webhook. The webhook URL is the credential."""

    channel = "slack"
    adapter = "slack-webhook"
    simulated = False

    def __init__(self, db, webhook_url: str, session: requests.Session | None = None):
        super().__init__(db)
        self._url = webhook_url
        self.session = session or requests.Session()
        path = urlsplit(webhook_url).path
        segments = [p for p in path.split("/") if p and p != "services"]
        self._redact = tuple(s for s in (webhook_url, path, *segments) if s)

    def __repr__(self) -> str:
        return "SlackWebhookNotifier()"

    def _deliver(self, to: str, subject: str, body: str) -> dict:
        resp = self.session.post(self._url, json={"text": f"*{subject}*\n{body}"}, timeout=TIMEOUT)
        if not 200 <= resp.status_code < 300:
            raise RuntimeError(f"Slack answered HTTP {resp.status_code}")
        return {}


class TwilioWhatsAppNotifier(Notifier):
    channel = "whatsapp"
    adapter = "twilio"
    simulated = False
    API = "https://api.twilio.com/2010-04-01"

    def __init__(self, db, account_sid: str, auth_token: str, from_number: str, session: requests.Session | None = None):
        super().__init__(db)
        self._sid, self._token = account_sid, auth_token
        self.from_number = from_number
        self.session = session or requests.Session()
        self._redact = tuple(s for s in (auth_token, account_sid) if s)

    def __repr__(self) -> str:
        return f"TwilioWhatsAppNotifier(from_number={self.from_number!r})"

    def status(self) -> dict:
        return {**super().status(), "from": self.from_number}

    @staticmethod
    def _wa(number: str) -> str:
        number = str(number).strip()
        return number if number.startswith("whatsapp:") else f"whatsapp:{number}"

    def _deliver(self, to: str, subject: str, body: str) -> dict:
        text = f"{subject}\n\n{body}" if subject else body
        resp = self.session.post(f"{self.API}/Accounts/{self._sid}/Messages.json",
                                 data={"From": self._wa(self.from_number), "To": self._wa(to), "Body": text},
                                 auth=(self._sid, self._token), timeout=TIMEOUT)
        if not 200 <= resp.status_code < 300:
            message = ""
            try:
                err = resp.json() or {}
                message = f" {err.get('code', '')}: {err.get('message', '')}".rstrip(": ")
            except ValueError:
                pass
            raise RuntimeError(f"Twilio answered HTTP {resp.status_code}{message}")
        data = resp.json() if resp.content else {}
        return {"message_sid": data.get("sid"), "provider_status": data.get("status")}
