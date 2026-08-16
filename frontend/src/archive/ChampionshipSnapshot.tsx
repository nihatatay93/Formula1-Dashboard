import { useEffect, useState } from "react";

import { getConstructorStandings, getDriverStandings } from "../api";
import type {
  ConstructorStandingsResponse,
  DriverStandingsResponse,
} from "../contracts";

/**
 * Who is leading, on the way in.
 *
 * A preview rather than a table: the full standings view carries the detail,
 * and repeating it here would make the landing page a worse version of it.
 * Failure is silent — the championship is not why someone opens this page, and
 * an archive with no scoring session yet is a normal state, not an error.
 */

const DRIVERS_SHOWN = 5;
const CONSTRUCTORS_SHOWN = 3;

function points(value: string): string {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? String(parsed) : parsed.toFixed(1);
}

export default function ChampionshipSnapshot({
  onOpen,
  year,
}: {
  onOpen: () => void;
  year: number;
}) {
  const [drivers, setDrivers] = useState<DriverStandingsResponse | null>(null);
  const [constructors, setConstructors] =
    useState<ConstructorStandingsResponse | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDrivers(null);
    setConstructors(null);

    Promise.all([
      getDriverStandings(year, controller.signal),
      getConstructorStandings(year, controller.signal),
    ])
      .then(([driverTable, constructorTable]) => {
        if (!controller.signal.aborted) {
          setDrivers(driverTable);
          setConstructors(constructorTable);
        }
      })
      .catch(() => {
        // Left silent on purpose; the standings view reports its own failures.
      });

    return () => controller.abort();
  }, [year]);

  const hasStandings = (drivers?.items?.length ?? 0) > 0;

  return (
    <section
      aria-labelledby="championship-snapshot-title"
      className="championship-snapshot"
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Championship</p>
          <h3 id="championship-snapshot-title">{year} standings</h3>
        </div>
        {hasStandings ? (
          <button className="text-action" onClick={onOpen} type="button">
            Full standings
          </button>
        ) : null}
      </div>

      {!hasStandings ? (
        <p className="session-explorer__hint">
          No scoring session of {year} has been archived yet, so there is
          nothing to rank.
        </p>
      ) : (
        <div className="championship-snapshot__columns">
          <div>
            <p className="championship-snapshot__label">Drivers</p>
            <ol className="championship-snapshot__list">
              {drivers?.items?.slice(0, DRIVERS_SHOWN).map((row) => (
                <li key={row.driver_id}>
                  <span className="championship-snapshot__position">
                    {row.position}
                  </span>
                  <span
                    aria-hidden="true"
                    className="championship-snapshot__swatch"
                    style={{
                      background: row.team_color
                        ? `#${row.team_color}`
                        : "var(--muted-dark)",
                    }}
                  />
                  <span className="championship-snapshot__name">
                    {row.display_name}
                  </span>
                  <span className="championship-snapshot__points">
                    {points(row.points)}
                  </span>
                </li>
              ))}
            </ol>
          </div>

          <div>
            <p className="championship-snapshot__label">Constructors</p>
            <ol className="championship-snapshot__list">
              {constructors?.items?.slice(0, CONSTRUCTORS_SHOWN).map((row) => (
                <li key={row.team_name}>
                  <span className="championship-snapshot__position">
                    {row.position}
                  </span>
                  <span
                    aria-hidden="true"
                    className="championship-snapshot__swatch"
                    style={{
                      background: row.team_color
                        ? `#${row.team_color}`
                        : "var(--muted-dark)",
                    }}
                  />
                  <span className="championship-snapshot__name">
                    {row.team_name}
                  </span>
                  <span className="championship-snapshot__points">
                    {points(row.points)}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </section>
  );
}
