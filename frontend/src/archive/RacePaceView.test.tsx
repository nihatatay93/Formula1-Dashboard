import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { RacePaceLap, RacePaceResponse } from "../contracts";
import RacePaceView from "./RacePaceView";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, getRacePace: vi.fn() };
});

const getRacePace = vi.mocked(api.getRacePace);

function lap(
  overrides: Partial<RacePaceLap> & { lap_number: number },
): RacePaceLap {
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

function response(
  overrides: Partial<RacePaceResponse> = {},
): RacePaceResponse {
  return {
    session_id: "821",
    session_key: "race",
    snapshot: {
      data_available: true,
      source: "fastf1_archive",
      record_state: "finalized",
      completed_at: "2026-06-07T15:00:00Z",
      source_updated_at: "2026-06-07T15:00:00Z",
    },
    filters: { clean_only: false, outlier_cutoff: 107 },
    clean_lap_definition: "A lap is clean when the track was green.",
    session_best_lap_time_us: 90_000_000,
    outlier_cutoff_lap_time_us: 96_300_000,
    items: [
      {
        session_entry_id: "1",
        driver_id: "1",
        display_name: "Kimi Antonelli",
        abbreviation: "ANT",
        racing_number: "12",
        team_name: "Mercedes",
        team_color_hex: "#00D7B6",
        finishing_position: 1,
        laps: [
          lap({ lap_number: 1, lap_time_us: 99_000_000, is_clean: false }),
          lap({ lap_number: 2, lap_time_us: 90_000_000 }),
          lap({ lap_number: 3, lap_time_us: 91_000_000 }),
        ],
      },
      {
        session_entry_id: "2",
        driver_id: "2",
        display_name: "Lewis Hamilton",
        abbreviation: "HAM",
        racing_number: "44",
        team_name: "Ferrari",
        team_color_hex: "#ED1131",
        finishing_position: 2,
        laps: [
          lap({ lap_number: 2, lap_time_us: 94_000_000 }),
          lap({ lap_number: 3, lap_time_us: 95_000_000 }),
        ],
      },
    ],
    ...overrides,
  };
}

describe("RacePaceView", () => {
  beforeEach(() => {
    getRacePace.mockReset();
    getRacePace.mockResolvedValue(response());
  });

  it("draws a row per driver, ordered by median", async () => {
    const { container } = render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);

    const names = [...container.querySelectorAll(".pace-box__name")].map(
      (node) => node.textContent,
    );

    expect(names).toEqual(["Kimi Antonelli", "Lewis Hamilton"]);
  });

  it("changes the lap count when clean laps only is toggled", async () => {
    const user = userEvent.setup();
    render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);

    // Clean-only is the default, so Antonelli's out lap is excluded.
    expect(screen.getByText(/4 laps from 2 drivers/)).toBeVisible();

    await user.click(screen.getByRole("checkbox", { name: /clean laps only/i }));

    expect(await screen.findByText(/5 laps from 2 drivers/)).toBeVisible();
  });

  it("filters without asking the backend again", async () => {
    const user = userEvent.setup();
    render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);
    expect(getRacePace).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("checkbox", { name: /clean laps only/i }));
    await screen.findByText(/5 laps from 2 drivers/);

    // Every lap arrives once, flagged. A toggle must redraw, not refetch.
    expect(getRacePace).toHaveBeenCalledTimes(1);
  });

  it("refetches when the cutoff moves, because the backend computes it", async () => {
    render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);

    // The cutoff is a backend query parameter, so moving it must refetch.
    fireEvent.change(screen.getByRole("slider"), { target: { value: "110" } });

    await waitFor(() =>
      expect(getRacePace).toHaveBeenCalledWith(
        "821",
        expect.objectContaining({ outlierCutoff: 110 }),
      ),
    );
  });

  it("names the whisker rule beside the box plot", async () => {
    const { container } = render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);

    // A box plot without its rule stated is not readable.
    expect(container.querySelector(".pace-box__caption")?.textContent).toMatch(
      /1.5x the interquartile range/,
    );
  });

  it("carries the backend's definition of a clean lap", async () => {
    render(<RacePaceView sessionId="821" />);

    // One definition, computed server-side, shown rather than re-worded here.
    expect(
      await screen.findByText("A lap is clean when the track was green."),
    ).toBeVisible();
  });

  it("explains a session with no clean laps instead of drawing nothing", async () => {
    getRacePace.mockResolvedValue(
      response({
        items: [
          {
            session_entry_id: "1",
            driver_id: "1",
            display_name: "Kimi Antonelli",
            abbreviation: "ANT",
            racing_number: "12",
            team_name: "Mercedes",
            team_color_hex: "#00D7B6",
            finishing_position: 1,
            laps: [lap({ lap_number: 1, is_clean: false })],
          },
        ],
      }),
    );

    render(<RacePaceView sessionId="821" />);

    expect(await screen.findByText("No laps to compare")).toBeVisible();
    // The way out is offered, not just the dead end.
    expect(screen.getByText(/Turn off clean laps only/)).toBeVisible();
  });

  it("surfaces a failure rather than an empty chart", async () => {
    getRacePace.mockRejectedValue(
      new api.ApiClientError(
        "The database is unavailable.",
        "database_unavailable",
        503,
      ),
    );

    render(<RacePaceView sessionId="821" />);

    expect(await screen.findByText("Race pace unavailable")).toBeVisible();
    expect(screen.queryByText("No laps to compare")).toBeNull();
  });

  it("never joins two laps that did not follow each other", async () => {
    getRacePace.mockResolvedValue(
      response({
        items: [
          {
            session_entry_id: "1",
            driver_id: "1",
            display_name: "Kimi Antonelli",
            abbreviation: "ANT",
            racing_number: "12",
            team_name: "Mercedes",
            team_color_hex: "#00D7B6",
            finishing_position: 1,
            laps: [
              lap({ lap_number: 1 }),
              lap({ lap_number: 2 }),
              // Laps 3 to 9 were not clean; 10 must start a new stroke.
              lap({ lap_number: 10 }),
              lap({ lap_number: 11 }),
            ],
          },
        ],
      }),
    );

    const { container } = render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);

    const strokes = container.querySelectorAll(".pace-evolution__line");

    // Two runs, two strokes. One stroke would draw pace across laps 3 to 9
    // that the driver never set.
    expect(strokes).toHaveLength(2);
  });

  it("labels every driver in the distribution, not colour alone", async () => {
    const { container } = render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);

    const rows = [...container.querySelectorAll(".pace-box__row")];

    // Several real team colours are close enough to be indistinguishable, and
    // team-mates share one exactly, so every row carries its own name.
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.querySelector(".pace-box__name")?.textContent).toMatch(/\S/);
      expect(row.querySelector(".pace-box__code")?.textContent).toMatch(/\S/);
    }
  });
});

describe("RacePaceView strategy tab", () => {
  beforeEach(() => {
    getRacePace.mockReset();
    getRacePace.mockResolvedValue(
      response({
        items: [
          {
            session_entry_id: "1",
            driver_id: "1",
            display_name: "Kimi Antonelli",
            abbreviation: "ANT",
            racing_number: "12",
            team_name: "Mercedes",
            team_color_hex: "#00D7B6",
            finishing_position: 1,
            laps: [
              lap({ lap_number: 1, stint_number: 1, compound: "MEDIUM" }),
              lap({
                lap_number: 2,
                stint_number: 1,
                compound: "MEDIUM",
                pit_in_time_us: 600_000_000,
              }),
              lap({
                lap_number: 3,
                stint_number: 2,
                compound: "HARD",
                pit_out_time_us: 624_000_000,
              }),
              lap({ lap_number: 4, stint_number: 2, compound: "HARD" }),
            ],
          },
        ],
      }),
    );
  });

  async function openStrategy() {
    const user = userEvent.setup();
    const view = render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);
    await user.click(screen.getByRole("tab", { name: "Strategy" }));
    return view;
  }

  it("draws one segment per stint", async () => {
    const { container } = await openStrategy();

    // Stint boundaries follow stint_number, not compound: two stints on the
    // same compound must still be two segments.
    expect(container.querySelectorAll(".strategy__stint")).toHaveLength(2);
  });

  it("marks the pit entry", async () => {
    const { container } = await openStrategy();

    expect(container.querySelectorAll(".strategy__stop")).toHaveLength(1);
  });

  it("uses the tyre palette rather than a driver colour", async () => {
    const { container } = await openStrategy();
    const fills = [...container.querySelectorAll(".strategy__stint")].map(
      (node) => node.getAttribute("fill"),
    );

    // Compound colour belongs to the tyre; the driver's name carries identity.
    expect(fills).toEqual(["var(--tyre-medium)", "var(--tyre-hard)"]);
  });

  it("reports pit-lane time, and says that is what it is", async () => {
    await openStrategy();

    expect(screen.getByText("24.000s")).toBeVisible();
    // The caveat must be on screen, not only in a code comment: pit-lane time
    // is about twenty seconds longer than the televised figure.
    expect(screen.getByText(/Pit-lane time, not stop time/)).toBeVisible();
    expect(
      screen.getByText(/roughly twenty seconds shorter/),
    ).toBeVisible();
  });

  it("does not time a stop taken while the race was suspended", async () => {
    getRacePace.mockResolvedValue(
      response({
        items: [
          {
            session_entry_id: "1",
            driver_id: "1",
            display_name: "Kimi Antonelli",
            abbreviation: "ANT",
            racing_number: "12",
            team_name: "Mercedes",
            team_color_hex: "#00D7B6",
            finishing_position: 1,
            laps: [
              lap({
                lap_number: 68,
                stint_number: 4,
                pit_in_time_us: 8_905_488_000,
                track_status: "451",
              }),
              lap({
                lap_number: 69,
                stint_number: 5,
                pit_out_time_us: 11_059_751_000,
                track_status: "14",
              }),
            ],
          },
        ],
      }),
    );

    const user = userEvent.setup();
    render(<RacePaceView sessionId="821" />);
    await screen.findByText(/session best/);
    await user.click(screen.getByRole("tab", { name: "Strategy" }));

    // 2154 seconds is the length of the stoppage, not of a pit stop.
    expect(screen.getByText("race suspended")).toBeVisible();
    expect(screen.queryByText(/2154/)).toBeNull();
  });

  it("keeps the pace charts out of the strategy tab", async () => {
    const { container } = await openStrategy();

    expect(container.querySelector(".pace-evolution")).toBeNull();
    expect(container.querySelector(".pace-box")).toBeNull();
  });
});
