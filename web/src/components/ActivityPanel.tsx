"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";

import { ChevronDownIcon, ClockIcon, PlayIcon, PowerIcon } from "@/components/icons";
import { relativeTime } from "@/lib/format";
import { useJobs, useWorkers } from "@/lib/hooks";
import type { JobRecord, JobStatus } from "@/lib/types";

const LIVE: JobStatus[] = ["queued", "claimed", "running", "deferred"];

const STATUS_TONE: Record<JobStatus, string> = {
  queued: "bg-ink-faint/70",
  claimed: "bg-amber",
  running: "bg-cyan pulse-ring",
  deferred: "bg-violet",
  succeeded: "bg-mint",
  failed: "bg-rose",
  cancelled: "bg-ink-faint/50",
};

/**
 * Collapsible activity rail.
 *
 * The dashboard otherwise only shows work on the card that happens to be in
 * view (an ETA bar, an error badge). This is the place that answers "is
 * anything running, and what did it last touch?" without opening an account.
 */
export function ActivityPanel() {
  const jobs = useJobs(16);
  const workers = useWorkers();
  const liveWorker = workers.find((worker) => worker.is_alive && worker.status !== "stopping");

  const live = useMemo(() => jobs.filter((job) => LIVE.includes(job.status)), [jobs]);
  const recent = useMemo(
    () => jobs.filter((job) => !LIVE.includes(job.status)).slice(0, 8),
    [jobs],
  );
  const latest = live[0] ?? recent[0] ?? null;

  const [open, setOpen] = useState(false);

  // Open itself the first time work appears so a silent queue is not missed;
  // after that the user's toggle wins for the session.
  useEffect(() => {
    if (live.length > 0) setOpen(true);
  }, [live.length]);

  const summary = liveWorker
    ? live[0]
      ? `Working on ${live[0].account_name ?? "an account"}${live[0].message ? ` · ${live[0].message}` : ""}`
      : `Scraper ${liveWorker.status}`
    : latest
      ? `Last: ${latest.account_name ?? "unknown"} · ${latest.error_summary || latest.message || latest.status}`
      : "No scrape activity yet";

  return (
    <section className="overflow-hidden rounded-2xl border border-hairline bg-surface/80">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-surface-2/60"
      >
        <span
          className={clsx(
            "h-2 w-2 shrink-0 rounded-full",
            liveWorker ? (live.length ? "bg-cyan pulse-ring" : "bg-mint") : "bg-ink-faint/60",
          )}
        />
        <PowerIcon className="hidden h-3.5 w-3.5 shrink-0 text-ink-faint sm:block" />
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-dim">
          <span className="mr-2 font-medium text-ink">Activity</span>
          {summary}
        </span>
        {live.length > 0 && (
          <span className="shrink-0 rounded-full border border-cyan/35 bg-cyan/10 px-2 py-0.5 text-[10.5px] font-medium text-cyan">
            {live.length} live
          </span>
        )}
        <ChevronDownIcon
          className={clsx("h-4 w-4 shrink-0 text-ink-faint transition-transform", open && "rotate-180")}
        />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="activity-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="overflow-hidden border-t border-hairline"
          >
            <div className="grid gap-4 p-3.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
              <div>
                <h3 className="mb-2 text-[10.5px] font-medium uppercase tracking-wide text-ink-faint">
                  Now
                </h3>
                {live.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-hairline px-3 py-4 text-[12px] text-ink-faint">
                    {liveWorker
                      ? "Worker is idle — nothing in the queue."
                      : "No worker connected. Queued jobs will wait."}
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {live.map((job) => (
                      <JobRow key={job.id} job={job} />
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h3 className="mb-2 text-[10.5px] font-medium uppercase tracking-wide text-ink-faint">
                  Recently finished
                </h3>
                {recent.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-hairline px-3 py-4 text-[12px] text-ink-faint">
                    Nothing has completed yet.
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {recent.map((job) => (
                      <JobRow key={job.id} job={job} />
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

function JobRow({ job }: { job: JobRecord }) {
  const when = job.finished_at ?? job.started_at ?? job.updated_at;
  const progress =
    job.percent_complete != null
      ? `${job.percent_complete}%`
      : job.items_expected
        ? `${job.items_downloaded}/${job.items_expected}`
        : null;

  return (
    <li className="flex items-start gap-2.5 rounded-xl border border-hairline bg-surface-2/50 px-3 py-2">
      <span className={clsx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", STATUS_TONE[job.status])} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="truncate text-[12.5px] font-medium text-ink">
            {job.account_name ?? "unscoped"}
            <span className="ml-1.5 font-normal text-ink-faint">{job.job_type}</span>
          </p>
          <span className="shrink-0 text-[11px] text-ink-faint" title={when}>
            <ClockIcon className="mr-1 inline h-3 w-3" />
            {relativeTime(when)}
          </span>
        </div>
        <p className="mt-0.5 truncate text-[11.5px] text-ink-dim">
          {job.error_summary || job.message || job.phase || job.status}
          {progress && LIVE.includes(job.status) ? ` · ${progress}` : ""}
        </p>
      </div>
      {LIVE.includes(job.status) && (
        <PlayIcon className="mt-0.5 h-3 w-3 shrink-0 text-cyan" />
      )}
    </li>
  );
}
