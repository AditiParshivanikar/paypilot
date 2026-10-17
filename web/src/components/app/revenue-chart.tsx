"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type BarShapeProps, type TooltipContentProps } from "recharts";

import { money } from "@/lib/utils";

export interface RevenueRow {
  customer: string;
  collected: number;
  outstanding: number;
}

const SERIES = [
  { key: "collected", label: "Collected", color: "var(--chart-1)" },
  { key: "outstanding", label: "Outstanding", color: "var(--chart-2)" },
] as const;

/** "12k", "1.5k", "800": one compact axis. */
function kTick(value: number) {
  if (Math.abs(value) >= 1000) return `${Number((value / 1000).toFixed(1))}k`;
  return String(value);
}

/**
 * A stacked segment with a 2px card-coloured gap (a 2px stroke in --card) and 4px rounding only on the top end
 * of each stack. "Top" is the outstanding segment, or the collected one when nothing is outstanding.
 */
function segment(series: "collected" | "outstanding") {
  function Segment(props: BarShapeProps) {
    const { x, y, width, height } = props;
    const row = props.payload as RevenueRow | undefined;
    if (!height || height <= 0 || !width) return <g />;
    const isTop = series === "outstanding" || (row?.outstanding ?? 0) <= 0;
    const r = isTop ? Math.min(4, height, width / 2) : 0;
    const d = r
      ? `M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`
      : `M${x},${y} H${x + width} V${y + height} H${x} Z`;
    const color = series === "collected" ? "var(--chart-1)" : "var(--chart-2)";
    return <path d={d} style={{ fill: color, stroke: "var(--card)", strokeWidth: 2 }} />;
  }
  return Segment;
}

function ChartTooltip({ active, payload, label, currency }: TooltipContentProps & { currency: string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload as RevenueRow | undefined;
  if (!row) return null;
  return (
    <div className="min-w-44 rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p className="mb-1.5 font-medium">{row.customer ?? label}</p>
      {SERIES.map((s) => (
        <div key={s.key} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex items-center gap-2 text-muted-foreground">
            <span className="size-2.5 rounded-[3px]" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </span>
          <span className="font-medium tabular-nums text-foreground">{money(currency, row[s.key])}</span>
        </div>
      ))}
    </div>
  );
}

export function RevenueChart({ rows, currency }: { rows: RevenueRow[]; currency: string }) {
  const description =
    "Collected vs outstanding by customer. " +
    rows
      .map((r) => `${r.customer}: collected ${money(currency, r.collected)}, outstanding ${money(currency, r.outstanding)}`)
      .join("; ") +
    ".";
  return (
    <figure className="space-y-3">
      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-hidden="true">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-[3px]" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <div role="img" aria-label={description} className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -8 }} accessibilityLayer={false} barCategoryGap="28%">
            <CartesianGrid vertical={false} stroke="var(--border)" strokeOpacity={0.7} />
            <XAxis
              dataKey="customer"
              tickLine={false}
              axisLine={false}
              tickMargin={8}
              interval={0}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
              tickFormatter={(name: string) => name.split(" ")[0]}
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              width={44}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
              tickFormatter={kTick}
            />
            <Tooltip
              cursor={{ fill: "var(--muted)", opacity: 0.5 }}
              content={(props) => <ChartTooltip {...props} currency={currency} />}
            />
            <Bar dataKey="collected" stackId="total" maxBarSize={48} shape={segment("collected")} isAnimationActive={false} />
            <Bar dataKey="outstanding" stackId="total" maxBarSize={48} shape={segment("outstanding")} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
