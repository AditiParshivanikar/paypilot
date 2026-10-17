"use client";

import { BellRing, Code2, KeyRound, LayoutGrid, Webhook } from "lucide-react";

import { ApiKeysTab, AppsTab, McpTab, NotificationsTab, WebhooksTab } from "@/components/app/integrations";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSession } from "@/lib/hooks";

export default function IntegrationsPage() {
  const { data: session } = useSession();
  const admin = session?.scopes.includes("admin") ?? false;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <p className="text-sm text-muted-foreground">
          Payment providers, notifications, exports, webhooks and API access. Credentials stay on the server.
        </p>
      </div>
      <Tabs defaultValue="apps">
        <TabsList className="max-w-full justify-start overflow-x-auto">
          <TabsTrigger value="apps">
            <LayoutGrid /> Apps
          </TabsTrigger>
          {admin ? (
            <TabsTrigger value="webhooks">
              <Webhook /> Webhooks
            </TabsTrigger>
          ) : null}
          {admin ? (
            <TabsTrigger value="keys">
              <KeyRound /> API keys
            </TabsTrigger>
          ) : null}
          <TabsTrigger value="notifications">
            <BellRing /> Notifications
          </TabsTrigger>
          <TabsTrigger value="mcp">
            <Code2 /> MCP &amp; API
          </TabsTrigger>
        </TabsList>
        <TabsContent value="apps">
          <AppsTab />
        </TabsContent>
        {admin ? (
          <TabsContent value="webhooks">
            <WebhooksTab />
          </TabsContent>
        ) : null}
        {admin ? (
          <TabsContent value="keys">
            <ApiKeysTab />
          </TabsContent>
        ) : null}
        <TabsContent value="notifications">
          <NotificationsTab />
        </TabsContent>
        <TabsContent value="mcp">
          <McpTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
