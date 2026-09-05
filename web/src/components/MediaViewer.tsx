"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect } from "react";

import { ChevronLeftIcon, ChevronRightIcon, CloseIcon, DownloadIcon } from "@/components/icons";
import { absoluteTime, humanBytes } from "@/lib/format";
import type { MediaItem } from "@/lib/types";

/**
 * Full-screen viewer with keyboard navigation.
 *
 * Bindings: arrows and Home/End move, Escape closes, Space plays or pauses a
 * video. Space is deliberately intercepted only when the current item is a
 * video — otherwise the browser would scroll the page behind the overlay.
 *
 * Videos play the raw file straight from the archive. The backend answers Range
 * requests with 206 responses, which is what makes the scrubber work; without
 * that, the browser buffers the whole file before it will seek.
 */
export function MediaViewer({
  items,
  index,
  onIndexChange,
  onClose,
}: {
  items: MediaItem[];
  index: number | null;
  onIndexChange: (index: number) => void;
  onClose: () => void;
}) {
  const open = index !== null && index >= 0 && index < items.length;
  const current = open ? items[index] : undefined;

  const step = useCallback(
    (delta: number) => {
      if (index === null || items.length === 0) return;
      // Wrap around. In a viewer that is opened from a grid, hitting a hard stop
      // at the end feels like a bug more often than it feels like a guardrail.
      const next = (index + delta + items.length) % items.length;
      onIndexChange(next);
    },
    [index, items.length, onIndexChange],
  );

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      switch (event.key) {
        case "Escape":
          event.preventDefault();
          // stopPropagation so Escape closes the viewer without also closing the
          // expanded account panel underneath it.
          event.stopPropagation();
          onClose();
          break;
        case "ArrowRight":
        case "ArrowDown":
          event.preventDefault();
          step(1);
          break;
        case "ArrowLeft":
        case "ArrowUp":
          event.preventDefault();
          step(-1);
          break;
        case "Home":
          event.preventDefault();
          onIndexChange(0);
          break;
        case "End":
          event.preventDefault();
          onIndexChange(items.length - 1);
          break;
        case " ":
          if (current?.media_type === "video") {
            event.preventDefault();
            const video = document.querySelector<HTMLVideoElement>("[data-viewer-video]");
            if (video) {
              if (video.paused) void video.play();
              else video.pause();
            }
          }
          break;
        default:
          break;
      }
    };

    // Capture phase so the viewer wins over the account panel's own Escape handler.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [open, step, onClose, onIndexChange, items.length, current?.media_type]);

  return (
    <AnimatePresence>
      {open && current && (
        <motion.div
          className="fixed inset-0 z-60 flex flex-col bg-abyss/96 backdrop-blur-xl"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.16 }}
          onClick={onClose}
        >
          <header
            className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-3"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="min-w-0">
              <p className="truncate text-[13px] font-medium text-ink">{current.filename}</p>
              <p className="text-[11px] text-ink-faint">
                {humanBytes(current.bytes)}
                {current.width && current.height && ` · ${current.width}×${current.height}`}
                {current.captured_at && ` · ${absoluteTime(current.captured_at)}`}
                <span className="ml-2 text-ink-faint/70">
                  {index! + 1} / {items.length}
                </span>
              </p>
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              <a
                href={`${current.raw_url}?download=true`}
                download={current.filename}
                className="rounded-lg border border-hairline p-2 text-ink-dim transition hover:border-cyan/45 hover:text-cyan"
                title="Download this file"
              >
                <DownloadIcon className="h-4 w-4" />
              </a>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close viewer"
                className="rounded-lg border border-hairline p-2 text-ink-dim transition hover:border-cyan/45 hover:text-cyan"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            </div>
          </header>

          <div className="relative flex min-h-0 flex-1 items-center justify-center p-4">
            <NavButton side="left" onClick={() => step(-1)} />

            <motion.div
              // Keyed on id so switching items animates instead of swapping abruptly.
              key={current.id}
              className="flex max-h-full max-w-full items-center justify-center"
              initial={{ opacity: 0, scale: 0.985 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.18 }}
              onClick={(event) => event.stopPropagation()}
            >
              {current.media_type === "video" ? (
                <video
                  data-viewer-video
                  src={current.raw_url}
                  controls
                  autoPlay
                  // No `preload="auto"`: with Range support the browser fetches
                  // only what it needs, and forcing a full preload defeats that.
                  preload="metadata"
                  className="max-h-[78vh] max-w-full rounded-lg bg-black"
                />
              ) : (
                <img
                  src={current.raw_url}
                  alt={current.filename}
                  className="max-h-[78vh] max-w-full rounded-lg object-contain"
                />
              )}
            </motion.div>

            <NavButton side="right" onClick={() => step(1)} />
          </div>

          <footer className="border-t border-hairline px-4 py-2 text-center text-[11px] text-ink-faint">
            <kbd className="rounded border border-hairline px-1.5 py-0.5">←</kbd>
            <kbd className="ml-1 rounded border border-hairline px-1.5 py-0.5">→</kbd> navigate ·
            <kbd className="ml-1.5 rounded border border-hairline px-1.5 py-0.5">Space</kbd> play/pause ·
            <kbd className="ml-1.5 rounded border border-hairline px-1.5 py-0.5">Esc</kbd> close
          </footer>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function NavButton({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      aria-label={side === "left" ? "Previous" : "Next"}
      className={`absolute ${
        side === "left" ? "left-3" : "right-3"
      } z-10 rounded-full border border-hairline bg-surface/80 p-2.5 text-ink-dim backdrop-blur transition hover:border-cyan/50 hover:text-cyan`}
    >
      {side === "left" ? (
        <ChevronLeftIcon className="h-5 w-5" />
      ) : (
        <ChevronRightIcon className="h-5 w-5" />
      )}
    </button>
  );
}
