export type IngestionStatus = "pending" | "running" | "completed" | "failed";

export type SeasonStatus =
  | "missing"
  | "pending"
  | "running"
  | "partial"
  | "completed"
  | "stale"
  | "failed";

export type RecordState = "provisional" | "finalized";

export interface LastError {
  code: string;
  message: string;
}

export interface SeasonCoverage {
  checked_at: string | null;
  valid_until: string | null;
  is_stale: boolean;
}

export interface ActiveJobSummary {
  id: string;
  status: IngestionStatus;
}

export interface SeasonCounts {
  events: number;
  sessions: number;
  archive_eligible: number;
  data_available: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
}

export interface ArchiveEligibility {
  eligible: boolean;
  reason:
    | "schedule_end_missing"
    | "availability_grace"
    | "initial_archive"
    | "checkpoint_pending"
    | "correction_checkpoint"
    | "stable";
  eligible_at: string | null;
}

export interface SessionIngestion {
  status: IngestionStatus;
  record_state: RecordState;
  attempt_count: number;
  completed_at: string | null;
  next_retry_at: string | null;
  last_error: LastError | null;
}

export interface SeasonSession {
  id: string;
  session_key: string;
  session_name: string;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  archive_eligibility: ArchiveEligibility;
  ingestion: SessionIngestion | null;
  data_available: boolean;
}

export interface SeasonEvent {
  id: string;
  round_number: number;
  official_name: string | null;
  event_name: string;
  country: string | null;
  location: string | null;
  event_format: string | null;
  starts_at: string | null;
  ends_at: string | null;
  sessions: SeasonSession[];
}

export interface SeasonOverview {
  year: number;
  status: SeasonStatus;
  coverage: SeasonCoverage;
  counts: SeasonCounts;
  active_job: ActiveJobSummary | null;
  events: SeasonEvent[];
}

export interface BackfillCoverage {
  refresh_reason: "fresh" | "missing" | "stale";
  refreshed: boolean;
  checked_at: string | null;
  valid_until: string | null;
}

export interface EnsureBackfillResponse {
  season_year: number;
  action:
    | "job_created"
    | "job_reused"
    | "coverage_refreshed"
    | "no_action";
  coverage: BackfillCoverage;
  job: ActiveJobSummary | null;
  eligible_session_count: number;
  newly_queued_session_count: number;
  deferred_future_events: {
    round_number: number;
    event_name: string;
    scheduled_start_at: string;
  }[];
}

export interface JobProgressCounts {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  terminal: number;
}

export interface BackfillJobSession {
  session_id: string;
  round_number: number;
  event_name: string;
  session_key: string;
  session_name: string;
  status: IngestionStatus;
  attempt_count: number;
  queued_at: string;
  started_at: string | null;
  heartbeat_at: string | null;
  next_retry_at: string | null;
  completed_at: string | null;
  last_error: LastError | null;
}

export type BackfillExecutionPhase =
  | "ready"
  | "fetching"
  | "pacing"
  | "rate_limit_cooldown"
  | "request_budget_cooldown"
  | "retry_backoff"
  | "idle"
  | "terminal";

export interface BackfillSessionReference {
  session_id: string;
  round_number: number;
  event_name: string;
  session_name: string;
}

export interface BackfillExecution {
  observed_at: string;
  phase: BackfillExecutionPhase;
  current_session: BackfillSessionReference | null;
  next_session: BackfillSessionReference | null;
  last_completed_session: BackfillSessionReference | null;
  next_action_at: string | null;
}

export interface BackfillJob {
  id: string;
  season_year: number;
  status: IngestionStatus;
  request_reason: string;
  requested_at: string;
  started_at: string | null;
  heartbeat_at: string | null;
  completed_at: string | null;
  last_error: LastError | null;
  progress: JobProgressCounts;
  execution: BackfillExecution;
  sessions: BackfillJobSession[];
}

export interface FastF1RequestBudget {
  source: "fastf1";
  window_seconds: number;
  observed_at: string;
  observed_requests: number;
  archive_requests: number;
  schedule_requests: number;
  library_limit: number;
  operational_ceiling: number;
  warning_threshold: number;
  remaining_before_pause: number;
  next_capacity_at: string | null;
  cooldown_until: string | null;
  cooldown_reason: "rate_limit" | "budget" | null;
  status: "available" | "warning" | "paused" | "rate_limited";
  authoritative: false;
}

export interface ApiErrorResponse {
  detail?: {
    code?: string;
    message?: string;
  };
}
