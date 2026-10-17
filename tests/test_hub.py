"""Notifiers (simulated, SMTP, Slack, Twilio), accounting exporters, the integration hub and the app context.
All outbound traffic is faked; no network or mail server is used."""
import csv
import io
import json
import smtplib
import xml.etree.ElementTree as ET
from types import SimpleNamespace as NS

import pytest
import requests

from conftest import days_from_today, inv_by_number
from paypilot.agent import ClaudeAgent, RuleAgent
from paypilot.app_context import build_context
from paypilot.backends import MockBackend
from paypilot.config import Settings
from paypilot.integrations.accounting import EXPORTERS, CsvExporter, JsonExporter, TallyXmlExporter, neutralise
from paypilot.integrations.base import NotificationError
from paypilot.integrations.hub import IntegrationHub
from paypilot.integrations.notifiers import (SimulatedNotifier, SlackWebhookNotifier, SmtpEmailNotifier,
                                             TwilioWhatsAppNotifier)
from paypilot.service import PayPilotService

SLACK_URL = "https://hooks.slack.com/services/T000/B000/sEcReTtOkEn"
TWILIO_SID, TWILIO_TOKEN = "AC0123456789abcdef", "twilio-token-do-not-leak"
SMTP_PASSWORD = "smtp-password-do-not-leak"
LIVE_ENV = {"RAZORPAY_KEY_ID": "rzp_test_k", "RAZORPAY_KEY_SECRET": "rzp-secret-1", "PAYPILOT_PAYMENT_PROVIDER": "razorpay",
            "RAZORPAY_WEBHOOK_SECRET": "rzp-whsec-2", "PAYPAL_CLIENT_ID": "pp-cid-3", "PAYPAL_CLIENT_SECRET": "pp-sec-4",
            "ANTHROPIC_API_KEY": "sk-ant-5", "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
            "SMTP_USER": "asha@studio.example", "SMTP_PASSWORD": SMTP_PASSWORD, "SMTP_FROM": "asha@studio.example",
            "SLACK_WEBHOOK_URL": SLACK_URL, "TWILIO_ACCOUNT_SID": TWILIO_SID, "TWILIO_AUTH_TOKEN": TWILIO_TOKEN,
            "TWILIO_WHATSAPP_FROM": "+14155238886", "PAYPILOT_SIM_WEBHOOK_SECRET": "sim-secret-6"}
SECRET_VALUES = ["rzp-secret-1", "rzp-whsec-2", "pp-sec-4", "sk-ant-5", SMTP_PASSWORD, SLACK_URL, "sEcReTtOkEn",
                 TWILIO_TOKEN, "sim-secret-6"]


class Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body = status_code, body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, json=None, data=None, auth=None, timeout=None):
        self.calls.append(NS(url=url, json=json, data=data, auth=auth, timeout=timeout))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


# ================================================================== notifiers
def test_simulated_notifier_records_and_sends_nothing(db):
    out = SimulatedNotifier(db, "whatsapp").send("+919800000003", "Reminder", "Please pay #0002")
    assert out == {"channel": "whatsapp", "adapter": "simulator", "to": "+919800000003", "status": "simulated"}
    [row] = db.notifications()
    assert (row["channel"], row["adapter"], row["recipient"], row["subject"], row["body"], row["status"]) == \
        ("whatsapp", "simulator", "+919800000003", "Reminder", "Please pay #0002", "simulated")


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, fail_login=False):
        self.host, self.port, self.timeout, self.log, self.sent = host, port, timeout, [], []
        self.fail_login = fail_login
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append("quit")

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user))
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, f"bad credentials for {user} / {password}".encode())

    def send_message(self, msg):
        self.log.append("send")
        self.sent.append(msg)


def test_smtp_uses_starttls_logs_in_and_records(db):
    FakeSMTP.instances.clear()
    n = SmtpEmailNotifier(db, "smtp.example.com", 587, "asha@studio.example", SMTP_PASSWORD, "asha@studio.example",
                          smtp_factory=FakeSMTP)
    out = n.send("kabir@cafe-kabir.example", "Payment reminder", "Hi Kabir")
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587) and smtp.timeout
    assert smtp.log == ["starttls", ("login", "asha@studio.example"), "send", "quit"]  # TLS before login
    msg = smtp.sent[0]
    assert (msg["From"], msg["To"], msg["Subject"]) == ("asha@studio.example", "kabir@cafe-kabir.example", "Payment reminder")
    assert msg.get_content().strip() == "Hi Kabir"
    assert out["status"] == "sent" and out["message_id"] == msg["Message-ID"]
    assert db.notifications()[0]["status"] == "sent"


def test_smtp_failure_is_recorded_raised_and_scrubbed(db):
    n = SmtpEmailNotifier(db, "smtp.example.com", 587, "asha@studio.example", SMTP_PASSWORD, "asha@studio.example",
                          smtp_factory=lambda h, p, timeout=None: FakeSMTP(h, p, timeout, fail_login=True))
    with pytest.raises(NotificationError) as e:
        n.send("kabir@cafe-kabir.example", "Payment reminder", "Hi")
    assert "SMTPAuthenticationError" in str(e.value) and SMTP_PASSWORD not in str(e.value)
    [row] = db.notifications()
    assert row["status"] == "failed" and SMTP_PASSWORD not in json.dumps(row)
    assert SMTP_PASSWORD not in repr(n) and SMTP_PASSWORD not in json.dumps(n.status())


def test_slack_payload(db):
    session = Session(Resp(200))
    out = SlackWebhookNotifier(db, SLACK_URL, session=session).send("owner", "Invoice paid", "#0002 Kabir Singh, INR 3,500.00")
    call = session.calls[0]
    assert call.url == SLACK_URL and call.json == {"text": "*Invoice paid*\n#0002 Kabir Singh, INR 3,500.00"}
    assert call.timeout and out["status"] == "sent" and db.notifications()[0]["status"] == "sent"


@pytest.mark.parametrize("failure", [Resp(500), Resp(404), requests.ConnectionError(f"cannot connect to {SLACK_URL}"),
                                     requests.ConnectionError("HTTPSConnectionPool(host='hooks.slack.com', port=443): Max "
                                                              "retries exceeded with url: /services/T000/B000/sEcReTtOkEn"),
                                     requests.HTTPError("404 Client Error: Not Found for url: "
                                                        "https://hooks.slack.com/services/T000/B000/sEcReTtOkEn")])
def test_slack_failures_are_recorded_raised_and_hide_the_url(db, failure):
    n = SlackWebhookNotifier(db, SLACK_URL, session=Session(failure))
    with pytest.raises(NotificationError) as e:
        n.send("owner", "Invoice paid", "body")
    assert "sEcReTtOkEn" not in str(e.value) and SLACK_URL not in str(e.value)
    [row] = db.notifications()
    assert row["status"] == "failed" and "sEcReTtOkEn" not in json.dumps(row)
    assert "sEcReTtOkEn" not in repr(n) + json.dumps(n.status())


def test_twilio_whatsapp_payload(db):
    session = Session(Resp(201, {"sid": "SM123", "status": "queued"}))
    n = TwilioWhatsAppNotifier(db, TWILIO_SID, TWILIO_TOKEN, "+14155238886", session=session)
    out = n.send("+919800000003", "Payment reminder", "Hi Kabir")
    call = session.calls[0]
    assert call.url == f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    assert call.auth == (TWILIO_SID, TWILIO_TOKEN)
    assert call.data == {"From": "whatsapp:+14155238886", "To": "whatsapp:+919800000003",
                         "Body": "Payment reminder\n\nHi Kabir"}
    assert out == {"channel": "whatsapp", "adapter": "twilio", "to": "+919800000003", "status": "sent",
                   "message_sid": "SM123", "provider_status": "queued"}
    n.send("whatsapp:+919800000001", "", "Hi")  # already prefixed numbers are not double-prefixed
    assert session.calls[1].data["To"] == "whatsapp:+919800000001" and session.calls[1].data["Body"] == "Hi"


def test_twilio_failure_is_recorded_raised_and_scrubbed(db):
    n = TwilioWhatsAppNotifier(db, TWILIO_SID, TWILIO_TOKEN, "+14155238886",
                               session=Session(Resp(400, {"code": 63007, "message": "Channel not found"})))
    with pytest.raises(NotificationError, match="HTTP 400 63007: Channel not found") as e:
        n.send("+919800000003", "Reminder", "Hi")
    assert TWILIO_TOKEN not in str(e.value)
    assert db.notifications()[0]["status"] == "failed"
    assert TWILIO_TOKEN not in repr(n) + json.dumps(n.status())


def test_redact_drops_urls_quoted_by_http_libraries():
    from paypilot.integrations.base import redact

    assert redact("Max retries exceeded with url: /services/T1/B2/tok (Caused by X)", []) == \
        "Max retries exceeded with url: *** (Caused by X)"
    assert redact("404 Client Error: Not Found for URL: https://x.example/a?k=1", []) == \
        "404 Client Error: Not Found for URL: ***"
    assert redact("token abc and abcdef", ["abc", "abcdef"]) == "token *** and ***"


def test_twilio_connection_errors_hide_the_account_sid(db):
    error = requests.ConnectionError("HTTPSConnectionPool(host='api.twilio.com', port=443): Max retries exceeded with "
                                     f"url: /2010-04-01/Accounts/{TWILIO_SID}/Messages.json (Caused by NameResolutionError)")
    n = TwilioWhatsAppNotifier(db, TWILIO_SID, TWILIO_TOKEN, "+14155238886", session=Session(error))
    with pytest.raises(NotificationError, match="ConnectionError") as e:
        n.send("+919800000003", "Reminder", "Hi")
    assert TWILIO_SID not in str(e.value) and TWILIO_SID not in json.dumps(db.notifications())


# ================================================================= exporters
def test_csv_neutralises_formulas(mock):
    evil = mock.create_and_send_invoice("=HYPERLINK(\"http://evil\")", "+cmd@evil.example", 100, "INR",
                                        "@SUM(A1:A9)", days_from_today(5), "-2+3")
    text = CsvExporter().export(mock.list_invoices(), "Asha Design Studio")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 6 and list(rows[0]) == ["number", "id", "provider", "status", "customer_name", "customer_email",
                                                "invoice_date", "due_date", "currency", "amount", "paid_amount",
                                                "refunded_amount", "outstanding", "description", "note"]
    row = next(r for r in rows if r["id"] == evil["id"])
    assert row["customer_name"] == "'=HYPERLINK(\"http://evil\")" and row["customer_email"] == "'+cmd@evil.example"
    assert row["description"] == "'@SUM(A1:A9)" and row["note"] == "'-2+3"
    assert row["amount"] == "100.00" and row["outstanding"] == "100.00"
    priya = next(r for r in rows if r["number"] == "0001")
    assert (priya["customer_name"], priya["paid_amount"], priya["outstanding"]) == ("Priya Nair", "12000.00", "0.00")
    assert [neutralise(v) for v in ("\tx", "\rx", "ok", "", None, 5)] == ["'\tx", "'\rx", "ok", "", "", "5"]


def test_json_export(mock):
    data = json.loads(JsonExporter().export(mock.list_invoices(), "Asha Design Studio"))
    assert data["business"] == "Asha Design Studio" and data["count"] == 5
    assert data["invoices"][0]["amount"] == 12000.0 and data["invoices"][1]["outstanding"] == 3500.0


def test_tally_xml_parses_with_one_voucher_per_non_draft_invoice(mock, db):
    invoices = mock.list_invoices()
    draft = {**invoices[0], "id": "INV2-DRAFT", "number": "0099", "status": "DRAFT"}
    tricky = mock.create_and_send_invoice("Tom & Jerry <Ltd>", "tj@example.com", 1500.5, "INR",
                                          'Logo "v2" & <banner>', days_from_today(5))
    xml = TallyXmlExporter().export(mock.list_invoices() + [draft], "Asha & Co Design Studio")
    root = ET.fromstring(xml)  # well-formed despite & < > " in the data
    assert root.tag == "ENVELOPE" and root.findtext("HEADER/TALLYREQUEST") == "Import Data"
    assert root.findtext("BODY/IMPORTDATA/REQUESTDESC/STATICVARIABLES/SVCURRENTCOMPANY") == "Asha & Co Design Studio"
    vouchers = root.findall(".//VOUCHER")
    assert len(vouchers) == 6  # 5 seeded + 1 new; the draft is skipped
    assert all(v.get("VCHTYPE") == "Sales" and v.findtext("VOUCHERTYPENAME") == "Sales" for v in vouchers)
    v = next(v for v in vouchers if v.findtext("REFERENCE") == tricky["id"])
    assert v.findtext("PARTYLEDGERNAME") == "Tom & Jerry <Ltd>" and 'Logo "v2" & <banner>' in v.findtext("NARRATION")
    entries = [(e.findtext("LEDGERNAME"), e.findtext("ISDEEMEDPOSITIVE"), e.findtext("AMOUNT"))
               for e in v.findall("ALLLEDGERENTRIES.LIST")]
    assert entries == [("Tom & Jerry <Ltd>", "Yes", "-1500.50"), ("Sales", "No", "1500.50")]  # debit party, credit sales
    first = vouchers[0]
    assert first.findtext("VOUCHERNUMBER") == "0001" and first.findtext("DATE") == invoices[0]["invoice_date"].replace("-", "")


def test_exporters_registry():
    assert set(EXPORTERS) == {"csv", "json", "tally"}
    assert [(e.format, e.extension) for e in EXPORTERS.values()] == [("csv", "csv"), ("json", "json"), ("tally", "xml")]


# ======================================================================= hub
def test_demo_hub_uses_simulators_everywhere_and_status_has_no_secrets(db):
    settings = Settings.from_env({**LIVE_ENV, "PAYPILOT_DEMO_MODE": "true"})  # keys set, but demo mode wins
    hub = IntegrationHub(settings, db)
    assert isinstance(hub.payments, MockBackend) and hub.payments.provider == "razorpay"
    assert all(isinstance(n, SimulatedNotifier) for n in hub.notifiers.values())
    status = hub.status()
    assert status["demo_mode"] is True and status["webhook_transport"] == "simulate"
    assert status["payments"]["label"] == "Razorpay simulator"
    assert {k: v["mode"] for k, v in status["providers"].items()} == {"paypal": "simulator", "razorpay": "simulator"}
    assert {k: v["adapter"] for k, v in status["notifiers"].items()} == \
        {"email": "simulator", "slack": "simulator", "whatsapp": "simulator"}
    assert [e["format"] for e in status["exporters"]] == ["csv", "json", "tally"]
    dumped = json.dumps(status)
    assert not any(secret in dumped for secret in SECRET_VALUES)


def test_live_hub_uses_real_adapters_and_status_still_has_no_secrets(db):
    hub = IntegrationHub(Settings.from_env(LIVE_ENV), db)
    assert type(hub.payments).__name__ == "RazorpayBackend"
    assert isinstance(hub.notifiers["email"], SmtpEmailNotifier)
    assert isinstance(hub.notifiers["slack"], SlackWebhookNotifier)
    assert isinstance(hub.notifiers["whatsapp"], TwilioWhatsAppNotifier)
    status = hub.status()
    assert status["payments"]["label"] == "Razorpay test mode" and status["providers"]["paypal"]["mode"] == "sandbox"
    assert status["notifiers"]["email"] == {"channel": "email", "adapter": "smtp", "simulated": False,
                                            "host": "smtp.example.com", "port": 587, "sender": "asha@studio.example"}
    assert status["notifiers"]["whatsapp"]["from"] == "+14155238886"
    dumped = json.dumps(status) + repr(hub.payments) + "".join(repr(n) for n in hub.notifiers.values())
    assert not any(secret in dumped for secret in SECRET_VALUES)
    assert TWILIO_SID not in json.dumps(status)  # account ids are not shown either


def test_partially_configured_channels_stay_simulated(db):
    hub = IntegrationHub(Settings.from_env({"PAYPAL_CLIENT_ID": "c", "PAYPAL_CLIENT_SECRET": "s",
                                            "SMTP_HOST": "smtp.example.com", "TWILIO_ACCOUNT_SID": TWILIO_SID}), db)
    assert {ch: n.adapter for ch, n in hub.notifiers.items()} == \
        {"email": "simulator", "slack": "simulator", "whatsapp": "simulator"}  # SMTP has no sender; Twilio no token


def test_hub_provider_returns_unseeded_adapters_for_other_providers(db):
    hub = IntegrationHub(Settings.from_env({}), db)
    assert hub.provider("paypal") is hub.payments and len(hub.payments.list_invoices()) == 5
    rzp = hub.provider("Razorpay")
    assert isinstance(rzp, MockBackend) and rzp.provider == "razorpay" and rzp.list_invoices() == []
    assert hub.provider("razorpay") is rzp and hub.status()["providers"]["razorpay"]["simulated"] is True
    with pytest.raises(ValueError):
        hub.provider("stripe")


# =============================================================== app context
def test_build_context_wires_everything(tmp_path):
    ctx = build_context(Settings.from_env({}), db_path=str(tmp_path / "p.db"))
    assert ctx.service.backend is ctx.hub.payments and ctx.service.hub is ctx.hub
    assert isinstance(ctx.agent, RuleAgent) and ctx.agent.service is ctx.service
    assert ctx.db.path.endswith("p.db") and len(ctx.service.backend.list_invoices()) == 5
    shared = build_context(Settings.from_env({}), db=ctx.db)
    assert shared.db is ctx.db


def test_build_context_with_injected_claude_client():
    fake = NS(messages=NS(create=lambda **kw: NS(content=[NS(type="text", text="Hello from fake Claude")])))
    ctx = build_context(Settings.from_env({}), db_path=":memory:", agent_client=fake)
    assert isinstance(ctx.agent, ClaudeAgent) and ctx.agent.respond("hi").text == "Hello from fake Claude"


def test_reminders_go_through_the_hub_notifier():
    ctx = build_context(Settings.from_env({}), db_path=":memory:")
    a = ctx.service.propose("propose_reminder", {"invoice_id": "0002", "note": "Hi Kabir", "channel": "whatsapp"})
    out = ctx.service.approve(a["action_id"])
    assert out["ok"] and out["result"]["delivery"] == "simulated" and out["result"]["to"] == "+919800000003"
    [row] = [n for n in ctx.db.notifications() if n["channel"] == "whatsapp"]  # owner Slack pings are separate
    assert (row["channel"], row["recipient"], row["status"], row["body"]) == \
        ("whatsapp", "+919800000003", "simulated", "Hi Kabir")
    assert row["subject"].startswith("Payment reminder: invoice #0002")


def test_failed_notification_marks_the_action_failed(db, mock):
    hub = NS(payments=mock, notifiers={"email": TwilioWhatsAppNotifier(db, TWILIO_SID, TWILIO_TOKEN, "+1",
                                                                       session=Session(Resp(503)))})
    svc = PayPilotService(db, hub=hub)
    a = svc.propose("propose_reminder", {"invoice_id": "0002", "note": "Hi", "channel": "email"})
    out = svc.approve(a["action_id"])
    assert not out["ok"] and out["action"]["status"] == "failed" and "HTTP 503" in out["error"]
    assert db.notifications()[0]["status"] == "failed" and TWILIO_TOKEN not in json.dumps(svc.db.audit())


def test_service_needs_a_backend_or_hub(db):
    with pytest.raises(ValueError):
        PayPilotService(db)
    assert inv_by_number(PayPilotService(db, MockBackend(db)), "0001")["customer_name"] == "Priya Nair"
