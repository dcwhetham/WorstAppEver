"use client";

/**
 * SWR wrappers.
 *
 * The polling intervals are adaptive, which is the main thing worth knowing here:
 * an idle dashboard refreshes every 30s, but as soon as any account has a live job
 * the whole grid drops to 2s so the ETA timers count down smoothly. A fixed fast
 * interval would hammer the backend for nothing 99% of the time; a fixed slow one
 * would make the progress bars jump in visible steps.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";

import { accountsQuery, fetcher } from "./api";
import type {
  AccountCard,
  AccountDetail,
  AccountFilters,
  ArchiveStats,
  JobRecord,
  LogEntry,
  MediaPage,
  WorkerStatus,
} from "./types";

const IDLE_POLL_MS = 30_000;
const ACTIVE_POLL_MS = 2_000;

function hasLiveJob(accounts: AccountCard[] | undefined): boolean {
  return Boolean(accounts?.some((account) => account.active_job !== null));
}

export function useAccounts(filters: AccountFilters) {
  const key = accountsQuery(filters);
  const { data, error, isLoading, mutate } = useSWR<AccountCard[]>(key, fetcher, {
    refreshInterval: (latest) => (hasLiveJob(latest) ? ACTIVE_POLL_MS : IDLE_POLL_MS),
    keepPreviousData: true,
  });

  return { accounts: data, error, isLoading, refresh: mutate, swrKey: key };
}

export function useAccount(id: number | null) {
  const { data, error, isLoading, mutate } = useSWR<AccountDetail>(
    id === null ? null : `/api/accounts/${id}`,
    fetcher,
    {
      refreshInterval: (latest) => (latest?.active_job ? ACTIVE_POLL_MS : IDLE_POLL_MS),
      keepPreviousData: false,
    },
  );
  return { account: data, error, isLoading, refresh: mutate };
}

export function useStats() {
  const { data } = useSWR<ArchiveStats>("/api/stats", fetcher, {
    refreshInterval: (latest) => (latest && latest.running_jobs > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS),
  });
  return data;
}

export function useWorkers() {
  const { data } = useSWR<WorkerStatus[]>("/api/workers", fetcher, { refreshInterval: 15_000 });
  return data ?? [];
}

function isLiveJob(job: JobRecord): boolean {
  return job.status === "queued" || job.status === "claimed" || job.status === "running" || job.status === "deferred";
}

export function useJobs(limit = 20) {
  const { data } = useSWR<JobRecord[]>(`/api/jobs?limit=${limit}`, fetcher, {
    refreshInterval: (latest) => (latest?.some(isLiveJob) ? ACTIVE_POLL_MS : IDLE_POLL_MS),
  });
  return data ?? [];
}

export function useMedia(accountId: number | null, mediaType?: "image" | "video") {
  const query = new URLSearchParams({ limit: "300" });
  if (mediaType) query.set("media_type", mediaType);
  const { data, isLoading } = useSWR<MediaPage>(
    accountId === null ? null : `/api/accounts/${accountId}/media?${query.toString()}`,
    fetcher,
  );
  return { media: data?.items ?? [], total: data?.total ?? 0, isLoading };
}

export function useLogs(accountId: number | null, level?: string) {
  const query = new URLSearchParams({ limit: "200" });
  if (level) query.set("level", level);
  const { data, isLoading, mutate } = useSWR<LogEntry[]>(
    accountId === null ? null : `/api/accounts/${accountId}/logs?${query.toString()}`,
    fetcher,
    // Logs are what you stare at while a job fails, so they refresh quickly
    // whenever the modal is open.
    { refreshInterval: 5_000 },
  );
  return { logs: data ?? [], isLoading, refresh: mutate };
}

/**
 * Revalidate every account-derived key at once.
 *
 * A single toggle changes the card, the detail view, the stats header and the
 * queue. Rather than tracking which keys each action touches, invalidate the
 * whole `/api/accounts`-and-friends family and let SWR dedupe.
 */
export function useRevalidateAll() {
  const { mutate } = useSWRConfig();
  return useCallback(() => {
    void mutate(
      (key) =>
        typeof key === "string" &&
        (key.startsWith("/api/accounts") ||
          key.startsWith("/api/stats") ||
          key.startsWith("/api/jobs") ||
          key.startsWith("/api/workers")),
      undefined,
      { revalidate: true },
    );
  }, [mutate]);
}

/**
 * Debounce, used for the search box.
 *
 * 220ms is short enough to feel immediate and long enough that a burst of
 * keystrokes produces one request rather than one per character.
 */
export function useDebounced<T>(value: T, delayMs = 220): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/** Multi-select state for the dashboard's batch mode. */
export function useSelection() {
  const [enabled, setEnabled] = useState(false);
  const [ids, setIds] = useState<Set<number>>(new Set());

  const toggle = useCallback((id: number) => {
    setIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => setIds(new Set()), []);

  const setMode = useCallback((next: boolean) => {
    setEnabled(next);
    // Leaving batch mode drops the selection: a hidden selection that resurfaces
    // later is how people accidentally bulk-scrape forty accounts.
    if (!next) setIds(new Set());
  }, []);

  const selectAll = useCallback((all: number[]) => setIds(new Set(all)), []);

  return useMemo(
    () => ({ enabled, ids, toggle, clear, setMode, selectAll, count: ids.size }),
    [enabled, ids, toggle, clear, setMode, selectAll],
  );
}

/**
 * Escape-to-close plus body scroll lock, for modals and the expanded card.
 *
 * The scroll lock matters more than it sounds: without it, scrolling inside an
 * overlay bleeds through to the grid behind, and the shared-element close
 * animation then flies to a card that has moved.
 */
export function useDismissable(active: boolean, onDismiss: () => void) {
  const handler = useRef(onDismiss);
  handler.current = onDismiss;

  useEffect(() => {
    if (!active) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        handler.current();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [active]);
}

/** Whether the user has asked for reduced motion. Tilt and expansion honour it. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const listener = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);

  return reduced;
}
