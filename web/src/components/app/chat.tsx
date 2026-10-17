"use client";

import { Bot, Loader2, SendHorizontal, ShieldAlert, Sparkles, Trash2 } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { ActionCard } from "@/components/app/action-card";
import { LogoMark } from "@/components/app/logo";
import { Markdown } from "@/components/app/markdown";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, type Action, type ChatTurn, type TableRowData } from "@/lib/api";
import { refreshAll, useActions, useHealth } from "@/lib/hooks";
import { CHAT_SUGGESTIONS } from "@/lib/labels";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "paypilot.chat.v1";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  table?: TableRowData[] | null;
  tainted?: boolean;
  /** Snapshot at the time of the reply; the live card comes from the actions list. */
  actions?: Action[];
  error?: boolean;
}

function loadMessages(): Message[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? (parsed as Message[]) : [];
  } catch {
    return []; // private mode, blocked storage, or corrupt JSON: start fresh
  }
}

function saveMessages(messages: Message[]) {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-60)));
  } catch {
    // storage full or unavailable: the chat still works, it just won't survive a reload
  }
}

const newId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

function ReplyTable({ rows }: { rows: TableRowData[] }) {
  const columns = Object.keys(rows[0] ?? {});
  return (
    <div className="max-h-80 overflow-auto rounded-lg border bg-card">
      <Table>
        <TableHeader className="sticky top-0 bg-card">
          <TableRow>
            {columns.map((c) => (
              <TableHead key={c}>{c}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {columns.map((c) => (
                <TableCell key={c} className={typeof row[c] === "number" ? "text-right tabular-nums" : undefined}>
                  {String(row[c] ?? "")}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function EmptyState({ onPick, disabled }: { onPick: (prompt: string) => void; disabled: boolean }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center gap-6 py-6 text-center sm:py-10">
      <LogoMark className="size-12" />
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">What should we get paid for today?</h1>
        <p className="text-sm text-muted-foreground">
          Ask in plain English. I draft invoices, reminders and refunds; nothing is sent until you approve it.
        </p>
      </div>
      <div className="grid w-full grid-cols-1 gap-3 text-left sm:grid-cols-2 lg:grid-cols-3">
        {CHAT_SUGGESTIONS.map((s) => (
          <button
            key={s.prompt}
            type="button"
            disabled={disabled}
            onClick={() => onPick(s.prompt)}
            className="group rounded-xl border bg-card p-4 text-left shadow-xs transition-colors hover:border-primary/40 hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Sparkles className="size-3.5 text-primary" aria-hidden="true" /> {s.title}
            </span>
            <span className="mt-1.5 block text-sm">{s.prompt}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function AssistantMessage({ message, live }: { message: Message; live: Map<number, Action> }) {
  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Bot className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1 space-y-3">
        {message.tainted ? (
          <div role="alert" className="flex gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>
              This reply read text that looks like hidden instructions (for example in an invoice note). PayPilot ignored
              them, and refunds are blocked for this message.
            </span>
          </div>
        ) : null}
        <div className={cn("rounded-2xl rounded-tl-sm border bg-card px-4 py-3", message.error && "border-destructive/40 text-destructive")}>
          <Markdown text={message.content} />
        </div>
        {message.table?.length ? <ReplyTable rows={message.table} /> : null}
        {message.actions?.map((a) => (
          <ActionCard key={a.id} action={live.get(a.id) ?? a} />
        ))}
      </div>
    </div>
  );
}

export function Chat() {
  const { data: health } = useHealth();
  const { actions } = useActions();
  const [messages, setMessages] = React.useState<Message[]>(loadMessages);
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const autoSent = React.useRef(false);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const live = React.useMemo(() => new Map((actions ?? []).map((a) => [a.id, a])), [actions]);

  React.useEffect(() => saveMessages(messages), [messages]);
  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  const send = React.useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;
      const history: ChatTurn[] = messages.slice(-20).map((m) => ({ role: m.role, content: m.content }));
      setMessages((prev) => [...prev, { id: newId(), role: "user", content: message }]);
      setDraft("");
      setBusy(true);
      try {
        const reply = await api.chat(message, history);
        setMessages((prev) => [
          ...prev,
          { id: newId(), role: "assistant", content: reply.text, table: reply.table, tainted: reply.tainted, actions: reply.actions },
        ]);
        void refreshAll();
      } catch (e) {
        setMessages((prev) => [
          ...prev,
          { id: newId(), role: "assistant", content: e instanceof Error ? e.message : "Something went wrong.", error: true },
        ]);
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, messages],
  );

  // ?q=... (from the dashboard's "Try asking" chips) sends once, then the parameter is removed from the URL.
  const q = searchParams.get("q");
  React.useEffect(() => {
    if (!q || autoSent.current) return;
    const timer = window.setTimeout(() => {
      if (autoSent.current) return;
      autoSent.current = true;
      router.replace(pathname);
      void send(q);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [q, pathname, router, send]);

  function clearChat() {
    setMessages([]);
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(draft);
    }
  }

  return (
    <div className="flex min-h-[calc(100dvh-3.5rem-3rem)] flex-col">
      <div className="flex-1 space-y-6 pb-4">
        {messages.length === 0 ? (
          <EmptyState onPick={(p) => void send(p)} disabled={busy} />
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground sm:max-w-[75%]">
                  {m.content}
                </p>
              </div>
            ) : (
              <AssistantMessage key={m.id} message={m} live={live} />
            ),
          )
        )}
        {busy ? (
          <div role="status" className="flex items-center gap-2 pl-11 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Thinking…
          </div>
        ) : null}
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-0 -mx-4 border-t bg-background/95 px-4 pb-3 pt-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(draft);
          }}
          className="flex items-end gap-2"
        >
          <Textarea
            ref={inputRef}
            aria-label="Message"
            placeholder="Invoice Rahul for ₹4,000 for logo design, due Friday"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            maxLength={2000}
            className="max-h-40 min-h-11 resize-none"
          />
          <Button type="submit" size="icon" className="size-11 shrink-0" aria-label="Send" disabled={busy || !draft.trim()}>
            {busy ? <Loader2 className="animate-spin" /> : <SendHorizontal />}
          </Button>
        </form>
        <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {health ? (health.agent === "llm" ? "Claude agent" : "Rule-based parser (demo)") : " "} · Enter to send,
            Shift+Enter for a new line
          </span>
          {messages.length ? (
            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={clearChat}>
              <Trash2 /> Clear chat
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
