import type { SessionEntryResult } from "../contracts";
import type { AnalysisSelection } from "./lapAnalysis";
import { isLapSelectable } from "./lapAnalysis";
import { formatLapTime } from "./sessionFormat";

/** Distinct strokes for entries whose archive row carries no team colour. */
const FALLBACK_SERIES_COLORS = ["#38bdf8", "#fbbf24", "#34d399", "#c084fc"];

const CHART = {
  width: 720,
  height: 260,
  padLeft: 64,
  padRight: 12,
  padTop: 12,
  padBottom: 30,
};

export interface PaceSeries {
  color: string;
  dashed: boolean;
  entry: SessionEntryResult;
  points: { lap_number: number; lap_time_us: number }[];
}

export function buildPaceSeries(selections: AnalysisSelection[]): PaceSeries[] {
  const usedColors = new Set<string>();

  return selections.map((selection, index) => {
    const color =
      selection.entry.team_color_hex ??
      FALLBACK_SERIES_COLORS[index % FALLBACK_SERIES_COLORS.length];
    // Team-mates share one colour, so the second of a pair is dashed rather
    // than drawn as an indistinguishable duplicate line.
    const dashed = usedColors.has(color);
    usedColors.add(color);

    return {
      color,
      dashed,
      entry: selection.entry,
      points: [...selection.laps]
        .filter(isLapSelectable)
        .sort((left, right) => left.lap_number - right.lap_number)
        .map((lap) => ({
          lap_number: lap.lap_number,
          lap_time_us: lap.lap_time_us,
        })),
    };
  });
}

export default function PaceTrendChart({ series }: { series: PaceSeries[] }) {
  const populated = series.filter((item) => item.points.length > 0);
  if (populated.length === 0) {
    return null;
  }

  const lapNumbers = populated.flatMap((item) =>
    item.points.map((point) => point.lap_number),
  );
  const lapTimes = populated.flatMap((item) =>
    item.points.map((point) => point.lap_time_us),
  );

  const minLap = Math.min(...lapNumbers);
  const maxLap = Math.max(...lapNumbers);
  const fastest = Math.min(...lapTimes);
  const slowest = Math.max(...lapTimes);
  // A flat or single-lap selection still needs a non-zero domain to scale.
  const timePad = Math.max(Math.round((slowest - fastest) * 0.08), 100_000);
  const yMin = fastest - timePad;
  const yMax = slowest + timePad;
  const lapSpan = Math.max(maxLap - minLap, 1);
  const timeSpan = Math.max(yMax - yMin, 1);

  const plotWidth = CHART.width - CHART.padLeft - CHART.padRight;
  const plotHeight = CHART.height - CHART.padTop - CHART.padBottom;
  const scaleX = (lap: number) =>
    CHART.padLeft + ((lap - minLap) / lapSpan) * plotWidth;
  // Faster laps are lower numbers, and belong at the top of the plot.
  const scaleY = (time: number) =>
    CHART.padTop + ((time - yMin) / timeSpan) * plotHeight;

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const time = Math.round(yMax - ratio * timeSpan);
    return { label: formatLapTime(time), y: scaleY(time) };
  });

  const tickCount = Math.min(6, maxLap - minLap + 1);
  const lapTicks = Array.from({ length: tickCount }, (_, index) =>
    Math.round(minLap + (index * (maxLap - minLap)) / Math.max(tickCount - 1, 1)),
  ).filter((lap, index, all) => all.indexOf(lap) === index);

  return (
    <figure className="pace-trend">
      <svg
        aria-label={`Selected-lap pace over laps ${minLap} to ${maxLap} for ${populated
          .map((item) => item.entry.display_name)
          .join(", ")}`}
        className="pace-trend__svg"
        role="img"
        viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      >
        {gridLines.map((line) => (
          <g key={line.label}>
            <line
              className="pace-trend__grid"
              x1={CHART.padLeft}
              x2={CHART.width - CHART.padRight}
              y1={line.y}
              y2={line.y}
            />
            <text
              className="pace-trend__axis"
              dominantBaseline="middle"
              textAnchor="end"
              x={CHART.padLeft - 8}
              y={line.y}
            >
              {line.label}
            </text>
          </g>
        ))}

        {lapTicks.map((lap) => (
          <text
            className="pace-trend__axis"
            key={lap}
            textAnchor="middle"
            x={scaleX(lap)}
            y={CHART.height - 10}
          >
            {lap}
          </text>
        ))}

        {populated.map((item) => (
          <g key={item.entry.session_entry_id}>
            <polyline
              className="pace-trend__line"
              fill="none"
              stroke={item.color}
              strokeDasharray={item.dashed ? "6 4" : undefined}
              points={item.points
                .map(
                  (point) =>
                    `${scaleX(point.lap_number)},${scaleY(point.lap_time_us)}`,
                )
                .join(" ")}
            />
            {item.points.map((point) => (
              <circle
                className="pace-trend__point"
                cx={scaleX(point.lap_number)}
                cy={scaleY(point.lap_time_us)}
                fill={item.color}
                key={point.lap_number}
                r={3}
              >
                <title>
                  {`${item.entry.display_name} · lap ${point.lap_number} · ${formatLapTime(point.lap_time_us)}`}
                </title>
              </circle>
            ))}
          </g>
        ))}
      </svg>

      <figcaption className="pace-trend__legend">
        {populated.map((item) => (
          <span key={item.entry.session_entry_id}>
            <i
              style={{
                background: item.dashed
                  ? `repeating-linear-gradient(90deg, ${item.color} 0 4px, transparent 4px 7px)`
                  : item.color,
              }}
            />
            {item.entry.display_name}
          </span>
        ))}
        <small>Lap number → · selected laps only</small>
      </figcaption>
    </figure>
  );
}