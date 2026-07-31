import type { IngestionStatus, SeasonStatus } from "../contracts";
import { humanize, statusTone } from "./format";

/**
 * One ingestion or season status, as a labelled pill.
 *
 * The label is always written out rather than left to the dot's colour, so the
 * state survives a reader who cannot separate the hues.
 */
export default function StatusPill({
  status,
}: {
  status: SeasonStatus | IngestionStatus | "available" | "not_due";
}) {
  const tone =
    status === "available"
      ? "success"
      : status === "not_due"
        ? "neutral"
        : statusTone(status);

  return (
    <span className={`status-pill status-pill--${tone}`}>
      <span className="status-pill__dot" aria-hidden="true" />
      {humanize(status)}
    </span>
  );
}
