"use client";

import { ChevronDown, Download, Loader2, Search, ShieldAlert, Wallet } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { InvoiceStatusBadge } from "@/components/app/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type InvoiceRow } from "@/lib/api";
import { refreshAll, useHealth, useInvoices } from "@/lib/hooks";
import { money } from "@/lib/utils";

type Filter = "all" | "unpaid" | "overdue" | "paid";
const OPEN = new Set(["SENT", "UNPAID", "PARTIALLY_PAID"]);
const PAID = new Set(["PAID", "PARTIALLY_REFUNDED", "REFUNDED"]);
const EXPORTS = [
  { format: "csv", label: "CSV (Excel, Sheets)" },
  { format: "json", label: "JSON" },
  { format: "tally", label: "Tally XML" },
] as const;

export default function InvoicesPage() {
  const { data, isLoading } = useInvoices();
  const { data: health } = useHealth();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [paying, setPaying] = useState<string | null>(null);
  const simulated = health?.payments.simulated ?? false;

  const overdueIds = useMemo(() => new Set(data?.unpaid.filter((r) => r.overdue).map((r) => r.invoice_id)), [data]);
  const counts = useMemo(() => {
    const all = data?.invoices ?? [];
    return {
      all: all.length,
      unpaid: all.filter((i) => OPEN.has(i.status)).length,
      overdue: overdueIds.size,
      paid: all.filter((i) => PAID.has(i.status)).length,
    };
  }, [data, overdueIds]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.invoices ?? [])
      .filter((i) =>
        filter === "unpaid" ? OPEN.has(i.status) : filter === "overdue" ? overdueIds.has(i.id) : filter === "paid" ? PAID.has(i.status) : true,
      )
      .filter((i) => !q || [i.number, i.customer_name, i.customer_email, i.description].some((v) => v?.toLowerCase().includes(q)));
  }, [data, filter, query, overdueIds]);
  const flagged = rows.some((i) => i.security_flags.length);

  async function customerPays(inv: InvoiceRow) {
    setPaying(inv.id);
    try {
      const res = await api.simulatePayment(inv.number);
      toast.success("Payment received", {
        description: `#${inv.number} from ${inv.customer_name} is now ${res.invoice_status === "PAID" ? "paid" : String(res.invoice_status).toLowerCase()}.`,
      });
      await refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "The payment could not be simulated.");
    } finally {
      setPaying(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Invoices</h1>
          <p className="text-sm text-muted-foreground">
            Straight from {health ? health.payments.label : "your payment provider"}. Paid webhooks update this list.
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline">
              <Download /> Export <ChevronDown className="opacity-60" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {EXPORTS.map((e) => (
              <DropdownMenuItem key={e.format} asChild>
                <a href={api.exportUrl(e.format)} download>
                  {e.label}
                </a>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)}>
          <TabsList className="max-w-full overflow-x-auto">
            <TabsTrigger value="all">All ({counts.all})</TabsTrigger>
            <TabsTrigger value="unpaid">Unpaid ({counts.unpaid})</TabsTrigger>
            <TabsTrigger value="overdue">Overdue ({counts.overdue})</TabsTrigger>
            <TabsTrigger value="paid">Paid ({counts.paid})</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            type="search"
            aria-label="Search invoices"
            placeholder="Search customer, number…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="pl-8"
          />
        </div>
      </div>

      <Card className="gap-0 py-0">
        <CardContent className="px-0">
          {isLoading && !data ? (
            <div className="space-y-3 p-5">
              {Array.from({ length: 5 }, (_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <p className="p-10 text-center text-sm text-muted-foreground">No invoices match.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-5">Invoice</TableHead>
                  <TableHead>Customer</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Outstanding</TableHead>
                  <TableHead>Due</TableHead>
                  {simulated ? <TableHead className="pr-5"><span className="sr-only">Actions</span></TableHead> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((inv) => (
                  <TableRow key={inv.id}>
                    <TableCell className="pl-5">
                      <div className="flex items-center gap-1.5 font-medium">
                        #{inv.number}
                        {inv.security_flags.length ? (
                          <span title={inv.security_flags.join("; ")}>
                            <ShieldAlert className="size-4 text-destructive" aria-hidden="true" />
                            <span className="sr-only">Contains text that looks like hidden instructions</span>
                          </span>
                        ) : null}
                      </div>
                      <div className="max-w-56 truncate text-xs text-muted-foreground">{inv.description}</div>
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">{inv.customer_name}</div>
                      <div className="text-xs text-muted-foreground">{inv.customer_email}</div>
                    </TableCell>
                    <TableCell>
                      <InvoiceStatusBadge status={inv.status} overdue={overdueIds.has(inv.id)} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{money(inv.currency, inv.amount)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {OPEN.has(inv.status) ? money(inv.currency, inv.outstanding) : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="tabular-nums">{inv.due_date ?? "—"}</TableCell>
                    {simulated ? (
                      <TableCell className="pr-5 text-right">
                        {OPEN.has(inv.status) ? (
                          <Button size="sm" variant="outline" disabled={paying !== null} onClick={() => customerPays(inv)}>
                            {paying === inv.id ? <Loader2 className="animate-spin" /> : <Wallet />} Customer pays
                          </Button>
                        ) : null}
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {flagged ? (
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden="true" />
          Marked invoices contain text that looks like hidden instructions (for example in a customer note). PayPilot
          treats it as data, never as instructions, and blocks refunds in any turn that reads it.
        </p>
      ) : null}
      {simulated ? (
        <p className="text-xs text-muted-foreground">
          &quot;Customer pays&quot; simulates the payment provider&apos;s signed &quot;paid&quot; webhook, exactly as it
          would arrive in production.
        </p>
      ) : null}
    </div>
  );
}
