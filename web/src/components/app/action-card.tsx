"use client";

import { BellRing, Check, CircleX, Loader2, Send, ShieldCheck, TriangleAlert, Undo2, type LucideIcon, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ActionStatusBadge } from "@/components/app/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { ApiError, api, type Action, type ActionType } from "@/lib/api";
import { refreshAll } from "@/lib/hooks";
import { ACTION_DONE, ACTION_TITLES } from "@/lib/labels";
import { cn, timeAgo } from "@/lib/utils";

const ICONS: Record<ActionType, LucideIcon> = { create_invoice: Send, send_reminder: BellRing, refund: Undo2 };
const LONG_TEXT_KEYS = new Set(["Message", "Note"]);

function failureReasons(action: Action): string[] {
  const result = action.result ?? {};
  const blocked = (result as { blocked?: unknown }).blocked;
  if (Array.isArray(blocked)) return blocked.map(String);
  const error = (result as { error?: unknown }).error;
  return error ? [String(error)] : [];
}

function outcome(action: Action): string | null {
  if (action.status !== "executed" || !action.result) return null;
  const r = action.result as Record<string, unknown>;
  if (action.action === "create_invoice" && r.number) return `Sent as invoice #${String(r.number)}.`;
  if (action.action === "send_reminder") {
    if (r.to) return `Delivered to ${String(r.to)}.`;
    return r.channel === "provider" ? "The payment provider emailed it to the customer." : null;
  }
  if (action.action === "refund") return "Refunded to the original payer.";
  return null;
}

export function ActionCard({ action, className }: { action: Action; className?: string }) {
  const [busy, setBusy] = React.useState<"approve" | "reject" | null>(null);
  const Icon = ICONS[action.action] ?? Send;
  const preview = Object.entries(action.preview ?? {});
  const fields = preview.filter(([k]) => !LONG_TEXT_KEYS.has(k));
  const note = typeof action.params?.note === "string" && action.params.note ? action.params.note : null;
  const longText = preview.filter(([k, v]) => LONG_TEXT_KEYS.has(k) && v).map(([k, v]) => [k, String(v)] as const);
  if (note && !longText.length) longText.push(["Note", note]);
  const reasons = action.status === "failed" ? failureReasons(action) : [];
  const done = outcome(action);

  async function decide(verb: "approve" | "reject") {
    setBusy(verb);
    try {
      if (verb === "reject") {
        await api.reject(action.id);
        toast(`Rejected #${action.id}. Nothing was sent.`);
      } else {
        const res = await api.approve(action.id);
        if (res.ok) {
          toast.success(ACTION_DONE[action.action] ?? "Done", { description: outcome(res.action) ?? undefined });
        } else if (res.action?.result && "blocked" in res.action.result) {
          toast.error("Stopped by the safety gate", { description: failureReasons(res.action).join(" ") });
        } else {
          toast.error("It didn't go through", { description: res.error });
        }
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) toast.info("This was already decided.");
      else toast.error(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(null);
      void refreshAll();
    }
  }

  return (
    <Card className={cn("gap-4", className)}>
      <CardHeader className="gap-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Icon className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h3 className="truncate font-semibold leading-tight">{ACTION_TITLES[action.action] ?? action.action}</h3>
              <p className="text-xs text-muted-foreground">
                #{action.id} · <time dateTime={action.created_at}>{timeAgo(action.created_at)}</time>
                {action.source && action.source !== "chat" ? ` · via ${action.source}` : null}
              </p>
            </div>
          </div>
          <ActionStatusBadge status={action.status} className="shrink-0" />
        </div>
        {action.rationale ? <p className="text-sm text-muted-foreground">Why: {action.rationale}</p> : null}
      </CardHeader>

      <CardContent className="space-y-3">
        {fields.length ? (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {fields.map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-xs text-muted-foreground">{k}</dt>
                <dd className="break-words font-medium">{String(v ?? "—")}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {longText.map(([k, v]) => (
          <div key={k} className="rounded-md bg-muted px-3 py-2 text-sm">
            <p className="text-xs text-muted-foreground">{k}</p>
            <p className="whitespace-pre-wrap break-words">{v}</p>
          </div>
        ))}

        {action.warnings?.length ? (
          <div role="note" className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
            {action.warnings.map((w) => (
              <p key={w} className="flex gap-2">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-foreground dark:text-warning" aria-hidden="true" />
                <span>{w}</span>
              </p>
            ))}
          </div>
        ) : null}

        {reasons.length ? (
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {reasons.map((r) => (
              <p key={r} className="flex gap-2">
                <CircleX className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>{r}</span>
              </p>
            ))}
          </div>
        ) : null}

        {done ? (
          <p className="flex items-center gap-2 text-sm text-success">
            <Check className="size-4" aria-hidden="true" /> {done}
          </p>
        ) : null}
      </CardContent>

      {action.status === "pending" ? (
        <CardFooter className="flex-col items-stretch gap-3 border-t pt-4 sm:flex-row sm:items-center">
          <p className="flex flex-1 items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" aria-hidden="true" /> Rules are checked again when you approve
          </p>
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1 sm:flex-none" disabled={busy !== null} onClick={() => decide("reject")}>
              {busy === "reject" ? <Loader2 className="animate-spin" /> : <X />} Reject
            </Button>
            <Button variant="success" className="flex-1 sm:flex-none" disabled={busy !== null} onClick={() => decide("approve")}>
              {busy === "approve" ? <Loader2 className="animate-spin" /> : <Check />} Approve
            </Button>
          </div>
        </CardFooter>
      ) : null}
    </Card>
  );
}
