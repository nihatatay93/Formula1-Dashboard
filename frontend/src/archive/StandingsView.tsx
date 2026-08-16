import { useEffect, useState } from "react";

import { getConstructorStandings, getDriverStandings } from "../api";
import type {
  ConstructorStandingsResponse,
  DriverStandingsResponse,
  StandingsRoundPoints,
} from "../contracts";
import { errorMessage } from "../shared/format";

/**
 * The drivers' and constructors' championships.
 *
 * Both tables are ordered by the backend, which breaks ties on wins and then
 * podiums; re-sorting here would silently change those tie-breaks. Rows are
 * rendered in the order they arrive.
 *
 * A standing is only as complete as the archive behind it, so the count of
 * sessions it was computed from is shown rather than implied — a table built
 * on half an ingested season looks exactly like a mid-season one otherwise.
 */

type Championship = "drivers" | "constructors";

/** Cumulative points after each scoring session, for the trend line. */
function cumulative(rounds: StandingsRoundPoints[]): number[] {
  let running = 0;
  return rounds.map((round) => {
    running += Number(round.points);
    return running;
  });
}

/**
 * A sparkline of the season so far.
 *
 * Deliberately unlabelled and hidden from assistive technology: it is a shape,
 * and every number it encodes is already in the row beside it.
 */
function PointsTrend({ rounds }: { rounds: StandingsRoundPoints[] }) {
  const totals = cumulative(rounds);
  if (totals.length < 2) {
    return <span className="standings__trend standings__trend--empty" />;
  }
  const peak = Math.max(...totals, 1);
  const step = 100 / (totals.length - 1);
  const points = totals
    .map((total, index) => `${index * step},${30 - (total / peak) * 28}`)
    .join(" ");

  return (
    <svg
      aria-hidden="true"
      className="standings__trend"
      preserveAspectRatio="none"
      viewBox="0 0 100 30"
    >
      <polyline fill="none" points={points} />
    </svg>
  );
}

function TeamSwatch({ colour }: { colour: string | null }) {
  return (
    <span
      aria-hidden="true"
      className="standings__swatch"
      style={{ background: colour ? `#${colour}` : "var(--muted-dark)" }}
    />
  );
}

function formatPoints(points: string): string {
  // Points arrive as exact decimals; half points exist, trailing zeroes do not
  // belong on screen.
  const value = Number(points);
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export default function StandingsView({ year }: { year: number }) {
  const [championship, setChampionship] = useState<Championship>("drivers");
  const [drivers, setDrivers] = useState<DriverStandingsResponse | null>(null);
  const [constructors, setConstructors] =
    useState<ConstructorStandingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDrivers(null);
    setConstructors(null);

    Promise.all([
      getDriverStandings(year, controller.signal),
      getConstructorStandings(year, controller.signal),
    ])
      .then(([driverTable, constructorTable]) => {
        if (controller.signal.aborted) {
          return;
        }
        setDrivers(driverTable);
        setConstructors(constructorTable);
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return;
        }
        setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [year]);

  const scoringSessions = drivers?.scoring_sessions ?? 0;
  const isEmpty =
    !loading &&
    !error &&
    (drivers?.items?.length ?? 0) === 0 &&
    (constructors?.items?.length ?? 0) === 0;

  return (
    <section
      aria-labelledby="standings-title"
      className="standings workspace-view"
      data-view="standings"
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Championship</p>
          <h2 id="standings-title">{year} standings</h2>
        </div>
        <div className="standings__tabs" role="tablist" aria-label="Championship">
          {(["drivers", "constructors"] as const).map((option) => (
            <button
              aria-selected={championship === option}
              className={championship === option ? "is-selected" : ""}
              key={option}
              onClick={() => setChampionship(option)}
              role="tab"
              type="button"
            >
              {option === "drivers" ? "Drivers" : "Constructors"}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="session-explorer__loading" aria-live="polite">
          <span />
          Computing the {year} championship…
        </div>
      ) : null}

      {error ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Standings unavailable</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {isEmpty ? (
        <div className="empty-state">
          <span className="empty-state__number">{year}</span>
          <div>
            <h3>Nothing to rank yet</h3>
            <p>
              No scoring session of this season has been ingested. Run the
              season check, then come back once a race has been archived.
            </p>
          </div>
        </div>
      ) : null}

      {!loading && !error && !isEmpty ? (
        <>
          <div className="standings__table-wrap">
            {championship === "drivers" ? (
              <table className="standings__table">
                <caption>
                  {year} drivers' championship, from {scoringSessions} scoring
                  session{scoringSessions === 1 ? "" : "s"}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Pos</th>
                    <th scope="col">Driver</th>
                    <th scope="col">Team</th>
                    <th scope="col">Points</th>
                    <th scope="col">Wins</th>
                    <th scope="col">Podiums</th>
                    <th scope="col">Poles</th>
                    <th scope="col">DNF</th>
                    <th scope="col">Season</th>
                  </tr>
                </thead>
                <tbody>
                  {drivers?.items?.map((row) => (
                    <tr key={row.driver_id}>
                      <td className="standings__position">{row.position}</td>
                      <td>
                        <div className="standings__name">
                          <TeamSwatch colour={row.team_color} />
                          <div>
                            <strong>{row.display_name}</strong>
                            {row.abbreviation ? (
                              <small>{row.abbreviation}</small>
                            ) : null}
                          </div>
                        </div>
                      </td>
                      <td className="standings__team">{row.team_name ?? "—"}</td>
                      <td className="standings__points">
                        {formatPoints(row.points)}
                      </td>
                      <td className="standings__count">{row.wins}</td>
                      <td className="standings__count">{row.podiums}</td>
                      <td className="standings__count">{row.poles}</td>
                      <td className="standings__count">{row.dnfs}</td>
                      <td>
                        <PointsTrend rounds={row.rounds} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table className="standings__table">
                <caption>
                  {year} constructors' championship, from {scoringSessions}{" "}
                  scoring session{scoringSessions === 1 ? "" : "s"}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Pos</th>
                    <th scope="col">Constructor</th>
                    <th scope="col">Drivers</th>
                    <th scope="col">Points</th>
                    <th scope="col">Wins</th>
                    <th scope="col">Podiums</th>
                    <th scope="col">Season</th>
                  </tr>
                </thead>
                <tbody>
                  {constructors?.items?.map((row) => (
                    <tr key={row.team_name}>
                      <td className="standings__position">{row.position}</td>
                      <td>
                        <div className="standings__name">
                          <TeamSwatch colour={row.team_color} />
                          <strong>{row.team_name}</strong>
                        </div>
                      </td>
                      <td className="standings__team">
                        {row.drivers.join(", ") || "—"}
                      </td>
                      <td className="standings__points">
                        {formatPoints(row.points)}
                      </td>
                      <td className="standings__count">{row.wins}</td>
                      <td className="standings__count">{row.podiums}</td>
                      <td>
                        <PointsTrend rounds={row.rounds} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <p className="standings__footnote">
            Computed from archived results. A round that has not been ingested
            is absent rather than zero, so a table can trail the real
            championship until the archive catches up.
          </p>
        </>
      ) : null}
    </section>
  );
}
