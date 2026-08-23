import { describe, expect, it } from "vitest";

import type { RacePaceEntry, RacePaceLap } from "../contracts";
import {
  buildPaceSeries,
  isRaceLike,
  orderByMedian,
  pitStopsOf,
  quantile,
  stintsOf,
  summarizeDistribution,
} from "./racePaceAnalysis";

function lap(overrides: Partial<RacePaceLap> & { lap_number: number }): RacePaceLap {
  return {
    lap_time_us: 90_000_000,
    stint_number: 1,
    pit_in_time_us: null,
    pit_out_time_us: null,
    track_status: "1",
    compound: "MEDIUM",
    tyre_life_laps: 1,
    position: 1,
    is_clean: true,
    is_personal_best: false,
    beyond_cutoff: false,
    ...overrides,
  };
}

function entry(overrides: Partial<RacePaceEntry> = {}): RacePaceEntry {
  return {
    session_entry_id: "1",
    driver_id: "1",
    display_name: "Ada Leader",
    abbreviation: "ADA",
    racing_number: "1",
    team_name: "Example Team",
    team_color_hex: "#27F4D2",
    finishing_position: 1,
    laps: [],
    ...overrides,
  };
}

describe("quantile", () => {
  it("interpolates between ranks", () => {
    // The R type 7 definition, which is what a spreadsheet agrees with.
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([1, 2, 3, 4], 0.25)).toBe(1.75);
    expect(quantile([1, 2, 3, 4], 0.75)).toBe(3.25);
  });

  it("returns the only value of a single-lap set", () => {
    expect(quantile([42], 0.25)).toBe(42);
  });
});

describe("summarizeDistribution", () => {
  it("reports the five-number summary", () => {
    const summary = summarizeDistribution([5, 1, 3, 2, 4]);

    expect(summary).not.toBeNull();
    expect(summary?.minimum).toBe(1);
    expect(summary?.q1).toBe(2);
    expect(summary?.median).toBe(3);
    expect(summary?.q3).toBe(4);
    expect(summary?.maximum).toBe(5);
    expect(summary?.count).toBe(5);
  });

  it("stops each whisker at a real lap, not at the fence", () => {
    // Q1 = 2, Q3 = 4, IQR = 2, so the fences sit at -1 and 7. Nothing is an
    // outlier, and the whiskers must land on 1 and 5 rather than on the fence.
    const summary = summarizeDistribution([1, 2, 3, 4, 5]);

    expect(summary?.lowerWhisker).toBe(1);
    expect(summary?.upperWhisker).toBe(5);
    expect(summary?.outliers).toEqual([]);
  });

  it("draws a lap beyond the fence as an outlier", () => {
    const summary = summarizeDistribution([10, 11, 12, 13, 14, 100]);

    expect(summary?.outliers).toEqual([100]);
    // The whisker retreats to the furthest lap that is not an outlier.
    expect(summary?.upperWhisker).toBe(14);
    // The maximum still reports the real slowest lap: nothing is discarded.
    expect(summary?.maximum).toBe(100);
  });

  it("has no summary without laps", () => {
    expect(summarizeDistribution([])).toBeNull();
  });
});

describe("buildPaceSeries", () => {
  const options = { cleanOnly: false, excludeBeyondCutoff: false };

  it("dashes the second car of a team rather than duplicating a colour", () => {
    const series = buildPaceSeries(
      [
        entry({ session_entry_id: "1", display_name: "First" }),
        entry({ session_entry_id: "2", display_name: "Second" }),
      ],
      options,
    );

    // Team-mates share a team colour exactly, so colour alone cannot tell them
    // apart and the stroke pattern has to.
    expect(series[0].dashed).toBe(false);
    expect(series[1].dashed).toBe(true);
    expect(series[0].color).toBe(series[1].color);
  });

  it("gives an entry with no team colour a neutral stroke", () => {
    const series = buildPaceSeries(
      [entry({ team_color_hex: null })],
      options,
    );

    expect(series[0].color).toBe("#8A8F98");
  });

  it("drops laps with no time", () => {
    const series = buildPaceSeries(
      [
        entry({
          laps: [lap({ lap_number: 1 }), lap({ lap_number: 2, lap_time_us: null })],
        }),
      ],
      options,
    );

    expect(series[0].laps).toHaveLength(1);
  });

  it("keeps every lap until clean-only is asked for", () => {
    const laps = [
      lap({ lap_number: 1, is_clean: false }),
      lap({ lap_number: 2 }),
    ];

    expect(buildPaceSeries([entry({ laps })], options)[0].laps).toHaveLength(2);
    expect(
      buildPaceSeries([entry({ laps })], { ...options, cleanOnly: true })[0]
        .laps,
    ).toHaveLength(1);
  });

  it("excludes laps beyond the cutoff only when asked", () => {
    const laps = [
      lap({ lap_number: 1 }),
      lap({ lap_number: 2, beyond_cutoff: true }),
    ];

    expect(buildPaceSeries([entry({ laps })], options)[0].laps).toHaveLength(2);
    expect(
      buildPaceSeries([entry({ laps })], {
        ...options,
        excludeBeyondCutoff: true,
      })[0].laps,
    ).toHaveLength(1);
  });

  it("summarizes the laps it kept, not the ones it filtered", () => {
    const series = buildPaceSeries(
      [
        entry({
          laps: [
            lap({ lap_number: 1, lap_time_us: 90_000_000 }),
            lap({ lap_number: 2, lap_time_us: 200_000_000, is_clean: false }),
          ],
        }),
      ],
      { ...options, cleanOnly: true },
    );

    expect(series[0].distribution?.median).toBe(90_000_000);
  });
});

describe("orderByMedian", () => {
  it("puts the fastest median first", () => {
    const series = buildPaceSeries(
      [
        entry({
          session_entry_id: "1",
          display_name: "Slower",
          team_color_hex: "#ED1131",
          laps: [lap({ lap_number: 1, lap_time_us: 95_000_000 })],
        }),
        entry({
          session_entry_id: "2",
          display_name: "Faster",
          team_color_hex: "#27F4D2",
          laps: [lap({ lap_number: 1, lap_time_us: 90_000_000 })],
        }),
      ],
      { cleanOnly: false, excludeBeyondCutoff: false },
    );

    expect(orderByMedian(series).map((item) => item.entry.display_name)).toEqual(
      ["Faster", "Slower"],
    );
  });

  it("keeps a driver who set no measurable lap, last", () => {
    const series = buildPaceSeries(
      [
        entry({ session_entry_id: "1", display_name: "No laps" }),
        entry({
          session_entry_id: "2",
          display_name: "Ran",
          team_color_hex: "#ED1131",
          laps: [lap({ lap_number: 1 })],
        }),
      ],
      { cleanOnly: false, excludeBeyondCutoff: false },
    );

    // Present but unranked: a driver who did not run is a fact about the
    // session, not a row to hide.
    expect(orderByMedian(series).map((item) => item.entry.display_name)).toEqual(
      ["Ran", "No laps"],
    );
  });

  it("does not repaint the survivors when the field is filtered", () => {
    const full = buildPaceSeries(
      [
        entry({
          session_entry_id: "1",
          display_name: "Kept",
          team_color_hex: "#ED1131",
          laps: [lap({ lap_number: 1, lap_time_us: 95_000_000 })],
        }),
        entry({
          session_entry_id: "2",
          display_name: "Removed",
          team_color_hex: "#27F4D2",
          laps: [lap({ lap_number: 1, lap_time_us: 90_000_000 })],
        }),
      ],
      { cleanOnly: false, excludeBeyondCutoff: false },
    );
    const kept = full.filter((item) => item.entry.display_name === "Kept");

    // Colour follows the team, never the rank. Dropping the faster car must
    // not recolour the one that remains.
    expect(kept[0].color).toBe("#ED1131");
    expect(orderByMedian(kept)[0].color).toBe("#ED1131");
  });
});

describe("stintsOf", () => {
  it("groups contiguous laps on one set of tyres", () => {
    const stints = stintsOf(
      entry({
        laps: [
          lap({ lap_number: 1, stint_number: 1, compound: "MEDIUM" }),
          lap({ lap_number: 2, stint_number: 1, compound: "MEDIUM" }),
          lap({ lap_number: 3, stint_number: 2, compound: "HARD" }),
        ],
      }),
    );

    expect(stints).toEqual([
      {
        stint_number: 1,
        compound: "MEDIUM",
        first_lap: 1,
        last_lap: 2,
        laps: 2,
      },
      {
        stint_number: 2,
        compound: "HARD",
        first_lap: 3,
        last_lap: 3,
        laps: 1,
      },
    ]);
  });

  it("skips laps with no stint rather than merging them into a neighbour", () => {
    const stints = stintsOf(
      entry({
        laps: [
          lap({ lap_number: 1, stint_number: 1 }),
          lap({ lap_number: 2, stint_number: null }),
          lap({ lap_number: 3, stint_number: 1 }),
        ],
      }),
    );

    // The gap must not split one stint in two: laps 1 and 3 are both stint 1,
    // and nothing in the data says the tyres changed between them.
    expect(stints).toHaveLength(1);
    expect(stints[0]).toMatchObject({ first_lap: 1, last_lap: 3 });
  });

  it("orders by lap number before grouping", () => {
    const stints = stintsOf(
      entry({
        laps: [
          lap({ lap_number: 3, stint_number: 2 }),
          lap({ lap_number: 1, stint_number: 1 }),
          lap({ lap_number: 2, stint_number: 1 }),
        ],
      }),
    );

    expect(stints.map((stint) => stint.first_lap)).toEqual([1, 3]);
  });
});

describe("pitStopsOf", () => {
  it("pairs an entry with the exit on the following lap", () => {
    const stops = pitStopsOf(
      entry({
        laps: [
          lap({ lap_number: 10, pit_in_time_us: 600_000_000 }),
          lap({ lap_number: 11, pit_out_time_us: 624_000_000 }),
        ],
      }),
    );

    // FastF1 records the two instants on different rows, so they have to be
    // stitched together.
    expect(stops).toEqual([
      {
        lap_number: 10,
        pit_lane_us: 24_000_000,
        under_red_flag: false,
        never_rejoined: false,
      },
    ]);
  });

  it("reports a stop whose exit was never recorded", () => {
    const stops = pitStopsOf(
      entry({ laps: [lap({ lap_number: 10, pit_in_time_us: 600_000_000 })] }),
    );

    // The car retired in the pits. The stop happened; its duration is unknown.
    expect(stops).toEqual([
      {
        lap_number: 10,
        pit_lane_us: null,
        under_red_flag: false,
        never_rejoined: false,
      },
    ]);
  });

  it("refuses to time a stop taken while the race was suspended", () => {
    const stops = pitStopsOf(
      entry({
        laps: [
          // "451" is safety car, then red flag, then green.
          lap({
            lap_number: 68,
            pit_in_time_us: 8_905_488_000,
            track_status: "451",
          }),
          lap({
            lap_number: 69,
            pit_out_time_us: 11_059_751_000,
            track_status: "14",
          }),
        ],
      }),
    );

    // Subtracting the instants gives 2154 seconds, which measures the
    // stoppage rather than the stop.
    expect(stops[0].under_red_flag).toBe(true);
    expect(stops[0].pit_lane_us).toBeNull();
  });

  it("finds every stop of a multi-stop race in lap order", () => {
    const stops = pitStopsOf(
      entry({
        laps: [
          lap({ lap_number: 37, pit_in_time_us: 6_162_250_000 }),
          lap({ lap_number: 38, pit_out_time_us: 6_186_328_000 }),
          lap({ lap_number: 61, pit_in_time_us: 8_060_549_000 }),
          lap({ lap_number: 62, pit_out_time_us: 8_087_516_000 }),
        ],
      }),
    );

    expect(stops.map((stop) => stop.lap_number)).toEqual([37, 61]);
    // The real Monaco figures: 24.078s and 26.967s in the pit lane.
    expect(stops[0].pit_lane_us).toBe(24_078_000);
    expect(stops[1].pit_lane_us).toBe(26_967_000);
  });
});

describe("stintsOf lap counts", () => {
  it("counts the laps of each stint", () => {
    const stints = stintsOf(
      entry({
        laps: [
          lap({ lap_number: 1, stint_number: 1 }),
          lap({ lap_number: 2, stint_number: 1 }),
          lap({ lap_number: 3, stint_number: 1 }),
          lap({ lap_number: 4, stint_number: 2 }),
        ],
      }),
    );

    expect(stints.map((stint) => stint.laps)).toEqual([3, 1]);
  });
});

describe("pit stops against real session shapes", () => {
  it("knows which sessions a pit stop means something in", () => {
    expect(isRaceLike("race")).toBe(true);
    expect(isRaceLike("sprint")).toBe(true);
    // A car in practice or qualifying waits in the garage between runs.
    expect(isRaceLike("qualifying")).toBe(false);
    expect(isRaceLike("practice_1")).toBe(false);
    expect(isRaceLike(undefined)).toBe(false);
  });

  it("refuses to time a stop the car never rejoined from", () => {
    // The 2026 Dutch sprint has exactly this: the car entered and stayed in
    // the garage, so subtracting the instants gives 1270 seconds.
    const stops = pitStopsOf(
      entry({
        laps: [
          lap({ lap_number: 20, pit_in_time_us: 1_000_000_000 }),
          lap({ lap_number: 21, pit_out_time_us: 2_270_500_000 }),
        ],
      }),
    );

    expect(stops[0].never_rejoined).toBe(true);
    expect(stops[0].pit_lane_us).toBeNull();
  });

  it("still times a normal stop, including a slow one", () => {
    const stops = pitStopsOf(
      entry({
        laps: [
          lap({ lap_number: 20, pit_in_time_us: 1_000_000_000 }),
          // 66s was the slowest real stop of the Monaco race.
          lap({ lap_number: 21, pit_out_time_us: 1_066_000_000 }),
        ],
      }),
    );

    expect(stops[0].never_rejoined).toBe(false);
    expect(stops[0].pit_lane_us).toBe(66_000_000);
  });

  it("cannot rely on lap adjacency to tell the two apart", () => {
    // FastF1 records the exit on the lap after the entry even when the car
    // stood in the garage, because the lap counter does not advance while it
    // is stationary. Both cases below are adjacent laps.
    const parked = pitStopsOf(
      entry({
        laps: [
          lap({ lap_number: 20, pit_in_time_us: 0 }),
          lap({ lap_number: 21, pit_out_time_us: 1_270_500_000 }),
        ],
      }),
    );
    const served = pitStopsOf(
      entry({
        laps: [
          lap({ lap_number: 20, pit_in_time_us: 0 }),
          lap({ lap_number: 21, pit_out_time_us: 24_000_000 }),
        ],
      }),
    );

    expect(parked[0].pit_lane_us).toBeNull();
    expect(served[0].pit_lane_us).toBe(24_000_000);
  });
});
