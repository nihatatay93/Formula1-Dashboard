import { useEffect, useState } from "react";

import { getLiveRecordings, getLiveStatus } from "./api";
import type { LiveStatus, SeasonOverview } from "./contracts";
import StatusPill from "./shared/StatusPill";

/**
 * The landing page.
 *
 * This application is two products sharing a shell. The archive serves
 * finalized data out of PostgreSQL and is the durable record; live timing
 * streams frames that are deliberately never stored as sporting data. Reading
 * one for the other would be a real mistake, so the split is stated on the way
 * in rather than left for a reader to infer from a sidebar list.
 *
 * Each card carries the current state of its path, so the choice is made with
 * the facts rather than from the labels alone.
 */

export type HomeDestination = "overview" | "calendar" | "live";

function liveSummary(status: LiveStatus | null): {
  headline: string;
  tone: "live" | "idle" | "unavailable";
} {
  if (status === null) {
    return { headline: "Checking…", tone: "idle" };
  }
  if (!status.feed_configured) {
    return { headline: "No feed provider configured", tone: "unavailable" };
  }
  const session = status.session;
  if (session === null) {
    return { headline: "No session being collected", tone: "idle" };
  }
  if (session.replay) {
    return {
      headline: session.finished ? "Replay complete" : "Replaying a recording",
      tone: "live",
    };
  }
  return { headline: "Collecting a live session", tone: "live" };
}

export default function Home({
  onNavigate,
  season,
  seasonLoading,
  year,
}: {
  onNavigate: (destination: HomeDestination) => void;
  season: SeasonOverview | null;
  seasonLoading: boolean;
  year: number;
}) {
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [recordings, setRecordings] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        setLive(await getLiveStatus(controller.signal));
      } catch {
        // The landing page must still route to the archive when the live
        // service cannot be reached, so this failure is left unreported here
        // and surfaces properly inside the live view itself.
      }
      try {
        setRecordings((await getLiveRecordings(controller.signal)).items.length);
      } catch {
        setRecordings(null);
      }
    }

    void load();
    return () => controller.abort();
  }, []);

  const counts = season?.counts;
  const ready = counts?.data_available ?? 0;
  const total = counts?.sessions ?? 0;
  const summary = liveSummary(live);
  // Optional at every step: this card is a summary, and a status missing its
  // authentication block should read as "not connected" rather than blank the
  // whole page.
  const authenticated = live?.authentication?.authenticated ?? false;

  return (
    <div className="workspace-view home" data-view="home">
      <section className="home__intro">
        <h2>Two ways into the data</h2>
        <p>
          They are opposites on purpose. The archive is the durable record:
          finalized sessions ingested from FastF1 into PostgreSQL, identical
          every time you ask for them. Live timing is the other end — frames
          arrive while a session runs, are never written as sporting data, and
          are deleted when retention sweeps. The same session eventually
          appears in the archive, from a different source.
        </p>
      </section>

      <div className="home__paths">
        <article className="home-card" aria-labelledby="home-archive-title">
          <header>
            <p className="section-kicker">Finalized data</p>
            <h3 id="home-archive-title">Season archive</h3>
          </header>
          <p className="home-card__blurb">
            Browse every round of a season, open a session, and compare lap
            pace, stints and telemetry. This is the durable record — the same
            data every time you ask for it.
          </p>
          <dl className="home-card__facts">
            <div>
              <dt>Season</dt>
              <dd>{year}</dd>
            </div>
            <div>
              <dt>Sessions ready</dt>
              <dd>
                {seasonLoading ? "…" : `${ready} / ${total}`}
              </dd>
            </div>
            <div>
              <dt>Rounds</dt>
              <dd>{seasonLoading ? "…" : (season?.events.length ?? 0)}</dd>
            </div>
          </dl>
          {season ? (
            <p className="home-card__state">
              <StatusPill status={season.status} />
            </p>
          ) : null}
          <div className="home-card__actions">
            <button
              className="primary-action"
              onClick={() => onNavigate("calendar")}
              type="button"
            >
              Browse season sessions
              <span aria-hidden="true">↗</span>
            </button>
            <button
              className="text-action"
              onClick={() => onNavigate("overview")}
              type="button"
            >
              Coverage &amp; ingestion
            </button>
          </div>
        </article>

        <article className="home-card" aria-labelledby="home-live-title">
          <header>
            <p className="section-kicker">Unconfirmed live data</p>
            <h3 id="home-live-title">Live timing</h3>
          </header>
          <p className="home-card__blurb">
            Connect to Formula 1&apos;s timing feed during a session for the
            running order, sectors and race control. Nothing here is stored as
            sporting data — the durable record of the same session arrives
            later through the archive.
          </p>
          <dl className="home-card__facts">
            <div>
              <dt>Status</dt>
              <dd>
                <span className={`home-dot home-dot--${summary.tone}`} aria-hidden="true" />
                {summary.headline}
              </dd>
            </div>
            <div>
              <dt>F1 TV account</dt>
              <dd>{authenticated ? "Connected" : "Not connected"}</dd>
            </div>
            <div>
              <dt>Recordings</dt>
              <dd>{recordings === null ? "—" : recordings}</dd>
            </div>
          </dl>
          <div className="home-card__actions">
            <button
              className="primary-action"
              onClick={() => onNavigate("live")}
              type="button"
            >
              Open live timing
              <span aria-hidden="true">↗</span>
            </button>
          </div>
        </article>
      </div>

      <p className="home__footnote">
        A live session needs an F1 TV subscription, and its frames are kept only
        for as long as retention allows. Past sessions you collected can be
        replayed from the live view.
      </p>
    </div>
  );
}
