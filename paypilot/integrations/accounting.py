"""Accounting exporters: CSV (spreadsheets), JSON, and Tally Prime XML ("Import Data" envelope).

Invoice text is third-party data, so CSV cells that a spreadsheet would treat as formulas are neutralised and
XML is built with ElementTree, which escapes everything.
"""
from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET

from ..backends import outstanding, round_money
from ..db import now_iso
from .base import AccountingExporter

COLUMNS = ["number", "id", "provider", "status", "customer_name", "customer_email", "invoice_date", "due_date",
           "currency", "amount", "paid_amount", "refunded_amount", "outstanding", "description", "note"]
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
MONEY = ("amount", "paid_amount", "refunded_amount", "outstanding")


def neutralise(value) -> str:
    """Stop CSV/formula injection: a text cell starting with = + - @ (or tab/CR) gets a leading apostrophe."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _row(inv: dict) -> dict:
    row = {c: inv.get(c) for c in COLUMNS if c != "outstanding"}
    row["outstanding"] = outstanding(inv)
    for c in MONEY:
        row[c] = f"{round_money(row[c]):.2f}"
    return row


class CsvExporter(AccountingExporter):
    format, mime, extension, label = "csv", "text/csv", "csv", "CSV (Excel, Google Sheets, Zoho Books)"

    def export(self, invoices: list[dict], business_name: str) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for inv in invoices:
            row = _row(inv)
            writer.writerow({c: (row[c] if c in MONEY else neutralise(row[c])) for c in COLUMNS})
        return buf.getvalue()


class JsonExporter(AccountingExporter):
    format, mime, extension, label = "json", "application/json", "json", "JSON"

    def export(self, invoices: list[dict], business_name: str) -> str:
        rows = [{**_row(inv), **{c: round_money(inv.get(c)) for c in MONEY if c != "outstanding"},
                 "outstanding": outstanding(inv)} for inv in invoices]
        return json.dumps({"business": business_name, "exported_at": now_iso(), "count": len(rows),
                           "invoices": rows}, indent=2, ensure_ascii=False)


class TallyXmlExporter(AccountingExporter):
    """One Sales voucher per non-draft invoice: debit the customer's ledger, credit Sales.

    Tally signs amounts by side: a debit is negative with ISDEEMEDPOSITIVE=Yes, a credit positive with No.
    """

    format, mime, extension, label = "tally", "application/xml", "xml", "Tally Prime XML (Import Data)"
    SALES_LEDGER = "Sales"

    def export(self, invoices: list[dict], business_name: str) -> str:
        env = ET.Element("ENVELOPE")
        ET.SubElement(ET.SubElement(env, "HEADER"), "TALLYREQUEST").text = "Import Data"
        imp = ET.SubElement(ET.SubElement(env, "BODY"), "IMPORTDATA")
        desc = ET.SubElement(imp, "REQUESTDESC")
        ET.SubElement(desc, "REPORTNAME").text = "Vouchers"
        ET.SubElement(ET.SubElement(desc, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = business_name
        data = ET.SubElement(imp, "REQUESTDATA")
        for inv in invoices:
            if inv.get("status") == "DRAFT":
                continue
            msg = ET.SubElement(data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            v = ET.SubElement(msg, "VOUCHER", {"VCHTYPE": "Sales", "ACTION": "Create"})
            ET.SubElement(v, "DATE").text = str(inv.get("invoice_date") or "").replace("-", "")
            ET.SubElement(v, "VOUCHERTYPENAME").text = "Sales"
            ET.SubElement(v, "VOUCHERNUMBER").text = str(inv.get("number") or inv.get("id"))
            ET.SubElement(v, "REFERENCE").text = str(inv.get("id"))
            ET.SubElement(v, "PARTYLEDGERNAME").text = inv.get("customer_name") or "Customer"
            ET.SubElement(v, "NARRATION").text = (f"{inv.get('description', '')} ({inv.get('currency')} "
                                                 f"{round_money(inv.get('amount')):.2f}, {inv.get('provider', '')} "
                                                 f"{inv.get('id')}, status {inv.get('status')})")
            amount = f"{round_money(inv.get('amount')):.2f}"
            for ledger, positive, value in ((inv.get("customer_name") or "Customer", "Yes", f"-{amount}"),
                                            (self.SALES_LEDGER, "No", amount)):
                entry = ET.SubElement(v, "ALLLEDGERENTRIES.LIST")
                ET.SubElement(entry, "LEDGERNAME").text = ledger
                ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = positive
                ET.SubElement(entry, "AMOUNT").text = value
        ET.indent(env)
        return ET.tostring(env, encoding="unicode", xml_declaration=False)


EXPORTERS: dict[str, AccountingExporter] = {"csv": CsvExporter(), "json": JsonExporter(), "tally": TallyXmlExporter()}
