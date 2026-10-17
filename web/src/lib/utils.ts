import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes, letting later ones win (shadcn convention). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const formatters = new Map<string, Intl.NumberFormat>();

/** "₹1,00,000.00" for INR (Indian digit grouping), "$1,250.50" for USD; falls back to "XYZ 12.00". */
export function money(currency: string | null | undefined, value: number | null | undefined): string {
  const code = (currency || "INR").toUpperCase();
  const amount = Number(value ?? 0);
  const locale = code === "INR" ? "en-IN" : "en-US";
  const key = `${locale}:${code}`;
  try {
    let fmt = formatters.get(key);
    if (!fmt) {
      fmt = new Intl.NumberFormat(locale, {
        style: "currency",
        currency: code,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
      formatters.set(key, fmt);
    }
    return fmt.format(amount);
  } catch {
    return `${code} ${amount.toFixed(2)}`;
  }
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];
const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** "3 minutes ago", "yesterday", "in 2 days". Accepts ISO strings, Dates or epoch milliseconds. */
export function timeAgo(when: string | number | Date | null | undefined, now: number = Date.now()): string {
  if (when === null || when === undefined || when === "") return "";
  const t = when instanceof Date ? when.getTime() : typeof when === "number" ? when : Date.parse(when);
  if (Number.isNaN(t)) return "";
  const seconds = Math.round((t - now) / 1000);
  if (Math.abs(seconds) < 45) return "just now";
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return relative.format(Math.round(seconds / 60), "minute");
}

const NICE: Record<string, string> = {
  paypal: "PayPal",
  razorpay: "Razorpay",
  smtp: "SMTP",
  twilio: "Twilio",
  "slack-webhook": "Slack webhook",
  simulator: "Simulator",
  whatsapp: "WhatsApp",
  email: "Email",
  slack: "Slack",
  provider: "Provider email",
  csv: "CSV",
  json: "JSON",
  tally: "Tally",
};

/** Human names for providers, adapters and channels: "paypal" -> "PayPal", "send_reminder" -> "Send reminder". */
export function nice(name: string | null | undefined): string {
  if (!name) return "";
  const key = name.toLowerCase();
  if (NICE[key]) return NICE[key];
  const words = key.replace(/[_.-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
