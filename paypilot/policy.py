"""The safety gate: deterministic rules every proposed action must pass (brief, principle 3).

The LLM never executes anything. It can only propose; this engine either blocks the proposal (with
reasons) or resolves it into the exact parameters and preview a human will approve. Addresses and refund
targets are always resolved here from the customer list and the invoice, never taken from the request.
The same check runs again at approval time, against the state at that moment.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from .backends import (OPEN_STATUSES, PROVIDER_LABELS, REFUNDABLE_STATUSES, BackendError, PaymentsBackend,
                       outstanding, refundable, round_money)
from .db import Database

MAX_DESCRIPTION = 200
MAX_DUE_DAYS = 365
NEAR_CAP = 0.8

# ------------------------------------------------------------------ injection scanner
INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("ignore previous instructions",
     r"\bignore\s+(?:all\s+|any\s+)?(?:of\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|preceding)\s+"
     r"(?:instructions?|rules|messages|prompts?|directions)"),
    ("disregard instructions",
     r"\bdisregard\s+(?:all\s+|any\s+)?(?:the\s+|your\s+|these\s+|those\s+)?(?:previous|prior|above|earlier|"
     r"instructions?|rules|policy|polic(?:y|ies))"),
    ("role marker at line start", r"(?im)^[\s>*\-\"']*(?:\[\s*(?:system|assistant|developer)\s*\]|"
                                  r"(?:system|assistant|developer)\s*:)"),
    ("persona switch", r"\byou\s+are\s+now\b"),
    ("override the safety rules",
     r"\b(?:override|bypass|disable|turn\s+off|switch\s+off|skip|circumvent)\s+(?:the\s+|all\s+|any\s+|your\s+)?"
     r"(?:safety\s+|spending\s+)?(?:polic(?:y|ies)|safety|limits?|caps?|approvals?|rules|gate|checks?|guardrails?)"),
    ("auto-approve", r"\bauto[-\s]?approv"),
    ("without asking", r"\bwithout\s+(?:asking|approval|confirmation|permission|checking)"),
    ("refund to an address", r"\brefund\w*\b.{0,60}?\bto\b\s*\S*@\S+"),
    ("send money to an address", r"\b(?:send|transfer|wire|pay|move)\b.{0,40}?\bto\b\s+\S+@\S+"),
    ("markup tag", r"<\s*/?\s*(?:script|system|instructions?)\b"),
    ("new instructions", r"\bnew\s+instructions?\b"),
    ("jailbreak", r"\bjailbreak"),
]
_COMPILED = [(label, re.compile(p, re.IGNORECASE | re.DOTALL)) for label, p in INJECTION_PATTERNS]
_ZERO_WIDTH = {"​", "‌", "‍", "‎", "‏", "⁠", "﻿"}


def scan_for_injection(text: str | None) -> list[str]:
    """Human-readable findings if ``text`` looks like an injected instruction; [] for normal text."""
    if not text:
        return []
    findings = []
    if any(ch in _ZERO_WIDTH or unicodedata.category(ch) == "Cf" for ch in text):
        findings.append("hidden zero-width or formatting characters")
    norm = unicodedata.normalize("NFKC", "".join(ch for ch in text if unicodedata.category(ch) != "Cf"))
    for label, pat in _COMPILED:
        if m := pat.search(norm):
            findings.append(f"{label}: {m[0].strip()[:60]!r}")
    return findings


def money(amount: float, currency: str) -> str:
    return f"{currency} {round_money(amount):,.2f}"


# ------------------------------------------------------------------------- engine
@dataclass
class Decision:
    action: str
    allowed: bool = False
    reasons: list[str] = field(default_factory=list)   # why it is blocked (empty when allowed)
    warnings: list[str] = field(default_factory=list)  # shown to the approver
    params: dict = field(default_factory=dict)         # resolved server-side; what will actually execute
    preview: dict = field(default_factory=dict)        # what the human sees before approving


class PolicyEngine:
    def __init__(self, db: Database, backend: PaymentsBackend):
        self.db = db
        self.backend = backend

    @property
    def policy(self) -> dict:
        return self.db.get_policy()

    def evaluate(self, action: str, params: dict, context_tainted: bool = False) -> Decision:
        d = Decision(action=action)
        pol = self.policy
        if action not in pol["allowed_actions"]:
            d.reasons.append(f"Action '{action}' is not on the action allowlist.")
            return d
        check = getattr(self, f"_check_{action}", None)
        if check is None:  # allowlisted but no rule written for it: blocked by default
            d.reasons.append(f"No safety rule exists for '{action}', so it is blocked by default.")
            return d
        check(dict(params or {}), d, pol)
        if context_tainted:
            if action == "refund":
                d.reasons.append("Text containing suspected injected instructions was read in this turn, so refunds "
                                 "are blocked. Ask again in a new message if you really want this refund.")
            else:
                d.warnings.append("The agent read text containing suspected injected instructions while preparing "
                                  "this. Check that it matches what you asked for.")
        d.allowed = not d.reasons
        return d

    # ---------------------------------------------------------------- helpers
    def _customer(self, name: str | None, d: Decision, pol: dict) -> dict | None:
        cust = self.db.find_customer(name)
        if not cust:
            d.reasons.append(f"'{name or ''}' is not in your customer list. Add them in Settings first.")
            return None
        if pol.get("require_approved_customer", True) and not cust["approved"]:
            d.reasons.append(f"{cust['name']} is not on the approved-customer list.")
        return cust

    def _invoice(self, invoice_id: str | None, d: Decision) -> dict | None:
        try:
            return self.backend.get_invoice(str(invoice_id or ""))
        except BackendError:
            d.reasons.append(f"Invoice {invoice_id or '(none)'} was not found.")
            return None

    @staticmethod
    def _amount(value, label: str, d: Decision) -> float | None:
        try:
            amount = round_money(value)
        except (ArithmeticError, TypeError, ValueError):
            amount = None
        if amount is None or value is None:
            d.reasons.append(f"{label} is missing or not a number.")
            return None
        if amount <= 0:
            d.reasons.append(f"{label} must be greater than zero.")
        return amount

    @staticmethod
    def _no_injection(label: str, text: str | None, d: Decision) -> None:
        if hits := scan_for_injection(text):
            d.reasons.append(f"{label} looks like an injected instruction ({'; '.join(hits)}).")

    def _executed_today(self, action: str) -> list[dict]:
        today = date.today().isoformat()  # decided_at is server-local time, so its date is the local "today"
        return [a for a in self.db.actions("executed") if a["action"] == action
                and (a.get("decided_at") or "").startswith(today)]

    def _pending_for(self, action: str, invoice_id: str) -> list[dict]:
        return [a for a in self.db.actions("pending")
                if a["action"] == action and a["params"].get("invoice_id") == invoice_id]

    # ----------------------------------------------------------------- rules
    def _check_create_invoice(self, p: dict, d: Decision, pol: dict) -> None:
        cust = self._customer(p.get("customer") or p.get("customer_name"), d, pol)
        amount = self._amount(p.get("amount"), "Amount", d)
        cap = float(pol["max_invoice_amount"])
        if amount is not None and amount > cap:
            d.reasons.append(f"Amount {amount:,.2f} exceeds the invoice cap of {cap:,.2f}.")
        elif amount is not None and amount > NEAR_CAP * cap:
            d.warnings.append(f"Amount is above {NEAR_CAP:.0%} of your invoice cap ({cap:,.2f}).")
        currency = str(p.get("currency") or "").strip().upper()
        if currency not in pol["allowed_currencies"]:
            d.reasons.append(f"Currency '{currency or '(none)'}' is not allowed "
                             f"(allowed: {', '.join(pol['allowed_currencies'])}).")
        desc = str(p.get("description") or "").strip()
        if not desc:
            d.reasons.append("A description of the work is required.")
        elif len(desc) > MAX_DESCRIPTION:
            d.reasons.append(f"Description is longer than {MAX_DESCRIPTION} characters.")
        self._no_injection("Description", desc, d)
        note = str(p.get("note") or "")
        self._no_injection("Note", note, d)
        due = None
        try:
            due = date.fromisoformat(str(p.get("due_date") or ""))
        except ValueError:
            d.reasons.append(f"Due date '{p.get('due_date') or ''}' is not a valid YYYY-MM-DD date.")
        if due is not None:
            if due < date.today():
                d.reasons.append(f"Due date {due} is in the past.")
            elif due > date.today() + timedelta(days=MAX_DUE_DAYS):
                d.reasons.append(f"Due date {due} is more than {MAX_DUE_DAYS} days away.")
        if not cust:
            return
        supplied = str(p.get("customer_email") or "").strip()
        if supplied and supplied.lower() != cust["email"].lower():
            d.warnings.append(f"Ignored the address '{supplied}' from the request; using the email on file, "
                              f"{cust['email']}.")
        d.params = {"customer_name": cust["name"], "customer_email": cust["email"], "amount": amount,
                    "currency": currency, "description": desc, "due_date": due.isoformat() if due else None,
                    "note": note}
        d.preview = {"Invoice": "New (number assigned when sent)", "To": f"{cust['name']} <{cust['email']}>",
                     "Amount": money(amount or 0, currency), "Item": desc, "Due": d.params["due_date"]}

    def _check_send_reminder(self, p: dict, d: Decision, pol: dict) -> None:
        inv = self._invoice(p.get("invoice_id"), d)
        note = str(p.get("note") or "")
        self._no_injection("Reminder note", note, d)
        channel = str(p.get("channel") or "provider").strip().lower()
        if channel not in pol["reminder_channels"]:
            d.reasons.append(f"Reminder channel '{channel}' is not allowed "
                             f"(allowed: {', '.join(pol['reminder_channels'])}).")
        if not inv:
            return
        if inv["status"] not in OPEN_STATUSES:
            d.reasons.append(f"Invoice #{inv['number']} is {inv['status']}; only unpaid invoices get reminders.")
        cust = self._customer(inv["customer_name"], d, pol)
        email = (cust or {}).get("email") or ""
        phone = (cust or {}).get("phone") or ""
        if cust and channel == "whatsapp" and not phone:
            d.reasons.append(f"WhatsApp needs a phone number on file for {cust['name']}, and there is none.")
        if cust and channel == "email" and not email:
            d.reasons.append(f"Email needs an address on file for {cust['name']}, and there is none.")
        limit = int(pol["max_reminders_per_invoice_per_day"])
        sent = [a for a in self._executed_today("send_reminder") if a["params"].get("invoice_id") == inv["id"]]
        if len(sent) >= limit:
            d.reasons.append(f"Invoice #{inv['number']} was already reminded today (limit {limit} per day).")
        if self._pending_for("send_reminder", inv["id"]):
            d.reasons.append(f"A reminder for invoice #{inv['number']} is already waiting in your Approval inbox.")
        provider = PROVIDER_LABELS.get(inv.get("provider", ""), str(inv.get("provider", "")).title())
        via = {"provider": f"{provider} reminder email to {email or inv['customer_email']}",
               "email": f"Email to {email}", "whatsapp": f"WhatsApp to {phone}"}.get(channel, channel)
        d.params = {"invoice_id": inv["id"], "note": note, "channel": channel}
        d.preview = {"Invoice": f"#{inv['number']} ({inv['description']})", "To": inv["customer_name"], "Via": via,
                     "Outstanding": money(outstanding(inv), inv["currency"]), "Due": inv["due_date"],
                     "Message": note}

    def _check_refund(self, p: dict, d: Decision, pol: dict) -> None:
        inv = self._invoice(p.get("invoice_id"), d)
        reason = str(p.get("reason") or "").strip()
        self._no_injection("Refund reason", reason, d)
        amount = self._amount(p.get("amount"), "Refund amount", d)
        cap = float(pol["max_refund_amount"])
        if amount is not None and amount > cap:
            d.reasons.append(f"Refund {amount:,.2f} exceeds the per-refund cap of {cap:,.2f}.")
        if amount is not None:
            done = sum(round_money(a["params"].get("amount")) for a in self._executed_today("refund"))
            limit = float(pol["daily_refund_limit"])
            if done + amount > limit:
                d.reasons.append(f"This would bring today's refunds to {done + amount:,.2f}, over the daily limit "
                                 f"of {limit:,.2f}.")
        if not inv:
            return
        if inv["status"] not in REFUNDABLE_STATUSES:
            d.reasons.append(f"Invoice #{inv['number']} is {inv['status']}; only paid invoices can be refunded.")
        balance = refundable(inv)
        if amount is not None and amount > balance:
            d.reasons.append(f"Refund {amount:,.2f} is more than the refundable balance {balance:,.2f} "
                             f"(paid {inv['paid_amount']:,.2f}, already refunded {inv['refunded_amount']:,.2f}).")
        self._customer(inv["customer_name"], d, pol)
        if self._pending_for("refund", inv["id"]):
            d.reasons.append(f"A refund for invoice #{inv['number']} is already waiting in your Approval inbox.")
        # No destination field exists: the refund always goes back through the invoice's own payment.
        d.params = {"invoice_id": inv["id"], "amount": amount, "reason": reason}
        d.preview = {"Invoice": f"#{inv['number']} ({inv['description']})",
                     "Refund to": f"{inv['customer_name']} <{inv['customer_email']}> (original payer)",
                     "Amount": f"{money(amount or 0, inv['currency'])} of {inv['paid_amount']:,.2f} paid "
                               f"({balance:,.2f} refundable)",
                     "Reason": reason}
