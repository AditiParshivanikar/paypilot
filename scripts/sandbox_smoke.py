"""Smoke test for the PayPal layer.

With PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET set (and demo mode off), lists invoices from the PayPal
sandbox. Otherwise lists the invoices held by the built-in simulator. Never prints credentials.

    python scripts/sandbox_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paypilot.backends import BackendError, make_backend  # noqa: E402
from paypilot.config import Settings  # noqa: E402
from paypilot.db import Database  # noqa: E402


def indian_grouping(amount: float) -> str:
    """12345678.5 -> '1,23,45,678.50' (last three digits, then groups of two)."""
    whole, frac = f"{abs(amount):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if amount < 0 else "") + ",".join(groups + [tail]) + "." + frac


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings.from_env()
    if settings.backend_name == "sandbox":
        print("Backend: PayPal sandbox (api-m.sandbox.paypal.com)")
    else:
        why = "demo mode is on" if settings.has_paypal_keys else "no PayPal keys set"
        print(f"Backend: PayPal simulator ({why}, running offline)")
    print(f"Database: {settings.db_path}\n")

    try:
        invoices = make_backend(settings, Database(settings.db_path)).list_invoices()
    except BackendError as e:
        print(f"FAILED: {e}")
        return 1

    print(f"{'#':<6}{'Status':<20}{'Customer':<16}{'Amount':>16}  {'Due':<12}{'ID':<26}Note")
    for inv in invoices:
        note = (inv.get("note") or "").replace("\n", " ")
        note = note[:45] + ("…" if len(note) > 45 else "")
        amount = f"{inv['currency']} {indian_grouping(inv['amount'])}"
        print(f"{inv['number'] or '-':<6}{inv['status']:<20}{inv['customer_name']:<16}{amount:>16}  "
              f"{inv['due_date'] or '-':<12}{inv['id']:<26}{note}")
    print(f"\n{len(invoices)} invoice(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
