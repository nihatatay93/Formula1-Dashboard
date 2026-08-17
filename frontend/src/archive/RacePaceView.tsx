import { useEffect, useMemo, useState } from "react";

import { getRacePace } from "../api";
import type { RacePaceResponse } from "../contracts";
import { errorMessage } from "../shared/format";
import PaceDistributionChart from "./PaceDistributionChart";
import PaceEvolutionChart from "./PaceEvolutionChart";
import PitStopTable from "./PitStopTable";
import StrategyChart from "./StrategyChart";
import { buildPaceSeries, orderByMedian } from "./racePaceAnalysis";
import { formatLapTime } from "./sessionFormat";

/**
 * Race pace for a whole session.
 *
 * Every lap is fetched once and flagged; the controls then filter in the
 * browser, so a toggle or a slider redraws immediately instead of waiting on
 * the network. Nothing is hidden by default -- an in lap and a safety-car lap
 * are part of what happened, and a chart that quietly dropped them would
 * misrepresent the race.
 */

const DEFAULT_CUTOFF = 107;

type Tab = "pace" | "strategy";

export default function RacePaceView({
  sessionId,
  sessionName,
}: {
  sessionId: string;
  sessionName?: string;
}) {
  const [data, setData] = useState<RacePaceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cleanOnly, setCleanOnly] = useState(true);
  const [excludeBeyondCutoff, setExcludeBeyondCutoff] = useState(false);
  const [cutoff, setCutoff] = useState(DEFAULT_CUTOFF);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("pace");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    setHighlighted(null);

    getRacePace(sessionId, { outlierCutoff: cutoff, signal: controller.signal })
      .then((response) => {
        if (!controller.signal.aborted) {
          setData(response);
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
  }, [sessionId, cutoff]);

  const series = useMemo(
    () =>
      orderByMedian(
        buildPaceSeries(data?.items ?? [], { cleanOnly, excludeBeyondCutoff }),
      ),
    [data, cleanOnly, excludeBeyondCutoff],
  );

  const measuredLaps = series.reduce(
    (total, item) => total + item.laps.length,
    0,
  );
  const isEmpty = !loading && !error && measuredLaps === 0;

  return (
    <section
      aria-labelledby="race-pace-title"
      className="race-pace workspace-view"
      data-view="race-pace"
    >
      {/* The page heading already names the event and the session, so the
          view carries only a screen-reader label rather than repeating it. */}
      <div className="section-heading">
        <h2 className="sr-only" id="race-pace-title">
          Race analysis{sessionName ? ` · ${sessionName}` : ""}
        </h2>
        <div className="standings__tabs" role="tablist" aria-label="Race analysis">
          {(
            [
              ["pace", "Pace"],
              ["strategy", "Strategy"],
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

      {tab === "pace" ? (
      <div className="race-pace__controls">
        <label className="race-pace__toggle">
          <input
            checked={cleanOnly}
            onChange={(input) => setCleanOnly(input.target.checked)}
            type="checkbox"
          />
          <span>Clean laps only</span>
        </label>

        <label className="race-pace__toggle">
          <input
            checked={excludeBeyondCutoff}
            onChange={(input) => setExcludeBeyondCutoff(input.target.checked)}
            type="checkbox"
          />
          <span>Drop laps beyond the cutoff</span>
        </label>

        <label className="race-pace__slider">
          <span>
            Outlier cutoff <strong>{cutoff}%</strong>
            {data?.outlier_cutoff_lap_time_us != null ? (
              <small> · {formatLapTime(data.outlier_cutoff_lap_time_us)}</small>
            ) : null}
          </span>
          <input
            max={130}
            min={100}
            onChange={(input) => setCutoff(Number(input.target.value))}
            step={1}
            type="range"
            value={cutoff}
          />
        </label>
      </div>
      ) : null}

      {loading ? (
        <div className="session-explorer__loading" aria-live="polite">
          <span />
          Reading every lap of the session…
        </div>
      ) : null}

      {error ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Race pace unavailable</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {isEmpty ? (
        <div className="empty-state">
          <span className="empty-state__number">0</span>
          <div>
            <h3>No laps to compare</h3>
            <p>
              {cleanOnly
                ? "No lap of this session is clean under the current filter. Turn off clean laps only to see what was set."
                : "This session has no timed laps in the archive yet."}
            </p>
          </div>
        </div>
      ) : null}

      {!loading && !error && !isEmpty && data !== null && tab === "pace" ? (
        <>
          <p className="race-pace__summary">
            {measuredLaps} laps from {series.filter((item) => item.laps.length > 0).length}{" "}
            drivers
            {data.session_best_lap_time_us !== null ? (
              <>
                {" "}
                · session best{" "}
                <strong>{formatLapTime(data.session_best_lap_time_us)}</strong>
              </>
            ) : null}
          </p>

          <PaceEvolutionChart
            cutoffLapTimeUs={data.outlier_cutoff_lap_time_us}
            highlighted={highlighted}
            onHighlight={setHighlighted}
            series={series}
          />

          <PaceDistributionChart
            highlighted={highlighted}
            onHighlight={setHighlighted}
            series={series}
          />

          <p className="race-pace__footnote">{data.clean_lap_definition}</p>
        </>
      ) : null}

      {!loading && !error && data !== null && tab === "strategy" ? (
        <>
          {/* Strategy reads every lap the car ran, not only the clean ones:
              an in lap is exactly where a stint ends. */}
          <StrategyChart entries={data.items ?? []} />
          <PitStopTable entries={data.items ?? []} />
        </>
      ) : null}
    </section>
  );
}
