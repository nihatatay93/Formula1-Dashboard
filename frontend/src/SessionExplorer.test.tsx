import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import SessionExplorer from "./SessionExplorer";
import {
  completedEvent,
  completedSession,
  completedSnapshot,
  firstLapPage,
  secondLapPage,
  sessionDetail,
  sessionResults,
  unavailableSessionDetail,
} from "./test/fixtures";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    getSessionDetail: vi.fn(),
    getSessionLaps: vi.fn(),
    getSessionResults: vi.fn(),
  };
});

const getSessionDetail = vi.mocked(api.getSessionDetail);
const getSessionResults = vi.mocked(api.getSessionResults);
const getSessionLaps = vi.mocked(api.getSessionLaps);

function renderExplorer(onClose = vi.fn()) {
  render(
    <SessionExplorer
      event={completedEvent}
      onClose={onClose}
      session={completedSession}
    />,
  );
  return onClose;
}

describe("SessionExplorer", () => {
  beforeEach(() => {
    getSessionDetail.mockReset();
    getSessionResults.mockReset();
    getSessionLaps.mockReset();
  });

  it("shows an unavailable snapshot without requesting results", async () => {
    getSessionDetail.mockResolvedValue(unavailableSessionDetail);

    renderExplorer();

    expect(
      await screen.findByText("This session has no completed archive snapshot yet."),
    ).toBeInTheDocument();
    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(getSessionResults).not.toHaveBeenCalled();
    expect(getSessionLaps).not.toHaveBeenCalled();
  });

  it("renders results, selects a participant, and appends lap pages", async () => {
    const user = userEvent.setup();
    getSessionDetail.mockResolvedValue(sessionDetail);
    getSessionResults.mockResolvedValue(sessionResults);
    getSessionLaps
      .mockResolvedValueOnce(firstLapPage)
      .mockResolvedValueOnce(secondLapPage);

    renderExplorer();

    const norrisRow = await screen.findByRole("row", { name: /Lando Norris/ });
    await user.click(within(norrisRow).getByRole("button", { name: "View laps" }));

    expect(await screen.findByText("2 laps loaded")).toBeInTheDocument();
    const firstLapTable = document.querySelector(".lap-table");
    expect(firstLapTable).not.toBeNull();
    expect(
      within(firstLapTable as HTMLTableElement).getByText("1:31.100"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load next 50 laps" }));

    expect(await screen.findByText("4 laps loaded")).toBeInTheDocument();
    expect(screen.getByText("1:30.900")).toBeInTheDocument();
    expect(screen.getByText("End of stored lap summaries")).toBeInTheDocument();
    expect(getSessionLaps).toHaveBeenNthCalledWith(
      2,
      completedSession.id,
      sessionResults.items[0].session_entry_id,
      { after_lap: 2, limit: 50 },
      expect.any(AbortSignal),
    );
  });

  it("restarts pagination instead of mixing archive snapshots", async () => {
    const user = userEvent.setup();
    const refreshedFirstPage = {
      ...firstLapPage,
      snapshot: {
        ...completedSnapshot,
        completed_at: "2026-07-28T13:00:00Z",
      },
      page: {
        limit: 50,
        has_more: false,
        next_after_lap: null,
      },
      items: [
        {
          ...firstLapPage.items[0],
          id: "901",
          lap_number: 10,
        },
      ],
    };
    const changedSecondPage = {
      ...secondLapPage,
      snapshot: refreshedFirstPage.snapshot,
    };
    getSessionDetail.mockResolvedValue(sessionDetail);
    getSessionResults.mockResolvedValue(sessionResults);
    getSessionLaps
      .mockResolvedValueOnce(firstLapPage)
      .mockResolvedValueOnce(changedSecondPage)
      .mockResolvedValueOnce(refreshedFirstPage);

    renderExplorer();

    const norrisRow = await screen.findByRole("row", { name: /Lando Norris/ });
    await user.click(within(norrisRow).getByRole("button", { name: "View laps" }));
    await screen.findByText("2 laps loaded");
    await user.click(screen.getByRole("button", { name: "Load next 50 laps" }));

    expect(
      await screen.findByText(
        "The archive snapshot changed, so lap pagination restarted from the latest data.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(getSessionLaps).toHaveBeenCalledTimes(3));
    expect(await screen.findByText("1 lap loaded")).toBeInTheDocument();
    const refreshedLapTable = document.querySelector(".lap-table");
    expect(refreshedLapTable).not.toBeNull();
    expect(
      within(refreshedLapTable as HTMLTableElement).getByRole("cell", {
        name: "10",
      }),
    ).toBeInTheDocument();
    expect(
      within(refreshedLapTable as HTMLTableElement).queryByRole("cell", {
        name: "2",
      }),
    ).not.toBeInTheDocument();
  });

  it("surfaces a safe backend error and closes on request", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    getSessionDetail.mockRejectedValue(new Error("internal detail"));

    renderExplorer(onClose);

    expect(
      await screen.findByRole("alert"),
    ).toHaveTextContent("Session data could not be loaded from the local backend.");
    expect(screen.queryByText("internal detail")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Close view/ }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
