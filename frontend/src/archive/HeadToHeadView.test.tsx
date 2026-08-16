import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type {
  ConsistencyResponse,
  DriverStandingsResponse,
  HeadToHeadResponse,
} from "../contracts";
import HeadToHeadView from "./HeadToHeadView";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    getDriverStandings: vi.fn(),
    getConsistency: vi.fn(),
    getHeadToHead: vi.fn(),
  };
});

const getDriverStandings = vi.mocked(api.getDriverStandings);
const getConsistency = vi.mocked(api.getConsistency);
const getHeadToHead = vi.mocked(api.getHeadToHead);

function standings(): DriverStandingsResponse {
  return {
    season_year: 2026,
    scoring_sessions: 11,
    rounds: [],
    items: [
      {
        position: 1,
        driver_id: "1",
        display_name: "Lando Norris",
        abbreviation: "NOR",
        team_name: "McLaren",
        team_color: "F47600",
        points: "128.000",
        wins: 1,
        podiums: 3,
        poles: 1,
        starts: 11,
        dnfs: 3,
        best_finish: 1,
        rounds: [],
      },
      {
        position: 2,
        driver_id: "2",
        display_name: "Oscar Piastri",
        abbreviation: "PIA",
        team_name: "McLaren",
        team_color: "F47600",
        points: "92.000",
        wins: 0,
        podiums: 2,
        poles: 0,
        starts: 11,
        dnfs: 3,
        best_finish: 2,
        rounds: [],
      },
      {
        position: 3,
        driver_id: "3",
        display_name: "Lewis Hamilton",
        abbreviation: "HAM",
        team_name: "Ferrari",
        team_color: "ED1131",
        points: "80.000",
        wins: 0,
        podiums: 1,
        poles: 0,
        starts: 11,
        dnfs: 0,
        best_finish: 2,
        rounds: [],
      },
    ],
  };
}

function comparison(
  overrides: Partial<HeadToHeadResponse> = {},
): HeadToHeadResponse {
  return {
    season_year: 2026,
    driver_a: {
      driver_id: "1",
      display_name: "Lando Norris",
      abbreviation: "NOR",
      team_name: "McLaren",
      team_color_hex: "#F47600",
    },
    driver_b: {
      driver_id: "2",
      display_name: "Oscar Piastri",
      abbreviation: "PIA",
      team_name: "McLaren",
      team_color_hex: "#F47600",
    },
    qualifying: {
      basis: "Finishing position in the qualifying session.",
      a_ahead: 7,
      b_ahead: 4,
      compared: 11,
      excluded: 0,
    },
    race: {
      basis: "Finishing position in the race, counting only races both classified.",
      a_ahead: 3,
      b_ahead: 3,
      compared: 6,
      excluded: 5,
    },
    totals_a: {
      points: "128.000",
      wins: 1,
      podiums: 3,
      poles: 1,
      starts: 11,
      dnfs: 3,
      best_finish: 1,
    },
    totals_b: {
      points: "92.000",
      wins: 0,
      podiums: 2,
      poles: 0,
      starts: 11,
      dnfs: 3,
      best_finish: 2,
    },
    never_met: false,
    ...overrides,
  };
}

function consistency(
  overrides: Partial<ConsistencyResponse> = {},
): ConsistencyResponse {
  return {
    season_year: 2026,
    clean_lap_definition: "A lap is clean when the track was green.",
    basis: "Race sessions only, as a percentage of the best clean lap.",
    items: [
      {
        driver_id: "1",
        display_name: "Lando Norris",
        abbreviation: "NOR",
        team_name: "McLaren",
        team_color_hex: "#F47600",
        clean_laps: 477,
        median_percent: 102.21,
        std_dev_percent: 1.21,
        iqr_percent: 1.73,
        races_started: 11,
        races_classified: 9,
        finish_rate: 0.8182,
      },
      {
        driver_id: "9",
        display_name: "Never Ran",
        abbreviation: "NEV",
        team_name: "Cadillac",
        team_color_hex: null,
        clean_laps: 0,
        median_percent: null,
        std_dev_percent: null,
        iqr_percent: null,
        races_started: 1,
        races_classified: 0,
        finish_rate: 0,
      },
    ],
    ...overrides,
  };
}

describe("HeadToHeadView", () => {
  beforeEach(() => {
    getDriverStandings.mockReset();
    getConsistency.mockReset();
    getHeadToHead.mockReset();
    getDriverStandings.mockResolvedValue(standings());
    getConsistency.mockResolvedValue(consistency());
    getHeadToHead.mockResolvedValue(comparison());
  });

  it("opens on the leading pair", async () => {
    render(<HeadToHeadView year={2026} />);

    await waitFor(() =>
      expect(getHeadToHead).toHaveBeenCalledWith(
        2026,
        "1",
        "2",
        expect.anything(),
      ),
    );
  });

  it("shows both the count and the share for each metric", async () => {
    render(<HeadToHeadView year={2026} />);

    // A 3-1 record and a 30-10 record have the same shape, so the count has
    // to appear beside the bar.
    expect(await screen.findByText("7–4")).toBeVisible();
    expect(screen.getByText(/from 11 sessions/)).toBeVisible();
    expect(screen.getByText(/NOR 64%/)).toBeVisible();
  });

  it("reports how many sessions could not be compared", async () => {
    render(<HeadToHeadView year={2026} />);

    // Five races were excluded because one of them was not classified. That
    // must be visible, not silently folded into the record.
    expect(await screen.findByText("5 excluded")).toBeVisible();
  });

  it("carries the backend's basis for each record", async () => {
    render(<HeadToHeadView year={2026} />);

    expect(
      await screen.findByText(/counting only races both classified/),
    ).toBeVisible();
  });

  it("offers team-mates as a quick-select", async () => {
    const user = userEvent.setup();
    render(<HeadToHeadView year={2026} />);
    await screen.findByText("7–4");

    // Only McLaren has two drivers in the fixture; Ferrari has one, so it is
    // not a pair and must not be offered.
    const mates = screen.getByText("Team-mates").parentElement;
    expect(within(mates as HTMLElement).getByText("McLaren")).toBeVisible();
    expect(within(mates as HTMLElement).queryByText("Ferrari")).toBeNull();

    await user.click(within(mates as HTMLElement).getByText("McLaren"));

    await waitFor(() =>
      expect(getHeadToHead).toHaveBeenLastCalledWith(
        2026,
        "1",
        "2",
        expect.anything(),
      ),
    );
  });

  it("refetches when a different driver is chosen", async () => {
    const user = userEvent.setup();
    render(<HeadToHeadView year={2026} />);
    await screen.findByText("7–4");

    await user.selectOptions(screen.getAllByRole("combobox")[1], "3");

    await waitFor(() =>
      expect(getHeadToHead).toHaveBeenLastCalledWith(
        2026,
        "1",
        "3",
        expect.anything(),
      ),
    );
  });

  it("explains a pair who never shared a session", async () => {
    getHeadToHead.mockResolvedValue(
      comparison({
        never_met: true,
        qualifying: {
          basis: "x",
          a_ahead: 0,
          b_ahead: 0,
          compared: 0,
          excluded: 0,
        },
        race: { basis: "x", a_ahead: 0, b_ahead: 0, compared: 0, excluded: 0 },
      }),
    );

    render(<HeadToHeadView year={2026} />);

    // Zeroes, not an error, and not an empty bar chart implying a 0-0 draw.
    expect(
      await screen.findByText("These two never shared a session"),
    ).toBeVisible();
  });

  it("never asks the backend to compare a driver with themselves", async () => {
    const user = userEvent.setup();
    render(<HeadToHeadView year={2026} />);
    await screen.findByText("7–4");
    getHeadToHead.mockClear();

    await user.selectOptions(screen.getAllByRole("combobox")[1], "1");

    expect(await screen.findByText(/Pick two different drivers/)).toBeVisible();
    expect(getHeadToHead).not.toHaveBeenCalled();
  });

  it("ranks the consistency table and keeps an unmeasured driver last", async () => {
    const user = userEvent.setup();
    const { container } = render(<HeadToHeadView year={2026} />);
    await screen.findByText("7–4");

    await user.click(screen.getByRole("tab", { name: "Consistency" }));

    const names = [
      ...container.querySelectorAll(".consistency__name strong"),
    ].map((node) => node.textContent);
    expect(names).toEqual(["Lando Norris", "Never Ran"]);

    // No clean lap means no spread, which must not read as a rank.
    expect(screen.getByText("no clean lap")).toBeVisible();
  });

  it("states what the consistency percentages are relative to", async () => {
    const user = userEvent.setup();
    render(<HeadToHeadView year={2026} />);
    await screen.findByText("7–4");

    await user.click(screen.getByRole("tab", { name: "Consistency" }));

    expect(
      screen.getByText(/percentage of the best clean lap/),
    ).toBeVisible();
  });

  it("explains a season with too few drivers", async () => {
    getDriverStandings.mockResolvedValue({ ...standings(), items: [] });

    render(<HeadToHeadView year={2019} />);

    expect(
      await screen.findByText("Not enough drivers to compare"),
    ).toBeVisible();
  });

  it("surfaces a failure rather than an empty comparison", async () => {
    getDriverStandings.mockRejectedValue(
      new api.ApiClientError(
        "The database is unavailable.",
        "database_unavailable",
        503,
      ),
    );

    render(<HeadToHeadView year={2026} />);

    expect(await screen.findByText("Comparison unavailable")).toBeVisible();
    expect(screen.queryByText("Not enough drivers to compare")).toBeNull();
  });

  it("survives a standings response with no items field", async () => {
    // A 200 with an unexpected body must leave an explanation, not a crash.
    getDriverStandings.mockResolvedValue({} as DriverStandingsResponse);
    getConsistency.mockResolvedValue({} as ConsistencyResponse);

    render(<HeadToHeadView year={2026} />);

    expect(
      await screen.findByText("Not enough drivers to compare"),
    ).toBeVisible();
  });
});
