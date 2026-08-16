import { useCallback, useEffect, useMemo, useState } from "react";

import {
  checkApiReadiness,
  ensureSeasonBackfill,
  getBackfillJob,
  getFastF1RequestBudget,
  getSeasonOverview,
  signOut,
} from "./api";
import type {
  BackfillJob,
  FastF1RequestBudget,
  SeasonOverview,
} from "./contracts";
import { useAccessControlled } from "./AuthGate";
import DashboardRail, { isArchiveView } from "./DashboardRail";
import type { DashboardView } from "./DashboardRail";
import Home from "./Home";
import ArchiveOverview from "./archive/ArchiveOverview";
import SeasonCalendar from "./archive/SeasonCalendar";
import StandingsView from "./archive/StandingsView";
import SessionExplorer from "./archive/SessionExplorer";
import LiveTiming from "./live/LiveTiming";
import { errorMessage, formatDateTime } from "./shared/format";

/**
 * Application shell.
 *
 * This owns navigation and the archive's data lifecycle, and nothing else: each
 * view renders itself. Live timing is deliberately not fed from here — it
 * fetches its own state, because it has to work when the archive is unreachable
 * and must never wait on season coverage.
 */

const FIRST_SUPPORTED_SEASON = 2018;
const JOB_POLL_INTERVAL_MS = 2_000;
const currentUtcYear = new Date().getUTCFullYear();

type ApiState = "checking" | "ready" | "unavailable";

const VIEW_HEADINGS: Record<
  Exclude<DashboardView, "session">,
  { description: string; title: (year: number) => string }
> = {
  home: {
    description:
      "Everything runs on this machine — nothing is sent anywhere, and no account is needed to read the archive.",
    title: () => "Formula One data platform",
  },
  overview: {
    description:
      "Coverage, synchronization health, and background ingestion at a glance.",
    title: (year) => `${year} coverage & ingestion`,
  },
  calendar: {
    description:
      "Open any event and session without leaving the season archive.",
    title: (year) => `${year} season sessions`,
  },
  standings: {
    description:
      "Both championships, computed from the sessions this archive holds.",
    title: (year) => `${year} championship`,
  },
  live: {
    description:
      "Unconfirmed live frames, served outside the archive and never stored as sporting data.",
    title: () => "Live timing",
  },
};

function App() {
  const [apiState, setApiState] = useState<ApiState>("checking");
  const [selectedYear, setSelectedYear] = useState(currentUtcYear);
  const [season, setSeason] = useState<SeasonOverview | null>(null);
  const [seasonLoading, setSeasonLoading] = useState(true);
  const [seasonError, setSeasonError] = useState<string | null>(null);
  const [commandPending, setCommandPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [job, setJob] = useState<BackfillJob | null>(null);
  const [pollingJobId, setPollingJobId] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [requestBudget, setRequestBudget] =
    useState<FastF1RequestBudget | null>(null);
  const [requestBudgetError, setRequestBudgetError] = useState<string | null>(
    null,
  );
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );
  const [activeView, setActiveView] = useState<DashboardView>("home");
  const [signOutError, setSignOutError] = useState<string | null>(null);
  // Read from the gate rather than asked for again: it already established
  // this, and a second answer could disagree with the one that let us render.
  const signedIn = useAccessControlled();
  const [now, setNow] = useState(() => Date.now());

  const supportedYears = useMemo(
    () =>
      Array.from(
        { length: currentUtcYear - FIRST_SUPPORTED_SEASON + 1 },
        (_, index) => currentUtcYear - index,
      ),
    [],
  );

  const refreshSeason = useCallback(
    async (year: number, signal?: AbortSignal) => {
      const overview = await getSeasonOverview(year, signal);
      setSeason(overview);
      setPollingJobId(overview.active_job?.id ?? null);
      return overview;
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();

    async function check() {
      try {
        setApiState("checking");
        const ready = await checkApiReadiness(controller.signal);
        setApiState(ready ? "ready" : "unavailable");
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setApiState("unavailable");
      }
    }

    void check();
    const timer = window.setInterval(() => void check(), 15_000);

    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  async function handleSignOut() {
    setSignOutError(null);
    try {
      await signOut();
    } catch {
      // Reloading regardless would look like a successful sign-out while the
      // cookie is still valid, and the reader would believe they had left.
      setSignOutError(
        "Sign-out did not reach the backend, so this session is still active. Check your connection and try again.",
      );
      return;
    }
    // Only once the session is really gone: a reload is the honest way to drop
    // every poller and cached view.
    window.location.reload();
  }

  useEffect(() => {
    if (apiState !== "ready") {
      return;
    }
    const controller = new AbortController();
    let timer: number | undefined;

    async function pollBudget() {
      try {
        setRequestBudget(await getFastF1RequestBudget(controller.signal));
        setRequestBudgetError(null);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setRequestBudgetError(errorMessage(error));
      }
      timer = window.setTimeout(() => void pollBudget(), 5_000);
    }

    void pollBudget();
    return () => {
      controller.abort();
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [apiState]);

  useEffect(() => {
    const controller = new AbortController();

    setSeasonLoading(true);
    setSeasonError(null);
    setNotice(null);
    setSeason(null);
    setJob(null);
    setJobError(null);
    setPollingJobId(null);
    setSelectedSessionId(null);
    // A season change invalidates any open session, but it must not throw the
    // reader out of home or live, which own no season at all.
    setActiveView((current) => (current === "session" ? "calendar" : current));

    refreshSeason(selectedYear, controller.signal)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setSeasonError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSeasonLoading(false);
        }
      });

    return () => controller.abort();
  }, [refreshSeason, selectedYear]);

  useEffect(() => {
    if (!pollingJobId) {
      return;
    }

    const controller = new AbortController();
    let timer: number | undefined;

    async function pollJob() {
      try {
        const nextJob = await getBackfillJob(
          pollingJobId as string,
          controller.signal,
        );
        setJob(nextJob);
        setJobError(null);

        if (nextJob.status === "pending" || nextJob.status === "running") {
          timer = window.setTimeout(() => void pollJob(), JOB_POLL_INTERVAL_MS);
          return;
        }

        setPollingJobId(null);
        await refreshSeason(nextJob.season_year, controller.signal);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setJobError(errorMessage(error));
      }
    }

    void pollJob();

    return () => {
      controller.abort();
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [pollingJobId, refreshSeason]);

  async function handleBackfill() {
    const controller = new AbortController();
    setCommandPending(true);
    setSeasonError(null);
    setNotice(null);
    setActiveView("overview");

    try {
      const result = await ensureSeasonBackfill(selectedYear, controller.signal);
      const deferredEvents = result.deferred_future_events;
      const deferredNotice =
        deferredEvents.length > 0
          ? ` ${deferredEvents.length} future event${deferredEvents.length === 1 ? "" : "s"}, starting with ${deferredEvents[0].event_name}, will be added when exact session timing is published.`
          : "";
      const messages = {
        coverage_refreshed: `Available calendar coverage refreshed. No session work is due yet.${deferredNotice}`,
        job_created: `${result.newly_queued_session_count} session${result.newly_queued_session_count === 1 ? "" : "s"} queued for ingestion.${deferredNotice}`,
        job_reused: `The existing season job is already handling eligible sessions.${deferredNotice}`,
        no_action: `This season is current. No backfill work is needed.${deferredNotice}`,
      };
      setNotice(messages[result.action]);

      if (result.job) {
        setJob(null);
        setJobError(null);
        setPollingJobId(result.job.id);
      }

      await refreshSeason(selectedYear);
    } catch (error) {
      setSeasonError(errorMessage(error));
    } finally {
      setCommandPending(false);
    }
  }

  async function handleRefresh() {
    setSeasonLoading(true);
    setSeasonError(null);
    try {
      await refreshSeason(selectedYear);
    } catch (error) {
      setSeasonError(errorMessage(error));
    } finally {
      setSeasonLoading(false);
    }
  }

  const selectedSession = useMemo(() => {
    if (!season || !selectedSessionId) {
      return null;
    }
    for (const event of season.events) {
      const session = event.sessions.find(
        (candidate) => candidate.id === selectedSessionId,
      );
      if (session) {
        return { event, session };
      }
    }
    return null;
  }, [season, selectedSessionId]);

  function handleSessionSelection(sessionId: string) {
    setSelectedSessionId(sessionId);
    setActiveView("session");
  }

  const heading =
    activeView === "session"
      ? {
          description: selectedSession
            ? `${selectedSession.session.session_name} · round ${selectedSession.event.round_number}`
            : "Choose a session from the season calendar to inspect its archive.",
          title: selectedSession
            ? `${selectedSession.event.event_name} workspace`
            : "Session workspace",
        }
      : {
          description: VIEW_HEADINGS[activeView].description,
          title: VIEW_HEADINGS[activeView].title(selectedYear),
        };

  // Only the archive views depend on season coverage; home and live render
  // without waiting for it.
  const needsSeason = isArchiveView(activeView);

  return (
    <div className="dashboard-frame">
      <DashboardRail
        apiState={apiState}
        commandPending={commandPending}
        job={job}
        onBackfill={() => void handleBackfill()}
        onRefresh={() => void handleRefresh()}
        onSelectView={setActiveView}
        onSelectYear={setSelectedYear}
        onSignOut={() => void handleSignOut()}
        season={season}
        seasonLoading={seasonLoading}
        sessionOpen={selectedSession !== null}
        signedIn={signedIn}
        supportedYears={supportedYears}
        view={activeView}
        year={selectedYear}
      />

      <div className="dashboard-workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">
              <span>
                {needsSeason ? "Archive / Championship" : "Formula One"}
              </span>
              2018—{currentUtcYear}
            </p>
            <h1 id="dashboard-title">{heading.title}</h1>
            <p>{heading.description}</p>
          </div>
          {needsSeason ? (
            <span className="workspace-header__year" aria-hidden="true">
              {selectedYear}
            </span>
          ) : null}
        </header>

        <main className="workspace-content" aria-labelledby="dashboard-title">
          {signOutError ? (
            <div className="inline-alert inline-alert--danger" role="alert">
              <strong>Still signed in</strong>
              <span>{signOutError}</span>
            </div>
          ) : null}

          {activeView === "home" ? (
            <Home
              onNavigate={setActiveView}
              season={season}
              seasonLoading={seasonLoading}
              year={selectedYear}
            />
          ) : null}

          {/* Live timing is a separate path: it never waits on season coverage
              and never reads archive state. */}
          {activeView === "live" ? (
            <div className="workspace-view" data-view="live">
              <LiveTiming />
            </div>
          ) : null}

          {needsSeason && seasonError ? (
            <div className="inline-alert inline-alert--danger" role="alert">
              <strong>Dashboard unavailable</strong>
              <span>{seasonError}</span>
            </div>
          ) : null}

          {needsSeason && notice ? (
            <div className="inline-alert inline-alert--success" role="status">
              <strong>Season updated</strong>
              <span>{notice}</span>
            </div>
          ) : null}

          {!needsSeason ? null : seasonLoading ? (
            <section className="dashboard-loading" aria-live="polite">
              <span />
              <p>Loading {selectedYear} season coverage…</p>
            </section>
          ) : season ? (
            <>
              {season.deferred_future_events.length > 0 &&
              activeView !== "session" ? (
                <div className="inline-alert inline-alert--warning" role="status">
                  <strong>Future calendar awaiting exact timing</strong>
                  <span>
                    {season.deferred_future_events.length} future event
                    {season.deferred_future_events.length === 1 ? "" : "s"},
                    starting with{" "}
                    {season.deferred_future_events[0].event_name} on{" "}
                    {formatDateTime(
                      season.deferred_future_events[0].scheduled_start_at,
                    )}, will appear when FastF1 publishes exact session
                    boundaries.
                  </span>
                </div>
              ) : null}

              {activeView === "overview" ? (
                <ArchiveOverview
                  budget={requestBudget}
                  budgetError={requestBudgetError}
                  job={job}
                  jobError={jobError}
                  now={now}
                  season={season}
                />
              ) : null}

              {activeView === "standings" ? (
                <StandingsView year={selectedYear} />
              ) : null}

              {activeView === "calendar" ? (
                <SeasonCalendar
                  onSelectSession={handleSessionSelection}
                  season={season}
                  selectedSessionId={selectedSessionId}
                  year={selectedYear}
                />
              ) : null}

              {activeView === "session" ? (
                <div className="workspace-view" data-view="session">
                  {selectedSession ? (
                    <SessionExplorer
                      event={selectedSession.event}
                      key={`${selectedSession.session.id}:${selectedSession.session.ingestion?.completed_at ?? "unavailable"}`}
                      onClose={() => {
                        setSelectedSessionId(null);
                        setActiveView("calendar");
                      }}
                      session={selectedSession.session}
                    />
                  ) : (
                    <div className="empty-state empty-state--workspace">
                      <span className="empty-state__number">—</span>
                      <div>
                        <h3>Select a session first</h3>
                        <p>
                          Open the season calendar, then choose a practice,
                          qualifying, sprint, or race session.
                        </p>
                        <button
                          className="secondary-action"
                          onClick={() => setActiveView("calendar")}
                          type="button"
                        >
                          Browse season sessions
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : null}
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}

export default App;
