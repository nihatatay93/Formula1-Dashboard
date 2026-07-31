import { useMemo, useState } from "react";

import type { LapTelemetrySample } from "./contracts";

/**
 * One lap's telemetry, as stacked facets over a shared distance axis.
 *
 * Speed, throttle and gear are three different measures on three different
 * scales, so they get three plots rather than one plot with three y-axes: a
 * second scale on the same frame makes crossings and gaps mean nothing, and the
 * reader cannot tell which line belongs to which axis.
 *
 * Colour carries no identity here — each facet holds a single trace and its own
 * heading says what it is — so every trace uses one hue, and the only second
 * colour is the braking annotation. Those two were checked for contrast and
 * colour-blind separation against the panel surface rather than eyeballed.
 */

const LAYOUT = {
  width: 720,
  facetHeight: 88,
  facetGap: 20,
  padLeft: 48,
  padRight: 14,
  padTop: 10,
  padBottom: 28,
};

/** An SVG polyline of a few thousand points costs more than it shows. */
const MAX_PLOT_POINTS = 600;

interface Facet {
  key: string;
  label: string;
  unit: string;
  value: (sample: LapTelemetrySample) => number | null;
  /** Fixed upper bound where the channel has one, so laps stay comparable. */
  max?: number;
  format: (value: number) => string;
  /** Gear holds its value until it changes; speed and throttle interpolate. */
  step?: boolean;
}

const FACETS: Facet[] = [
  {
    key: "speed",
    label: "Speed",
    unit: "km/h",
    value: (sample) => sample.speed_kph,
    format: (value) => `${Math.round(value)} km/h`,
  },
  {
    key: "throttle",
    label: "Throttle",
    unit: "%",
    value: (sample) => sample.throttle_percent,
    max: 100,
    format: (value) => `${Math.round(value)}%`,
  },
  {
    key: "gear",
    label: "Gear",
    unit: "",
    value: (sample) => sample.gear,
    format: (value) => `${Math.round(value)}`,
    step: true,
  },
];

function stride<T>(items: T[], limit: number): T[] {
  if (items.length <= limit) {
    return items;
  }
  const step = Math.ceil(items.length / limit);
  const reduced = items.filter((_, index) => index % step === 0);
  const last = items[items.length - 1];
  // Keep the final sample so the trace reaches the end of the lap.
  return reduced[reduced.length - 1] === last ? reduced : [...reduced, last];
}

/** Distance when the upstream recorded it, sample order otherwise. */
function distanceOf(sample: LapTelemetrySample, index: number): number {
  return sample.distance_m ?? index;
}

export default function LapTelemetryChart({
  samples,
  lapNumber,
  driverName,
}: {
  samples: LapTelemetrySample[];
  lapNumber: number;
  driverName: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);

  const plotted = useMemo(() => stride(samples, MAX_PLOT_POINTS), [samples]);

  const points = useMemo(
    () =>
      plotted.map((sample, index) => ({
        sample,
        distance: distanceOf(sample, index),
      })),
    [plotted],
  );

  const summary = useMemo(() => {
    const speeds = samples
      .map((sample) => sample.speed_kph)
      .filter((value): value is number => value !== null);
    const throttles = samples.filter(
      (sample) => (sample.throttle_percent ?? 0) > 95,
    ).length;
    const braking = samples.filter((sample) => sample.brake === true).length;
    return {
      topSpeed: speeds.length > 0 ? Math.max(...speeds) : null,
      minSpeed: speeds.length > 0 ? Math.min(...speeds) : null,
      fullThrottle: samples.length > 0 ? (throttles / samples.length) * 100 : 0,
      braking: samples.length > 0 ? (braking / samples.length) * 100 : 0,
    };
  }, [samples]);

  if (points.length < 2) {
    return (
      <p className="session-explorer__hint">
        This lap stored too few telemetry samples to plot.
      </p>
    );
  }

  const minDistance = points[0].distance;
  const maxDistance = points[points.length - 1].distance;
  const span = Math.max(maxDistance - minDistance, 1);
  const plotWidth = LAYOUT.width - LAYOUT.padLeft - LAYOUT.padRight;
  const height =
    LAYOUT.padTop +
    FACETS.length * LAYOUT.facetHeight +
    (FACETS.length - 1) * LAYOUT.facetGap +
    LAYOUT.padBottom;

  const scaleX = (distance: number) =>
    LAYOUT.padLeft + ((distance - minDistance) / span) * plotWidth;

  const facetTop = (index: number) =>
    LAYOUT.padTop + index * (LAYOUT.facetHeight + LAYOUT.facetGap);

  // Contiguous runs where the brake was on, drawn once behind every facet so a
  // braking zone reads as one vertical band across all three measures.
  const brakeZones: { from: number; to: number }[] = [];
  let zoneStart: number | null = null;
  points.forEach((point, index) => {
    const braking = point.sample.brake === true;
    if (braking && zoneStart === null) {
      zoneStart = point.distance;
    }
    if ((!braking || index === points.length - 1) && zoneStart !== null) {
      brakeZones.push({ from: zoneStart, to: point.distance });
      zoneStart = null;
    }
  });

  const hoveredPoint = hovered === null ? null : points[hovered];

  function handleMove(event: React.PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width === 0) {
      return;
    }
    // Pointer position is in CSS pixels; the plot is in viewBox units.
    const viewX = ((event.clientX - bounds.left) / bounds.width) * LAYOUT.width;
    const ratio = (viewX - LAYOUT.padLeft) / plotWidth;
    const target = minDistance + ratio * span;
    let nearest = 0;
    let best = Infinity;
    points.forEach((point, index) => {
      const gap = Math.abs(point.distance - target);
      if (gap < best) {
        best = gap;
        nearest = index;
      }
    });
    setHovered(nearest);
  }

  return (
    <figure className="telemetry-chart">
      <svg
        aria-label={
          `Telemetry for ${driverName} on lap ${lapNumber}: speed, throttle ` +
          `and gear over ${Math.round(span)} metres, from ${points.length} ` +
          `plotted samples. Top speed ` +
          `${summary.topSpeed === null ? "unknown" : Math.round(summary.topSpeed)} ` +
          `km/h, full throttle for ${Math.round(summary.fullThrottle)} percent ` +
          `of the lap, braking for ${Math.round(summary.braking)} percent.`
        }
        className="telemetry-chart__svg"
        onPointerLeave={() => setHovered(null)}
        onPointerMove={handleMove}
        role="img"
        viewBox={`0 0 ${LAYOUT.width} ${height}`}
      >
        {brakeZones.map((zone) => (
          <rect
            className="telemetry-chart__brake"
            height={height - LAYOUT.padTop - LAYOUT.padBottom}
            key={`${zone.from}-${zone.to}`}
            width={Math.max(scaleX(zone.to) - scaleX(zone.from), 1)}
            x={scaleX(zone.from)}
            y={LAYOUT.padTop}
          />
        ))}

        {FACETS.map((facet, facetIndex) => {
          const values = points.map((point) => facet.value(point.sample));
          const present = values.filter(
            (value): value is number => value !== null,
          );
          const top = facetTop(facetIndex);
          const bottom = top + LAYOUT.facetHeight;
          if (present.length < 2) {
            return (
              <text
                className="telemetry-chart__axis"
                key={facet.key}
                x={LAYOUT.padLeft}
                y={top + LAYOUT.facetHeight / 2}
              >
                {`${facet.label} was not recorded for this lap`}
              </text>
            );
          }
          const upper = facet.max ?? Math.max(...present);
          const lower = facet.max !== undefined ? 0 : Math.min(...present);
          const range = Math.max(upper - lower, 1);
          const scaleY = (value: number) =>
            bottom - ((value - lower) / range) * LAYOUT.facetHeight;

          // Step channels hold their value to the next sample rather than
          // sloping toward it, which is what a gear change actually does.
          const path: string[] = [];
          let previous: number | null = null;
          points.forEach((point, index) => {
            const value = values[index];
            if (value === null) {
              return;
            }
            const x = scaleX(point.distance);
            if (previous === null) {
              path.push(`M${x},${scaleY(value)}`);
            } else if (facet.step) {
              path.push(`L${x},${scaleY(previous)}`, `L${x},${scaleY(value)}`);
            } else {
              path.push(`L${x},${scaleY(value)}`);
            }
            previous = value;
          });

          return (
            <g key={facet.key}>
              <line
                className="telemetry-chart__grid"
                x1={LAYOUT.padLeft}
                x2={LAYOUT.width - LAYOUT.padRight}
                y1={bottom}
                y2={bottom}
              />
              <text
                className="telemetry-chart__facet-label"
                x={LAYOUT.padLeft}
                y={top - 4}
              >
                {facet.label}
                {facet.unit ? ` (${facet.unit})` : ""}
              </text>
              <text
                className="telemetry-chart__axis"
                dominantBaseline="middle"
                textAnchor="end"
                x={LAYOUT.padLeft - 6}
                y={top + 6}
              >
                {facet.format(upper)}
              </text>
              <text
                className="telemetry-chart__axis"
                dominantBaseline="middle"
                textAnchor="end"
                x={LAYOUT.padLeft - 6}
                y={bottom}
              >
                {facet.format(lower)}
              </text>
              <path
                className="telemetry-chart__trace"
                d={path.join(" ")}
                fill="none"
              />
              {hoveredPoint !== null &&
              facet.value(hoveredPoint.sample) !== null ? (
                <circle
                  className="telemetry-chart__marker"
                  cx={scaleX(hoveredPoint.distance)}
                  cy={scaleY(facet.value(hoveredPoint.sample) as number)}
                  r={4}
                />
              ) : null}
            </g>
          );
        })}

        {hoveredPoint !== null ? (
          <line
            className="telemetry-chart__crosshair"
            x1={scaleX(hoveredPoint.distance)}
            x2={scaleX(hoveredPoint.distance)}
            y1={LAYOUT.padTop}
            y2={height - LAYOUT.padBottom}
          />
        ) : null}

        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const distance = minDistance + ratio * span;
          return (
            <text
              className="telemetry-chart__axis"
              key={ratio}
              textAnchor="middle"
              x={scaleX(distance)}
              y={height - 8}
            >
              {`${Math.round(distance)} m`}
            </text>
          );
        })}
      </svg>

      <figcaption className="telemetry-chart__caption">
        <span className="telemetry-chart__key">
          <i aria-hidden="true" className="telemetry-chart__key-brake" />
          Braking
        </span>
        {hoveredPoint !== null ? (
          <span className="telemetry-chart__readout">
            {`${Math.round(hoveredPoint.distance)} m`}
            {FACETS.map((facet) => {
              const value = facet.value(hoveredPoint.sample);
              return (
                <b key={facet.key}>
                  {facet.label} {value === null ? "—" : facet.format(value)}
                </b>
              );
            })}
          </span>
        ) : (
          <span className="telemetry-chart__readout telemetry-chart__readout--idle">
            Point at the trace to read values along the lap
          </span>
        )}
      </figcaption>

      <dl className="telemetry-summary">
        <div>
          <dt>Top speed</dt>
          <dd>
            {summary.topSpeed === null
              ? "—"
              : `${Math.round(summary.topSpeed)} km/h`}
          </dd>
        </div>
        <div>
          <dt>Slowest</dt>
          <dd>
            {summary.minSpeed === null
              ? "—"
              : `${Math.round(summary.minSpeed)} km/h`}
          </dd>
        </div>
        <div>
          <dt>Full throttle</dt>
          <dd>{`${Math.round(summary.fullThrottle)}%`}</dd>
        </div>
        <div>
          <dt>Braking</dt>
          <dd>{`${Math.round(summary.braking)}%`}</dd>
        </div>
        <div>
          <dt>Samples</dt>
          <dd>{samples.length.toLocaleString("en")}</dd>
        </div>
      </dl>

      <details className="telemetry-table">
        <summary>Read the trace as a table</summary>
        <div className="telemetry-table__wrap">
          <table>
            <caption>
              {`Lap ${lapNumber} telemetry for ${driverName}, sampled every ` +
                `${Math.max(Math.round(samples.length / 24), 1)} rows.`}
            </caption>
            <thead>
              <tr>
                <th scope="col">Distance</th>
                <th scope="col">Speed</th>
                <th scope="col">Throttle</th>
                <th scope="col">Brake</th>
                <th scope="col">Gear</th>
              </tr>
            </thead>
            <tbody>
              {stride(samples, 24).map((sample, index) => (
                <tr key={sample.sample_index}>
                  <td>{`${Math.round(distanceOf(sample, index))} m`}</td>
                  <td>
                    {sample.speed_kph === null
                      ? "—"
                      : `${Math.round(sample.speed_kph)}`}
                  </td>
                  <td>
                    {sample.throttle_percent === null
                      ? "—"
                      : `${Math.round(sample.throttle_percent)}%`}
                  </td>
                  <td>{sample.brake === true ? "Yes" : "No"}</td>
                  <td>{sample.gear ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
