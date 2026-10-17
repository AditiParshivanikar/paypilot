import { Bot, Check, Clock, Send, ShieldCheck, X } from "lucide-react";

/**
 * The hero's product mock: real markup (browser chrome, chat, an approval card), not a screenshot, so it stays
 * sharp, follows the theme and costs no image bytes. It is decorative for screen readers, which get one summary.
 */
export function HeroMock() {
  return (
    <div
      role="img"
      aria-label="PayPilot drafting an invoice for Rahul Mehta for ₹4,000, waiting for the owner to approve it"
      className="relative mx-auto w-full max-w-md pb-6 lg:max-w-none"
    >
      <div aria-hidden="true" className="overflow-hidden rounded-xl border bg-card shadow-2xl shadow-primary/10">
        <div className="flex items-center gap-2 border-b bg-muted/50 px-3 py-2">
          <span className="flex gap-1.5">
            <span className="size-2.5 rounded-full bg-destructive/60" />
            <span className="size-2.5 rounded-full bg-warning/70" />
            <span className="size-2.5 rounded-full bg-success/70" />
          </span>
          <span className="mx-auto truncate rounded-md bg-background px-3 py-0.5 font-mono text-[11px] text-muted-foreground">
            paypilot.app/chat
          </span>
        </div>

        <div className="space-y-3 p-4 text-sm">
          <div className="flex justify-end">
            <p className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-3.5 py-2 text-primary-foreground">
              Invoice Rahul for ₹4,000 for logo design, due Friday
            </p>
          </div>
          <div className="flex gap-2">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Bot className="size-3.5" />
            </span>
            <p className="rounded-2xl rounded-tl-sm border bg-background px-3.5 py-2">
              Drafted it. It&apos;s waiting in your <strong>Approval inbox</strong>.
            </p>
          </div>

          <div className="ml-9 rounded-xl border bg-background p-3.5 shadow-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 font-semibold">
                <span className="flex size-7 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Send className="size-3.5" />
                </span>
                Send invoice
              </span>
              <span className="inline-flex items-center gap-1 rounded-md border border-warning/30 bg-warning/15 px-1.5 py-0.5 text-[11px] font-medium text-warning-foreground dark:text-warning">
                <Clock className="size-3" /> Waiting for you
              </span>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
              <div className="col-span-2 min-w-0">
                <dt className="text-muted-foreground">To</dt>
                <dd className="truncate font-medium">Rahul Mehta &lt;rahul.mehta@example.com&gt;</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Amount</dt>
                <dd className="font-medium tabular-nums">₹4,000.00</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Due</dt>
                <dd className="font-medium">Friday</dd>
              </div>
            </dl>
            <div className="mt-3 flex justify-end gap-2 border-t pt-3">
              <span className="inline-flex h-7 items-center gap-1 rounded-md border px-2.5 text-xs font-medium">
                <X className="size-3.5" /> Reject
              </span>
              <span className="inline-flex h-7 items-center gap-1 rounded-md bg-success px-2.5 text-xs font-medium text-success-foreground">
                <Check className="size-3.5" /> Approve
              </span>
            </div>
          </div>
        </div>
      </div>

      <div
        aria-hidden="true"
        className="absolute bottom-0 left-3 flex items-center gap-1.5 rounded-full border bg-background/95 px-3 py-1.5 text-xs font-medium shadow-lg backdrop-blur sm:-left-4"
      >
        <ShieldCheck className="size-3.5 text-success" />
        Under ₹50,000 cap · approved customer
      </div>
    </div>
  );
}
