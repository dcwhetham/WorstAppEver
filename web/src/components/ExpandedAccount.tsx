"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useState, useTransition } from "react";

import { LinkManager } from "@/components/LinkManager";
import { MediaGallery } from "@/components/MediaGallery";
import { EtaProgress } from "@/components/EtaProgress";
import {
  AlertIcon,
  ArchiveIcon,
  CloseIcon,
  DownloadIcon,
  PhotoIcon,
  PlayIcon,
  StarIcon,
  TerminalIcon,
  VideoIcon,
} from "@/components/icons";
import { Badge, StatusBadges } from "@/components/ui/Badge";
import { api, downloadBundle } from "@/lib/api";
import { absoluteTime, compactNumber, humanBytes, relativeTime } from "@/lib/format";
import { useAccount, useDismissable, useRevalidateAll } from "@/lib/hooks";

/**
 * ---------------------------------------------------------------------------
 * Netflix-style expansion
 * ---------------------------------------------------------------------------
 *
 * The morph is a Framer Motion shared-element transition: this panel and the grid
 * card carry the same `layoutId`, and exactly one of them is mounted at a time.
 * The grid swaps its card for an invisible placeholder of identical size while
 * this is open (see `AccountCard`), which is what keeps the surrounding cards from
 * reflowing mid-animation — a shifting grid would make the panel fly back to the
 * wrong place on close.
 *
 * Content fades in *after* the box has finished travelling (`delay: 0.12`).
 * Cross-fading text while the container is still resizing produces visible
 * stretching, because the layout animation scales the element rather than
 * re-laying it out.
 */

const PANEL_SPRING = { type: "spring", stiffness: 260, damping: 30 } as const;

export function ExpandedAccount({
  accountId,
  onClose,
  onShowLogs,
}: {
  accountId: number | null;
  onClose: () => void;
  onShowLogs: (id: number) => void;
}) {
  useDismissable(accountId !== null, onClose);
  const { account } = useAccount(accountId);
  const revalidate = useRevalidateAll();
  const [pending, startTransition] = useTransition();
  const [toast, setToast] = useState<string | null>(null);

  const act = useCallback(
    (action: () => Promise<unknown>, message?: string) => {
      startTransition(async () => {
        try {
          await action();
          if (message) setToast(message);
        } catch (error) {
          setToast(error instanceof Error ? error.message : "Action failed");
        } finally {
          revalidate();
          setTimeout(() => setToast(null), 3200);
        }
      });
    },
    [revalidate],
  );

  return (
    <AnimatePresence>
      {accountId !== null && (
        <motion.div
          className="fixed inset-0 z-40 overflow-y-auto"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <motion.div
            className="fixed inset-0 bg-abyss/85 backdrop-blur-lg"
            onClick={onClose}
            aria-hidden
          />

          <div className="relative mx-auto w-full max-w-5xl px-3 py-6 sm:px-6 sm:py-10">
            <motion.article
              layoutId={`account-shell-${accountId}`}
              transition={PANEL_SPRING}
              className="overflow-hidden rounded-2xl border border-hairline-bright bg-surface shadow-[0_40px_120px_-24px_rgba(0,0,0,0.95)]"
            >
              {/* Hero. Fixed height so the layout animation has a stable target;
                  an auto-height hero measured mid-morph causes a visible jump. */}
              <div className="relative h-56 overflow-hidden bg-void sm:h-72">
                {account?.cover_url ? (
                  <>
                    <img
                      src={account.cover_url}
                      alt=""
                      className="h-full w-full object-cover opacity-70"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-surface via-surface/70 to-transparent" />
                  </>
                ) : (
                  <div className="h-full w-full bg-gradient-to-br from-surface-2 via-surface to-void" />
                )}

                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close"
                  className="absolute right-3 top-3 z-20 rounded-full border border-hairline-bright bg-abyss/70 p-2 text-ink-dim backdrop-blur transition hover:border-cyan/50 hover:text-cyan"
                >
                  <CloseIcon className="h-4 w-4" />
                </button>

                <motion.div
                  className="absolute bottom-0 left-0 right-0 p-4 sm:p-6"
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.12, duration: 0.28 }}
                >
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {account && (
                      <StatusBadges
                        status={account.status}
                        platformState={account.platform_state}
                        isNew={account.is_new}
                      />
                    )}
                    {account?.is_favorite && <Badge tone="amber">Favourite</Badge>}
                    {account && !account.scrape_enabled && <Badge tone="neutral">Scraping off</Badge>}
                  </div>

                  <h2 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                    {account?.display_name || account?.name || "Loading…"}
                  </h2>
                  {account && <p className="mt-0.5 text-sm text-ink-faint">@{account.name}</p>}
                </motion.div>
              </div>

              <motion.div
                className="space-y-6 p-4 sm:p-6"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.12, duration: 0.3 }}
              >
                {account ? (
                  <>
                    <div className={clsx("flex flex-wrap gap-2", pending && "opacity-70")}>
                      <ActionButton
                        primary
                        icon={<PlayIcon className="h-4 w-4" />}
                        label="Run Now"
                        title="Queue an on-demand scrape, skipping the scheduled window"
                        onClick={() =>
                          act(async () => {
                            const result = await api.runNow(account.id, true);
                            setToast(
                              result.created
                                ? "Scrape job queued"
                                : "A job is already pending for this account",
                            );
                          })
                        }
                      />
                      <ActionButton
                        icon={<DownloadIcon className="h-4 w-4" />}
                        label="Bundle"
                        title="Zip this account's entire archive and download it"
                        onClick={() => {
                          downloadBundle(account.id);
                          setToast("Preparing bundle — the download will start shortly");
                          setTimeout(() => setToast(null), 3200);
                        }}
                      />
                      <ActionButton
                        icon={<StarIcon className="h-4 w-4" filled={account.is_favorite} />}
                        label={account.is_favorite ? "Favourited" : "Favourite"}
                        active={account.is_favorite}
                        onClick={() => act(() => api.toggleFavorite(account.id))}
                      />
                      <ActionButton
                        icon={<ArchiveIcon className="h-4 w-4" />}
                        label={account.scrape_enabled ? "Scraping on" : "Scraping off"}
                        active={account.scrape_enabled}
                        onClick={() => act(() => api.toggleScrape(account.id))}
                      />
                      <ActionButton
                        icon={<TerminalIcon className="h-4 w-4" />}
                        label="Logs"
                        badge={account.unresolved_error_count || undefined}
                        onClick={() => onShowLogs(account.id)}
                      />
                    </div>

                    {account.active_job && (
                      <div className="rounded-xl border border-cyan/25 bg-cyan/[0.04] p-3.5">
                        <EtaProgress account={account} />
                      </div>
                    )}

                    {account.last_error && (
                      <button
                        type="button"
                        onClick={() => onShowLogs(account.id)}
                        className="flex w-full items-start gap-2.5 rounded-xl border border-rose/30 bg-rose/[0.06] p-3.5 text-left transition hover:border-rose/50"
                      >
                        <AlertIcon className="mt-0.5 h-4 w-4 shrink-0 text-rose" />
                        <span className="min-w-0">
                          <span className="block text-[13px] font-medium text-rose">
                            {account.last_error.event.replace(/_/g, " ")}
                          </span>
                          <span className="mt-0.5 block text-xs text-ink-dim">
                            {account.last_error.message}
                          </span>
                          <span className="mt-1 block text-[11px] text-ink-faint">
                            {relativeTime(account.last_error.ts)} · click to open the full log
                          </span>
                        </span>
                      </button>
                    )}

                    <section className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                      <Stat
                        icon={<PhotoIcon className="h-4 w-4 text-cyan/70" />}
                        label="Images"
                        value={compactNumber(account.image_count)}
                        hint={
                          account.expected_image_count
                            ? `${account.expected_image_count} known remotely`
                            : undefined
                        }
                      />
                      <Stat
                        icon={<VideoIcon className="h-4 w-4 text-azure/70" />}
                        label="Videos"
                        value={compactNumber(account.video_count)}
                        hint={
                          account.expected_video_count
                            ? `${account.expected_video_count} known remotely`
                            : undefined
                        }
                      />
                      <Stat label="On disk" value={humanBytes(account.total_bytes)} />
                      <Stat
                        label="Backlog"
                        value={compactNumber(
                          account.pending_remote_count || account.estimated_missing_count,
                        )}
                        hint="Discovered remotely, not yet downloaded"
                      />
                    </section>

                    <section className="grid gap-x-6 gap-y-2 rounded-xl border border-hairline bg-surface-2/60 p-4 text-[12.5px] sm:grid-cols-2">
                      <MetaRow label="Last import" value={account.last_import_at} />
                      <MetaRow label="Last download" value={account.last_download_at} />
                      <MetaRow label="Last scrape" value={account.last_scrape_at} />
                      <MetaRow label="Last success" value={account.last_success_at} />
                      <MetaRow label="Added" value={account.created_at} />
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="text-ink-faint">Consecutive failures</span>
                        <span
                          className={clsx(
                            "tabular-nums",
                            account.consecutive_failures > 0 ? "text-rose" : "text-ink-dim",
                          )}
                        >
                          {account.consecutive_failures}
                        </span>
                      </div>
                      {account.notes && (
                        <p className="sm:col-span-2 mt-1 border-t border-hairline pt-2 text-ink-dim">
                          {account.notes}
                        </p>
                      )}
                    </section>

                    <LinkManager account={account} onChanged={revalidate} />

                    <MediaGallery accountId={account.id} />
                  </>
                ) : (
                  <div className="space-y-3">
                    {[0, 1, 2].map((row) => (
                      <div key={row} className="h-16 animate-pulse rounded-xl bg-surface-2" />
                    ))}
                  </div>
                )}
              </motion.div>
            </motion.article>
          </div>

          <AnimatePresence>
            {toast && (
              <motion.div
                className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full border border-cyan/35 bg-surface/95 px-4 py-2 text-[12.5px] text-ink shadow-lg backdrop-blur"
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 10 }}
              >
                {toast}
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  primary = false,
  active = false,
  badge,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  primary?: boolean;
  active?: boolean;
  badge?: number;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title ?? label}
      className={clsx(
        "inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 text-[13px] font-medium transition",
        primary
          ? "border-cyan/60 bg-cyan/15 text-cyan-bright hover:bg-cyan/25"
          : active
            ? "border-mint/45 bg-mint/10 text-mint hover:bg-mint/20"
            : "border-hairline bg-surface-2 text-ink-dim hover:border-cyan/40 hover:text-cyan",
      )}
    >
      {icon}
      {label}
      {badge !== undefined && badge > 0 && (
        <span className="rounded-full bg-rose/25 px-1.5 text-[10px] font-semibold text-rose">{badge}</span>
      )}
    </button>
  );
}

function Stat({
  icon,
  label,
  value,
  hint,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-hairline bg-surface-2/60 p-3" title={hint}>
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-ink-faint">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-ink">{value}</div>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-ink-faint">{label}</span>
      <span className="text-ink-dim" title={absoluteTime(value)}>
        {relativeTime(value)}
      </span>
    </div>
  );
}
