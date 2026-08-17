import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ScopeBar from "./ScopeBar";
import type { SeasonOverview } from "./contracts";

type Events = SeasonOverview["events"];

function events(): Events {
  return [
    {
      round_number: 1,
      event_name: "Australian Grand Prix",
      official_name: "FORMULA 1 AUSTRALIAN GRAND PRIX",
      country: "Australia",
      location: "Melbourne",
      event_format: "conventional",
      scheduled_start_at: "2026-03-06T01:30:00Z",
      scheduled_end_at: "2026-03-08T06:00:00Z",
      sessions: [
        {
          id: "5001",
          session_key: "race",
          session_name: "Race",
          scheduled_start_at: "2026-03-08T04:00:00Z",
          scheduled_end_at: "2026-03-08T06:00:00Z",
          ingestion: {
            status: "completed",
            completed_at: "2026-03-08T07:00:00Z",
            attempt_count: 1,
            source: "fastf1_archive",
            record_state: "finalized",
            last_error: null,
            next_earliest_attempt_at: null,
          },
        },
        {
          id: "5002",
          session_key: "qualifying",
          session_name: "Qualifying",
          scheduled_start_at: "2026-03-07T05:00:00Z",
          scheduled_end_at: "2026-03-07T06:00:00Z",
          // Never ingested, so it cannot be analysed.
          ingestion: null,
        },
      ],
    },
  ] as unknown as Events;
}

function renderBar(overrides: Partial<Parameters<typeof ScopeBar>[0]> = {}) {
  const onSelectSession = vi.fn();
  const onSelectDensity = vi.fn();
  render(
    <ScopeBar
      density="comfortable"
      events={events()}
      onSelectDensity={onSelectDensity}
      onSelectSession={onSelectSession}
      selectedSessionId="5001"
      showSession
      year={2026}
      {...overrides}
    />,
  );
  return { onSelectSession, onSelectDensity };
}

describe("ScopeBar", () => {
  it("shows the season in scope", () => {
    renderBar();

    expect(screen.getByText("2026")).toBeVisible();
  });

  it("offers only sessions that have been archived", () => {
    renderBar();
    const options = within(screen.getByRole("combobox")).getAllByRole("option");

    // Qualifying has no snapshot, so choosing it would be a dead end.
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent(/Race/);
  });

  it("changes the session without leaving the view", () => {
    const { onSelectSession } = renderBar();

    expect(screen.getByRole("combobox")).toHaveValue("5001");
    expect(onSelectSession).not.toHaveBeenCalled();
  });

  it("hides the session control where scope has no session", () => {
    renderBar({ showSession: false });

    // Standings describe a whole season; a session choice would change nothing.
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("says so when nothing has been archived yet", () => {
    renderBar({ events: [] as unknown as Events });

    expect(screen.getByText("none archived yet")).toBeVisible();
  });

  it("reports which density is active", () => {
    renderBar({ density: "compact" });

    expect(
      screen.getByRole("button", { name: "Compact" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: "Comfortable" }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("changes density on request", async () => {
    const user = userEvent.setup();
    const { onSelectDensity } = renderBar();

    await user.click(screen.getByRole("button", { name: "Compact" }));

    expect(onSelectDensity).toHaveBeenCalledWith("compact");
  });

  it("can be traversed and operated by keyboard alone", async () => {
    const user = userEvent.setup();
    const { onSelectDensity } = renderBar();

    await user.tab();
    expect(screen.getByRole("combobox")).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "Comfortable" })).toHaveFocus();

    await user.tab();
    const compact = screen.getByRole("button", { name: "Compact" });
    expect(compact).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(onSelectDensity).toHaveBeenCalledWith("compact");
  });
});
