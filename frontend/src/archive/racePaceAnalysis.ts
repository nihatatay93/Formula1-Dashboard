import type { RacePaceEntry, RacePaceLap } from "../contracts";

/**
 * Distribution statistics for race pace.
 *
 * A box plot is only honest if its rules are stated, so both are fixed here and
 * named in the chart's caption: quartiles by linear interpolation between
 * closest ranks (the R type 7 definition, and the one a spreadsheet agrees
 * with), and Tukey whiskers reaching the furthest lap within 1.5 x IQR of the
 * quartiles. Laps outside a whisker are drawn individually rather than hidden.
 */

export interface PaceDistribution {
  count: number;
  minimum: number;
  q1: number;
  median: number;
  q3: number;
  maximum: number;
  mean: number;
  /** Furthest lap still within 1.5 x IQR below Q1. */
  lowerWhisker: number;
  /** Furthest lap still within 1.5 x IQR above Q3. */
  upperWhisker: number;
  /** Laps beyond a whisker, ascending. Drawn, never dropped. */
  outliers: number[];
}

export const WHISKER_RULE =
  "Box spans the middle half of the laps, the line is the median, and the " +
  "whiskers reach the furthest lap within 1.5x the interquartile range. " +
  "Laps beyond a whisker are drawn as points.";

/**
 * The quantile at `fraction` of already-sorted values, interpolating between
 * ranks. Exported for the tests that pin the definition.
 */
export function quantile(sorted: readonly number[], fraction: number): number {
  if (sorted.length === 0) {
    throw new Error("quantile of an empty set is undefined");
  }
  if (sorted.length === 1) {
    return sorted[0];
  }
  const position = (sorted.length - 1) * fraction;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) {
    return sorted[lower];
  }
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

export function summarizeDistribution(
  times: readonly number[],
): PaceDistribution | null {
  if (times.length === 0) {
    return null;
  }
  const sorted = [...times].sort((left, right) => left - right);
  const q1 = quantile(sorted, 0.25);
  const median = quantile(sorted, 0.5);
  const q3 = quantile(sorted, 0.75);
  const reach = (q3 - q1) * 1.5;
  const lowerFence = q1 - reach;
  const upperFence = q3 + reach;

  // The whisker stops at real data, never at the fence itself: drawing to the
  // fence would invent a lap nobody set.
  const withinLower = sorted.find((value) => value >= lowerFence);
  const withinUpper = [...sorted].reverse().find((value) => value <= upperFence);

  return {
    count: sorted.length,
    minimum: sorted[0],
    q1,
    median,
    q3,
    maximum: sorted[sorted.length - 1],
    mean:
      sorted.reduce((total, value) => total + value, 0) / sorted.length,
    lowerWhisker: withinLower ?? sorted[0],
    upperWhisker: withinUpper ?? sorted[sorted.length - 1],
    outliers: sorted.filter(
      (value) => value < lowerFence || value > upperFence,
    ),
  };
}

export interface PaceSeries {
  entry: RacePaceEntry;
  /** The team's own colour, or a neutral stroke when the archive has none. */
  color: string;
  /** Team-mates share a colour exactly, so the second of a pair is dashed. */
  dashed: boolean;
  laps: (RacePaceLap & { lap_time_us: number })[];
  distribution: PaceDistribution | null;
}

const NO_TEAM_COLOR = "#8A8F98";

export interface SeriesOptions {
  cleanOnly: boolean;
  /** Drop laps flagged beyond the cutoff before measuring. */
  excludeBeyondCutoff: boolean;
}

export function buildPaceSeries(
  entries: readonly RacePaceEntry[],
  options: SeriesOptions,
): PaceSeries[] {
  const usedColors = new Set<string>();

  return entries.map((entry) => {
    const color = entry.team_color_hex ?? NO_TEAM_COLOR;
    const dashed = usedColors.has(color);
    usedColors.add(color);

    const laps = entry.laps.filter(
      (lap): lap is RacePaceLap & { lap_time_us: number } =>
        lap.lap_time_us !== null &&
        (!options.cleanOnly || lap.is_clean) &&
        (!options.excludeBeyondCutoff || !lap.beyond_cutoff),
    );

    return {
      entry,
      color,
      dashed,
      laps,
      distribution: summarizeDistribution(laps.map((lap) => lap.lap_time_us)),
    };
  });
}

/**
 * Orders series by median pace, fastest first, with drivers who set no
 * measurable lap last. Ordering is by the statistic on screen, so the box plot
 * reads top to bottom as a pace ranking.
 */
export function orderByMedian(series: readonly PaceSeries[]): PaceSeries[] {
  return [...series].sort((left, right) => {
    if (left.distribution === null && right.distribution === null) {
      return left.entry.display_name.localeCompare(right.entry.display_name);
    }
    if (left.distribution === null) {
      return 1;
    }
    if (right.distribution === null) {
      return -1;
    }
    return left.distribution.median - right.distribution.median;
  });
}

/**
 * Splits a driver's laps into runs of consecutive lap numbers.
 *
 * A line may only be drawn through laps that actually follow one another.
 * Filtering out pit and safety-car laps leaves gaps, and joining across one
 * would draw a straight segment through pace the driver never set -- at Monaco
 * that produced single strokes spanning sixteen missing laps.
 */
export function contiguousRuns<T extends { lap_number: number }>(
  laps: readonly T[],
): T[][] {
  const runs: T[][] = [];
  for (const lap of [...laps].sort(
    (left, right) => left.lap_number - right.lap_number,
  )) {
    const current = runs[runs.length - 1];
    if (
      current !== undefined &&
      lap.lap_number === current[current.length - 1].lap_number + 1
    ) {
      current.push(lap);
      continue;
    }
    runs.push([lap]);
  }
  return runs;
}

export interface Stint {
  stint_number: number;
  compound: string | null;
  first_lap: number;
  last_lap: number;
  /** Laps on this set of tyres, counted from the laps actually present. */
  laps: number;
}

/** FastF1's code for a red flag, within a lap's concatenated track status. */
const RED_FLAG = "5";

/**
 * Sessions where entering the pit lane means serving a stop.
 *
 * In practice and qualifying a car comes in and waits in the garage between
 * runs, so the interval between entering and leaving is garage time, not a
 * pit-lane transit. Measured across the 2026 Dutch Grand Prix weekend the
 * median was 259s in practice and 185s in qualifying, against 24s across
 * seventy stops of the Monaco race.
 */
export const RACE_LIKE_SESSIONS = new Set(["race", "sprint"]);

export function isRaceLike(sessionKey: string | undefined): boolean {
  return RACE_LIKE_SESSIONS.has((sessionKey ?? "").toLowerCase());
}

/**
 * The longest interval still credible as one pit-lane transit.
 *
 * A stop is a drive in, the stationary work and a drive out, and even a car
 * serving a penalty clears the lane well inside this. Beyond it the car was
 * parked: the 2026 Dutch sprint has an entry that computes to 1270s because
 * the car went into the garage and stayed there.
 */
export const PIT_LANE_CEILING_US = 120_000_000;

export interface PitStop {
  /** The lap the car entered the pit lane on. */
  lap_number: number;
  /**
   * Time between entering and leaving the pit lane, when both instants are
   * known and the lap was racing. This is pit-lane time, not the stationary
   * time a broadcast quotes: it includes the drive in and out and runs
   * roughly twenty seconds longer.
   */
  pit_lane_us: number | null;
  /**
   * The car was in the pit lane while the race was suspended, so the time it
   * spent there measures the stoppage rather than the stop. In the 2026
   * Monaco race the sixteen red-flagged entries all read between 2023 and
   * 2158 seconds, against 19 to 66 for the seventy that were not.
   */
  under_red_flag: boolean;
  /**
   * The car entered but did not rejoin promptly, so the interval measures how
   * long it stood in the garage. Reported rather than dropped: the entry
   * happened, and a retirement into the pits is worth seeing.
   */
  never_rejoined: boolean;
}

/**
 * Every pit entry, paired with the following exit.
 *
 * FastF1 records the entry on the lap the car came in and the exit on the next
 * lap, so the two live on different rows and have to be stitched together. A
 * final entry with no exit -- the car retired in the pits -- is reported with
 * no duration rather than dropped, because the stop still happened.
 */
export function pitStopsOf(entry: RacePaceEntry): PitStop[] {
  const ordered = [...entry.laps].sort(
    (left, right) => left.lap_number - right.lap_number,
  );
  const stops: PitStop[] = [];

  for (const [index, lap] of ordered.entries()) {
    if (lap.pit_in_time_us === null) {
      continue;
    }
    const exit = ordered.slice(index + 1).find(
      (candidate) => candidate.pit_out_time_us !== null,
    );
    const underRedFlag =
      (lap.track_status ?? "").includes(RED_FLAG) ||
      (exit?.track_status ?? "").includes(RED_FLAG);
    const elapsed =
      exit?.pit_out_time_us != null
        ? exit.pit_out_time_us - lap.pit_in_time_us
        : null;
    // Adjacency cannot be used here: FastF1 records the exit on the lap after
    // the entry even when the car stood in the garage for twenty minutes,
    // because the lap counter does not advance while it is stationary.
    const neverRejoined =
      elapsed !== null && !underRedFlag && elapsed > PIT_LANE_CEILING_US;

    stops.push({
      lap_number: lap.lap_number,
      // A suspension, or a car that parked, is reported as no duration rather
      // than as the many-minute figure subtracting the instants would give.
      pit_lane_us:
        elapsed !== null && !underRedFlag && !neverRejoined ? elapsed : null,
      under_red_flag: underRedFlag,
      never_rejoined: neverRejoined,
    });
  }
  return stops;
}

/**
 * Contiguous runs on one set of tyres, for shading behind the evolution chart.
 * A lap carrying no stint number neither opens a stint nor closes one: it is
 * passed over, so a run either side of it stays a single stint rather than
 * gaining a boundary the data does not actually record.
 */
export function stintsOf(entry: RacePaceEntry): Stint[] {
  const stints: Stint[] = [];
  for (const lap of [...entry.laps].sort(
    (left, right) => left.lap_number - right.lap_number,
  )) {
    if (lap.stint_number === null) {
      continue;
    }
    const current = stints[stints.length - 1];
    if (current !== undefined && current.stint_number === lap.stint_number) {
      current.last_lap = lap.lap_number;
      current.laps += 1;
      continue;
    }
    stints.push({
      stint_number: lap.stint_number,
      compound: lap.compound,
      first_lap: lap.lap_number,
      last_lap: lap.lap_number,
      laps: 1,
    });
  }
  return stints;
}
