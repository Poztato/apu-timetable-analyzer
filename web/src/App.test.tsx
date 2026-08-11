/* @vitest-environment jsdom */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { Dashboard } from "./App";
import { parseDashboardData } from "./data";
import { filterWeeklyMetrics, rankVariants } from "./ranking";

afterEach(cleanup);

function loadRealDashboardData() {
  const path = resolve(process.cwd(), "public/data/latest.json");
  return parseDashboardData(
    JSON.parse(readFileSync(path, "utf-8")) as unknown,
  );
}

describe("Dashboard MVP", () => {
  it("reproduces every exported default ranking across all weeks", () => {
    const data = loadRealDashboardData();

    for (const week of data.weeks) {
      const exported = data.weeklyMetrics.filter(
        (row) => row.week_start === week.week_start,
      );
      const recalculated = rankVariants(
        exported,
        data.scoring.default_criterion_order,
        data.scoring.position_weights,
      );

      for (const row of recalculated) {
        expect(row.recalculatedScore).toBe(row.overall_frustration);
        expect(row.recalculatedBestRank).toBe(row.best_rank);
        expect(row.recalculatedWorstRank).toBe(row.worst_rank);
        expect(row.recalculatedIsBest).toBe(row.is_best);
        expect(row.recalculatedIsWorst).toBe(row.is_worst);
        expect(row.recalculatedIsMostAverage).toBe(row.is_most_average);
      }
    }
  });

  it("loads the real export and supports search, filters, and priority changes", async () => {
    const data = loadRealDashboardData();
    const defaultPeerCount = data.weeklyMetrics.filter(
      (row) => row.week_start === "2026-08-10",
    ).length;
    const user = userEvent.setup();
    render(<Dashboard data={data} />);

    expect(
      screen.getByRole("heading", { name: "APU Timetable Analyzer" }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        new RegExp(`${defaultPeerCount} variants in the current comparison set`),
      ),
    ).toBeTruthy();
    expect(screen.getAllByText("AFCF2507ICT (G1)").length).toBeGreaterThan(0);

    const search = screen.getByLabelText("Search intake code");
    await user.type(search, "APD3F2605CS(DA)");
    const matchingRow = screen.getByRole("row", {
      name: /APD3F2605CS\(DA\).*G1/i,
    });
    expect(matchingRow.textContent).toContain("APD3F2605CS(DA)");
    expect(
      screen.getByText(new RegExp(`Scores use ${defaultPeerCount} peers`)),
    ).toBeTruthy();

    await user.click(screen.getByLabelText("Move Gap burden down"));
    const priorities = screen.getByRole("list", {
      name: "Frustration priority order",
    });
    expect(within(priorities).getAllByRole("listitem")[0].textContent).toContain(
      "Late-only campus days",
    );

    const intakeByCode = new Map(
      data.intakes.map((intake) => [intake.intake_code, intake]),
    );
    const expectedCoursePeers = filterWeeklyMetrics(
      data.weeklyMetrics,
      intakeByCode,
      {
        weekStart: "2026-08-10",
        grouping: "",
        programmeRoute: "",
        academicLevel: "",
        courseCode: "CS",
        specialismCode: "",
        school: "",
        studyMode: "",
        deliveryMode: "",
      },
    ).length;
    await user.selectOptions(screen.getByLabelText("Course"), "CS");
    expect(
      await screen.findByText(
        new RegExp(`${expectedCoursePeers} variants in the current comparison set`),
      ),
    ).toBeTruthy();
  });
});
