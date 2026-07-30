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

export type DataSource = "live_signalr" | "fastf1_archive" | "jolpica";

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

export interface DeferredFutureEvent {
  round_number: number;
  event_name: string;
  scheduled_start_at: string;
}

export interface SeasonOverview {
  year: number;
  status: SeasonStatus;
  coverage: SeasonCoverage;
  counts: SeasonCounts;
  active_job: ActiveJobSummary | null;
  events: SeasonEvent[];
  deferred_future_events: DeferredFutureEvent[];
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
  deferred_future_events: DeferredFutureEvent[];
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
  telemetry_requests: number;
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

export interface SessionSnapshot {
  data_available: boolean;
  source: DataSource | null;
  record_state: RecordState | null;
  completed_at: string | null;
  source_updated_at: string | null;
}

export interface SessionDetailIngestion {
  status: IngestionStatus;
  source: DataSource;
  record_state: RecordState;
  attempt_count: number;
  completed_at: string | null;
  next_retry_at: string | null;
  last_error: LastError | null;
}

export interface SessionDetail {
  id: string;
  session_key: string;
  session_name: string;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  event: {
    id: string;
    season_year: number;
    round_number: number;
    official_name: string | null;
    event_name: string;
    country: string | null;
    location: string | null;
    event_format: string | null;
  };
  snapshot: SessionSnapshot;
  ingestion: SessionDetailIngestion | null;
  counts: {
    entries: number;
    results: number;
    laps: number;
  };
}

export interface SessionResultDriver {
  id: string;
  jolpica_driver_id: string | null;
  given_name: string | null;
  family_name: string | null;
  full_name: string;
  country_code: string | null;
}

export interface SessionResultData {
  position: number | null;
  classified_position: string | null;
  grid_position: number | null;
  points: string | null;
  status: string | null;
  laps_completed: number | null;
  q1_time_us: number | null;
  q2_time_us: number | null;
  q3_time_us: number | null;
  elapsed_time_us: number | null;
  gap_to_leader_us: number | null;
  gap_to_leader_laps: number | null;
  source: DataSource;
  record_state: RecordState;
}

export interface SessionEntryResult {
  session_entry_id: string;
  driver: SessionResultDriver | null;
  racing_number: string | null;
  abbreviation: string | null;
  broadcast_name: string | null;
  display_name: string;
  team_jolpica_id: string | null;
  team_name: string | null;
  team_color_hex: string | null;
  source: DataSource;
  record_state: RecordState;
  result: SessionResultData | null;
}

export interface SessionResults {
  session_id: string;
  snapshot: SessionSnapshot;
  items: SessionEntryResult[];
}

export interface LapSummary {
  id: string;
  lap_number: number;
  stint_number: number | null;
  session_time_us: number | null;
  lap_time_us: number | null;
  lap_start_time_us: number | null;
  pit_out_time_us: number | null;
  pit_in_time_us: number | null;
  sector_1_time_us: number | null;
  sector_2_time_us: number | null;
  sector_3_time_us: number | null;
  sector_1_session_time_us: number | null;
  sector_2_session_time_us: number | null;
  sector_3_session_time_us: number | null;
  speed_i1_kph: number | null;
  speed_i2_kph: number | null;
  speed_fl_kph: number | null;
  speed_st_kph: number | null;
  is_personal_best: boolean | null;
  compound: string | null;
  tyre_life_laps: number | null;
  fresh_tyre: boolean | null;
  track_status: string | null;
  position: number | null;
  deleted: boolean | null;
  deleted_reason: string | null;
  fastf1_generated: boolean;
  is_accurate: boolean;
  source: DataSource;
  record_state: RecordState;
}

export interface LapSummaryResponse {
  session_id: string;
  session_entry_id: string;
  snapshot: SessionSnapshot;
  filters: {
    lap_from: number | null;
    lap_to: number | null;
    stint_number: number | null;
    include_deleted: boolean;
  };
  page: {
    limit: number;
    has_more: boolean;
    next_after_lap: number | null;
  };
  items: LapSummary[];
}

export interface LapSummaryRequest {
  after_lap?: number;
  limit?: number;
  lap_from?: number;
  lap_to?: number;
  stint_number?: number;
  include_deleted?: boolean;
}

export interface ApiErrorResponse {
  detail?: {
    code?: string;
    message?: string;
  };
}

/*
 * Live timing. These describe the separate ephemeral live path and never mix
 * with the archive contracts above: live rows are not stored as sporting data,
 * and every payload is explicitly unconfirmed.
 */

export interface LiveSessionSummary {
  session_date: string;
  event_name: string;
  session_key: string;
}

export interface LiveCollectorStats {
  accepted: number;
  duplicates: number;
  rejected: Record<string, number>;
  connection_attempts: number;
  reconnects: number;
  dropped_by_log_cap: number;
}

export interface LiveCollectorStatus {
  state: string;
  session: LiveSessionSummary;
  topics_subscribed: string[];
  log_degraded: boolean;
  subscribers: number;
  stats: LiveCollectorStats;
}

/** Observable F1 TV auth state. The token value is never sent to the client. */
export interface LiveAuthStatus {
  authenticated: boolean;
  expired: boolean;
  expires_at: string | null;
  seconds_remaining: number;
  expiry_source: string | null;
  token_source: string | null;
  /** One-click entry point that primes the companion extension with our port. */
  companion_url: string | null;
}

export interface LiveStatus {
  record_state: string;
  active: boolean;
  feed_configured: boolean;
  retention_days: number;
  log_directory_bytes: number;
  max_directory_bytes: number;
  authentication: LiveAuthStatus;
  session: LiveCollectorStatus | null;
}

/**
 * The feed has no sequence number. A connect delivers one full-state frame per
 * topic and every later frame is a deep partial delta, so the backend publishes
 * accumulated merged state and counts snapshots and merged updates.
 */
export interface LiveTopicState {
  received_at: string;
  feed_timestamp: string | null;
  snapshots: number;
  updates: number;
  payload: Record<string, unknown>;
}

export interface LiveViewState {
  latest_received_at: string | null;
  applied_frames: number;
  topics: Record<string, LiveTopicState>;
}

export interface LiveSessionRequest {
  session_date: string;
  event_name: string;
  session_key: string;
}

export type LiveStreamMessage =
  | {
      type: "snapshot";
      record_state: string;
      session: LiveCollectorStatus | null;
      state: LiveViewState;
    }
  | {
      type: "update";
      topic: string;
      initial: boolean;
      received_at: string;
      /** Merged topic state, not the raw delta. */
      payload: Record<string, unknown>;
    }
  | { type: "error"; code: string; message: string };
