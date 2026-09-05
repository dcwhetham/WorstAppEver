"use client";

import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";

import { useDismissable } from "@/lib/hooks";
import { CloseIcon } from "@/components/icons";

/**
 * Centred modal with a blurred scrim.
 *
 * Two details that are easy to miss and annoying to live without: clicks are
 * closed on the scrim only (`stopPropagation` on the panel), and the panel is
 * `max-h-[85vh]` with its own scroll area so a long log never pushes the header
 * and close button off screen.
 */
export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useDismissable(open, onClose);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-8"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={onClose}
        >
          <div className="absolute inset-0 bg-abyss/80 backdrop-blur-md" />

          <motion.div
            role="dialog"
            aria-modal="true"
            className={clsxPanel(wide)}
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 8 }}
            transition={{ type: "spring", stiffness: 320, damping: 30 }}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4">
              <div className="min-w-0">
                <h2 className="truncate text-[15px] font-semibold text-ink">{title}</h2>
                {subtitle && <p className="mt-0.5 text-xs text-ink-faint">{subtitle}</p>}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="shrink-0 rounded-lg border border-hairline p-1.5 text-ink-dim transition hover:border-cyan/40 hover:text-cyan"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>

            {footer && <footer className="border-t border-hairline px-5 py-3">{footer}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function clsxPanel(wide: boolean): string {
  return [
    "relative flex max-h-[85vh] w-full flex-col overflow-hidden rounded-2xl",
    "border border-hairline-bright bg-surface shadow-[0_30px_80px_-20px_rgba(0,0,0,0.9)]",
    wide ? "max-w-4xl" : "max-w-2xl",
  ].join(" ");
}
