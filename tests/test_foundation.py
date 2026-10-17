"""Settings, storage and the PayPal simulator. (The sandbox adapter is tested in test_agent_and_sandbox.py.)"""
from datetime import date, timedelta

import pytest

from conftest import inv_by_number
from paypilot.backends import INJECTED_NOTE, BackendError, MockBackend, make_backend, round_money
from paypilot.config import DEFAULT_POLICY, Settings

KEYS = {"PAYPAL_CLIENT_ID": "cid-123", "PAYPAL_CLIENT_SECRET": "sec-456"}


# ------------------------------------------------------------------- settings
def test_settings_defaults_are_offline_demo():
    s = Settings.from_env({})
    assert (s.db_path, s.business_name, s.currency, s.model) == \
        ("data/paypilot.db", "Asha Design Studio", "INR", "claude-sonnet-5-5")
    assert not s.has_real_keys and s.demo_mode
    assert s.backend_name == "mock" and s.agent_name == "rules"


def test_settings_real_keys_switch_modes_and_demo_flag_overrides():
    s = Settings.from_env({**KEYS, "ANTHROPIC_API_KEY": "sk-x"})
    assert s.has_real_keys and not s.demo_mode
    assert s.backend_name == "sandbox" and s.agent_name == "llm"
    forced = Settings.from_env({**KEYS, "ANTHROPIC_API_KEY": "sk-x", "PAYPILOT_DEMO_MODE": "true"})
    assert forced.demo_mode and forced.backend_name == "mock" and forced.agent_name == "rules"
    # one PayPal key is a "real key" but not enough for the sandbox
    half = Settings.from_env({"PAYPAL_CLIENT_ID": "cid"})
    assert half.has_real_keys and not half.demo_mode and half.backend_name == "mock"


def test_settings_repr_hides_secrets():
    text = repr(Settings.from_env({**KEYS, "ANTHROPIC_API_KEY": "sk-secret", "PAYPAL_WEBHOOK_ID": "wh-789"}))
    assert "sec-456" not in text and "sk-secret" not in text and "cid-123" not in text and "wh-789" not in text


# ------------------------------------------------------------------- database
def test_seed_customers_and_policy(db):
    names = {c["name"]: c for c in db.customers()}
    assert set(names) == {"Rahul Mehta", "Priya Nair", "Kabir Singh", "Neha Joshi", "Unverified Lead"}
    assert names["Kabir Singh"]["phone"].startswith("+91980000000")
    assert names["Neha Joshi"]["phone"] is None and names["Neha Joshi"]["approved"] is True
    assert names["Unverified Lead"]["approved"] is False
    assert db.get_policy() == DEFAULT_POLICY


def test_policy_is_merged_over_defaults(db):
    db.set_setting("policy", {"max_invoice_amount": 3000})
    pol = db.get_policy()
    assert pol["max_invoice_amount"] == 3000 and pol["daily_refund_limit"] == 15000.0


def test_find_customer(db):
    assert db.find_customer("rahul")["name"] == "Rahul Mehta"
    assert db.find_customer("PRIYA.NAIR@example.com")["name"] == "Priya Nair"
    assert db.find_customer("  Neha   Joshi ")["name"] == "Neha Joshi"
    assert db.find_customer("Bob") is None and db.find_customer("") is None
    db.upsert_customer("Rahul Verma", "rv@example.com")
    assert db.find_customer("Rahul") is None  # ambiguous first name
    assert db.find_customer("Rahul Verma")["email"] == "rv@example.com"


def test_actions_and_audit_round_trip_json(db):
    aid = db.add_pending("refund", {"invoice_id": "X", "amount": 10.0}, {"Amount": "INR 10.00"}, "why", ["careful"])
    a = db.get_action(aid)
    assert a["params"]["amount"] == 10.0 and a["warnings"] == ["careful"] and a["status"] == "pending"
    db.set_action_status(aid, "executed", {"refund_id": "RF-1"})
    assert db.get_action(aid)["result"] == {"refund_id": "RF-1"} and db.actions("pending") == []
    db.log("user", "message", {"text": "hi"}, aid)
    entry = db.audit(1)[0]
    assert entry["detail"] == {"text": "hi"} and entry["action_id"] == aid
    assert entry["ts"][:10] == date.today().isoformat()  # server-local date


def test_file_database_uses_wal(tmp_path):
    from paypilot.db import Database

    d = Database(str(tmp_path / "sub" / "p.db"))
    assert d._all("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
    d.close()


# ------------------------------------------------------------------ simulator
def test_mock_seed_matches_the_demo_data(mock):
    invs = mock.list_invoices()
    today = date.today()
    got = [(i["number"], i["customer_name"], i["amount"], i["status"]) for i in invs]
    assert got == [("0001", "Priya Nair", 12000.0, "PAID"), ("0002", "Kabir Singh", 3500.0, "SENT"),
                   ("0003", "Neha Joshi", 8000.0, "SENT"), ("0004", "Rahul Mehta", 2500.0, "PAID"),
                   ("0005", "Kabir Singh", 6000.0, "SENT")]
    assert invs[1]["due_date"] == (today - timedelta(days=2)).isoformat()
    assert invs[2]["due_date"] == (today + timedelta(days=8)).isoformat()
    assert invs[4]["note"] == INJECTED_NOTE
    assert all(i["id"].startswith("INV2-") and len(i["id"]) == 24 and i["provider"] == "paypal" for i in invs)
    assert set(invs[0]) == {"id", "number", "status", "customer_name", "customer_email", "amount", "currency",
                            "description", "invoice_date", "due_date", "paid_amount", "refunded_amount",
                            "payment_id", "note", "provider"}


def test_mock_seeds_only_once(db):
    MockBackend(db)
    assert len(MockBackend(db).list_invoices()) == 5


def test_mock_refund_rules(mock):
    priya = inv_by_number(mock, "0001")["id"]
    assert mock.refund(priya, 2000, "cut short")["status"] == "COMPLETED"
    assert inv_by_number(mock, "0001")["status"] == "PARTIALLY_REFUNDED"
    with pytest.raises(BackendError, match="refundable balance"):
        mock.refund(priya, 10000.01, "too much")
    mock.refund(priya, 10000, "rest")
    inv = inv_by_number(mock, "0001")
    assert inv["status"] == "REFUNDED" and inv["refunded_amount"] == 12000.0
    with pytest.raises(BackendError):
        mock.refund(inv_by_number(mock, "0003")["id"], 1, "unpaid invoice has nothing to refund")


def test_mock_create_remind_and_record_payment(mock):
    due = (date.today() + timedelta(days=4)).isoformat()
    inv = mock.create_and_send_invoice("Rahul Mehta", "rahul.mehta@example.com", 4000, "INR", "Logo design", due)
    assert (inv["number"], inv["status"], inv["amount"], inv["due_date"]) == ("0006", "SENT", 4000.0, due)
    assert mock.send_reminder(inv["id"], "Hi")["reminded"]
    assert mock.record_payment(inv["id"], 1500)["status"] == "PARTIALLY_PAID"
    assert mock.record_payment(inv["id"])["status"] == "PAID"
    with pytest.raises(BackendError, match="reminders only apply"):
        mock.send_reminder(inv["id"], "Hi again")
    with pytest.raises(BackendError, match="not found"):
        mock.get_invoice("INV2-NOPE")


def test_round_money_is_decimal_safe():
    assert round_money(2.675) == 2.68 and round_money(0.1 + 0.2) == 0.3 and round_money(None) == 0.0


def test_make_backend_picks_mock_without_keys(db):
    assert isinstance(make_backend(Settings.from_env({}), db), MockBackend)


def test_vercel_defaults_the_database_to_the_writable_temp_dir():
    import tempfile

    assert Settings.from_env({}).db_path == "data/paypilot.db"
    assert Settings.from_env({"VERCEL": "1"}).db_path.startswith(tempfile.gettempdir())
    assert Settings.from_env({"VERCEL": "1", "PAYPILOT_DB": "/data/p.db"}).db_path == "/data/p.db"
