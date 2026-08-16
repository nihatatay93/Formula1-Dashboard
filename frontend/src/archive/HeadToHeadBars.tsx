import type { ComparedDriver, HeadToHeadRecord } from "../contracts";

/**
 * One metric, split between two drivers.
 *
 * A diverging bar from the centre: the wider side won more. Both the count and
 * the share are printed, because a 3-1 record and a 30-10 record have the same
 * shape and are not the same claim.
 *
 * Team-mates share a team colour exactly, so the bar cannot be the only thing
 * telling them apart -- each side is labelled with the driver's own code, and
 * the second of a pair is hatched.
 */

function share(value: number, total: number): number {
  return total === 0 ? 0 : (value / total) * 100;
}

export default function HeadToHeadBars({
  driverA,
  driverB,
  label,
  record,
  sameColour,
}: {
  driverA: ComparedDriver;
  driverB: ComparedDriver;
  label: string;
  record: HeadToHeadRecord;
  sameColour: boolean;
}) {
  const colourA = driverA.team_color_hex ?? "#8A8F98";
  const colourB = driverB.team_color_hex ?? "#8A8F98";
  const percentA = share(record.a_ahead, record.compared);
  const percentB = share(record.b_ahead, record.compared);
  const codeA = driverA.abbreviation ?? driverA.display_name;
  const codeB = driverB.abbreviation ?? driverB.display_name;

  return (
    <div className="h2h-metric">
      <div className="h2h-metric__heading">
        <h4>{label}</h4>
        {record.compared === 0 ? (
          <span className="h2h-metric__none">nothing to compare</span>
        ) : (
          <span className="h2h-metric__count">
            {record.a_ahead}–{record.b_ahead}
            <small>
              {" "}
              from {record.compared} session
              {record.compared === 1 ? "" : "s"}
            </small>
          </span>
        )}
      </div>

      <div
        aria-label={
          record.compared === 0
            ? `${label}: no session could be compared`
            : `${label}: ${driverA.display_name} ahead in ${record.a_ahead} of ` +
              `${record.compared}, ${driverB.display_name} ahead in ` +
              `${record.b_ahead}`
        }
        className="h2h-metric__bar"
        role="img"
      >
        <div className="h2h-metric__side h2h-metric__side--a">
          <span
            className={`h2h-metric__fill${sameColour ? " is-hatched" : ""}`}
            style={{ background: colourA, width: `${percentA}%` }}
          />
        </div>
        <span className="h2h-metric__axis" />
        <div className="h2h-metric__side h2h-metric__side--b">
          <span
            className="h2h-metric__fill"
            style={{ background: colourB, width: `${percentB}%` }}
          />
        </div>
      </div>

      <div className="h2h-metric__scale">
        <span>
          {codeA} {Math.round(percentA)}%
        </span>
        {record.excluded > 0 ? (
          <small>
            {record.excluded} excluded
          </small>
        ) : null}
        <span>
          {Math.round(percentB)}% {codeB}
        </span>
      </div>

      <p className="h2h-metric__basis">{record.basis}</p>
    </div>
  );
}
