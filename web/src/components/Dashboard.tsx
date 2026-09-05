"use client";

import { useCallback, useMemo, useState } from "react";

import { AccountGrid } from "@/components/AccountGrid";
import { BatchActionBar } from "@/components/BatchActionBar";
import { DashboardHeader } from "@/components/DashboardHeader";
import { ExpandedAccount } from "@/components/ExpandedAccount";
import { FilterBar } from "@/components/FilterBar";
import { LogViewerModal } from "@/components/LogViewerModal";
import { useAccounts, useDebounced, useSelection } from "@/lib/hooks";
import { EMPTY_FILTERS, type AccountFilters } from "@/lib/types";

/**
 * Dashboard shell.
 *
 * All the cross-cutting UI state lives here — filters, which card is expanded,
 * which account's logs are open, batch selection — because the alternative is
 * threading callbacks through the grid and card in both directions.
 *
 * The search box is stored raw but debounced before it reaches the query, so
 * typing never queues a request per keystroke while the field itself stays fully
 * responsive.
 */
export function Dashboard() {
  const [filters, setFilters] = useState<AccountFilters>(EMPTY_FILTERS);
  const debouncedQuery = useDebounced(filters.q);
  const effectiveFilters = useMemo<AccountFilters>(
    () => ({ ...filters, q: debouncedQuery }),
    [filters, debouncedQuery],
  );

  const { accounts, isLoading, error } = useAccounts(effectiveFilters);
  const selection = useSelection();

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [logAccountId, setLogAccountId] = useState<number | null>(null);

  const logAccountName = useMemo(
    () => accounts?.find((account) => account.id === logAccountId)?.name,
    [accounts, logAccountId],
  );

  const handleSelectionMode = useCallback(
    (next: boolean) => {
      selection.setMode(next);
      // Entering batch mode while a card is expanded would leave the overlay
      // covering the very grid the user is trying to select from.
      if (next) setExpandedId(null);
    },
    [selection],
  );

  return (
    <main className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <DashboardHeader />

      <FilterBar
        filters={filters}
        onChange={setFilters}
        selectionMode={selection.enabled}
        onSelectionModeChange={handleSelectionMode}
        resultCount={accounts?.length}
      />

      {error && (
        <div className="rounded-xl border border-rose/30 bg-rose/[0.06] px-4 py-3 text-[12.5px] text-rose">
          Could not reach the API: {error instanceof Error ? error.message : "unknown error"}. The
          dashboard keeps working from cached data; check that the backend container is running.
        </div>
      )}

      <AccountGrid
        accounts={accounts}
        isLoading={isLoading}
        expandedId={expandedId}
        onOpen={setExpandedId}
        onShowLogs={setLogAccountId}
        selectionMode={selection.enabled}
        selectedIds={selection.ids}
        onToggleSelect={selection.toggle}
      />

      <ExpandedAccount
        accountId={expandedId}
        onClose={() => setExpandedId(null)}
        onShowLogs={setLogAccountId}
      />

      <LogViewerModal
        accountId={logAccountId}
        accountName={logAccountName}
        onClose={() => setLogAccountId(null)}
      />

      <BatchActionBar
        selectedIds={[...selection.ids]}
        onClear={selection.clear}
        onSelectAll={() => selection.selectAll((accounts ?? []).map((account) => account.id))}
        totalVisible={accounts?.length ?? 0}
      />
    </main>
  );
}
