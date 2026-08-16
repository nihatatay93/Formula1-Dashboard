import { useEffect, useMemo, useState } from "react";

import {
  getConsistency,
  getDriverStandings,
  getHeadToHead,
} from "../api";
import type {
  ConsistencyResponse,
  DriverStandingsResponse,
  HeadToHeadResponse,
} from "../contracts";
import { errorMessage } from "../shared/format";
import ConsistencyTable from "./ConsistencyTable";
import HeadToHeadBars from "./HeadToHeadBars";

/**
 * Two drivers against each other, and the field ranked by repeatability.
 *
 * The driver list comes from the standings, so only drivers this archive
 * actually holds can be picked and a comparison cannot be requested for a
 * season that has none.
 *
 * Team-mates are offered as a quick-select because that is the comparison
 * people reach for: same car, so the difference is the driver.
 */

type Tab = "head-to-head" | "consistency";

function formatPoints(points: string): string {
  const value = Number(points);
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export default function HeadToHeadView({ year }: { year: number }) {
  const [tab, setTab] = useState<Tab>("head-to-head");
  const [drivers, setDrivers] = useState<DriverStandingsResponse | null>(null);
  const [driverA, setDriverA] = useState<string | null>(null);
  const [driverB, setDriverB] = useState<string | null>(null);
  const [comparison, setComparison] = useState<HeadToHeadResponse | null>(null);
  const [consistency, setConsistency] = useState<ConsistencyResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDrivers(null);
    setComparison(null);
    setConsistency(null);
    setDriverA(null);
    setDriverB(null);

    Promise.all([
      getDriverStandings(year, controller.signal),
      getConsistency(year, controller.signal),
    ])
      .then(([standings, spread]) => {
        if (controller.signal.aborted) {
          return;
        }
        setDrivers(standings);
        setConsistency(spread);
        // Open on the closest thing to a real question: the leading pair.
        const items = standings.items ?? [];
        if (items.length >= 2) {
          setDriverA(items[0].driver_id);
          setDriverB(items[1].driver_id);
        }
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

  useEffect(() => {
    if (driverA === null || driverB === null || driverA === driverB) {
      setComparison(null);
      return;
    }
    const controller = new AbortController();
    getHeadToHead(year, driverA, driverB, controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) {
          setComparison(response);
        }
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") {
          return;
        }
        setError(errorMessage(caught));
      });

    return () => controller.abort();
  }, [year, driverA, driverB]);

  const options = drivers?.items ?? [];

  /** Pairs who share a team, which is the comparison worth offering. */
  const teamMates = useMemo(() => {
    const byTeam = new Map<string, typeof options>();
    for (const driver of options) {
      if (driver.team_name === null) {
        continue;
      }
      byTeam.set(driver.team_name, [
        ...(byTeam.get(driver.team_name) ?? []),
        driver,
      ]);
    }
    return [...byTeam.entries()]
      .filter(([, members]) => members.length === 2)
      .map(([team, members]) => ({ team, members }));
  }, [options]);

  const isEmpty = !loading && !error && options.length < 2;

  return (
    <section
      aria-labelledby="head-to-head-title"
      className="head-to-head workspace-view"
      data-view="head-to-head"
    >
      <div className="section-heading">
        {/* The page heading already carries the title; repeating it here
            would show it twice, so this one is for assistive technology. */}
        <h2 className="sr-only" id="head-to-head-title">
          {year} drivers compared
        </h2>
        <div className="standings__tabs" role="tablist" aria-label="Comparison">
          {(
            [
              ["head-to-head", "Head to head"],
              ["consistency", "Consistency"],
            ] as const
          ).map(([value, label]) => (
            <button
              aria-selected={tab === value}
              className={tab === value ? "is-selected" : ""}
              key={value}
              onClick={() => setTab(value)}
              role="tab"
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="session-explorer__loading" aria-live="polite">
          <span />
          Reading the {year} season…
        </div>
      ) : null}

      {error ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Comparison unavailable</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {isEmpty ? (
        <div className="empty-state">
          <span className="empty-state__number">{year}</span>
          <div>
            <h3>Not enough drivers to compare</h3>
            <p>
              A comparison needs at least two drivers with archived results.
              Run the season check, then come back once a race has been
              ingested.
            </p>
          </div>
        </div>
      ) : null}

      {!loading && !error && !isEmpty && tab === "head-to-head" ? (
        <>
          <div className="h2h-picker">
            <label>
              <span>Driver</span>
              <select
                onChange={(input) => setDriverA(input.target.value)}
                value={driverA ?? ""}
              >
                {options.map((driver) => (
                  <option key={driver.driver_id} value={driver.driver_id}>
                    {driver.display_name}
                  </option>
                ))}
              </select>
            </label>
            <span className="h2h-picker__versus">v</span>
            <label>
              <span>Driver</span>
              <select
                onChange={(input) => setDriverB(input.target.value)}
                value={driverB ?? ""}
              >
                {options.map((driver) => (
                  <option key={driver.driver_id} value={driver.driver_id}>
                    {driver.display_name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {teamMates.length > 0 ? (
            <div className="h2h-mates">
              <span className="h2h-mates__label">Team-mates</span>
              {teamMates.map(({ team, members }) => (
                <button
                  key={team}
                  onClick={() => {
                    setDriverA(members[0].driver_id);
                    setDriverB(members[1].driver_id);
                  }}
                  type="button"
                >
                  {team}
                </button>
              ))}
            </div>
          ) : null}

          {driverA === driverB ? (
            <p className="session-explorer__hint">
              Pick two different drivers to compare.
            </p>
          ) : null}

          {comparison !== null ? (
            comparison.never_met ? (
              <div className="empty-state">
                <span className="empty-state__number">0</span>
                <div>
                  <h3>These two never shared a session</h3>
                  <p>
                    {comparison.driver_a.display_name} and{" "}
                    {comparison.driver_b.display_name} have no archived session
                    in {year} in common, so there is no record to show.
                  </p>
                </div>
              </div>
            ) : (
              <>
                <HeadToHeadBars
                  driverA={comparison.driver_a}
                  driverB={comparison.driver_b}
                  label="Qualifying"
                  record={comparison.qualifying}
                  sameColour={
                    comparison.driver_a.team_color_hex ===
                    comparison.driver_b.team_color_hex
                  }
                />
                <HeadToHeadBars
                  driverA={comparison.driver_a}
                  driverB={comparison.driver_b}
                  label="Race"
                  record={comparison.race}
                  sameColour={
                    comparison.driver_a.team_color_hex ===
                    comparison.driver_b.team_color_hex
                  }
                />

                <table className="h2h-totals">
                  <caption>Season totals, from every completed session</caption>
                  <thead>
                    <tr>
                      <th scope="col">
                        {comparison.driver_a.display_name}
                      </th>
                      <th scope="col">Metric</th>
                      <th scope="col">
                        {comparison.driver_b.display_name}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["Points", formatPoints(comparison.totals_a.points), formatPoints(comparison.totals_b.points)],
                        ["Wins", comparison.totals_a.wins, comparison.totals_b.wins],
                        ["Podiums", comparison.totals_a.podiums, comparison.totals_b.podiums],
                        ["Poles", comparison.totals_a.poles, comparison.totals_b.poles],
                        ["Starts", comparison.totals_a.starts, comparison.totals_b.starts],
                        ["DNFs", comparison.totals_a.dnfs, comparison.totals_b.dnfs],
                        [
                          "Best finish",
                          comparison.totals_a.best_finish ?? "—",
                          comparison.totals_b.best_finish ?? "—",
                        ],
                      ] as const
                    ).map(([label, a, b]) => (
                      <tr key={label}>
                        <td className="h2h-totals__value">{a}</td>
                        <th scope="row">{label}</th>
                        <td className="h2h-totals__value">{b}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )
          ) : null}
        </>
      ) : null}

      {!loading && !error && !isEmpty && tab === "consistency" ? (
        consistency !== null ? (
          <>
            <ConsistencyTable items={consistency.items ?? []} />
            <p className="race-pace__footnote">{consistency.basis}</p>
          </>
        ) : null
      ) : null}
    </section>
  );
}
