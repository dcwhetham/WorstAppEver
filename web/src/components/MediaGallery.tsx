"use client";

import clsx from "clsx";
import { useState } from "react";

import { MediaViewer } from "@/components/MediaViewer";
import { PhotoIcon, PlayIcon, VideoIcon } from "@/components/icons";
import { clockTime, humanBytes } from "@/lib/format";
import { useMedia } from "@/lib/hooks";
import type { MediaType } from "@/lib/types";

type Tab = "all" | "image" | "video";

/**
 * Thumbnail grid for the expanded account view.
 *
 * The grid renders full-size images scaled down by the browser, because the
 * archive holds raw files only and generating thumbnails would create a derived
 * cache that can drift from what is on disk. `loading="lazy"` keeps that
 * affordable: off-screen tiles are never fetched, so a 3,000-image account costs
 * one screenful of bandwidth.
 *
 * Videos show a poster-less tile with a play affordance rather than a `<video>`
 * element per cell. Mounting hundreds of video elements to harvest first frames
 * would open hundreds of connections and stall the tab.
 */
export function MediaGallery({ accountId }: { accountId: number }) {
  const [tab, setTab] = useState<Tab>("all");
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);

  const mediaType: MediaType | undefined = tab === "all" ? undefined : tab;
  const { media, total, isLoading } = useMedia(accountId, mediaType as "image" | "video" | undefined);

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-[13px] font-semibold uppercase tracking-wide text-ink-faint">Media</h3>
        <div className="flex gap-1 rounded-lg border border-hairline bg-surface-2 p-0.5">
          {(["all", "image", "video"] as Tab[]).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => {
                setTab(option);
                setViewerIndex(null);
              }}
              className={clsx(
                "rounded-md px-2.5 py-1 text-[11.5px] font-medium capitalize transition",
                tab === option ? "bg-cyan/15 text-cyan-bright" : "text-ink-faint hover:text-ink-dim",
              )}
            >
              {option === "image" ? "Photos" : option === "video" ? "Videos" : "All"}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {Array.from({ length: 10 }, (_, index) => (
            <div key={index} className="aspect-square animate-pulse rounded-lg bg-surface-2" />
          ))}
        </div>
      ) : media.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-hairline py-10 text-ink-faint">
          <PhotoIcon className="h-7 w-7 opacity-40" />
          <p className="text-[12.5px]">Nothing indexed yet for this account.</p>
          <p className="text-[11px]">
            Drop files into the archive folder and run a scan, or trigger a scrape.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
            {media.map((item, index) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setViewerIndex(index)}
                title={`${item.filename} · ${humanBytes(item.bytes)}`}
                className="group relative aspect-square overflow-hidden rounded-lg border border-hairline bg-void transition hover:border-cyan/50"
              >
                {item.media_type === "image" ? (
                  <img
                    src={item.raw_url}
                    alt={item.filename}
                    loading="lazy"
                    decoding="async"
                    className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                  />
                ) : (
                  <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 bg-gradient-to-br from-surface-2 to-void">
                    <VideoIcon className="h-5 w-5 text-azure/60" />
                    {item.duration_seconds && (
                      <span className="text-[10px] tabular-nums text-ink-faint">
                        {clockTime(item.duration_seconds)}
                      </span>
                    )}
                  </div>
                )}

                {item.media_type === "video" && (
                  <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
                    <span className="rounded-full bg-abyss/70 p-2 text-cyan backdrop-blur">
                      <PlayIcon className="h-4 w-4" />
                    </span>
                  </span>
                )}
              </button>
            ))}
          </div>

          {total > media.length && (
            <p className="text-[11px] text-ink-faint">
              Showing {media.length} of {total}. Older items load as you narrow the filter.
            </p>
          )}
        </>
      )}

      <MediaViewer
        items={media}
        index={viewerIndex}
        onIndexChange={setViewerIndex}
        onClose={() => setViewerIndex(null)}
      />
    </section>
  );
}
