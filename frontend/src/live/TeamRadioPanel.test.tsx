import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LiveTeamRadioClip } from "../contracts";
import TeamRadioPanel from "./TeamRadioPanel";

function clip(overrides: Partial<LiveTeamRadioClip> = {}): LiveTeamRadioClip {
  return {
    utc: "2026-08-23T13:06:37Z",
    racing_number: "3",
    tla: "VER",
    display_name: "Max VERSTAPPEN",
    team_colour: "4781D7",
    audio_url:
      "https://livetiming.formula1.com/static/2026/x/y/TeamRadio/VER_3.mp3",
    ...overrides,
  };
}

describe("TeamRadioPanel", () => {
  beforeEach(() => {
    // jsdom has no media stack; play() is undefined there.
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
      () => undefined,
    );
  });

  it("lists a clip with its driver and time", () => {
    render(<TeamRadioPanel clips={[clip()]} />);

    expect(screen.getByText("VER")).toBeVisible();
    expect(screen.getByText("Max VERSTAPPEN")).toBeVisible();
  });

  it("plays the clip it was asked for", async () => {
    const user = userEvent.setup();
    render(<TeamRadioPanel clips={[clip()]} />);

    await user.click(screen.getByRole("button", { name: /Play team radio/ }));

    // The feed carries audio, never text, so playing it is the whole feature.
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: /Stop team radio/ }),
    ).toBeVisible();
  });

  it("stops a clip that is already playing", async () => {
    const user = userEvent.setup();
    render(<TeamRadioPanel clips={[clip()]} />);

    await user.click(screen.getByRole("button", { name: /Play team radio/ }));
    await user.click(screen.getByRole("button", { name: /Stop team radio/ }));

    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });

  it("plays one clip at a time", async () => {
    const user = userEvent.setup();
    render(
      <TeamRadioPanel
        clips={[
          clip({ audio_url: "https://example.test/one.mp3", tla: "VER" }),
          clip({ audio_url: "https://example.test/two.mp3", tla: "NOR" }),
        ]}
      />,
    );

    await user.click(screen.getAllByRole("button", { name: /Play/ })[0]);
    await user.click(screen.getAllByRole("button", { name: /Play/ })[0]);

    // Two messages talking over each other is exactly what a radio panel
    // must not do, so only one row is ever in the playing state.
    expect(screen.getAllByRole("button", { name: /Stop/ })).toHaveLength(1);
  });

  it("says so when a clip will not play", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(
      new Error("NotAllowedError"),
    );
    const user = userEvent.setup();
    render(<TeamRadioPanel clips={[clip()]} />);

    await user.click(screen.getByRole("button", { name: /Play/ }));

    // Autoplay policy or an unreachable host must not leave a button that
    // appears to do nothing.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not play",
    );
  });

  it("keeps a long list reachable without a pointer", () => {
    // The list is capped at ten rows and scrolls past that. A scroll container
    // that cannot take focus hides its overflow from keyboard users.
    const many = Array.from({ length: 14 }, (_, index) =>
      clip({ audio_url: `https://example.test/${index}.mp3` }),
    );
    const { container } = render(<TeamRadioPanel clips={many} />);

    const list = container.querySelector(".live-radio");
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveAttribute("aria-label", "Team radio, 14 messages");
    // The count stays in the heading, so the total is visible even when the
    // rows below the cap are not.
    expect(screen.getByText("14")).toBeVisible();
  });

  it("names the driver for a screen reader, not only the code", () => {
    render(<TeamRadioPanel clips={[clip()]} />);

    expect(
      screen.getByRole("button", { name: /Max VERSTAPPEN/ }),
    ).toBeVisible();
  });
});
