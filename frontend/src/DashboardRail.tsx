import type { BackfillJob, SeasonOverview } from "./contracts";
import SeasonSelect from "./shared/SeasonSelect";
import StatusPill from "./shared/StatusPill";

/**
 * The navigation rail.
 *
 * Sections are grouped by path rather than listed as one flat run, because the
 * archive and live timing are separate products: nothing in the archive group
 * waits on the feed, and nothing in the live group reads archive state. The
 * season controls sit inside the archive group for the same reason — a season
 * is meaningless to live timing, and offering it there implies otherwise.
 */

export type DashboardView =
  | "home"
  | "overview"
  | "calendar"
  | "standings"
  | "session"
  | "race-pace"
  | "head-to-head"
  | "live";

/** Which group a view belongs to, so the season controls know when to show. */
export function isArchiveView(view: DashboardView): boolean {
  return (
    view === "overview" ||
    view === "calendar" ||
    view === "standings" ||
    view === "session" ||
    view === "race-pace" ||
    view === "head-to-head"
  );
}

function RailButton({
  active,
  badge,
  busy,
  disabled,
  label,
  onSelect,
}: {
  active: boolean;
  badge?: string;
  busy?: boolean;
  disabled?: boolean;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      aria-current={active ? "page" : undefined}
      className={active ? "rail-nav__active" : ""}
      disabled={disabled}
      onClick={onSelect}
      type="button"
    >
      <strong>{label}</strong>
      {badge ? <small>{badge}</small> : null}
      {busy ? <i aria-label="Active ingestion job" /> : null}
    </button>
  );
}

export default function DashboardRail({
  apiState,
  commandPending,
  job,
  onBackfill,
  onRefresh,
  onSelectView,
  onSelectYear,
  onSignOut,
  season,
  seasonLoading,
  sessionOpen,
  signedIn,
  supportedYears,
  view,
  year,
}: {
  apiState: string;
  commandPending: boolean;
  job: BackfillJob | null;
  onBackfill: () => void;
  onRefresh: () => void;
  onSelectView: (view: DashboardView) => void;
  onSelectYear: (year: number) => void;
  onSignOut: () => void;
  season: SeasonOverview | null;
  seasonLoading: boolean;
  sessionOpen: boolean;
  /** True only when a session actually exists to be ended. */
  signedIn: boolean;
  supportedYears: number[];
  view: DashboardView;
  year: number;
}) {
  const counts = season?.counts;
  const jobRunning = job?.status === "pending" || job?.status === "running";
  const showSeasonControls = isArchiveView(view);

  return (
    <aside className="dashboard-rail">
      <div className="rail-topline">
        <a className="brand" href="/" aria-label="Formula1 Dashboard home">
          <span className="brand__mark" aria-hidden="true">
            F<span>1</span>
          </span>
          <span>
            Formula One
            <small>Data platform</small>
          </span>
        </a>
        <div className={`api-status api-status--${apiState}`} aria-live="polite">
          <span aria-hidden="true" />
          <span className="api-status__label">System</span>
          API {apiState}
        </div>
      </div>

      <nav className="rail-nav" aria-label="Dashboard sections">
        <RailButton
          active={view === "home"}
          label="Home"
          onSelect={() => onSelectView("home")}
        />

        <p className="rail-nav__group" id="rail-group-archive">
          Archive
        </p>
        <div role="group" aria-labelledby="rail-group-archive">
          <RailButton
            active={view === "calendar"}
            badge={String(season?.events.length ?? 0)}
            label="Season sessions"
            onSelect={() => onSelectView("calendar")}
          />
          <RailButton
            active={view === "standings"}
            label="Standings"
            onSelect={() => onSelectView("standings")}
          />
          <RailButton
            active={view === "session"}
            badge={sessionOpen ? "Open" : "—"}
            disabled={!sessionOpen}
            label="Session workspace"
            onSelect={() => onSelectView("session")}
          />
          <RailButton
            active={view === "head-to-head"}
            label="Head to head"
            onSelect={() => onSelectView("head-to-head")}
          />
          <RailButton
            active={view === "race-pace"}
            disabled={!sessionOpen}
            label="Race analysis"
            onSelect={() => onSelectView("race-pace")}
          />
          <RailButton
            active={view === "overview"}
            busy={jobRunning}
            label="Coverage"
            onSelect={() => onSelectView("overview")}
          />
        </div>

        <p className="rail-nav__group" id="rail-group-live">
          Live
        </p>
        <div role="group" aria-labelledby="rail-group-live">
          <RailButton
            active={view === "live"}
            badge="Beta"
            label="Live timing"
            onSelect={() => onSelectView("live")}
          />
        </div>
      </nav>

      {showSeasonControls ? (
        <aside className="season-control" aria-label="Season controls">
          <label htmlFor="season-select" id="season-select-label">
            Championship season
          </label>
          <SeasonSelect
            disabled={commandPending || seasonLoading}
            id="season-select"
            labelId="season-select-label"
            onChange={onSelectYear}
            options={supportedYears}
            value={year}
          />
          <button
            className="primary-action"
            disabled={commandPending || seasonLoading || apiState === "unavailable"}
            onClick={onBackfill}
            type="button"
          >
            {commandPending ? "Checking calendar…" : "Check & sync season"}
            <span aria-hidden="true">↗</span>
          </button>
          <button
            className="text-action"
            disabled={seasonLoading}
            onClick={onRefresh}
            type="button"
          >
            Refresh data
          </button>
        </aside>
      ) : null}

      {showSeasonControls && season ? (
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

      {/* Absent on a deployment that requires no sign-in: there would be no
          session to end, and offering one would imply otherwise. Kept out of
          the footnote below because that is hidden on narrow screens, which
          would leave a phone with no way to sign out. */}
      {signedIn ? (
        <div className="rail-account">
          <button className="text-action" onClick={onSignOut} type="button">
            Sign out
          </button>
        </div>
      ) : null}

      <p className="rail-footnote">
        FastF1 · PostgreSQL
        <span>Session-safe local ingestion</span>
      </p>
    </aside>
  );
}
