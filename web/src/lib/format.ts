/** Formatting helpers for the card footers and metadata rows. */

/**
 * Relative time, coarse on purpose.
 *
 * "3 days ago" is what someone glancing at a card actually wants; the exact
 * timestamp goes in a `title` attribute for the rare case they need it. Falls
 * back to "never" rather than an empty string, because a blank slot next to
 * "Last import" reads as a rendering bug.
 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return "unknown";

  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 90) return "a minute ago";

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.round(hours / 24);
  if (days < 31) return `${days}d ago`;

  const months = Math.round(days / 30.4);
  if (months < 12) return `${months}mo ago`;

  return `${Math.round(months / 12)}y ago`;
}

export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const stamp = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(stamp.valueOf())) return "unknown";
  return stamp.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function humanBytes(bytes: number | null | undefined): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/**
 * Duration for the ETA readout.
 *
 * Rounded to whole minutes above an hour: claiming "2h 14m 08s remaining" from a
 * median-of-twenty estimate is false precision, and watching a seconds digit tick
 * on an estimate that jumps by minutes looks broken.
 */
export function humanDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "estimating…";
  if (seconds <= 0) return "done";
  if (seconds < 60) return `${Math.round(seconds)}s`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }

  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) return restMinutes ? `${hours}h ${restMinutes}m` : `${hours}h`;

  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function compactNumber(value: number): string {
  if (value < 1000) return String(value);
  if (value < 10_000) return `${(value / 1000).toFixed(1)}k`;
  if (value < 1_000_000) return `${Math.round(value / 1000)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

/** Seconds to `m:ss`, for video durations. */
export function clockTime(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  return `${minutes}:${String(whole % 60).padStart(2, "0")}`;
}

export function pacingLabel(paceDelayMs: number | null | undefined): string | null {
  if (!paceDelayMs) return null;
  return `pacing ${(paceDelayMs / 1000).toFixed(1)}s/item`;
}
