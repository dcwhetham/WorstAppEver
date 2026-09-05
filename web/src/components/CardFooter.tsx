"use client";

import clsx from "clsx";
import { useTransition } from "react";

import { EtaProgress } from "@/components/EtaProgress";
import { PhotoIcon, StarIcon, TerminalIcon, VideoIcon } from "@/components/icons";
import { api } from "@/lib/api";
import { compactNumber, humanBytes, relativeTime, absoluteTime } from "@/lib/format";
import { useRevalidateAll } from "@/lib/hooks";
import type { AccountCard as AccountCardModel } from "@/lib/types";

/**
 * The metadata strip under each card: counts, last import, quick toggles, and the
 * ETA timer when relevant.
 *
 * Every control here calls `stopPropagation`. The card itself is clickable (it
 * expands), so without that, flipping the favourite star would also open the
 * detail view — a small omission that makes the whole grid feel broken.
 */
export function CardFooter({
  account,
  onShowLogs,
}: {
  account: AccountCardModel;
  onShowLogs: (id: number) => void;
}) {
  const revalidate = useRevalidateAll();
  const [pending, startTransition] = useTransition();

  const mutate = (action: () => Promise<unknown>) => {
    startTransition(async () => {
      try {
        await action();
      } finally {
        // Revalidate even on failure: the optimistic-looking UI must never end up
        // showing a state the server rejected.
        revalidate();
      }
    });
  };

  const lastActivity = account.last_import_at ?? account.last_download_at ?? account.last_scrape_at;

  return (
    <div className={clsx("space-y-2", pending && "opacity-70")}>
      <div className="flex items-center gap-3 text-[11.5px] text-ink-dim">
        <span className="inline-flex items-center gap-1" title={`${account.image_count} images`}>
          <PhotoIcon className="h-3.5 w-3.5 text-cyan/70" />
          <span className="tabular-nums">{compactNumber(account.image_count)}</span>
        </span>
        <span className="inline-flex items-center gap-1" title={`${account.video_count} videos`}>
          <VideoIcon className="h-3.5 w-3.5 text-azure/70" />
          <span className="tabular-nums">{compactNumber(account.video_count)}</span>
        </span>
        <span className="ml-auto text-ink-faint tabular-nums" title="Total size on disk">
          {humanBytes(account.total_bytes)}
        </span>
      </div>

      <div
        className="text-[11px] text-ink-faint"
        title={`Last import: ${absoluteTime(account.last_import_at)}\nLast download: ${absoluteTime(
          account.last_download_at,
        )}\nLast scrape: ${absoluteTime(account.last_scrape_at)}`}
      >
        Last import {relativeTime(lastActivity)}
      </div>

      <EtaProgress account={account} compact />

      <div className="flex items-center gap-1.5 pt-0.5">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            mutate(() => api.toggleFavorite(account.id));
          }}
          aria-pressed={account.is_favorite}
          title={account.is_favorite ? "Remove from favourites" : "Add to favourites"}
          className={clsx(
            "rounded-md border p-1.5 transition",
            account.is_favorite
              ? "border-amber/50 bg-amber/12 text-amber"
              : "border-hairline text-ink-faint hover:border-amber/40 hover:text-amber",
          )}
        >
          <StarIcon className="h-3.5 w-3.5" filled={account.is_favorite} />
        </button>

        <ScrapeSwitch
          enabled={account.scrape_enabled}
          onToggle={() => mutate(() => api.toggleScrape(account.id))}
        />

        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onShowLogs(account.id);
          }}
          title="View scrape log for this account"
          className={clsx(
            "ml-auto rounded-md border p-1.5 transition",
            account.unresolved_error_count > 0
              ? "border-rose/45 bg-rose/10 text-rose"
              : "border-hairline text-ink-faint hover:border-cyan/40 hover:text-cyan",
          )}
        >
          <TerminalIcon className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

/**
 * Scrape on/off switch.
 *
 * A real switch rather than a checkbox because it controls ongoing background
 * behaviour, and the label spells out "Scrape" — an unlabelled toggle on a card
 * full of other controls is a coin flip for the user.
 */
function ScrapeSwitch({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
      title={enabled ? "Scraping enabled — click to pause" : "Scraping paused — click to enable"}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-md border py-1 pl-1.5 pr-2 text-[10.5px] font-medium uppercase tracking-wide transition",
        enabled
          ? "border-mint/45 bg-mint/10 text-mint"
          : "border-hairline bg-surface-2 text-ink-faint hover:border-hairline-bright",
      )}
    >
      <span
        className={clsx(
          "relative h-3 w-6 rounded-full transition-colors",
          enabled ? "bg-mint/70" : "bg-hairline-bright",
        )}
      >
        <span
          className={clsx(
            "absolute top-0.5 h-2 w-2 rounded-full bg-abyss transition-all duration-200",
            enabled ? "left-3.5" : "left-0.5",
          )}
        />
      </span>
      Scrape
    </button>
  );
}
