import { ApiClientError } from "../api";
import type { IngestionStatus, SeasonStatus } from "../contracts";

/**
 * Formatting shared by the archive and live paths.
 *
 * Every timestamp the backend returns is UTC and is displayed as UTC. A local
 * timezone would silently move a session's date for the reader, and a
 * dashboard about race weekends cannot afford that.
 */

const shortDateFormatter = new Intl.DateTimeFormat("en", {
  day: "2-digit",
  month: "short",
  timeZone: "UTC",
});

const dateTimeFormatter = new Intl.DateTimeFormat("en", {
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  month: "short",
  timeZone: "UTC",
  timeZoneName: "short",
  year: "numeric",
});

export function humanize(value: string): string {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatDate(value: string | null): string {
  return value ? shortDateFormatter.format(new Date(value)) : "TBC";
}

export function formatDateTime(value: string | null): string {
  return value ? dateTimeFormatter.format(new Date(value)) : "Not available";
}

export function formatCountdown(value: string | null, now: number): string {
  if (!value) {
    return "Ready now";
  }
  const seconds = Math.max(
    0,
    Math.ceil((new Date(value).getTime() - now) / 1_000),
  );
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "The dashboard could not reach the backend. Check the local stack and retry.";
}

export function statusTone(
  status: SeasonStatus | IngestionStatus,
): "neutral" | "active" | "success" | "warning" | "danger" {
  switch (status) {
    case "completed":
      return "success";
    case "running":
      return "active";
    case "partial":
    case "stale":
      return "warning";
    case "failed":
      return "danger";
    default:
      return "neutral";
  }
}
