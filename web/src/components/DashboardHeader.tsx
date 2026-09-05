"use client";

import clsx from "clsx";
import { useState, useTransition } from "react";

import { AddAccountDialog } from "@/components/AddAccountDialog";
import { AlertIcon, ArchiveIcon, PhotoIcon, PlusIcon, PowerIcon, RefreshIcon, VideoIcon } from "@/components/icons";
import { api } from "@/lib/api";
import { compactNumber, humanBytes, relativeTime } from "@/lib/format";
import { useRevalidateAll, useStats, useWorkers } from "@/lib/hooks";

/**
 * Dashboard header: archive totals, scraper liveness, and the global actions.
 *
 * The worker indicator reads `worker_heartbeats`, so the dashboard reports the
 * scraper as offline without ever calling it. That is the visible half of the
 * decoupling: a dead container shows up as a grey dot and queued jobs, not as a
 * failing page.
 */
export function DashboardHeader() {
  const stats = useStats();
  const workers = useWorkers();
  const revalidate = useRevalidateAll();
  const [pending, startTransition] = useTransition();
  const [adding, setAdding] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);

  const live = workers.filter((worker) => worker.is_alive);
  const scraper = live[0];

  const triggerScan = () => {
    startTransition(async () => {
      await api.scan();
      setScanNote("Scan started — new folders and files appear as it progresses");
      revalidate();
      setTimeout(() => setScanNote(null), 5000);
    });
  };

  return (
    <header className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">
            Media <span className="text-cyan text-glow-cyan">Archive</span>
          </h1>
          <p className="mt-0.5 text-[12.5px] text-ink-faint">
            {stats
              ? `${stats.account_count} accounts · ${compactNumber(stats.image_count)} images · ${compactNumber(
                  stats.video_count,
                )} videos · ${humanBytes(stats.total_bytes)}`
              : "Loading archive…"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <WorkerPill
            online={Boolean(scraper)}
            label={
              scraper
                ? `Scraper ${scraper.status}${scraper.current_job_id ? ` · job #${scraper.current_job_id}` : ""}`
                : workers.length > 0
                  ? `Scraper offline · last beat ${relativeTime(workers[0]?.beat_at)}`
                  : "Scraper never connected"
            }
          />

          <button
            type="button"
            onClick={triggerScan}
            disabled={pending}
            title="Reconcile the archive folder with the index"
            className="inline-flex items-center gap-1.5 rounded-xl border border-hairline bg-surface-2 px-3 py-2 text-[12.5px] text-ink-dim transition hover:border-cyan/40 hover:text-cyan disabled:opacity-50"
          >
            <RefreshIcon className={clsx("h-3.5 w-3.5", pending && "animate-spin")} />
            Scan archive
          </button>

          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1.5 rounded-xl border border-cyan/50 bg-cyan/15 px-3 py-2 text-[12.5px] font-medium text-cyan-bright transition hover:bg-cyan/25"
          >
            <PlusIcon className="h-3.5 w-3.5" />
            Add account
          </button>
        </div>
      </div>

      {scanNote && (
        <p className="rounded-lg border border-cyan/25 bg-cyan/[0.05] px-3 py-2 text-[12px] text-cyan-bright">
          {scanNote}
        </p>
      )}

      {stats && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <MiniStat icon={<ArchiveIcon className="h-3.5 w-3.5" />} label="Active" value={stats.active_count} />
          <MiniStat label="Legacy" value={stats.legacy_count} tone="violet" />
          <MiniStat label="Flagged" value={stats.flagged_count} tone={stats.flagged_count ? "rose" : "neutral"} />
          <MiniStat icon={<PhotoIcon className="h-3.5 w-3.5" />} label="Queued jobs" value={stats.queued_jobs} />
          <MiniStat icon={<VideoIcon className="h-3.5 w-3.5" />} label="Running" value={stats.running_jobs} tone={stats.running_jobs ? "cyan" : "neutral"} />
          <MiniStat
            icon={<AlertIcon className="h-3.5 w-3.5" />}
            label="Open errors"
            value={stats.open_errors}
            tone={stats.open_errors ? "rose" : "neutral"}
          />
        </div>
      )}

      <AddAccountDialog open={adding} onClose={() => setAdding(false)} />
    </header>
  );
}

function WorkerPill({ online, label }: { online: boolean; label: string }) {
  return (
    <span
      title={label}
      className={clsx(
        "inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-[12px]",
        online ? "border-mint/40 bg-mint/[0.07] text-mint" : "border-hairline bg-surface-2 text-ink-faint",
      )}
    >
      <span className={clsx("h-2 w-2 rounded-full", online ? "bg-mint pulse-ring" : "bg-ink-faint/60")} />
      <PowerIcon className="h-3.5 w-3.5" />
      <span className="max-w-56 truncate">{label}</span>
    </span>
  );
}

function MiniStat({
  icon,
  label,
  value,
  tone = "neutral",
}: {
  icon?: React.ReactNode;
  label: string;
  value: number;
  tone?: "neutral" | "cyan" | "rose" | "violet";
}) {
  const tint = {
    neutral: "text-ink",
    cyan: "text-cyan",
    rose: "text-rose",
    violet: "text-violet",
  }[tone];

  return (
    <div className="rounded-xl border border-hairline bg-surface/70 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[10.5px] uppercase tracking-wide text-ink-faint">
        {icon}
        {label}
      </div>
      <div className={clsx("mt-0.5 text-base font-semibold tabular-nums", tint)}>{value}</div>
    </div>
  );
}
