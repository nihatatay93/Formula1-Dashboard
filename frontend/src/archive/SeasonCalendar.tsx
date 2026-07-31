import type { SeasonOverview } from "../contracts";
import EventCard from "./EventCard";

/** Every round of one season, with its sessions. */
export default function SeasonCalendar({
  onSelectSession,
  season,
  selectedSessionId,
  year,
}: {
  onSelectSession: (sessionId: string) => void;
  season: SeasonOverview;
  selectedSessionId: string | null;
  year: number;
}) {
  return (
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
          {season.events.length} round{season.events.length === 1 ? "" : "s"}
        </span>
      </div>

      {season.events.length > 0 ? (
        <div className="event-grid">
          {season.events.map((event) => (
            <EventCard
              event={event}
              key={event.id}
              onSelectSession={(session) => onSelectSession(session.id)}
              selectedSessionId={selectedSessionId}
            />
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <span className="empty-state__number">{year}</span>
          <div>
            <h3>No calendar coverage yet</h3>
            <p>
              Run the season check to discover events and queue archive sessions
              that are ready for ingestion.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
