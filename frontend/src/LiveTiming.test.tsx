import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import type { LiveStatus } from "./contracts";
import LiveTiming from "./LiveTiming";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    getLiveStatus: vi.fn(),
    startLiveSession: vi.fn(),
    stopLiveSession: vi.fn(),
  };
});

const getLiveStatus = vi.mocked(api.getLiveStatus);
const startLiveSession = vi.mocked(api.startLiveSession);
const stopLiveSession = vi.mocked(api.stopLiveSession);

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
        gap_to_leader: "",
        interval: "",
        last_lap: "1:23.625",
        last_lap_personal_best: true,
        last_lap_overall_best: false,
        best_lap: "1:22.491",
        sectors: [
          { value: "27.446", personal_best: true, overall_best: false },
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
