import * as React from "react";

import { cn } from "@/lib/utils";

/** A plain divider; decorative by default, so screen readers skip it. */
function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<"div"> & { orientation?: "horizontal" | "vertical"; decorative?: boolean }) {
  return (
    <div
      data-slot="separator"
      data-orientation={orientation}
      {...(decorative ? { role: "none" } : { role: "separator", "aria-orientation": orientation })}
      className={cn("shrink-0 bg-border", orientation === "horizontal" ? "h-px w-full" : "h-full w-px", className)}
      {...props}
    />
  );
}

export { Separator };
