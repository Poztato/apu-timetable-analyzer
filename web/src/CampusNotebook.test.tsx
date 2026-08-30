/* @vitest-environment jsdom */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  CampusNotebook,
  normalizeIntakeSearch,
  rankIntakeMatches,
} from "./CampusNotebook";
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

function findSingleConfigurationFixture() {
  const weekStart = activeWeek();
  for (const intake of data.intakes) {
    const rows = data.weeklyMetrics.filter(
      (row) =>
        row.week_start === weekStart && row.intake_code === intake.intake_code,
    );
    if (
      rows.length !== 1 ||
      !["no_electives", "not_active"].includes(rows[0].elective_status)
    ) {
      continue;
    }

    const normalized = normalizeIntakeSearch(intake.intake_code);
    for (let index = normalized.length - 2; index > 0; index -= 1) {
      if (normalized[index] === normalized[index + 1]) continue;
      const characters = [...normalized];
      [characters[index], characters[index + 1]] = [
        characters[index + 1],
        characters[index],
      ];
      const query = characters.join("");
      const matches = rankIntakeMatches(data.intakes, query, weekStart);
      if (
        matches.length > 1 &&
        matches[0].intake.intake_code === intake.intake_code
      ) {
        return { intake, matches, query, row: rows[0] };
      }
    }
  }
  throw new Error("No single-configuration intake supports the keyboard-flow test.");
}

function findElectiveFixture() {
  const weekStart = activeWeek();
  for (const intake of data.intakes) {
    const rows = data.weeklyMetrics.filter(
      (row) =>
        row.week_start === weekStart && row.intake_code === intake.intake_code,
    );
    const profiles = new Map(
      rows.map((row) => [row.elective_profile, row.elective_profile_name]),
    );
    if (profiles.size > 1) return { intake, profiles, rows };
  }
  throw new Error("No intake with multiple elective profiles is available.");
}

function expectedProgrammeTitle(intake: DashboardData["intakes"][number]) {
  const course = intake.course_name ?? intake.course_code ?? intake.intake_code;
  return intake.specialism_name
    ? `${course} with a specialism in ${intake.specialism_name}`
    : course;
}

function expectedProgrammeMeta(intake: DashboardData["intakes"][number]) {
  return [
    intake.programme_level_name,
    intake.academic_level === null ? null : `Year ${intake.academic_level}`,
    intake.programme_route_name,
  ]
    .filter(Boolean)
    .join(", ");
}

describe("Campus Notebook wizard", () => {
  it("shows Leonard's website, social profiles, and contact email", () => {
    render(<CampusNotebook data={data} onOpenDashboard={vi.fn()} />);

    const website = screen.getByRole("link", { name: /Website, leonardsu\.com/i });
    const linkedIn = screen.getByRole("link", { name: /LinkedIn, Leonard Su/i });
    const github = screen.getByRole("link", { name: /GitHub, @Poztato/i });
    const email = screen.getByRole("link", {
      name: /Email Leonard Su at leonardsu\.contact@gmail\.com/i,
    });

    expect(website.getAttribute("href")).toBe("https://leonardsu.com");
    expect(linkedIn.getAttribute("href")).toBe(
      "https://www.linkedin.com/in/leonard-su/",
    );
    expect(github.getAttribute("href")).toBe("https://github.com/Poztato/");
    expect(email.getAttribute("href")).toBe("mailto:leonardsu.contact@gmail.com");
    expect(website.querySelector("img")?.getAttribute("src")).toContain(
      "potato-logo.png",
    );
  });

  it("ranks typo-tolerant intake suggestions by their strongest match", () => {
    const fixture = findSingleConfigurationFixture();

    expect(fixture.matches[0].intake.intake_code).toBe(
      fixture.intake.intake_code,
    );
    expect(fixture.matches[0].kind).toMatch(/Strong|Close/);
  });

  it("derives valid group and elective choices from the selected intake", async () => {
    const fixture = findElectiveFixture();
    const user = userEvent.setup();
    render(<CampusNotebook data={data} onOpenDashboard={vi.fn()} />);

    const search = screen.getByRole("combobox", { name: "Search intake code" });
    await user.type(search, fixture.intake.intake_code);
    await user.keyboard("{Enter}{Enter}");

    const groupings = [...new Set(fixture.rows.map((row) => row.grouping))];
    if (groupings.length === 1) {
      expect(screen.getByText("Only one group detected")).toBeTruthy();
      expect(screen.getByText(groupings[0])).toBeTruthy();
    } else {
      for (const grouping of groupings) {
        expect(screen.getByRole("button", { name: grouping })).toBeTruthy();
      }
    }
    for (const profileName of fixture.profiles.values()) {
      expect(screen.getAllByText(profileName).length).toBeGreaterThan(0);
    }
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
    const fixture = findSingleConfigurationFixture();
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
        row.intake_code === fixture.intake.intake_code &&
        row.grouping === fixture.row.grouping,
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
    await user.type(search, fixture.query);
    expect(search.getAttribute("aria-activedescendant")).toBe("tn-suggestion-0");

    await user.keyboard("{ArrowDown}");
    expect(search.getAttribute("aria-activedescendant")).toBe("tn-suggestion-1");
    await user.keyboard("{ArrowUp}");
    expect(search.getAttribute("aria-activedescendant")).toBe("tn-suggestion-0");

    await user.keyboard("{Enter}");
    expect(search).toHaveProperty("value", fixture.intake.intake_code);
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
      screen.getByRole("heading", {
        name: "What does your ideal timetable look like?",
      }),
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
        name: expectedProgrammeTitle(fixture.intake),
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(expectedProgrammeMeta(fixture.intake)),
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
      name: "See the full list of timetables.",
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
