"use client";

import clsx from "clsx";
import { useState, useTransition } from "react";

import { CheckIcon, RefreshIcon } from "@/components/icons";
import { Modal } from "@/components/ui/Modal";
import { api } from "@/lib/api";
import { absoluteTime, relativeTime } from "@/lib/format";
import { useLogs, useRevalidateAll } from "@/lib/hooks";
import type { LogLevel } from "@/lib/types";

/**
 * Per-account log viewer.
 *
 * The whole point of this modal is that nobody should need `docker logs` to find
 * out why an account stopped syncing. Anything the scraper knows about a failure
 * is here: the event name, the message, the structured detail (adapter, proxy
 * label, retry-after), and the traceback if there was one.
 *
 * `occurrences` is rendered as a "×47" chip rather than 47 rows. A rate-limit
 * storm collapsing into one line is the difference between a readable log and a
 * wall of noise hiding the one entry that matters.
 */

const LEVEL_STYLES: Record<LogLevel, { dot: string; text: string; label: string }> = {
  debug: { dot: "bg-ink-faint", text: "text-ink-faint", label: "DEBUG" },
  info: { dot: "bg-azure", text: "text-ink-dim", label: "INFO" },
  warn: { dot: "bg-amber", text: "text-amber", label: "WARN" },
  error: { dot: "bg-rose", text: "text-rose", label: "ERROR" },
  critical: { dot: "bg-rose", text: "text-rose", label: "CRIT" },
};

const LEVEL_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "info", label: "Info+" },
  { value: "warn", label: "Warnings+" },
  { value: "error", label: "Errors" },
];

export function LogViewerModal({
  accountId,
  accountName,
  onClose,
}: {
  accountId: number | null;
  accountName?: string;
  onClose: () => void;
}) {
  const [level, setLevel] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const { logs, isLoading, refresh } = useLogs(accountId, level || undefined);
  const revalidate = useRevalidateAll();
  const [pending, startTransition] = useTransition();

  const dismissAll = () => {
    if (accountId === null) return;
    startTransition(async () => {
      await api.resolveLogs(accountId);
      await refresh();
      revalidate();
    });
  };

  const openCount = logs.filter(
    (entry) => !entry.resolved_at && (entry.level === "error" || entry.level === "critical"),
  ).length;

  return (
    <Modal
      wide
      open={accountId !== null}
      onClose={onClose}
      title={`Log — ${accountName ?? `account ${accountId}`}`}
      subtitle="Scraper, scheduler and scanner events for this account"
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex gap-1 rounded-lg border border-hairline bg-surface-2 p-0.5">
            {LEVEL_FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setLevel(option.value)}
                className={clsx(
                  "rounded-md px-2.5 py-1 text-[11.5px] font-medium transition",
                  level === option.value
                    ? "bg-cyan/15 text-cyan-bright"
                    : "text-ink-faint hover:text-ink-dim",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-hairline px-2.5 py-1.5 text-[11.5px] text-ink-dim transition hover:border-cyan/40 hover:text-cyan"
            >
              <RefreshIcon className="h-3.5 w-3.5" />
              Refresh
            </button>
            {openCount > 0 && (
              <button
                type="button"
                onClick={dismissAll}
                disabled={pending}
                className="inline-flex items-center gap-1.5 rounded-lg border border-mint/40 bg-mint/10 px-2.5 py-1.5 text-[11.5px] text-mint transition hover:bg-mint/20 disabled:opacity-50"
              >
                <CheckIcon className="h-3.5 w-3.5" />
                Dismiss {openCount} error{openCount === 1 ? "" : "s"}
              </button>
            )}
          </div>
        </div>
      }
    >
      {isLoading ? (
        <div className="space-y-2 p-5">
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="h-12 animate-pulse rounded-lg bg-surface-2" />
          ))}
        </div>
      ) : logs.length === 0 ? (
        <p className="p-10 text-center text-[12.5px] text-ink-faint">
          No log entries yet. Events appear here as soon as the scraper touches this account.
        </p>
      ) : (
        <ul className="divide-y divide-hairline">
          {logs.map((entry) => {
            const style = LEVEL_STYLES[entry.level];
            const isOpen = expanded === entry.id;
            const hasDetail = Boolean(entry.detail || entry.traceback);

            return (
              <li key={entry.id} className={clsx("px-5 py-3", entry.resolved_at && "opacity-55")}>
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : entry.id)}
                  disabled={!hasDetail}
                  className="flex w-full items-start gap-3 text-left disabled:cursor-default"
                >
                  <span className={clsx("mt-1.5 h-2 w-2 shrink-0 rounded-full", style.dot)} />

                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <code className={clsx("text-[11px] font-semibold tracking-wide", style.text)}>
                        {style.label}
                      </code>
                      <span className="text-[12.5px] font-medium text-ink">
                        {entry.event.replace(/_/g, " ")}
                      </span>
                      <span className="rounded border border-hairline px-1.5 text-[10px] uppercase tracking-wide text-ink-faint">
                        {entry.source}
                      </span>
                      {entry.occurrences > 1 && (
                        <span
                          className="rounded-full bg-surface-3 px-1.5 text-[10px] font-semibold text-ink-dim"
                          title={`First seen ${relativeTime(entry.first_seen_at)}`}
                        >
                          ×{entry.occurrences}
                        </span>
                      )}
                      {entry.retryable && (
                        <span className="text-[10px] uppercase tracking-wide text-mint/80">retryable</span>
                      )}
                      {entry.resolved_at && (
                        <span className="text-[10px] uppercase tracking-wide text-mint/70">resolved</span>
                      )}
                    </span>

                    <span className="mt-1 block text-[12.5px] leading-relaxed text-ink-dim">
                      {entry.message}
                    </span>

                    <span className="mt-1 block text-[11px] text-ink-faint" title={absoluteTime(entry.ts)}>
                      {relativeTime(entry.ts)}
                      {entry.job_id && ` · job #${entry.job_id}`}
                      {entry.error_type && ` · ${entry.error_type}`}
                      {hasDetail && (isOpen ? " · click to collapse" : " · click for detail")}
                    </span>
                  </span>
                </button>

                {isOpen && (
                  <div className="mt-2.5 space-y-2 pl-5">
                    {entry.detail && (
                      <pre className="max-h-52 overflow-auto rounded-lg border border-hairline bg-void p-3 text-[11px] leading-relaxed text-ink-dim">
                        {JSON.stringify(entry.detail, null, 2)}
                      </pre>
                    )}
                    {entry.traceback && (
                      <pre className="max-h-64 overflow-auto rounded-lg border border-rose/25 bg-void p-3 text-[11px] leading-relaxed text-rose/80">
                        {entry.traceback}
                      </pre>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Modal>
  );
}
