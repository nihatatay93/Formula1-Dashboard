import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import StandingsView from "./StandingsView";
import type {
  ConstructorStandingsResponse,
  DriverStandingsResponse,
} from "../contracts";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    getDriverStandings: vi.fn(),
    getConstructorStandings: vi.fn(),
  };
});

const getDriverStandings = vi.mocked(api.getDriverStandings);
const getConstructorStandings = vi.mocked(api.getConstructorStandings);

function drivers(
  overrides: Partial<DriverStandingsResponse> = {},
): DriverStandingsResponse {
  return {
    season_year: 2026,
    scoring_sessions: 15,
    rounds: [],
    items: [
      {
        position: 1,
        driver_id: "1",
        display_name: "Kimi Antonelli",
        abbreviation: "ANT",
        team_name: "Mercedes",
        team_color: "27F4D2",
        points: "219.000",
        wins: 6,
        podiums: 9,
        poles: 6,
        starts: 15,
        dnfs: 0,
        best_finish: 1,
        rounds: [
          { round_number: 1, session_key: "race", points: "18.000", position: 2 },
          { round_number: 2, session_key: "race", points: "25.000", position: 1 },
        ],
      },
      {
        position: 2,
        driver_id: "2",
        display_name: "Lewis Hamilton",
        abbreviation: "HAM",
        team_name: "Ferrari",
        team_color: "E8002D",
        points: "169.000",
        wins: 1,
        podiums: 5,
        poles: 0,
        starts: 15,
        dnfs: 1,
        best_finish: 1,
        rounds: [],
      },
    ],
    ...overrides,
  };
}

function constructors(
  overrides: Partial<ConstructorStandingsResponse> = {},
): ConstructorStandingsResponse {
  return {
    season_year: 2026,
    scoring_sessions: 15,
    rounds: [],
    items: [
      {
        position: 1,
        team_name: "Mercedes",
        team_color: "27F4D2",
        points: "379.000",
        wins: 8,
        podiums: 14,
        poles: 6,
        best_finish: 1,
        drivers: ["George Russell", "Kimi Antonelli"],
        rounds: [],
      },
    ],
    ...overrides,
  };
}

describe("StandingsView", () => {
  beforeEach(() => {
    getDriverStandings.mockReset();
    getConstructorStandings.mockReset();
    getDriverStandings.mockResolvedValue(drivers());
    getConstructorStandings.mockResolvedValue(constructors());
  });

  it("shows the drivers' championship first", async () => {
    render(<StandingsView year={2026} />);

    expect(await screen.findByText("Kimi Antonelli")).toBeVisible();
    expect(screen.getByText("Lewis Hamilton")).toBeVisible();
    // Trailing zeroes of an exact decimal do not belong on screen.
    expect(screen.getByText("219")).toBeVisible();
  });

  it("renders rows in the order the backend returned", async () => {
    // The backend breaks ties on wins then podiums; re-sorting here would
    // silently change them.
    const { container } = render(<StandingsView year={2026} />);
    await screen.findByText("Kimi Antonelli");

    const names = [...container.querySelectorAll(".standings__name strong")].map(
      (node) => node.textContent,
    );

    expect(names).toEqual(["Kimi Antonelli", "Lewis Hamilton"]);
  });

  it("switches to the constructors' championship", async () => {
    const user = userEvent.setup();
    render(<StandingsView year={2026} />);
    await screen.findByText("Kimi Antonelli");

    await user.click(screen.getByRole("tab", { name: "Constructors" }));

    expect(await screen.findByText("379")).toBeVisible();
    expect(screen.getByText("George Russell, Kimi Antonelli")).toBeVisible();
  });

  it("says how many sessions the table was computed from", async () => {
    // A standing is only as complete as the archive behind it.
    render(<StandingsView year={2026} />);

    expect(
      await screen.findByText(/from 15 scoring sessions/),
    ).toBeVisible();
  });

  it("explains an empty season rather than showing a blank table", async () => {
    getDriverStandings.mockResolvedValue(
      drivers({ items: [], scoring_sessions: 0 }),
    );
    getConstructorStandings.mockResolvedValue(constructors({ items: [] }));

    render(<StandingsView year={2019} />);

    expect(await screen.findByText("Nothing to rank yet")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("surfaces a failure instead of rendering an empty championship", async () => {
    getDriverStandings.mockRejectedValue(
      new api.ApiClientError("The database is unavailable.", "database_unavailable", 503),
    );

    render(<StandingsView year={2026} />);

    expect(await screen.findByText("Standings unavailable")).toBeVisible();
    expect(screen.getByText("The database is unavailable.")).toBeVisible();
    // An error must not be mistaken for a season with no results.
    expect(screen.queryByText("Nothing to rank yet")).toBeNull();
  });

  it("draws a season trend only where there is more than one round", async () => {
    const { container } = render(<StandingsView year={2026} />);
    await screen.findByText("Kimi Antonelli");

    const rows = [...container.querySelectorAll("tbody tr")];

    // Antonelli has two rounds and gets a line; Hamilton has none, and a
    // single point is not a trend.
    expect(rows[0].querySelector("polyline")).not.toBeNull();
    expect(rows[1].querySelector("polyline")).toBeNull();
  });

  it("refetches when the season changes", async () => {
    const { rerender } = render(<StandingsView year={2026} />);
    await screen.findByText("Kimi Antonelli");

    rerender(<StandingsView year={2025} />);

    await waitFor(() =>
      expect(getDriverStandings).toHaveBeenLastCalledWith(2025, expect.anything()),
    );
  });
});

describe("StandingsView against an unexpected response", () => {
  beforeEach(() => {
    getDriverStandings.mockReset();
    getConstructorStandings.mockReset();
  });

  it("survives a 200 whose body has no items", async () => {
    // A proxy returning an empty body, or a version skew, resolves rather than
    // rejects. Guarding only the outer object left `.items.length` to throw
    // and took the whole page down with it.
    getDriverStandings.mockResolvedValue({} as DriverStandingsResponse);
    getConstructorStandings.mockResolvedValue({} as ConstructorStandingsResponse);

    render(<StandingsView year={2026} />);

    expect(await screen.findByText("Nothing to rank yet")).toBeVisible();
  });
});
