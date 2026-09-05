"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { useEffect, useState } from "react";

import { ClockIcon, LayersIcon } from "@/components/icons";
import { compactNumber, humanDuration, pacingLabel } from "@/lib/format";
import type { AccountCard } from "@/lib/types";

/** Backlog at which an idle account is worth flagging on the card footer. */
const BACKLOG_NOTICE_THRESHOLD = 5;

/**
 * Real-time ETA timer for the card footer.
 *
 * The countdown ticks locally once a second and re-syncs whenever the poll brings
 * a fresh `eta_seconds`. Relying on the poll alone would make the timer jump in
 * two-second steps, which reads as a stalled job; ticking locally without
 * re-syncing would drift away from reality. Doing both gives a smooth countdown
 * that stays honest.
 *
 * When the worker has not yet gathered enough samples it sends `eta_seconds:
 * null`, and this renders "estimating…" rather than inventing a number.
 */
export function EtaProgress({ account, compact = false }: { account: AccountCard; compact?: boolean }) {
  const job = account.active_job;
  const [localEta, setLocalEta] = useState<number | null>(job?.eta_seconds ?? null);

  useEffect(() => {
    setLocalEta(job?.eta_seconds ?? null);
  }, [job?.eta_seconds, job?.id]);

  useEffect(() => {
    if (!job || job.eta_seconds === null) return;
    const timer = setInterval(() => {
      // Floor at 1s: hitting zero while work is clearly still running looks
      // broken, and the next poll will correct the estimate anyway.
      setLocalEta((current) => (current === null ? null : Math.max(1, current - 1)));
    }, 1000);
    return () => clearInterval(timer);
  }, [job, job?.eta_seconds]);

  if (job) {
    const done = job.items_downloaded + job.items_skipped;
    const percent =
      job.percent_complete ??
      (job.items_expected > 0 ? Math.min(100, Math.round((done * 100) / job.items_expected)) : null);
    const isQueued = job.status === "queued" || job.status === "deferred";
    const pacing = pacingLabel(job.pace_delay_ms);

    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2 text-[11px]">
          <span
            className={clsx(
              "inline-flex items-center gap-1.5 font-medium",
              isQueued ? "text-ink-faint" : "text-cyan",
            )}
          >
            <ClockIcon className={clsx("h-3.5 w-3.5", !isQueued && "animate-pulse")} />
            {isQueued ? "Queued" : humanDuration(localEta)}
            {!isQueued && localEta !== null && <span className="text-ink-faint">left</span>}
          </span>

          <span className="tabular-nums text-ink-faint">
            {job.items_expected > 0 ? `${done}/${job.items_expected}` : `${done} items`}
            {percent !== null && <span className="ml-1.5 text-cyan/70">{percent}%</span>}
          </span>
        </div>

        <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
          {percent === null ? (
            // Indeterminate: discovery is running and there is no denominator yet.
            <div className="shimmer h-full w-full">
              <div className="shimmer-bar h-full w-1/2" />
            </div>
          ) : (
            <motion.div
              className="h-full rounded-full bg-gradient-to-r from-cyan-deep via-cyan to-azure"
              initial={{ width: 0 }}
              animate={{ width: `${percent}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 24 }}
            />
          )}
        </div>

        {!compact && (job.message || pacing) && (
          <p className="truncate text-[10.5px] text-ink-faint" title={job.message ?? undefined}>
            {job.message}
            {pacing && <span className="ml-1.5 text-cyan/60">· {pacing}</span>}
          </p>
        )}
      </div>
    );
  }

  // Idle, but behind. Worth surfacing: a new account or one missing a chunk of
  // media is exactly what the ETA affordance exists for.
  const backlog = account.pending_remote_count || account.estimated_missing_count;
  if (account.is_new || backlog >= BACKLOG_NOTICE_THRESHOLD) {
    return (
      <div className="flex items-center gap-1.5 text-[11px] text-amber">
        <LayersIcon className="h-3.5 w-3.5" />
        <span>
          {backlog > 0 ? `${compactNumber(backlog)} item${backlog === 1 ? "" : "s"} behind` : "Awaiting first sync"}
        </span>
        {account.scrape_enabled ? (
          <span className="text-ink-faint">· paced</span>
        ) : (
          <span className="text-ink-faint">· scraping off</span>
        )}
      </div>
    );
  }

  return null;
}
