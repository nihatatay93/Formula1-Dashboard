import type { PaceSeries } from "./racePaceAnalysis";
import { contiguousRuns, stintsOf } from "./racePaceAnalysis";
import { formatLapTime } from "./sessionFormat";

/**
 * Lap time against lap number, one line per driver.
 *
 * A full field is twenty overlapping lines, and no palette makes that legible
 * -- the team colours a reader recognises are given by the sport, and several
 * pairs of them are genuinely close. So the chart is focus-and-context:
 * everything is drawn, hovering or selecting one driver raises that line and
 * recedes the rest, and the raised line is named on the plot. Colour carries
 * team identity; position, the label and the stroke pattern carry the rest.
 *
 * Stint shading sits behind the highlighted driver only. Twenty overlapping
 * stint bands would be a wash of colour that reads as data.
 */

const CHART = {
  width: 760,
  height: 320,
  padLeft: 66,
  padRight: 46,
  padTop: 14,
  padBottom: 34,
};

export default function PaceEvolutionChart({
  series,
  onHighlight,
  highlighted,
  cutoffLapTimeUs,
}: {
  series: PaceSeries[];
  onHighlight: (sessionEntryId: string | null) => void;
  highlighted: string | null;
  cutoffLapTimeUs: number | null;
}) {
  const populated = series.filter((item) => item.laps.length > 0);
  if (populated.length === 0) {
    return null;
  }

  const lapNumbers = populated.flatMap((item) =>
    item.laps.map((lap) => lap.lap_number),
  );
  const lapTimes = populated.flatMap((item) =>
    item.laps.map((lap) => lap.lap_time_us),
  );

  const minLap = Math.min(...lapNumbers);
  const maxLap = Math.max(...lapNumbers);
  const fastest = Math.min(...lapTimes);
  const slowest = Math.max(...lapTimes);
  const pad = Math.max(Math.round((slowest - fastest) * 0.06), 100_000);
  const yMin = fastest - pad;
  const yMax = slowest + pad;
  const lapSpan = Math.max(maxLap - minLap, 1);
  const timeSpan = Math.max(yMax - yMin, 1);

  const plotWidth = CHART.width - CHART.padLeft - CHART.padRight;
  const plotHeight = CHART.height - CHART.padTop - CHART.padBottom;
  const scaleX = (lap: number) =>
    CHART.padLeft + ((lap - minLap) / lapSpan) * plotWidth;
  // Faster laps are smaller numbers and belong at the top.
  const scaleY = (time: number) =>
    CHART.padTop + ((time - yMin) / timeSpan) * plotHeight;

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const time = Math.round(yMax - ratio * timeSpan);
    return { label: formatLapTime(time), y: scaleY(time) };
  });

  const tickCount = Math.min(8, maxLap - minLap + 1);
  const lapTicks = Array.from({ length: tickCount }, (_, index) =>
    Math.round(minLap + (index * (maxLap - minLap)) / Math.max(tickCount - 1, 1)),
  ).filter((lap, index, all) => all.indexOf(lap) === index);

  const focused =
    highlighted === null
      ? null
      : (populated.find(
          (item) => item.entry.session_entry_id === highlighted,
        ) ?? null);

  const cutoffY =
    cutoffLapTimeUs !== null &&
    cutoffLapTimeUs >= yMin &&
    cutoffLapTimeUs <= yMax
      ? scaleY(cutoffLapTimeUs)
      : null;

  return (
    <figure className="pace-evolution">
      <svg
        className="pace-evolution__svg"
        role="img"
        aria-label={
          `Lap times over laps ${minLap} to ${maxLap} for ${populated.length} ` +
          `drivers. Fastest lap ${formatLapTime(fastest)}.`
        }
        viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      >
        {focused !== null
          ? stintsOf(focused.entry).map((stint) => (
              <g key={stint.stint_number}>
                <rect
                  className="pace-evolution__stint"
                  height={plotHeight}
                  width={Math.max(
                    scaleX(stint.last_lap) - scaleX(stint.first_lap),
                    1,
                  )}
                  x={scaleX(stint.first_lap)}
                  y={CHART.padTop}
                />
                <text
                  className="pace-evolution__stint-label"
                  x={scaleX(stint.first_lap) + 4}
                  y={CHART.padTop + 12}
                >
                  {stint.compound ?? `Stint ${stint.stint_number}`}
                </text>
              </g>
            ))
          : null}

        {gridLines.map((line) => (
          <g key={line.label}>
            <line
              className="pace-evolution__grid"
              x1={CHART.padLeft}
              x2={CHART.width - CHART.padRight}
              y1={line.y}
              y2={line.y}
            />
            <text
              className="pace-evolution__axis"
              dominantBaseline="middle"
              textAnchor="end"
              x={CHART.padLeft - 8}
              y={line.y}
            >
              {line.label}
            </text>
          </g>
        ))}

        {cutoffY !== null ? (
          <g>
            <line
              className="pace-evolution__cutoff"
              x1={CHART.padLeft}
              x2={CHART.width - CHART.padRight}
              y1={cutoffY}
              y2={cutoffY}
            />
            <text
              className="pace-evolution__cutoff-label"
              textAnchor="end"
              x={CHART.width - CHART.padRight}
              y={cutoffY - 5}
            >
              cutoff
            </text>
          </g>
        ) : null}

        {lapTicks.map((lap) => (
          <text
            className="pace-evolution__axis"
            key={lap}
            textAnchor="middle"
            x={scaleX(lap)}
            y={CHART.height - 12}
          >
            {lap}
          </text>
        ))}

        {/* SVG has no z-index: paint order is document order, so the raised
            line is drawn last or the receded ones cross over it. */}
        {[...populated]
          .sort((left, right) => {
            const leftFocused = left.entry.session_entry_id === highlighted;
            const rightFocused = right.entry.session_entry_id === highlighted;
            return Number(leftFocused) - Number(rightFocused);
          })
          .map((item) => {
          const isFocused =
            highlighted === item.entry.session_entry_id;
          const isDim = highlighted !== null && !isFocused;
          const last = item.laps[item.laps.length - 1];

          return (
            <g
              className={`pace-evolution__series${isDim ? " is-dim" : ""}${
                isFocused ? " is-focused" : ""
              }`}
              key={item.entry.session_entry_id}
              onMouseEnter={() => onHighlight(item.entry.session_entry_id)}
              onMouseLeave={() => onHighlight(null)}
            >
              {/* One stroke per run of consecutive laps. Bridging a gap would
                  draw pace the driver never set. */}
              {contiguousRuns(item.laps).map((run) =>
                run.length === 1 ? (
                  <circle
                    className="pace-evolution__lone-lap"
                    cx={scaleX(run[0].lap_number)}
                    cy={scaleY(run[0].lap_time_us)}
                    fill={item.color}
                    key={run[0].lap_number}
                    r={1.8}
                  />
                ) : (
                  <polyline
                    className="pace-evolution__line"
                    fill="none"
                    key={run[0].lap_number}
                    points={run
                      .map(
                        (lap) =>
                          `${scaleX(lap.lap_number)},${scaleY(lap.lap_time_us)}`,
                      )
                      .join(" ")}
                    stroke={item.color}
                    strokeDasharray={item.dashed ? "6 4" : undefined}
                  />
                ),
              )}
              {isFocused
                ? item.laps.map((lap) => (
                    <circle
                      className="pace-evolution__point"
                      cx={scaleX(lap.lap_number)}
                      cy={scaleY(lap.lap_time_us)}
                      fill={item.color}
                      key={lap.lap_number}
                      r={2.5}
                    >
                      <title>
                        {`${item.entry.display_name} · lap ${lap.lap_number} · ` +
                          `${formatLapTime(lap.lap_time_us)}` +
                          `${lap.is_clean ? "" : " · not a clean lap"}`}
                      </title>
                    </circle>
                  ))
                : null}
              {isFocused && last !== undefined ? (
                <text
                  className="pace-evolution__label"
                  dominantBaseline="middle"
                  x={scaleX(last.lap_number) + 6}
                  y={scaleY(last.lap_time_us)}
                >
                  {item.entry.abbreviation ?? item.entry.display_name}
                </text>
              ) : null}
              <title>{item.entry.display_name}</title>
            </g>
            );
          })}
      </svg>

      <figcaption className="pace-evolution__caption">
        Lap number →. Hover a line, or a driver in the distribution below, to
        follow one car and shade its stints.
      </figcaption>
    </figure>
  );
}
