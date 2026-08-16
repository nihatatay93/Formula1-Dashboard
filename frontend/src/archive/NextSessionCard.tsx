import { useEffect, useMemo, useState } from "react";

import type { SeasonOverview, SeasonSession } from "../contracts";
import { formatDateTime } from "../shared/format";

/**
 * The next session on the calendar, counting down.
 *
 * Only sessions with an exact published start can be counted down to. Events
 * whose timing FastF1 has not published yet arrive in
 * `deferred_future_events`, and are named without a countdown rather than
 * given a fabricated one.
 */

interface UpcomingSession {
  roundNumber: number;
  eventName: string;
  session: SeasonSession;
  startsAt: Date;
}

function findNext(
  season: SeasonOverview | null,
  now: number,
): UpcomingSession | null {
  if (!season) {
    return null;
  }
  let best: UpcomingSession | null = null;
  for (const event of season.events) {
    for (const session of event.sessions) {
      if (!session.scheduled_start_at) {
        continue;
      }
      const startsAt = new Date(session.scheduled_start_at);
      if (Number.isNaN(startsAt.getTime()) || startsAt.getTime() <= now) {
        continue;
      }
      if (best === null || startsAt < best.startsAt) {
        best = {
          roundNumber: event.round_number,
          eventName: event.event_name,
          session,
          startsAt,
        };
      }
    }
  }
  return best;
}

function countdownParts(milliseconds: number): { label: string; value: string }[] {
  const total = Math.max(0, Math.floor(milliseconds / 1000));
  return [
    { label: "days", value: String(Math.floor(total / 86_400)) },
    { label: "hrs", value: String(Math.floor((total % 86_400) / 3_600)).padStart(2, "0") },
    { label: "mins", value: String(Math.floor((total % 3_600) / 60)).padStart(2, "0") },
    { label: "secs", value: String(total % 60).padStart(2, "0") },
  ];
}

export default function NextSessionCard({
  season,
}: {
  season: SeasonOverview | null;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const next = useMemo(() => findNext(season, now), [season, now]);
  const deferred = season?.deferred_future_events[0] ?? null;

  if (next === null) {
    // Nothing with an exact start. Either the calendar has not been published
    // that far ahead, or the season is over — say which.
    return (
      <article className="next-session next-session--idle">
        <p className="section-kicker">Next session</p>
        {deferred ? (
          <>
            <h3>{deferred.event_name}</h3>
            <p className="next-session__note">
              Round {deferred.round_number}, expected{" "}
              {formatDateTime(deferred.scheduled_start_at)}. FastF1 has not
              published exact session times yet, so there is nothing to count
              down to.
            </p>
          </>
        ) : (
          <>
            <h3>Nothing scheduled</h3>
            <p className="next-session__note">
              No session of this season has a published start time ahead of
              now.
            </p>
          </>
        )}
      </article>
    );
  }

  const remaining = next.startsAt.getTime() - now;

  return (
    <article className="next-session" aria-labelledby="next-session-title">
      <div className="next-session__heading">
        <span className="next-session__round">R{next.roundNumber}</span>
        <div>
          <p className="section-kicker">{next.eventName}</p>
          <h3 id="next-session-title">{next.session.session_name}</h3>
        </div>
      </div>

      <div
        className="next-session__clock"
        // Announced as one sentence rather than four ticking numbers, which a
        // screen reader would otherwise read out every second.
        aria-label={`Starts in ${countdownParts(remaining)
          .map((part) => `${Number(part.value)} ${part.label}`)
          .join(", ")}`}
        role="timer"
      >
        {countdownParts(remaining).map((part) => (
          <div aria-hidden="true" key={part.label}>
            <strong>{part.value}</strong>
            <span>{part.label}</span>
          </div>
        ))}
      </div>

      <p className="next-session__note">
        {formatDateTime(next.session.scheduled_start_at)}
      </p>
    </article>
  );
}
