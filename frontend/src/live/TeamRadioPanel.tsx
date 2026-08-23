import { useRef, useState } from "react";

import type { LiveTeamRadioClip } from "../contracts";

/**
 * Radio captures, newest first, each one playable.
 *
 * The feed names an audio file, the car it came from and when. It carries no
 * transcript: the words shown on a broadcast are transcribed there, not sent
 * down the timing feed, so this panel offers who and when and lets you listen
 * rather than pretending to quote anyone.
 *
 * One audio element is reused for every clip. Several would let two messages
 * talk over each other, which is exactly what a radio panel must not do.
 */

function clipTime(utc: string): string {
  const at = new Date(utc);
  return Number.isNaN(at.getTime())
    ? "—"
    : at.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

export default function TeamRadioPanel({
  clips,
}: {
  clips: LiveTeamRadioClip[];
}) {
  const player = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  function toggle(clip: LiveTeamRadioClip) {
    const audio = player.current;
    if (audio === null) {
      return;
    }
    if (playing === clip.audio_url) {
      audio.pause();
      setPlaying(null);
      return;
    }
    setFailed(null);
    audio.src = clip.audio_url;
    void audio
      .play()
      .then(() => setPlaying(clip.audio_url))
      .catch(() => {
        // Autoplay policy, a expired clip, or the host being unreachable. Say
        // so on the row rather than leaving a button that appears to do
        // nothing.
        setPlaying(null);
        setFailed(clip.audio_url);
      });
  }

  return (
    <section className="live-board__panel">
      <h4>
        Team radio <span className="live-radio__count">{clips.length}</span>
      </h4>

      <audio
        onEnded={() => setPlaying(null)}
        preload="none"
        ref={player}
      />

      <ol className="live-radio">
        {clips.map((clip) => {
          const isPlaying = playing === clip.audio_url;
          const name = clip.display_name || clip.tla || clip.racing_number;
          return (
            <li key={clip.audio_url}>
              <button
                aria-label={`${isPlaying ? "Stop" : "Play"} team radio from ${name} at ${clipTime(clip.utc)}`}
                className={`live-radio__play${isPlaying ? " is-playing" : ""}`}
                onClick={() => toggle(clip)}
                type="button"
              >
                <span aria-hidden="true">{isPlaying ? "■" : "▶"}</span>
              </button>
              <span
                aria-hidden="true"
                className="live-radio__swatch"
                style={{
                  background: clip.team_colour
                    ? `#${clip.team_colour}`
                    : "var(--muted-dark)",
                }}
              />
              <span className="live-radio__driver">
                {clip.tla || name}
                <small>{name}</small>
              </span>
              <span className="live-radio__time">{clipTime(clip.utc)}</span>
              {failed === clip.audio_url ? (
                <span className="live-radio__failed" role="alert">
                  could not play
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
