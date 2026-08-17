import type { SeasonOverview } from "./contracts";
import type { Density } from "./shared/useDensity";

/**
 * What the analysis views are looking at, in one place.
 *
 * Before this, changing the session meant leaving whichever analysis you were
 * reading, going back to the calendar and drilling in again — so comparing two
 * races meant losing your place twice. The bar keeps the scope visible and
 * changeable without navigating away, and the density control sits here
 * because it applies to every table below it.
 *
 * It renders only where scope means something. On the landing page or live
 * timing there is no season or session to choose, and a disabled control that
 * never applies is worse than none.
 */

export default function ScopeBar({
  density,
  events,
  onSelectDensity,
  onSelectSession,
  selectedSessionId,
  showSession,
  year,
}: {
  density: Density;
  events: SeasonOverview["events"] | undefined;
  onSelectDensity: (next: Density) => void;
  onSelectSession: (sessionId: string) => void;
  selectedSessionId: string | null;
  /** Session scope is meaningless to the season-wide views. */
  showSession: boolean;
  year: number;
}) {
  // Only sessions with an archived snapshot can be analysed, so offering the
  // rest would be offering a dead end.
  const available = (events ?? []).flatMap((event) =>
    event.sessions
      .filter((session) => session.ingestion?.completed_at != null)
      .map((session) => ({
        id: session.id,
        label: `R${event.round_number} · ${event.event_name} · ${session.session_name}`,
      })),
  );

  return (
    <div className="scope-bar">
      <div className="scope-bar__group">
        <span className="scope-bar__label">Season</span>
        <span className="scope-bar__value">{year}</span>
      </div>

      {showSession ? (
        <div className="scope-bar__group scope-bar__group--grow">
          <label className="scope-bar__label" htmlFor="scope-session">
            Session
          </label>
          {available.length === 0 ? (
            <span className="scope-bar__value scope-bar__value--muted">
              none archived yet
            </span>
          ) : (
            <select
              id="scope-session"
              onChange={(input) => onSelectSession(input.target.value)}
              value={selectedSessionId ?? ""}
            >
              {selectedSessionId === null ? (
                <option value="">Choose a session…</option>
              ) : null}
              {available.map((session) => (
                <option key={session.id} value={session.id}>
                  {session.label}
                </option>
              ))}
            </select>
          )}
        </div>
      ) : null}

      <div
        aria-label="Table density"
        className="scope-bar__density"
        role="group"
      >
        {(["comfortable", "compact"] as const).map((option) => (
          <button
            aria-pressed={density === option}
            className={density === option ? "is-selected" : ""}
            key={option}
            onClick={() => onSelectDensity(option)}
            type="button"
          >
            {option === "comfortable" ? "Comfortable" : "Compact"}
          </button>
        ))}
      </div>
    </div>
  );
}
