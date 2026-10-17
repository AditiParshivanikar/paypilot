"""Functional tests: do plain-English commands produce the right actions, and do approvals do the right thing?"""
import calendar
from datetime import date, timedelta

import pytest

from conftest import days_from_today, inv_by_number, next_weekday
from paypilot.parsing import parse_amount, parse_due_date

FRIDAY = 4


# -------------------------------------------------------------------- parsing
def test_due_dates_relative_to_today():
    today = date.today()
    iso = lambda d: d.isoformat()  # noqa: E731
    assert parse_due_date("Friday") == iso(next_weekday(FRIDAY))
    assert parse_due_date(today.strftime("%A")) == iso(today + timedelta(days=7))  # same weekday -> next week's
    assert parse_due_date("today") == iso(today)
    assert parse_due_date("tomorrow") == iso(today + timedelta(days=1))
    assert parse_due_date("in 10 days") == iso(today + timedelta(days=10))
    assert parse_due_date("in 2 weeks") == iso(today + timedelta(days=14))
    assert parse_due_date("next week") == iso(today + timedelta(days=7))
    assert parse_due_date("end of month") == iso(today.replace(day=calendar.monthrange(today.year, today.month)[1]))
    assert parse_due_date(None) == parse_due_date("sometime") == iso(today + timedelta(days=14))
    soon = today + timedelta(days=30)
    assert parse_due_date(soon.strftime("%d %b")) == iso(soon)  # "12 Nov"
    assert parse_due_date(soon.strftime("%b %d")) == iso(soon)  # "Nov 12"
    assert parse_due_date(iso(soon)) == iso(soon)


def test_day_month_rolls_to_next_year_once_passed():
    today = date.today()
    passed = today - timedelta(days=10)
    if (passed.month, passed.day) == (2, 29):  # no 29 Feb next year
        passed -= timedelta(days=1)
    assert parse_due_date(passed.strftime("%d %b")) == passed.replace(year=passed.year + 1).isoformat()


@pytest.mark.parametrize("ref,text,expected", [  # fixed reference dates for calendar edge cases
    (date(2026, 10, 16), "Friday", "2026-10-23"),        # on a Friday, "Friday" means next week's
    (date(2026, 1, 31), "in 1 month", "2026-02-28"),     # month arithmetic clamps to the month's end
    (date(2028, 2, 10), "end of month", "2028-02-29"),   # leap year
    (date(2026, 12, 20), "5 Jan", "2027-01-05"),         # rolls over the year end
    (date(2026, 10, 13), "31 Feb", "2026-10-27"),        # impossible date -> default 14 days
    (date(2026, 10, 13), "monthly retainer", "2026-10-27"),  # "mon" inside a word is not Monday
])
def test_due_date_calendar_edges(ref, text, expected):
    assert parse_due_date(text, ref) == expected


@pytest.mark.parametrize("text,expected", [
    ("₹4,000", (4000.0, "INR")), ("Rs 500", (500.0, "INR")), ("Rs. 750", (750.0, "INR")),
    ("INR 1,20,000", (120000.0, "INR")), ("$120.50", (120.5, "USD")), ("5k", (5000.0, "INR")),
    ("300 usd", (300.0, "USD")), ("1.5 lakh", (150000.0, "INR")), ("2,500 rupees", (2500.0, "INR")),
    ("no amount here", (None, "INR")),
])
def test_amount_parsing(text, expected):
    assert parse_amount(text) == expected


# ------------------------------------------------------------- the PDF example
PDF_EXAMPLE = "Invoice Rahul for ₹4,000 for logo design, due Friday"


def test_pdf_example_drafts_exactly_one_invoice_and_sends_nothing(agent, svc):
    before = svc.backend.list_invoices()
    reply = agent.respond(PDF_EXAMPLE)
    pending = svc.db.actions("pending")
    assert len(pending) == 1 and pending[0]["action"] == "create_invoice"
    p = pending[0]["params"]
    assert p["customer_name"] == "Rahul Mehta"
    assert p["customer_email"] == "rahul.mehta@example.com"  # registered email
    assert (p["amount"], p["currency"], p["description"]) == (4000.0, "INR", "Logo design")
    assert p["due_date"] == next_weekday(FRIDAY).isoformat()
    assert "Approval inbox" in reply.text
    assert svc.backend.list_invoices() == before  # nothing sent yet


def test_approve_sends_the_invoice(agent, svc):
    agent.respond(PDF_EXAMPLE)
    action_id = svc.db.actions("pending")[0]["id"]
    out = svc.approve(action_id)
    assert out["ok"] and svc.db.get_action(action_id)["status"] == "executed"
    sent = out["result"]
    assert (sent["number"], sent["status"], sent["customer_email"], sent["amount"], sent["due_date"]) == \
        ("0006", "SENT", "rahul.mehta@example.com", 4000.0, next_weekday(FRIDAY).isoformat())
    assert inv_by_number(svc, "0006")["description"] == "Logo design"


def test_reject_sends_nothing(agent, svc):
    before = svc.backend.list_invoices()
    agent.respond("Invoice Priya for ₹1,500 for a poster, due tomorrow")
    action_id = svc.db.actions("pending")[0]["id"]
    svc.reject(action_id)
    assert svc.db.get_action(action_id)["status"] == "rejected"
    assert svc.backend.list_invoices() == before and svc.db.actions("pending") == []


# ------------------------------------------------------------------ status
def test_who_hasnt_paid_lists_the_right_customers_and_total(agent, svc):
    reply = agent.respond("Who hasn't paid me?")
    assert "3 unpaid invoice(s)" in reply.text and "1 overdue" in reply.text
    assert "INR 17,500.00" in reply.text
    assert [(r["Invoice"], r["Customer"], r["Days overdue"]) for r in reply.table] == \
        [("#0002", "Kabir Singh", 2), ("#0005", "Kabir Singh", 0), ("#0003", "Neha Joshi", 0)]
    assert svc.db.actions("pending") == []  # read-only


# --------------------------------------------------------------- reminders
def test_reminder_flow(agent, svc, db):
    reply = agent.respond("Remind Neha")
    [a] = svc.db.actions("pending")
    neha = inv_by_number(svc, "0003")
    assert a["action"] == "send_reminder" and a["params"]["invoice_id"] == neha["id"]
    assert "Neha" in a["params"]["note"] and "Approval inbox" in reply.text
    assert db.mock_get(neha["id"])["_reminders"] == []  # not sent before approval
    out = svc.approve(a["id"])
    assert out["ok"] and out["result"]["reminded"]
    assert len(db.mock_get(neha["id"])["_reminders"]) == 1


def test_remind_everyone_overdue_targets_only_overdue_invoices(agent, svc):
    agent.respond("Remind everyone who is overdue")
    pending = svc.db.actions("pending")
    assert [a["params"]["invoice_id"] for a in pending] == [inv_by_number(svc, "0002")["id"]]
    assert inv_by_number(svc, "0002")["due_date"] == days_from_today(-2)


# ----------------------------------------------------------------- refunds
def test_partial_refund(agent, svc):
    agent.respond("Refund Priya ₹2,000 because the project was cut short")
    [a] = svc.db.actions("pending")
    assert a["params"] == {"invoice_id": inv_by_number(svc, "0001")["id"], "amount": 2000.0,
                           "reason": "the project was cut short"}
    assert svc.approve(a["id"])["ok"]
    inv = inv_by_number(svc, "0001")
    assert (inv["refunded_amount"], inv["status"]) == (2000.0, "PARTIALLY_REFUNDED")


def test_default_refund_is_the_full_refundable_amount(agent, svc):
    agent.respond("Refund Rahul")
    [a] = svc.db.actions("pending")
    assert a["params"]["amount"] == 2500.0
    assert svc.approve(a["id"])["ok"]
    inv = inv_by_number(svc, "0004")
    assert (inv["refunded_amount"], inv["status"]) == (2500.0, "REFUNDED")


# ------------------------------------------------------------------- audit
def test_audit_log_records_every_step_in_order(agent, svc):
    agent.respond(PDF_EXAMPLE)
    svc.approve(svc.db.actions("pending")[0]["id"])
    events = [(e["actor"], e["event"]) for e in reversed(svc.db.audit())]
    assert events == [
        ("user", "message"),
        ("agent", "proposed create_invoice"),
        ("policy", "queued create_invoice for approval"),
        ("agent", "reply"),
        ("user", "approved create_invoice"),
        ("executor", "executed create_invoice via mock"),
    ]
    action_id = svc.db.actions()[0]["id"]
    assert {e["action_id"] for e in svc.db.audit() if e["event"] != "reply" and e["actor"] in ("user", "executor")
            and e["event"] != "message"} == {action_id}
