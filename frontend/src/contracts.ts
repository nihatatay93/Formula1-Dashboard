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

/** One telemetry sample along a lap. Every channel is optional upstream. */
export interface LapTelemetrySample {
  sample_index: number;
  lap_time_us: number;
  session_time_us: number | null;
  distance_m: number | null;
  relative_distance: number | null;
  speed_kph: number | null;
  rpm: number | null;
  gear: number | null;
  throttle_percent: number | null;
  brake: boolean | null;
  drs: number | null;
  x: number | null;
  y: number | null;
  z: number | null;
}

export type TelemetryIngestionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface LapTelemetryIngestionState {
  status: TelemetryIngestionStatus;
  attempt_count: number;
  sample_count: number;
  requested_at: string;
  heartbeat_at: string | null;
  next_retry_at: string | null;
  completed_at: string | null;
  last_error: { code: string; message: string } | null;
}

export interface LapTelemetryResponse {
  session_id: string;
  session_entry_id: string;
  lap_id: string;
  lap_number: number;
  /** False while ingestion is incomplete, empty, or from a superseded snapshot. */
  data_available: boolean;
  snapshot: {
    /** False when the archive snapshot moved on after telemetry was stored. */
    compatible: boolean;
    source_snapshot_completed_at: string;
    current_snapshot_completed_at: string;
  };
  ingestion: LapTelemetryIngestionState;
  page: {
    limit: number;
    has_more: boolean;
    next_after_sample: number | null;
  };
  items: LapTelemetrySample[];
}

/** `available` answers immediately; `queued` and `reused` need the worker. */
export type TelemetryCommandAction = "queued" | "reused" | "available";

export interface EnsureLapTelemetryResponse {
  session_id: string;
  session_entry_id: string;
  lap_id: string;
  lap_number: number;
  action: TelemetryCommandAction;
  status: TelemetryIngestionStatus;
  source_snapshot_completed_at: string;
}

export interface LapSummaryRequest {
  after_lap?: number;
  limit?: number;
  lap_from?: number;
  lap_to?: number;
  stint_number?: number;
  include_deleted?: boolean;
}

/**
 * Whether this deployment requires a sign-in, and whether the caller has one.
 *
 * `required: false` is a deployment that was deliberately left open — a local
 * stack bound to loopback — and the dashboard renders straight through.
 */
export interface AuthSession {
  authenticated: boolean;
  required: boolean;
  kind: string | null;
  expires_at: string | null;
}

export interface LoginResult {
  authenticated: boolean;
  /** For native clients. The browser is authenticated by the cookie instead. */
  token: string;
  expires_at: string;
}

/**
 * Championship standings, aggregated server-side from stored results.
 *
 * `scoring_sessions` is how many sessions the table was computed from, so a
 * reader can tell a mid-season standing from one built on an incomplete
 * archive. Points arrive as strings because they are exact decimals.
 */
export interface StandingsRound {
  round_number: number;
  event_name: string;
  session_key: string;
  session_id: string;
}

export interface StandingsRoundPoints {
  round_number: number;
  session_key: string;
  points: string;
  position: number | null;
}

export interface DriverStanding {
  position: number;
  driver_id: string;
  display_name: string;
  abbreviation: string | null;
  team_name: string | null;
  team_color: string | null;
  points: string;
  wins: number;
  podiums: number;
  poles: number;
  starts: number;
  /** Races entered but not classified; a classified retirement is not one. */
  dnfs: number;
  best_finish: number | null;
  rounds: StandingsRoundPoints[];
}

export interface ConstructorStanding {
  position: number;
  team_name: string;
  team_color: string | null;
  points: string;
  wins: number;
  podiums: number;
  poles: number;
  best_finish: number | null;
  drivers: string[];
  rounds: StandingsRoundPoints[];
}

export interface DriverStandingsResponse {
  season_year: number;
  scoring_sessions: number;
  rounds: StandingsRound[];
  items: DriverStanding[];
}

export interface ConstructorStandingsResponse {
  season_year: number;
  scoring_sessions: number;
  rounds: StandingsRound[];
  items: ConstructorStanding[];
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
  /** Null until the feed's SessionInfo has named the session. */
  session: LiveSessionSummary | null;
  topics_subscribed: string[];
  /** True when this is a recorded session being replayed, not the live feed. */
  replay: boolean;
  /** True once a replay reached the end of its recording. */
  finished: boolean;
  log_degraded: boolean;
  subscribers: number;
  stats: LiveCollectorStats;
}

/**
 * A session log left behind by an earlier live session, still inside the
 * retention window. Identity is recovered from the file name, so a recording
 * captured outside the naming convention reports a null date and empty session.
 */
export interface LiveRecording {
  name: string;
  event_name: string;
  session_key: string;
  session_date: string | null;
  size_bytes: number;
  modified_at: string;
}

export interface LiveRecordingList {
  record_state: string;
  retention_days: number;
  items: LiveRecording[];
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
  /** Allowlisted display claims only; never subscriber identifiers. */
  subscription: { product?: string; status?: string; first_name?: string };
}

export interface LiveStatus {
  record_state: string;
  active: boolean;
  feed_configured: boolean;
  retention_days: number;
  log_directory_bytes: number;
  max_directory_bytes: number;
  requires_authentication: boolean;
  authentication: LiveAuthStatus;
  session: LiveCollectorStatus | null;
}

/**
 * The feed has no sequence number. A connect delivers one full-state frame per
 * topic and every later frame is a deep partial delta, so the backend publishes
 * accumulated merged state and counts snapshots and merged updates.
 */
/**
 * Micro-sector state, already resolved server-side from the feed's numeric
 * status codes. The codes carry no documented meaning, so the mapping lives in
 * one place — `app/live/board.py` — where it was derived from a recording.
 */
export type LiveSegmentStatus =
  | "pending"
  | "yellow"
  | "green"
  | "purple"
  | "pit"
  | "unknown";

export interface LiveSectorCell {
  value: string;
  personal_best: boolean;
  overall_best: boolean;
  segments: LiveSegmentStatus[];
}

export interface LiveDriverRow {
  racing_number: string;
  tla: string;
  full_name: string;
  team_name: string;
  team_colour: string;
  position: number | null;
  line: number;
  /**
   * Places gained since the collector first saw this driver; positive is a
   * gain. The feed carries no grid position, so this means "since the session
   * was connected", which `position_baseline` makes explicit.
   */
  places_gained: number | null;
  position_baseline: number | null;
  /** "up" or "down" while a place change is recent, otherwise empty. */
  recent_move: string;
  gap_to_leader: string;
  interval: string;
  last_lap: string;
  last_lap_personal_best: boolean;
  last_lap_overall_best: boolean;
  best_lap: string;
  sectors: LiveSectorCell[];
  compound: string;
  tyre_age: number | null;
  pit_stops: number | null;
  laps: number | null;
  in_pit: boolean;
  pit_out: boolean;
  retired: boolean;
  stopped: boolean;
  knocked_out: boolean;
  status: string;
}

export interface LiveRaceControlMessage {
  utc: string;
  category: string;
  message: string;
  lap: number | null;
  flag: string;
}

/** Display-ready board, normalised server-side from the raw feed topics. */
export interface LiveBoard {
  meeting_name: string;
  session_name: string;
  session_type: string;
  session_status: string;
  started: string;
  track_status: string;
  track_status_code: string;
  current_lap: number | null;
  total_laps: number | null;
  remaining: string;
  extrapolating: boolean;
  weather: Record<string, string>;
  drivers: LiveDriverRow[];
  race_control: LiveRaceControlMessage[];
}

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

export type LiveStreamMessage =
  | {
      type: "snapshot" | "board";
      record_state: string;
      session: LiveCollectorStatus | null;
      board: LiveBoard;
    }
  | { type: "error"; code: string; message: string };


export interface RacePaceLap {
  lap_number: number;
  lap_time_us: number | null;
  stint_number: number | null;
  compound: string | null;
  tyre_life_laps: number | null;
  position: number | null;
  is_clean: boolean;
  is_personal_best: boolean | null;
  beyond_cutoff: boolean;
}

export interface RacePaceEntry {
  session_entry_id: string;
  driver_id: string | null;
  display_name: string;
  abbreviation: string | null;
  racing_number: string | null;
  team_name: string | null;
  team_color_hex: string | null;
  finishing_position: number | null;
  laps: RacePaceLap[];
}

export interface RacePaceResponse {
  session_id: string;
  snapshot: SessionSnapshot;
  filters: {
    clean_only: boolean;
    outlier_cutoff: number;
  };
  clean_lap_definition: string;
  session_best_lap_time_us: number | null;
  outlier_cutoff_lap_time_us: number | null;
  items: RacePaceEntry[];
}


export interface ComparedDriver {
  driver_id: string;
  display_name: string;
  abbreviation: string | null;
  team_name: string | null;
  team_color_hex: string | null;
}

export interface HeadToHeadRecord {
  basis: string;
  a_ahead: number;
  b_ahead: number;
  compared: number;
  excluded: number;
}

export interface SeasonTotals {
  points: string;
  wins: number;
  podiums: number;
  poles: number;
  starts: number;
  dnfs: number;
  best_finish: number | null;
}

export interface HeadToHeadResponse {
  season_year: number;
  driver_a: ComparedDriver;
  driver_b: ComparedDriver;
  qualifying: HeadToHeadRecord;
  race: HeadToHeadRecord;
  totals_a: SeasonTotals;
  totals_b: SeasonTotals;
  never_met: boolean;
}

export interface ConsistencyRow {
  driver_id: string;
  display_name: string;
  abbreviation: string | null;
  team_name: string | null;
  team_color_hex: string | null;
  clean_laps: number;
  median_percent: number | null;
  std_dev_percent: number | null;
  iqr_percent: number | null;
  races_started: number;
  races_classified: number;
  finish_rate: number | null;
}

export interface ConsistencyResponse {
  season_year: number;
  clean_lap_definition: string;
  basis: string;
  items: ConsistencyRow[];
}
