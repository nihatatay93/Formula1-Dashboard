import { ApiClientError, ensureLapTelemetry, getLapTelemetry } from "./api";
import type {
  LapTelemetryResponse,
  LapTelemetrySample,
  TelemetryIngestionStatus,
} from "./contracts";

/**
 * Loading one lap's telemetry.
 *
 * Two things make this more than a fetch. Telemetry is not stored until it is
 * asked for, so a request may be answered by the worker minutes later; and the
 * samples are keyset-paginated, so a lap arrives in pages rather than at once.
 * Both loops are bounded — an unbounded poll or page walk against an upstream
 * that never completes would spin forever.
 */

/** The read endpoint caps a page at 1000 samples. */
export const TELEMETRY_PAGE_LIMIT = 1000;

/**
 * A racing lap is on the order of 1000–2000 samples, so a handful of pages
 * covers one. The cap exists so a bad `has_more` can never loop forever.
 */
export const MAX_TELEMETRY_PAGES = 12;

/** The API asks for two seconds; polling slower than that wastes the wait. */
export const TELEMETRY_POLL_INTERVAL_MS = 2_000;

/** Roughly two minutes. Ingestion that slow is reported, not waited on. */
export const MAX_TELEMETRY_POLLS = 60;

export class TelemetryTimeoutError extends Error {
  constructor() {
    super("Telemetry is still being fetched. Try again in a moment.");
    this.name = "TelemetryTimeoutError";
  }
}

export interface TelemetryLoad {
  samples: LapTelemetrySample[];
  response: LapTelemetryResponse;
  /** True when the sample walk stopped at the page cap rather than the end. */
  truncated: boolean;
}

const TERMINAL: ReadonlySet<TelemetryIngestionStatus> = new Set([
  "completed",
  "failed",
]);

function isNotRequested(error: unknown): boolean {
  return (
    error instanceof ApiClientError && error.code === "telemetry_not_requested"
  );
}

/** Read every page of a lap's samples, bounded by `MAX_TELEMETRY_PAGES`. */
export async function readAllSamples(
  sessionId: string,
  sessionEntryId: string,
  lapNumber: number,
  signal?: AbortSignal,
): Promise<TelemetryLoad> {
  const samples: LapTelemetrySample[] = [];
  let after: number | undefined;
  let response = await getLapTelemetry(
    sessionId,
    sessionEntryId,
    lapNumber,
    { limit: TELEMETRY_PAGE_LIMIT },
    signal,
  );
  const first = response;
  let pages = 0;

  for (;;) {
    samples.push(...response.items);
    pages += 1;
    if (!response.page.has_more || response.page.next_after_sample === null) {
      return { samples, response: first, truncated: false };
    }
    // Stop before requesting a page this walk would not consume.
    if (pages >= MAX_TELEMETRY_PAGES) {
      return { samples, response: first, truncated: true };
    }
    after = response.page.next_after_sample;
    response = await getLapTelemetry(
      sessionId,
      sessionEntryId,
      lapNumber,
      { after_sample: after, limit: TELEMETRY_PAGE_LIMIT },
      signal,
    );
  }
}

/**
 * Request a lap's telemetry and wait for it, then read every sample.
 *
 * `sleep` is injected so tests drive the poll loop without real timers.
 */
export async function loadLapTelemetry(
  sessionId: string,
  sessionEntryId: string,
  lapNumber: number,
  options: {
    signal?: AbortSignal;
    sleep?: (ms: number) => Promise<void>;
  } = {},
): Promise<TelemetryLoad> {
  const { signal } = options;
  const sleep =
    options.sleep ??
    ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));

  const command = await ensureLapTelemetry(
    sessionId,
    sessionEntryId,
    lapNumber,
    signal,
  );

  if (command.action === "available") {
    return readAllSamples(sessionId, sessionEntryId, lapNumber, signal);
  }

  for (let poll = 0; poll < MAX_TELEMETRY_POLLS; poll += 1) {
    await sleep(TELEMETRY_POLL_INTERVAL_MS);
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    let response: LapTelemetryResponse;
    try {
      response = await getLapTelemetry(
        sessionId,
        sessionEntryId,
        lapNumber,
        { limit: 1 },
        signal,
      );
    } catch (error) {
      // A queued lap can briefly still read as never-requested; keep waiting
      // rather than reporting a failure the worker is about to resolve.
      if (isNotRequested(error)) {
        continue;
      }
      throw error;
    }
    if (response.data_available) {
      return readAllSamples(sessionId, sessionEntryId, lapNumber, signal);
    }
    if (TERMINAL.has(response.ingestion.status)) {
      // Completed-but-unavailable means an empty or superseded ingestion; the
      // caller reports it from the response rather than retrying blindly.
      return { samples: [], response, truncated: false };
    }
  }

  throw new TelemetryTimeoutError();
}
