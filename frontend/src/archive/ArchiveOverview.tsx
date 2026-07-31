import type {
  BackfillJob,
  FastF1RequestBudget,
  SeasonOverview,
} from "../contracts";
import MetricCard from "../shared/MetricCard";
import ProgressTrack from "../shared/ProgressTrack";
import StatusPill from "../shared/StatusPill";
import { formatDateTime } from "../shared/format";
import { JobPanel, RequestBudgetPanel } from "./BackfillPanels";

/** Coverage, upstream budget and background ingestion for one season. */
export default function ArchiveOverview({
  budget,
  budgetError,
  job,
  jobError,
  now,
  season,
}: {
  budget: FastF1RequestBudget | null;
  budgetError: string | null;
  job: BackfillJob | null;
  jobError: string | null;
  now: number;
  season: SeasonOverview;
}) {
  const counts = season.counts;
  const total = counts?.sessions ?? 0;
  // Anything the counters do not account for is still pending, so the bar
  // always sums to the session total rather than leaving a silent gap.
  const pending =
    (counts?.pending ?? 0) +
    Math.max(
      0,
      total -
        (counts?.pending ?? 0) -
        (counts?.running ?? 0) -
        (counts?.completed ?? 0) -
        (counts?.failed ?? 0),
    );

  return (
    <div className="workspace-view" data-view="overview">
      <section className="overview-panel" aria-labelledby="overview-title">
        <div className="section-heading">
          <div>
            <p className="section-kicker">Season coverage</p>
            <h2 id="overview-title">Archive overview</h2>
          </div>
          <div className="season-state">
            <span>Season state</span>
            <StatusPill status={season.status} />
          </div>
        </div>

        <div className="metric-grid">
          <MetricCard
            detail="Championship rounds discovered"
            label="Events"
            value={counts?.events ?? 0}
          />
          <MetricCard
            detail="Practice, qualifying and races"
            label="Sessions"
            value={counts?.sessions ?? 0}
          />
          <MetricCard
            detail="Finalized archive snapshots"
            label="Data ready"
            value={counts?.data_available ?? 0}
          />
          <MetricCard
            detail="Sessions currently due to ingest"
            label="Archive eligible"
            value={counts?.archive_eligible ?? 0}
          />
        </div>

        <div className="season-progress">
          <div className="season-progress__heading">
            <strong>Session archive coverage</strong>
            <span>
              {counts?.data_available ?? 0} / {total} available
            </span>
          </div>
          <ProgressTrack
            completed={counts?.completed ?? 0}
            failed={counts?.failed ?? 0}
            pending={pending}
            running={counts?.running ?? 0}
            total={total}
          />
          <div className="coverage-meta">
            <span>Coverage checked: {formatDateTime(season.coverage.checked_at)}</span>
            <span>Valid until: {formatDateTime(season.coverage.valid_until)}</span>
          </div>
        </div>
      </section>

      {budget ? <RequestBudgetPanel budget={budget} now={now} /> : null}
      {budgetError ? (
        <p className="inline-alert inline-alert--danger" role="alert">
          {budgetError}
        </p>
      ) : null}

      {job ? <JobPanel job={job} now={now} /> : null}
      {jobError ? (
        <p className="inline-alert inline-alert--danger" role="alert">
          {jobError}
        </p>
      ) : null}
    </div>
  );
}
