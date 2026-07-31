import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { LiveAuthStatus } from "../contracts";
import LiveAuthPanel from "./LiveAuthPanel";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    clearLiveAuth: vi.fn(),
    storeLiveAuth: vi.fn(),
  };
});

const storeLiveAuth = vi.mocked(api.storeLiveAuth);
const clearLiveAuth = vi.mocked(api.clearLiveAuth);

const TOKEN = "abc123def456ghi789jkl012mno345";

function auth(overrides: Partial<LiveAuthStatus> = {}): LiveAuthStatus {
  return {
    authenticated: false,
    expired: false,
    expires_at: null,
    seconds_remaining: 0,
    expiry_source: null,
    token_source: null,
    companion_url: "https://f1login.fastf1.dev?port=8000",
    subscription: {},
    ...overrides,
  };
}

function connected(seconds = 3 * 86_400): LiveAuthStatus {
  return auth({
    authenticated: true,
    expires_at: "2026-08-03T12:00:00Z",
    seconds_remaining: seconds,
    expiry_source: "configured_ttl",
  });
}

describe("LiveAuthPanel", () => {
  beforeEach(() => {
    storeLiveAuth.mockReset();
    clearLiveAuth.mockReset();
  });

  it("states that the password never reaches the application", () => {
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    expect(
      screen.getByText(/Your password is never sent to this application/),
    ).toBeVisible();
  });

  it("offers a one-click sign-in link carrying our callback port", () => {
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    const link = screen.getByRole("link", { name: /Sign in with Formula 1/ });
    expect(link).toHaveAttribute(
      "href",
      "https://f1login.fastf1.dev?port=8000",
    );
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(screen.getByText("Not connected")).toBeVisible();
  });

  it("links to the companion extension as the one-time install step", () => {
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    expect(
      screen.getByRole("link", { name: /FastF1 companion extension/ }),
    ).toHaveAttribute(
      "href",
      "https://github.com/theOehrly/fastf1-companion",
    );
  });

  it("keeps the manual cookie paste as a secondary fallback", () => {
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    // Present, but tucked behind a disclosure rather than the primary path.
    const disclosure = screen.getByText(/No extension\? Paste the cookie/);
    expect(disclosure.closest("details")).not.toBeNull();
    expect(screen.getByLabelText(/login-session cookie/)).toBeInTheDocument();
  });

  it("hides the sign-in link when the backend supplies none", () => {
    render(
      <LiveAuthPanel auth={auth({ companion_url: null })} onChanged={vi.fn()} />,
    );

    expect(
      screen.queryByRole("link", { name: /Sign in with Formula 1/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps connect disabled until a token is entered", async () => {
    const user = userEvent.setup();
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    const connect = screen.getByRole("button", { name: /Connect account/ });
    expect(connect).toBeDisabled();

    await user.type(screen.getByLabelText(/login-session cookie/), TOKEN);

    expect(connect).toBeEnabled();
  });

  it("masks the token field so it is not shoulder-readable", () => {
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    expect(screen.getByLabelText(/login-session cookie/)).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("stores a pasted token and clears it from the field afterwards", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    storeLiveAuth.mockResolvedValue(connected());
    render(<LiveAuthPanel auth={auth()} onChanged={onChanged} />);

    const field = screen.getByLabelText(/login-session cookie/);
    await user.type(field, `  ${TOKEN}  `);
    await user.click(screen.getByRole("button", { name: /Connect account/ }));

    expect(storeLiveAuth).toHaveBeenCalledWith(TOKEN);
    expect(onChanged).toHaveBeenCalledWith(connected());
    // The credential must not linger in component state.
    expect(field).toHaveValue("");
  });

  it("surfaces a rejected token without claiming success", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    storeLiveAuth.mockRejectedValue(
      new api.ApiClientError(
        "login session is too short to be valid",
        "invalid_login_session",
        422,
      ),
    );
    render(<LiveAuthPanel auth={auth()} onChanged={onChanged} />);

    await user.type(screen.getByLabelText(/login-session cookie/), TOKEN);
    await user.click(screen.getByRole("button", { name: /Connect account/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "login session is too short to be valid",
    );
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("shows subscription product and status but no identifiers", () => {
    render(
      <LiveAuthPanel
        auth={{
          ...connected(),
          subscription: {
            product: "F1 TV Pro Annual",
            status: "active",
            first_name: "Ada",
          },
        }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText("F1 TV Pro Annual")).toBeVisible();
    expect(screen.getByText("active")).toBeVisible();
    expect(screen.getByText("Ada")).toBeVisible();
  });

  it("omits the subscription block when no claims are available", () => {
    render(<LiveAuthPanel auth={connected()} onChanged={vi.fn()} />);

    expect(screen.queryByText("Subscription")).not.toBeInTheDocument();
  });

  it("reports remaining validity when connected", () => {
    render(<LiveAuthPanel auth={connected()} onChanged={vi.fn()} />);

    expect(screen.getByText("Connected")).toBeVisible();
    expect(screen.getByText(/valid for 3d 0h/)).toBeVisible();
    expect(
      screen.queryByLabelText(/login-session cookie/),
    ).not.toBeInTheDocument();
  });

  it("says when the expiry came from the token itself", () => {
    render(
      <LiveAuthPanel
        auth={{ ...connected(), expiry_source: "token_claim" }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText(/from the token itself/)).toBeVisible();
  });

  it("prompts to reconnect once the session has expired", () => {
    render(
      <LiveAuthPanel
        auth={auth({ expired: true, expires_at: "2026-07-20T12:00:00Z" })}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText("Your F1 TV session has expired")).toBeVisible();
    expect(screen.getByText("Expired")).toBeVisible();
    // Re-authentication offers the same one-click path, not a manual chore.
    expect(
      screen.getByRole("link", { name: /Sign in with Formula 1/ }),
    ).toBeVisible();
    expect(screen.getByLabelText(/login-session cookie/)).toBeInTheDocument();
  });

  it("signs out and reports the cleared status", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    clearLiveAuth.mockResolvedValue(auth());
    render(<LiveAuthPanel auth={connected()} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: /Sign out/ }));

    expect(clearLiveAuth).toHaveBeenCalledOnce();
    expect(onChanged).toHaveBeenCalledWith(auth());
  });
});
