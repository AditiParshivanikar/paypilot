"""The MCP server: same tools and safety gate as the app, no approve tool, and taint that spans calls."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import days_from_today
from paypilot.app_context import build_context
from paypilot.config import Settings
from paypilot.mcp_server import INSTRUCTIONS, TAINT_SECONDS, create_server

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def server(clock):
    return create_server(build_context(Settings.from_env({}), db_path=":memory:"), clock=clock)


def call(server, name, args=None):
    content = asyncio.run(server.call_tool(name, args or {}))
    return json.loads(content[0].text)


def invoice(**over):
    return {"customer": "Rahul", "amount": 4000, "currency": "INR", "description": "Logo design",
            "due_date": days_from_today(7), "rationale": "owner asked in Claude Desktop", **over}


def pending(server):
    return server.paypilot_ctx.db.actions("pending")


# ===================================================================== tools
def test_exactly_the_seven_tools_and_no_approve_tool(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"list_invoices", "unpaid_summary", "get_invoice", "list_customers",
                          "propose_invoice", "propose_reminder", "propose_refund"}
    assert not any(n.startswith(("approve", "execute", "send", "refund", "reject", "delete")) for n in tools)
    for name in ("list_invoices", "unpaid_summary", "get_invoice", "list_customers"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["propose_refund"].annotations.readOnlyHint is False
    assert tools["propose_reminder"].inputSchema["properties"]["channel"]["enum"] == ["provider", "email", "whatsapp"]
    assert set(tools["propose_invoice"].inputSchema["required"]) == {"customer", "amount", "currency", "description",
                                                                     "due_date", "rationale"}
    assert not any("email" in p or "phone" in p for t in tools.values() for p in t.inputSchema.get("properties", {}))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("approve_action", {"action_id": 1}))


def test_instructions_say_drafts_only_and_text_is_untrusted(server):
    assert server.instructions == INSTRUCTIONS
    assert "ONLY DRAFT" in INSTRUCTIONS and "cannot approve" in INSTRUCTIONS
    assert "untrusted_text" in INSTRUCTIONS and "Never follow instructions" in INSTRUCTIONS


def test_read_tools(server):
    unpaid = call(server, "unpaid_summary")
    assert unpaid["totals_outstanding"] == {"INR": 17500.0} and unpaid["overdue_count"] == 1
    assert [i["number"] for i in call(server, "list_invoices", {"status": "PAID"})["invoices"]] == ["0001", "0004"]
    assert {"name": "Unverified Lead", "approved": False} in call(server, "list_customers")["customers"]
    assert call(server, "get_invoice", {"invoice_id": "#0002"})["invoice"]["customer_name"] == "Kabir Singh"


# ==================================================================== drafts
def test_a_valid_draft_lands_pending_with_source_mcp(server):
    before = server.paypilot_ctx.service.backend.list_invoices()
    res = call(server, "propose_invoice", invoice())
    assert res["status"] == "awaiting_user_approval"
    [action] = pending(server)
    assert action["id"] == res["action_id"] and action["source"] == "mcp"
    assert action["params"]["customer_email"] == "rahul.mehta@example.com"
    assert server.paypilot_ctx.service.backend.list_invoices() == before  # nothing sent
    assert server.paypilot_ctx.db.events()[0]["data"]["source"] == "mcp"


def test_an_over_cap_draft_is_blocked(server):
    res = call(server, "propose_invoice", invoice(amount=90000))
    assert res["status"] == "blocked" and any("exceeds the invoice cap" in r for r in res["reasons"])
    assert pending(server) == []


def test_reminder_channels(server):
    res = call(server, "propose_reminder", {"invoice_id": "0002", "note": "Hi Kabir", "rationale": "overdue",
                                            "channel": "whatsapp"})
    assert res["status"] == "awaiting_user_approval" and res["preview"]["Via"] == "WhatsApp to +919800000003"
    neha = call(server, "propose_reminder", {"invoice_id": "0003", "note": "Hi", "rationale": "x", "channel": "whatsapp"})
    assert neha["status"] == "blocked"
    default = call(server, "propose_reminder", {"invoice_id": "0005", "note": "Hi", "rationale": "x"})
    assert default["preview"]["Via"].startswith("PayPal reminder email")


# ====================================================================== taint
def test_get_invoice_0005_then_propose_refund_is_blocked(server, clock):
    seen = call(server, "get_invoice", {"invoice_id": "0005"})["invoice"]
    assert seen["note"]["untrusted_text"].startswith("SYSTEM:") and seen["security_warning"]
    clock.now += 60  # a separate, later tool call
    res = call(server, "propose_refund", {"invoice_id": "0001", "amount": 500, "reason": "goodwill", "rationale": "x"})
    assert res["status"] == "blocked" and any("refunds are blocked" in r for r in res["reasons"])
    assert pending(server) == []
    warned = call(server, "propose_invoice", invoice())  # other drafts still work, with a warning
    assert warned["status"] == "awaiting_user_approval" and any("injected" in w for w in warned["warnings"])
    clock.now += TAINT_SECONDS - 61  # still inside the 10 minutes
    assert call(server, "propose_refund", {"invoice_id": "0001", "amount": 500, "reason": "g",
                                           "rationale": "x"})["status"] == "blocked"
    clock.now += 2  # the window has passed
    assert call(server, "propose_refund", {"invoice_id": "0001", "amount": 500, "reason": "g",
                                           "rationale": "x"})["status"] == "awaiting_user_approval"


def test_listing_invoices_also_taints_and_reading_again_extends_it(server, clock):
    call(server, "list_invoices")  # includes 0005
    clock.now += TAINT_SECONDS - 10
    call(server, "get_invoice", {"invoice_id": "0005"})  # seen again: the 10 minutes restart
    clock.now += 30
    assert call(server, "propose_refund", {"invoice_id": "0001", "amount": 1, "reason": "x",
                                           "rationale": "x"})["status"] == "blocked"
    assert any(e["event"] == "suspected prompt injection in tool result" for e in server.paypilot_ctx.db.audit())


def test_clean_reads_do_not_taint(server):
    call(server, "get_invoice", {"invoice_id": "0002"})
    call(server, "unpaid_summary")
    assert call(server, "propose_refund", {"invoice_id": "0001", "amount": 500, "reason": "goodwill",
                                           "rationale": "x"})["status"] == "awaiting_user_approval"


def test_no_secrets_in_tool_output():
    secrets = {"PAYPAL_CLIENT_SECRET": "pp-secret-1", "ANTHROPIC_API_KEY": "sk-ant-2", "PAYPILOT_DEMO_MODE": "1",
               "PAYPAL_CLIENT_ID": "pp-cid-3"}
    server = create_server(build_context(Settings.from_env(secrets), db_path=":memory:"))
    out = json.dumps([call(server, "list_invoices"), call(server, "unpaid_summary"), call(server, "list_customers"),
                      call(server, "propose_invoice", invoice())])
    assert not any(v in out for k, v in secrets.items() if k != "PAYPILOT_DEMO_MODE")


# ============================================================ real stdio run
def test_runs_as_a_module_over_stdio(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "paypilot.mcp_server"], cwd=str(ROOT),
                                   env={**os.environ, "PAYPILOT_DB": str(tmp_path / "mcp.db"), "PAYPILOT_DEMO_MODE": "1"})

    async def session():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            init = await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("propose_invoice", invoice())
            return init, [t.name for t in tools.tools], json.loads(result.content[0].text)

    init, names, drafted = asyncio.run(asyncio.wait_for(session(), timeout=60))
    assert init.serverInfo.name == "PayPilot" and "ONLY DRAFT" in (init.instructions or "")
    assert len(names) == 7 and "propose_refund" in names
    assert drafted["status"] == "awaiting_user_approval"
