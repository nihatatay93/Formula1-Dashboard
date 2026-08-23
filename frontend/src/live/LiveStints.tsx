import type { LiveDriverRow } from "../contracts";
import { compoundTone } from "../archive/sessionFormat";

/**
 * What each car has run, as a bar per driver segmented by stint.
 *
 * Same idea as the archive's strategy chart, built from live data instead: the
 * feed reports each run's compound, the lap it started on and how many laps it
 * has covered. Width follows lap count, so a long stint reads as a long bar
 * without needing a lap axis the live board does not otherwise have.
 *
 * Compound colour comes from the tyre palette. It belongs to the rubber, not
 * the driver, so the driver's code carries their identity.
 */

const COMPOUND_VARIABLE: Record<string, string> = {
  soft: "var(--tyre-soft)",
  medium: "var(--tyre-medium)",
  hard: "var(--tyre-hard)",
  intermediate: "var(--tyre-intermediate)",
  wet: "var(--tyre-wet)",
  unknown: "var(--tyre-unknown)",
};

export default function LiveStints({ rows }: { rows: LiveDriverRow[] }) {
  const running = rows.filter((row) => (row.stints?.length ?? 0) > 0);

  if (running.length === 0) {
    return (
      <p className="session-explorer__hint">
        No stint has been reported yet. Tyre history appears once a car has
        completed a run.
      </p>
    );
  }

  const longest = Math.max(
    1,
    ...running.map((row) =>
      row.stints.reduce((total, stint) => total + (stint.laps ?? 0), 0),
    ),
  );

  return (
    <div className="live-stints">
      {running.map((row) => (
        <div className="live-stints__row" key={row.racing_number}>
          <span className="live-stints__driver">
            <span
              aria-hidden="true"
              className="live-stints__swatch"
              style={{
                background: row.team_colour
                  ? `#${row.team_colour}`
                  : "var(--muted-dark)",
              }}
            />
            {row.tla || row.racing_number}
          </span>
          <span className="live-stints__bar">
            {row.stints.map((stint, index) => {
              const laps = stint.laps ?? 0;
              const tone = compoundTone(stint.compound);
              return (
                <span
                  className="live-stints__stint"
                  key={`${stint.compound}-${stint.started_on_lap}-${index}`}
                  style={{
                    background: COMPOUND_VARIABLE[tone],
                    width: `${(laps / longest) * 100}%`,
                  }}
                  title={
                    `${stint.compound}${stint.fitted_new ? " (new)" : " (used)"}` +
                    `, ${laps} lap${laps === 1 ? "" : "s"}` +
                    (stint.started_on_lap !== null
                      ? `, from lap ${stint.started_on_lap}`
                      : "")
                  }
                >
                  <span className="sr-only">
                    {stint.compound}, {laps} laps
                  </span>
                </span>
              );
            })}
          </span>
          <span className="live-stints__count">
            {row.stints.length} stint{row.stints.length === 1 ? "" : "s"}
          </span>
        </div>
      ))}
    </div>
  );
}
