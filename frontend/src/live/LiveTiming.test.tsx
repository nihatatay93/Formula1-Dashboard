import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { LiveStatus } from "../contracts";
import LiveTiming from "./LiveTiming";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    getLiveStatus: vi.fn(),
    startLiveSession: vi.fn(),
    stopLiveSession: vi.fn(),
    getLiveRecordings: vi.fn(),
    startLiveReplay: vi.fn(),
  };
});

const getLiveStatus = vi.mocked(api.getLiveStatus);
const startLiveSession = vi.mocked(api.startLiveSession);
const stopLiveSession = vi.mocked(api.stopLiveSession);
const getLiveRecordings = vi.mocked(api.getLiveRecordings);
const startLiveReplay = vi.mocked(api.startLiveReplay);

const RECORDING = {
  name: "2026-08-21__dutch-grand-prix__race.jsonl",
  event_name: "Dutch Grand Prix",
  session_key: "Race",
  session_date: "2026-08-21",
  size_bytes: 2_400_000,
  modified_at: "2026-08-21T16:00:00+00:00",
};

function recordings(items = [RECORDING]) {
  return { record_state: "unconfirmed_live", retention_days: 7, items };
}

function replayStatus(overrides: Record<string, unknown> = {}): LiveStatus {
  return activeStatus({ replay: true, ...overrides });
}

/** Minimal WebSocket double that lets a test push server frames. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.onopen?.();
  }

  emit(payload: unknown): void {
    this.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }

  emitRaw(data: string): void {
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  close(): void {
    this.closed = true;
    this.onclose?.();
  }
}

function status(overrides: Partial<LiveStatus> = {}): LiveStatus {
  return {
    record_state: "unconfirmed_live",
    active: false,
    feed_configured: true,
    retention_days: 7,
    log_directory_bytes: 2048,
    max_directory_bytes: 5_368_709_120,
    authentication: {
      authenticated: false,
      expired: false,
      expires_at: null,
      seconds_remaining: 0,
      expiry_source: null,
      token_source: null,
      companion_url: "https://f1login.fastf1.dev?port=8000",
      subscription: {},
    },
    requires_authentication: false,
    session: null,
    ...overrides,
  };
}

function activeStatus(overrides: Record<string, unknown> = {}): LiveStatus {
  return status({
    active: true,
    session: {
      state: "streaming",
      session: {
        session_date: "2026-08-21",
        event_name: "Dutch Grand Prix",
        session_key: "qualifying",
      },
      topics_subscribed: ["TimingData"],
      replay: false,
      finished: false,
      log_degraded: false,
      subscribers: 1,
      stats: {
        accepted: 12,
        duplicates: 3,
        rejected: {},
        connection_attempts: 1,
        reconnects: 0,
        dropped_by_log_cap: 0,
      },
      ...overrides,
    },
  });
}

function socket(): FakeWebSocket {
  const instance = FakeWebSocket.instances.at(-1);
  if (!instance) {
    throw new Error("no WebSocket was opened");
  }
  return instance;
}


function board(overrides: Record<string, unknown> = {}) {
  return {
    meeting_name: "Hungarian Grand Prix",
    session_name: "Race",
    session_type: "Race",
    session_status: "Started",
    started: "Started",
    track_status: "AllClear",
    track_status_code: "1",
    current_lap: 12,
    total_laps: 70,
    remaining: "01:20:00",
    extrapolating: true,
    weather: { AirTemp: "31.3" },
    drivers: [
      {
        racing_number: "1",
        tla: "NOR",
        full_name: "Lando NORRIS",
        team_name: "McLaren",
        team_colour: "F47600",
        position: 1,
        line: 1,
        places_gained: 3,
        position_baseline: 4,
        recent_move: "up",
        gap_to_leader: "",
        interval: "",
        last_lap: "1:23.625",
        last_lap_personal_best: true,
        last_lap_overall_best: false,
        best_lap: "1:22.491",
        sectors: [
          {
            value: "27.446",
            personal_best: true,
            overall_best: false,
            segments: ["green", "green", "purple", "pending"],
          },
          {
            value: "",
            personal_best: false,
            overall_best: false,
            segments: ["pit", "pending", "pending"],
          },
        ],
        compound: "SOFT",
        tyre_age: 9,
        pit_stops: 1,
        laps: 12,
        in_pit: false,
        pit_out: false,
        retired: false,
        stopped: false,
        knocked_out: false,
        status: "On track",
      },
    ],
    race_control: [
      { utc: "2026-07-26T14:45:40", category: "Flag", message: "GREEN LIGHT", lap: 1, flag: "GREEN" },
    ],
    ...overrides,
  };
}

describe("LiveTiming", () => {
  beforeEach(() => {
    getLiveStatus.mockReset();
    startLiveSession.mockReset();
    stopLiveSession.mockReset();
    getLiveRecordings.mockReset();
    getLiveRecordings.mockResolvedValue(recordings([]));
    startLiveReplay.mockReset();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  it("always states that live data is not stored as sporting data", async () => {
    getLiveStatus.mockResolvedValue(status());

    render(<LiveTiming />);

    expect(await screen.findByText(/never mixed|Nothing here is/i)).toBeVisible();
    expect(screen.getByText("Unconfirmed live data")).toBeInTheDocument();
  });

  it("explains an unconfigured feed and refuses to start", async () => {
    getLiveStatus.mockResolvedValue(status({ feed_configured: false }));

    render(<LiveTiming />);

    expect(
      await screen.findByText("No live feed provider is configured"),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Connect to live session/ }),
      ).toBeDisabled(),
    );
  });

  it("connects without asking for a session identity", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(status());
    startLiveSession.mockResolvedValue(activeStatus());

    render(<LiveTiming />);

    const connect = await screen.findByRole("button", {
      name: /Connect to live session/,
    });
    // Nothing to fill in: the feed states which session it is.
    expect(connect).toBeEnabled();
    expect(screen.queryByLabelText(/Event name/)).not.toBeInTheDocument();

    await user.click(connect);

    expect(startLiveSession).toHaveBeenCalledWith();
    expect(await screen.findByText("Streaming")).toBeInTheDocument();
  });

  it("shows the session as identifying until the feed names it", async () => {
    getLiveStatus.mockResolvedValue(activeStatus({ session: null }));

    render(<LiveTiming />);

    expect(await screen.findByText("Identifying…")).toBeVisible();
  });

  it("surfaces a rejected start without claiming a session is live", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(status());
    startLiveSession.mockRejectedValue(
      new api.ApiClientError(
        "A different live session is already active.",
        "live_session_conflict",
        409,
      ),
    );

    render(<LiveTiming />);

    await user.click(
      await screen.findByRole("button", { name: /Connect to live session/ }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A different live session is already active.",
    );
    expect(screen.queryByText("Streaming")).not.toBeInTheDocument();
  });

  it("renders a live timing board from the snapshot", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: board(),
    });

    // Real rows, not raw topic payloads.
    expect(await screen.findByText("NOR")).toBeVisible();
    expect(screen.getByText("McLaren")).toBeVisible();
    expect(screen.getByText("1:23.625")).toBeVisible();
    expect(screen.getByText("1:22.491")).toBeVisible();
    expect(screen.getByText("GREEN LIGHT")).toBeVisible();
    expect(screen.getByText("AllClear")).toBeVisible();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("renders a micro-sector strip and its legend", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    const { container } = render(<LiveTiming />);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: board(),
    });

    await screen.findByText("NOR");

    // One block per micro-sector, coloured by its status.
    const row = container.querySelector(".live-board__sectors");
    expect(row?.querySelectorAll(".live-segments__block")).toHaveLength(7);
    expect(row?.querySelectorAll(".live-segments__block--green")).toHaveLength(2);
    expect(row?.querySelectorAll(".live-segments__block--purple")).toHaveLength(1);
    expect(row?.querySelectorAll(".live-segments__block--pit")).toHaveLength(1);
    expect(row?.querySelectorAll(".live-segments__block--pending")).toHaveLength(3);

    // The strip is decoration over the sector time, which still reads normally.
    expect(screen.getByText("27.446")).toBeVisible();
    expect(row?.querySelector(".live-segments")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(
      container.querySelector(".live-sector[title]"),
    ).toHaveAttribute("title", expect.stringContaining("3/4 micro-sectors"));

    // Colours mean nothing without a key.
    expect(screen.getByText("overall fastest")).toBeVisible();
    expect(screen.getByText("pit lane")).toBeVisible();
  });

  it("shows race columns for a race and drops them for qualifying", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: board(),
    });
    expect(await screen.findByRole("columnheader", { name: "Int" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Stops" })).toBeVisible();

    socket().emit({
      type: "board",
      record_state: "unconfirmed_live",
      session: null,
      board: board({ session_type: "Qualifying", session_name: "Qualifying" }),
    });

    await waitFor(() =>
      expect(
        screen.queryByRole("columnheader", { name: "Int" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("columnheader", { name: "Stops" }),
    ).not.toBeInTheDocument();
  });

  it("replaces the board on each coalesced update", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: board(),
    });
    await screen.findByText("NOR");

    socket().emit({
      type: "board",
      record_state: "unconfirmed_live",
      session: null,
      board: board({ current_lap: 40 }),
    });

    expect(await screen.findByText("40")).toBeVisible();
  });

  it("ignores an unparseable stream frame instead of crashing", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emitRaw("not json");
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: board(),
    });

    expect(await screen.findByText("NOR")).toBeVisible();
  });

  it("shows a stream error frame from the backend", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "error",
      code: "no_active_live_session",
      message: "No live session is currently being collected.",
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No live session is currently being collected.",
    );
  });

  it("reports collector counters including duplicates", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);

    expect(await screen.findByText("Frames accepted")).toBeVisible();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Duplicates dropped")).toBeVisible();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("warns when the session log is degraded but still streaming", async () => {
    getLiveStatus.mockResolvedValue(activeStatus({ log_degraded: true }));

    render(<LiveTiming />);

    expect(await screen.findByText("Session log degraded")).toBeVisible();
  });

  it("stops a session and closes the stream", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(activeStatus());
    stopLiveSession.mockResolvedValue(status());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    await user.click(await screen.findByRole("button", { name: /Stop session/ }));

    expect(stopLiveSession).toHaveBeenCalledOnce();
    await waitFor(() => expect(socket().closed).toBe(true));
  });

  it("surfaces an unreachable live service", async () => {
    getLiveStatus.mockRejectedValue(
      new api.ApiClientError("boom", "request_failed", 503),
    );

    render(<LiveTiming />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Live timing unavailable",
    );
  });

  it("reports the retention window so nothing looks permanent", async () => {
    getLiveStatus.mockResolvedValue(status({ retention_days: 1 }));

    render(<LiveTiming />);

    expect(
      await screen.findByText(/Session logs are deleted after 1 day/),
    ).toBeVisible();
  });

  it("does not open a stream while no session is active", async () => {
    getLiveStatus.mockResolvedValue(status());

    render(<LiveTiming />);
    await screen.findByRole("button", { name: /Connect to live session/ });

    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});

describe("LiveTiming replay", () => {
  beforeEach(() => {
    getLiveStatus.mockReset();
    startLiveSession.mockReset();
    stopLiveSession.mockReset();
    getLiveRecordings.mockReset();
    startLiveReplay.mockReset();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  it("lists recorded sessions with their identity", async () => {
    getLiveStatus.mockResolvedValue(status());
    getLiveRecordings.mockResolvedValue(recordings());

    render(<LiveTiming />);

    expect(await screen.findByText("Dutch Grand Prix")).toBeVisible();
    expect(screen.getByText(/Race · Aug 21, 2026 · 2\.3 MB/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Replay" })).toBeEnabled();
  });

  it("explains the empty list rather than showing nothing", async () => {
    getLiveStatus.mockResolvedValue(status());
    getLiveRecordings.mockResolvedValue(recordings([]));

    render(<LiveTiming />);

    expect(await screen.findByText(/No recorded sessions yet/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Replay" })).toBeNull();
  });

  it("starts a replay at the selected speed", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(status());
    getLiveRecordings.mockResolvedValue(recordings());
    startLiveReplay.mockResolvedValue(replayStatus());

    render(<LiveTiming />);
    await screen.findByText("Dutch Grand Prix");
    await user.click(screen.getByRole("radio", { name: "5×" }));
    await user.click(screen.getByRole("button", { name: "Replay" }));

    expect(startLiveReplay).toHaveBeenCalledWith(RECORDING.name, 5);
  });

  it("says a replay is not the live feed and writes nothing", async () => {
    getLiveStatus.mockResolvedValue(replayStatus());

    render(<LiveTiming />);

    expect(
      await screen.findByText("Replaying a recorded session"),
    ).toBeVisible();
    expect(
      screen.getByText(/come from a session log on disk, not the live feed/),
    ).toBeVisible();
    // Writing no log is deliberate here, so the degraded warning must not show.
    expect(screen.queryByText("Session log degraded")).toBeNull();
  });

  it("keeps the final board on screen once a replay finishes", async () => {
    // The collector is no longer active, but the session is still addressable.
    getLiveStatus.mockResolvedValue(
      replayStatus({ state: "finished", finished: true }),
    );

    render(<LiveTiming />);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: board(),
    });

    expect(await screen.findByText("Replay complete")).toBeVisible();
    // The board survives the end of the recording rather than blanking.
    expect(screen.getByText("NOR")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Close replay" }),
    ).toBeEnabled();
  });

  it("hides the recording list while a session is running", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());
    getLiveRecordings.mockResolvedValue(recordings());

    render(<LiveTiming />);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Replay" })).toBeNull();
  });

  it("surfaces a rejected replay without claiming one started", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(status());
    getLiveRecordings.mockResolvedValue(recordings());
    startLiveReplay.mockRejectedValue(
      new api.ApiClientError(
        "Stop the running session first.",
        "live_session_busy",
        409,
      ),
    );

    render(<LiveTiming />);
    await screen.findByText("Dutch Grand Prix");
    await user.click(screen.getByRole("button", { name: "Replay" }));

    expect(await screen.findByText("Command failed")).toBeVisible();
    expect(screen.getByText("Stop the running session first.")).toBeVisible();
    expect(screen.queryByText("Replaying a recorded session")).toBeNull();
  });

  it("keeps working when the recording list cannot be read", async () => {
    getLiveStatus.mockResolvedValue(status());
    getLiveRecordings.mockRejectedValue(new Error("offline"));

    render(<LiveTiming />);

    // Replay is secondary; a failed listing must not break the live view.
    expect(
      await screen.findByRole("button", { name: /Connect to live session/ }),
    ).toBeInTheDocument();
  });
});

describe("LiveTiming position movement", () => {
  beforeEach(() => {
    getLiveStatus.mockReset();
    startLiveSession.mockReset();
    stopLiveSession.mockReset();
    getLiveRecordings.mockReset();
    getLiveRecordings.mockResolvedValue(recordings([]));
    startLiveReplay.mockReset();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  async function boardWith(driver: Record<string, unknown>) {
    getLiveStatus.mockResolvedValue(activeStatus());
    const rendered = render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    const base = board();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      board: {
        ...base,
        drivers: [{ ...(base.drivers[0] as object), ...driver }],
      },
    });
    await screen.findByText("NOR");
    return rendered;
  }

  it("states how many places were gained, not only a direction", async () => {
    const { container } = await boardWith({
      places_gained: 3,
      position_baseline: 4,
      recent_move: "",
    });

    // An arrow alone says direction without saying how far.
    expect(screen.getByText("+3")).toBeVisible();
    expect(container.querySelector(".live-move--up")).not.toBeNull();
  });

  it("writes the sign out so colour is never the only carrier", async () => {
    const { container } = await boardWith({
      places_gained: -2,
      position_baseline: 1,
      recent_move: "",
    });

    expect(screen.getByText("-2")).toBeVisible();
    expect(container.querySelector(".live-move--down")).not.toBeNull();
  });

  it("names the baseline, because it is not the grid", async () => {
    // The feed carries no grid position, so the count is measured from the
    // first position this session saw. Saying so is the honest part.
    const { container } = await boardWith({
      places_gained: 5,
      position_baseline: 9,
      recent_move: "",
    });

    expect(container.querySelector(".live-move")).toHaveAttribute(
      "title",
      "Gained 5 places (from P9 when this session was connected)",
    );
  });

  it("marks a change that just happened", async () => {
    const { container } = await boardWith({
      places_gained: 1,
      position_baseline: 2,
      recent_move: "up",
    });

    expect(container.querySelector(".live-move--recent")).not.toBeNull();
  });

  it("leaves a driver who has not moved unmarked", async () => {
    const { container } = await boardWith({
      places_gained: 0,
      position_baseline: 1,
      recent_move: "",
    });

    expect(screen.queryByText("+0")).toBeNull();
    expect(container.querySelector(".live-move--up")).toBeNull();
    expect(container.querySelector(".live-move--down")).toBeNull();
  });

  it("shows nothing when the backend reports no history", async () => {
    const { container } = await boardWith({
      places_gained: null,
      position_baseline: null,
      recent_move: "",
    });

    expect(container.querySelector(".live-move--up")).toBeNull();
    expect(container.querySelector(".live-move--down")).toBeNull();
    // The position itself still reads normally.
    expect(
      container.querySelector(".live-board__position > span:first-child")
        ?.textContent,
    ).toBe("1");
  });
});
