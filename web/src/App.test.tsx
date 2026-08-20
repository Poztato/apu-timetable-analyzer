/* @vitest-environment jsdom */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { Dashboard, scheduleBlocksWithGaps } from "./App";
import { parseDashboardData } from "./data";
import {
  createScoringContext,
  filterWeeklyMetrics,
  rankVariants,
} from "./ranking";
import type { TimetableBlock } from "./types";

afterEach(cleanup);

function loadRealDashboardData() {
  const path = resolve(process.cwd(), "public/data/latest.json");
  return parseDashboardData(
    JSON.parse(readFileSync(path, "utf-8")) as unknown,
  );
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-MY").format(value);
}

function timetableBlock(
  moduleId: string,
  startAt: string,
  endAt: string,
  deliveryMode: TimetableBlock["delivery_mode"],
): TimetableBlock {
  return {
    variant_index: 0,
    event_date: startAt.slice(0, 10),
    start_at: startAt,
    end_at: endAt,
    duration_minutes: Math.round(
      (Date.parse(endAt) - Date.parse(startAt)) / 60_000,
    ),
    module_id: moduleId,
    module_name: moduleId,
    class_code: null,
    location: null,
    room: null,
    delivery_mode: deliveryMode,
    source_grouping: "G1",
    is_common_event: false,
    is_elective: false,
    elective_group_id: null,
    elective_option_id: null,
    is_shared_slot: false,
    shared_group_count: 1,
    color: null,
  };
}

describe("Campus-bound gap markers", () => {
  it("does not show a gap before an online class after the last campus class", () => {
    const blocks = [
      timetableBlock(
        "CAMPUS-MORNING",
        "2026-08-11T11:15:00+08:00",
        "2026-08-11T12:45:00+08:00",
        "campus",
      ),
      timetableBlock(
        "CAMPUS-AFTERNOON",
        "2026-08-11T14:00:00+08:00",
        "2026-08-11T15:00:00+08:00",
        "campus",
      ),
      timetableBlock(
        "ONLINE",
        "2026-08-11T18:45:00+08:00",
        "2026-08-11T20:45:00+08:00",
        "online",
      ),
    ];

    expect(scheduleBlocksWithGaps(blocks).map((item) => item.gapBefore)).toEqual([
      0, 75, 0,
    ]);
  });

  it("keeps an online class occupied between two campus classes", () => {
    const blocks = [
      timetableBlock(
        "CAMPUS-MORNING",
        "2026-08-11T09:00:00+08:00",
        "2026-08-11T10:00:00+08:00",
        "campus",
      ),
      timetableBlock(
        "ONLINE-MIDDAY",
        "2026-08-11T12:00:00+08:00",
        "2026-08-11T13:00:00+08:00",
        "online",
      ),
      timetableBlock(
        "CAMPUS-AFTERNOON",
        "2026-08-11T15:00:00+08:00",
        "2026-08-11T16:00:00+08:00",
        "campus",
      ),
    ];

    expect(scheduleBlocksWithGaps(blocks).map((item) => item.gapBefore)).toEqual([
      0, 120, 120,
    ]);
  });
});

describe("Dashboard MVP", () => {
  it("reproduces every exported default ranking across all weeks", () => {
    const data = loadRealDashboardData();
    const context = createScoringContext(data.dailyMetrics, data.timetableBlocks);
    const preferences = {
      timePreference: data.scoring.default_time_preference,
      emphasizeShortDays: false,
      emphasizeLongDays: false,
    };

    for (const week of data.weeks) {
      const exported = data.weeklyMetrics.filter(
        (row) => row.week_start === week.week_start,
      );
      const recalculated = rankVariants(
        exported,
        data.scoring,
        preferences,
        context,
      );

      for (const row of recalculated) {
        expect(row.recalculatedScore).toBeCloseTo(row.overall_score, 5);
        expect(row.recalculatedBestRank).toBe(row.best_rank);
        expect(row.recalculatedWorstRank).toBe(row.worst_rank);
        expect(row.recalculatedIsBest).toBe(row.is_best);
        expect(row.recalculatedIsWorst).toBe(row.is_worst);
        expect(row.recalculatedIsMostAverage).toBe(row.is_most_average);
      }
    }
  });

  it("supports smart filters, fuzzy search, inspection, and comparison", async () => {
    const data = loadRealDashboardData();
    expect(data.filters.courses.every((option) => option.name)).toBe(true);
    expect(data.filters.specialisms.every((option) => option.name)).toBe(true);
    const now = new Date();
    const today = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
    ].join("-");
    const defaultWeek = data.weeks.find(
      (week) => week.week_start <= today && today <= week.week_end,
    )?.week_start ?? data.weeks[0].week_start;
    const defaultPeerCount = data.weeklyMetrics.filter(
      (row) => row.week_start === defaultWeek,
    ).length;
    const user = userEvent.setup();
    render(<Dashboard data={data} />);

    expect(
      screen.getByRole("heading", {
        name: "Dashboard",
      }),
    ).toBeTruthy();
    const controls = screen.getByRole("region", { name: "Dashboard controls" });
    expect(
      within(controls).getByText(`${formatNumber(defaultPeerCount)} variants`),
    ).toBeTruthy();
    expect(within(controls).queryByText("Fixed daily recipe")).toBeNull();
    expect(
      within(controls).getByRole("button", { name: "How scoring works" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);

    const inspectTab = screen.getByRole("tab", { name: /Inspect timetable/ });
    const compareTab = screen.getByRole("tab", { name: /Compare timetables/ });
    expect(inspectTab).toHaveProperty("disabled", false);
    expect(compareTab).toHaveProperty("disabled", false);

    await user.click(inspectTab);
    expect(screen.getByText("Choose a timetable to inspect.")).toBeTruthy();
    const inspectorPicker = screen.getByRole("combobox", {
      name: "Timetable to inspect",
    });
    expect(inspectorPicker.textContent).toContain("Choose a timetable");
    await user.click(inspectorPicker);
    const inspectorOptions = within(
      screen.getByRole("listbox", { name: "Timetable to inspect" }),
    ).getAllByRole("option");
    await user.click(inspectorOptions[0]);
    expect(
      screen.getByRole("region", { name: /Weekly timetable for/ }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Explain score components" }),
    ).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: /Compare timetables/ }));
    expect(
      screen.queryByRole("button", { name: "Explain score components" }),
    ).toBeNull();
    expect(screen.getByText("Choose two timetables to begin.")).toBeTruthy();
    expect(
      screen.getByRole("combobox", { name: "Timetable A" }).textContent,
    ).toContain("Choose timetable A");
    expect(
      screen.getByRole("combobox", { name: "Timetable B" }).textContent,
    ).toContain("Choose timetable B");
    expect(screen.queryByRole("region", { name: "Comparison summary" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: /Ranked list/ }));

    const programmeLevelSelect = screen.getByRole("combobox", {
      name: "Programme level",
    });
    expect(programmeLevelSelect.textContent).toContain("All programme levels");
    await user.click(programmeLevelSelect);
    const programmeOptions = screen.getByRole("listbox", {
      name: "Programme level",
    });
    expect(
      within(programmeOptions).getByRole("option", { name: /^Degree/ }),
    ).toBeTruthy();
    expect(
      within(programmeOptions).queryByRole("option", {
        name: /^degree: Degree/,
      }),
    ).toBeNull();

    const afternoonPreference = screen.getByRole("radio", {
      name: /Afternoon.*13:30 to 16:00/i,
    });
    await user.click(afternoonPreference);
    expect(afternoonPreference).toHaveProperty("checked", true);

    const intakeByCode = new Map(
      data.intakes.map((intake) => [intake.intake_code, intake]),
    );
    const expectedCoursePeers = filterWeeklyMetrics(
      data.weeklyMetrics,
      intakeByCode,
      {
        weekStart: defaultWeek,
        grouping: "",
        programmeLevel: "",
        programmeRoute: "",
        academicLevel: "",
        courseCode: "CS",
        specialismCode: "",
        school: "",
        studyMode: "",
        deliveryMode: "",
      },
    ).length;

    const expectedFoundationPeers = filterWeeklyMetrics(
      data.weeklyMetrics,
      intakeByCode,
      {
        weekStart: defaultWeek,
        grouping: "",
        programmeLevel: "foundation",
        programmeRoute: "",
        academicLevel: "",
        courseCode: "",
        specialismCode: "",
        school: "",
        studyMode: "",
        deliveryMode: "",
      },
    ).length;
    await user.click(programmeLevelSelect);
    await user.click(
      within(
        screen.getByRole("listbox", { name: "Programme level" }),
      ).getByRole("option", { name: /^Foundation/ }),
    );
    expect(
      await within(controls).findByText(
        `${formatNumber(expectedFoundationPeers)} variants`,
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("combobox", { name: "Specialism" }),
    ).toHaveProperty("disabled", true);

    await user.click(
      screen.getByRole("combobox", { name: "Programme level" }),
    );
    await user.click(
      within(
        screen.getByRole("listbox", { name: "Programme level" }),
      ).getByRole("option", { name: /^All programme levels/ }),
    );

    await user.click(screen.getByRole("combobox", { name: "Course" }));
    const courseOptions = screen.getByRole("listbox", { name: "Course" });
    expect(
      within(courseOptions).getByRole("option", {
        name: /^Computer Science \(CS\)/,
      }),
    ).toBeTruthy();
    await user.click(
      within(courseOptions).getByRole("option", {
        name: /^Computer Science \(CS\)/,
      }),
    );
    expect(
      await within(controls).findByText(`${formatNumber(expectedCoursePeers)} variants`),
    ).toBeTruthy();

    await user.click(screen.getByRole("combobox", { name: "Specialism" }));
    const specialismOptions = screen.getByRole("listbox", {
      name: "Specialism",
    });
    expect(
      within(specialismOptions).getByRole("option", { name: /^Data Analytics/ }),
    ).toBeTruthy();
    expect(
      within(specialismOptions).queryByRole("option", {
        name: /^Financial Technology/,
      }),
    ).toBeNull();
    await user.keyboard("{Escape}");

    const search = screen.getByLabelText("Search intake or programme");
    await user.type(search, "APD3F2605CS(D)");
    const matchingRow = screen.getByRole("row", {
      name: /APD3F2605CS\(DA\).*G1/i,
    });
    expect(matchingRow.textContent).toContain("APD3F2605CS(DA)");
    expect(
      screen.getByText(`Scores still use ${formatNumber(expectedCoursePeers)} peers.`),
    ).toBeTruthy();
    expect(
      within(matchingRow).queryByRole("button", { name: "Compare" }),
    ).toBeNull();

    await user.click(within(matchingRow).getByRole("button", { name: "Inspect" }));
    expect(
      screen
        .getByRole("tab", { name: /Inspect timetable/ })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen.getByRole("region", {
        name: "Weekly timetable for APD3F2605CS(DA)",
      }),
    ).toBeTruthy();
    expect(screen.getByText("Lower is better")).toBeTruthy();
    expect(screen.getByText("/100")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Compare as/ })).toBeNull();

    await user.click(screen.getByRole("tab", { name: /Compare timetables/ }));
    expect(
      screen.getByRole("heading", {
        name: "Compare two timetables.",
      }),
    ).toBeTruthy();

    await user.click(screen.getByRole("combobox", { name: "Timetable A" }));
    const timetableAOptions = within(
      screen.getByRole("listbox", { name: "Timetable A" }),
    ).getAllByRole("option");
    expect(timetableAOptions.length).toBeGreaterThan(1);
    await user.click(timetableAOptions[0]);

    await user.click(screen.getByRole("combobox", { name: "Timetable B" }));
    const timetableBOptions = within(
      screen.getByRole("listbox", { name: "Timetable B" }),
    ).getAllByRole("option");
    await user.click(timetableBOptions.at(-1)!);

    expect(screen.getByRole("region", { name: "Comparison summary" })).toBeTruthy();
    expect(
      screen.getByRole("region", {
        name: /Compared weekly timetable for/,
      }),
    ).toBeTruthy();
  });
});
