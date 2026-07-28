import type {
  ApiErrorResponse,
  BackfillJob,
  EnsureBackfillResponse,
  FastF1RequestBudget,
  SeasonOverview,
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
