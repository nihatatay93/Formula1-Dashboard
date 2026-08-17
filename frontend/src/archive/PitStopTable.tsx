import type { RacePaceEntry } from "../contracts";
import { pitStopsOf } from "./racePaceAnalysis";

/**
 * Every pit entry in the session, ordered by how long the car was in the lane.
 *
 * This is pit-*lane* time: from crossing the entry line to crossing the exit
 * line. It is not the stationary time a broadcast quotes as "2.1 seconds" --
 * that is the wheel gun figure, it is roughly twenty seconds shorter, and it
 * is not derivable from anything the archive holds. The caveat is printed
 * beside the numbers rather than left in a comment, because a reader who knows
 * the sport would otherwise assume the wrong measure.
 */

export const PIT_LANE_CAVEAT =
  "Time in the pit lane, measured from the entry line to the exit line. " +
  "This is not the stationary time quoted on television, which excludes the " +
  "drive in and out and is roughly twenty seconds shorter.";

function formatSeconds(microseconds: number): string {
  return `${(microseconds / 1_000_000).toFixed(3)}s`;
}

interface Row {
  entry: RacePaceEntry;
  lapNumber: number;
  pitLaneUs: number | null;
  underRedFlag: boolean;
}

export default function PitStopTable({
  entries,
}: {
  entries: RacePaceEntry[];
}) {
  const rows: Row[] = entries.flatMap((entry) =>
    pitStopsOf(entry).map((stop) => ({
      entry,
      lapNumber: stop.lap_number,
      pitLaneUs: stop.pit_lane_us,
      underRedFlag: stop.under_red_flag,
    })),
  );

  if (rows.length === 0) {
    return (
      <p className="session-explorer__hint">
        No pit entry is recorded for this session.
      </p>
    );
  }

  // Quickest first; a stop whose exit was never recorded has no duration to
  // rank by and goes last rather than sorting as zero.
  const ordered = [...rows].sort((left, right) => {
    if (left.pitLaneUs === null && right.pitLaneUs === null) {
      return left.lapNumber - right.lapNumber;
    }
    if (left.pitLaneUs === null) {
      return 1;
    }
    if (right.pitLaneUs === null) {
      return -1;
    }
    return left.pitLaneUs - right.pitLaneUs;
  });

  return (
    <div className="pit-stops">
      <div className="pit-stops__table-wrap">
        <table className="pit-stops__table">
          <caption>
            {ordered.length} pit entr{ordered.length === 1 ? "y" : "ies"},
            quickest first
          </caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Driver</th>
              <th scope="col">Team</th>
              <th scope="col">Lap</th>
              <th scope="col">Pit lane</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((row, index) => (
              <tr key={`${row.entry.session_entry_id}-${row.lapNumber}`}>
                <td className="pit-stops__rank">
                  {row.pitLaneUs === null ? "—" : index + 1}
                </td>
                <td>
                  <div className="pit-stops__name">
                    <span
                      aria-hidden="true"
                      className="pit-stops__swatch"
                      style={{
                        background:
                          row.entry.team_color_hex ?? "var(--muted-dark)",
                      }}
                    />
                    <strong>{row.entry.display_name}</strong>
                  </div>
                </td>
                <td className="pit-stops__team">
                  {row.entry.team_name ?? "—"}
                </td>
                <td className="pit-stops__figure">{row.lapNumber}</td>
                <td className="pit-stops__figure pit-stops__figure--lead">
                  {row.pitLaneUs !== null
                    ? formatSeconds(row.pitLaneUs)
                    : row.underRedFlag
                      ? "race suspended"
                      : "no exit recorded"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="pit-stops__caveat">
        <strong>Pit-lane time, not stop time.</strong> {PIT_LANE_CAVEAT}
      </p>
    </div>
  );
}
