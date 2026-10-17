"use client";

import { History, Inbox, MessageSquare } from "lucide-react";
import Link from "next/link";

import { ActionCard } from "@/components/app/action-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Action } from "@/lib/api";
import { useActions } from "@/lib/hooks";

function EmptyState({ icon: Icon, title, text, cta }: { icon: typeof Inbox; title: string; text: string; cta?: boolean }) {
  return (
    <Card className="bg-grid">
      <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
        <span className="flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon className="size-5" aria-hidden="true" />
        </span>
        <div className="space-y-1">
          <p className="font-medium">{title}</p>
          <p className="text-sm text-muted-foreground">{text}</p>
        </div>
        {cta ? (
          <Button asChild>
            <Link href="/app/chat">
              <MessageSquare /> Ask the assistant
            </Link>
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function CardGrid({ items }: { items: Action[] }) {
  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      {items.map((a) => (
        <ActionCard key={a.id} action={a} />
      ))}
    </div>
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      {[0, 1].map((i) => (
        <Skeleton key={i} className="h-64 rounded-xl" />
      ))}
    </div>
  );
}

export default function ApprovalsPage() {
  const { actions } = useActions();
  const pending = actions?.filter((a) => a.status === "pending") ?? [];
  const history = actions?.filter((a) => a.status !== "pending") ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          Everything the assistant, the API or MCP drafted. Nothing is sent, charged or refunded until you approve it.
        </p>
      </div>
      <Tabs defaultValue="waiting">
        <TabsList>
          <TabsTrigger value="waiting">
            <Inbox /> Waiting ({actions ? pending.length : "…"})
          </TabsTrigger>
          <TabsTrigger value="history">
            <History /> History
          </TabsTrigger>
        </TabsList>
        <TabsContent value="waiting">
          {!actions ? (
            <GridSkeleton />
          ) : pending.length ? (
            <CardGrid items={pending} />
          ) : (
            <EmptyState
              icon={Inbox}
              title="Nothing waiting for you"
              text="Ask the assistant to draft an invoice, a reminder or a refund, and it will appear here for approval."
              cta
            />
          )}
        </TabsContent>
        <TabsContent value="history">
          {!actions ? (
            <GridSkeleton />
          ) : history.length ? (
            <CardGrid items={history} />
          ) : (
            <EmptyState icon={History} title="No decisions yet" text="Approved, rejected and failed actions will show up here." />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
