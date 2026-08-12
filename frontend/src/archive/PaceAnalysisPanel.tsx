import type { AnalysisSelection } from "./lapAnalysis";
import {
  calculateLapSelectionStats,
  compareLapSelections,
  rankLapSelections,
} from "./lapAnalysis";
import PaceTrendChart, { buildPaceSeries } from "./PaceTrendChart";
import { formatLapTime, formatShortDelta } from "./sessionFormat";

/** Two participants are compared by default, and at most four. */
export const DEFAULT_ANALYSIS_SLOTS = 2;
export const MAX_ANALYSIS_PARTICIPANTS = 4;

export default function PaceAnalysisPanel({
  maxSlots,
  onAddSlot,
  onClearSelection,
  onRemoveSlot,
  selections,
  slots,
}: {
  maxSlots: number;
  onAddSlot: () => void;
  onClearSelection: (sessionEntryId: string) => void;
  onRemoveSlot: () => void;
  selections: AnalysisSelection[];
  slots: number;
}) {
  const analyzed = selections.flatMap((selection) => {
    const stats = calculateLapSelectionStats(selection.laps);
    return stats ? [{ selection, stats }] : [];
  });
  const ranked = rankLapSelections(
    analyzed.map((item) => ({
      entry: item.selection.entry,
      stats: item.stats,
    })),
  );
  const rankByEntryId = new Map(
    ranked.map((item) => [item.entry.session_entry_id, item]),
  );
  const series = buildPaceSeries(analyzed.map((item) => item.selection));
  // The two-participant sentence stays the primary read-out; three or more
  // are ranked instead, because "X is faster than Y" no longer describes it.
  const comparison =
    analyzed.length === 2
      ? compareLapSelections(analyzed[0].stats, analyzed[1].stats)
      : null;

  return (
    <section
      aria-labelledby="pace-analysis-title"
      className="pace-analysis"
    >
      <div className="pace-analysis__heading">
        <div>
          <p className="section-kicker">Manual long-run study</p>
          <h3 id="pace-analysis-title">Selected-lap pace analysis</h3>
        </div>
        <div className="pace-analysis__slots">
          <span>
            {analyzed.length}/{slots} comparison slots
          </span>
          <button
            aria-label="Remove a comparison slot"
            disabled={slots <= DEFAULT_ANALYSIS_SLOTS || selections.length >= slots}
            onClick={onRemoveSlot}
            type="button"
          >
            − Slot
          </button>
          <button
            aria-label="Add a comparison slot"
            disabled={slots >= maxSlots}
            onClick={onAddSlot}
            type="button"
          >
            + Slot
          </button>
        </div>
      </div>
      <p className="pace-analysis__disclaimer">
        Select the laps you consider representative. The dashboard calculates
        only those choices and does not infer fuel load, engine mode, or a race
        simulation.
      </p>

      <div className="pace-analysis__grid">
        {Array.from({ length: slots }, (_, slot) => {
          const analysis = analyzed[slot];
          if (!analysis) {
            return (
              <div className="pace-analysis__empty" key={slot}>
                <span>Slot {slot + 1}</span>
                <strong>Select timed laps from a participant</strong>
              </div>
            );
          }
          const { selection, stats } = analysis;
          const qualityWarnings =
            stats.quality.deleted +
            stats.quality.inaccurate +
            stats.quality.pit_transition;
          return (
            <article className="pace-analysis__card" key={selection.entry.session_entry_id}>
              <header>
                <div>
                  <span>
                    {selection.entry.team_name ?? "Independent"} ·{" "}
                    {selection.entry.abbreviation ?? "Driver"}
                  </span>
                  <strong>{selection.entry.display_name}</strong>
                  {rankByEntryId.has(selection.entry.session_entry_id) ? (
                    <span className="pace-analysis__rank">
                      {(() => {
                        const rankInfo = rankByEntryId.get(
                          selection.entry.session_entry_id,
                        ) as NonNullable<
                          ReturnType<typeof rankByEntryId.get>
                        >;
                        return rankInfo.delta_to_fastest_us === 0
                          ? `P${rankInfo.rank} · fastest average`
                          : `P${rankInfo.rank} · +${formatShortDelta(rankInfo.delta_to_fastest_us)}`;
                      })()}
                    </span>
                  ) : null}
                </div>
                <button
                  aria-label={`Clear ${selection.entry.display_name} pace selection`}
                  onClick={() =>
                    onClearSelection(selection.entry.session_entry_id)
                  }
                  type="button"
                >
                  Clear
                </button>
              </header>
              <div className="pace-analysis__metrics">
                <div>
                  <span>Average</span>
                  <strong>{formatLapTime(stats.average_lap_time_us)}</strong>
                </div>
                <div>
                  <span>Fastest</span>
                  <strong>{formatLapTime(stats.fastest_lap_time_us)}</strong>
                </div>
                <div>
                  <span>Spread</span>
                  <strong>{formatShortDelta(stats.spread_us)}</strong>
                </div>
              </div>
              <p>
                {stats.lap_count} selected · laps{" "}
                {stats.lap_numbers.join(", ")}
              </p>
              <div className="pace-analysis__quality">
                {qualityWarnings === 0 ? (
                  <span className="pace-analysis__quality--clean">
                    No quality warnings
                  </span>
                ) : (
                  <>
                    {stats.quality.deleted > 0 ? (
                      <span>{stats.quality.deleted} deleted</span>
                    ) : null}
                    {stats.quality.inaccurate > 0 ? (
                      <span>{stats.quality.inaccurate} inaccurate</span>
                    ) : null}
                    {stats.quality.pit_transition > 0 ? (
                      <span>{stats.quality.pit_transition} pit transition</span>
                    ) : null}
                  </>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <PaceTrendChart series={series} />

      {comparison ? (
        <p className="pace-analysis__comparison" role="status">
          {comparison.faster === "equal" ? (
            "Selected averages are equal."
          ) : (
            <>
              <strong>
                {comparison.faster === "first"
                  ? analyzed[0].selection.entry.display_name
                  : analyzed[1].selection.entry.display_name}
              </strong>{" "}
              is {formatShortDelta(comparison.average_delta_us)} faster on the
              selected average.
            </>
          )}
        </p>
      ) : ranked.length > 2 ? (
        <ol className="pace-analysis__ranking" role="status">
          {ranked.map((item) => (
            <li key={item.entry.session_entry_id}>
              <span>P{item.rank}</span>
              <strong>{item.entry.display_name}</strong>
              <span>{formatLapTime(item.stats.average_lap_time_us)}</span>
              <span>
                {item.delta_to_fastest_us === 0
                  ? "Fastest"
                  : `+${formatShortDelta(item.delta_to_fastest_us)}`}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}