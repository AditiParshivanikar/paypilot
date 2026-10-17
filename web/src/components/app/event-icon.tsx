import {
  Ban,
  CheckCheck,
  CheckCircle2,
  Clock,
  type LucideIcon,
  Send,
  ShieldX,
  TriangleAlert,
  Undo2,
  Wallet,
  XCircle,
  BellRing,
} from "lucide-react";

import type { EventType } from "@/lib/api";
import { cn } from "@/lib/utils";

const META: Record<EventType, { icon: LucideIcon; tone: string }> = {
  "action.proposed": { icon: Clock, tone: "bg-warning/15 text-warning-foreground dark:text-warning" },
  "action.blocked": { icon: ShieldX, tone: "bg-destructive/10 text-destructive" },
  "action.approved": { icon: CheckCheck, tone: "bg-primary/10 text-primary" },
  "action.rejected": { icon: XCircle, tone: "bg-muted text-muted-foreground" },
  "action.failed": { icon: TriangleAlert, tone: "bg-destructive/10 text-destructive" },
  "invoice.created": { icon: Send, tone: "bg-primary/10 text-primary" },
  "invoice.paid": { icon: Wallet, tone: "bg-success/12 text-success" },
  "invoice.refunded": { icon: Undo2, tone: "bg-muted text-muted-foreground" },
  "invoice.cancelled": { icon: Ban, tone: "bg-muted text-muted-foreground" },
  "reminder.sent": { icon: BellRing, tone: "bg-primary/10 text-primary" },
  "refund.completed": { icon: CheckCircle2, tone: "bg-success/12 text-success" },
};

export function EventIcon({ type, className }: { type: EventType; className?: string }) {
  const meta = META[type] ?? META["action.approved"];
  const Icon = meta.icon;
  return (
    <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-full", meta.tone, className)}>
      <Icon className="size-4" aria-hidden="true" />
    </span>
  );
}
