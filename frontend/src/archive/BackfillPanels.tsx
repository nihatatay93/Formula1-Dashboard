import type {
  BackfillJob,
  BackfillJobSession,
  FastF1RequestBudget,
} from "../contracts";
import ProgressTrack from "../shared/ProgressTrack";
import StatusPill from "../shared/StatusPill";
import { formatCountdown, formatDateTime, humanize } from "../shared/format";

function sessionReferenceLabel(
  session: BackfillJob["execution"]["current_session"],
): string {
  return session
    ? `R${session.round_number} · ${session.event_name} — ${session.session_name}`
    : "None";
}

export function RequestBudgetPanel({
  budget,
  now,
}: {
  budget: FastF1RequestBudget;
  now: number;
}) {
  const percentage = Math.min(
    100,
    (budget.observed_requests / budget.operational_ceiling) * 100,
  );
  const cooldownTarget = budget.cooldown_until ?? budget.next_capacity_at;

  return (
    <section className={`budget-panel budget-panel--${budget.status}`}>
      <div>
        <p className="section-kicker">FastF1 local safety budget</p>
        <strong>
          {budget.observed_requests}
          <span> / {budget.operational_ceiling}</span>
        </strong>
        <p>
          {budget.archive_requests} archive · {budget.schedule_requests} schedule
          {" · "}
          {budget.telemetry_requests} telemetry requests in the rolling hour
        </p>
      </div>
      <div className="budget-panel__meter">
        <span style={{ width: `${percentage}%` }} />
      </div>
      <div className="budget-panel__status">
        <span>{humanize(budget.status)}</span>
        <strong>
          {cooldownTarget
            ? `${formatCountdown(cooldownTarget, now)} until capacity`
            : `${budget.remaining_before_pause} requests available`}
        </strong>
        <small>
          Local estimate · FastF1 library threshold {budget.library_limit}
        </small>
      </div>
    </section>
  );
}

function JobSessionGroups({
  sessions,
}: {
  sessions: BackfillJobSession[];
}) {
  const groups = Array.from(
    sessions.reduce((grouped, session) => {
      const key = `${session.round_number}:${session.event_name}`;
      const group = grouped.get(key) ?? [];
      group.push(session);
      grouped.set(key, group);
      return grouped;
    }, new Map<string, BackfillJobSession[]>()),
  );

  return (
    <div className="job-session-groups">
      {groups.map(([key, group]) => {
        const first = group[0];
        const terminal = group.filter(
          (session) =>
            session.status === "completed" || session.status === "failed",
        ).length;
        const active = group.some((session) => session.status === "running");
        return (
          <details className="job-event" key={key} open={active}>
            <summary>
              <span>R{String(first.round_number).padStart(2, "0")}</span>
              <strong>{first.event_name}</strong>
              <small>
                {terminal}/{group.length} terminal
              </small>
            </summary>
            <div>
              {group.map((session) => (
                <div className="job-session-row" key={session.session_id}>
                  <div>
                    <strong>{session.session_name}</strong>
                    <span>
                      Attempt {session.attempt_count}
                      {session.next_retry_at
                        ? ` · retry ${formatDateTime(session.next_retry_at)}`
                        : ""}
                    </span>
                  </div>
                  <StatusPill status={session.status} />
                  {session.last_error ? (
                    <small>{session.last_error.message}</small>
                  ) : null}
                </div>
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}

export function JobPanel({ job, now }: { job: BackfillJob; now: number }) {
  const completion =
    job.progress.total === 0
      ? 0
      : Math.round((job.progress.terminal / job.progress.total) * 100);

  return (
    <section
      className="job-panel"
      aria-labelledby="job-progress-title"
      aria-live="polite"
    >
      <div className="section-heading section-heading--compact">
        <div>
          <p className="section-kicker">Background ingestion</p>
          <h2 id="job-progress-title">Backfill progress</h2>
        </div>
        <div className="job-panel__summary">
          <strong>{completion}%</strong>
          <StatusPill status={job.status} />
        </div>
      </div>

      <ProgressTrack {...job.progress} />

      <div className="job-execution">
        <div className="job-execution__phase">
          <span>{humanize(job.execution.phase)}</span>
          <strong>
            {job.execution.next_action_at
              ? formatCountdown(job.execution.next_action_at, now)
              : job.execution.phase === "fetching"
                ? "Live"
                : "—"}
          </strong>
          <small>
            {job.execution.next_action_at
              ? "until next operation"
              : "execution state"}
          </small>
        </div>
        <div>
          <span>Fetching now</span>
          <strong>
            {sessionReferenceLabel(job.execution.current_session)}
          </strong>
        </div>
        <div>
          <span>Next fetch</span>
          <strong>{sessionReferenceLabel(job.execution.next_session)}</strong>
        </div>
        <div>
          <span>Last completed</span>
          <strong>
            {sessionReferenceLabel(job.execution.last_completed_session)}
          </strong>
        </div>
      </div>

      <div className="job-panel__meta">
        <span>Requested {formatDateTime(job.requested_at)}</span>
        <span>Reason: {humanize(job.request_reason)}</span>
        <span className="job-panel__id" title={job.id}>
          Job {job.id.slice(0, 8)}
        </span>
      </div>

      {job.last_error ? (
        <p className="inline-alert inline-alert--danger">
          {job.last_error.message}
        </p>
      ) : null}

      <JobSessionGroups sessions={job.sessions} />
    </section>
  );
}