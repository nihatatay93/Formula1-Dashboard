import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "./ErrorBoundary";

function Explode({ when }: { when: boolean }): React.ReactElement {
  if (when) {
    throw new Error("items is not defined");
  }
  return <p>the view rendered</p>;
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    // React logs the caught error itself; the boundary logs it again on
    // purpose. Neither belongs in the test output.
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders its children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <Explode when={false} />
      </ErrorBoundary>,
    );

    expect(screen.getByText("the view rendered")).toBeVisible();
  });

  it("contains a render failure instead of blanking the page", () => {
    render(
      <ErrorBoundary label="The standings">
        <Explode when />
      </ErrorBoundary>,
    );

    // This is the failure that took the whole dashboard down once: a field
    // read on a response that did not have it.
    expect(
      screen.getByText(/The standings could not be displayed/),
    ).toBeVisible();
    expect(screen.getByText("items is not defined")).toBeVisible();
  });

  it("announces the failure to assistive technology", () => {
    render(
      <ErrorBoundary>
        <Explode when />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toBeVisible();
  });

  it("retries when asked", async () => {
    const user = userEvent.setup();
    // Held outside the component: React retries a failed concurrent render
    // synchronously, so a component that throws only once never reaches the
    // boundary at all. The condition has to survive that retry.
    let failing = true;

    function Flaky() {
      if (failing) {
        throw new Error("transient");
      }
      return <p>recovered</p>;
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeVisible();

    failing = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(screen.getByText("recovered")).toBeVisible();
  });

  it("clears the failure when the view changes", () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="standings">
        <Explode when />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeVisible();

    rerender(
      <ErrorBoundary resetKey="race-pace">
        <Explode when={false} />
      </ErrorBoundary>,
    );

    // Navigating away and back must not leave a view permanently broken.
    expect(screen.getByText("the view rendered")).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
