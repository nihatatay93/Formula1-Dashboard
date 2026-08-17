import type { RacePaceEntry } from "../contracts";
import { compoundTone } from "./sessionFormat";
import { pitStopsOf, stintsOf } from "./racePaceAnalysis";

/**
 * One bar per driver, segmented by stint and coloured by compound.
 *
 * Segments are placed by lap number rather than by width alone, so a bar reads
 * against the lap axis and two drivers' stops can be compared by eye. Pit
 * entries are marked on the boundary they happened at.
 *
 * Compound colour is the tyre's own, from the existing palette -- it is not a
 * per-driver series colour, and the driver's name carries their identity.
 */

const ROW_HEIGHT = 26;
const BAR_HEIGHT = 15;
const CHART = {
  width: 760,
  padLeft: 132,
  padRight: 18,
  padTop: 14,
  padBottom: 34,
};

const COMPOUND_VARIABLE: Record<string, string> = {
  soft: "var(--tyre-soft)",
  medium: "var(--tyre-medium)",
  hard: "var(--tyre-hard)",
  intermediate: "var(--tyre-intermediate)",
  wet: "var(--tyre-wet)",
  unknown: "var(--tyre-unknown)",
};

export default function StrategyChart({
  entries,
}: {
  entries: RacePaceEntry[];
}) {
  const running = entries.filter((entry) => stintsOf(entry).length > 0);
  if (running.length === 0) {
    return null;
  }

  const lapNumbers = running.flatMap((entry) =>
    entry.laps.map((lap) => lap.lap_number),
  );
  const firstLap = Math.min(...lapNumbers);
  const lastLap = Math.max(...lapNumbers);
  const span = Math.max(lastLap - firstLap + 1, 1);

  const height = CHART.padTop + running.length * ROW_HEIGHT + CHART.padBottom;
  const plotWidth = CHART.width - CHART.padLeft - CHART.padRight;
  // A stint covering laps 5 to 10 occupies the whole of lap 10, so the span is
  // inclusive at both ends.
  const scaleX = (lap: number) =>
    CHART.padLeft + ((lap - firstLap) / span) * plotWidth;

  const tickCount = Math.min(8, span);
  const ticks = Array.from({ length: tickCount }, (_, index) =>
    Math.round(firstLap + (index * (lastLap - firstLap)) / Math.max(tickCount - 1, 1)),
  ).filter((lap, index, all) => all.indexOf(lap) === index);

  const compounds = [
    ...new Set(
      running.flatMap((entry) =>
        stintsOf(entry).map((stint) => compoundTone(stint.compound)),
      ),
    ),
  ];

  return (
    <figure className="strategy">
      <svg
        aria-label={
          `Tyre strategy for ${running.length} drivers over laps ${firstLap} ` +
          `to ${lastLap}, one bar per driver segmented by stint.`
        }
        className="strategy__svg"
        role="img"
        viewBox={`0 0 ${CHART.width} ${height}`}
      >
        {ticks.map((lap) => (
          <g key={lap}>
            <line
              className="strategy__grid"
              x1={scaleX(lap)}
              x2={scaleX(lap)}
              y1={CHART.padTop}
              y2={height - CHART.padBottom}
            />
            <text
              className="strategy__axis"
              textAnchor="middle"
              x={scaleX(lap)}
              y={height - CHART.padBottom + 18}
            >
              {lap}
            </text>
          </g>
        ))}

        {running.map((entry, index) => {
          const y = CHART.padTop + index * ROW_HEIGHT + ROW_HEIGHT / 2;
          const stints = stintsOf(entry);
          const stops = pitStopsOf(entry);

          return (
            <g className="strategy__row" key={entry.session_entry_id}>
              <text
                className="strategy__name"
                dominantBaseline="middle"
                textAnchor="end"
                x={CHART.padLeft - 30}
                y={y}
              >
                {entry.display_name}
              </text>
              <text
                className="strategy__code"
                dominantBaseline="middle"
                textAnchor="end"
                x={CHART.padLeft - 8}
                y={y}
              >
                {entry.abbreviation ??
                  entry.display_name.slice(0, 3).toUpperCase()}
              </text>

              {stints.map((stint) => {
                const tone = compoundTone(stint.compound);
                const start = scaleX(stint.first_lap);
                const end = scaleX(stint.last_lap + 1);
                return (
                  <rect
                    className="strategy__stint"
                    fill={COMPOUND_VARIABLE[tone]}
                    height={BAR_HEIGHT}
                    key={stint.stint_number}
                    rx={2}
                    // A 2px gap keeps two stints from reading as one.
                    width={Math.max(end - start - 2, 1)}
                    x={start}
                    y={y - BAR_HEIGHT / 2}
                  >
                    <title>
                      {`${entry.display_name} · stint ${stint.stint_number} · ` +
                        `${stint.compound ?? "unknown compound"} · laps ` +
                        `${stint.first_lap}–${stint.last_lap} (${stint.laps})`}
                    </title>
                  </rect>
                );
              })}

              {stops.map((stop) => (
                <line
                  className="strategy__stop"
                  key={stop.lap_number}
                  x1={scaleX(stop.lap_number + 1)}
                  x2={scaleX(stop.lap_number + 1)}
                  y1={y - BAR_HEIGHT / 2 - 3}
                  y2={y + BAR_HEIGHT / 2 + 3}
                />
              ))}
            </g>
          );
        })}
      </svg>

      <figcaption className="strategy__legend">
        {compounds.map((tone) => (
          <span className={`compound compound--${tone}`} key={tone}>
            {tone}
          </span>
        ))}
        <small>Lap number → · a vertical mark is a pit entry</small>
      </figcaption>
    </figure>
  );
}
