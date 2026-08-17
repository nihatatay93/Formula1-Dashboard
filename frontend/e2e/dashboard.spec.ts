import { expect, test, type Page, type Route } from "@playwright/test";

import {
  completedSeason,
  consistency,
  constructorStandings,
  driverStandings,
  headToHead,
  racePace,
  ensureLapTelemetryAvailable,
  firstLapPage,
  lapTelemetry,
  missingSeason,
  piastriLapPage,
  queuedBackfill,
  requestBudget,
  runningBackfill,
  secondLapPage,
  sessionDetail,
  sessionResults,
} from "../src/test/fixtures";

function json(route: Route, payload: unknown, status = 200) {
  return route.fulfill({
    body: JSON.stringify(payload),
    contentType: "application/json",
    status,
  });
}

async function installApiRoutes(page: Page) {
  let backfillStarted = false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/health/ready") {
      return json(route, { status: "ready", database: "ready" });
    }
    if (path === "/api/v1/auth/session") {
      // These workflows run against a deployment left open on purpose, which
      // reports itself authenticated; sign-in has its own spec.
      return json(route, {
        authenticated: true,
        required: false,
        kind: null,
        expires_at: null,
      });
    }
    if (path === "/api/v1/upstreams/fastf1/usage") {
      return json(route, requestBudget);
    }
    if (path === "/api/v1/live/session") {
      // No SignalR provider exists yet, so the deployed state is unconfigured.
      return json(route, {
        record_state: "unconfirmed_live",
        active: false,
        feed_configured: false,
        retention_days: 7,
        log_directory_bytes: 0,
        max_directory_bytes: 5368709120,
        authentication: {
          authenticated: false,
          expired: false,
          expires_at: null,
          seconds_remaining: 0,
          expiry_source: null,
          token_source: null,
          companion_url: "https://f1login.fastf1.dev?port=8000",
        },
        session: null,
      });
    }
    if (path === "/api/v1/live/recordings") {
      // No session has been collected in this deployment, so nothing to replay.
      return json(route, {
        record_state: "unconfirmed_live",
        retention_days: 7,
        items: [],
      });
    }
    if (path === "/api/v1/seasons/2026/standings/drivers") {
      return json(route, driverStandings);
    }
    if (path === "/api/v1/seasons/2026/standings/constructors") {
      return json(route, constructorStandings);
    }
    if (path.endsWith("/head-to-head")) {
      return json(route, headToHead);
    }
    if (path.endsWith("/consistency")) {
      return json(route, consistency);
    }
    if (/^\/api\/v1\/sessions\/\d+\/laps$/.test(path)) {
      return json(route, racePace);
    }
    if (path.includes("/standings/")) {
      // Any other season has nothing archived to rank.
      return json(route, {
        season_year: 2025,
        scoring_sessions: 0,
        rounds: [],
        items: [],
      });
    }
    if (path === "/api/v1/seasons/2026" && request.method() === "GET") {
      return json(route, completedSeason);
    }
    if (path === "/api/v1/seasons/2025" && request.method() === "GET") {
      return json(
        route,
        backfillStarted
          ? {
              ...missingSeason,
              status: "running",
              active_job: queuedBackfill.job,
              counts: {
                ...missingSeason.counts,
                sessions: 1,
                pending: 1,
              },
            }
          : missingSeason,
      );
    }
    if (
      path === "/api/v1/seasons/2025/backfill" &&
      request.method() === "POST"
    ) {
      backfillStarted = true;
      return json(route, queuedBackfill, 202);
    }
    if (path === `/api/v1/backfill-jobs/${runningBackfill.id}`) {
      return json(route, runningBackfill);
    }
    if (path === `/api/v1/sessions/${sessionDetail.id}`) {
      return json(route, sessionDetail);
    }
    if (path === `/api/v1/sessions/${sessionDetail.id}/results`) {
      return json(route, sessionResults);
    }
    if (
      path ===
      `/api/v1/sessions/${sessionDetail.id}/entries/${sessionResults.items[0].session_entry_id}/laps`
    ) {
      return json(
        route,
        url.searchParams.get("after_lap") === "2"
          ? secondLapPage
          : firstLapPage,
      );
    }
    if (
      path ===
      `/api/v1/sessions/${sessionDetail.id}/entries/${sessionResults.items[1].session_entry_id}/laps`
    ) {
      return json(route, piastriLapPage);
    }
    if (path.endsWith("/laps/1/telemetry")) {
      // Already stored upstream, so the reader never waits on the worker.
      return request.method() === "POST"
        ? json(route, ensureLapTelemetryAvailable)
        : json(route, lapTelemetry);
    }

    return json(
      route,
      {
        detail: {
          code: "test_route_missing",
          message: `No browser fixture exists for ${request.method()} ${path}`,
        },
      },
      500,
    );
  });
}

test.beforeEach(async ({ page }) => {
  await installApiRoutes(page);
});

test("opens a completed session and traverses bounded lap pages", async ({
  page,
}) => {
  await page.goto("/");

  // The landing page routes into the archive rather than opening on it.
  await expect(
    page.getByRole("heading", { name: "Formula One data platform" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Browse season sessions" })
    .click();
  await expect(
    page.getByRole("heading", { name: "2026 season sessions" }),
  ).toBeVisible();
  await expect(page.getByText("Australian Grand Prix")).toBeVisible();
  await page.getByRole("button", { name: /Mar 08.*Race/ }).click();

  await expect(
    page.getByRole("heading", {
      name: "Australian Grand Prix workspace",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 2, name: "Australian Grand Prix" }),
  ).toBeVisible();
  const norrisRow = page.getByRole("row", { name: /Lando Norris/ });
  await norrisRow.getByRole("button", { name: "View laps" }).click();

  await expect(page.getByText("2 laps loaded")).toBeVisible();
  await page
    .getByRole("checkbox", { name: "Select lap 1 for pace analysis" })
    .check();
  await page
    .getByRole("checkbox", { name: "Select lap 2 for pace analysis" })
    .check();
  // Scoped to the participant card: the trend chart's axis labels are also
  // formatted lap times and would otherwise match too.
  await expect(
    page.locator(".pace-analysis__card").first().getByText("1:30.750"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Load next 50 laps" }).click();
  await expect(page.getByText("4 laps loaded")).toBeVisible();
  await expect(page.getByText("End of stored lap summaries")).toBeVisible();

  const piastriRow = page.getByRole("row", { name: /Oscar Piastri/ });
  await piastriRow.getByRole("button", { name: "View laps" }).click();
  await page
    .getByRole("checkbox", { name: "Select lap 1 for pace analysis" })
    .check();
  await page
    .getByRole("checkbox", { name: "Select lap 2 for pace analysis" })
    .check();
  await expect(
    page.getByText(
      "Lando Norris is 0.750s faster on the selected average.",
    ),
  ).toBeVisible();

  // Telemetry is fetched per lap, on request, and plots one facet per measure.
  await norrisRow.getByRole("button", { name: "View laps" }).click();
  await page
    .getByRole("button", { name: "View telemetry for lap 1" })
    .click();
  await expect(
    page.getByRole("heading", { name: /Lando Norris · lap 1/ }),
  ).toBeVisible();
  await expect(page.locator(".telemetry-chart__trace")).toHaveCount(3);
  // Scoped to the summary: the axis label carries the same figure.
  await expect(
    page.locator(".telemetry-summary").getByText("289 km/h"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close telemetry" }).click();
  await expect(page.locator(".telemetry-chart")).toHaveCount(0);

  await page.getByRole("button", { name: /Close view/ }).click();
  await expect(page.locator("#session-explorer")).toBeHidden();
  await expect(
    page.getByRole("button", { name: /Season sessions/ }),
  ).toHaveAttribute("aria-current", "page");
});

test("changes season, starts synchronization, and displays job progress", async ({
  page,
}) => {
  await page.goto("/");

  // Season controls belong to the archive, so they appear once inside it.
  await page.getByRole("button", { name: /Season sessions/ }).click();
  // The season picker is a custom listbox, not a native select.
  await page
    .getByRole("combobox", { name: /Championship season/ })
    .click();
  await page.getByRole("option", { name: "2025" }).click();
  await expect(
    page.getByRole("heading", { name: "2025 season sessions" }),
  ).toBeVisible();
  await expect(page.getByText("No calendar coverage yet")).toBeVisible();

  await page.getByRole("button", { name: /Check & sync season/ }).click();

  await expect(
    page.getByRole("button", { name: /Coverage/ }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("1 session queued for ingestion.")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Backfill progress" }),
  ).toBeVisible();
  await expect(page.getByText("Fetching", { exact: true })).toBeVisible();
  await expect(page.getByText(/Australian Grand Prix — Practice 1/)).toBeVisible();
});

test("keeps the primary dashboard within the viewport", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Dashboard sections" })).toBeVisible();
  await page.getByRole("button", { name: /Season sessions/ }).click();
  await expect(
    page.getByText("Future calendar awaiting exact timing"),
  ).toBeVisible();
  await expect(page.getByText(/starting with Dutch Grand Prix/)).toBeVisible();
  await expect(page.getByText("Australian Grand Prix")).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));

  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("lands on a home page that separates the two paths", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Two ways into the data" }),
  ).toBeVisible();

  // Each path states what it is and what state it is in, so the choice is
  // made from the facts rather than from two labels in a sidebar.
  const archive = page.getByRole("article", { name: "Season archive" });
  await expect(archive.getByText("1 / 1")).toBeVisible();
  const live = page.getByRole("article", { name: "Live timing" });
  await expect(live.getByText("No feed provider configured")).toBeVisible();
  await expect(live.getByText("Not connected")).toBeVisible();

  // The distinction that matters is stated, not implied.
  await expect(archive.getByText(/This is the durable record/)).toBeVisible();
  await expect(
    live.getByText(/Nothing here is stored as sporting data/),
  ).toBeVisible();

  // Season controls belong to the archive and are not offered here.
  await expect(
    page.getByRole("combobox", { name: /Championship season/ }),
  ).toHaveCount(0);

  await page.getByRole("button", { name: "Open live timing" }).click();
  await expect(
    page.getByRole("heading", { name: "Live timing", level: 1 }),
  ).toBeVisible();
});

test("opens live timing as a separate view without archive state", async ({
  page,
}) => {
  await page.goto("/");

  await page.getByRole("button", { name: /Live timing/ }).click();

  await expect(
    page.getByRole("heading", { name: "Live timing", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText("Unconfirmed live data")).toBeVisible();
  await expect(
    page.getByText("No live feed provider is configured"),
  ).toBeVisible();

  // F1 TV access is a browser sign-in handing over a cookie, never a password.
  await expect(page.getByRole("heading", { name: "Live feed access" })).toBeVisible();
  await expect(
    page.getByText(/Your password is never sent to this application/),
  ).toBeVisible();
  // The primary path is one click; the cookie paste is a collapsed fallback.
  await expect(
    page.getByRole("link", { name: /Sign in with Formula 1/ }),
  ).toHaveAttribute("href", "https://f1login.fastf1.dev?port=8000");
  await expect(
    page.getByRole("link", { name: /FastF1 companion extension/ }),
  ).toBeVisible();
  await expect(page.getByLabel(/login-session cookie/)).toHaveAttribute(
    "type",
    "password",
  );
  // No session identity to fill in: the feed states which session it is.
  await expect(page.getByLabel(/Event name/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Connect to live session/ }),
  ).toBeDisabled();

  // Replay is offered alongside the live path, and says so when it is empty.
  await expect(
    page.getByRole("heading", { name: "Replay a session" }),
  ).toBeVisible();
  await expect(page.getByText(/No recorded sessions yet/)).toBeVisible();

  // The archive Session Workspace must not leak into the live view.
  await expect(page.locator("#session-explorer")).toBeHidden();
  await expect(page.getByText("Season calendar")).toBeHidden();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});

test("race pace compares the field and never bridges a gap in the laps", async ({
  page,
}) => {
  await installApiRoutes(page);
  await page.goto("/");

  await page.getByRole("button", { name: /^Season sessions/ }).click();
  await page.getByRole("button", { name: /Mar 08.*Race/ }).click();
  await page.getByRole("button", { name: "Race analysis", exact: true }).click();

  // A row per driver, ordered by median, each one named rather than left to
  // its team colour.
  await expect(page.locator(".pace-box__name")).toHaveText([
    "Kimi Antonelli",
    "Lewis Hamilton",
  ]);

  // Antonelli's clean laps are 2 and 3 -- consecutive, so one stroke. Hamilton
  // has 2 and 3 too. Turning off clean-only adds his lap 1, which is still
  // consecutive, so the count must not change.
  await expect(page.locator(".pace-box__caption")).toContainText(
    "1.5x the interquartile range",
  );

  await page.getByRole("checkbox", { name: /clean laps only/i }).uncheck();
  await expect(page.getByText(/5 laps from 2 drivers/)).toBeVisible();

  await page.getByRole("checkbox", { name: /clean laps only/i }).check();
  await expect(page.getByText(/4 laps from 2 drivers/)).toBeVisible();
});

test("strategy shows stints by compound and states the pit-lane caveat", async ({
  page,
}) => {
  await installApiRoutes(page);
  await page.goto("/");

  await page.getByRole("button", { name: /^Season sessions/ }).click();
  await page.getByRole("button", { name: /Mar 08.*Race/ }).click();
  await page.getByRole("button", { name: "Race analysis", exact: true }).click();
  await page.getByRole("tab", { name: "Strategy" }).click();

  // One segment per stint, coloured from the tyre palette.
  await expect(page.locator(".strategy__stint").first()).toBeVisible();
  await expect(page.locator(".strategy__stop")).toHaveCount(1);

  // Pit-lane time is roughly twenty seconds longer than the televised stop
  // time, so the distinction has to be on screen.
  await expect(page.getByText(/Pit-lane time, not stop time/)).toBeVisible();
});

test("head to head reports the record, the share and what was excluded", async ({
  page,
}) => {
  await installApiRoutes(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Head to head", exact: true }).click();

  // Identity is carried by the driver code as well as by colour: these two
  // share a team, so the bars alone cannot tell them apart.
  await expect(page.getByText("1–0").first()).toBeVisible();
  await expect(page.getByText(/NOR 100%/).first()).toBeVisible();

  await page.getByRole("tab", { name: "Consistency" }).click();

  await expect(page.getByText(/percentage of the best clean lap/)).toBeVisible();
  await expect(page.locator(".consistency__name strong")).toHaveText([
    "Lando Norris",
    "Oscar Piastri",
  ]);
});

test("the scope bar switches session, and density persists", async ({ page }) => {
  await installApiRoutes(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Standings", exact: true }).click();

  // Standings describe a whole season, so no session control belongs here.
  await expect(page.locator(".scope-bar")).toBeVisible();
  await expect(page.locator("#scope-session")).toHaveCount(0);

  await page.getByRole("button", { name: "Compact" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-density", "compact");

  // The preference survives a reload; it is stored, not component state.
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-density", "compact");
});

test("the scope bar changes session without leaving race analysis", async ({
  page,
}) => {
  await installApiRoutes(page);
  await page.goto("/");

  await page.getByRole("button", { name: /^Season sessions/ }).click();
  await page.getByRole("button", { name: /Mar 08.*Race/ }).click();
  await page.getByRole("button", { name: "Race analysis", exact: true }).click();

  // Changing scope used to mean going back to the calendar and drilling in
  // again, which lost your place in whatever you were reading.
  const session = page.locator("#scope-session");
  await expect(session).toBeVisible();
  await expect(page.locator("[data-view='race-pace']")).toBeVisible();
});

test("every analysis view offers its data as CSV", async ({ page }) => {
  await installApiRoutes(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Standings", exact: true }).click();

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  const file = await download;

  expect(file.suggestedFilename()).toBe("2026-drivers-standings.csv");
});
