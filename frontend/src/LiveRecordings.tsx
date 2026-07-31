import type { LiveRecording } from "./contracts";

/**
 * Recorded sessions available to replay.
 *
 * These are the disposable session logs the collector wrote during earlier live
 * sessions, so the list empties itself as retention sweeps. That is stated
 * plainly rather than left for a reader to discover when a recording vanishes.
 */

const REPLAY_SPEEDS = [1, 5, 10, 30] as const;

const dateFormatter = new Intl.DateTimeFormat("en", {
  day: "2-digit",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

function formatDate(value: string | null): string {
  if (!value) {
    return "Unknown date";
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown date";
  }
  return dateFormatter.format(parsed);
}

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB"];
  let size = bytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

export default function LiveRecordings({
  busy,
  onReplay,
  recordings,
  retentionDays,
  speed,
  onSpeedChange,
}: {
  busy: boolean;
  onReplay: (name: string) => void;
  recordings: LiveRecording[];
  retentionDays: number;
  speed: number;
  onSpeedChange: (speed: number) => void;
}) {
  return (
    <section aria-labelledby="live-recordings-title" className="live-recordings">
      <div className="section-heading">
        <div>
          <p className="section-kicker">Recorded sessions</p>
          <h3 id="live-recordings-title">Replay a session</h3>
        </div>
        <fieldset className="live-recordings__speed">
          <legend>Speed</legend>
          {REPLAY_SPEEDS.map((option) => (
            <label key={option}>
              <input
                checked={speed === option}
                name="replay-speed"
                onChange={() => onSpeedChange(option)}
                type="radio"
                value={option}
              />
              <span>{option}&times;</span>
            </label>
          ))}
        </fieldset>
      </div>

      {recordings.length === 0 ? (
        <p className="session-explorer__hint">
          No recorded sessions yet. Every live session you collect is kept here
          for {retentionDays} day{retentionDays === 1 ? "" : "s"}, then deleted.
        </p>
      ) : (
        <>
          <ul className="live-recordings__list">
            {recordings.map((recording) => (
              <li key={recording.name}>
                <div>
                  <strong>{recording.event_name || recording.name}</strong>
                  <small>
                    {recording.session_key
                      ? `${recording.session_key} · `
                      : null}
                    {formatDate(recording.session_date)} ·{" "}
                    {formatSize(recording.size_bytes)}
                  </small>
                </div>
                <button
                  className="secondary-action"
                  disabled={busy}
                  onClick={() => onReplay(recording.name)}
                  type="button"
                >
                  Replay
                </button>
              </li>
            ))}
          </ul>
          <p className="live-recordings__note">
            Replays are read-only: they write no session log and need no F1 TV
            connection. Recordings are deleted {retentionDays} day
            {retentionDays === 1 ? "" : "s"} after the session.
          </p>
        </>
      )}
    </section>
  );
}
