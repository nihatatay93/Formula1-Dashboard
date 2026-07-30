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
        screen.getByRole("button", { name: /Connect to session/ }),
      ).toBeDisabled(),
    );
  });

  it("keeps connect disabled until an event name is entered", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(status());

    render(<LiveTiming />);

    const connect = await screen.findByRole("button", {
      name: /Connect to session/,
    });
    expect(connect).toBeDisabled();

    await user.type(screen.getByLabelText(/Event name/), "Dutch Grand Prix");

    expect(connect).toBeEnabled();
  });

  it("starts a session with the entered identity", async () => {
    const user = userEvent.setup();
    getLiveStatus.mockResolvedValue(status());
    startLiveSession.mockResolvedValue(activeStatus());

    render(<LiveTiming />);

    await user.type(
      await screen.findByLabelText(/Event name/),
      "Dutch Grand Prix",
    );
    await user.selectOptions(screen.getByLabelText(/^Session$/), "qualifying");
    await user.click(screen.getByRole("button", { name: /Connect to session/ }));

    expect(startLiveSession).toHaveBeenCalledWith({
      event_name: "Dutch Grand Prix",
      session_date: expect.any(String),
      session_key: "qualifying",
    });
    expect(await screen.findByText("Streaming")).toBeInTheDocument();
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

    await user.type(await screen.findByLabelText(/Event name/), "Dutch GP");
    await user.click(screen.getByRole("button", { name: /Connect to session/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A different live session is already active.",
    );
    expect(screen.queryByText("Streaming")).not.toBeInTheDocument();
  });

  it("opens a stream and renders the snapshot topics", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      state: {
        latest_received_at: "2026-08-21T13:04:11Z",
        applied_frames: 4,
        topics: {
          TimingData: {
            received_at: "2026-08-21T13:04:11Z",
            feed_timestamp: "2026-08-21T13:04:11Z",
            snapshots: 1,
            updates: 18,
            payload: { Lines: { "1": { Position: "1" } }, SessionPart: 2 },
          },
        },
      },
    });

    expect(await screen.findByText("TimingData")).toBeVisible();
    expect(screen.getByText("+18")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("merges an incremental update into the rendered topics", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emit({
      type: "snapshot",
      record_state: "unconfirmed_live",
      session: null,
      state: { latest_received_at: null, applied_frames: 0, topics: {} },
    });
    socket().emit({
      type: "update",
      topic: "TrackStatus",
      initial: false,
      received_at: "2026-08-21T13:05:00Z",
      payload: { Status: "2", Message: "Yellow" },
    });

    const card = (await screen.findByText("TrackStatus")).closest("article");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("+1")).toBeInTheDocument();
    expect(within(card as HTMLElement).getByText("Yellow")).toBeInTheDocument();
  });

  it("ignores an unparseable stream frame instead of crashing", async () => {
    getLiveStatus.mockResolvedValue(activeStatus());

    render(<LiveTiming />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    socket().open();
    socket().emitRaw("not json");
    socket().emit({
      type: "update",
      topic: "LapCount",
      initial: false,
      received_at: "2026-08-21T13:06:00Z",
      payload: { CurrentLap: 12 },
    });

    expect(await screen.findByText("LapCount")).toBeVisible();
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
    await screen.findByRole("button", { name: /Connect to session/ });

    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});
