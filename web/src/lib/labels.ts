import type { ActionType, EventType, InvoiceStatus, PayPilotEvent, ReminderChannel, Scope } from "@/lib/api";
import { money, nice } from "@/lib/utils";

/** Short names for the three action types. */
export const ACTION_LABELS: Record<ActionType, string> = {
  create_invoice: "Invoice",
  send_reminder: "Reminder",
  refund: "Refund",
};

/** Card titles: what approving will do. */
export const ACTION_TITLES: Record<ActionType, string> = {
  create_invoice: "Send invoice",
  send_reminder: "Send payment reminder",
  refund: "Issue refund",
};

/** Toast after a successful approval. */
export const ACTION_DONE: Record<ActionType, string> = {
  create_invoice: "Invoice sent",
  send_reminder: "Reminder sent",
  refund: "Refund issued",
};

/** The six starter cards on the empty chat. */
export const CHAT_SUGGESTIONS: { title: string; prompt: string }[] = [
  { title: "Send an invoice", prompt: "Invoice Rahul for ₹4,000 for logo design, due Friday" },
  { title: "See who owes you", prompt: "Who hasn't paid me?" },
  { title: "Chase late payers", prompt: "Remind everyone who is overdue" },
  { title: "Give money back", prompt: "Refund Priya ₹2,000 because the project was cut short" },
  { title: "Test the safety gate", prompt: "Invoice Rahul for ₹90,000 for app development" },
  { title: "Spot a hidden instruction", prompt: "Show invoice 0005" },
];

/** What each integration does and the backend environment variables that switch it from simulator to live. */
export const INTEGRATION_INFO: Record<string, { name: string; blurb: string; env: string[] }> = {
  paypal: {
    name: "PayPal",
    blurb: "Invoices, reminders and refunds through the PayPal Developer Sandbox.",
    env: ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID"],
  },
  razorpay: {
    name: "Razorpay",
    blurb: "Invoices and refunds in Razorpay test mode, with UPI and cards.",
    env: ["PAYPILOT_PAYMENT_PROVIDER=razorpay", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"],
  },
  email: {
    name: "Email",
    blurb: "Payment reminders and owner alerts over your SMTP server.",
    env: ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"],
  },
  whatsapp: {
    name: "WhatsApp",
    blurb: "Payment reminders on WhatsApp through Twilio, to the number on file.",
    env: ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"],
  },
  slack: {
    name: "Slack",
    blurb: "Owner alerts in #payments when something needs approval or gets paid.",
    env: ["SLACK_WEBHOOK_URL"],
  },
  csv: { name: "CSV", blurb: "Spreadsheet export for Excel, Google Sheets or Zoho Books.", env: [] },
  json: { name: "JSON", blurb: "Machine-readable export of every invoice.", env: [] },
  tally: { name: "Tally XML", blurb: "Sales vouchers for Tally Prime's Import Data.", env: [] },
};

export const SCOPE_INFO: Record<Scope, string> = {
  read: "Read invoices, actions and logs",
  propose: "Draft actions for approval",
  approve: "Approve and execute actions",
  admin: "Everything, including rules and keys",
};

export const ACTOR_LABELS: Record<string, string> = {
  user: "You",
  agent: "Assistant",
  policy: "Safety gate",
  executor: "Executor",
  webhook: "Webhook",
  system: "System",
};

export const ACTION_TYPE_LABELS: Record<ActionType, string> = {
  create_invoice: "Create invoices",
  send_reminder: "Send reminders",
  refund: "Issue refunds",
};

export const CHANNEL_LABELS: Record<ReminderChannel, string> = {
  provider: "Provider email (PayPal/Razorpay)",
  email: "Email (SMTP)",
  whatsapp: "WhatsApp",
};

/** Verb phrases for timelines: "Invoice drafted", "Refund approved"... */
export const ACTION_NOUNS: Record<ActionType, string> = {
  create_invoice: "invoice",
  send_reminder: "reminder",
  refund: "refund",
};

export const EVENT_TITLES: Record<EventType, string> = {
  "action.proposed": "Waiting for your approval",
  "action.blocked": "Blocked by the safety gate",
  "action.approved": "Approved",
  "action.rejected": "Rejected",
  "action.failed": "Failed",
  "invoice.created": "Invoice sent",
  "invoice.paid": "Invoice paid",
  "invoice.refunded": "Invoice refunded",
  "invoice.cancelled": "Invoice cancelled",
  "reminder.sent": "Reminder sent",
  "refund.completed": "Refund completed",
};

export const INVOICE_STATUS_LABELS: Record<InvoiceStatus, string> = {
  DRAFT: "Draft",
  SENT: "Sent",
  UNPAID: "Unpaid",
  PARTIALLY_PAID: "Part paid",
  PAID: "Paid",
  PARTIALLY_REFUNDED: "Part refunded",
  REFUNDED: "Refunded",
  CANCELLED: "Cancelled",
};

/** Suggested prompts; each links to /app/chat?q=... */
export const TRY_ASKING: string[] = [
  "Invoice Rahul for ₹4,000 for logo design, due Friday",
  "Who hasn't paid me?",
  "Remind everyone who is overdue",
  "Refund Priya ₹2,000 because the project was cut short",
  "Show invoice 0005",
];

type Data = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : "");
const num = (v: unknown) => (typeof v === "number" ? v : Number(v ?? 0));

/** One line describing an event for timelines, e.g. "#0002 · Kabir Singh · ₹3,500.00". */
export function eventDetail(event: PayPilotEvent): string {
  const d: Data = event.data ?? {};
  const inv = (d.invoice ?? null) as Data | null;
  if (inv) return [`#${str(inv.number)}`, str(inv.customer_name), money(str(inv.currency), num(inv.amount))].join(" · ");
  if (d.customer) return [`#${str(d.number)}`, str(d.customer), money(str(d.currency), num(d.amount))].join(" · ");
  if (event.type === "refund.completed") return `#${str(d.number)} · ${money(str(d.currency), num(d.amount))}`;
  if (event.type === "reminder.sent") return `#${str(d.number)} · via ${nice(str(d.channel))}`;
  const action = ACTION_LABELS[str(d.action) as ActionType] ?? nice(str(d.action));
  const preview = (d.preview ?? null) as Data | null;
  if (preview) return [action, str(preview.To) || str(preview.Invoice), str(preview.Amount)].filter(Boolean).join(" · ");
  const reasons = Array.isArray(d.reasons) ? (d.reasons as unknown[]) : [];
  if (reasons.length) return `${action}: ${str(reasons[0])}`;
  if (d.error) return `${action}: ${str(d.error)}`;
  return d.action_id ? `${action} #${str(d.action_id)}` : action;
}

export function initials(name: string | null | undefined): string {
  const parts = (name || "?").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
