import type {
  ApiErrorResponse,
  AuthSession,
  BackfillJob,
  EnsureBackfillResponse,
  EnsureLapTelemetryResponse,
  FastF1RequestBudget,
  LapSummaryRequest,
  LapSummaryResponse,
  LapTelemetryResponse,
  LiveAuthStatus,
  LiveRecordingList,
  LiveStatus,
  LoginResult,
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

/**
 * Notified whenever the backend refuses a request for want of a session.
 *
 * A session can lapse at any point, including mid-poll, so this is handled
 * once here rather than at each of the several dozen call sites — every one of
 * which would otherwise have to remember.
 */
type UnauthorizedListener = () => void;

let unauthorizedListener: UnauthorizedListener | null = null;

export function onUnauthorized(listener: UnauthorizedListener | null): void {
  unauthorizedListener = listener;
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    // Same-origin by default, but stated so the session cookie travels even if
    // the dashboard is ever served from somewhere else.
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...init.headers,
    },
  });

  if (response.status === 401) {
    unauthorizedListener?.();
  }

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

function telemetryPath(
  sessionId: string,
  sessionEntryId: string,
  lapNumber: number,
): string {
  return (
    `/api/v1/sessions/${sessionId}/entries/${sessionEntryId}` +
    `/laps/${lapNumber}/telemetry`
  );
}

/**
 * Request a lap's telemetry. Returns `available` when it is already stored, and
 * `queued`/`reused` when the worker has to fetch it from the upstream archive.
 */
export function ensureLapTelemetry(
  sessionId: string,
  sessionEntryId: string,
  lapNumber: number,
  signal?: AbortSignal,
): Promise<EnsureLapTelemetryResponse> {
  return requestJson<EnsureLapTelemetryResponse>(
    telemetryPath(sessionId, sessionEntryId, lapNumber),
    { method: "POST", signal },
  );
}

/** One keyset page of samples. 409 until the lap has been requested. */
export function getLapTelemetry(
  sessionId: string,
  sessionEntryId: string,
  lapNumber: number,
  query: { after_sample?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<LapTelemetryResponse> {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      parameters.set(key, String(value));
    }
  }
  const encoded = parameters.toString();
  return requestJson<LapTelemetryResponse>(
    `${telemetryPath(sessionId, sessionEntryId, lapNumber)}${
      encoded ? `?${encoded}` : ""
    }`,
    { signal },
  );
}

/** Whether this deployment requires a sign-in, and whether we have one. */
export function getAuthSession(signal?: AbortSignal): Promise<AuthSession> {
  return requestJson<AuthSession>("/api/v1/auth/session", { signal });
}

/**
 * Sign in. The browser is authenticated by the HttpOnly cookie the response
 * sets; the bearer token it also returns is for native clients and is
 * deliberately not stored here, where script could reach it.
 */
export function signIn(
  password: string,
  signal?: AbortSignal,
): Promise<LoginResult> {
  return requestJson<LoginResult>("/api/v1/auth/login", {
    body: JSON.stringify({ password }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
    signal,
  });
}

export function signOut(signal?: AbortSignal): Promise<AuthSession> {
  return requestJson<AuthSession>("/api/v1/auth/logout", {
    method: "POST",
    signal,
  });
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
