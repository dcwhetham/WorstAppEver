import clsx from "clsx";
import type { ReactNode } from "react";

import type { AccountStatus, PlatformState } from "@/lib/types";

type Tone = "neutral" | "cyan" | "amber" | "rose" | "mint" | "violet";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-3/80 text-ink-dim border-hairline-bright",
  cyan: "bg-cyan/12 text-cyan-bright border-cyan/35",
  amber: "bg-amber/12 text-amber border-amber/35",
  rose: "bg-rose/12 text-rose border-rose/40",
  mint: "bg-mint/12 text-mint border-mint/35",
  violet: "bg-violet/12 text-violet border-violet/35",
};

export function Badge({
  children,
  tone = "neutral",
  className,
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-[3px] text-[10.5px] font-medium tracking-wide uppercase",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * `status` and `platform_state` are separate columns, and the badges keep them
 * separate too. Collapsing them would lose the distinction between "we filed this
 * as legacy" and "the platform says it is gone" — which are different problems
 * with different fixes.
 */
export function StatusBadges({
  status,
  platformState,
  isNew,
}: {
  status: AccountStatus;
  platformState: PlatformState;
  isNew?: boolean;
}) {
  return (
    <>
      {status === "legacy" && (
        <Badge tone="violet" title="Archived and frozen; no longer synced">
          Legacy
        </Badge>
      )}
      {status === "flagged" && (
        <Badge tone="rose" title="Needs attention: unavailable or banned upstream">
          Flagged
        </Badge>
      )}
      {platformState === "banned" && (
        <Badge tone="rose" title="Suspended on the platform">
          Banned
        </Badge>
      )}
      {platformState === "unavailable" && (
        <Badge tone="amber" title="Profile returns 404 on every source">
          Unavailable
        </Badge>
      )}
      {platformState === "private" && (
        <Badge tone="amber" title="Profile exists but is not visible to us">
          Private
        </Badge>
      )}
      {isNew && (
        <Badge tone="cyan" title="Never synced; the first pull is being paced">
          First sync
        </Badge>
      )}
    </>
  );
}
