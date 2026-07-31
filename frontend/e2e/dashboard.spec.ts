import { expect, test, type Page, type Route } from "@playwright/test";

import {
  completedSeason,
  firstLapPage,
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

  await expect(
    page.getByRole("heading", { name: "2026 season control" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Season sessions/ }).click();
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

  // The season picker is a custom listbox, not a native select.
  await page
    .getByRole("combobox", { name: /Championship season/ })
    .click();
  await page.getByRole("option", { name: "2025" }).click();
  await expect(
    page.getByRole("heading", { name: "2025 season control" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Season sessions/ }).click();
  await expect(page.getByText("No calendar coverage yet")).toBeVisible();

  await page.getByRole("button", { name: /Check & sync season/ }).click();

  await expect(
    page.getByRole("button", { name: /Overview/ }),
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
  await expect(
    page.getByText("Future calendar awaiting exact timing"),
  ).toBeVisible();
  await expect(page.getByText(/starting with Dutch Grand Prix/)).toBeVisible();
  await page.getByRole("button", { name: /Season sessions/ }).click();
  await expect(page.getByText("Australian Grand Prix")).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));

  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
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
