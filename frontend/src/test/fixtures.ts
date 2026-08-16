import type {
  BackfillJob,
  ConstructorStandingsResponse,
  DriverStandingsResponse,
  EnsureBackfillResponse,
  EnsureLapTelemetryResponse,
  FastF1RequestBudget,
  LapSummary,
  LapSummaryResponse,
  LapTelemetryResponse,
  SeasonEvent,
  SeasonOverview,
  SeasonSession,
  SessionDetail,
  SessionEntryResult,
  SessionResults,
  SessionSnapshot,
} from "../contracts";

export const completedSnapshot: SessionSnapshot = {
  data_available: true,
  source: "fastf1_archive",
  record_state: "finalized",
  completed_at: "2026-07-28T12:00:00Z",
  source_updated_at: "2026-07-28T12:00:00Z",
};

export const unavailableSnapshot: SessionSnapshot = {
  data_available: false,
  source: null,
  record_state: null,
  completed_at: null,
  source_updated_at: null,
};

export const completedSession: SeasonSession = {
  id: "101",
  session_key: "race",
  session_name: "Race",
  scheduled_start_at: "2026-03-08T14:00:00Z",
  scheduled_end_at: "2026-03-08T16:00:00Z",
  archive_eligibility: {
    eligible: false,
    reason: "stable",
    eligible_at: null,
  },
  ingestion: {
    status: "completed",
    record_state: "finalized",
    attempt_count: 1,
    completed_at: completedSnapshot.completed_at,
    next_retry_at: null,
    last_error: null,
  },
  data_available: true,
};

export const completedEvent: SeasonEvent = {
  id: "11",
  round_number: 1,
  official_name: "Australian Grand Prix",
  event_name: "Australian Grand Prix",
  country: "Australia",
  location: "Melbourne",
  event_format: "conventional",
  starts_at: "2026-03-06T01:30:00Z",
  ends_at: "2026-03-08T16:00:00Z",
  sessions: [completedSession],
};

export const completedSeason: SeasonOverview = {
  year: 2026,
  status: "completed",
  coverage: {
    checked_at: "2026-07-28T11:00:00Z",
    valid_until: "2026-07-28T17:00:00Z",
    is_stale: false,
  },
  counts: {
    events: 1,
    sessions: 1,
    archive_eligible: 0,
    data_available: 1,
    pending: 0,
    running: 0,
    completed: 1,
    failed: 0,
  },
  active_job: null,
  events: [completedEvent],
  deferred_future_events: [
    {
      round_number: 12,
      event_name: "Dutch Grand Prix",
      scheduled_start_at: "2026-08-21T10:30:00Z",
    },
  ],
};

export const missingSeason: SeasonOverview = {
  year: 2025,
  status: "missing",
  coverage: {
    checked_at: null,
    valid_until: null,
    is_stale: true,
  },
  counts: {
    events: 0,
    sessions: 0,
    archive_eligible: 0,
    data_available: 0,
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
  },
  active_job: null,
  events: [],
  deferred_future_events: [],
};

export const sessionDetail: SessionDetail = {
  id: completedSession.id,
  session_key: completedSession.session_key,
  session_name: completedSession.session_name,
  scheduled_start_at: completedSession.scheduled_start_at,
  scheduled_end_at: completedSession.scheduled_end_at,
  event: {
    id: completedEvent.id,
    season_year: 2026,
    round_number: completedEvent.round_number,
    official_name: completedEvent.official_name,
    event_name: completedEvent.event_name,
    country: completedEvent.country,
    location: completedEvent.location,
    event_format: completedEvent.event_format,
  },
  snapshot: completedSnapshot,
  ingestion: {
    status: "completed",
    source: "fastf1_archive",
    record_state: "finalized",
    attempt_count: 1,
    completed_at: completedSnapshot.completed_at,
    next_retry_at: null,
    last_error: null,
  },
  counts: {
    entries: 2,
    results: 2,
    laps: 4,
  },
};

export const unavailableSessionDetail: SessionDetail = {
  ...sessionDetail,
  snapshot: unavailableSnapshot,
  ingestion: {
    status: "pending",
    source: "fastf1_archive",
    record_state: "finalized",
    attempt_count: 0,
    completed_at: null,
    next_retry_at: null,
    last_error: null,
  },
  counts: {
    entries: 0,
    results: 0,
    laps: 0,
  },
};

export const resultEntries: SessionEntryResult[] = [
  {
    session_entry_id: "201",
    driver: {
      id: "301",
      jolpica_driver_id: "norris",
      given_name: "Lando",
      family_name: "Norris",
      full_name: "Lando Norris",
      country_code: "GBR",
    },
    racing_number: "1",
    abbreviation: "NOR",
    broadcast_name: "L NORRIS",
    display_name: "Lando Norris",
    team_jolpica_id: "mclaren",
    team_name: "McLaren",
    team_color_hex: "#FF8700",
    source: "fastf1_archive",
    record_state: "finalized",
    result: {
      position: 1,
      classified_position: "1",
      grid_position: 1,
      points: "25.000",
      status: "Finished",
      laps_completed: 58,
      q1_time_us: null,
      q2_time_us: null,
      q3_time_us: null,
      elapsed_time_us: 5_400_000_000,
      gap_to_leader_us: null,
      gap_to_leader_laps: null,
      source: "fastf1_archive",
      record_state: "finalized",
    },
  },
  {
    session_entry_id: "202",
    driver: {
      id: "302",
      jolpica_driver_id: "piastri",
      given_name: "Oscar",
      family_name: "Piastri",
      full_name: "Oscar Piastri",
      country_code: "AUS",
    },
    racing_number: "81",
    abbreviation: "PIA",
    broadcast_name: "O PIASTRI",
    display_name: "Oscar Piastri",
    team_jolpica_id: "mclaren",
    team_name: "McLaren",
    team_color_hex: "#FF8700",
    source: "fastf1_archive",
    record_state: "finalized",
    result: {
      position: 2,
      classified_position: "2",
      grid_position: 2,
      points: "18.000",
      status: "Finished",
      laps_completed: 58,
      q1_time_us: null,
      q2_time_us: null,
      q3_time_us: null,
      elapsed_time_us: null,
      gap_to_leader_us: 5_500_000,
      gap_to_leader_laps: null,
      source: "fastf1_archive",
      record_state: "finalized",
    },
  },
  {
    session_entry_id: "203",
    driver: {
      id: "303",
      jolpica_driver_id: "russell",
      given_name: "George",
      family_name: "Russell",
      full_name: "George Russell",
      country_code: "GBR",
    },
    racing_number: "63",
    abbreviation: "RUS",
    broadcast_name: "G RUSSELL",
    display_name: "George Russell",
    team_jolpica_id: "mercedes",
    team_name: "Mercedes",
    team_color_hex: "#27F4D2",
    source: "fastf1_archive",
    record_state: "finalized",
    result: {
      position: 3,
      classified_position: "3",
      grid_position: 3,
      points: "15.000",
      status: "Finished",
      laps_completed: 58,
      q1_time_us: null,
      q2_time_us: null,
      q3_time_us: null,
      elapsed_time_us: null,
      gap_to_leader_us: 9_100_000,
      gap_to_leader_laps: null,
      source: "fastf1_archive",
      record_state: "finalized",
    },
  },
];

export const sessionResults: SessionResults = {
  session_id: completedSession.id,
  snapshot: completedSnapshot,
  items: resultEntries,
};

function lap(
  id: string,
  lapNumber: number,
  lapTimeUs: number,
  compound: "SOFT" | "MEDIUM" | "HARD",
): LapSummary {
  return {
    id,
    lap_number: lapNumber,
    stint_number: 1,
    session_time_us: lapNumber * 90_000_000,
    lap_time_us: lapTimeUs,
    lap_start_time_us: (lapNumber - 1) * 90_000_000,
    pit_out_time_us: null,
    pit_in_time_us: null,
    sector_1_time_us: 28_000_000,
    sector_2_time_us: 31_000_000,
    sector_3_time_us: lapTimeUs - 59_000_000,
    sector_1_session_time_us: null,
    sector_2_session_time_us: null,
    sector_3_session_time_us: null,
    speed_i1_kph: 286,
    speed_i2_kph: 294,
    speed_fl_kph: 307,
    speed_st_kph: 318,
    is_personal_best: lapNumber === 2,
    compound,
    tyre_life_laps: lapNumber,
    fresh_tyre: lapNumber === 1,
    track_status: "1",
    position: 1,
    deleted: false,
    deleted_reason: null,
    fastf1_generated: false,
    is_accurate: true,
    source: "fastf1_archive",
    record_state: "finalized",
  };
}

export const firstLapPage: LapSummaryResponse = {
  session_id: completedSession.id,
  session_entry_id: resultEntries[0].session_entry_id,
  snapshot: completedSnapshot,
  filters: {
    lap_from: null,
    lap_to: null,
    stint_number: null,
    include_deleted: true,
  },
  page: {
    limit: 50,
    has_more: true,
    next_after_lap: 2,
  },
  items: [
    lap("401", 1, 91_100_000, "MEDIUM"),
    lap("402", 2, 90_400_000, "MEDIUM"),
  ],
};

export const secondLapPage: LapSummaryResponse = {
  ...firstLapPage,
  page: {
    limit: 50,
    has_more: false,
    next_after_lap: null,
  },
  items: [
    lap("403", 3, 90_700_000, "MEDIUM"),
    lap("404", 4, 90_900_000, "HARD"),
  ],
};

export const piastriLapPage: LapSummaryResponse = {
  ...firstLapPage,
  session_entry_id: resultEntries[1].session_entry_id,
  page: {
    limit: 50,
    has_more: false,
    next_after_lap: null,
  },
  items: [
    {
      ...firstLapPage.items[0],
      id: "451",
      lap_time_us: 91_700_000,
    },
    {
      ...firstLapPage.items[1],
      id: "452",
      lap_time_us: 91_300_000,
    },
  ],
};

export const requestBudget: FastF1RequestBudget = {
  source: "fastf1",
  window_seconds: 3_600,
  observed_at: "2026-07-28T12:05:00Z",
  observed_requests: 20,
  archive_requests: 18,
  schedule_requests: 2,
  telemetry_requests: 0,
  library_limit: 500,
  operational_ceiling: 450,
  warning_threshold: 400,
  remaining_before_pause: 430,
  next_capacity_at: null,
  cooldown_until: null,
  cooldown_reason: null,
  status: "available",
  authoritative: false,
};

export const queuedBackfill: EnsureBackfillResponse = {
  season_year: 2025,
  action: "job_created",
  coverage: {
    refresh_reason: "missing",
    refreshed: true,
    checked_at: "2026-07-28T12:10:00Z",
    valid_until: "2026-08-27T12:10:00Z",
  },
  job: {
    id: "00000000-0000-4000-8000-000000000001",
    status: "pending",
  },
  eligible_session_count: 1,
  newly_queued_session_count: 1,
  deferred_future_events: [],
};

export const runningBackfill: BackfillJob = {
  id: queuedBackfill.job!.id,
  season_year: 2025,
  status: "running",
  request_reason: "missing",
  requested_at: "2026-07-28T12:10:00Z",
  started_at: "2026-07-28T12:10:01Z",
  heartbeat_at: "2026-07-28T12:10:02Z",
  completed_at: null,
  last_error: null,
  progress: {
    total: 1,
    pending: 0,
    running: 1,
    completed: 0,
    failed: 0,
    terminal: 0,
  },
  execution: {
    observed_at: "2026-07-28T12:10:02Z",
    phase: "fetching",
    current_session: {
      session_id: "501",
      round_number: 1,
      event_name: "Australian Grand Prix",
      session_name: "Practice 1",
    },
    next_session: null,
    last_completed_session: null,
    next_action_at: null,
  },
  sessions: [
    {
      session_id: "501",
      round_number: 1,
      event_name: "Australian Grand Prix",
      session_key: "practice-1",
      session_name: "Practice 1",
      status: "running",
      attempt_count: 1,
      queued_at: "2026-07-28T12:10:00Z",
      started_at: "2026-07-28T12:10:01Z",
      heartbeat_at: "2026-07-28T12:10:02Z",
      next_retry_at: null,
      completed_at: null,
      last_error: null,
    },
  ],
};

/** One lap's telemetry: a braking zone into a corner, then acceleration. */
export const lapTelemetry: LapTelemetryResponse = {
  session_id: completedSession.id,
  session_entry_id: resultEntries[0].session_entry_id,
  lap_id: "401",
  lap_number: 1,
  data_available: true,
  snapshot: {
    compatible: true,
    source_snapshot_completed_at: completedSnapshot.completed_at as string,
    current_snapshot_completed_at: completedSnapshot.completed_at as string,
  },
  ingestion: {
    status: "completed",
    attempt_count: 1,
    sample_count: 10,
    requested_at: "2026-03-08T06:00:00Z",
    heartbeat_at: null,
    next_retry_at: null,
    completed_at: "2026-03-08T06:01:00Z",
    last_error: null,
  },
  page: { limit: 1000, has_more: false, next_after_sample: null },
  items: Array.from({ length: 10 }, (_, index) => {
    const braking = index > 3 && index < 7;
    return {
      sample_index: index,
      lap_time_us: index * 50_000,
      session_time_us: null,
      distance_m: index * 120,
      relative_distance: index / 10,
      speed_kph: braking ? 295 - (index - 3) * 55 : 190 + index * 11,
      rpm: 11_000,
      gear: braking ? 3 : 7,
      throttle_percent: braking ? 0 : 100,
      brake: braking,
      drs: 0,
      x: null,
      y: null,
      z: null,
    };
  }),
};

export const ensureLapTelemetryAvailable: EnsureLapTelemetryResponse = {
  session_id: completedSession.id,
  session_entry_id: resultEntries[0].session_entry_id,
  lap_id: "401",
  lap_number: 1,
  action: "available",
  status: "completed",
  source_snapshot_completed_at: completedSnapshot.completed_at as string,
};

export const driverStandings: DriverStandingsResponse = {
  season_year: 2026,
  scoring_sessions: 1,
  rounds: [
    {
      round_number: 1,
      event_name: "Australian Grand Prix",
      session_key: "race",
      session_id: completedSession.id,
    },
  ],
  items: [
    {
      position: 1,
      driver_id: "11",
      display_name: "Lando Norris",
      abbreviation: "NOR",
      team_name: "McLaren",
      team_color: "F47600",
      points: "25.000",
      wins: 1,
      podiums: 1,
      poles: 1,
      starts: 1,
      dnfs: 0,
      best_finish: 1,
      rounds: [
        { round_number: 1, session_key: "race", points: "25.000", position: 1 },
      ],
    },
    {
      position: 2,
      driver_id: "12",
      display_name: "Oscar Piastri",
      abbreviation: "PIA",
      team_name: "McLaren",
      team_color: "F47600",
      points: "18.000",
      wins: 0,
      podiums: 1,
      poles: 0,
      starts: 1,
      dnfs: 0,
      best_finish: 2,
      rounds: [
        { round_number: 1, session_key: "race", points: "18.000", position: 2 },
      ],
    },
  ],
};

export const constructorStandings: ConstructorStandingsResponse = {
  season_year: 2026,
  scoring_sessions: 1,
  rounds: driverStandings.rounds,
  items: [
    {
      position: 1,
      team_name: "McLaren",
      team_color: "F47600",
      points: "43.000",
      wins: 1,
      podiums: 2,
      poles: 1,
      best_finish: 1,
      drivers: ["Lando Norris", "Oscar Piastri"],
      rounds: [
        { round_number: 1, session_key: "race", points: "43.000", position: 1 },
      ],
    },
  ],
};

/**
 * Race pace for one session: two drivers, one of whom has a gap in their clean
 * laps, so a spec can prove the evolution chart never bridges it.
 */
export const racePace = {
  session_id: "5001",
  snapshot: {
    data_available: true,
    source: "fastf1_archive",
    record_state: "finalized",
    completed_at: "2026-03-08T06:00:00Z",
    source_updated_at: "2026-03-08T06:00:00Z",
  },
  filters: { clean_only: false, outlier_cutoff: 107 },
  clean_lap_definition:
    "A lap is clean when it has a recorded time, FastF1 marks it accurate, " +
    "it was not deleted, the car neither entered nor left the pits on it, " +
    'and the track was green for the whole lap (track status exactly "1").',
  session_best_lap_time_us: 80_000_000,
  outlier_cutoff_lap_time_us: 85_600_000,
  items: [
    {
      session_entry_id: "9001",
      driver_id: "1",
      display_name: "Kimi Antonelli",
      abbreviation: "ANT",
      racing_number: "12",
      team_name: "Mercedes",
      team_color_hex: "#00D7B6",
      finishing_position: 1,
      laps: [
        {
          lap_number: 1,
          lap_time_us: 92_000_000,
          stint_number: 1,
          compound: "MEDIUM",
          tyre_life_laps: 1,
          position: 1,
          is_clean: false,
          is_personal_best: false,
          beyond_cutoff: true,
        },
        {
          lap_number: 2,
          lap_time_us: 80_000_000,
          stint_number: 1,
          compound: "MEDIUM",
          tyre_life_laps: 2,
          position: 1,
          is_clean: true,
          is_personal_best: true,
          beyond_cutoff: false,
        },
        {
          lap_number: 3,
          lap_time_us: 81_000_000,
          stint_number: 1,
          compound: "MEDIUM",
          tyre_life_laps: 3,
          position: 1,
          is_clean: true,
          is_personal_best: false,
          beyond_cutoff: false,
        },
      ],
    },
    {
      session_entry_id: "9002",
      driver_id: "2",
      display_name: "Lewis Hamilton",
      abbreviation: "HAM",
      racing_number: "44",
      team_name: "Ferrari",
      team_color_hex: "#ED1131",
      finishing_position: 2,
      laps: [
        {
          lap_number: 2,
          lap_time_us: 83_000_000,
          stint_number: 1,
          compound: "HARD",
          tyre_life_laps: 2,
          position: 2,
          is_clean: true,
          is_personal_best: true,
          beyond_cutoff: false,
        },
        {
          lap_number: 3,
          lap_time_us: 84_000_000,
          stint_number: 1,
          compound: "HARD",
          tyre_life_laps: 3,
          position: 2,
          is_clean: true,
          is_personal_best: false,
          beyond_cutoff: false,
        },
      ],
    },
  ],
};
