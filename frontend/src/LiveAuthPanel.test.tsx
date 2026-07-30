import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import type { LiveAuthStatus } from "./contracts";
import LiveAuthPanel from "./LiveAuthPanel";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
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

  it("explains the browser sign-in step and links to formula1.com", () => {
    render(<LiveAuthPanel auth={auth()} onChanged={vi.fn()} />);

    const link = screen.getByRole("link", { name: /account.formula1.com/ });
    expect(link).toHaveAttribute("href", "https://account.formula1.com/");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(screen.getByText("Not connected")).toBeVisible();
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
    expect(screen.getByLabelText(/login-session cookie/)).toBeVisible();
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
