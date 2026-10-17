import { cn } from "@/lib/utils";

/**
 * The PayPilot mark: a rounded square in --chart-1 with a white arrow.
 * Deliberately uses no <defs>/gradient ids: the logo renders twice (sidebar and mobile drawer), and duplicate
 * SVG ids make the second, initially hidden copy lose its fill.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false" className={cn("size-8 shrink-0", className)}>
      <rect width="32" height="32" rx="8" style={{ fill: "var(--chart-1)" }} />
      <path
        d="M9.5 21.5 21 10m0 0h-8m8 0v8"
        fill="none"
        stroke="#fff"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ className, subtitle }: { className?: string; subtitle?: string }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className="flex flex-col leading-tight">
        <span className="font-semibold tracking-tight">PayPilot</span>
        {subtitle ? <span className="text-xs text-muted-foreground">{subtitle}</span> : null}
      </span>
    </span>
  );
}
