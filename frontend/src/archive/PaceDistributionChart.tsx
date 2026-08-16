import type { PaceSeries } from "./racePaceAnalysis";
import { WHISKER_RULE } from "./racePaceAnalysis";
import { formatLapTime } from "./sessionFormat";

/**
 * One box per driver, ordered by median.
 *
 * This is the view that stays readable with a full field. Twenty lines on the
 * evolution chart overlap; twenty boxes on a named axis do not, and the reader
 * gets the ranking from position rather than from telling two similar team
 * colours apart.
 */

const ROW_HEIGHT = 26;
const CHART = {
  width: 760,
  padLeft: 132,
  padRight: 20,
  padTop: 16,
  padBottom: 34,
};

export default function PaceDistributionChart({
  series,
  onHighlight,
  highlighted,
}: {
  series: PaceSeries[];
  onHighlight: (sessionEntryId: string | null) => void;
  highlighted: string | null;
}) {
  const measured = series.filter((item) => item.distribution !== null);
  if (measured.length === 0) {
    return null;
  }

  const height =
    CHART.padTop + measured.length * ROW_HEIGHT + CHART.padBottom;
  const plotWidth = CHART.width - CHART.padLeft - CHART.padRight;

  // The axis spans every drawn value, outliers included, so a point can never
  // fall outside the plot.
  const values = measured.flatMap((item) => [
    item.distribution!.minimum,
    item.distribution!.maximum,
  ]);
  const fastest = Math.min(...values);
  const slowest = Math.max(...values);
  const pad = Math.max(Math.round((slowest - fastest) * 0.04), 100_000);
  const domainMin = fastest - pad;
  const domainMax = slowest + pad;
  const span = Math.max(domainMax - domainMin, 1);
  const scaleX = (time: number) =>
    CHART.padLeft + ((time - domainMin) / span) * plotWidth;

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const time = Math.round(domainMin + ratio * span);
    return { label: formatLapTime(time), x: scaleX(time) };
  });

  return (
    <figure className="pace-box">
      <svg
        className="pace-box__svg"
        role="img"
        aria-label={
          `Lap time distribution for ${measured.length} drivers, ordered by ` +
          `median. Fastest median: ${measured[0].entry.display_name} at ` +
          `${formatLapTime(Math.round(measured[0].distribution!.median))}.`
        }
        viewBox={`0 0 ${CHART.width} ${height}`}
      >
        {ticks.map((tick) => (
          <g key={tick.label}>
            <line
              className="pace-box__grid"
              x1={tick.x}
              x2={tick.x}
              y1={CHART.padTop}
              y2={height - CHART.padBottom}
            />
            <text
              className="pace-box__axis"
              textAnchor="middle"
              x={tick.x}
              y={height - CHART.padBottom + 18}
            >
              {tick.label}
            </text>
          </g>
        ))}

        {measured.map((item, index) => {
          const summary = item.distribution!;
          const y = CHART.padTop + index * ROW_HEIGHT + ROW_HEIGHT / 2;
          const boxTop = y - 7;
          const isDim = highlighted !== null && highlighted !== item.entry.session_entry_id;
          const label =
            item.entry.abbreviation ?? item.entry.display_name.slice(0, 3).toUpperCase();

          return (
            <g
              className={`pace-box__row${isDim ? " is-dim" : ""}`}
              key={item.entry.session_entry_id}
              onMouseEnter={() => onHighlight(item.entry.session_entry_id)}
              onMouseLeave={() => onHighlight(null)}
            >
              {/* Identity is never colour alone: every row is named on the axis. */}
              <text
                className="pace-box__name"
                dominantBaseline="middle"
                textAnchor="end"
                x={CHART.padLeft - 30}
                y={y}
              >
                {item.entry.display_name}
              </text>
              <text
                className="pace-box__code"
                dominantBaseline="middle"
                textAnchor="end"
                x={CHART.padLeft - 8}
                y={y}
              >
                {label}
              </text>

              <line
                className="pace-box__whisker"
                stroke={item.color}
                x1={scaleX(summary.lowerWhisker)}
                x2={scaleX(summary.upperWhisker)}
                y1={y}
                y2={y}
              />
              <line
                className="pace-box__cap"
                stroke={item.color}
                x1={scaleX(summary.lowerWhisker)}
                x2={scaleX(summary.lowerWhisker)}
                y1={y - 5}
                y2={y + 5}
              />
              <line
                className="pace-box__cap"
                stroke={item.color}
                x1={scaleX(summary.upperWhisker)}
                x2={scaleX(summary.upperWhisker)}
                y1={y - 5}
                y2={y + 5}
              />

              <rect
                className="pace-box__box"
                fill={item.color}
                height={14}
                rx={3}
                stroke={item.color}
                strokeDasharray={item.dashed ? "5 3" : undefined}
                width={Math.max(scaleX(summary.q3) - scaleX(summary.q1), 2)}
                x={scaleX(summary.q1)}
                y={boxTop}
              />
              <line
                className="pace-box__median"
                x1={scaleX(summary.median)}
                x2={scaleX(summary.median)}
                y1={boxTop}
                y2={boxTop + 14}
              />

              {summary.outliers.map((value, outlierIndex) => (
                <circle
                  className="pace-box__outlier"
                  cx={scaleX(value)}
                  cy={y}
                  key={`${value}-${outlierIndex}`}
                  r={2.5}
                  stroke={item.color}
                />
              ))}

              <title>
                {`${item.entry.display_name} · ${summary.count} laps · ` +
                  `median ${formatLapTime(Math.round(summary.median))} · ` +
                  `best ${formatLapTime(summary.minimum)} · ` +
                  `middle half ${formatLapTime(Math.round(summary.q1))}–${formatLapTime(
                    Math.round(summary.q3),
                  )}`}
              </title>
            </g>
          );
        })}
      </svg>

      <figcaption className="pace-box__caption">{WHISKER_RULE}</figcaption>
    </figure>
  );
}
