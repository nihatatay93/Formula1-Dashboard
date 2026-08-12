import { useMemo } from "react";

import type { LapSummary } from "../contracts";
import { isLapSelectable } from "./lapAnalysis";
import {
  compoundTone,
  formatDelta,
  formatLapTime,
  formatSectorTime,
} from "./sessionFormat";

export default function LapTable({
  activeTelemetryLap,
  laps,
  onToggleLap,
  onViewTelemetry,
  selectedLapNumbers,
  selectionDisabled,
}: {
  activeTelemetryLap: number | null;
  laps: LapSummary[];
  onToggleLap: (lap: LapSummary) => void;
  onViewTelemetry: (lap: LapSummary) => void;
  selectedLapNumbers: ReadonlySet<number>;
  selectionDisabled: boolean;
}) {
  const fastest = useMemo(() => {
    const timed = laps
      .filter((lap) => lap.deleted !== true && lap.is_accurate)
      .map((lap) => lap.lap_time_us)
      .filter((value): value is number => value !== null);
    return timed.length > 0 ? Math.min(...timed) : null;
  }, [laps]);

  return (
    <div className="lap-table-wrap">
      <table className="lap-table">
        <thead>
          <tr>
            <th scope="col">Select</th>
            <th scope="col">Lap</th>
            <th scope="col">Time</th>
            <th scope="col">Delta</th>
            <th scope="col">Stint</th>
            <th scope="col">Tyre</th>
            <th scope="col">Life</th>
            <th scope="col">S1</th>
            <th scope="col">S2</th>
            <th scope="col">S3</th>
            <th scope="col">Quality</th>
            <th scope="col">Telemetry</th>
          </tr>
        </thead>
        <tbody>
          {laps.map((lap) => (
            <tr
              className={[
                lap.deleted === true ? "lap-table__deleted" : "",
                selectedLapNumbers.has(lap.lap_number)
                  ? "lap-table__selected"
                  : "",
              ]
                .filter(Boolean)
                .join(" ")}
              key={lap.id}
            >
              <td>
                <label className="lap-selector">
                  <input
                    aria-label={`Select lap ${lap.lap_number} for pace analysis`}
                    checked={selectedLapNumbers.has(lap.lap_number)}
                    disabled={
                      !isLapSelectable(lap) ||
                      (selectionDisabled &&
                        !selectedLapNumbers.has(lap.lap_number))
                    }
                    onChange={() => onToggleLap(lap)}
                    type="checkbox"
                  />
                  <span aria-hidden="true" />
                </label>
              </td>
              <td>
                <strong>{lap.lap_number}</strong>
              </td>
              <td className="lap-table__time">
                {formatLapTime(lap.lap_time_us)}
              </td>
              <td>{formatDelta(lap.lap_time_us, fastest)}</td>
              <td>{lap.stint_number ?? "—"}</td>
              <td>
                <span
                  className={`compound compound--${compoundTone(lap.compound)}`}
                >
                  {lap.compound ?? "Unknown"}
                </span>
              </td>
              <td>{lap.tyre_life_laps ?? "—"}</td>
              <td>{formatSectorTime(lap.sector_1_time_us)}</td>
              <td>{formatSectorTime(lap.sector_2_time_us)}</td>
              <td>{formatSectorTime(lap.sector_3_time_us)}</td>
              <td>
                {lap.deleted === true
                  ? "Deleted"
                  : lap.is_accurate
                    ? "Accurate"
                    : "Unverified"}
              </td>
              <td>
                <button
                  aria-label={`View telemetry for lap ${lap.lap_number}`}
                  className="lap-table__telemetry"
                  onClick={() => onViewTelemetry(lap)}
                  type="button"
                >
                  {activeTelemetryLap === lap.lap_number
                    ? "Viewing"
                    : "Telemetry"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
