/**
 * API client.
 *
 * All requests are relative, so Next's rewrite in `next.config.ts` forwards them
 * to the backend and the browser only ever sees its own origin. That is what
 * makes raw media and bundle downloads work without CORS.
 *
 * `ApiError` carries the status code because the UI reacts to specific ones: 410
 * means "indexed but gone from disk" (grey the tile out), 413 means the bundle is
 * too large (offer per-folder download instead).
 */

import type {
  AccountCard,
  AccountDetail,
  AccountFilters,
  AccountLink,
  ArchiveStats,
  JobProgress,
  LogEntry,
  MediaPage,
  MediaType,
  WorkerStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    // FastAPI puts the human-readable reason in `detail`; fall back to the
    // status text so a proxy error never surfaces as "undefined".
    let message = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail) message = JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body; the status text will do */
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const fetcher = <T>(path: string) => request<T>(path);

/** Build the query string for the dashboard grid. */
export function accountsQuery(filters: AccountFilters): string {
  const params = new URLSearchParams();
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.favorite) params.set("favorite", "true");
  if (filters.status) params.set("status", filters.status);
  if (filters.hasErrors) params.set("has_errors", "true");
  params.set("sort", filters.sort);
  return `/api/accounts?${params.toString()}`;
}

export const api = {
  stats: () => request<ArchiveStats>("/api/stats"),
  workers: () => request<WorkerStatus[]>("/api/workers"),

  accounts: (filters: AccountFilters) => request<AccountCard[]>(accountsQuery(filters)),
  account: (id: number) => request<AccountDetail>(`/api/accounts/${id}`),

  createAccount: (payload: { name: string; is_favorite?: boolean; notes?: string; links?: string[] }) =>
    request<AccountDetail>("/api/accounts", { method: "POST", body: JSON.stringify(payload) }),

  updateAccount: (id: number, patch: Record<string, unknown>) =>
    request<AccountCard>(`/api/accounts/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  deleteAccount: (id: number) => request<void>(`/api/accounts/${id}`, { method: "DELETE" }),

  /** Omit `value` to flip the current state server-side. */
  toggleFavorite: (id: number, value?: boolean) =>
    request<AccountCard>(`/api/accounts/${id}/favorite`, {
      method: "POST",
      body: JSON.stringify({ value: value ?? null }),
    }),

  toggleScrape: (id: number, value?: boolean) =>
    request<AccountCard>(`/api/accounts/${id}/scrape-toggle`, {
      method: "POST",
      body: JSON.stringify({ value: value ?? null }),
    }),

  /** "Run Now". `created: false` means a job was already pending. */
  runNow: (id: number, force = false) =>
    request<{ job_id: number; created: boolean; job: JobProgress }>(`/api/accounts/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ job_type: "sync", trigger: "manual", force }),
    }),

  media: (id: number, options: { mediaType?: MediaType; limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (options.mediaType) params.set("media_type", options.mediaType);
    params.set("limit", String(options.limit ?? 120));
    params.set("offset", String(options.offset ?? 0));
    return request<MediaPage>(`/api/accounts/${id}/media?${params.toString()}`);
  },

  links: (id: number) => request<AccountLink[]>(`/api/accounts/${id}/links`),

  addLink: (id: number, payload: { url: string; label?: string; remote_handle?: string }) =>
    request<AccountLink>(`/api/accounts/${id}/links`, { method: "POST", body: JSON.stringify(payload) }),

  removeLink: (accountId: number, linkId: number) =>
    request<void>(`/api/accounts/${accountId}/links/${linkId}`, { method: "DELETE" }),

  logs: (accountId: number, level?: string) => {
    const params = new URLSearchParams({ limit: "200" });
    if (level) params.set("level", level);
    return request<LogEntry[]>(`/api/accounts/${accountId}/logs?${params.toString()}`);
  },

  resolveLogs: (accountId: number) =>
    request<{ resolved: number }>(`/api/accounts/${accountId}/logs/resolve`, { method: "POST" }),

  jobs: (accountId?: number) =>
    request<JobProgress[]>(`/api/jobs${accountId ? `?account_id=${accountId}` : ""}`),

  cancelJob: (jobId: number) =>
    request<{ status: string }>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),

  retryJob: (jobId: number) => request<{ job_id: number }>(`/api/jobs/${jobId}/retry`, { method: "POST" }),

  batchRun: (accountIds: number[]) =>
    request<{ queued: number[]; already_pending: number[]; count: number }>("/api/batch/run", {
      method: "POST",
      body: JSON.stringify({ account_ids: accountIds, job_type: "sync" }),
    }),

  batchUpdate: (accountIds: number[], patch: Record<string, unknown>) =>
    request<{ updated: number }>("/api/batch/accounts", {
      method: "PATCH",
      body: JSON.stringify({ account_ids: accountIds, patch }),
    }),

  batchBundle: (accountIds: number[]) =>
    request<{
      prepared: { account_id: number; filename: string; size_bytes: number; download_url: string }[];
      failed: { account_id: number; error: string }[];
    }>("/api/batch/bundle", { method: "POST", body: JSON.stringify({ account_ids: accountIds }) }),

  scan: (accountId?: number, rehash = false) =>
    request<{ status: string }>("/api/scan", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId ?? null, rehash }),
    }),
};

/**
 * Trigger a browser download.
 *
 * A plain anchor click rather than fetch-to-blob: bundles can be many gigabytes,
 * and buffering one into memory to create an object URL would take the tab down.
 * Letting the browser stream it to disk is both faster and gives the user a real
 * progress indicator in their downloads UI.
 */
export function downloadBundle(accountId: number, mediaType?: MediaType): void {
  const query = mediaType ? `?media_type=${mediaType}` : "";
  const anchor = document.createElement("a");
  anchor.href = `/api/accounts/${accountId}/bundle${query}`;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
