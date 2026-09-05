"use client";

import { useState, useTransition } from "react";

import { Modal } from "@/components/ui/Modal";
import { api } from "@/lib/api";
import { useRevalidateAll } from "@/lib/hooks";

/**
 * Add-account form.
 *
 * The handle is the only required field, because it is also the folder name and
 * the basis for every derived mirror link. A leading `@` is stripped client-side
 * for immediate feedback; the server strips and validates it again, since the
 * value ends up on the filesystem.
 */
export function AddAccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const revalidate = useRevalidateAll();
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [extraLinks, setExtraLinks] = useState("");
  const [favorite, setFavorite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const reset = () => {
    setName("");
    setNotes("");
    setExtraLinks("");
    setFavorite(false);
    setError(null);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const handle = name.trim().replace(/^@/, "");
    if (!handle) return;

    startTransition(async () => {
      setError(null);
      try {
        await api.createAccount({
          name: handle,
          is_favorite: favorite,
          ...(notes.trim() ? { notes: notes.trim() } : {}),
          links: extraLinks
            .split(/[\n,]/)
            .map((value) => value.trim())
            .filter(Boolean),
        });
        revalidate();
        reset();
        onClose();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not create account");
      }
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add account"
      subtitle="Creates the archive folders, derives mirror links, and queues a paced first sync"
    >
      <form onSubmit={submit} className="space-y-4 p-5">
        <Field label="Handle" hint="Also the folder name under /archive. Letters, digits, dot, underscore, hyphen.">
          <input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="aurora.films"
            className="w-full rounded-lg border border-hairline bg-surface-2 px-3 py-2 text-[13px] text-ink placeholder:text-ink-faint/70 focus:border-cyan/50 focus:outline-none"
          />
        </Field>

        <Field label="Extra mirrors" hint="Optional. One URL per line; the provider is inferred from the host.">
          <textarea
            value={extraLinks}
            onChange={(event) => setExtraLinks(event.target.value)}
            rows={3}
            placeholder={"https://imginn.com/aurora.films.backup/"}
            className="w-full resize-y rounded-lg border border-hairline bg-surface-2 px-3 py-2 text-[12.5px] text-ink placeholder:text-ink-faint/70 focus:border-cyan/50 focus:outline-none"
          />
        </Field>

        <Field label="Notes" hint="Optional.">
          <input
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Why this account matters"
            className="w-full rounded-lg border border-hairline bg-surface-2 px-3 py-2 text-[12.5px] text-ink placeholder:text-ink-faint/70 focus:border-cyan/50 focus:outline-none"
          />
        </Field>

        <label className="flex items-center gap-2 text-[12.5px] text-ink-dim">
          <input
            type="checkbox"
            checked={favorite}
            onChange={(event) => setFavorite(event.target.checked)}
            className="h-4 w-4 accent-cyan"
          />
          Mark as favourite (checked more often by the scheduler)
        </label>

        {error && <p className="text-[12px] text-rose">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-hairline px-3 py-2 text-[12.5px] text-ink-dim transition hover:border-hairline-bright"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={pending || !name.trim()}
            className="rounded-lg border border-cyan/50 bg-cyan/15 px-3.5 py-2 text-[12.5px] font-medium text-cyan-bright transition hover:bg-cyan/25 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {pending ? "Creating…" : "Create account"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="block text-[12px] font-medium text-ink-dim">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-ink-faint">{hint}</span>}
    </label>
  );
}
