import { describe, expect, it } from "vitest";

import type { LapSummary } from "./contracts";
import {
  calculateLapSelectionStats,
  compareLapSelections,
  isLapSelectable,
} from "./lapAnalysis";
import { firstLapPage } from "./test/fixtures";

function selectedLap(
  lapNumber: number,
  lapTimeUs: number | null,
  overrides: Partial<LapSummary> = {},
): LapSummary {
  return {
    ...firstLapPage.items[0],
    id: String(8_000 + lapNumber),
    lap_number: lapNumber,
    lap_time_us: lapTimeUs,
    ...overrides,
  };
}

describe("lap analysis", () => {
  it("calculates deterministic selected-lap pace and quality facts", () => {
    const stats = calculateLapSelectionStats([
      selectedLap(8, 90_500_000, { pit_in_time_us: 700_000_000 }),
      selectedLap(6, 89_500_000, { deleted: true }),
      selectedLap(7, 90_000_000, { is_accurate: false }),
    ]);

    expect(stats).toEqual({
      lap_numbers: [6, 7, 8],
      lap_count: 3,
      average_lap_time_us: 90_000_000,
      fastest_lap_time_us: 89_500_000,
      slowest_lap_time_us: 90_500_000,
      spread_us: 1_000_000,
      quality: {
        deleted: 1,
        inaccurate: 1,
        pit_transition: 1,
      },
    });
  });

  it("ignores untimed laps and returns null without a usable selection", () => {
    const untimed = selectedLap(1, null);

    expect(isLapSelectable(untimed)).toBe(false);
    expect(calculateLapSelectionStats([untimed])).toBeNull();
  });

  it("rejects duplicate lap numbers instead of double-counting", () => {
    expect(() =>
      calculateLapSelectionStats([
        selectedLap(4, 90_000_000),
        selectedLap(4, 91_000_000),
      ]),
    ).toThrow("duplicate selected lap number 4");
  });

  it("compares the second average to the first with a signed delta", () => {
    const first = calculateLapSelectionStats([
      selectedLap(1, 90_000_000),
      selectedLap(2, 92_000_000),
    ]);
    const second = calculateLapSelectionStats([
      selectedLap(1, 90_500_000),
      selectedLap(2, 91_000_000),
    ]);

    expect(first).not.toBeNull();
    expect(second).not.toBeNull();
    expect(compareLapSelections(first!, second!)).toEqual({
      average_delta_us: -250_000,
      faster: "second",
    });
  });
});
