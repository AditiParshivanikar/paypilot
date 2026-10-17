"""Run the offline RuleAgent on six sample commands against the PayPal simulator.

Uses an in-memory database, so nothing is written to disk and every run starts from the demo data.
Every proposal goes through the safety gate; allowed ones wait in the Approval inbox.

    python scripts/agent_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paypilot.agent import RuleAgent  # noqa: E402
from paypilot.backends import MockBackend  # noqa: E402
from paypilot.db import Database  # noqa: E402
from paypilot.service import PayPilotService  # noqa: E402

COMMANDS = [
    "Invoice Rahul for ₹4,000 for logo design, due Friday",
    "Who hasn't paid me?",
    "Remind everyone who is overdue",
    "Refund Priya ₹2,000 because the project was cut short",
    "Show invoice 0005",
    "Show all invoices",
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = Database(":memory:")
    agent = RuleAgent(PayPilotService(db, MockBackend(db)))
    for cmd in COMMANDS:
        reply = agent.respond(cmd)
        print("=" * 88)
        print(f"YOU:   {cmd}")
        print(f"TOOLS: {', '.join(c['name'] for c in reply.tool_calls) or '-'}{'   [tainted]' if reply.tainted else ''}")
        print("-" * 88)
        print(reply.text)
    print("=" * 88)
    pending = db.actions("pending")
    print(f"Approval inbox: {len(pending)} pending ({', '.join(a['action'] for a in reversed(pending))}). "
          f"Audit log: {len(db.audit(1000))} entries. Nothing was sent.")


if __name__ == "__main__":
    main()
