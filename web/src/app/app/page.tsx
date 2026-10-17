"use client";

import { ArrowRight, Clock, Inbox, MessageSquare, TriangleAlert, Wallet, WalletCards } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { EventIcon } from "@/components/app/event-icon";
import { RevenueChart, type RevenueRow } from "@/components/app/revenue-chart";
import { StatCard, StatCardSkeleton } from "@/components/app/stat-card";
import { ActionStatusBadge, InvoiceStatusBadge } from "@/components/app/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { InvoicesResponse } from "@/lib/api";
import { useActions, useEvents, useHealth, useInvoices } from "@/lib/hooks";
import { ACTION_LABELS, EVENT_TITLES, TRY_ASKING, eventDetail, initials } from "@/lib/labels";
import { money, timeAgo } from "@/lib/utils";

const OPEN = new Set(["SENT", "UNPAID", "PARTIALLY_PAID"]);
const NOT_BILLED = new Set(["DRAFT", "CANCELLED"]);

function summarise(data: InvoicesResponse, currency: string) {
  const inCurrency = data.invoices.filter((i) => i.currency === currency && !NOT_BILLED.has(i.status));
  const billed = inCurrency.reduce((s, i) => s + i.amount, 0);
  const collected = data.collected_totals[currency] ?? 0;
  const overdue = data.unpaid.filter((r) => r.overdue);
  const byCustomer = new Map<string, RevenueRow>();
  for (const inv of inCurrency) {
    const row = byCustomer.get(inv.customer_name) ?? { customer: inv.customer_name, collected: 0, outstanding: 0 };
    row.collected += inv.paid_amount - inv.refunded_amount;
    if (OPEN.has(inv.status)) row.outstanding += inv.outstanding;
    byCustomer.set(inv.customer_name, row);
  }
  const chart = [...byCustomer.values()]
    .sort((a, b) => b.collected + b.outstanding - (a.collected + a.outstanding))
    .slice(0, 8);
  return {
    outstanding: data.outstanding_totals[currency] ?? 0,
    otherOutstanding: Object.entries(data.outstanding_totals).filter(([c]) => c !== currency),
    collected,
    collectedPct: billed > 0 ? Math.round((collected / billed) * 100) : 0,
    overdueCount: data.overdue_count,
    overdueAmount: overdue.filter((r) => r.currency === currency).reduce((s, r) => s + r.outstanding, 0),
    chart,
  };
}

function SectionCard({ title, description, action, children }: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
        {action ? <CardAction>{action}</CardAction> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function ListSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-9 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-2/5" />
            <Skeleton className="h-3 w-3/5" />
          </div>
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const { data: health } = useHealth();
  const { data: invoices } = useInvoices();
  const { actions: pending } = useActions("pending");
  const { events } = useEvents(undefined, 8);
  const currency = health?.currency ?? "INR";
  const summary = useMemo(() => (invoices ? summarise(invoices, currency) : null), [invoices, currency]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Who owes you, what came in, and what is waiting for your approval.</p>
      </div>

      <section aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
        {summary && pending ? (
          <>
            <StatCard
              label="Outstanding"
              icon={WalletCards}
              value={money(currency, summary.outstanding)}
              hint={
                summary.otherOutstanding.length
                  ? `+ ${summary.otherOutstanding.map(([c, v]) => money(c, v)).join(", ")}`
                  : `${invoices?.unpaid.length ?? 0} open invoices`
              }
            />
            <StatCard
              label="Collected"
              icon={Wallet}
              tone="success"
              value={money(currency, summary.collected)}
              hint={`${summary.collectedPct}% of billed`}
            />
            <StatCard
              label="Overdue"
              icon={TriangleAlert}
              tone={summary.overdueCount ? "destructive" : "default"}
              value={summary.overdueCount}
              hint={summary.overdueCount ? money(currency, summary.overdueAmount) : "Nothing overdue"}
            />
            <StatCard
              label="Waiting for you"
              icon={Inbox}
              tone={pending.length ? "warning" : "default"}
              value={pending.length}
              hint={pending.length ? "Drafts to approve" : "All caught up"}
            />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <StatCardSkeleton key={i} />)
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="min-w-0 space-y-6 lg:col-span-3">
          <SectionCard title="Collected vs outstanding" description={`By customer, in ${currency}`}>
            {summary ? (
              summary.chart.length ? (
                <RevenueChart rows={summary.chart} currency={currency} />
              ) : (
                <p className="py-10 text-center text-sm text-muted-foreground">No invoices yet.</p>
              )
            ) : (
              <Skeleton className="h-64 w-full" />
            )}
          </SectionCard>

          <SectionCard
            title="Unpaid invoices"
            description="Most overdue first"
            action={
              <Button asChild variant="ghost" size="sm">
                <Link href="/app/invoices">
                  View all <ArrowRight />
                </Link>
              </Button>
            }
          >
            {!invoices ? (
              <ListSkeleton />
            ) : invoices.unpaid.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">Everyone has paid. 🎉</p>
            ) : (
              <ul className="divide-y">
                {invoices.unpaid.slice(0, 6).map((r) => (
                  <li key={r.invoice_id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                    <span
                      className="flex size-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground"
                      aria-hidden="true"
                    >
                      {initials(r.customer)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{r.customer}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        #{r.number} · {r.description}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <span className="text-sm font-medium tabular-nums">{money(r.currency, r.outstanding)}</span>
                      {r.overdue ? (
                        <InvoiceStatusBadge status={r.status} overdue />
                      ) : (
                        <span className="text-xs text-muted-foreground">Due {r.due_date}</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>

        <div className="min-w-0 space-y-6 lg:col-span-2">
          <SectionCard
            title="Waiting for you"
            description="Drafts the assistant prepared"
            action={
              <Button asChild variant="ghost" size="sm">
                <Link href="/app/approvals">
                  Approvals <ArrowRight />
                </Link>
              </Button>
            }
          >
            {!pending ? (
              <ListSkeleton rows={2} />
            ) : pending.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">Nothing to approve right now.</p>
            ) : (
              <div className="space-y-3">
                {pending.slice(0, 2).map((a) => (
                  <Link
                    key={a.id}
                    href="/app/approvals"
                    className="block rounded-lg border p-3 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">
                        {ACTION_LABELS[a.action]} <span className="text-muted-foreground">#{a.id}</span>
                      </span>
                      <ActionStatusBadge status={a.status} />
                    </div>
                    <dl className="mt-2 space-y-0.5 text-xs">
                      {Object.entries(a.preview)
                        .filter(([k]) => k !== "Message")
                        .slice(0, 3)
                        .map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <dt className="w-20 shrink-0 text-muted-foreground">{k}</dt>
                            <dd className="min-w-0 truncate">{String(v ?? "")}</dd>
                          </div>
                        ))}
                    </dl>
                  </Link>
                ))}
                {pending.length > 2 ? (
                  <p className="text-xs text-muted-foreground">and {pending.length - 2} more in Approvals</p>
                ) : null}
              </div>
            )}
          </SectionCard>

          <SectionCard title="Try asking" description="Opens the assistant with the question filled in">
            <div className="flex flex-wrap gap-2">
              {TRY_ASKING.map((q) => (
                <Link
                  key={q}
                  href={`/app/chat?q=${encodeURIComponent(q)}`}
                  className="inline-flex items-center gap-1.5 rounded-full border bg-background px-3 py-1.5 text-xs text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <MessageSquare className="size-3 text-muted-foreground" aria-hidden="true" />
                  {q}
                </Link>
              ))}
            </div>
          </SectionCard>

          <SectionCard title="Recent activity">
            {!events ? (
              <ListSkeleton />
            ) : events.length === 0 ? (
              <p className="flex items-center justify-center gap-2 py-4 text-sm text-muted-foreground">
                <Clock className="size-4" aria-hidden="true" /> Nothing has happened yet.
              </p>
            ) : (
              <ol className="relative space-y-4 before:absolute before:bottom-2 before:left-4 before:top-2 before:w-px before:bg-border">
                {events.map((e) => (
                  <li key={e.id} className="relative flex gap-3">
                    <EventIcon type={e.type} className="ring-4 ring-card" />
                    <div className="min-w-0 flex-1 pt-0.5">
                      <p className="text-sm font-medium">{EVENT_TITLES[e.type] ?? e.type}</p>
                      <p className="truncate text-xs text-muted-foreground">{eventDetail(e)}</p>
                    </div>
                    <time dateTime={e.created} className="shrink-0 pt-0.5 text-xs text-muted-foreground">
                      {timeAgo(e.created)}
                    </time>
                  </li>
                ))}
              </ol>
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
