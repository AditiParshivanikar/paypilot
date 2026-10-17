"use client";

import useSWR, { mutate, type SWRConfiguration } from "swr";

import {
  request,
  type Action,
  type ActionStatus,
  type ApiKey,
  type AuditEntry,
  type Customer,
  type EventType,
  type Health,
  type IntegrationsStatus,
  type InvoicesResponse,
  type NotificationRow,
  type PayPilotEvent,
  type Policy,
  type Session,
  type Subscription,
} from "@/lib/api";

/** Every SWR key is the API path after /api/v1, e.g. "/actions?status=pending". */
const fetcher = <T,>(key: string) => request<T>(key);

function useApi<T>(key: string | null, config?: SWRConfiguration<T>) {
  return useSWR<T>(key, fetcher<T>, { revalidateOnFocus: true, keepPreviousData: true, ...config });
}

export const useHealth = () => useApi<Health>("/health", { revalidateOnFocus: false });
export const useSession = () => useApi<Session>("/session");
export const useIntegrations = () => useApi<IntegrationsStatus>("/integrations");

/** The Approval inbox and history; refreshes every 5 s so drafts from chat, the API or MCP show up. */
export function useActions(status?: ActionStatus) {
  const res = useApi<{ actions: Action[] }>(status ? `/actions?status=${status}` : "/actions", { refreshInterval: 5000 });
  return { ...res, actions: res.data?.actions };
}

/** Invoices and totals; refreshes every 10 s so paid webhooks show up without a reload. */
export function useInvoices() {
  return useApi<InvoicesResponse>("/invoices", { refreshInterval: 10000 });
}

export function useCustomers() {
  const res = useApi<{ customers: Customer[] }>("/customers");
  return { ...res, customers: res.data?.customers };
}

export const usePolicy = () => useApi<Policy>("/policy");

export function useAudit(limit = 200) {
  const res = useApi<{ entries: AuditEntry[] }>(`/audit?limit=${limit}`);
  return { ...res, entries: res.data?.entries };
}

export function useEvents(type?: EventType, limit = 200) {
  const res = useApi<{ events: PayPilotEvent[] }>(type ? `/events?type=${type}&limit=${limit}` : `/events?limit=${limit}`);
  return { ...res, events: res.data?.events };
}

export function useNotifications(limit = 200) {
  const res = useApi<{ notifications: NotificationRow[] }>(`/notifications?limit=${limit}`);
  return { ...res, notifications: res.data?.notifications };
}

export function useSubscriptions() {
  const res = useApi<{ subscriptions: Subscription[] }>("/webhooks/subscriptions");
  return { ...res, subscriptions: res.data?.subscriptions };
}

export function useApiKeys(enabled = true) {
  const res = useApi<{ keys: ApiKey[] }>(enabled ? "/keys" : null);
  return { ...res, keys: res.data?.keys };
}

/**
 * Revalidate every cached API read (after an approval, a simulated payment, a reset...).
 *
 * Only the key filter is passed. Calling mutate(filter, undefined) would REPLACE every cached value with
 * undefined: pages would flash back to loading skeletons, components keyed on data would unmount, and
 * in-memory state such as the chat transcript would be lost. With no data argument SWR keeps the current
 * data and simply refetches in the background.
 */
export function refreshAll() {
  return mutate((key) => typeof key === "string" && key.startsWith("/"));
}
