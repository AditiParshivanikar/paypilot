"use client";

import { Check, Copy } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

export function CopyButton({ value, label = "Copy", className }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = React.useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success("Copied to the clipboard");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy. Select the text and copy it manually.");
    }
  }

  return (
    <Button type="button" variant="outline" size="sm" onClick={copy} className={className} aria-label={label}>
      {copied ? <Check /> : <Copy />} {copied ? "Copied" : label}
    </Button>
  );
}

/** A code block with a copy button, for secrets shown once and config snippets. */
export function CopyBlock({ value, label = "Copy" }: { value: string; label?: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-md border bg-muted p-3 pr-24 font-mono text-xs leading-relaxed">{value}</pre>
      <CopyButton value={value} label={label} className="absolute right-2 top-2 h-7 bg-background" />
    </div>
  );
}
