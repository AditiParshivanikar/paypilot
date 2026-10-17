/**
 * Typed client for the PayPilot REST API (api/index.py). Every call goes to `/api/v1/...` on this same origin;
 * next.config.ts proxies it to the backend, so the backend's httpOnly cookies are sent automatically.
 */

// ------------------------------------------------------------------ shared types
export type Provider = "paypal" | "razorpay";
export type Scope = "read" | "propose" | "approve" | "admin";
export type ActionType = "create_invoice" | "send_reminder" | "refund";
export type ActionStatus = "pending" | "rejected" | "approved" | "executed" | "failed";
export type ProposeTool = "propose_invoice" | "propose_reminder" | "propose_refund";
export type ReminderChannel = "provider" | "email" | "whatsapp";
export type NotifierChannel = "email" | "slack" | "whatsapp";
export type ExportFormat = "csv" | "json" | "tally";
export type InvoiceStatus =
  | "DRAFT"
  | "SENT"
  | "UNPAID"
  | "PARTIALLY_PAID"
  | "PAID"
  | "PARTIALLY_REFUNDED"
  | "REFUNDED"
  | "CANCELLED";
export type EventType =
  | "action.proposed"
  | "action.blocked"
  | "action.approved"
  | "action.rejected"
  | "action.failed"
  | "invoice.created"
  | "invoice.paid"
  | "invoice.refunded"
  | "invoice.cancelled"
  | "reminder.sent"
  | "refund.completed";

export const EVENT_TYPES: EventType[] = [
  "action.proposed",
  "action.blocked",
  "action.approved",
  "action.rejected",
  "action.failed",
  "invoice.created",
  "invoice.paid",
  "invoice.refunded",
  "invoice.cancelled",
  "reminder.sent",
  "refund.completed",
];

/** The normalized invoice every payments adapter returns (docs/DESIGN.md section 4). */
export interface Invoice {
  id: string;
  number: string;
  status: InvoiceStatus;
  customer_name: string;
  customer_email: string;
  amount: number;
  currency: string;
  /** Third-party text: render as plain text only. */
  description: string;
  invoice_date: string | null;
  due_date: string | null;
  paid_amount: number;
  refunded_amount: number;
  payment_id: string | null;
  /** Third-party text: render as plain text only. */
  note: string;
  provider: Provider;
}

export interface InvoiceRow extends Invoice {
  outstanding: number;
  /** Non-empty when the note or description looks like an injected instruction. */
  security_flags: string[];
}

export interface UnpaidRow {
  invoice_id: string;
  number: string;
  customer: string;
  description: string;
  currency: string;
  outstanding: number;
  due_date: string | null;
  days_overdue: number;
  status: InvoiceStatus;
  overdue: boolean;
}

export interface InvoicesResponse {
  invoices: InvoiceRow[];
  unpaid: UnpaidRow[];
  outstanding_totals: Record<string, number>;
  collected_totals: Record<string, number>;
  overdue_count: number;
}

export interface BackendStatus {
  provider: Provider;
  name?: string;
  simulated: boolean;
  mode: "simulator" | "sandbox" | "test mode";
  label: string;
}

export interface Health {
  ok: boolean;
  version: string;
  demo_mode: boolean;
  agent: "llm" | "rules";
  payments: BackendStatus;
  business: string;
  currency: string;
}

export interface Session {
  authenticated: boolean;
  kind: "api_key" | "owner" | "open" | "anonymous";
  scopes: Scope[];
  login_available: boolean;
  open_access: boolean;
}

export interface Action {
  id: number;
  created_at: string;
  action: ActionType;
  params: Record<string, unknown>;
  preview: Record<string, string | number | null>;
  rationale: string | null;
  warnings: string[];
  status: ActionStatus;
  decided_at: string | null;
  result: Record<string, unknown> | null;
  source: string | null;
}

export type TableRowData = Record<string, string | number | null>;

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  text: string;
  table: TableRowData[] | null;
  tainted: boolean;
  actions: Action[];
}

export interface ProposeResponse {
  status: "awaiting_user_approval";
  action_id: number;
  preview: Action["preview"];
  warnings: string[];
  action: Action;
}

export interface DecisionResponse {
  ok: boolean;
  result?: Record<string, unknown>;
  error?: string;
  action: Action;
}

export interface Customer {
  id: number;
  name: string;
  email: string;
  approved: boolean;
  phone: string | null;
}

export interface CustomerInput {
  name: string;
  email: string;
  approved: boolean;
  phone?: string | null;
}

export interface Policy {
  allowed_actions: ActionType[];
  max_invoice_amount: number;
  max_refund_amount: number;
  daily_refund_limit: number;
  max_reminders_per_invoice_per_day: number;
  allowed_currencies: string[];
  require_approved_customer: boolean;
  reminder_channels: ReminderChannel[];
  owner_notifications: Partial<Record<EventType, ("slack" | "email")[]>>;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string;
  event: string;
  detail: unknown;
  action_id: number | null;
}

export interface PayPilotEvent {
  id: string;
  type: EventType;
  created: string;
  data: Record<string, unknown>;
}

export interface NotificationRow {
  id: number;
  ts: string;
  channel: NotifierChannel;
  adapter: string;
  recipient: string | null;
  subject: string | null;
  body: string | null;
  status: "sent" | "simulated" | "failed";
  detail: Record<string, unknown> | null;
}

export interface NotifierStatus {
  channel: NotifierChannel;
  adapter: string;
  simulated: boolean;
  host?: string;
  port?: number;
  sender?: string;
  from?: string;
}

export interface IntegrationsStatus {
  demo_mode: boolean;
  webhook_transport: "simulate" | "http";
  payments: BackendStatus;
  providers: Record<Provider, BackendStatus>;
  notifiers: Record<NotifierChannel, NotifierStatus>;
  exporters: { format: ExportFormat; label: string; mime: string; extension: string }[];
  owner_email_configured: boolean;
  webhooks: { paypal_verification: boolean; razorpay_verification: boolean };
}

export interface Delivery {
  id: number;
  ts: string;
  event_id: string;
  subscription_id: string;
  attempt: number;
  ok: boolean;
  status_code: number | null;
  error: string | null;
}

export interface Subscription {
  id: string;
  created_at: string;
  url: string;
  events: (EventType | "*")[];
  description: string | null;
  active: boolean;
  /** Always masked ("whsec_••••••••") in listings. */
  secret: string;
  recent_deliveries: Delivery[];
}

/** Returned once, when the subscription is created: the real signing secret. */
export type NewSubscription = Omit<Subscription, "recent_deliveries">;

export interface SubscriptionInput {
  url: string;
  events: (EventType | "*")[];
  description?: string;
}

export interface ApiKey {
  id: number;
  created_at: string;
  name: string;
  prefix: string;
  scopes: Scope[];
  last_used: string | null;
  revoked: boolean;
}

/** Returned once, when the key is created: the full key. */
export interface NewApiKey {
  id: number;
  name: string;
  prefix: string;
  scopes: Scope[];
  key: string;
}

export interface WebhookResult {
  status: "processed" | "duplicate" | "ignored" | "unmatched";
  event_id: string;
  event?: EventType;
  invoice_id?: string;
  invoice_status?: InvoiceStatus;
  raw_type?: string;
}

export interface DispatchResult {
  retried: number;
  succeeded: number;
}

// ------------------------------------------------------------------- errors
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  /** Present when the safety gate blocked a proposal (HTTP 422). */
  readonly reasons: string[] | undefined;

  constructor(status: number, message: string, detail?: unknown, reasons?: string[]) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.reasons = reasons;
  }
}

interface ValidationIssue {
  loc?: (string | number)[];
  msg?: string;
}

/** Turn FastAPI's `detail` (a string, validation issues, or {status: "blocked", reasons}) into a sentence. */
export function errorMessage(status: number, detail: unknown): { message: string; reasons?: string[] } {
  if (typeof detail === "string" && detail) return { message: detail };
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationIssue[]).map((issue) => {
      const field = (issue.loc ?? []).filter((p) => p !== "body" && p !== "query" && p !== "path").join(".");
      const msg = (issue.msg ?? "is invalid").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    });
    return { message: parts.join("; ") || "The request was not valid." };
  }
  if (detail && typeof detail === "object" && "reasons" in detail) {
    const reasons = ((detail as { reasons?: unknown }).reasons as string[] | undefined) ?? [];
    return { message: `Blocked by the safety gate: ${reasons.join(" ")}`.trim(), reasons };
  }
  const fallback: Record<number, string> = {
    401: "Please sign in to continue.",
    403: "You don't have permission to do that.",
    404: "Not found.",
    409: "That action was already decided.",
    502: "The payment provider did not respond. Please try again.",
  };
  return { message: fallback[status] ?? `Request failed (HTTP ${status}).` };
}

// ------------------------------------------------------------------ requests
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  } catch {
    throw new ApiError(0, "Can't reach the PayPilot server. Is the backend running?");
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;
    const { message, reasons } = errorMessage(res.status, detail);
    throw new ApiError(res.status, message, detail, reasons);
  }
  return body as T;
}

const json = (data: unknown): RequestInit => ({ body: JSON.stringify(data) });
const post = <T>(path: string, data?: unknown) =>
  request<T>(path, { method: "POST", ...(data === undefined ? {} : json(data)) });
const qs = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : "";
};

export const api = {
  health: () => request<Health>("/health"),
  session: () => request<Session>("/session"),
  login: (password: string) => post<{ ok: boolean; scopes: Scope[] }>("/login", { password }),
  logout: () => post<{ ok: boolean }>("/logout"),
  integrations: () => request<IntegrationsStatus>("/integrations"),

  chat: (message: string, history: ChatTurn[] = []) => post<ChatResponse>("/chat", { message, history }),
  actions: (status?: ActionStatus) => request<{ actions: Action[] }>(`/actions${qs({ status })}`),
  propose: (tool: ProposeTool, args: Record<string, unknown>) => post<ProposeResponse>("/actions", { tool, args }),
  approve: (id: number) => post<DecisionResponse>(`/actions/${id}/approve`),
  reject: (id: number) => post<DecisionResponse>(`/actions/${id}/reject`),

  invoices: () => request<InvoicesResponse>("/invoices"),
  customers: () => request<{ customers: Customer[] }>("/customers"),
  saveCustomer: (input: CustomerInput) => post<Customer>("/customers", input),
  policy: () => request<Policy>("/policy"),
  /** Omit owner_notifications to keep the current owner notification settings. */
  savePolicy: (policy: Omit<Policy, "owner_notifications"> & Partial<Pick<Policy, "owner_notifications">>) =>
    request<Policy>("/policy", { method: "PUT", ...json(policy) }),
  resetPolicy: () => post<Policy>("/policy/reset"),

  audit: (limit = 200) => request<{ entries: AuditEntry[] }>(`/audit${qs({ limit })}`),
  events: (type?: EventType, limit = 200) => request<{ events: PayPilotEvent[] }>(`/events${qs({ type, limit })}`),
  dispatch: () => post<DispatchResult>("/events/dispatch"),
  notifications: (limit = 200) => request<{ notifications: NotificationRow[] }>(`/notifications${qs({ limit })}`),
  /** A same-origin URL for an <a download> link; the browser sends the cookies. */
  exportUrl: (format: ExportFormat) => `/api/v1/exports/${format}`,

  subscriptions: () => request<{ subscriptions: Subscription[] }>("/webhooks/subscriptions"),
  subscribe: (input: SubscriptionInput) => post<NewSubscription>("/webhooks/subscriptions", input),
  unsubscribe: (id: string) => request<{ ok: boolean }>(`/webhooks/subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  simulatePayment: (invoiceRef: string) => post<WebhookResult>(`/simulate/payment/${encodeURIComponent(invoiceRef)}`),

  keys: () => request<{ keys: ApiKey[] }>("/keys"),
  createKey: (name: string, scopes?: Scope[]) => post<NewApiKey>("/keys", { name, scopes }),
  revokeKey: (id: number) => request<{ ok: boolean }>(`/keys/${id}`, { method: "DELETE" }),
  resetDemo: () => post<{ ok: boolean }>("/demo/reset"),
};
