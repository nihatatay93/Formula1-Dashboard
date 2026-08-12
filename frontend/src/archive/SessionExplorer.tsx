import { useEffect, useMemo, useRef, useState } from "react";

import { getSessionDetail, getSessionLaps, getSessionResults } from "../api";
import type {
  LapSummary,
  LapSummaryResponse,
  SeasonEvent,
  SeasonSession,
  SessionDetail,
  SessionEntryResult,
  SessionResults,
} from "../contracts";
import LapPaceChart from "./LapPaceChart";
import LapTable from "./LapTable";
import LapTelemetryPanel from "./LapTelemetryPanel";
import PaceAnalysisPanel, {
  DEFAULT_ANALYSIS_SLOTS,
  MAX_ANALYSIS_PARTICIPANTS,
} from "./PaceAnalysisPanel";
import ResultsTable from "./ResultsTable";
import SessionSummary from "./SessionSummary";
import type { AnalysisSelection } from "./lapAnalysis";
import { isLapSelectable } from "./lapAnalysis";
import { formatSnapshotTime, safeErrorMessage } from "./sessionFormat";

/**
 * One session's workspace: results, a participant's laps, and the ephemeral
 * pace comparison built from laps a reader selects.
 *
 * This owns the session's data lifecycle and the selection state; every panel
 * below it is presentational. Selections are held against the snapshot they
 * were made from, because an archive re-ingestion makes earlier laps
 * incomparable rather than merely stale.
 */

const LAP_PAGE_SIZE = 50;

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
  const [analysisNotice, setAnalysisNotice] = useState<string | null>(null);
  const [analysisSelections, setAnalysisSelections] = useState<
    AnalysisSelection[]
  >([]);
  const [comparisonSlots, setComparisonSlots] = useState(
    DEFAULT_ANALYSIS_SLOTS,
  );
  // One lap at a time: telemetry is the expensive upstream call, so it is
  // fetched only for the lap a reader has explicitly opened.
  const [telemetryLap, setTelemetryLap] = useState<number | null>(null);
  const lapSnapshotRef = useRef<string | null>(null);
  const analysisSelectionsRef = useRef<AnalysisSelection[]>([]);

  useEffect(() => {
    analysisSelectionsRef.current = analysisSelections;
  }, [analysisSelections]);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setResults(null);
    setSelectedEntry(null);
    setLaps(null);
    setLapCursor(null);
    lapSnapshotRef.current = null;
    setAnalysisNotice(null);
    setAnalysisSelections([]);
    setComparisonSlots(DEFAULT_ANALYSIS_SLOTS);
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
        const incompatibleSelection = analysisSelectionsRef.current.some(
          (selection) =>
            selection.sessionId !== session.id ||
            selection.snapshotCompletedAt !== nextPage.snapshot.completed_at,
        );
        if (incompatibleSelection) {
          setAnalysisSelections([]);
          setAnalysisNotice(
            "The archive snapshot changed, so the manual pace selection was cleared.",
          );
        }
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
    // The open lap belongs to the entry being replaced.
    setTelemetryLap(null);
  }

  function handleTelemetrySelection(lap: LapSummary) {
    setTelemetryLap((current) =>
      current === lap.lap_number ? null : lap.lap_number,
    );
  }

  function handleLapSelection(lap: LapSummary) {
    if (!selectedEntry || !isLapSelectable(lap) || !lapSnapshotRef.current) {
      return;
    }
    setAnalysisSelections((current) => {
      const existingIndex = current.findIndex(
        (selection) =>
          selection.entry.session_entry_id === selectedEntry.session_entry_id,
      );
      if (existingIndex === -1 && current.length >= comparisonSlots) {
        setAnalysisNotice(
          comparisonSlots >= MAX_ANALYSIS_PARTICIPANTS
            ? `All ${MAX_ANALYSIS_PARTICIPANTS} comparison slots are in use. Clear one selection before adding another.`
            : `${comparisonSlots} participants are already in the comparison. Add a slot or clear one selection before adding another.`,
        );
        return current;
      }

      const existing =
        existingIndex === -1
          ? {
              entry: selectedEntry,
              laps: [],
              sessionId: session.id,
              snapshotCompletedAt: lapSnapshotRef.current as string,
            }
          : current[existingIndex];
      const isSelected = existing.laps.some(
        (selectedLap) => selectedLap.lap_number === lap.lap_number,
      );
      const nextLaps = isSelected
        ? existing.laps.filter(
            (selectedLap) => selectedLap.lap_number !== lap.lap_number,
          )
        : [...existing.laps, lap].sort(
            (left, right) => left.lap_number - right.lap_number,
          );
      setAnalysisNotice(null);
      if (nextLaps.length === 0) {
        return existingIndex === -1
          ? current
          : current.filter((_, index) => index !== existingIndex);
      }
      const nextSelection = { ...existing, laps: nextLaps };
      if (existingIndex === -1) {
        return [...current, nextSelection];
      }
      return current.map((selection, index) =>
        index === existingIndex ? nextSelection : selection,
      );
    });
  }

  function handleClearSelection(sessionEntryId: string) {
    setAnalysisSelections((current) =>
      current.filter(
        (selection) =>
          selection.entry.session_entry_id !== sessionEntryId,
      ),
    );
    setAnalysisNotice(null);
  }

  const selectedLapNumbers = useMemo(
    () =>
      new Set(
        analysisSelections.find(
          (selection) =>
            selection.entry.session_entry_id ===
            selectedEntry?.session_entry_id,
        )?.laps.map((lap) => lap.lap_number) ?? [],
      ),
    [analysisSelections, selectedEntry?.session_entry_id],
  );
  const selectionParticipantLimitReached =
    analysisSelections.length >= comparisonSlots &&
    !analysisSelections.some(
      (selection) =>
        selection.entry.session_entry_id === selectedEntry?.session_entry_id,
    );

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

          {detail.snapshot.data_available ? (
            <>
              {analysisNotice ? (
                <p className="inline-alert inline-alert--success" role="status">
                  {analysisNotice}
                </p>
              ) : null}
              <PaceAnalysisPanel
                maxSlots={MAX_ANALYSIS_PARTICIPANTS}
                onAddSlot={() =>
                  setComparisonSlots((current) =>
                    Math.min(current + 1, MAX_ANALYSIS_PARTICIPANTS),
                  )
                }
                onClearSelection={handleClearSelection}
                onRemoveSlot={() =>
                  setComparisonSlots((current) =>
                    Math.max(current - 1, DEFAULT_ANALYSIS_SLOTS),
                  )
                }
                selections={analysisSelections}
                slots={comparisonSlots}
              />
            </>
          ) : null}

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
                    <LapTable
                      activeTelemetryLap={telemetryLap}
                      laps={laps.items}
                      onToggleLap={handleLapSelection}
                      onViewTelemetry={handleTelemetrySelection}
                      selectedLapNumbers={selectedLapNumbers}
                      selectionDisabled={selectionParticipantLimitReached}
                    />
                  ) : (
                    <div className="session-explorer__empty">
                      <strong>No lap summaries were stored for this entry.</strong>
                    </div>
                  )}
                  {telemetryLap !== null ? (
                    <LapTelemetryPanel
                      driverName={selectedEntry.display_name}
                      // Remounting per lap restarts the request cleanly rather
                      // than racing the previous lap's poll.
                      key={`${selectedEntry.session_entry_id}-${telemetryLap}`}
                      lapNumber={telemetryLap}
                      onClose={() => setTelemetryLap(null)}
                      sessionEntryId={selectedEntry.session_entry_id}
                      sessionId={session.id}
                    />
                  ) : null}
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
