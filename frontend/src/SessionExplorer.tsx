import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiClientError,
  getSessionDetail,
  getSessionLaps,
  getSessionResults,
} from "./api";
import type {
  LapSummary,
  LapSummaryResponse,
  SeasonEvent,
  SeasonSession,
  SessionDetail,
  SessionEntryResult,
  SessionResults,
} from "./contracts";

const LAP_PAGE_SIZE = 50;

const snapshotFormatter = new Intl.DateTimeFormat("en", {
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  month: "short",
  timeZone: "UTC",
  timeZoneName: "short",
  year: "numeric",
});

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "Session data could not be loaded from the local backend.";
}

function formatSnapshotTime(value: string | null): string {
  return value ? snapshotFormatter.format(new Date(value)) : "Not available";
}

function formatLapTime(value: number | null): string {
  if (value === null) {
    return "—";
  }
  const totalMilliseconds = Math.round(value / 1_000);
  const minutes = Math.floor(totalMilliseconds / 60_000);
  const seconds = Math.floor((totalMilliseconds % 60_000) / 1_000);
  const milliseconds = totalMilliseconds % 1_000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function formatSectorTime(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return (value / 1_000_000).toFixed(3);
}

function formatDelta(value: number | null, fastest: number | null): string {
  if (value === null || fastest === null) {
    return "—";
  }
  const delta = (value - fastest) / 1_000_000;
  if (delta === 0) {
    return "Best";
  }
  return `${delta > 0 ? "+" : ""}${delta.toFixed(3)}`;
}

function compoundTone(compound: string | null): string {
  const normalized = compound?.toLowerCase() ?? "unknown";
  return ["soft", "medium", "hard", "intermediate", "wet"].includes(normalized)
    ? normalized
    : "unknown";
}

function LapPaceChart({ laps }: { laps: LapSummary[] }) {
  const timedLaps = laps.filter(
    (lap): lap is LapSummary & { lap_time_us: number } =>
      lap.lap_time_us !== null,
  );
  if (timedLaps.length === 0) {
    return (
      <div className="lap-chart lap-chart--empty">
        No lap times are available in this page.
      </div>
    );
  }

  const values = timedLaps.map((lap) => lap.lap_time_us);
  const fastest = Math.min(...values);
  const slowest = Math.max(...values);
  const range = Math.max(1, slowest - fastest);

  return (
    <div
      aria-label={`Loaded lap-time profile from lap ${timedLaps[0].lap_number} to ${timedLaps[timedLaps.length - 1].lap_number}`}
      className="lap-chart"
      role="img"
    >
      <div className="lap-chart__plot">
        {timedLaps.map((lap) => {
          const height = 34 + ((slowest - lap.lap_time_us) / range) * 66;
          const qualityClass =
            lap.deleted === true || !lap.is_accurate
              ? " lap-chart__bar--muted"
              : "";
          return (
            <span
              className={`lap-chart__bar lap-chart__bar--${compoundTone(lap.compound)}${qualityClass}`}
              key={lap.id}
              style={{ height: `${height}%` }}
              title={`Lap ${lap.lap_number} · ${formatLapTime(lap.lap_time_us)} · ${lap.compound ?? "compound unknown"}`}
            >
              <small>{lap.lap_number}</small>
            </span>
          );
        })}
      </div>
      <div className="lap-chart__legend" aria-hidden="true">
        <span>Slower</span>
        <i />
        <span>Faster</span>
      </div>
    </div>
  );
}

function SessionSummary({
  detail,
  fallbackEvent,
  fallbackSession,
}: {
  detail: SessionDetail | null;
  fallbackEvent: SeasonEvent;
  fallbackSession: SeasonSession;
}) {
  const event = detail?.event ?? fallbackEvent;
  const sessionName = detail?.session_name ?? fallbackSession.session_name;

  return (
    <div className="session-explorer__summary">
      <div className="session-explorer__round">
        <span>Round</span>
        <strong>{String(event.round_number).padStart(2, "0")}</strong>
      </div>
      <div>
        <p>
          {[event.location, event.country].filter(Boolean).join(" · ") ||
            "Location TBC"}
        </p>
        <h2 id="session-explorer-title">{event.event_name}</h2>
        <strong>{sessionName}</strong>
      </div>
    </div>
  );
}

function ResultsTable({
  items,
  selectedEntryId,
  onSelectEntry,
}: {
  items: SessionEntryResult[];
  selectedEntryId: string | null;
  onSelectEntry: (entry: SessionEntryResult) => void;
}) {
  return (
    <div className="results-table-wrap">
      <table className="results-table">
        <thead>
          <tr>
            <th scope="col">Pos</th>
            <th scope="col">Driver</th>
            <th scope="col">Team</th>
            <th scope="col">No.</th>
            <th scope="col">Status</th>
            <th scope="col">Laps</th>
            <th scope="col">Points</th>
            <th scope="col">
              <span className="sr-only">Lap action</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((entry) => (
            <tr
              className={
                selectedEntryId === entry.session_entry_id
                  ? "results-table__selected"
                  : undefined
              }
              key={entry.session_entry_id}
            >
              <td className="results-table__position">
                {entry.result?.classified_position ??
                  entry.result?.position ??
                  "—"}
              </td>
              <td>
                <div className="driver-cell">
                  <span
                    aria-hidden="true"
                    style={{
                      backgroundColor: entry.team_color_hex ?? "#667085",
                    }}
                  />
                  <div>
                    <strong>{entry.display_name}</strong>
                    <small>{entry.abbreviation ?? "—"}</small>
                  </div>
                </div>
              </td>
              <td>{entry.team_name ?? "Independent"}</td>
              <td>{entry.racing_number ?? "—"}</td>
              <td>{entry.result?.status ?? "No classification"}</td>
              <td>{entry.result?.laps_completed ?? "—"}</td>
              <td>{entry.result?.points ?? "—"}</td>
              <td>
                <button
                  aria-pressed={selectedEntryId === entry.session_entry_id}
                  className="lap-action"
                  onClick={() => onSelectEntry(entry)}
                  type="button"
                >
                  {selectedEntryId === entry.session_entry_id
                    ? "Viewing"
                    : "View laps"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LapTable({
  laps,
}: {
  laps: LapSummary[];
}) {
  const fastest = useMemo(() => {
    const timed = laps
      .filter((lap) => lap.deleted !== true && lap.is_accurate)
      .map((lap) => lap.lap_time_us)
      .filter((value): value is number => value !== null);
    return timed.length > 0 ? Math.min(...timed) : null;
  }, [laps]);

  return (
    <div className="lap-table-wrap">
      <table className="lap-table">
        <thead>
          <tr>
            <th scope="col">Lap</th>
            <th scope="col">Time</th>
            <th scope="col">Delta</th>
            <th scope="col">Stint</th>
            <th scope="col">Tyre</th>
            <th scope="col">Life</th>
            <th scope="col">S1</th>
            <th scope="col">S2</th>
            <th scope="col">S3</th>
            <th scope="col">Quality</th>
          </tr>
        </thead>
        <tbody>
          {laps.map((lap) => (
            <tr
              className={lap.deleted === true ? "lap-table__deleted" : undefined}
              key={lap.id}
            >
              <td>
                <strong>{lap.lap_number}</strong>
              </td>
              <td className="lap-table__time">
                {formatLapTime(lap.lap_time_us)}
              </td>
              <td>{formatDelta(lap.lap_time_us, fastest)}</td>
              <td>{lap.stint_number ?? "—"}</td>
              <td>
                <span
                  className={`compound compound--${compoundTone(lap.compound)}`}
                >
                  {lap.compound ?? "Unknown"}
                </span>
              </td>
              <td>{lap.tyre_life_laps ?? "—"}</td>
              <td>{formatSectorTime(lap.sector_1_time_us)}</td>
              <td>{formatSectorTime(lap.sector_2_time_us)}</td>
              <td>{formatSectorTime(lap.sector_3_time_us)}</td>
              <td>
                {lap.deleted === true
                  ? "Deleted"
                  : lap.is_accurate
                    ? "Accurate"
                    : "Unverified"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SessionExplorer({
  event,
  onClose,
  session,
}: {
  event: SeasonEvent;
  onClose: () => void;
  session: SeasonSession;
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedEntry, setSelectedEntry] =
    useState<SessionEntryResult | null>(null);
  const [laps, setLaps] = useState<LapSummaryResponse | null>(null);
  const [lapCursor, setLapCursor] = useState<number | null>(null);
  const [lapLoading, setLapLoading] = useState(false);
  const [lapError, setLapError] = useState<string | null>(null);
  const [lapNotice, setLapNotice] = useState<string | null>(null);
  const lapSnapshotRef = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setResults(null);
    setSelectedEntry(null);
    setLaps(null);
    setLapCursor(null);
    lapSnapshotRef.current = null;
    setError(null);
    setLoading(true);

    async function loadSession() {
      try {
        const nextDetail = await getSessionDetail(
          session.id,
          controller.signal,
        );
        setDetail(nextDetail);
        if (nextDetail.snapshot.data_available) {
          setResults(
            await getSessionResults(session.id, controller.signal),
          );
        }
      } catch (nextError) {
        if (
          nextError instanceof DOMException &&
          nextError.name === "AbortError"
        ) {
          return;
        }
        setError(safeErrorMessage(nextError));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void loadSession();
    return () => controller.abort();
  }, [session.id]);

  useEffect(() => {
    if (!selectedEntry) {
      return;
    }
    const controller = new AbortController();
    const requestedCursor = lapCursor;
    setLapLoading(true);
    setLapError(null);

    getSessionLaps(
      session.id,
      selectedEntry.session_entry_id,
      {
        after_lap: requestedCursor ?? undefined,
        limit: LAP_PAGE_SIZE,
      },
      controller.signal,
    )
      .then((nextPage) => {
        if (
          requestedCursor !== null &&
          lapSnapshotRef.current !== nextPage.snapshot.completed_at
        ) {
          setLapNotice(
            "The archive snapshot changed, so lap pagination restarted from the latest data.",
          );
          setLaps(null);
          setLapCursor(null);
          lapSnapshotRef.current = null;
          return;
        }
        lapSnapshotRef.current = nextPage.snapshot.completed_at;
        setLaps((current) => {
          if (requestedCursor === null || current === null) {
            return nextPage;
          }
          return {
            ...nextPage,
            items: [...current.items, ...nextPage.items],
          };
        });
      })
      .catch((nextError: unknown) => {
        if (
          nextError instanceof DOMException &&
          nextError.name === "AbortError"
        ) {
          return;
        }
        setLapError(safeErrorMessage(nextError));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLapLoading(false);
        }
      });

    return () => controller.abort();
  }, [lapCursor, selectedEntry, session.id]);

  function handleEntrySelection(entry: SessionEntryResult) {
    if (entry.session_entry_id === selectedEntry?.session_entry_id) {
      return;
    }
    setSelectedEntry(entry);
    setLaps(null);
    setLapCursor(null);
    lapSnapshotRef.current = null;
    setLapError(null);
    setLapNotice(null);
  }

  return (
    <section
      aria-labelledby="session-explorer-title"
      className="session-explorer"
      id="session-explorer"
    >
      <div className="session-explorer__topline">
        <p className="section-kicker">Session workspace</p>
        <button
          className="session-explorer__close"
          onClick={onClose}
          type="button"
        >
          Close view <span aria-hidden="true">×</span>
        </button>
      </div>

      <SessionSummary
        detail={detail}
        fallbackEvent={event}
        fallbackSession={session}
      />

      {loading ? (
        <div className="session-explorer__loading" aria-live="polite">
          <span />
          Loading session classification…
        </div>
      ) : error ? (
        <p className="inline-alert inline-alert--danger" role="alert">
          {error}
        </p>
      ) : detail ? (
        <>
          <div className="session-facts">
            <div>
              <span>Snapshot</span>
              <strong>
                {detail.snapshot.data_available ? "Available" : "Not ready"}
              </strong>
            </div>
            <div>
              <span>Entries</span>
              <strong>{detail.counts.entries}</strong>
            </div>
            <div>
              <span>Results</span>
              <strong>{detail.counts.results}</strong>
            </div>
            <div>
              <span>Laps</span>
              <strong>{detail.counts.laps.toLocaleString("en")}</strong>
            </div>
            <div>
              <span>Finalized</span>
              <strong>{formatSnapshotTime(detail.snapshot.completed_at)}</strong>
            </div>
          </div>

          {!detail.snapshot.data_available ? (
            <div className="session-explorer__empty">
              <strong>This session has no completed archive snapshot yet.</strong>
              <p>
                Its calendar metadata remains visible. Results and laps will
                become available after ingestion completes.
              </p>
            </div>
          ) : results && results.items.length > 0 ? (
            <>
              <div className="session-subheading">
                <div>
                  <p className="section-kicker">Classification</p>
                  <h3>Results & entries</h3>
                </div>
                <span>{results.items.length} participants</span>
              </div>
              <ResultsTable
                items={results.items}
                onSelectEntry={handleEntrySelection}
                selectedEntryId={selectedEntry?.session_entry_id ?? null}
              />
            </>
          ) : (
            <div className="session-explorer__empty">
              <strong>The snapshot contains no session entries.</strong>
            </div>
          )}

          {selectedEntry ? (
            <section
              aria-labelledby="lap-workspace-title"
              className="lap-workspace"
            >
              <div className="session-subheading">
                <div>
                  <p className="section-kicker">Lap summaries</p>
                  <h3 id="lap-workspace-title">{selectedEntry.display_name}</h3>
                  <p>
                    {[selectedEntry.team_name, selectedEntry.racing_number
                      ? `Car ${selectedEntry.racing_number}`
                      : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <span>
                  {laps?.items.length ?? 0} lap
                  {(laps?.items.length ?? 0) === 1 ? "" : "s"} loaded
                </span>
              </div>

              {lapNotice ? (
                <p className="inline-alert inline-alert--success" role="status">
                  {lapNotice}
                </p>
              ) : null}
              {lapError ? (
                <p className="inline-alert inline-alert--danger" role="alert">
                  {lapError}
                </p>
              ) : null}

              {laps ? (
                <>
                  <LapPaceChart laps={laps.items} />
                  {laps.items.length > 0 ? (
                    <LapTable laps={laps.items} />
                  ) : (
                    <div className="session-explorer__empty">
                      <strong>No lap summaries were stored for this entry.</strong>
                    </div>
                  )}
                  {laps.page.has_more ? (
                    <button
                      className="load-more-laps"
                      disabled={lapLoading}
                      onClick={() =>
                        setLapCursor(laps.page.next_after_lap)
                      }
                      type="button"
                    >
                      {lapLoading ? "Loading laps…" : "Load next 50 laps"}
                    </button>
                  ) : (
                    <p className="lap-workspace__complete">
                      End of stored lap summaries
                    </p>
                  )}
                </>
              ) : lapLoading ? (
                <div className="session-explorer__loading" aria-live="polite">
                  <span />
                  Loading lap summaries…
                </div>
              ) : null}
            </section>
          ) : detail.snapshot.data_available ? (
            <p className="session-explorer__hint">
              Choose a participant to inspect lap pace, stints, compounds, and
              data-quality markers.
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
