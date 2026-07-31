import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import type { LapTelemetryResponse, LapTelemetrySample } from "./contracts";
import {
  MAX_TELEMETRY_PAGES,
  MAX_TELEMETRY_POLLS,
  TelemetryTimeoutError,
  loadLapTelemetry,
} from "./lapTelemetry";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    ensureLapTelemetry: vi.fn(),
    getLapTelemetry: vi.fn(),
  };
});

const ensureLapTelemetry = vi.mocked(api.ensureLapTelemetry);
const getLapTelemetry = vi.mocked(api.getLapTelemetry);

function sample(index: number): LapTelemetrySample {
  return {
    sample_index: index,
    lap_time_us: index * 1000,
    session_time_us: null,
    distance_m: index * 10,
    relative_distance: null,
    speed_kph: 200 + index,
    rpm: 11000,
    gear: 7,
    throttle_percent: 100,
    brake: false,
    drs: 0,
    x: null,
    y: null,
    z: null,
  };
}

function page(
  items: LapTelemetrySample[],
  overrides: Partial<LapTelemetryResponse> = {},
): LapTelemetryResponse {
  return {
    session_id: "1",
    session_entry_id: "2",
    lap_id: "3",
    lap_number: 5,
    data_available: true,
    snapshot: {
      compatible: true,
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
      current_snapshot_completed_at: "2026-03-08T06:00:00Z",
    },
    ingestion: {
      status: "completed",
      attempt_count: 1,
      sample_count: items.length,
      requested_at: "2026-03-08T06:00:00Z",
      heartbeat_at: null,
      next_retry_at: null,
      completed_at: "2026-03-08T06:01:00Z",
      last_error: null,
    },
    page: { limit: 1000, has_more: false, next_after_sample: null },
    items,
    ...overrides,
  };
}

const nap = () => Promise.resolve();

function load() {
  return loadLapTelemetry("1", "2", 5, { sleep: nap });
}

describe("loadLapTelemetry", () => {
  beforeEach(() => {
    ensureLapTelemetry.mockReset();
    getLapTelemetry.mockReset();
  });

  it("reads straight through when telemetry is already stored", async () => {
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "available",
      status: "completed",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry.mockResolvedValue(page([sample(0), sample(1)]));

    const result = await load();

    expect(result.samples).toHaveLength(2);
    expect(result.truncated).toBe(false);
    // Available telemetry must not be polled for.
    expect(getLapTelemetry).toHaveBeenCalledTimes(1);
  });

  it("waits for a queued lap and then reads it", async () => {
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "queued",
      status: "pending",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry
      .mockResolvedValueOnce(
        page([], {
          data_available: false,
          ingestion: { ...page([]).ingestion, status: "pending" },
        }),
      )
      .mockResolvedValueOnce(
        page([], {
          data_available: false,
          ingestion: { ...page([]).ingestion, status: "running" },
        }),
      )
      .mockResolvedValueOnce(page([sample(0)]))
      .mockResolvedValue(page([sample(0), sample(1)]));

    const result = await load();

    expect(result.samples).toHaveLength(2);
  });

  it("walks every keyset page", async () => {
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "available",
      status: "completed",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry
      .mockResolvedValueOnce(
        page([sample(0), sample(1)], {
          page: { limit: 1000, has_more: true, next_after_sample: 1 },
        }),
      )
      .mockResolvedValueOnce(page([sample(2)]));

    const result = await load();

    expect(result.samples.map((item) => item.sample_index)).toEqual([0, 1, 2]);
    expect(getLapTelemetry).toHaveBeenLastCalledWith(
      "1",
      "2",
      5,
      { after_sample: 1, limit: 1000 },
      undefined,
    );
  });

  it("stops at the page cap rather than paging forever", async () => {
    // A backend that always claims another page must not spin the client.
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "available",
      status: "completed",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry.mockResolvedValue(
      page([sample(0)], {
        page: { limit: 1000, has_more: true, next_after_sample: 99 },
      }),
    );

    const result = await load();

    expect(result.truncated).toBe(true);
    expect(getLapTelemetry).toHaveBeenCalledTimes(MAX_TELEMETRY_PAGES);
  });

  it("gives up waiting rather than polling forever", async () => {
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "queued",
      status: "pending",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry.mockResolvedValue(
      page([], {
        data_available: false,
        ingestion: { ...page([]).ingestion, status: "running" },
      }),
    );

    await expect(load()).rejects.toBeInstanceOf(TelemetryTimeoutError);
    expect(getLapTelemetry).toHaveBeenCalledTimes(MAX_TELEMETRY_POLLS);
  });

  it("keeps waiting through a not-yet-requested read", async () => {
    // A just-queued lap can still read as never-requested for a moment.
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "queued",
      status: "pending",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry
      .mockRejectedValueOnce(
        new api.ApiClientError(
          "Telemetry has not been requested for this lap.",
          "telemetry_not_requested",
          409,
        ),
      )
      .mockResolvedValue(page([sample(0)]));

    const result = await load();

    expect(result.samples).toHaveLength(1);
  });

  it("returns a failed ingestion instead of waiting it out", async () => {
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 5,
      action: "queued",
      status: "pending",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    const failed = page([], {
      data_available: false,
      ingestion: {
        ...page([]).ingestion,
        status: "failed",
        last_error: { code: "upstream_error", message: "no telemetry" },
      },
    });
    getLapTelemetry.mockResolvedValue(failed);

    const result = await load();

    expect(result.samples).toEqual([]);
    expect(result.response.ingestion.status).toBe("failed");
    expect(getLapTelemetry).toHaveBeenCalledTimes(1);
  });

  it("propagates an error that is not a missing request", async () => {
    ensureLapTelemetry.mockRejectedValue(
      new api.ApiClientError("gone", "telemetry_unavailable", 503),
    );

    await expect(load()).rejects.toBeInstanceOf(api.ApiClientError);
  });
});
