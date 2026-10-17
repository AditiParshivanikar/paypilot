"use client";

import { CheckCircle2, Loader2, Lock, RotateCcw, Save, ShieldCheck, UserPlus, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type ActionType, type Customer, type Policy, type ReminderChannel } from "@/lib/api";
import { refreshAll, useCustomers, usePolicy, useSession } from "@/lib/hooks";
import { ACTION_TYPE_LABELS, CHANNEL_LABELS } from "@/lib/labels";

const ACTIONS: ActionType[] = ["create_invoice", "send_reminder", "refund"];
const CHANNELS: ReminderChannel[] = ["provider", "email", "whatsapp"];

function NumberField({ id, label, hint, value, onChange, step = "any", min = 0.01 }: {
  id: string;
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  /** "any" for money; a fixed step makes the browser reject values off the min+n*step grid. */
  step?: number | "any";
  min?: number;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" inputMode="decimal" min={min} step={step} value={Number.isFinite(value) ? value : ""}
             onChange={(e) => onChange(e.target.valueAsNumber)} required />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

function ToggleRow({ id, label, checked, onChange }: { id: string; label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <Label htmlFor={id} className="font-normal">{label}</Label>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function PolicyForm({ policy, canEdit }: { policy: Policy; canEdit: boolean }) {
  const [form, setForm] = useState(policy);
  const [currencies, setCurrencies] = useState(policy.allowed_currencies.join(", "));
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const set = <K extends keyof Policy>(key: K, value: Policy[K]) => setForm((f) => ({ ...f, [key]: value }));
  const toggleIn = <T extends string>(list: T[], item: T, on: boolean) =>
    on ? [...new Set([...list, item])] : list.filter((x) => x !== item);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy("save");
    try {
      // Everything except owner_notifications, which the backend keeps as it is.
      await api.savePolicy({
        allowed_actions: form.allowed_actions,
        max_invoice_amount: form.max_invoice_amount,
        max_refund_amount: form.max_refund_amount,
        daily_refund_limit: form.daily_refund_limit,
        max_reminders_per_invoice_per_day: form.max_reminders_per_invoice_per_day,
        allowed_currencies: currencies.split(/[\s,]+/).filter(Boolean),
        require_approved_customer: form.require_approved_customer,
        reminder_channels: form.reminder_channels,
      });
      toast.success("Safety rules saved", { description: "They apply to new drafts and are re-checked at every approval." });
      await refreshAll();
    } catch (err) {
      toast.error("Rules not saved", { description: err instanceof Error ? err.message : undefined });
    } finally {
      setBusy(null);
    }
  }

  async function reset() {
    if (!window.confirm("Reset every safety rule to the defaults?")) return;
    setBusy("reset");
    try {
      await api.resetPolicy();
      toast.success("Safety rules reset to the defaults");
      await refreshAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not reset the rules.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <form onSubmit={save}>
      <fieldset disabled={!canEdit || busy !== null} className="contents">
        <CardContent className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField id="max-invoice" label="Max invoice amount" hint="Bigger invoices are blocked." value={form.max_invoice_amount}
                         onChange={(v) => set("max_invoice_amount", v)} />
            <NumberField id="max-refund" label="Max single refund" hint="Per refund." value={form.max_refund_amount}
                         onChange={(v) => set("max_refund_amount", v)} />
            <NumberField id="daily-refund" label="Daily refund limit" hint="Total of refunds executed today." value={form.daily_refund_limit}
                         onChange={(v) => set("daily_refund_limit", v)} />
            <NumberField id="reminders-per-day" label="Reminders per invoice per day" hint="Avoids spamming customers."
                         value={form.max_reminders_per_invoice_per_day} onChange={(v) => set("max_reminders_per_invoice_per_day", v)} step={1} min={0} />
          </div>
          <Separator />
          <div className="grid gap-6 md:grid-cols-2">
            <fieldset>
              <legend className="mb-1 text-sm font-medium">Allowed actions</legend>
              {ACTIONS.map((a) => (
                <ToggleRow key={a} id={`action-${a}`} label={ACTION_TYPE_LABELS[a]} checked={form.allowed_actions.includes(a)}
                           onChange={(on) => set("allowed_actions", toggleIn(form.allowed_actions, a, on))} />
              ))}
            </fieldset>
            <fieldset>
              <legend className="mb-1 text-sm font-medium">Reminder channels</legend>
              {CHANNELS.map((c) => (
                <ToggleRow key={c} id={`channel-${c}`} label={CHANNEL_LABELS[c]} checked={form.reminder_channels.includes(c)}
                           onChange={(on) => set("reminder_channels", toggleIn(form.reminder_channels, c, on))} />
              ))}
            </fieldset>
          </div>
          <Separator />
          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="currencies">Allowed currencies</Label>
              <Input id="currencies" value={currencies} onChange={(e) => setCurrencies(e.target.value.toUpperCase())} placeholder="INR, USD" />
              <p className="text-xs text-muted-foreground">3-letter codes, separated by commas.</p>
            </div>
            <div className="rounded-lg border p-3">
              <ToggleRow id="approved-only" label="Only approved customers" checked={form.require_approved_customer}
                         onChange={(on) => set("require_approved_customer", on)} />
              <p className="text-xs text-muted-foreground">Invoices and reminders only go to customers marked approved below.</p>
            </div>
          </div>
        </CardContent>
        <CardFooter className="mt-6 flex-wrap gap-2 border-t pt-5">
          <Button type="submit">{busy === "save" ? <Loader2 className="animate-spin" /> : <Save />} Save rules</Button>
          <Button type="button" variant="outline" onClick={reset}>
            {busy === "reset" ? <Loader2 className="animate-spin" /> : <RotateCcw />} Reset
          </Button>
        </CardFooter>
      </fieldset>
    </form>
  );
}

function CustomerRow({ customer, canEdit }: { customer: Customer; canEdit: boolean }) {
  const [busy, setBusy] = useState(false);
  async function setApproved(approved: boolean) {
    setBusy(true);
    try {
      await api.saveCustomer({ name: customer.name, email: customer.email, phone: customer.phone, approved });
      toast.success(`${customer.name} is ${approved ? "approved" : "no longer approved"}`);
      await refreshAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not update the customer.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{customer.name}</p>
        <p className="truncate text-xs text-muted-foreground">
          {customer.email}
          {customer.phone ? ` · ${customer.phone}` : " · no WhatsApp number"}
        </p>
      </div>
      {customer.approved ? (
        <Badge variant="success"><CheckCircle2 aria-hidden="true" /> Approved</Badge>
      ) : (
        <Badge variant="secondary"><XCircle aria-hidden="true" /> Not approved</Badge>
      )}
      <Switch aria-label={`Approve ${customer.name}`} checked={customer.approved} disabled={!canEdit || busy} onCheckedChange={setApproved} />
    </li>
  );
}

function AddCustomer({ canEdit }: { canEdit: boolean }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.saveCustomer({ name: name.trim(), email: email.trim(), phone: phone.trim() || null, approved: true });
      toast.success(`${name.trim()} added and approved`);
      setName("");
      setEmail("");
      setPhone("");
      await refreshAll();
    } catch (err) {
      toast.error("Customer not saved", { description: err instanceof Error ? err.message : undefined });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={add} className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <p className="text-sm font-medium">Add or update a customer</p>
      <fieldset disabled={!canEdit || busy} className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="customer-name">Name</Label>
          <Input id="customer-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="customer-email">Email</Label>
          <Input id="customer-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="customer-phone">WhatsApp number</Label>
          <Input id="customer-phone" type="tel" placeholder="+919800000001" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
      </fieldset>
      <Button type="submit" disabled={!canEdit || busy || !name.trim() || !email.trim()}>
        {busy ? <Loader2 className="animate-spin" /> : <UserPlus />} Save customer
      </Button>
    </form>
  );
}

export default function SettingsPage() {
  const { data: session } = useSession();
  const { data: policy } = usePolicy();
  const { customers } = useCustomers();
  const canEdit = session?.scopes.includes("admin") ?? false;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Safety rules</h1>
        <p className="text-sm text-muted-foreground">
          The deterministic gate every draft must pass, when it is proposed and again when you approve it.
        </p>
      </div>
      {!canEdit && session ? (
        <p className="flex items-center gap-2 rounded-lg border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">
          <Lock className="size-4" aria-hidden="true" /> You can view the rules, but changing them needs the admin scope.
        </p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-success" aria-hidden="true" /> Safety gate
          </CardTitle>
          <CardDescription>Limits and allow-lists. Anything outside them is blocked with a reason.</CardDescription>
        </CardHeader>
        {policy ? (
          <PolicyForm key={JSON.stringify(policy)} policy={policy} canEdit={canEdit} />
        ) : (
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-16" />)}
          </CardContent>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Approved customers</CardTitle>
          <CardDescription>
            Invoices and reminders always go to the email or WhatsApp number on file here, never to an address in a message.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {!customers ? (
            <Skeleton className="h-32 w-full" />
          ) : (
            <ul className="divide-y">
              {customers.map((c) => (
                <CustomerRow key={`${c.id}-${c.approved}`} customer={c} canEdit={canEdit} />
              ))}
            </ul>
          )}
          <AddCustomer canEdit={canEdit} />
        </CardContent>
      </Card>
    </div>
  );
}
