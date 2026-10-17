import { Loader2 } from "lucide-react";
import { Suspense } from "react";

import { Chat } from "@/components/app/chat";

// Chat reads ?q= with useSearchParams, so it must sit inside a Suspense boundary for the static build.
export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading the assistant…
        </div>
      }
    >
      <Chat />
    </Suspense>
  );
}
