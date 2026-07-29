import type { LapSummary } from "./contracts";

export interface LapSelectionQuality {
  deleted: number;
  inaccurate: number;
  pit_transition: number;
}

export interface LapSelectionStats {
  lap_numbers: number[];
  lap_count: number;
  average_lap_time_us: number;
  fastest_lap_time_us: number;
  slowest_lap_time_us: number;
  spread_us: number;
  quality: LapSelectionQuality;
}

export interface LapSelectionComparison {
  average_delta_us: number;
  faster: "first" | "second" | "equal";
}

export interface RankedLapSelection<T> {
  entry: T;
  stats: LapSelectionStats;
  /** 1-based position by selected average, fastest first. Ties share a rank. */
  rank: number;
  /** Always >= 0: how far this average sits behind the fastest average. */
  delta_to_fastest_us: number;
}

export function isLapSelectable(
  lap: LapSummary,
): lap is LapSummary & { lap_time_us: number } {
  return (
    lap.lap_time_us !== null &&
    Number.isSafeInteger(lap.lap_time_us) &&
    lap.lap_time_us >= 0
  );
}

export function calculateLapSelectionStats(
  laps: readonly LapSummary[],
): LapSelectionStats | null {
  const selected = [...laps]
    .filter(isLapSelectable)
    .sort((left, right) => left.lap_number - right.lap_number);
  if (selected.length === 0) {
    return null;
  }

  const uniqueLapNumbers = new Set<number>();
  for (const lap of selected) {
    if (uniqueLapNumbers.has(lap.lap_number)) {
      throw new Error(`duplicate selected lap number ${lap.lap_number}`);
    }
    uniqueLapNumbers.add(lap.lap_number);
  }

  const times = selected.map((lap) => lap.lap_time_us);
  const total = times.reduce((sum, value) => sum + value, 0);
  const fastest = Math.min(...times);
  const slowest = Math.max(...times);

  return {
    lap_numbers: selected.map((lap) => lap.lap_number),
    lap_count: selected.length,
    average_lap_time_us: Math.round(total / selected.length),
    fastest_lap_time_us: fastest,
    slowest_lap_time_us: slowest,
    spread_us: slowest - fastest,
    quality: {
      deleted: selected.filter((lap) => lap.deleted === true).length,
      inaccurate: selected.filter((lap) => !lap.is_accurate).length,
      pit_transition: selected.filter(
        (lap) => lap.pit_in_time_us !== null || lap.pit_out_time_us !== null,
      ).length,
    },
  };
}

/**
 * Orders any number of selections by their selected average, fastest first,
 * and reports how far each sits behind the fastest. Equal averages share a
 * rank so two identical long runs are not presented as one beating the other.
 */
export function rankLapSelections<T>(
  selections: readonly { entry: T; stats: LapSelectionStats }[],
): RankedLapSelection<T>[] {
  const ordered = [...selections].sort(
    (left, right) =>
      left.stats.average_lap_time_us - right.stats.average_lap_time_us,
  );
  if (ordered.length === 0) {
    return [];
  }

  const fastest = ordered[0].stats.average_lap_time_us;
  let rank = 0;
  let previousAverage: number | null = null;

  return ordered.map((selection, index) => {
    const average = selection.stats.average_lap_time_us;
    if (previousAverage === null || average !== previousAverage) {
      rank = index + 1;
      previousAverage = average;
    }
    return {
      entry: selection.entry,
      stats: selection.stats,
      rank,
      delta_to_fastest_us: average - fastest,
    };
  });
}

export function compareLapSelections(
  first: LapSelectionStats,
  second: LapSelectionStats,
): LapSelectionComparison {
  const averageDelta =
    second.average_lap_time_us - first.average_lap_time_us;
  return {
    average_delta_us: averageDelta,
    faster:
      averageDelta === 0 ? "equal" : averageDelta > 0 ? "first" : "second",
  };
}
