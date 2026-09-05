"use client";

import clsx from "clsx";

import { LayersIcon, SearchIcon, StarIcon } from "@/components/icons";
import type { AccountFilters, SortKey } from "@/lib/types";

const SORTS: { value: SortKey; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "recent", label: "Recently updated" },
  { value: "added", label: "Recently added" },
  { value: "media", label: "Most media" },
  { value: "size", label: "Largest" },
  { value: "backlog", label: "Biggest backlog" },
  { value: "errors", label: "Most errors" },
];

/**
 * Global search and filter bar.
 *
 * Filters are toggle chips rather than a dropdown, so the active set is visible
 * without opening anything — with a grid this dense, a hidden filter that silently
 * removes half the accounts is a support question waiting to happen.
 *
 * The search input is uncontrolled with respect to the network: the parent
 * debounces it before it reaches the query, so typing stays responsive.
 */
export function FilterBar({
  filters,
  onChange,
  selectionMode,
  onSelectionModeChange,
  resultCount,
}: {
  filters: AccountFilters;
  onChange: (next: AccountFilters) => void;
  selectionMode: boolean;
  onSelectionModeChange: (next: boolean) => void;
  resultCount: number | undefined;
}) {
  const patch = (partial: Partial<AccountFilters>) => onChange({ ...filters, ...partial });

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="relative min-w-56 flex-1">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
        <input
          type="search"
          value={filters.q}
          onChange={(event) => patch({ q: event.target.value })}
          placeholder="Search accounts…"
          className="w-full rounded-xl border border-hairline bg-surface-2 py-2.5 pl-9 pr-3 text-[13px] text-ink placeholder:text-ink-faint/70 transition focus:border-cyan/50 focus:outline-none"
        />
      </label>

      <Chip
        active={filters.favorite}
        onClick={() => patch({ favorite: !filters.favorite })}
        icon={<StarIcon className="h-3.5 w-3.5" filled={filters.favorite} />}
        tone="amber"
      >
        Favourites
      </Chip>

      <Chip
        active={filters.status === "legacy"}
        onClick={() => patch({ status: filters.status === "legacy" ? "" : "legacy" })}
        tone="violet"
      >
        Legacy
      </Chip>

      <Chip
        active={filters.status === "flagged"}
        onClick={() => patch({ status: filters.status === "flagged" ? "" : "flagged" })}
        tone="rose"
      >
        Flagged
      </Chip>

      <Chip
        active={filters.hasErrors}
        onClick={() => patch({ hasErrors: !filters.hasErrors })}
        tone="rose"
      >
        Has errors
      </Chip>

      <select
        value={filters.sort}
        onChange={(event) => patch({ sort: event.target.value as SortKey })}
        aria-label="Sort accounts"
        className="rounded-xl border border-hairline bg-surface-2 px-3 py-2.5 text-[12.5px] text-ink-dim transition focus:border-cyan/50 focus:outline-none"
      >
        {SORTS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <Chip
        active={selectionMode}
        onClick={() => onSelectionModeChange(!selectionMode)}
        icon={<LayersIcon className="h-3.5 w-3.5" />}
        tone="cyan"
      >
        Select
      </Chip>

      {resultCount !== undefined && (
        <span className="ml-auto text-[11.5px] tabular-nums text-ink-faint">
          {resultCount} account{resultCount === 1 ? "" : "s"}
        </span>
      )}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
  icon,
  tone = "cyan",
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  icon?: React.ReactNode;
  tone?: "cyan" | "amber" | "rose" | "violet";
}) {
  const activeTone = {
    cyan: "border-cyan/50 bg-cyan/12 text-cyan-bright",
    amber: "border-amber/50 bg-amber/12 text-amber",
    rose: "border-rose/50 bg-rose/12 text-rose",
    violet: "border-violet/50 bg-violet/12 text-violet",
  }[tone];

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-xl border px-3 py-2.5 text-[12.5px] font-medium transition",
        active ? activeTone : "border-hairline bg-surface-2 text-ink-faint hover:border-hairline-bright hover:text-ink-dim",
      )}
    >
      {icon}
      {children}
    </button>
  );
}
