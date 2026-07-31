import type {
  LiveBoard as Board,
  LiveDriverRow,
  LiveSectorCell,
  LiveSegmentStatus,
} from "./contracts";

/**
 * Live timing board.
 *
 * The backend normalises the feed's awkward per-session-type shapes into rows,
 * so this only decides layout and colour. Columns differ by session because the
 * data genuinely does: a race has gaps, intervals and pit stops, while
 * qualifying has a best lap per session part.
 */

function compoundClass(compound: string): string {
  const known = ["soft", "medium", "hard", "intermediate", "wet"];
  const normalized = compound.toLowerCase();
  return known.includes(normalized) ? normalized : "unknown";
}

function sectorClass(cell: LiveSectorCell): string {
  if (cell.overall_best) {
    return "live-sector live-sector--overall";
  }
  return cell.personal_best ? "live-sector live-sector--personal" : "live-sector";
}

const SEGMENT_LABELS: Record<LiveSegmentStatus, string> = {
  pending: "not yet reached",
  yellow: "slower",
  green: "personal best",
  purple: "overall fastest",
  pit: "pit lane",
  unknown: "unknown",
};

/** The strip is unreadable without a key; `unknown` is omitted until it occurs. */
const LEGEND: LiveSegmentStatus[] = ["purple", "green", "yellow", "pit", "pending"];

function SegmentLegend() {
  return (
    <p className="live-segments__legend">
      <span>Micro-sectors</span>
      {LEGEND.map((status) => (
        <span key={status}>
          <span
            aria-hidden="true"
            className={`live-segments__block live-segments__block--${status}`}
          />
          {SEGMENT_LABELS[status]}
        </span>
      ))}
    </p>
  );
}

/**
 * The micro-sector strip: one block per timing loop inside the sector, which is
 * how a lap in progress is read before its sector time is published.
 *
 * It is detail layered on top of the sector time, so it is hidden from assistive
 * technology and summarised in the sector's title instead — 22 rows of ~22
 * blocks would otherwise flood a screen reader on every tick.
 */
function SegmentStrip({ segments }: { segments: LiveSegmentStatus[] }) {
  if (segments.length === 0) {
    return null;
  }
  return (
    <span aria-hidden="true" className="live-segments">
      {segments.map((status, index) => (
        <span
          className={`live-segments__block live-segments__block--${status}`}
          key={index}
        />
      ))}
    </span>
  );
}

function sectorTitle(cell: LiveSectorCell, index: number): string {
  const reached = cell.segments.filter((status) => status !== "pending").length;
  const progress =
    cell.segments.length > 0
      ? `, ${reached}/${cell.segments.length} micro-sectors`
      : "";
  const tone = cell.overall_best
    ? " (overall fastest)"
    : cell.personal_best
      ? " (personal best)"
      : "";
  return `Sector ${index + 1}: ${cell.value || "no time"}${tone}${progress}`;
}

/**
 * Places gained, and whether it just happened.
 *
 * The count is stated as a number rather than an arrow alone, because an arrow
 * on its own says direction without saying how far — and colour is never the
 * only carrier: the sign is written out.
 */
function PositionMove({ row }: { row: LiveDriverRow }) {
  const gained = row.places_gained;
  if (gained === null || gained === 0) {
    // A driver who has not moved gets no mark rather than a "0".
    return row.recent_move ? null : <span className="live-move" />;
  }
  const direction = gained > 0 ? "up" : "down";
  const baseline =
    row.position_baseline === null
      ? ""
      : ` (from P${row.position_baseline} when this session was connected)`;
  return (
    <span
      className={`live-move live-move--${direction}${
        row.recent_move ? " live-move--recent" : ""
      }`}
      title={`${
        gained > 0 ? "Gained" : "Lost"
      } ${Math.abs(gained)} place${Math.abs(gained) === 1 ? "" : "s"}${baseline}`}
    >
      <i aria-hidden="true" />
      {gained > 0 ? `+${gained}` : gained}
    </span>
  );
}

function lapClass(row: LiveDriverRow): string {
  if (row.last_lap_overall_best) {
    return "live-board__lap live-board__lap--overall";
  }
  return row.last_lap_personal_best
    ? "live-board__lap live-board__lap--personal"
    : "live-board__lap";
}

function trackStatusTone(code: string): string {
  switch (code) {
    case "1":
      return "clear";
    case "2":
    case "3":
      return "yellow";
    case "4":
      return "safety";
    case "5":
    case "6":
      return "red";
    default:
      return "unknown";
  }
}

function DriverRow({ row, isRace }: { row: LiveDriverRow; isRace: boolean }) {
  const inactive = row.retired || row.stopped || row.knocked_out;

  return (
    <tr className={inactive ? "live-board__row--out" : undefined}>
      <td className="live-board__position">
        <span>{row.position ?? "—"}</span>
        <PositionMove row={row} />
      </td>
      <td>
        <div className="live-board__driver">
          <span
            aria-hidden="true"
            style={{ background: `#${row.team_colour || "667085"}` }}
          />
          <div>
            <strong>{row.tla || row.racing_number}</strong>
            <small>{row.team_name}</small>
          </div>
        </div>
      </td>
      <td>
        {row.compound ? (
          <span className={`live-tyre live-tyre--${compoundClass(row.compound)}`}>
            {row.compound.charAt(0)}
            {row.tyre_age !== null ? <small>{row.tyre_age}</small> : null}
          </span>
        ) : (
          "—"
        )}
      </td>
      {isRace ? (
        <>
          <td className="live-board__gap">{row.gap_to_leader || "—"}</td>
          <td className="live-board__gap">{row.interval || "—"}</td>
        </>
      ) : (
        <td className="live-board__gap">{row.gap_to_leader || "—"}</td>
      )}
      <td className={lapClass(row)}>{row.last_lap || "—"}</td>
      <td className="live-board__lap">{row.best_lap || "—"}</td>
      <td>
        <div className="live-board__sectors">
          {row.sectors.length > 0
            ? row.sectors.map((cell, index) => (
                <span
                  className={sectorClass(cell)}
                  key={index}
                  title={sectorTitle(cell, index)}
                >
                  <span className="live-sector__value">{cell.value || "—"}</span>
                  <SegmentStrip segments={cell.segments} />
                </span>
              ))
            : "—"}
        </div>
      </td>
      {isRace ? <td className="live-board__gap">{row.pit_stops ?? "—"}</td> : null}
      <td>
        <span className={`live-board__status live-board__status--${
          inactive ? "out" : row.in_pit ? "pit" : "on"
        }`}
        >
          {row.status}
        </span>
      </td>
    </tr>
  );
}

export default function LiveBoard({ board }: { board: Board }) {
  const isRace = board.session_type.toLowerCase() === "race";
  const tone = trackStatusTone(board.track_status_code);

  return (
    <div className="live-board">
      <div className="live-board__header">
        <div>
          <p className="section-kicker">
            {board.meeting_name || "Live session"}
          </p>
          <h3>{board.session_name || board.session_type || "Live timing"}</h3>
        </div>
        <div className="live-board__meta">
          {board.current_lap !== null ? (
            <span>
              Lap <strong>{board.current_lap}</strong>
              {board.total_laps ? ` / ${board.total_laps}` : null}
            </span>
          ) : null}
          {board.remaining && board.remaining !== "00:00:00" ? (
            <span>
              Remaining <strong>{board.remaining}</strong>
            </span>
          ) : null}
          {board.session_status ? (
            <span>
              Session <strong>{board.session_status}</strong>
            </span>
          ) : null}
          <span className={`live-track live-track--${tone}`}>
            {board.track_status || "Unknown"}
          </span>
        </div>
      </div>

      {board.drivers.length > 0 ? (
        <>
          <div className="live-board__table-wrap">
            <table className="live-board__table">
              <thead>
                <tr>
                  <th scope="col">Pos</th>
                  <th scope="col">Driver</th>
                  <th scope="col">Tyre</th>
                  {isRace ? (
                    <>
                      <th scope="col">Gap</th>
                      <th scope="col">Int</th>
                    </>
                  ) : (
                    <th scope="col">Gap</th>
                  )}
                  <th scope="col">Last lap</th>
                  <th scope="col">Best lap</th>
                  <th scope="col">Sectors</th>
                  {isRace ? <th scope="col">Stops</th> : null}
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {board.drivers.map((row) => (
                  <DriverRow isRace={isRace} key={row.racing_number} row={row} />
                ))}
              </tbody>
            </table>
          </div>
          {/* Outside the scroll region, so the key stays readable while the
              table is scrolled sideways. */}
          <SegmentLegend />
        </>
      ) : (
        <p className="session-explorer__hint">
          Connected. Waiting for the first timing data from the feed.
        </p>
      )}

      <div className="live-board__panels">
        {board.race_control.length > 0 ? (
          <section className="live-board__panel">
            <h4>Race control</h4>
            <ol className="live-messages">
              {board.race_control.map((item, index) => (
                <li key={`${item.utc}-${index}`}>
                  <span className="live-messages__lap">
                    {item.lap !== null ? `L${item.lap}` : item.category || "—"}
                  </span>
                  <span>{item.message}</span>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        {Object.keys(board.weather).length > 0 ? (
          <section className="live-board__panel">
            <h4>Weather</h4>
            <dl className="live-weather">
              {Object.entries(board.weather).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replace(/([a-z])([A-Z])/g, "$1 $2")}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}
      </div>
    </div>
  );
}
