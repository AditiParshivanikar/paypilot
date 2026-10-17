import {
  AlertTriangle,
  Ban,
  CheckCheck,
  CheckCircle2,
  CircleDashed,
  Clock,
  FileText,
  type LucideIcon,
  Send,
  ShieldX,
  Undo2,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ActionStatus, InvoiceStatus } from "@/lib/api";
import { INVOICE_STATUS_LABELS } from "@/lib/labels";

type Variant = "default" | "secondary" | "outline" | "success" | "warning" | "destructive";
interface Meta {
  label: string;
  icon: LucideIcon;
  variant: Variant;
}

/** Every status has an icon and a word, so it never depends on colour alone. */
const ACTION_META: Record<ActionStatus | "blocked", Meta> = {
  pending: { label: "Waiting for you", icon: Clock, variant: "warning" },
  approved: { label: "Approved", icon: CheckCheck, variant: "secondary" },
  executed: { label: "Done", icon: CheckCircle2, variant: "success" },
  rejected: { label: "Rejected", icon: XCircle, variant: "secondary" },
  failed: { label: "Failed", icon: AlertTriangle, variant: "destructive" },
  blocked: { label: "Blocked", icon: ShieldX, variant: "destructive" },
};

const INVOICE_META: Record<InvoiceStatus, Omit<Meta, "label">> = {
  DRAFT: { icon: FileText, variant: "outline" },
  SENT: { icon: Send, variant: "outline" },
  UNPAID: { icon: Clock, variant: "warning" },
  PARTIALLY_PAID: { icon: CircleDashed, variant: "warning" },
  PAID: { icon: CheckCircle2, variant: "success" },
  PARTIALLY_REFUNDED: { icon: Undo2, variant: "secondary" },
  REFUNDED: { icon: Undo2, variant: "secondary" },
  CANCELLED: { icon: Ban, variant: "outline" },
};

export function ActionStatusBadge({ status, className }: { status: ActionStatus | "blocked"; className?: string }) {
  const meta = ACTION_META[status] ?? ACTION_META.pending;
  const Icon = meta.icon;
  return (
    <Badge variant={meta.variant} className={className}>
      <Icon aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}

/** Pass ``overdue`` for open invoices past their due date; it takes precedence over Sent/Unpaid. */
export function InvoiceStatusBadge({
  status,
  overdue = false,
  className,
}: {
  status: InvoiceStatus;
  overdue?: boolean;
  className?: string;
}) {
  if (overdue && (status === "SENT" || status === "UNPAID" || status === "PARTIALLY_PAID")) {
    return (
      <Badge variant="destructive" className={className}>
        <AlertTriangle aria-hidden="true" />
        Overdue
      </Badge>
    );
  }
  const meta = INVOICE_META[status] ?? INVOICE_META.SENT;
  const Icon = meta.icon;
  return (
    <Badge variant={meta.variant} className={className}>
      <Icon aria-hidden="true" />
      {INVOICE_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}
