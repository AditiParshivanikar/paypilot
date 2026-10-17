"use client";

import {
  BellRing,
  CheckCircle2,
  CircleX,
  Download,
  FileJson,
  FileSpreadsheet,
  KeyRound,
  Loader2,
  Mail,
  MessageCircle,
  Plus,
  RefreshCw,
  Hash,
  TriangleAlert,
  Trash2,
  Wallet,
  Webhook,
  type LucideIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { CopyBlock } from "@/components/app/copy-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  EVENT_TYPES,
  api,
  type EventType,
  type ExportFormat,
  type IntegrationsStatus,
  type NewApiKey,
  type NewSubscription,
  type Scope,
} from "@/lib/api";
import { useApiKeys, useIntegrations, useNotifications, useSubscriptions } from "@/lib/hooks";
import { EVENT_TITLES, INTEGRATION_INFO, SCOPE_INFO } from "@/lib/labels";
import { cn, nice, timeAgo } from "@/lib/utils";

// ------------------------------------------------------------------------------ apps
type AppState = "Connected" | "Simulator" | "Available";
const STATE_VARIANT: Record<AppState, "success" | "secondary" | "outline"> = {
  Connected: "success",
  Simulator: "secondary",
  Available: "outline",
};

function AppCard({
  id,
  icon: Icon,
  state,
  detail,
  footer,
}: {
  id: keyof typeof INTEGRATION_INFO;
  icon: LucideIcon;
  state: AppState;
  detail?: string;
  footer?: React.ReactNode;
}) {
  const info = INTEGRATION_INFO[id];
  return (
    <Card className="gap-4">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Icon className="size-5" aria-hidden="true" />
          </span>
          <Badge variant={STATE_VARIANT[state]}>
            {state === "Connected" ? <CheckCircle2 aria-hidden="true" /> : null}
            {state}
          </Badge>
        </div>
        <CardTitle className="mt-2">{info.name}</CardTitle>
        <CardDescription>{info.blurb}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {detail ? <p className="text-muted-foreground">{detail}</p> : null}
        {info.env.length && state !== "Connected" ? (
          <div>
            <p className="mb-1 text-xs text-muted-foreground">To go live, set on the backend:</p>
            <div className="flex flex-wrap gap-1">
              {info.env.map((v) => (
                <code key={v} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                  {v}
                </code>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
      {footer ? <CardFooter className="mt-auto">{footer}</CardFooter> : null}
    </Card>
  );
}

function providerState(status: IntegrationsStatus, provider: "paypal" | "razorpay"): AppState {
  const s = status.providers[provider];
  if (!s.simulated) return "Connected";
  return status.payments.provider === provider ? "Simulator" : "Available";
}

const EXPORT_ICONS: Record<ExportFormat, LucideIcon> = { csv: FileSpreadsheet, json: FileJson, tally: FileSpreadsheet };

export function AppsTab() {
  const { data: status } = useIntegrations();
  if (!status) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-56 rounded-xl" />
        ))}
      </div>
    );
  }
  const notifier = (ch: "email" | "whatsapp" | "slack"): AppState => (status.notifiers[ch].simulated ? "Simulator" : "Connected");
  return (
    <div className="space-y-4">
      {status.demo_mode ? (
        <p className="rounded-lg border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">
          Demo mode: every integration runs on a built-in simulator, so nothing leaves the server. Set real keys on the
          backend (and turn demo mode off) to connect them.
        </p>
      ) : null}
      <div className="grid items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <AppCard id="paypal" icon={Wallet} state={providerState(status, "paypal")} detail={providerState(status, "paypal") === "Available" ? "Not in use. Set its keys to switch to it." : status.providers.paypal.label} />
        <AppCard id="razorpay" icon={Wallet} state={providerState(status, "razorpay")} detail={providerState(status, "razorpay") === "Available" ? "Not in use. Set its keys to switch to it." : status.providers.razorpay.label} />
        <AppCard id="email" icon={Mail} state={notifier("email")} detail={status.notifiers.email.host ? `${status.notifiers.email.host} as ${status.notifiers.email.sender}` : undefined} />
        <AppCard id="whatsapp" icon={MessageCircle} state={notifier("whatsapp")} detail={status.notifiers.whatsapp.from ? `From ${status.notifiers.whatsapp.from}` : undefined} />
        <AppCard id="slack" icon={Hash} state={notifier("slack")} />
        {status.exporters.map((e) => {
          const Icon = EXPORT_ICONS[e.format] ?? FileSpreadsheet;
          return (
            <AppCard
              key={e.format}
              id={e.format}
              icon={Icon}
              state="Available"
              footer={
                <Button asChild variant="outline" size="sm">
                  <a href={api.exportUrl(e.format)} download>
                    <Download /> Download {nice(e.format)}
                  </a>
                </Button>
              }
            />
          );
        })}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------- webhooks
export function WebhooksTab() {
  const { subscriptions, mutate } = useSubscriptions();
  const [url, setUrl] = React.useState("");
  const [events, setEvents] = React.useState<EventType[]>(["invoice.paid", "action.proposed"]);
  const [busy, setBusy] = React.useState(false);
  const [created, setCreated] = React.useState<NewSubscription | null>(null);
  const [retrying, setRetrying] = React.useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const sub = await api.subscribe({ url: url.trim(), events });
      setCreated(sub);
      setUrl("");
      toast.success("Webhook added");
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not add the webhook.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!window.confirm("Remove this webhook? It will stop receiving events.")) return;
    try {
      await api.unsubscribe(id);
      toast("Webhook removed");
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove it.");
    }
  }

  async function retry() {
    setRetrying(true);
    try {
      const res = await api.dispatch();
      toast(res.retried ? `Retried ${res.retried}, ${res.succeeded} delivered` : "Nothing to retry");
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Retry failed.");
    } finally {
      setRetrying(false);
    }
  }

  const active = subscriptions?.filter((s) => s.active) ?? [];
  const urls = new Map((subscriptions ?? []).map((s) => [s.id, s.url]));
  const deliveries = (subscriptions ?? []).flatMap((s) => s.recent_deliveries).sort((a, b) => b.id - a.id).slice(0, 20);

  return (
    <div className="grid items-start gap-6 lg:grid-cols-5">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Add a webhook</CardTitle>
          <CardDescription>PayPilot POSTs signed JSON to your endpoint when these events happen.</CardDescription>
        </CardHeader>
        <form onSubmit={add}>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="webhook-url">Endpoint URL</Label>
              <Input
                id="webhook-url"
                type="url"
                inputMode="url"
                placeholder="https://hooks.example.com/paypilot"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
              <p className="text-xs text-muted-foreground">Public https only; private and internal addresses are refused.</p>
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Events</legend>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                {EVENT_TYPES.map((t) => (
                  <label key={t} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={events.includes(t)}
                      onCheckedChange={(on) => setEvents((prev) => (on ? [...prev, t] : prev.filter((x) => x !== t)))}
                    />
                    <span>
                      {EVENT_TITLES[t]} <code className="text-xs text-muted-foreground">{t}</code>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          </CardContent>
          <CardFooter className="mt-5">
            <Button type="submit" disabled={busy || !url.trim() || events.length === 0}>
              {busy ? <Loader2 className="animate-spin" /> : <Plus />} Add webhook
            </Button>
          </CardFooter>
        </form>
      </Card>

      <div className="space-y-6 lg:col-span-3">
        {created ? (
          <Card className="border-warning/50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="size-4" aria-hidden="true" /> Signing secret (shown once)
              </CardTitle>
              <CardDescription>
                Store it now. Verify each delivery&apos;s <code className="font-mono">PayPilot-Signature</code> header
                (HMAC-SHA256 of <code className="font-mono">&quot;timestamp.body&quot;</code>) with it.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <CopyBlock value={created.secret} label="Copy secret" />
              <p className="truncate text-xs text-muted-foreground">For {created.url}</p>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>Endpoints</CardTitle>
          </CardHeader>
          <CardContent>
            {!subscriptions ? (
              <Skeleton className="h-16 w-full" />
            ) : active.length === 0 ? (
              <p className="text-sm text-muted-foreground">No webhooks yet.</p>
            ) : (
              <ul className="divide-y">
                {active.map((s) => (
                  <li key={s.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                    <Webhook className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-sm">{s.url}</p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {s.events.map((e) => (
                          <Badge key={e} variant="outline" className="font-mono text-[10px]">
                            {e}
                          </Badge>
                        ))}
                      </div>
                    </div>
                    <Button variant="ghost" size="icon" aria-label={`Remove ${s.url}`} onClick={() => remove(s.id)}>
                      <Trash2 />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent deliveries</CardTitle>
            <CardDescription>Failed deliveries are retried up to 5 times.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {deliveries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No deliveries yet. Events are delivered as they happen.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Endpoint</TableHead>
                    <TableHead>Attempt</TableHead>
                    <TableHead>Result</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {deliveries.map((d) => (
                    <TableRow key={d.id}>
                      <TableCell className="text-muted-foreground">{timeAgo(d.ts)}</TableCell>
                      <TableCell className="max-w-48 truncate font-mono text-xs">{urls.get(d.subscription_id)}</TableCell>
                      <TableCell>#{d.attempt}</TableCell>
                      <TableCell>
                        {d.ok ? (
                          <Badge variant="success">
                            <CheckCircle2 aria-hidden="true" /> {d.status_code ?? "Simulated"}
                          </Badge>
                        ) : (
                          <Badge variant="destructive" title={d.error ?? undefined}>
                            <CircleX aria-hidden="true" /> {d.status_code ?? d.error ?? "Failed"}
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <Button variant="outline" size="sm" onClick={retry} disabled={retrying}>
              {retrying ? <Loader2 className="animate-spin" /> : <RefreshCw />} Retry failed deliveries
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------- api keys
const ALL_SCOPES: Scope[] = ["read", "propose", "approve", "admin"];

export function ApiKeysTab() {
  const { keys, mutate } = useApiKeys();
  const [name, setName] = React.useState("");
  const [scopes, setScopes] = React.useState<Scope[]>(["read", "propose"]);
  const [busy, setBusy] = React.useState(false);
  const [created, setCreated] = React.useState<NewApiKey | null>(null);
  const risky = scopes.includes("approve") || scopes.includes("admin");

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const key = await api.createKey(name.trim(), scopes);
      setCreated(key);
      setName("");
      toast.success(`Key "${key.name}" created`);
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create the key.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number, keyName: string) {
    if (!window.confirm(`Revoke "${keyName}"? Anything using it will stop working.`)) return;
    try {
      await api.revokeKey(id);
      toast(`Revoked "${keyName}"`);
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not revoke it.");
    }
  }

  return (
    <div className="grid items-start gap-6 lg:grid-cols-5">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Create an API key</CardTitle>
          <CardDescription>For Zapier, scripts or your own tools. Send it in the X-API-Key header.</CardDescription>
        </CardHeader>
        <form onSubmit={create}>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="key-name">Name</Label>
              <Input id="key-name" placeholder="zapier" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} required />
              <p className="text-xs text-muted-foreground">Letters, digits, dots, dashes or underscores.</p>
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Scopes</legend>
              {ALL_SCOPES.map((s) => (
                <label key={s} className="flex items-start gap-2 text-sm">
                  <Checkbox
                    className="mt-0.5"
                    checked={scopes.includes(s)}
                    onCheckedChange={(on) => setScopes((prev) => (on ? [...prev, s] : prev.filter((x) => x !== s)))}
                  />
                  <span>
                    <span className="font-medium">{nice(s)}</span>
                    <span className="block text-xs text-muted-foreground">{SCOPE_INFO[s]}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            {risky ? (
              <div role="alert" className="flex gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-foreground dark:text-warning" aria-hidden="true" />
                <span>
                  A key that can approve can send invoices and issue refunds without you. Keep it to read and propose
                  unless you really need it.
                </span>
              </div>
            ) : null}
          </CardContent>
          <CardFooter className="mt-5">
            <Button type="submit" disabled={busy || !name.trim() || scopes.length === 0}>
              {busy ? <Loader2 className="animate-spin" /> : <KeyRound />} Create key
            </Button>
          </CardFooter>
        </form>
      </Card>

      <div className="space-y-6 lg:col-span-3">
        {created ? (
          <Card className="border-warning/50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="size-4" aria-hidden="true" /> Your new key (shown once)
              </CardTitle>
              <CardDescription>Copy it now; PayPilot only stores a hash of it.</CardDescription>
            </CardHeader>
            <CardContent>
              <CopyBlock value={created.key} label="Copy key" />
            </CardContent>
          </Card>
        ) : null}
        <Card>
          <CardHeader>
            <CardTitle>Keys</CardTitle>
          </CardHeader>
          <CardContent>
            {!keys ? (
              <Skeleton className="h-16 w-full" />
            ) : keys.length === 0 ? (
              <p className="text-sm text-muted-foreground">No API keys yet.</p>
            ) : (
              <ul className="divide-y">
                {keys.map((k) => (
                  <li key={k.id} className={cn("flex items-start gap-3 py-3 first:pt-0 last:pb-0", k.revoked && "opacity-60")}>
                    <KeyRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">
                        {k.name} <span className="font-mono text-xs text-muted-foreground">{k.prefix}_…</span>
                      </p>
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        {k.scopes.map((s) => (
                          <Badge key={s} variant={s === "approve" || s === "admin" ? "warning" : "outline"}>
                            {s}
                          </Badge>
                        ))}
                        <span className="text-xs text-muted-foreground">
                          · created {timeAgo(k.created_at)} · {k.last_used ? `used ${timeAgo(k.last_used)}` : "never used"}
                        </span>
                      </div>
                    </div>
                    {k.revoked ? (
                      <Badge variant="secondary">Revoked</Badge>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => revoke(k.id, k.name)}>
                        Revoke
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------- notifications
const CHANNEL_ICONS: Record<string, LucideIcon> = { email: Mail, whatsapp: MessageCircle, slack: Hash };

export function NotificationsTab() {
  const { notifications } = useNotifications(100);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Outbox</CardTitle>
        <CardDescription>Every email, WhatsApp and Slack message PayPilot sent or simulated, newest first.</CardDescription>
      </CardHeader>
      <CardContent>
        {!notifications ? (
          <Skeleton className="h-24 w-full" />
        ) : notifications.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing sent yet.</p>
        ) : (
          <ul className="divide-y">
            {notifications.map((n) => {
              const Icon = CHANNEL_ICONS[n.channel] ?? BellRing;
              return (
                <li key={n.id} className="flex gap-3 py-3 first:pt-0 last:pb-0">
                  <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{n.subject || "(no subject)"}</span>
                      <Badge variant={n.status === "failed" ? "destructive" : n.status === "sent" ? "success" : "secondary"}>
                        {n.status}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {nice(n.channel)} to {n.recipient} via {nice(n.adapter)} · {timeAgo(n.ts)}
                    </p>
                    {n.body ? <p className="mt-1 line-clamp-2 whitespace-pre-wrap break-words text-sm">{n.body}</p> : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------------- mcp & api
export function McpTab() {
  const origin = typeof window !== "undefined" ? window.location.origin : "https://your-paypilot-site";
  const config = JSON.stringify(
    {
      mcpServers: {
        paypilot: {
          command: "C:\\path\\to\\paypilot\\.venv\\Scripts\\python.exe",
          args: ["-m", "paypilot.mcp_server"],
          cwd: "C:\\path\\to\\paypilot",
          env: { PAYPILOT_DB: "C:\\path\\to\\paypilot\\data\\paypilot.db" },
        },
      },
    },
    null,
    2,
  );
  const curl = [
    `curl -X POST "${origin}/api/v1/actions" \\`,
    `  -H "X-API-Key: pp_xxxxxxxx_your-key" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"tool": "propose_invoice", "args": {"customer": "Rahul", "amount": 4000, "currency": "INR",`,
    `       "description": "Logo design", "due_date": "2026-10-23", "rationale": "Zapier: new project"}}'`,
  ].join("\n");
  return (
    <div className="grid items-start gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Claude Desktop (MCP)</CardTitle>
          <CardDescription>
            Settings → Developer → Edit Config. Claude can read invoices and draft actions; it has no approve tool.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <CopyBlock value={config} label="Copy config" />
          <p className="text-xs text-muted-foreground">
            Replace the paths with your checkout. On macOS/Linux use <code className="font-mono">.venv/bin/python</code>.
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>REST API</CardTitle>
          <CardDescription>
            Draft an invoice with an API key. It lands in Approvals; keys can&apos;t approve unless you allow it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <CopyBlock value={curl} label="Copy curl" />
          <p className="text-xs text-muted-foreground">
            Full reference:{" "}
            <a className="underline underline-offset-2" href="/api/docs" target="_blank" rel="noreferrer">
              /api/docs
            </a>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
