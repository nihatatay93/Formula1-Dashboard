import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  getLiveRecordings,
  getLiveStatus,
  liveStreamUrl,
  startLiveReplay,
  startLiveSession,
  stopLiveSession,
} from "../api";
import LiveAuthPanel from "./LiveAuthPanel";
import LiveBoardView from "./LiveBoard";
import LiveRecordings from "./LiveRecordings";
import type {
  LiveAuthStatus,
  LiveBoard,
  LiveRecording,
  LiveStatus,
  LiveStreamMessage,
} from "../contracts";

const STATUS_POLL_INTERVAL_MS = 5_000;
const DEFAULT_REPLAY_SPEED = 10;

type ConnectionState = "idle" | "connecting" | "open" | "closed";

const CONNECTION_LABELS: Record<ConnectionState, string> = {
  idle: "Idle",
  connecting: "Connecting",
  open: "Live",
  closed: "Disconnected",
};

const timeFormatter = new Intl.DateTimeFormat("en", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: "UTC",
});

function formatClock(value: string | null): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return `${timeFormatter.format(parsed)} UTC`;
}

function humanize(value: string): string {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB"];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "The dashboard could not reach the live timing service.";
}

export default function LiveTiming() {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [commandPending, setCommandPending] = useState(false);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [board, setBoard] = useState<LiveBoard | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [recordings, setRecordings] = useState<LiveRecording[]>([]);
  const [replaySpeed, setReplaySpeed] = useState(DEFAULT_REPLAY_SPEED);
  const socketRef = useRef<WebSocket | null>(null);

  const refreshStatus = useCallback(async (signal?: AbortSignal) => {
    const next = await getLiveStatus(signal);
    setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function poll() {
      try {
        await refreshStatus(controller.signal);
        setStatusError(null);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setStatusError(errorMessage(error));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void poll();
    const timer = window.setInterval(() => void poll(), STATUS_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refreshStatus]);

  const refreshRecordings = useCallback(async (signal?: AbortSignal) => {
    try {
      setRecordings((await getLiveRecordings(signal)).items);
    } catch {
      // Replay is an extra, not the point of this view: a listing that cannot
      // be read leaves the live path untouched.
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRecordings(controller.signal);
    return () => controller.abort();
  }, [refreshRecordings]);

  const active = status?.active ?? false;
  const session = status?.session ?? null;
  const isReplay = session?.replay ?? false;
  const finished = session?.finished ?? false;

  // The stream follows the session rather than `active`, so a finished replay
  // keeps its final board on screen instead of blanking the moment it ends.
  const hasSession = session !== null;
  useEffect(() => {
    if (!hasSession) {
      socketRef.current?.close();
      socketRef.current = null;
      setConnection("idle");
      setBoard(null);
      return;
    }

    setConnection("connecting");
    setStreamError(null);
    const socket = new WebSocket(liveStreamUrl());
    socketRef.current = socket;

    socket.onopen = () => setConnection("open");
    socket.onclose = () => setConnection("closed");
    socket.onerror = () =>
      setStreamError("The live stream connection failed.");
    socket.onmessage = (event: MessageEvent<string>) => {
      let message: LiveStreamMessage;
      try {
        message = JSON.parse(event.data) as LiveStreamMessage;
      } catch {
        return;
      }
      if (message.type === "snapshot" || message.type === "board") {
        // The backend sends a fully normalised board, coalesced so a busy
        // session does not push a render per delta.
        setBoard(message.board);
        return;
      }
      if (message.type === "error") {
        setStreamError(message.message);
      }
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [hasSession]);

  async function handleReplay(name: string) {
    setCommandPending(true);
    setCommandError(null);
    try {
      setStatus(await startLiveReplay(name, replaySpeed));
    } catch (error) {
      setCommandError(errorMessage(error));
    } finally {
      setCommandPending(false);
    }
  }

  async function handleStart() {
    setCommandPending(true);
    setCommandError(null);
    try {
      setStatus(await startLiveSession());
    } catch (error) {
      setCommandError(errorMessage(error));
    } finally {
      setCommandPending(false);
    }
  }

  async function handleStop() {
    setCommandPending(true);
    setCommandError(null);
    try {
      setStatus(await stopLiveSession());
      // A finished live session leaves a new recording behind.
      await refreshRecordings();
    } catch (error) {
      setCommandError(errorMessage(error));
    } finally {
      setCommandPending(false);
    }
  }

  return (
    <section aria-labelledby="live-timing-title" className="live-panel">
      <div className="section-heading">
        <div>
          <p className="section-kicker">Unconfirmed live data</p>
          <h2 id="live-timing-title">Live timing</h2>
        </div>
        <span className={`live-badge live-badge--${connection}`}>
          {CONNECTION_LABELS[connection]}
        </span>
      </div>

      <p className="live-panel__disclaimer">
        Live frames are streamed and logged outside the archive. Nothing here is
        stored as sporting data, and the durable record of this session arrives
        later from the FastF1 archive backfill.
      </p>

      {loading ? (
        <div className="session-explorer__loading" aria-live="polite">
          <span />
          Loading live timing status…
        </div>
      ) : null}

      {statusError ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Live timing unavailable</strong>
          <span>{statusError}</span>
        </div>
      ) : null}

      {status ? (
        <LiveAuthPanel
          auth={status.authentication}
          onChanged={(authentication: LiveAuthStatus) =>
            setStatus((current) =>
              current === null ? current : { ...current, authentication },
            )
          }
        />
      ) : null}

      {status && !status.feed_configured ? (
        <div className="inline-alert inline-alert--warning" role="status">
          <strong>No live feed provider is configured</strong>
          <span>
            The live path is ready, but this deployment has no SignalR provider,
            so a session cannot be started yet.
          </span>
        </div>
      ) : null}

      {commandError ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Command failed</strong>
          <span>{commandError}</span>
        </div>
      ) : null}

      {status ? (
        <div className="live-controls">
          <p className="live-controls__hint">
            {isReplay
              ? finished
                ? "Replay complete. The final state of the recorded session is shown below."
                : "Replaying a recorded session. Nothing is being collected from the feed."
              : active
                ? "Collecting the session the feed is currently broadcasting."
                : "Connect while a session is running. The feed states which session it is."}
          </p>
          {hasSession ? (
            <button
              className="secondary-action"
              disabled={commandPending}
              onClick={() => void handleStop()}
              type="button"
            >
              {isReplay ? (finished ? "Close replay" : "Stop replay") : "Stop session"}
            </button>
          ) : (
            <button
              className="primary-action"
              disabled={commandPending || !status.feed_configured}
              onClick={() => void handleStart()}
              type="button"
            >
              {commandPending ? "Connecting…" : "Connect to live session"}
            </button>
          )}
        </div>
      ) : null}

      {isReplay ? (
        <div className="inline-alert inline-alert--info" role="status">
          <strong>
            {finished ? "Replay complete" : "Replaying a recorded session"}
          </strong>
          <span>
            These frames come from a session log on disk, not the live feed.
            Nothing is being recorded, and the durable record of this session
            still comes from the FastF1 archive backfill.
          </span>
        </div>
      ) : null}

      {streamError ? (
        <p className="inline-alert inline-alert--danger" role="alert">
          {streamError}
        </p>
      ) : null}

      {status?.session ? (
        <div className="live-facts">
          <div>
            <span>Session</span>
            <strong>
              {status.session.session
                ? status.session.session.event_name
                : "Identifying…"}
            </strong>
          </div>
          <div>
            <span>Collector</span>
            <strong>{humanize(status.session.state)}</strong>
          </div>
          <div>
            <span>Frames accepted</span>
            <strong>{status.session.stats.accepted.toLocaleString("en")}</strong>
          </div>
          <div>
            <span>Duplicates dropped</span>
            <strong>
              {status.session.stats.duplicates.toLocaleString("en")}
            </strong>
          </div>
          <div>
            <span>Reconnects</span>
            <strong>{status.session.stats.reconnects}</strong>
          </div>
          <div>
            <span>Drivers</span>
            <strong>{board ? board.drivers.length : "—"}</strong>
          </div>
        </div>
      ) : null}

      {status?.session?.log_degraded ? (
        <div className="inline-alert inline-alert--warning" role="status">
          <strong>Session log degraded</strong>
          <span>
            Frames are still streaming, but they are no longer being written to
            the disposable session log.
          </span>
        </div>
      ) : null}

      {hasSession && board ? <LiveBoardView board={board} /> : null}

      {hasSession && !board ? (
        <p className="session-explorer__hint">
          {isReplay
            ? "Replaying. Waiting for the first frames from the recording."
            : "Connected. Waiting for the first frames from the feed."}
        </p>
      ) : null}

      {status && !hasSession && status.feed_configured ? (
        <p className="session-explorer__hint">
          No live session is being collected. Connect while a session is
          running, or replay a recorded one below.
        </p>
      ) : null}

      {status && !hasSession ? (
        <LiveRecordings
          busy={commandPending}
          onReplay={(name) => void handleReplay(name)}
          onSpeedChange={setReplaySpeed}
          recordings={recordings}
          retentionDays={status.retention_days}
          speed={replaySpeed}
        />
      ) : null}

      {status ? (
        <p className="live-panel__retention">
          Session logs are deleted after {status.retention_days} day
          {status.retention_days === 1 ? "" : "s"} ·{" "}
          {formatBytes(status.log_directory_bytes)} of{" "}
          {formatBytes(status.max_directory_bytes)} used
        </p>
      ) : null}
    </section>
  );
}
