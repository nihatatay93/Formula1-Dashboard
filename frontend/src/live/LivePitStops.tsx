import type { LivePitStop } from "../contracts";

/**
 * Completed trips through the pit lane, quickest first.
 *
 * The feed sends no duration. `InPit` is a boolean, and `PitLaneTimeCollection`
 * arrived empty throughout the race this was built against, so each stop is
 * timed from the flag turning on to turning off. Measured that way once the
 * Dutch Grand Prix was running, every stop fell between 14.8 and 19.4 seconds.
 *
 * That makes this pit-*lane* time, entry to exit, timed at this end rather than
 * by the circuit's loops. It is not the stationary figure a broadcast quotes,
 * which is roughly twenty seconds shorter, and the caveat is on screen rather
 * than left for a reader to assume otherwise.
 */
export default function LivePitStops({ stops }: { stops: LivePitStop[] }) {
  if (stops.length === 0) {
    return (
      <section className="live-board__panel">
        <h4>Pit stops</h4>
        <p className="session-explorer__hint">
          No completed stop yet. A stop is timed from the car entering the pit
          lane to leaving it, so one appears here once a car has done both
          since this session started being collected.
        </p>
      </section>
    );
  }

  const quickest = stops[0].seconds;

  return (
    <section className="live-board__panel">
      <h4>
        Pit stops <span className="live-radio__count">{stops.length}</span>
      </h4>
      <ol
        aria-label={`Pit stops, ${stops.length} completed, quickest first`}
        className="live-pits"
        tabIndex={0}
      >
        {stops.map((stop, index) => (
          <li key={`${stop.racing_number}-${stop.lap_number}-${index}`}>
            <span className="live-pits__rank">{index + 1}</span>
            <span
              aria-hidden="true"
              className="live-pits__swatch"
              style={{
                background: stop.team_colour
                  ? `#${stop.team_colour}`
                  : "var(--muted-dark)",
              }}
            />
            <span className="live-pits__driver">
              {stop.tla || stop.display_name || stop.racing_number}
            </span>
            {stop.lap_number !== null ? (
              <span className="live-pits__lap">L{stop.lap_number}</span>
            ) : null}
            <span className="live-pits__time">
              {stop.seconds.toFixed(1)}s
              {index > 0 ? (
                <small>+{(stop.seconds - quickest).toFixed(1)}</small>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
      <p className="live-pits__caveat">
        Time in the pit lane, entry to exit, timed from the feed's in-pit flag.
        Not the stationary time quoted on television, which is about twenty
        seconds shorter.
      </p>
    </section>
  );
}
