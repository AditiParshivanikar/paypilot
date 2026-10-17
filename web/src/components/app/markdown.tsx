import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A deliberately tiny, SAFE markdown renderer for assistant replies: **bold**, `inline code`, "- " lists and
 * paragraphs. It builds React elements from plain strings, so any HTML in the text (including third-party invoice
 * notes the assistant quotes) is shown as text and never interpreted. No dangerouslySetInnerHTML anywhere.
 */
type Block = { kind: "p"; lines: string[] } | { kind: "ul"; items: string[] };

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;

function inline(text: string, keyPrefix: string): React.ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={key} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <React.Fragment key={key}>{part}</React.Fragment>;
  });
}

function parse(source: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const item = /^\s*[-*]\s+(.*)$/.exec(line);
    const last = blocks[blocks.length - 1];
    if (item) {
      if (last?.kind === "ul") last.items.push(item[1]);
      else blocks.push({ kind: "ul", items: [item[1]] });
    } else if (!line.trim()) {
      blocks.push({ kind: "p", lines: [] }); // paragraph break; empty ones are dropped below
    } else if (last?.kind === "p") {
      last.lines.push(line);
    } else {
      blocks.push({ kind: "p", lines: [line] });
    }
  }
  return blocks.filter((b) => (b.kind === "p" ? b.lines.length > 0 : b.items.length > 0));
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = parse(text || "");
  return (
    <div className={cn("space-y-2 text-sm leading-relaxed", className)}>
      {blocks.map((block, b) =>
        block.kind === "ul" ? (
          <ul key={b} className="list-disc space-y-1 pl-5 marker:text-muted-foreground">
            {block.items.map((it, i) => (
              <li key={i}>{inline(it, `${b}-${i}`)}</li>
            ))}
          </ul>
        ) : (
          <p key={b}>
            {block.lines.map((ln, i) => (
              <React.Fragment key={i}>
                {i > 0 ? <br /> : null}
                {inline(ln, `${b}-${i}`)}
              </React.Fragment>
            ))}
          </p>
        ),
      )}
    </div>
  );
}
