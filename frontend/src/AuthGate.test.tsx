import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import AuthGate from "./AuthGate";
import type { AuthSession } from "./contracts";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    getAuthSession: vi.fn(),
    signIn: vi.fn(),
    onUnauthorized: vi.fn(),
  };
});

const getAuthSession = vi.mocked(api.getAuthSession);
const signIn = vi.mocked(api.signIn);
const onUnauthorized = vi.mocked(api.onUnauthorized);

/** Fire the client's unauthorized signal, as a 401 response would. */
function lapseTheSession(): void {
  const registered = onUnauthorized.mock.calls
    .map(([listener]) => listener)
    .filter((listener): listener is () => void => typeof listener === "function");
  const latest = registered.at(-1);
  if (!latest) {
    throw new Error("the gate never subscribed to the unauthorized signal");
  }
  latest();
}

function session(overrides: Partial<AuthSession> = {}) {
  return {
    authenticated: false,
    required: true,
    kind: null,
    expires_at: null,
    ...overrides,
  };
}

function gate() {
  return render(
    <AuthGate>
      <p>the dashboard</p>
    </AuthGate>,
  );
}

describe("AuthGate", () => {
  beforeEach(() => {
    getAuthSession.mockReset();
    signIn.mockReset();
    onUnauthorized.mockReset();
  });

  it("shows sign-in instead of the dashboard when a session is needed", async () => {
    getAuthSession.mockResolvedValue(session());

    gate();

    expect(
      await screen.findByRole("heading", { name: "Data platform" }),
    ).toBeVisible();
    expect(screen.queryByText("the dashboard")).toBeNull();
  });

  it("renders the dashboard once signed in", async () => {
    getAuthSession.mockResolvedValue(session({ authenticated: true }));

    gate();

    expect(await screen.findByText("the dashboard")).toBeVisible();
  });

  it("renders straight through on a deployment that needs no sign-in", async () => {
    // A local stack bound to loopback reports itself authenticated.
    getAuthSession.mockResolvedValue(
      session({ authenticated: true, required: false }),
    );

    gate();

    expect(await screen.findByText("the dashboard")).toBeVisible();
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("signs in and then reveals the dashboard", async () => {
    const user = userEvent.setup();
    getAuthSession
      .mockResolvedValueOnce(session())
      .mockResolvedValue(session({ authenticated: true }));
    signIn.mockResolvedValue({
      authenticated: true,
      token: "t",
      expires_at: "2026-11-01T00:00:00Z",
    });

    gate();
    await user.type(await screen.findByLabelText("Password"), "hunter2hunter2");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("the dashboard")).toBeVisible();
    expect(signIn).toHaveBeenCalledWith("hunter2hunter2");
  });

  it("reports a rejected password and clears the field", async () => {
    const user = userEvent.setup();
    getAuthSession.mockResolvedValue(session());
    signIn.mockRejectedValue(
      new api.ApiClientError("That password was not accepted.", "invalid_credentials", 401),
    );

    gate();
    const field = await screen.findByLabelText("Password");
    await user.type(field, "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByText("That password was not accepted."),
    ).toBeVisible();
    // A rejected password must not sit in the field afterwards.
    expect(field).toHaveValue("");
  });

  it("passes the lockout message through as the backend wrote it", async () => {
    const user = userEvent.setup();
    getAuthSession.mockResolvedValue(session());
    signIn.mockRejectedValue(
      new api.ApiClientError(
        "Too many failed sign-in attempts. Try again in 240 seconds.",
        "too_many_attempts",
        429,
      ),
    );

    gate();
    await user.type(await screen.findByLabelText("Password"), "whatever-here");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText(/Try again in 240 seconds/)).toBeVisible();
  });

  it("returns to sign-in when a session lapses mid-use", async () => {
    getAuthSession.mockResolvedValue(session({ authenticated: true }));

    gate();
    await screen.findByText("the dashboard");

    // Any request answering 401 notifies the client, which the gate observes.
    act(() => lapseTheSession());

    await waitFor(() =>
      expect(screen.queryByText("the dashboard")).toBeNull(),
    );
    expect(await screen.findByLabelText("Password")).toBeVisible();
  });

  it("says so when the backend cannot be reached at all", async () => {
    getAuthSession.mockRejectedValue(new Error("offline"));

    gate();

    expect(
      await screen.findByText("The backend is not reachable"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("cannot submit an empty password", async () => {
    getAuthSession.mockResolvedValue(session());

    gate();
    await screen.findByLabelText("Password");

    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    expect(signIn).not.toHaveBeenCalled();
  });
});
