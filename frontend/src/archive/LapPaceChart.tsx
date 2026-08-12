import type { LapSummary } from "../contracts";
import { compoundTone, formatLapTime } from "./sessionFormat";

export default function LapPaceChart({ laps }: { laps: LapSummary[] }) {
  const timedLaps = laps.filter(
    (lap): lap is LapSummary & { lap_time_us: number } =>
      lap.lap_time_us !== null,
  );
  if (timedLaps.length === 0) {
    return (
      <div className="lap-chart lap-chart--empty">
        No lap times are available in this page.
      </div>
    );
  }

  const values = timedLaps.map((lap) => lap.lap_time_us);
  const fastest = Math.min(...values);
  const slowest = Math.max(...values);
  const range = Math.max(1, slowest - fastest);

  return (
    <div
      aria-label={`Loaded lap-time profile from lap ${timedLaps[0].lap_number} to ${timedLaps[timedLaps.length - 1].lap_number}`}
      className="lap-chart"
      role="img"
    >
      <div className="lap-chart__plot">
        {timedLaps.map((lap) => {
          const height = 34 + ((slowest - lap.lap_time_us) / range) * 66;
          const qualityClass =
            lap.deleted === true || !lap.is_accurate
              ? " lap-chart__bar--muted"
              : "";
          return (
            <span
              className={`lap-chart__bar lap-chart__bar--${compoundTone(lap.compound)}${qualityClass}`}
              key={lap.id}
              style={{ height: `${height}%` }}
              title={`Lap ${lap.lap_number} · ${formatLapTime(lap.lap_time_us)} · ${lap.compound ?? "compound unknown"}`}
            >
              <small>{lap.lap_number}</small>
            </span>
          );
        })}
      </div>
      <div className="lap-chart__legend" aria-hidden="true">
        <span>Slower</span>
        <i />
        <span>Faster</span>
      </div>
    </div>
  );
}