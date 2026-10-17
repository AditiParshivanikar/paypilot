# PayPilot: Project Brief

PayPilot is an AI payments assistant for freelancers and small businesses in India. The owner manages
invoices, payment status, reminders and refunds through plain chat, with strict safety controls.
Example: "Invoice Rahul for ₹4,000 for logo design, due Friday" -> the agent drafts the invoice and shows a
preview -> the owner clicks Approve -> only then is it sent.

## Stack (do not change)
- Backend: Python 3.12, FastAPI REST API in api/index.py (also the Vercel serverless function), SQLite locally,
  Postgres (Neon) optionally in production, pytest.
- Payments: PayPal Developer Sandbox via the official `paypal-agent-toolkit` package; Razorpay test mode as a
  second provider. Each has a built-in simulator.
- AI: Claude via the `anthropic` SDK with tool calling (default model claude-sonnet-5-5, configurable with
  PAYPILOT_MODEL); a rule-based parser when no key is set.
- Website: Next.js (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui-style components in web/.
- Deploy: two Vercel projects from one GitHub repo (backend at repo root, website with root directory web/).

## Safety principles (non-negotiable)
1. The LLM can only PROPOSE actions. No tool given to an LLM, the REST API (by default) or MCP may send,
   remind, refund or execute.
2. Every money-moving or customer-facing action goes to an Approval inbox and runs only after a human approves.
3. A deterministic policy engine (the "safety gate") checks every proposal, and checks it AGAIN at approval time.
4. Credentials are read from environment variables, live only inside adapter objects, and never appear in a
   prompt, API response, event, log line or error.
5. Every message, proposal, decision, approval, rejection, webhook and API call is written to an audit log.
6. Third-party text (invoice notes, descriptions, webhook payloads) is untrusted data, never instructions.
7. Inbound webhooks only record facts (invoice paid/refunded/cancelled). They never create or execute actions.
8. With no keys the whole app runs offline in "demo mode" on simulators, and each website visitor gets a
   private workspace.

## Layout
```
api/index.py
paypilot/{__init__.py, config.py, db.py, parsing.py, backends.py, policy.py, service.py, agent.py,
          events.py, webhooks.py, auth.py, app_context.py, mcp_server.py}
paypilot/integrations/{__init__.py, base.py, razorpay.py, notifiers.py, accounting.py, hub.py}
web/            (Next.js website)
tests/          (pytest)
scripts/        (smoke and browser scripts)
docs/
```

## Conventions
- Money: Decimal-safe rounding to 2 places; INR default; display with Indian digit grouping.
- Dates: ISO YYYY-MM-DD; "today" in the server's local date.
- Never commit .env. Never print secrets.
- After every change: run `pytest -q` (and `cd web && npm run build` once the website exists).
