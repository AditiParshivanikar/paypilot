"""Unit tests for the tool layer (service.py) and the rule agent's phrasings, beyond the functional flows."""
from datetime import date

from conftest import days_from_today, inv_by_number
from paypilot.agent import _ToolRunner
from paypilot.backends import INJECTED_NOTE
from paypilot.service import TOOL_SPECS, days_overdue


# ----------------------------------------------------------------- tool specs
def test_tool_specs_match_the_design():
    assert [t["name"] for t in TOOL_SPECS] == ["list_invoices", "unpaid_summary", "get_invoice", "list_customers",
                                               "propose_invoice", "propose_reminder", "propose_refund"]
    props = {t["name"]: set(t["input_schema"]["properties"]) for t in TOOL_SPECS}
    assert props["list_invoices"] == {"status", "customer"} and props["get_invoice"] == {"invoice_id"}
    assert props["propose_invoice"] == {"customer", "amount", "currency", "description", "due_date", "note", "rationale"}
    assert props["propose_reminder"] == {"invoice_id", "note", "channel", "rationale"}
    assert props["propose_refund"] == {"invoice_id", "amount", "reason", "rationale"}
    required = {t["name"]: set(t["input_schema"].get("required", [])) for t in TOOL_SPECS}
    assert required["propose_invoice"] == {"customer", "amount", "currency", "description", "due_date", "rationale"}


# ----------------------------------------------------------------- read tools
def test_unpaid_summary_rows_and_totals(svc):
    res, tainted = svc.run_read_tool("unpaid_summary", {})
    assert not tainted and res["count"] == 3 and res["overdue_count"] == 1
    assert res["totals_outstanding"] == {"INR": 17500.0}
    first = res["unpaid"][0]
    assert set(first) == {"invoice_id", "number", "customer", "description", "currency", "outstanding",
                          "due_date", "days_overdue", "status", "overdue"}
    assert (first["number"], first["days_overdue"], first["overdue"], first["outstanding"]) == ("0002", 2, True, 3500.0)


def test_third_party_text_is_wrapped_and_taints(svc):
    res, tainted = svc.run_read_tool("get_invoice", {"invoice_id": "#0005"})
    assert tainted and res["invoice"]["security_warning"]
    assert res["invoice"]["note"] == {"untrusted_text": INJECTED_NOTE}
    assert res["invoice"]["description"] == {"untrusted_text": "Social media kit"}
    clean, tainted = svc.run_read_tool("get_invoice", {"invoice_id": "0002"})
    assert not tainted and "security_warning" not in clean["invoice"]
    listed, tainted = svc.run_read_tool("list_invoices", {"customer": "kabir", "status": "sent"})
    assert tainted and [i["number"] for i in listed["invoices"]] == ["0002", "0005"]


def test_invoice_references_and_customer_list(svc):
    real_id = inv_by_number(svc, "0003")["id"]
    assert svc.resolve_invoice_id("0003") == svc.resolve_invoice_id("#0003") == svc.resolve_invoice_id(real_id) == real_id
    assert "not found" in svc.run_read_tool("get_invoice", {"invoice_id": "9999"})[0]["error"]
    customers = svc.run_read_tool("list_customers")[0]["customers"]
    assert {"name": "Unverified Lead", "approved": False} in customers
    assert all(set(c) == {"name", "approved"} for c in customers)  # no addresses for the model


def test_days_overdue():
    today = date.today()
    assert days_overdue({"due_date": days_from_today(-2)}) == 2
    assert days_overdue({"due_date": days_from_today(5)}) == 0
    assert days_overdue({"due_date": None}, today) == 0


# ------------------------------------------------------------------- previews
def test_preview_fields(svc):
    inv = svc.propose("propose_invoice", {"customer": "rahul", "amount": 4000, "currency": "inr",
                                          "description": "Logo design", "due_date": days_from_today(3), "rationale": "x"})
    assert list(inv["preview"]) == ["Invoice", "To", "Amount", "Item", "Due"]
    assert inv["preview"]["To"] == "Rahul Mehta <rahul.mehta@example.com>" and inv["preview"]["Amount"] == "INR 4,000.00"
    rem = svc.propose("propose_reminder", {"invoice_id": "0002", "note": "Hi", "channel": "email", "rationale": "x"})
    assert list(rem["preview"]) == ["Invoice", "To", "Via", "Outstanding", "Due", "Message"]
    assert rem["preview"]["Via"] == "Email to kabir@cafe-kabir.example"
    ref = svc.propose("propose_refund", {"invoice_id": "0001", "amount": 2000, "reason": "cut short", "rationale": "x"})
    assert list(ref["preview"]) == ["Invoice", "Refund to", "Amount", "Reason"]
    assert ref["preview"]["Amount"] == "INR 2,000.00 of 12,000.00 paid (12,000.00 refundable)"
    stored = svc.db.get_action(inv["action_id"])
    assert stored["rationale"] == "x" and "rationale" not in stored["params"]


# ------------------------------------------------------------- rule agent
def test_invoice_phrasings(agent, svc):
    agent.respond("Invoice Rahul ₹1,500 for a poster, send it to rahul.new@gmail.com")
    a = svc.db.actions("pending")[0]
    assert a["params"]["customer_email"] == "rahul.mehta@example.com" and a["params"]["description"] == "Poster"
    assert any("Ignored the address" in w for w in a["warnings"])
    agent.respond("Invoice Priya $300 for a banner due 30 Dec")
    p = svc.db.actions("pending")[0]["params"]
    assert (p["amount"], p["currency"], p["due_date"][5:]) == (300.0, "USD", "12-30")


def test_unpaid_phrasings(agent):
    for phrase in ("Who hasn't paid me?", "show unpaid invoices", "who owes me money", "what's overdue?"):
        reply = agent.respond(phrase)
        assert "INR 17,500.00" in reply.text, phrase
        assert {r["Customer"] for r in reply.table} == {"Kabir Singh", "Neha Joshi"}


def test_reminder_channels(agent, svc):
    agent.respond("Remind Neha by email")
    neha = svc.db.actions("pending")[0]
    assert neha["params"]["channel"] == "email" and neha["params"]["invoice_id"] == inv_by_number(svc, "0003")["id"]
    reply = agent.respond("remind kabir on WhatsApp")  # Kabir has #0002 and #0005 open
    whatsapp = [a for a in svc.db.actions("pending") if a["params"]["channel"] == "whatsapp"]
    assert len(whatsapp) == 2 and whatsapp[0]["preview"]["Via"] == "WhatsApp to +919800000003"
    assert "couldn't find" in agent.respond("Remind Priya").text  # nothing open for Priya
    assert "Nobody is overdue" not in reply.text


def test_show_invoice_and_all(agent):
    r = agent.respond("Show invoice 0005")
    assert r.tainted and "ignored it" in r.text and "Refunds are blocked" in r.text and "#0005" in r.text
    r = agent.respond("show invoice 2")
    assert not r.tainted and "#0002" in r.text
    r = agent.respond("Show all invoices")
    assert r.tainted and r.text.count("\n- #") == 5


def test_help_and_unknown(agent):
    assert "I can help with" in agent.respond("help").text
    assert agent.respond("make me a sandwich").text.startswith("Sorry")


def test_every_turn_logs_message_and_reply(agent, svc):
    agent.respond("Who hasn't paid me?")
    assert [(e["actor"], e["event"]) for e in reversed(svc.db.audit())] == [("user", "message"), ("agent", "reply")]


def test_tool_runner_refuses_unknown_tools(svc):
    run = _ToolRunner(svc)
    assert "not available" in run("transfer_money", {"to": "x@y.z"})["error"]
    assert svc.db.audit(1)[0]["event"] == "refused unknown tool" and svc.db.actions() == []


def test_reminder_replies_do_not_repeat_the_unpaid_table(agent):
    assert agent.respond("Who hasn't paid me?").table  # the question that asks for the table gets it
    reply = agent.respond("Remind Neha")
    assert "Drafted and waiting" in reply.text and reply.table is None  # the internal lookup stays internal
    assert agent.respond("Remind everyone who is overdue").table is None
