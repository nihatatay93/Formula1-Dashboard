import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiClientError,
  getLiveStatus,
  liveStreamUrl,
  startLiveSession,
  stopLiveSession,
} from "./api";
import LiveAuthPanel from "./LiveAuthPanel";
import type {
  LiveAuthStatus,
  LiveStatus,
  LiveStreamMessage,
  LiveTopicState,
  LiveViewState,
} from "./contracts";

const STATUS_POLL_INTERVAL_MS = 5_000;

const SESSION_KEYS = [
  "practice_1",
  "practice_2",
  "practice_3",
  "sprint_qualifying",
  "sprint",
  "qualifying",
  "race",
] as const;

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

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Renders one topic's latest payload without assuming its shape. The real feed's
 * payload schemas are confirmed alongside the SignalR provider, so a
 * purpose-built leaderboard would be a guess presented as fact.
 */
function TopicCard({
  name,
  state,
}: {
  name: string;
  state: LiveTopicState;
}) {
  const entries = Object.entries(state.payload);

  return (
    <article className="live-topic">
      <header>
        <strong>{name}</strong>
        <span title="Merged deltas since the last full-state frame">
          +{state.updates}
        </span>
      </header>
      <p className="live-topic__time">{formatClock(state.received_at)}</p>
      {entries.length === 0 ? (
        <p className="live-topic__empty">Empty payload</p>
      ) : (
        <dl>
          {entries.slice(0, 6).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>
                {typeof value === "object" && value !== null
                  ? `${Object.keys(value as object).length} fields`
                  : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {entries.length > 6 ? (
        <p className="live-topic__more">
          +{entries.length - 6} more field
          {entries.length - 6 === 1 ? "" : "s"}
        </p>
      ) : null}
    </article>
  );
}

export default function LiveTiming() {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [commandPending, setCommandPending] = useState(false);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [viewState, setViewState] = useState<LiveViewState | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [sessionDate, setSessionDate] = useState(todayUtc);
  const [eventName, setEventName] = useState("");
  const [sessionKey, setSessionKey] = useState<string>("race");
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

  const active = status?.active ?? false;
  const activeIdentity = status?.session?.session ?? null;

  // While a session is running the form must describe it rather than keep
  // showing defaults for a session that is not the one being collected.
  useEffect(() => {
    if (activeIdentity === null) {
      return;
    }
    setSessionDate(activeIdentity.session_date);
    setEventName(activeIdentity.event_name);
    setSessionKey(activeIdentity.session_key);
  }, [
    activeIdentity?.session_date,
    activeIdentity?.event_name,
    activeIdentity?.session_key,
  ]);

  useEffect(() => {
    if (!active) {
      socketRef.current?.close();
      socketRef.current = null;
      setConnection("idle");
      setViewState(null);
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
      if (message.type === "snapshot") {
        setViewState(message.state);
        return;
      }
      if (message.type === "update") {
        setViewState((current) => {
          const base: LiveViewState =
            current ?? {
              latest_received_at: null,
              applied_frames: 0,
              topics: {},
            };
          const previous = base.topics[message.topic];
          // The payload is already merged server-side, so this replaces the
          // topic rather than reapplying a delta on the client.
          return {
            latest_received_at: message.received_at,
            applied_frames: base.applied_frames + 1,
            topics: {
              ...base.topics,
              [message.topic]: {
                received_at: message.received_at,
                feed_timestamp: null,
                snapshots:
                  (previous?.snapshots ?? 0) + (message.initial ? 1 : 0),
                updates: message.initial ? 0 : (previous?.updates ?? 0) + 1,
                payload: message.payload,
              },
            },
          };
        });
        return;
      }
      setStreamError(message.message);
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [active]);

  async function handleStart() {
    setCommandPending(true);
    setCommandError(null);
    try {
      setStatus(
        await startLiveSession({
          event_name: eventName.trim(),
          session_date: sessionDate,
          session_key: sessionKey,
        }),
      );
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
    } catch (error) {
      setCommandError(errorMessage(error));
    } finally {
      setCommandPending(false);
    }
  }

  const topics = useMemo(
    () => Object.entries(viewState?.topics ?? {}),
    [viewState],
  );
  const startDisabled =
    commandPending ||
    !status?.feed_configured ||
    eventName.trim().length === 0;

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
          <label htmlFor="live-session-date">
            Session date
            <input
              disabled={active}
              id="live-session-date"
              onChange={(event) => setSessionDate(event.target.value)}
              type="date"
              value={sessionDate}
            />
          </label>
          <label htmlFor="live-event-name">
            Event name
            <input
              disabled={active}
              id="live-event-name"
              onChange={(event) => setEventName(event.target.value)}
              placeholder="Dutch Grand Prix"
              type="text"
              value={eventName}
            />
          </label>
          <label htmlFor="live-session-key">
            Session
            <select
              disabled={active}
              id="live-session-key"
              onChange={(event) => setSessionKey(event.target.value)}
              value={sessionKey}
            >
              {SESSION_KEYS.map((key) => (
                <option key={key} value={key}>
                  {humanize(key)}
                </option>
              ))}
            </select>
          </label>
          {active ? (
            <button
              className="secondary-action"
              disabled={commandPending}
              onClick={() => void handleStop()}
              type="button"
            >
              Stop session
            </button>
          ) : (
            <button
              className="primary-action"
              disabled={startDisabled}
              onClick={() => void handleStart()}
              type="button"
            >
              {commandPending ? "Connecting…" : "Connect to session"}
            </button>
          )}
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
            <strong>{status.session.session.event_name}</strong>
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
            <span>Last frame</span>
            <strong>{formatClock(viewState?.latest_received_at ?? null)}</strong>
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

      {active && topics.length > 0 ? (
        <div className="live-topics">
          {topics.map(([name, state]) => (
            <TopicCard key={name} name={name} state={state} />
          ))}
        </div>
      ) : null}

      {active && topics.length === 0 ? (
        <p className="session-explorer__hint">
          Connected. Waiting for the first frames from the feed.
        </p>
      ) : null}

      {status && !active && status.feed_configured ? (
        <p className="session-explorer__hint">
          No live session is being collected. Enter the session details and
          connect when a session is running.
        </p>
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
