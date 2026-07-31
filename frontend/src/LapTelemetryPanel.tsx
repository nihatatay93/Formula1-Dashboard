import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError } from "./api";
import LapTelemetryChart from "./LapTelemetryChart";
import type { LapTelemetryResponse, LapTelemetrySample } from "./contracts";
import { TelemetryTimeoutError, loadLapTelemetry } from "./lapTelemetry";

/**
 * Telemetry for one lap.
 *
 * Telemetry is the expensive upstream call the request budget exists to
 * protect, so it is fetched only for a lap a reader explicitly asks for, one
 * lap at a time. The first request for a lap usually has to be fetched by the
 * worker, so waiting is the normal path rather than an error.
 */

function describe(error: unknown): string {
  if (error instanceof TelemetryTimeoutError) {
    return error.message;
  }
  if (error instanceof ApiClientError) {
    if (error.code === "telemetry_unavailable") {
      return "The upstream archive has no telemetry for this lap.";
    }
    return error.message;
  }
  return "Telemetry could not be loaded.";
}

export default function LapTelemetryPanel({
  driverName,
  lapNumber,
  onClose,
  sessionEntryId,
  sessionId,
}: {
  driverName: string;
  lapNumber: number;
  onClose: () => void;
  sessionEntryId: string;
  sessionId: string;
}) {
  const [samples, setSamples] = useState<LapTelemetrySample[] | null>(null);
  const [response, setResponse] = useState<LapTelemetryResponse | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const result = await loadLapTelemetry(
          sessionId,
          sessionEntryId,
          lapNumber,
          { signal },
        );
        if (signal.aborted) {
          return;
        }
        setSamples(result.samples);
        setResponse(result.response);
        setTruncated(result.truncated);
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return;
        }
        setError(describe(caught));
      } finally {
        if (!signal.aborted) {
          setLoading(false);
        }
      }
    },
    [lapNumber, sessionEntryId, sessionId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setSamples(null);
    setResponse(null);
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // Opening the panel moves focus to it, so a keyboard reader is not left where
  // the button used to be.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const ingestion = response?.ingestion ?? null;
  const incompatible = response !== null && !response.snapshot.compatible;

  return (
    <section aria-labelledby="lap-telemetry-title" className="telemetry-panel">
      <div className="section-heading">
        <div>
          <p className="section-kicker">Lap telemetry</p>
          <h4 id="lap-telemetry-title" ref={headingRef} tabIndex={-1}>
            {driverName} · lap {lapNumber}
          </h4>
        </div>
        <button className="secondary-action" onClick={onClose} type="button">
          Close telemetry
        </button>
      </div>

      {loading ? (
        <div className="session-explorer__loading" aria-live="polite">
          <span />
          Fetching this lap from the archive. The first request for a lap is
          queued for the ingestion worker, so this can take a moment.
        </div>
      ) : null}

      {error ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Telemetry unavailable</strong>
          <span>{error}</span>
          <button
            className="secondary-action"
            onClick={() => {
              const controller = new AbortController();
              void load(controller.signal);
            }}
            type="button"
          >
            Try again
          </button>
        </div>
      ) : null}

      {!loading && ingestion?.status === "failed" ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>The archive fetch failed</strong>
          <span>
            {[
              ingestion.last_error
                ? `${ingestion.last_error.message} (${ingestion.last_error.code}).`
                : "The upstream request did not complete.",
              ingestion.attempt_count > 1
                ? `After ${ingestion.attempt_count} attempts.`
                : null,
            ]
              .filter(Boolean)
              .join(" ")}
          </span>
        </div>
      ) : null}

      {incompatible ? (
        <div className="inline-alert inline-alert--warning" role="status">
          <strong>Stored against an older snapshot</strong>
          <span>
            This lap's telemetry was fetched before the session was re-ingested,
            so it is not shown. Request it again to refresh it.
          </span>
        </div>
      ) : null}

      {truncated ? (
        <div className="inline-alert inline-alert--warning" role="status">
          <strong>Showing the start of this lap</strong>
          <span>
            The lap returned more sample pages than this view reads, so the
            trace stops early.
          </span>
        </div>
      ) : null}

      {!loading &&
      !error &&
      samples !== null &&
      samples.length === 0 &&
      !incompatible &&
      ingestion?.status !== "failed" ? (
        <p className="session-explorer__hint">
          No telemetry samples were stored for this lap.
        </p>
      ) : null}

      {samples !== null && samples.length > 0 ? (
        <LapTelemetryChart
          driverName={driverName}
          lapNumber={lapNumber}
          samples={samples}
        />
      ) : null}
    </section>
  );
}
