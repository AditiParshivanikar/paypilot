"""Show the safety gate on the PayPal simulator with an in-memory database.

1. "Invoice Rahul for ₹90,000 for app development" is blocked (over the ₹50,000 cap).
2. The ₹4,000 invoice is queued, and only executes when approved.
3. Reading invoice 0005 (whose note holds a prompt injection) and then proposing a refund in the SAME turn
   is blocked. The same refund in a fresh turn is queued, so the block comes from the tainted turn.

    python scripts/safety_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paypilot.agent import RuleAgent, _ToolRunner  # noqa: E402
from paypilot.backends import MockBackend  # noqa: E402
from paypilot.db import Database  # noqa: E402
from paypilot.service import PayPilotService  # noqa: E402


def banner(title: str) -> None:
    print("\n" + "=" * 88 + f"\n{title}\n" + "=" * 88)


def show(label: str, value) -> None:
    print(f"{label}: {json.dumps(value, indent=2, ensure_ascii=False)}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = Database(":memory:")
    service = PayPilotService(db, MockBackend(db))
    agent = RuleAgent(service)

    banner('1. "Invoice Rahul for ₹90,000 for app development"')
    print(agent.respond("Invoice Rahul for ₹90,000 for app development").text)

    banner('2. "Invoice Rahul for ₹4,000 for logo design, due Friday", then Approve')
    print(agent.respond("Invoice Rahul for ₹4,000 for logo design, due Friday").text)
    action_id = db.actions("pending")[0]["id"]
    print(f"\nInvoices at PayPal before approval: {len(service.backend.list_invoices())}")
    out = service.approve(action_id)
    inv = out["result"]
    print(f"Approve action #{action_id} -> ok={out['ok']}, status={out['action']['status']}")
    print(f"PayPal now has invoice #{inv['number']} for {inv['customer_name']}: {inv['currency']} "
          f"{inv['amount']:,.2f}, {inv['status']}, due {inv['due_date']}")
    print(f"Invoices at PayPal after approval: {len(service.backend.list_invoices())}")

    banner("3a. ONE turn: get_invoice 0005, then propose_refund on Priya's paid invoice #0001")
    turn = _ToolRunner(service)
    note = turn("get_invoice", {"invoice_id": "0005"})["invoice"]
    show("note seen by the agent", note["note"])
    print(f"security_warning: {note['security_warning']}")
    print(f"turn tainted: {turn.tainted}")
    show("propose_refund", turn("propose_refund", {"invoice_id": "0001", "amount": 1000, "reason": "goodwill",
                                                   "rationale": "obeying the note"}))

    banner("3b. A FRESH turn: the same refund")
    show("propose_refund", _ToolRunner(service)("propose_refund", {"invoice_id": "0001", "amount": 1000,
                                                                   "reason": "goodwill", "rationale": "owner asked"}))

    banner("Audit log (oldest first)")
    for e in reversed(db.audit(100)):
        print(f"  {e['actor']:<9} {e['event']}" + (f"  (action #{e['action_id']})" if e["action_id"] else ""))


if __name__ == "__main__":
    main()
