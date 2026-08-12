/* @vitest-environment jsdom */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CampusNotebook, rankIntakeMatches } from "./CampusNotebook";
import { parseDashboardData } from "./data";
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
    const user = userEvent.setup();
    render(<CampusNotebook data={data} onOpenDashboard={vi.fn()} />);

    const search = screen.getByRole("combobox", { name: "Search intake code" });
    await user.type(search, "APU2F2602CS(DF)");
    await user.keyboard("{Enter}{Enter}");
    await user.click(
      screen.getByRole("button", { name: /Continue to frustrations/ }),
    );

    for (const frustration of [
      "Late-only campus days",
      "Early-only campus days",
      "One-hour-only campus trips",
      "Overloaded days",
    ]) {
      await user.click(
        screen.getByRole("button", { name: `Remove ${frustration}` }),
      );
    }

    await user.click(
      screen.getByRole("button", { name: /Continue to comparison/ }),
    );
    await user.click(screen.getByRole("button", { name: /Show my timetable/ }));

    expect(
      screen.getByRole("heading", {
        name: "1,021 out of 1,023 timetables are better than yours.",
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "2 timetables share this score, so the tied positions run from 1,022 to 1,023.",
      ),
    ).toBeTruthy();

    const resultSummary = screen.getByRole("complementary", {
      name: "Result summary",
    });
    expect(within(resultSummary).getByText("Your position")).toBeTruthy();
    expect(within(resultSummary).getByText("1,022")).toBeTruthy();
    expect(within(resultSummary).getByText("of 1,023")).toBeTruthy();
  });

  it("supports the keyboard flow, detected-only states, priorities, and result", async () => {
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
      screen.getByRole("heading", { name: "Let us match your timetable." }),
    ).toBeTruthy();
    expect(screen.getByText("Only one group detected")).toBeTruthy();
    expect(screen.getByText("No electives detected")).toBeTruthy();

    await user.click(
      screen.getByRole("button", { name: /Continue to frustrations/ }),
    );
    expect(
      screen.getByRole("heading", { name: "What bothers you most?" }),
    ).toBeTruthy();
    expect(screen.getByText("Most frustrating")).toBeTruthy();
    expect(screen.getByText("Least frustrating")).toBeTruthy();
    expect(
      screen.getByText("Your biggest frustration: Long gaps between classes"),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Drag Long gaps between classes" }),
    ).toBeNull();

    await user.click(
      screen.getByRole("button", { name: "Remove Long gaps between classes" }),
    );
    expect(
      screen.getByText("Your biggest frustration: Late-only campus days"),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Undo/ }));
    expect(
      screen.getByText("Your biggest frustration: Long gaps between classes"),
    ).toBeTruthy();

    const equalWeight = screen.getByRole("checkbox", {
      name: /Treat everything equally/,
    });
    await user.click(equalWeight);
    expect(
      screen.getByText("All 5 remaining frustrations count equally."),
    ).toBeTruthy();
    await user.click(
      screen.getByRole("checkbox", { name: /Use default settings/ }),
    );
    expect(
      screen.getByText("Your biggest frustration: Long gaps between classes"),
    ).toBeTruthy();

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
    expect(screen.getByText("Based on your configuration...")).toBeTruthy();
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
    expect(within(resultSummary).getByText("422")).toBeTruthy();
    expect(within(resultSummary).getByText("of 1,023")).toBeTruthy();
    expect(within(resultSummary).getByText("Lower is better")).toBeTruthy();
    expect(within(resultSummary).queryByRole("button")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "How your score was built." }),
    ).toBeTruthy();
    const scoreTable = screen.getByRole("table");
    expect(within(scoreTable).getAllByRole("row")).toHaveLength(6);
    expect(within(scoreTable).getByText("Peer percentile")).toBeTruthy();
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
