"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState, useTransition } from "react";

import { CloseIcon, DownloadIcon, PlayIcon, StarIcon } from "@/components/icons";
import { api } from "@/lib/api";
import { useRevalidateAll } from "@/lib/hooks";

/**
 * Floating action bar for batch mode.
 *
 * Bulk bundling returns one zip per account rather than a single combined
 * archive: a multi-account bundle routinely exceeds what a browser will hold, and
 * separate files mean one oversized account fails alone instead of taking the
 * whole batch with it. The downloads are triggered with a small stagger so the
 * browser does not silently drop most of them as a popup burst.
 */

const DOWNLOAD_STAGGER_MS = 700;

export function BatchActionBar({
  selectedIds,
  onClear,
  onSelectAll,
  totalVisible,
}: {
  selectedIds: number[];
  onClear: () => void;
  onSelectAll: () => void;
  totalVisible: number;
}) {
  const revalidate = useRevalidateAll();
  const [pending, startTransition] = useTransition();
  const [status, setStatus] = useState<string | null>(null);

  const run = (action: () => Promise<string>) => {
    startTransition(async () => {
      try {
        setStatus(await action());
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Batch action failed");
      } finally {
        revalidate();
        setTimeout(() => setStatus(null), 4000);
      }
    });
  };

  const bulkScrape = () =>
    run(async () => {
      const result = await api.batchRun(selectedIds);
      const skipped = result.already_pending.length;
      return `Queued ${result.count} scrape job${result.count === 1 ? "" : "s"}${
        skipped ? ` (${skipped} already pending)` : ""
      }, staggered to avoid a traffic spike`;
    });

  const bulkFavorite = () =>
    run(async () => {
      const result = await api.batchUpdate(selectedIds, { is_favorite: true });
      return `Favourited ${result.updated} account${result.updated === 1 ? "" : "s"}`;
    });

  const bulkBundle = () =>
    run(async () => {
      const result = await api.batchBundle(selectedIds);
      result.prepared.forEach((bundle, index) => {
        setTimeout(() => {
          const anchor = document.createElement("a");
          anchor.href = bundle.download_url;
          anchor.rel = "noopener";
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
        }, index * DOWNLOAD_STAGGER_MS);
      });
      const failed = result.failed.length;
      return `Prepared ${result.prepared.length} bundle${result.prepared.length === 1 ? "" : "s"}${
        failed ? `, ${failed} failed` : ""
      }`;
    });

  return (
    <AnimatePresence>
      {selectedIds.length > 0 && (
        <motion.div
          className="fixed bottom-5 left-1/2 z-30 -translate-x-1/2"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 20 }}
          transition={{ type: "spring", stiffness: 320, damping: 30 }}
        >
          <div className="glass flex flex-col gap-2 rounded-2xl border border-cyan/25 px-4 py-3 shadow-[0_20px_60px_-16px_rgba(0,0,0,0.9)]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12.5px] font-medium tabular-nums text-cyan-bright">
                {selectedIds.length} selected
              </span>

              {selectedIds.length < totalVisible && (
                <button
                  type="button"
                  onClick={onSelectAll}
                  className="text-[11.5px] text-ink-faint underline-offset-2 transition hover:text-cyan hover:underline"
                >
                  select all {totalVisible}
                </button>
              )}

              <span className="mx-1 h-4 w-px bg-hairline-bright" />

              <BatchButton
                icon={<PlayIcon className="h-3.5 w-3.5" />}
                label="Bulk scrape"
                onClick={bulkScrape}
                disabled={pending}
                primary
              />
              <BatchButton
                icon={<DownloadIcon className="h-3.5 w-3.5" />}
                label="Bulk bundle"
                onClick={bulkBundle}
                disabled={pending}
              />
              <BatchButton
                icon={<StarIcon className="h-3.5 w-3.5" />}
                label="Favourite"
                onClick={bulkFavorite}
                disabled={pending}
              />

              <button
                type="button"
                onClick={onClear}
                aria-label="Clear selection"
                className="ml-1 rounded-lg border border-hairline p-1.5 text-ink-faint transition hover:border-rose/40 hover:text-rose"
              >
                <CloseIcon className="h-3.5 w-3.5" />
              </button>
            </div>

            {status && <p className="max-w-md text-[11.5px] text-ink-dim">{status}</p>}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function BatchButton({
  icon,
  label,
  onClick,
  disabled,
  primary = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled: boolean;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={
        primary
          ? "inline-flex items-center gap-1.5 rounded-lg border border-cyan/50 bg-cyan/15 px-2.5 py-1.5 text-[12px] font-medium text-cyan-bright transition hover:bg-cyan/25 disabled:opacity-50"
          : "inline-flex items-center gap-1.5 rounded-lg border border-hairline bg-surface-2 px-2.5 py-1.5 text-[12px] font-medium text-ink-dim transition hover:border-cyan/40 hover:text-cyan disabled:opacity-50"
      }
    >
      {icon}
      {label}
    </button>
  );
}
