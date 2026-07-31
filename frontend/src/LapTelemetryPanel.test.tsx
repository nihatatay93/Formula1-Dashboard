import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import LapTelemetryPanel from "./LapTelemetryPanel";
import type { LapTelemetryResponse, LapTelemetrySample } from "./contracts";

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
  // A rough corner: brake on, off throttle, down two gears, then accelerating.
  const braking = index > 3 && index < 7;
  return {
    sample_index: index,
    lap_time_us: index * 50_000,
    session_time_us: null,
    distance_m: index * 100,
    relative_distance: index / 20,
    speed_kph: braking ? 300 - (index - 3) * 55 : 180 + index * 12,
    rpm: 11_000,
    gear: braking ? 3 : 7,
    throttle_percent: braking ? 0 : 100,
    brake: braking,
    drs: 0,
    x: null,
    y: null,
    z: null,
  };
}

const SAMPLES = Array.from({ length: 12 }, (_, index) => sample(index));

function response(
  overrides: Partial<LapTelemetryResponse> = {},
): LapTelemetryResponse {
  return {
    session_id: "1",
    session_entry_id: "2",
    lap_id: "3",
    lap_number: 7,
    data_available: true,
    snapshot: {
      compatible: true,
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
      current_snapshot_completed_at: "2026-03-08T06:00:00Z",
    },
    ingestion: {
      status: "completed",
      attempt_count: 1,
      sample_count: SAMPLES.length,
      requested_at: "2026-03-08T06:00:00Z",
      heartbeat_at: null,
      next_retry_at: null,
      completed_at: "2026-03-08T06:01:00Z",
      last_error: null,
    },
    page: { limit: 1000, has_more: false, next_after_sample: null },
    items: SAMPLES,
    ...overrides,
  };
}

function available() {
  ensureLapTelemetry.mockResolvedValue({
    session_id: "1",
    session_entry_id: "2",
    lap_id: "3",
    lap_number: 7,
    action: "available",
    status: "completed",
    source_snapshot_completed_at: "2026-03-08T06:00:00Z",
  });
}

function panel() {
  return render(
    <LapTelemetryPanel
      driverName="Lando Norris"
      lapNumber={7}
      onClose={() => {}}
      sessionEntryId="2"
      sessionId="1"
    />,
  );
}

describe("LapTelemetryPanel", () => {
  beforeEach(() => {
    ensureLapTelemetry.mockReset();
    getLapTelemetry.mockReset();
  });

  it("says a queued lap is being fetched rather than showing an error", async () => {
    ensureLapTelemetry.mockReturnValue(new Promise(() => {}));

    panel();

    expect(
      await screen.findByText(/queued for the ingestion worker/),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("plots each channel in its own facet over a shared axis", async () => {
    available();
    getLapTelemetry.mockResolvedValue(response());

    const { container } = panel();

    await waitFor(() =>
      expect(container.querySelector(".telemetry-chart")).not.toBeNull(),
    );

    // Three measures, three plots — never three y-scales on one frame.
    expect(container.querySelectorAll(".telemetry-chart__trace")).toHaveLength(
      3,
    );
    expect(
      [...container.querySelectorAll(".telemetry-chart__facet-label")].map(
        (node) => node.textContent,
      ),
    ).toEqual(["Speed (km/h)", "Throttle (%)", "Gear"]);

    // Braking is one band across the facets, not a fourth trace.
    expect(
      container.querySelectorAll(".telemetry-chart__brake").length,
    ).toBeGreaterThan(0);
  });

  it("describes the trace for a reader who cannot see it", async () => {
    available();
    getLapTelemetry.mockResolvedValue(response());

    panel();

    const figure = await screen.findByRole("img");
    expect(figure).toHaveAccessibleName(
      /Telemetry for Lando Norris on lap 7.*Top speed 312 km\/h/s,
    );
    // The same numbers are readable as text, not only as a picture.
    const summary = screen.getByText("Top speed").closest("div");
    expect(summary).not.toBeNull();
    expect(within(summary as HTMLElement).getByText("312 km/h")).toBeVisible();
    expect(screen.getByText("Read the trace as a table")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("reports a failed archive fetch with its reason", async () => {
    ensureLapTelemetry.mockResolvedValue({
      session_id: "1",
      session_entry_id: "2",
      lap_id: "3",
      lap_number: 7,
      action: "queued",
      status: "pending",
      source_snapshot_completed_at: "2026-03-08T06:00:00Z",
    });
    getLapTelemetry.mockResolvedValue(
      response({
        data_available: false,
        items: [],
        ingestion: {
          ...response().ingestion,
          status: "failed",
          attempt_count: 3,
          last_error: { code: "upstream_timeout", message: "Upstream timed out" },
        },
      }),
    );

    // The real poll waits two seconds; drive it rather than sit through it.
    vi.useFakeTimers();
    try {
      panel();
      await vi.advanceTimersByTimeAsync(2_000);
    } finally {
      vi.useRealTimers();
    }

    expect(await screen.findByText("The archive fetch failed")).toBeVisible();
    expect(
      screen.getByText(/Upstream timed out \(upstream_timeout\). After 3 attempts./),
    ).toBeVisible();
  });

  it("refuses to plot telemetry from a superseded snapshot", async () => {
    available();
    getLapTelemetry.mockResolvedValue(
      response({
        data_available: false,
        items: [],
        snapshot: {
          compatible: false,
          source_snapshot_completed_at: "2026-03-01T06:00:00Z",
          current_snapshot_completed_at: "2026-03-08T06:00:00Z",
        },
      }),
    );

    const { container } = panel();

    expect(
      await screen.findByText("Stored against an older snapshot"),
    ).toBeVisible();
    expect(container.querySelector(".telemetry-chart")).toBeNull();
  });

  it("says so when the upstream stored no samples", async () => {
    available();
    getLapTelemetry.mockResolvedValue(response({ items: [] }));

    panel();

    expect(
      await screen.findByText("No telemetry samples were stored for this lap."),
    ).toBeVisible();
  });

  it("surfaces an unreachable telemetry service and offers a retry", async () => {
    ensureLapTelemetry.mockRejectedValue(
      new api.ApiClientError("Upstream is down.", "telemetry_unavailable", 503),
    );

    panel();

    expect(await screen.findByText("Telemetry unavailable")).toBeVisible();
    expect(
      screen.getByText("The upstream archive has no telemetry for this lap."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});
