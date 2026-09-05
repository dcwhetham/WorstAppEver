"use client";

import { AnimatePresence, LayoutGroup, motion } from "framer-motion";

import { AccountCard } from "@/components/AccountCard";
import { ArchiveIcon } from "@/components/icons";
import type { AccountCard as AccountCardModel } from "@/lib/types";

/**
 * The card grid.
 *
 * `LayoutGroup` scopes the shared-element animation so the card-to-panel morph is
 * coordinated with the grid's own reflow. Without it, filtering while a card is
 * expanded can animate the panel toward a stale position.
 *
 * Entry animation is staggered but capped: past a dozen or so cards the delay is
 * clamped, because a 200-account archive would otherwise spend three seconds
 * fading in and the last cards would arrive after the user had already scrolled.
 */

const STAGGER_STEP = 0.022;
const MAX_STAGGER = 0.28;

export function AccountGrid({
  accounts,
  isLoading,
  expandedId,
  onOpen,
  onShowLogs,
  selectionMode,
  selectedIds,
  onToggleSelect,
}: {
  accounts: AccountCardModel[] | undefined;
  isLoading: boolean;
  expandedId: number | null;
  onOpen: (id: number) => void;
  onShowLogs: (id: number) => void;
  selectionMode: boolean;
  selectedIds: Set<number>;
  onToggleSelect: (id: number) => void;
}) {
  if (isLoading && !accounts) {
    return (
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {Array.from({ length: 10 }, (_, index) => (
          <div key={index} className="aspect-4/5 animate-pulse rounded-2xl bg-surface-2/70" />
        ))}
      </div>
    );
  }

  if (accounts && accounts.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-hairline py-20 text-center">
        <ArchiveIcon className="h-9 w-9 text-ink-faint opacity-50" />
        <div>
          <p className="text-[13.5px] font-medium text-ink-dim">No accounts match this view</p>
          <p className="mt-1 text-[12px] text-ink-faint">
            Clear the filters, add an account, or drop folders into <code>/archive</code> and run a scan.
          </p>
        </div>
      </div>
    );
  }

  return (
    <LayoutGroup>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        <AnimatePresence mode="popLayout">
          {accounts?.map((account, index) => (
            <motion.div
              key={account.id}
              layout
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={{
                delay: Math.min(index * STAGGER_STEP, MAX_STAGGER),
                type: "spring",
                stiffness: 300,
                damping: 30,
              }}
            >
              <AccountCard
                account={account}
                onOpen={onOpen}
                onShowLogs={onShowLogs}
                isExpanded={expandedId === account.id}
                selectionMode={selectionMode}
                isSelected={selectedIds.has(account.id)}
                onToggleSelect={onToggleSelect}
              />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </LayoutGroup>
  );
}
