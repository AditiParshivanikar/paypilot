"use client";

import { Bot, ChevronRight, KeyRound, Server, ShieldCheck, User, Webhook, Zap, type LucideIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { EventIcon } from "@/components/app/event-icon";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { AuditEntry, EventType, PayPilotEvent } from "@/lib/api";
import { useAudit, useEvents } from "@/lib/hooks";
import { ACTOR_LABELS, EVENT_TITLES, eventDetail } from "@/lib/labels";
import { cn, timeAgo } from "@/lib/utils";

const ACTOR_ICONS: Record<string, LucideIcon> = {
  user: User,
  agent: Bot,
  policy: ShieldCheck,
  executor: Zap,
  webhook: Webhook,
  system: Server,
};

function actorIcon(actor: string): LucideIcon {
  return actor.startsWith("api:") ? KeyRound : (ACTOR_ICONS[actor] ?? Server);
}

function actorLabel(actor: string): string {
  return actor.startsWith("api:") ? `API key ${actor.slice(4)}` : (ACTOR_LABELS[actor] ?? actor);
}

/** Toggle chips: an empty selection means "all". */
function FilterChips({ options, selected, onChange, label }: {
  options: { value: string; label: string; count: number }[];
  selected: string[];
  onChange: (next: string[]) => void;
  label: string;
}) {
  const toggle = (v: string) => onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  const chip = (active: boolean) =>
    cn(
      "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      active ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-accent",
    );
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-2">
      <button type="button" className={chip(selected.length === 0)} aria-pressed={selected.length === 0} onClick={() => onChange([])}>
        All
      </button>
      {options.map((o) => (
        <button key={o.value} type="button" className={chip(selected.includes(o.value))} aria-pressed={selected.includes(o.value)} onClick={() => toggle(o.value)}>
          {o.label} <span className="opacity-70">{o.count}</span>
        </button>
      ))}
    </div>
  );
}

function JsonDetails({ value }: { value: unknown }) {
  if (value === null || value === undefined || (typeof value === "object" && Object.keys(value as object).length === 0)) return null;
  return (
    <details className="group mt-1.5">
      <summary className="inline-flex cursor-pointer select-none items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight className="size-3 transition-transform group-open:rotate-90" aria-hidden="true" /> Details
      </summary>
      <pre className="mt-1.5 max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs leading-relaxed">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-4">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex gap-3">
          <Skeleton className="size-8 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}

function countBy<T>(items: T[], key: (item: T) => string) {
  const counts = new Map<string, number>();
  for (const it of items) counts.set(key(it), (counts.get(key(it)) ?? 0) + 1);
  return counts;
}

function AuditLog() {
  const { entries } = useAudit(500);
  const [actors, setActors] = useState<string[]>([]);
  const options = useMemo(
    () => [...countBy(entries ?? [], (e) => e.actor)].map(([value, count]) => ({ value, label: actorLabel(value), count })),
    [entries],
  );
  const shown = (entries ?? []).filter((e: AuditEntry) => actors.length === 0 || actors.includes(e.actor));
  return (
    <Card>
      <CardContent className="space-y-5">
        <FilterChips label="Filter by actor" options={options} selected={actors} onChange={setActors} />
        {!entries ? (
          <ListSkeleton />
        ) : shown.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Nothing logged yet.</p>
        ) : (
          <ol className="divide-y">
            {shown.map((e) => {
              const Icon = actorIcon(e.actor);
              return (
                <li key={e.id} className="flex gap-3 py-3 first:pt-0 last:pb-0">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                    <Icon className="size-4" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <p className="text-sm">
                        <span className="font-medium">{actorLabel(e.actor)}</span>{" "}
                        <span className="text-muted-foreground">{e.event}</span>
                        {e.action_id ? <span className="text-muted-foreground"> · action #{e.action_id}</span> : null}
                      </p>
                      <time dateTime={e.ts} title={e.ts} className="text-xs text-muted-foreground">
                        {timeAgo(e.ts)}
                      </time>
                    </div>
                    <JsonDetails value={e.detail} />
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function EventLog() {
  const { events } = useEvents(undefined, 300);
  const [types, setTypes] = useState<string[]>([]);
  const options = useMemo(
    () =>
      [...countBy(events ?? [], (e) => e.type)].map(([value, count]) => ({
        value,
        label: EVENT_TITLES[value as EventType] ?? value,
        count,
      })),
    [events],
  );
  const shown = (events ?? []).filter((e: PayPilotEvent) => types.length === 0 || types.includes(e.type));
  return (
    <Card>
      <CardContent className="space-y-5">
        <FilterChips label="Filter by event type" options={options} selected={types} onChange={setTypes} />
        {!events ? (
          <ListSkeleton />
        ) : shown.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No events yet.</p>
        ) : (
          <ol className="divide-y">
            {shown.map((e) => (
              <li key={e.id} className="flex gap-3 py-3 first:pt-0 last:pb-0">
                <EventIcon type={e.type} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                    <p className="text-sm">
                      <span className="font-medium">{EVENT_TITLES[e.type] ?? e.type}</span>{" "}
                      <code className="text-xs text-muted-foreground">{e.type}</code>
                    </p>
                    <time dateTime={e.created} title={e.created} className="text-xs text-muted-foreground">
                      {timeAgo(e.created)}
                    </time>
                  </div>
                  <p className="truncate text-xs text-muted-foreground">{eventDetail(e)}</p>
                  <JsonDetails value={e.data} />
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

export default function ActivityPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
        <p className="text-sm text-muted-foreground">
          Every message, proposal, safety decision, approval, webhook and provider call, with the reason.
        </p>
      </div>
      <Tabs defaultValue="audit">
        <TabsList>
          <TabsTrigger value="audit">Audit log</TabsTrigger>
          <TabsTrigger value="events">Events</TabsTrigger>
        </TabsList>
        <TabsContent value="audit">
          <AuditLog />
        </TabsContent>
        <TabsContent value="events">
          <EventLog />
        </TabsContent>
      </Tabs>
    </div>
  );
}
