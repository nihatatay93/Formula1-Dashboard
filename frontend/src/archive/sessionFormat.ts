import { ApiClientError } from "../api";

/**
 * Session-explorer formatting.
 *
 * Lap and sector times arrive as microseconds and are shown as a driver reads
 * them: a lap as m:ss.mmm, a sector as seconds to three decimals, a gap as a
 * signed delta against the fastest.
 */

const snapshotFormatter = new Intl.DateTimeFormat("en", {
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  month: "short",
  timeZone: "UTC",
  timeZoneName: "short",
  year: "numeric",
});

export function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "Session data could not be loaded from the local backend.";
}

export function formatSnapshotTime(value: string | null): string {
  return value ? snapshotFormatter.format(new Date(value)) : "Not available";
}

export function formatLapTime(value: number | null): string {
  if (value === null) {
    return "—";
  }
  const totalMilliseconds = Math.round(value / 1_000);
  const minutes = Math.floor(totalMilliseconds / 60_000);
  const seconds = Math.floor((totalMilliseconds % 60_000) / 1_000);
  const milliseconds = totalMilliseconds % 1_000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

export function formatSectorTime(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return (value / 1_000_000).toFixed(3);
}

export function formatDelta(value: number | null, fastest: number | null): string {
  if (value === null || fastest === null) {
    return "—";
  }
  const delta = (value - fastest) / 1_000_000;
  if (delta === 0) {
    return "Best";
  }
  return `${delta > 0 ? "+" : ""}${delta.toFixed(3)}`;
}

export function formatShortDelta(value: number): string {
  const seconds = Math.abs(value) / 1_000_000;
  return `${seconds.toFixed(3)}s`;
}

export function compoundTone(compound: string | null): string {
  const normalized = compound?.toLowerCase() ?? "unknown";
  return ["soft", "medium", "hard", "intermediate", "wet"].includes(normalized)
    ? normalized
    : "unknown";
}