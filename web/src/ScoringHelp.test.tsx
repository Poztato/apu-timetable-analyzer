/* @vitest-environment jsdom */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { ScoringHelp } from "./ScoringHelp";
import { parseDashboardData } from "./data";
import type { DashboardData } from "./types";

let data: DashboardData;

beforeAll(() => {
  const path = resolve(process.cwd(), "public/data/latest.json");
  data = parseDashboardData(
    JSON.parse(readFileSync(path, "utf-8")) as unknown,
  );
});

afterEach(cleanup);

describe("Scoring help", () => {
  it("starts with a simple factor menu and opens one clear explanation", async () => {
    const user = userEvent.setup();
    render(
      <div className="tn-root">
        <section className="tn-step-panel">
          <ScoringHelp
            scoring={data.scoring}
            preferences={{
              timePreference: "balanced",
              emphasizeShortDays: false,
              emphasizeLongDays: false,
            }}
            triggerLabel="How scoring works"
          />
        </section>
      </div>,
    );

    const trigger = screen.getByRole("button", { name: "How scoring works" });
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", {
      name: "How is my timetable ranked?",
    });
    expect(dialog.parentElement?.classList.contains("score-help-overlay")).toBe(true);
    expect(dialog.parentElement?.parentElement?.classList.contains("tn-root")).toBe(true);
    expect(dialog.parentElement?.parentElement?.classList.contains("tn-step-panel")).toBe(false);
    expect(
      within(dialog).getByText(
        /By default, your timetable is ranked based on these factors/i,
      ),
    ).toBeTruthy();

    const factors = within(dialog).getByRole("navigation", {
      name: "Timetable ranking factors",
    });
    expect(within(factors).getAllByRole("button")).toHaveLength(7);
    expect(factors.querySelectorAll("[data-factor-icon]")).toHaveLength(7);

    for (const factor of [
      "Campus trips",
      "Online-only days",
      "Time placement",
      "Day span",
      "Campus waiting",
      "Short campus days",
      "Heavy teaching days",
    ]) {
      expect(
        within(factors).getByRole("button", { name: new RegExp(factor) }),
      ).toBeTruthy();
    }

    expect(dialog.textContent).not.toMatch(
      /base cost|time band|daily cap|smooth range|weighted deviation/i,
    );

    await user.click(
      within(factors).getByRole("button", { name: /Campus trips/ }),
    );
    expect(
      within(dialog).getByRole("heading", { name: "Campus trips" }),
    ).toBeTruthy();
    expect(
      within(dialog).getByText(
        "A day without a campus trip is easier than a day that needs one.",
      ),
    ).toBeTruthy();
    const goodHeading = within(dialog).getByRole("heading", {
      name: "Good Timetable (no classes that day)",
    });
    expect(goodHeading).toBeTruthy();
    expect(
      goodHeading.closest(".score-help-scenario")?.querySelector(
        ".score-help-day-visual",
      ),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("heading", {
        name: "Bad Timetable (a physical class day)",
      }),
    ).toBeTruthy();
    expect(within(dialog).getAllByRole("img")).toHaveLength(2);

    const backButton = within(dialog).getByRole("button", {
      name: "Back to all factors",
    });
    expect(backButton.querySelector("svg")).toBeTruthy();
    await user.click(backButton);
    expect(
      within(dialog).getByRole("navigation", {
        name: "Timetable ranking factors",
      }),
    ).toBeTruthy();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("uses the selected preferred time in the placement explanation", async () => {
    const user = userEvent.setup();
    render(
      <div className="db-root">
        <ScoringHelp
          scoring={data.scoring}
          preferences={{
            timePreference: "morning",
            emphasizeShortDays: true,
            emphasizeLongDays: false,
          }}
        />
      </div>,
    );

    await user.click(screen.getByRole("button", { name: "Scoring guide" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.parentElement?.parentElement?.classList.contains("db-root")).toBe(true);
    const factors = within(dialog).getByRole("navigation", {
      name: "Timetable ranking factors",
    });
    await user.click(
      within(factors).getByRole("button", { name: /Time placement/ }),
    );

    expect(
      within(dialog).getByText(/Your selected time is morning/i),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("heading", {
        name: "Good Timetable (class near your chosen time)",
      }),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("heading", {
        name: "Bad Timetable (class far from your chosen time)",
      }),
    ).toBeTruthy();
  });

  it("keeps the close control still on hover", () => {
    const css = readFileSync(
      resolve(process.cwd(), "src/scoring-help.css"),
      "utf-8",
    );
    expect(css).not.toContain("rotate(");
    expect(css).toContain("backdrop-filter: blur(8px)");
  });
});
