import type { IngestionStatus, SeasonEvent, SeasonSession } from "../contracts";
import StatusPill from "../shared/StatusPill";
import { formatDate, humanize } from "../shared/format";

/** How a session's archive state should read, collapsing four sources into one. */
export function sessionDisplayStatus(
  session: SeasonSession,
): IngestionStatus | "available" | "not_due" {
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

export default function EventCard({
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