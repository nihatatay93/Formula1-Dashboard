import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiClientError,
  checkApiReadiness,
  ensureSeasonBackfill,
  getBackfillJob,
  getFastF1RequestBudget,
  getSeasonOverview,
} from "./api";
import type {
  BackfillJob,
  BackfillJobSession,
  FastF1RequestBudget,
  IngestionStatus,
  SeasonEvent,
  SeasonOverview,
  SeasonSession,
  SeasonStatus,
} from "./contracts";
import LiveTiming from "./LiveTiming";
import SessionExplorer from "./SessionExplorer";

const FIRST_SUPPORTED_SEASON = 2018;
const JOB_POLL_INTERVAL_MS = 2_000;
const currentUtcYear = new Date().getUTCFullYear();

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

type ApiState = "checking" | "ready" | "unavailable";
type DashboardView = "overview" | "calendar" | "session" | "live";

function humanize(value: string): string {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatDate(value: string | null): string {
  return value ? shortDateFormatter.format(new Date(value)) : "TBC";
}

function formatDateTime(value: string | null): string {
  return value ? dateTimeFormatter.format(new Date(value)) : "Not available";
}

function formatCountdown(value: string | null, now: number): string {
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

function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "The dashboard could not reach the backend. Check the local stack and retry.";
}

function statusTone(
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

function sessionDisplayStatus(session: SeasonSession): IngestionStatus | "available" | "not_due" {
  if (session.data_available) {
    return "available";
  }
  if (session.ingestion) {
    return session.ingestion.status;
  }
  if (!session.archive_eligibility.eligible) {
    return "not_due";
  }
  return "pending";
}

function StatusPill({
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

function ProgressTrack({
  completed,
  failed,
  pending,
  running,
  total,
}: {
  completed: number;
  failed: number;
  pending: number;
  running: number;
  total: number;
}) {
  const segments = [
    { className: "progress-track__completed", label: "Completed", value: completed },
    { className: "progress-track__running", label: "Running", value: running },
    { className: "progress-track__pending", label: "Pending", value: pending },
    { className: "progress-track__failed", label: "Failed", value: failed },
  ];
  const safeTotal = Math.max(total, 1);

  return (
    <>
      <div
        className={`progress-track${total === 0 ? " progress-track--empty" : ""}`}
        aria-label={
          total === 0
            ? "No sessions discovered"
            : `${completed} of ${total} sessions completed`
        }
        role="img"
      >
        {segments.map((segment) =>
          segment.value > 0 ? (
            <span
              className={segment.className}
              key={segment.label}
              style={{ width: `${(segment.value / safeTotal) * 100}%` }}
              title={`${segment.label}: ${segment.value}`}
            />
          ) : null,
        )}
      </div>
      <div className="progress-legend" aria-hidden="true">
        {segments.map((segment) => (
          <span key={segment.label}>
            <i className={segment.className} />
            {segment.value} {segment.label.toLowerCase()}
          </span>
        ))}
      </div>
    </>
  );
}

/**
 * Listbox-backed season picker. The native <select> rendered an OS-styled
 * popup that could not be themed with the rest of the dashboard, so this
 * follows the ARIA combobox pattern instead: focus stays on the trigger and
 * the active option is tracked with aria-activedescendant.
 */
function SeasonSelect({
  disabled,
  id,
  labelId,
  onChange,
  options,
  value,
}: {
  disabled: boolean;
  id: string;
  labelId: string;
  onChange: (year: number) => void;
  options: number[];
  value: number;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() =>
    Math.max(0, options.indexOf(value)),
  );
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    return () =>
      document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  function commit(index: number) {
    const year = options[index];
    if (year !== undefined) {
      onChange(year);
    }
    setOpen(false);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (disabled) {
      return;
    }
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        const step = event.key === "ArrowDown" ? 1 : -1;
        setActiveIndex((current) =>
          Math.min(Math.max(current + step, 0), options.length - 1),
        );
        return;
      }
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        return;
      case "End":
        event.preventDefault();
        setActiveIndex(options.length - 1);
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        if (open) {
          commit(activeIndex);
        } else {
          setOpen(true);
        }
        return;
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        return;
      default:
    }
  }

  return (
    <div
      className={`season-select${open ? " season-select--open" : ""}`}
      ref={containerRef}
    >
      <button
        aria-activedescendant={open ? `${id}-option-${activeIndex}` : undefined}
        aria-controls={`${id}-listbox`}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-labelledby={`${labelId} ${id}`}
        className="season-select__trigger"
        disabled={disabled}
        id={id}
        onClick={() => {
          setActiveIndex(Math.max(0, options.indexOf(value)));
          setOpen((current) => !current);
        }}
        onKeyDown={handleKeyDown}
        role="combobox"
        type="button"
      >
        <span>{value}</span>
        <span aria-hidden="true">⌄</span>
      </button>

      {open ? (
        <ul
          aria-labelledby={labelId}
          className="season-select__list"
          id={`${id}-listbox`}
          role="listbox"
        >
          {options.map((year, index) => (
            <li
              aria-selected={year === value}
              className={
                index === activeIndex ? "season-select__option--active" : undefined
              }
              id={`${id}-option-${index}`}
              key={year}
              onClick={() => commit(index)}
              onPointerMove={() => setActiveIndex(index)}
              role="option"
            >
              {year}
              {year === value ? <span aria-hidden="true">✓</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function MetricCard({
  detail,
  label,
  value,
}: {
  detail: string;
  label: string;
  value: number;
}) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value.toLocaleString("en")}</strong>
      <p>{detail}</p>
    </article>
  );
}

function EventCard({
  event,
  onSelectSession,
  selectedSessionId,
}: {
  event: SeasonEvent;
  onSelectSession: (session: SeasonSession) => void;
  selectedSessionId: string | null;
}) {
  const availableCount = event.sessions.filter(
    (session) => session.data_available,
  ).length;

  return (
    <article className="event-card">
      <header className="event-card__header">
        <span className="round-number">
          <small>Round</small>
          {String(event.round_number).padStart(2, "0")}
        </span>
        <div>
          <p className="event-card__location">
            {[event.location, event.country].filter(Boolean).join(" · ") || "Location TBC"}
          </p>
          <h3>{event.event_name}</h3>
        </div>
        <div className="event-card__coverage">
          <strong>
            {availableCount}/{event.sessions.length}
          </strong>
          <span>sessions ready</span>
        </div>
      </header>

      <div className="session-list">
        {event.sessions.map((session) => (
          <button
            aria-pressed={selectedSessionId === session.id}
            className={`session-row${
              selectedSessionId === session.id
                ? " session-row--selected"
                : ""
            }`}
            key={session.id}
            onClick={() => onSelectSession(session)}
            type="button"
          >
            <span className="session-row__date">
              {formatDate(session.scheduled_start_at)}
            </span>
            <div className="session-row__name">
              <strong>{session.session_name}</strong>
              <span>
                {session.ingestion
                  ? `${session.ingestion.record_state} · attempt ${session.ingestion.attempt_count}`
                  : humanize(session.archive_eligibility.reason)}
              </span>
            </div>
            <StatusPill status={sessionDisplayStatus(session)} />
            <span className="session-row__open" aria-hidden="true">
              →
            </span>
          </button>
        ))}
      </div>
    </article>
  );
}

function sessionReferenceLabel(
  session: BackfillJob["execution"]["current_session"],
): string {
  return session
    ? `R${session.round_number} · ${session.event_name} — ${session.session_name}`
    : "None";
}

function RequestBudgetPanel({
  budget,
  now,
}: {
  budget: FastF1RequestBudget;
  now: number;
}) {
  const percentage = Math.min(
    100,
    (budget.observed_requests / budget.operational_ceiling) * 100,
  );
  const cooldownTarget = budget.cooldown_until ?? budget.next_capacity_at;

  return (
    <section className={`budget-panel budget-panel--${budget.status}`}>
      <div>
        <p className="section-kicker">FastF1 local safety budget</p>
        <strong>
          {budget.observed_requests}
          <span> / {budget.operational_ceiling}</span>
        </strong>
        <p>
          {budget.archive_requests} archive · {budget.schedule_requests} schedule
          {" · "}
          {budget.telemetry_requests} telemetry requests in the rolling hour
        </p>
      </div>
      <div className="budget-panel__meter">
        <span style={{ width: `${percentage}%` }} />
      </div>
      <div className="budget-panel__status">
        <span>{humanize(budget.status)}</span>
        <strong>
          {cooldownTarget
            ? `${formatCountdown(cooldownTarget, now)} until capacity`
            : `${budget.remaining_before_pause} requests available`}
        </strong>
        <small>
          Local estimate · FastF1 library threshold {budget.library_limit}
        </small>
      </div>
    </section>
  );
}

function JobSessionGroups({
  sessions,
}: {
  sessions: BackfillJobSession[];
}) {
  const groups = Array.from(
    sessions.reduce((grouped, session) => {
      const key = `${session.round_number}:${session.event_name}`;
      const group = grouped.get(key) ?? [];
      group.push(session);
      grouped.set(key, group);
      return grouped;
    }, new Map<string, BackfillJobSession[]>()),
  );

  return (
    <div className="job-session-groups">
      {groups.map(([key, group]) => {
        const first = group[0];
        const terminal = group.filter(
          (session) =>
            session.status === "completed" || session.status === "failed",
        ).length;
        const active = group.some((session) => session.status === "running");
        return (
          <details className="job-event" key={key} open={active}>
            <summary>
              <span>R{String(first.round_number).padStart(2, "0")}</span>
              <strong>{first.event_name}</strong>
              <small>
                {terminal}/{group.length} terminal
              </small>
            </summary>
            <div>
              {group.map((session) => (
                <div className="job-session-row" key={session.session_id}>
                  <div>
                    <strong>{session.session_name}</strong>
                    <span>
                      Attempt {session.attempt_count}
                      {session.next_retry_at
                        ? ` · retry ${formatDateTime(session.next_retry_at)}`
                        : ""}
                    </span>
                  </div>
                  <StatusPill status={session.status} />
                  {session.last_error ? (
                    <small>{session.last_error.message}</small>
                  ) : null}
                </div>
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function JobPanel({ job, now }: { job: BackfillJob; now: number }) {
  const completion =
    job.progress.total === 0
      ? 0
      : Math.round((job.progress.terminal / job.progress.total) * 100);

  return (
    <section
      className="job-panel"
      aria-labelledby="job-progress-title"
      aria-live="polite"
    >
      <div className="section-heading section-heading--compact">
        <div>
          <p className="section-kicker">Background ingestion</p>
          <h2 id="job-progress-title">Backfill progress</h2>
        </div>
        <div className="job-panel__summary">
          <strong>{completion}%</strong>
          <StatusPill status={job.status} />
        </div>
      </div>

      <ProgressTrack {...job.progress} />

      <div className="job-execution">
        <div className="job-execution__phase">
          <span>{humanize(job.execution.phase)}</span>
          <strong>
            {job.execution.next_action_at
              ? formatCountdown(job.execution.next_action_at, now)
              : job.execution.phase === "fetching"
                ? "Live"
                : "—"}
          </strong>
          <small>
            {job.execution.next_action_at
              ? "until next operation"
              : "execution state"}
          </small>
        </div>
        <div>
          <span>Fetching now</span>
          <strong>
            {sessionReferenceLabel(job.execution.current_session)}
          </strong>
        </div>
        <div>
          <span>Next fetch</span>
          <strong>{sessionReferenceLabel(job.execution.next_session)}</strong>
        </div>
        <div>
          <span>Last completed</span>
          <strong>
            {sessionReferenceLabel(job.execution.last_completed_session)}
          </strong>
        </div>
      </div>

      <div className="job-panel__meta">
        <span>Requested {formatDateTime(job.requested_at)}</span>
        <span>Reason: {humanize(job.request_reason)}</span>
        <span className="job-panel__id" title={job.id}>
          Job {job.id.slice(0, 8)}
        </span>
      </div>

      {job.last_error ? (
        <p className="inline-alert inline-alert--danger">
          {job.last_error.message}
        </p>
      ) : null}

      <JobSessionGroups sessions={job.sessions} />
    </section>
  );
}

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
  const [activeView, setActiveView] = useState<DashboardView>("overview");
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

  useEffect(() => {
    if (apiState !== "ready") {
      return;
    }
    const controller = new AbortController();
    let timer: number | undefined;

    async function pollBudget() {
      try {
        setRequestBudget(
          await getFastF1RequestBudget(controller.signal),
        );
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
    setActiveView("overview");

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

  const counts = season?.counts;
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
  const progressTotal = counts?.sessions ?? 0;
  const progressPending =
    (counts?.pending ?? 0) +
    Math.max(
      0,
      progressTotal -
        (counts?.pending ?? 0) -
        (counts?.running ?? 0) -
        (counts?.completed ?? 0) -
        (counts?.failed ?? 0),
    );

  function handleSessionSelection(sessionId: string) {
    setSelectedSessionId(sessionId);
    setActiveView("session");
  }

  const workspaceTitle =
    activeView === "live"
      ? "Live timing"
      : activeView === "overview"
        ? `${selectedYear} season control`
        : activeView === "calendar"
          ? `${selectedYear} season sessions`
          : selectedSession
            ? `${selectedSession.event.event_name} workspace`
            : "Session workspace";
  const workspaceDescription =
    activeView === "live"
      ? "Unconfirmed live frames, served outside the archive and never stored as sporting data."
      : activeView === "overview"
        ? "Coverage, synchronization health, and background ingestion at a glance."
        : activeView === "calendar"
          ? "Open any event and session without leaving the season archive."
          : selectedSession
            ? `${selectedSession.session.session_name} · round ${selectedSession.event.round_number}`
            : "Choose a session from the season calendar to inspect its archive.";

  return (
    <div className="dashboard-frame">
      <aside className="dashboard-rail">
        <div className="rail-topline">
          <a className="brand" href="/" aria-label="Formula1 Dashboard home">
            <span className="brand__mark" aria-hidden="true">
              F<span>1</span>
            </span>
            <span>
              Formula One
              <small>Data archive</small>
            </span>
          </a>
          <div className={`api-status api-status--${apiState}`} aria-live="polite">
            <span aria-hidden="true" />
            <span className="api-status__label">System</span>
            API {apiState}
          </div>
        </div>

        <nav className="rail-nav" aria-label="Dashboard sections">
          <button
            aria-current={activeView === "overview" ? "page" : undefined}
            className={activeView === "overview" ? "rail-nav__active" : ""}
            onClick={() => setActiveView("overview")}
            type="button"
          >
            <span>01</span>
            <strong>Overview</strong>
            {job && (job.status === "pending" || job.status === "running") ? (
              <i aria-label="Active ingestion job" />
            ) : null}
          </button>
          <button
            aria-current={activeView === "calendar" ? "page" : undefined}
            className={activeView === "calendar" ? "rail-nav__active" : ""}
            onClick={() => setActiveView("calendar")}
            type="button"
          >
            <span>02</span>
            <strong>Season sessions</strong>
            <small>{season?.events.length ?? 0}</small>
          </button>
          <button
            aria-current={activeView === "session" ? "page" : undefined}
            className={activeView === "session" ? "rail-nav__active" : ""}
            disabled={!selectedSession}
            onClick={() => setActiveView("session")}
            type="button"
          >
            <span>03</span>
            <strong>Session workspace</strong>
            <small>{selectedSession ? "Open" : "—"}</small>
          </button>
          <button
            aria-current={activeView === "live" ? "page" : undefined}
            className={activeView === "live" ? "rail-nav__active" : ""}
            onClick={() => setActiveView("live")}
            type="button"
          >
            <span>04</span>
            <strong>Live timing</strong>
            <small>Beta</small>
          </button>
        </nav>

        <aside className="season-control" aria-label="Season controls">
          <label htmlFor="season-select" id="season-select-label">
            Championship season
          </label>
          <SeasonSelect
            disabled={commandPending || seasonLoading}
            id="season-select"
            labelId="season-select-label"
            onChange={setSelectedYear}
            options={supportedYears}
            value={selectedYear}
          />
          <button
            className="primary-action"
            disabled={commandPending || seasonLoading || apiState === "unavailable"}
            onClick={() => void handleBackfill()}
            type="button"
          >
            {commandPending ? "Checking calendar…" : "Check & sync season"}
            <span aria-hidden="true">↗</span>
          </button>
          <button
            className="text-action"
            disabled={seasonLoading}
            onClick={() => void handleRefresh()}
            type="button"
          >
            Refresh data
          </button>
        </aside>

        {season ? (
          <div className="rail-season-state">
            <div>
              <span>Season state</span>
              <StatusPill status={season.status} />
            </div>
            <p>
              <strong>{counts?.data_available ?? 0}</strong>
              <span> / {counts?.sessions ?? 0} sessions ready</span>
            </p>
          </div>
        ) : null}

        <p className="rail-footnote">
          FastF1 · PostgreSQL
          <span>Session-safe local ingestion</span>
        </p>
      </aside>

      <div className="dashboard-workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">
              <span>Archive / Championship</span>
              2018—{currentUtcYear}
            </p>
            <h1 id="dashboard-title">{workspaceTitle}</h1>
            <p>{workspaceDescription}</p>
          </div>
          <span className="workspace-header__year" aria-hidden="true">
            {selectedYear}
          </span>
        </header>

        <main className="workspace-content" aria-labelledby="dashboard-title">
          {/* Live timing is a separate path: it never waits on season coverage
              and never reads archive state. */}
          {activeView === "live" ? (
            <div className="workspace-view" data-view="live">
              <LiveTiming />
            </div>
          ) : null}

          {activeView !== "live" && seasonError ? (
            <div className="inline-alert inline-alert--danger" role="alert">
              <strong>Dashboard unavailable</strong>
              <span>{seasonError}</span>
            </div>
          ) : null}

          {activeView !== "live" && notice ? (
            <div className="inline-alert inline-alert--success" role="status">
              <strong>Season updated</strong>
              <span>{notice}</span>
            </div>
          ) : null}

          {activeView === "live" ? null : seasonLoading ? (
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
                <div className="workspace-view" data-view="overview">
                  <section
                    className="overview-panel"
                    aria-labelledby="overview-title"
                  >
                    <div className="section-heading">
                      <div>
                        <p className="section-kicker">Season coverage</p>
                        <h2 id="overview-title">Archive overview</h2>
                      </div>
                      <div className="season-state">
                        <span>Season state</span>
                        <StatusPill status={season.status} />
                      </div>
                    </div>

                    <div className="metric-grid">
                      <MetricCard
                        detail="Championship rounds discovered"
                        label="Events"
                        value={counts?.events ?? 0}
                      />
                      <MetricCard
                        detail="Practice, qualifying and races"
                        label="Sessions"
                        value={counts?.sessions ?? 0}
                      />
                      <MetricCard
                        detail="Finalized archive snapshots"
                        label="Data ready"
                        value={counts?.data_available ?? 0}
                      />
                      <MetricCard
                        detail="Sessions currently due to ingest"
                        label="Archive eligible"
                        value={counts?.archive_eligible ?? 0}
                      />
                    </div>

                    <div className="season-progress">
                      <div className="season-progress__heading">
                        <strong>Session archive coverage</strong>
                        <span>
                          {counts?.data_available ?? 0} / {progressTotal} available
                        </span>
                      </div>
                      <ProgressTrack
                        completed={counts?.completed ?? 0}
                        failed={counts?.failed ?? 0}
                        pending={progressPending}
                        running={counts?.running ?? 0}
                        total={progressTotal}
                      />
                      <div className="coverage-meta">
                        <span>
                          Coverage checked:{" "}
                          {formatDateTime(season.coverage.checked_at)}
                        </span>
                        <span>
                          Valid until:{" "}
                          {formatDateTime(season.coverage.valid_until)}
                        </span>
                      </div>
                    </div>
                  </section>

                  {requestBudget ? (
                    <RequestBudgetPanel budget={requestBudget} now={now} />
                  ) : null}
                  {requestBudgetError ? (
                    <p className="inline-alert inline-alert--danger" role="alert">
                      {requestBudgetError}
                    </p>
                  ) : null}

                  {job ? <JobPanel job={job} now={now} /> : null}
                  {jobError ? (
                    <p className="inline-alert inline-alert--danger" role="alert">
                      {jobError}
                    </p>
                  ) : null}
                </div>
              ) : null}

              {activeView === "calendar" ? (
                <section
                  className="calendar-section workspace-view"
                  aria-labelledby="calendar-title"
                  data-view="calendar"
                >
                  <div className="section-heading">
                    <div>
                      <p className="section-kicker">Event by event</p>
                      <h2 id="calendar-title">Season calendar</h2>
                    </div>
                    <span className="calendar-count">
                      {season.events.length} round
                      {season.events.length === 1 ? "" : "s"}
                    </span>
                  </div>

                  {season.events.length > 0 ? (
                    <div className="event-grid">
                      {season.events.map((event) => (
                        <EventCard
                          event={event}
                          key={event.id}
                          onSelectSession={(session) =>
                            handleSessionSelection(session.id)
                          }
                          selectedSessionId={selectedSessionId}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="empty-state">
                      <span className="empty-state__number">{selectedYear}</span>
                      <div>
                        <h3>No calendar coverage yet</h3>
                        <p>
                          Run the season check to discover events and queue archive
                          sessions that are ready for ingestion.
                        </p>
                      </div>
                    </div>
                  )}
                </section>
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
                      <span className="empty-state__number">03</span>
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
