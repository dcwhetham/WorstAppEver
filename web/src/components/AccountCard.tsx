"use client";

import clsx from "clsx";
import { motion, useMotionTemplate, useMotionValue, useSpring, useTransform } from "framer-motion";
import { useCallback, useRef } from "react";

import { CardFooter } from "@/components/CardFooter";
import { AlertIcon, CheckIcon, PhotoIcon } from "@/components/icons";
import { StatusBadges } from "@/components/ui/Badge";
import type { AccountCard as AccountCardModel } from "@/lib/types";
import { usePrefersReducedMotion } from "@/lib/hooks";

/**
 * ---------------------------------------------------------------------------
 * Tilt physics
 * ---------------------------------------------------------------------------
 *
 * The card sits leaning *away* from the viewer and comes forward under the
 * cursor. `REST_TILT_X` is positive, which pushes the top edge back; on hover the
 * rotation crosses zero to `LEAN_TILT_X` so the card ends up leaning slightly
 * toward you. Passing through zero is what makes the movement read as "standing
 * up" rather than "wobbling".
 *
 * Springs, not transitions, in both directions. A CSS transition on mouse-leave
 * gives a linear glide that feels mechanical and — worse — restarts from wherever
 * the pointer was, so flicking across a grid leaves cards snapping at different
 * speeds. A spring carries its velocity, so a fast flick decays naturally and
 * every card settles the same way.
 *
 * `damping: 26` against `stiffness: 260` is just under critical: a barely
 * perceptible settle, no visible bounce. Bouncier values look like a toy at this
 * card size, and a card that oscillates under the cursor is genuinely hard to
 * click.
 */
const REST_TILT_X = 7.5; // degrees, leaning backward at rest
const LEAN_TILT_X = -5; // degrees, leaning toward the viewer on hover
const MAX_POINTER_TILT = 9; // extra degrees from cursor position
const HOVER_LIFT = 26; // px of translateZ on hover

const TILT_SPRING = { stiffness: 260, damping: 26, mass: 0.6 } as const;
// Softer than the tilt: the sheen is a light effect, and a snappy highlight
// looks like a glitch rather than a reflection.
const SHEEN_SPRING = { stiffness: 150, damping: 22, mass: 0.5 } as const;

export interface AccountCardProps {
  account: AccountCardModel;
  onOpen: (id: number) => void;
  onShowLogs: (id: number) => void;
  /** True while this card is the expanded one; its shared element lives in the overlay. */
  isExpanded: boolean;
  selectionMode: boolean;
  isSelected: boolean;
  onToggleSelect: (id: number) => void;
}

export function AccountCard({
  account,
  onOpen,
  onShowLogs,
  isExpanded,
  selectionMode,
  isSelected,
  onToggleSelect,
}: AccountCardProps) {
  const reducedMotion = usePrefersReducedMotion();
  const cardRef = useRef<HTMLDivElement>(null);

  // Raw pointer-derived targets, then springs that the DOM actually reads.
  const targetX = useMotionValue(REST_TILT_X);
  const targetY = useMotionValue(0);
  const targetLift = useMotionValue(0);
  const rotateX = useSpring(targetX, TILT_SPRING);
  const rotateY = useSpring(targetY, TILT_SPRING);
  const lift = useSpring(targetLift, TILT_SPRING);

  // Sheen position as a 0-100 percentage of the card box.
  const sheenX = useSpring(useMotionValue(50), SHEEN_SPRING);
  const sheenY = useSpring(useMotionValue(50), SHEEN_SPRING);
  const sheenOpacity = useSpring(useMotionValue(0), SHEEN_SPRING);
  const sheenBackground = useMotionTemplate`radial-gradient(420px circle at ${sheenX}% ${sheenY}%, rgba(103,232,249,0.18), transparent 45%)`;

  // Shadow grows with the forward lean, which is most of what sells the depth:
  // rotation alone reads as a flat skew without a shadow that responds to it.
  const shadowStrength = useTransform(rotateX, [REST_TILT_X, LEAN_TILT_X], [0.35, 0.75]);
  const shadowSpread = useTransform(lift, [0, HOVER_LIFT], [18, 42]);
  const boxShadow = useMotionTemplate`0 ${shadowSpread}px ${shadowSpread}px -12px rgba(2,6,12,${shadowStrength})`;

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (reducedMotion || isExpanded) return;
      const element = cardRef.current;
      if (!element) return;

      const bounds = element.getBoundingClientRect();
      // Normalised to -0.5..0.5 from the card centre.
      const px = (event.clientX - bounds.left) / bounds.width - 0.5;
      const py = (event.clientY - bounds.top) / bounds.height - 0.5;

      // Cursor above centre leans the card further forward, below pushes it back:
      // the card tracks the pointer like a physical panel on a hinge.
      targetX.set(LEAN_TILT_X + py * MAX_POINTER_TILT);
      targetY.set(px * MAX_POINTER_TILT * 1.4);
      targetLift.set(HOVER_LIFT);

      sheenX.set((px + 0.5) * 100);
      sheenY.set((py + 0.5) * 100);
      sheenOpacity.set(1);
    },
    [reducedMotion, isExpanded, targetX, targetY, targetLift, sheenX, sheenY, sheenOpacity],
  );

  const handlePointerLeave = useCallback(() => {
    // Only the targets are reset; the springs interpolate back, carrying whatever
    // velocity the pointer left behind.
    targetX.set(REST_TILT_X);
    targetY.set(0);
    targetLift.set(0);
    sheenOpacity.set(0);
    sheenX.set(50);
    sheenY.set(50);
  }, [targetX, targetY, targetLift, sheenOpacity, sheenX, sheenY]);

  const handleActivate = useCallback(() => {
    if (selectionMode) onToggleSelect(account.id);
    else onOpen(account.id);
  }, [selectionMode, onToggleSelect, onOpen, account.id]);

  const hasErrors = account.unresolved_error_count > 0;
  const label = account.display_name || account.name;

  return (
    // `scene` supplies the perspective. Without it on the parent, rotateX is an
    // orthographic squash with no depth at all.
    <div className="scene">
      <motion.div
        style={
          reducedMotion
            ? undefined
            : { rotateX, rotateY, translateZ: lift, transformPerspective: 1100, boxShadow }
        }
        className="preserve-3d rounded-2xl"
      >
        {/*
          The layout-animated element is separate from the tilt layer above.
          Framer Motion drives `transform` on a `layoutId` element, so combining
          the two on one node makes the tilt fight the expansion animation.

          While expanded, the shared element is rendered by the overlay instead,
          and this slot holds an invisible placeholder that preserves grid height.
        */}
        {isExpanded ? (
          <div
            aria-hidden
            className="invisible rounded-2xl border border-hairline"
            style={{ aspectRatio: "4 / 5" }}
          />
        ) : (
          <motion.article
            ref={cardRef}
            layoutId={`account-shell-${account.id}`}
            onPointerMove={handlePointerMove}
            onPointerLeave={handlePointerLeave}
            onClick={handleActivate}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                handleActivate();
              }
            }}
            role="button"
            tabIndex={0}
            aria-label={`${label}: ${account.image_count} images, ${account.video_count} videos`}
            className={clsx(
              "group card-sheen relative isolate cursor-pointer overflow-hidden rounded-2xl border bg-surface",
              "transition-colors duration-200",
              isSelected
                ? "border-cyan/70 ring-2 ring-cyan/35"
                : hasErrors
                  ? "border-rose/35 hover:border-rose/60"
                  : "border-hairline hover:border-cyan/45",
            )}
          >
            {/* Pointer-tracked specular highlight. */}
            {!reducedMotion && (
              <motion.div
                aria-hidden
                className="pointer-events-none absolute inset-0 z-20"
                style={{ background: sheenBackground, opacity: sheenOpacity }}
              />
            )}

            <CardCover account={account} label={label} />

            <div className="relative z-10 space-y-2.5 px-3.5 pb-3.5 pt-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="truncate text-[13.5px] font-semibold text-ink" title={label}>
                    {label}
                  </h3>
                  <p className="truncate text-[11px] text-ink-faint">@{account.name}</p>
                </div>

                {hasErrors && (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onShowLogs(account.id);
                    }}
                    title={`${account.unresolved_error_count} unresolved error(s) — open log viewer`}
                    className="shrink-0 rounded-md border border-rose/40 bg-rose/10 p-1 text-rose transition hover:bg-rose/20"
                  >
                    <AlertIcon className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              <CardFooter account={account} onShowLogs={onShowLogs} />
            </div>

            {selectionMode && (
              <div className="absolute left-2.5 top-2.5 z-30">
                <span
                  className={clsx(
                    "flex h-6 w-6 items-center justify-center rounded-md border-2 transition",
                    isSelected
                      ? "border-cyan bg-cyan text-abyss"
                      : "border-ink-faint/70 bg-abyss/70 text-transparent backdrop-blur",
                  )}
                >
                  <CheckIcon className="h-3.5 w-3.5" strokeWidth={3} />
                </span>
              </div>
            )}
          </motion.article>
        )}
      </motion.div>
    </div>
  );
}

/**
 * Card artwork.
 *
 * The cover is the newest indexed image, served raw. There is no thumbnail
 * pipeline, so a full-size JPEG is scaled down by the browser — acceptable for a
 * self-hosted tool on a LAN, and it keeps the archive the single source of truth.
 * `loading="lazy"` plus `decoding="async"` stops a hundred-card grid from stalling
 * the main thread on decode.
 */
function CardCover({ account, label }: { account: AccountCardModel; label: string }) {
  return (
    <div className="relative aspect-4/5 overflow-hidden bg-void">
      {account.cover_url ? (
        <>
          <img
            src={account.cover_url}
            alt=""
            loading="lazy"
            decoding="async"
            className="h-full w-full scale-[1.02] object-cover transition-transform duration-500 ease-out group-hover:scale-[1.06]"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-surface via-surface/45 to-transparent" />
        </>
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-surface-2 to-void">
          <div className="flex flex-col items-center gap-2 text-ink-faint">
            <PhotoIcon className="h-8 w-8 opacity-40" />
            <span className="text-[26px] font-semibold tracking-tight opacity-25">
              {label.slice(0, 2).toUpperCase()}
            </span>
          </div>
        </div>
      )}

      <div className="absolute left-2.5 top-2.5 z-10 flex flex-wrap gap-1">
        <StatusBadges
          status={account.status}
          platformState={account.platform_state}
          isNew={account.is_new}
        />
      </div>
    </div>
  );
}
