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
