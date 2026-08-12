import type { SeasonEvent, SeasonSession, SessionDetail } from "../contracts";

export default function SessionSummary({
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