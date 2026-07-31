import type {
  ApiErrorResponse,
  BackfillJob,
  EnsureBackfillResponse,
  FastF1RequestBudget,
  LapSummaryRequest,
  LapSummaryResponse,
  LiveAuthStatus,
  LiveRecordingList,
  LiveStatus,
  SeasonOverview,
  SessionDetail,
  SessionResults,
} from "./contracts";

export class ApiClientError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.status = status;
  }
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...init.headers,
    },
  });

  if (!response.ok) {
    let payload: ApiErrorResponse | null = null;
    try {
      payload = (await response.json()) as ApiErrorResponse;
    } catch {
      // The backend contract is JSON, but keep a safe fallback for proxy errors.
    }

    throw new ApiClientError(
      payload?.detail?.message ?? "The dashboard request could not be completed.",
      payload?.detail?.code ?? "request_failed",
      response.status,
    );
  }

  return (await response.json()) as T;
}

export function getSeasonOverview(
  seasonYear: number,
  signal?: AbortSignal,
): Promise<SeasonOverview> {
  return requestJson<SeasonOverview>(`/api/v1/seasons/${seasonYear}`, { signal });
}

export function ensureSeasonBackfill(
  seasonYear: number,
  signal?: AbortSignal,
): Promise<EnsureBackfillResponse> {
  return requestJson<EnsureBackfillResponse>(
    `/api/v1/seasons/${seasonYear}/backfill`,
    {
      method: "POST",
      signal,
    },
  );
}

export function getBackfillJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<BackfillJob> {
  return requestJson<BackfillJob>(`/api/v1/backfill-jobs/${jobId}`, {
    signal,
  });
}

export function getFastF1RequestBudget(
  signal?: AbortSignal,
): Promise<FastF1RequestBudget> {
  return requestJson<FastF1RequestBudget>(
    "/api/v1/upstreams/fastf1/usage",
    { signal },
  );
}

export function getSessionDetail(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionDetail> {
  return requestJson<SessionDetail>(`/api/v1/sessions/${sessionId}`, {
    signal,
  });
}

export function getSessionResults(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionResults> {
  return requestJson<SessionResults>(
    `/api/v1/sessions/${sessionId}/results`,
    { signal },
  );
}

export function getSessionLaps(
  sessionId: string,
  sessionEntryId: string,
  query: LapSummaryRequest = {},
  signal?: AbortSignal,
): Promise<LapSummaryResponse> {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      parameters.set(key, String(value));
    }
  }
  const encodedParameters = parameters.toString();
  const queryString = encodedParameters ? `?${encodedParameters}` : "";
  return requestJson<LapSummaryResponse>(
    `/api/v1/sessions/${sessionId}/entries/${sessionEntryId}/laps${queryString}`,
    { signal },
  );
}

export function getLiveStatus(signal?: AbortSignal): Promise<LiveStatus> {
  return requestJson<LiveStatus>("/api/v1/live/session", { signal });
}

/** No identity is sent: the feed states which session it is. */
export function startLiveSession(signal?: AbortSignal): Promise<LiveStatus> {
  return requestJson<LiveStatus>("/api/v1/live/session", {
    method: "POST",
    signal,
  });
}

export function stopLiveSession(signal?: AbortSignal): Promise<LiveStatus> {
  return requestJson<LiveStatus>("/api/v1/live/session", {
    method: "DELETE",
    signal,
  });
}

/** Session logs from earlier sessions that retention has not yet deleted. */
export function getLiveRecordings(
  signal?: AbortSignal,
): Promise<LiveRecordingList> {
  return requestJson<LiveRecordingList>("/api/v1/live/recordings", { signal });
}

/** Replay a recording through the live pipeline. Needs no F1 TV token. */
export function startLiveReplay(
  name: string,
  speed?: number,
  signal?: AbortSignal,
): Promise<LiveStatus> {
  return requestJson<LiveStatus>("/api/v1/live/replay", {
    body: JSON.stringify(speed === undefined ? { name } : { name, speed }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
    signal,
  });
}

export function getLiveAuthStatus(
  signal?: AbortSignal,
): Promise<LiveAuthStatus> {
  return requestJson<LiveAuthStatus>("/api/v1/live/auth", { signal });
}

export function storeLiveAuth(
  loginSession: string,
  signal?: AbortSignal,
): Promise<LiveAuthStatus> {
  return requestJson<LiveAuthStatus>("/api/v1/live/auth", {
    body: JSON.stringify({ login_session: loginSession }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
    signal,
  });
}

export function clearLiveAuth(signal?: AbortSignal): Promise<LiveAuthStatus> {
  return requestJson<LiveAuthStatus>("/api/v1/live/auth", {
    method: "DELETE",
    signal,
  });
}

/** Same-origin WebSocket URL for the live stream, upgraded to wss on https. */
export function liveStreamUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/v1/live/stream`;
}

export async function checkApiReadiness(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch("/api/health/ready", {
      cache: "no-store",
      signal,
    });
    return response.ok;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    return false;
  }
}
