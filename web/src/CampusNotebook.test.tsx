/* @vitest-environment jsdom */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CampusNotebook, rankIntakeMatches } from "./CampusNotebook";
import { parseDashboardData } from "./data";
import {
  createScoringContext,
  rankVariants,
  summarizeRankPosition,
} from "./ranking";
import type { DashboardData } from "./types";

let data: DashboardData;

beforeAll(() => {
  const path = resolve(process.cwd(), "public/data/latest.json");
  data = parseDashboardData(
    JSON.parse(readFileSync(path, "utf-8")) as unknown,
  );
  Object.defineProperty(window, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(window, "scrollBy", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(cleanup);

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-MY").format(value);
}

function activeWeek(): string {
  const now = new Date();
  const today = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
  return data.weeks.find(
    (week) => week.week_start <= today && today <= week.week_end,
  )?.week_start ?? data.weeks[0].week_start;
}

function defaultRanked() {
  return rankVariants(
    data.weeklyMetrics.filter((row) => row.week_start === activeWeek()),
    data.scoring,
    {
      timePreference: data.scoring.default_time_preference,
      emphasizeShortDays: false,
      emphasizeLongDays: false,
    },
    createScoringContext(data.dailyMetrics, data.timetableBlocks),
  );
}

describe("Campus Notebook wizard", () => {
  it("ranks typo-tolerant intake suggestions by their strongest match", () => {
    const matches = rankIntakeMatches(
      data.intakes,
      "APD3F2605CS(D)",
      "2026-08-10",
    );

    expect(matches[0].intake.intake_code).toBe("APD3F2605CS(DA)");
    expect(matches[0].kind).toMatch(/Strong|Close/);
  });

  it("derives valid group and elective choices from the selected intake", async () => {
    const user = userEvent.setup();
    render(<CampusNotebook data={data} onOpenDashboard={vi.fn()} />);

    const search = screen.getByRole("combobox", { name: "Search intake code" });
    await user.type(search, "APD2F2602CS(CYB)");
    await user.keyboard("{Enter}{Enter}");

    expect(screen.getByRole("button", { name: "G1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "G2" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "G3" })).toBeTruthy();
    expect(screen.getByText("Social Psychology")).toBeTruthy();
    expect(screen.getByText("Implementation of Secure Systems")).toBeTruthy();
    expect(screen.queryByText("Data Analytics")).toBeNull();
    expect(screen.queryByText(/^resolved$/i)).toBeNull();
    expect(screen.queryByText(/Smart filtering is active/i)).toBeNull();
  });

  it("explains the tied worst position for APU2F2602CS(DF)", async () => {
    const ranked = defaultRanked();
    const target = ranked.find(
      (row) => row.intake_code === "APU2F2602CS(DF)",
    );
    expect(target).toBeDefined();
    const position = summarizeRankPosition(target!);
    const user = userEvent.setup();
    render(<CampusNotebook data={data} onOpenDashboard={vi.fn()} />);

    const search = screen.getByRole("combobox", { name: "Search intake code" });
    await user.type(search, "APU2F2602CS(DF)");
    await user.keyboard("{Enter}{Enter}");
    await user.click(
      screen.getByRole("button", { name: /Continue to preferences/ }),
    );
    await user.click(
      screen.getByRole("button", { name: /Continue to comparison/ }),
    );
    await user.click(screen.getByRole("button", { name: /Show my timetable/ }));

    expect(
      screen.getByRole("heading", {
        name: `${formatNumber(position.betterCount)} out of ${formatNumber(target!.peerCount)} timetables are better than yours.`,
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        `${formatNumber(position.tiedCount)} timetables share this score, so the tied positions run from ${formatNumber(position.firstPosition)} to ${formatNumber(position.lastPosition)}.`,
      ),
    ).toBeTruthy();

    const resultSummary = screen.getByRole("complementary", {
      name: "Result summary",
    });
    expect(within(resultSummary).getByText("Your position")).toBeTruthy();
    expect(
      within(resultSummary).getByText(formatNumber(position.firstPosition)),
    ).toBeTruthy();
    expect(
      within(resultSummary).getByText(`of ${formatNumber(target!.peerCount)}`),
    ).toBeTruthy();
  });

  it("supports the keyboard flow, detected-only states, preferences, and result", async () => {
    const ranked = rankVariants(
      data.weeklyMetrics.filter((row) => row.week_start === activeWeek()),
      data.scoring,
      {
        timePreference: data.scoring.default_time_preference,
        emphasizeShortDays: true,
        emphasizeLongDays: false,
      },
      createScoringContext(data.dailyMetrics, data.timetableBlocks),
    );
    const expectedResult = ranked.find(
      (row) =>
        row.intake_code === "APD3F2605CS(DA)" && row.grouping === "G1",
    );
    expect(expectedResult).toBeDefined();
    const user = userEvent.setup();
    const openDashboard = vi.fn();
    render(<CampusNotebook data={data} onOpenDashboard={openDashboard} />);

    expect(
      screen
        .getByRole("button", { name: /Continue to configuration/ })
        .classList.contains("is-find"),
    ).toBe(true);

    const search = screen.getByRole("combobox", { name: "Search intake code" });
    await user.type(search, "APD3F2605CS(D)");
    expect(search.getAttribute("aria-activedescendant")).toBe("tn-suggestion-0");

    await user.keyboard("{ArrowDown}");
    expect(search.getAttribute("aria-activedescendant")).toBe("tn-suggestion-1");
    await user.keyboard("{ArrowUp}");
    expect(search.getAttribute("aria-activedescendant")).toBe("tn-suggestion-0");

    await user.keyboard("{Enter}");
    expect(search).toHaveProperty("value", "APD3F2605CS(DA)");
    expect(
      screen.getByRole("heading", { name: "Which intake are you in?" }),
    ).toBeTruthy();

    await user.keyboard("{Enter}");
    expect(
      screen.getByRole("heading", { name: "Which timetable should we use?" }),
    ).toBeTruthy();
    expect(screen.getByText("Only one group detected")).toBeTruthy();
    expect(screen.getByText("No electives detected")).toBeTruthy();

    await user.click(
      screen.getByRole("button", { name: /Continue to preferences/ }),
    );
    expect(
      screen.getByRole("heading", { name: "When should your classes happen?" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("radio", { name: /Balanced midday/ }),
    ).toBeTruthy();
    expect(screen.queryByText("One clear daily score")).toBeNull();
    const scoringHelp = screen.getByRole("button", {
      name: "How scoring works",
    });
    expect(scoringHelp.closest(".tn-time-heading-row")).toBeTruthy();
    expect(scoringHelp.closest(".tn-step-intro")).toBeNull();
    expect(screen.queryByText("LIVE RECIPE")).toBeNull();
    const shortTripEmphasis = screen.getByRole("checkbox", {
      name: /Avoid short campus trips/,
    });
    await user.click(shortTripEmphasis);
    expect((shortTripEmphasis as HTMLInputElement).checked).toBe(true);

    await user.click(
      screen.getByRole("button", { name: /Continue to comparison/ }),
    );
    expect(
      screen.getByRole("heading", { name: "Who should we compare you with?" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("checkbox", { name: /Stay within my school/ }),
    ).toBeTruthy();
    expect(screen.queryByText(/Hide schedules without classes/i)).toBeNull();

    await user.click(screen.getByRole("button", { name: /Show my timetable/ }));
    expect(screen.getByText("Step 5/5")).toBeTruthy();
    expect(
      screen.getByRole("heading", {
        name: /timetables are better than yours|No timetable out of/,
      }),
    ).toBeTruthy();
    expect(screen.getByRole("region", { name: "Weekly timetable" })).toBeTruthy();
    expect(screen.queryByText("Days run across the top. Time runs down the left.")).toBeNull();
    expect(
      screen.getByRole("heading", {
        name: "Computer Science with a specialism in Data Analytics",
      }),
    ).toBeTruthy();
    expect(
      screen.getByText("Degree, Year 3, Dual-degree programme"),
    ).toBeTruthy();

    const resultSummary = screen.getByRole("complementary", {
      name: "Result summary",
    });
    expect(within(resultSummary).getByText("Your position")).toBeTruthy();
    expect(
      within(resultSummary).getByText(
        formatNumber(expectedResult!.recalculatedBestRank),
      ),
    ).toBeTruthy();
    expect(
      within(resultSummary).getByText(
        `of ${formatNumber(expectedResult!.peerCount)}`,
      ),
    ).toBeTruthy();
    expect(within(resultSummary).getByText("Lower is better")).toBeTruthy();
    expect(within(resultSummary).queryByRole("button")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "How your score was built." }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Explain score components" }),
    ).toBeTruthy();
    const scoreTable = screen.getByRole("table");
    expect(within(scoreTable).getAllByRole("row")).toHaveLength(8);
    expect(within(scoreTable).getByText("Daily cap")).toBeTruthy();
    expect(within(scoreTable).getByText("Score impact")).toBeTruthy();
    expect(screen.getAllByText("Lower is better")).toHaveLength(2);

    const dashboardNext = screen.getByRole("region", {
      name: "Put your timetable beside the best and worst.",
    });
    expect(within(dashboardNext).getByText("UP NEXT")).toBeTruthy();
    expect(within(dashboardNext).getByText("06")).toBeTruthy();
    await user.click(
      within(dashboardNext).getByRole("button", { name: /View dashboard/ }),
    );
    expect(openDashboard).toHaveBeenCalledTimes(1);
    expect(openDashboard.mock.calls[0]).toHaveLength(0);
  });
});
