import { expect, test, type Page, type Route } from "@playwright/test";

import {
  completedSeason,
  consistency,
  constructorStandings,
  driverStandings,
  headToHead,
  racePace,
  requestBudget,
} from "../src/test/fixtures";

/** No SignalR provider in this deployment, matching the archive workflows. */
const liveUnconfigured = {
  record_state: "unconfirmed_live",
  active: false,
  feed_configured: false,
  retention_days: 7,
  log_directory_bytes: 0,
  max_directory_bytes: 5368709120,
  requires_authentication: false,
  authentication: {
    authenticated: false,
    expired: false,
    expires_at: null,
    seconds_remaining: 0,
    expiry_source: null,
    token_source: null,
    companion_url: "https://f1login.fastf1.dev?port=8000",
    subscription: {},
  },
  session: null,
};

function json(route: Route, payload: unknown, status = 200) {
  return route.fulfill({
    body: JSON.stringify(payload),
    contentType: "application/json",
    status,
  });
}

const PASSWORD = "a-long-enough-password";

/**
 * A deployment that requires a sign-in.
 *
 * The session route answers from server-side state so the flow is exercised as
 * it really runs: signed out, then signed in after the password is accepted.
 */
async function installGatedApi(page: Page) {
  let signedIn = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === "/api/v1/auth/session") {
      return json(route, {
        authenticated: signedIn,
        required: true,
        kind: signedIn ? "session" : null,
        expires_at: null,
      });
    }
    if (path === "/api/v1/auth/login") {
      const body = request.postDataJSON() as { password?: string };
      if (body?.password !== PASSWORD) {
        return json(
          route,
          {
            detail: {
              code: "invalid_credentials",
              message: "That password was not accepted.",
            },
          },
          401,
        );
      }
      signedIn = true;
      return json(route, {
        authenticated: true,
        token: "a-bearer-token",
        expires_at: "2026-11-01T00:00:00Z",
      });
    }
    if (path === "/api/health/ready") {
      return json(route, { status: "ready", database: "ready" });
    }

    // Everything else is closed until signed in, exactly as the gate behaves.
    if (!signedIn) {
      return json(
        route,
        {
          detail: {
            code: "not_authenticated",
            message: "Sign in to use this dashboard.",
          },
        },
        401,
      );
    }

    if (path === "/api/v1/seasons/2026") {
      return json(route, completedSeason);
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
    if (path.endsWith("/standings/constructors")) {
      return json(route, constructorStandings);
    }
    if (path.endsWith("/standings/drivers")) {
      return json(route, driverStandings);
    }
    if (path === "/api/v1/upstreams/fastf1/usage") {
      return json(route, requestBudget);
    }
    if (path === "/api/v1/live/session") {
      return json(route, liveUnconfigured);
    }
    if (path === "/api/v1/live/recordings") {
      return json(route, {
        record_state: "unconfirmed_live",
        retention_days: 7,
        items: [],
      });
    }
    return json(route, {}, 200);
  });
}

test("a gated deployment asks for a password before showing anything", async ({
  page,
}) => {
  await installGatedApi(page);
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Data platform" }),
  ).toBeVisible();
  // No part of the dashboard leaks before sign-in.
  await expect(
    page.getByRole("navigation", { name: "Dashboard sections" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Password")).toHaveAttribute("type", "password");
});

test("a wrong password is refused and the field is cleared", async ({
  page,
}) => {
  await installGatedApi(page);
  await page.goto("/");

  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("That password was not accepted.")).toBeVisible();
  await expect(page.getByLabel("Password")).toHaveValue("");
  await expect(
    page.getByRole("navigation", { name: "Dashboard sections" }),
  ).toHaveCount(0);
});

test("the right password opens the dashboard", async ({ page }) => {
  await installGatedApi(page);
  await page.goto("/");

  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(
    page.getByRole("navigation", { name: "Dashboard sections" }),
  ).toBeVisible();
  await expect(page.getByLabel("Password")).toHaveCount(0);
  // A gated deployment offers a way back out.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("a sign-out that never reached the backend says so", async ({ page }) => {
  await installGatedApi(page);
  // The logout call fails after the session is already established.
  await page.route("**/api/v1/auth/logout", (route) => route.abort("failed"));
  await page.goto("/");

  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("navigation", { name: "Dashboard sections" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();

  // Reloading regardless would look like success while the cookie is valid.
  await expect(page.getByText("Still signed in")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Dashboard sections" }),
  ).toBeVisible();
});
