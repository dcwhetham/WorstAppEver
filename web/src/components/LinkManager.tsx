"use client";

import clsx from "clsx";
import { useState, useTransition } from "react";

import { LinkIcon, PlusIcon, TrashIcon } from "@/components/icons";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { AccountDetail } from "@/lib/types";

/**
 * External links and user-managed mirrors.
 *
 * Derived links (Instagram, Imginn, Pixnoy) are generated from the handle and are
 * not deletable here — removing one would just have it reappear on the next
 * re-derive. Manual mirrors are the user's, so those get a delete button. The
 * provider for a pasted URL is inferred server-side from its host, which means the
 * add form is one field instead of a dropdown the user has to think about.
 */

const PROVIDER_TINT: Record<string, string> = {
  instagram: "text-rose",
  imginn: "text-cyan",
  pixnoy: "text-azure",
  picuki: "text-violet",
  dumpor: "text-mint",
  sotwe: "text-amber",
  custom: "text-ink-dim",
};

export function LinkManager({ account, onChanged }: { account: AccountDetail; onChanged: () => void }) {
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;

    startTransition(async () => {
      setError(null);
      try {
        await api.addLink(account.id, {
          url: trimmed,
          ...(label.trim() ? { label: label.trim() } : {}),
        });
        setUrl("");
        setLabel("");
        onChanged();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not add link");
      }
    });
  };

  const remove = (linkId: number) => {
    startTransition(async () => {
      try {
        await api.removeLink(account.id, linkId);
        onChanged();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not remove link");
      }
    });
  };

  return (
    <section className="space-y-3">
      <h3 className="text-[13px] font-semibold uppercase tracking-wide text-ink-faint">
        Sources &amp; mirrors
      </h3>

      <ul className="grid gap-1.5 sm:grid-cols-2">
        {account.links.map((link) => (
          <li
            key={link.id}
            className="flex items-center gap-2 rounded-lg border border-hairline bg-surface-2/60 px-2.5 py-2"
          >
            <LinkIcon className={clsx("h-3.5 w-3.5 shrink-0", PROVIDER_TINT[link.provider] ?? "text-ink-dim")} />

            <a
              href={link.url}
              target="_blank"
              rel="noreferrer noopener"
              className="min-w-0 flex-1 truncate text-[12.5px] text-ink-dim transition hover:text-cyan"
              title={link.url}
            >
              {link.label || link.provider}
              {link.remote_handle && link.remote_handle !== account.name && (
                <span className="ml-1 text-ink-faint">@{link.remote_handle}</span>
              )}
            </a>

            {link.last_error_at && !link.last_ok_at && (
              <span className="shrink-0 text-[10px] text-rose" title={`Last failed ${relativeTime(link.last_error_at)}`}>
                failing
              </span>
            )}

            {link.kind === "manual" ? (
              <button
                type="button"
                onClick={() => remove(link.id)}
                aria-label={`Remove ${link.label || link.url}`}
                className="shrink-0 rounded p-1 text-ink-faint transition hover:text-rose"
              >
                <TrashIcon className="h-3.5 w-3.5" />
              </button>
            ) : (
              <span
                className="shrink-0 text-[10px] uppercase tracking-wide text-ink-faint/60"
                title="Generated from the account handle"
              >
                auto
              </span>
            )}
          </li>
        ))}
      </ul>

      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <input
          type="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://imginn.com/other.handle/"
          className="min-w-0 flex-1 rounded-lg border border-hairline bg-surface-2 px-3 py-2 text-[12.5px] text-ink placeholder:text-ink-faint/70 focus:border-cyan/50 focus:outline-none"
        />
        <input
          type="text"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Label (optional)"
          className="w-36 rounded-lg border border-hairline bg-surface-2 px-3 py-2 text-[12.5px] text-ink placeholder:text-ink-faint/70 focus:border-cyan/50 focus:outline-none"
        />
        <button
          type="submit"
          disabled={pending || !url.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-cyan/45 bg-cyan/12 px-3 py-2 text-[12.5px] font-medium text-cyan-bright transition hover:bg-cyan/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <PlusIcon className="h-3.5 w-3.5" />
          Add mirror
        </button>
      </form>

      {error && <p className="text-[11.5px] text-rose">{error}</p>}
    </section>
  );
}
