import { expect, test, type Page, type Route } from "@playwright/test";

import {
  completedSeason,
  firstLapPage,
  missingSeason,
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

  await expect(page.getByRole("heading", { name: /The 2026 season/ })).toBeVisible();
  await expect(page.getByText("Australian Grand Prix")).toBeVisible();
  await page.getByRole("button", { name: /Mar 08.*Race/ }).click();

  await expect(
    page.getByRole("heading", { level: 2, name: "Australian Grand Prix" }),
  ).toBeVisible();
  const norrisRow = page.getByRole("row", { name: /Lando Norris/ });
  await norrisRow.getByRole("button", { name: "View laps" }).click();

  await expect(page.getByText("2 laps loaded")).toBeVisible();
  await page.getByRole("button", { name: "Load next 50 laps" }).click();
  await expect(page.getByText("4 laps loaded")).toBeVisible();
  await expect(page.getByText("End of stored lap summaries")).toBeVisible();

  await page.getByRole("button", { name: /Close view/ }).click();
  await expect(page.getByText("Session workspace")).toBeHidden();
});

test("changes season, starts synchronization, and displays job progress", async ({
  page,
}) => {
  await page.goto("/");

  await page.getByLabel("Championship season").selectOption("2025");
  await expect(page.getByRole("heading", { name: /The 2025 season/ })).toBeVisible();
  await expect(page.getByText("No calendar coverage yet")).toBeVisible();

  await page.getByRole("button", { name: /Check & sync season/ }).click();

  await expect(page.getByText("1 session queued for ingestion.")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Backfill progress" }),
  ).toBeVisible();
  await expect(page.getByText("Fetching", { exact: true })).toBeVisible();
  await expect(page.getByText(/Australian Grand Prix — Practice 1/)).toBeVisible();
});

test("keeps the primary dashboard within the viewport", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Australian Grand Prix")).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));

  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
});
