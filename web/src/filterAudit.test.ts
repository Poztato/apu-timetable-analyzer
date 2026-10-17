/* @vitest-environment node */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  filterCheckerComparisonRows,
  type ComparisonScope,
} from "./CampusNotebook";
import { buildSmartFilterOptions } from "./Dashboard";
import { parseDashboardData } from "./data";
import {
  createScoringContext,
  filterWeeklyMetrics,
  rankVariants,
  summarizeRankPosition,
  type FilterState,
  type ScoringPreferences,
} from "./ranking";
import type { IntakeMetadata, WeeklyMetric } from "./types";

const FILTER_KEYS = [
  "grouping", "programmeLevel", "programmeRoute", "academicLevel",
  "courseCode", "specialismCode", "school", "studyMode", "deliveryMode",
] as const;

const RECIPES: ScoringPreferences[] = [
  { timePreference: "balanced", emphasizeShortDays: false, emphasizeLongDays: false },
  { timePreference: "morning", emphasizeShortDays: false, emphasizeLongDays: false },
  { timePreference: "afternoon", emphasizeShortDays: false, emphasizeLongDays: false },
  { timePreference: "balanced", emphasizeShortDays: true, emphasizeLongDays: false },
  { timePreference: "balanced", emphasizeShortDays: false, emphasizeLongDays: true },
  { timePreference: "balanced", emphasizeShortDays: true, emphasizeLongDays: true },
];
const SCOPES: ComparisonScope[] = ["similar", "level", "all"];

// Defaults to the current generated export. The optional path is only for
// replaying these same checks against archived exports, never production input.
const exportPath = resolve(
  process.cwd(), process.env.APU_TEST_DASHBOARD ?? "public/data/latest.json",
);
const data = parseDashboardData(JSON.parse(readFileSync(exportPath, "utf-8")) as unknown);
const intakeByCode = new Map(data.intakes.map((intake) => [intake.intake_code, intake]));
const scoringContext = createScoringContext(data.dailyMetrics, data.timetableBlocks);

function emptyFilters(weekStart: string): FilterState {
  return {
    weekStart, grouping: "", programmeLevel: "", programmeRoute: "",
    academicLevel: "", courseCode: "", specialismCode: "", school: "",
    studyMode: "", deliveryMode: "",
  };
}

function optionCount(meta: string | undefined): number {
  const match = meta?.match(/[\d,]+/);
  if (!match) throw new Error(`Option count is missing from ${String(meta)}.`);
  return Number(match[0].replaceAll(",", ""));
}

function rowFilters(row: WeeklyMetric, intake: IntakeMetadata): FilterState {
  const deliveryMode = row.total_campus_events > 0 ? "campus"
    : row.total_online_events > 0 ? "online"
      : row.total_unknown_events > 0 ? "unknown" : "";
  return {
    weekStart: row.week_start,
    grouping: row.grouping,
    programmeLevel: intake.programme_level,
    programmeRoute: intake.programme_route ?? "",
    academicLevel: intake.academic_level === null ? "" : String(intake.academic_level),
    courseCode: intake.course_code ?? "",
    specialismCode: intake.specialism_code ?? "",
    school: intake.school ?? "",
    studyMode: intake.study_mode ?? "",
    deliveryMode,
  };
}

function expectValidRanks(rows: WeeklyMetric[], preferences: ScoringPreferences) {
  const ranked = rankVariants(rows, data.scoring, preferences, scoringContext);
  const frequencies = new Map<number, number>();
  for (const row of ranked) {
    frequencies.set(row.recalculatedScore, (frequencies.get(row.recalculatedScore) ?? 0) + 1);
  }
  const orderedScores = [...frequencies.keys()].sort((left, right) => left - right);
  const lowerCounts = new Map<number, number>();
  let lowerCount = 0;
  for (const score of orderedScores) {
    lowerCounts.set(score, lowerCount);
    lowerCount += frequencies.get(score) ?? 0;
  }
  for (const row of ranked) {
    const tiedCount = frequencies.get(row.recalculatedScore) ?? 0;
    const betterCount = lowerCounts.get(row.recalculatedScore) ?? 0;
    const worseCount = rows.length - betterCount - tiedCount;
    const position = summarizeRankPosition(row);
    if (
      !Number.isFinite(row.recalculatedScore) || row.recalculatedScore < 0 ||
      row.recalculatedScore > 100 || row.peerCount !== rows.length ||
      position.betterCount !== betterCount || position.worseCount !== worseCount ||
      position.tiedCount !== tiedCount ||
      position.lastPosition !== position.firstPosition + tiedCount - 1 ||
      row.recalculatedIsBest !== (betterCount === 0) ||
      row.recalculatedIsWorst !== (worseCount === 0)
    ) {
      throw new Error(`Invalid rank for ${row.intake_code} in ${row.week_start}.`);
    }
  }
}

// Every week, filter option, peer row and scoring recipe is still audited.
// Each case has its own unchanged Vitest deadline, so adding weeks or modes
// adds test cases rather than consuming one shared 5-second budget.
describe("dashboard and checker filter audit", () => {
  it("maps every checker week, group, and elective option to one real variant", () => {
    const rowsByIntake = new Map<string, WeeklyMetric[]>();
    for (const row of data.weeklyMetrics) {
      const current = rowsByIntake.get(row.intake_code) ?? [];
      current.push(row);
      rowsByIntake.set(row.intake_code, current);
    }
    const dailyIndexes = new Set(data.dailyMetrics.map((day) => day.variant_index));
    const blockIndexes = new Set(data.timetableBlocks.map((block) => block.variant_index));
    for (const intake of data.intakes) {
      const rows = rowsByIntake.get(intake.intake_code) ?? [];
      const weeks = [...new Set(rows.map((row) => row.week_start))].sort();
      expect(weeks).toEqual([...intake.week_starts].sort());
      expect([...new Set(rows.map((row) => row.grouping))].sort())
        .toEqual([...intake.groupings].sort());
      for (const weekStart of intake.week_starts) {
        const weekRows = rows.filter((row) => row.week_start === weekStart);
        expect(weekRows.length).toBeGreaterThan(0);
        for (const grouping of new Set(weekRows.map((row) => row.grouping))) {
          const groupRows = weekRows.filter((row) => row.grouping === grouping);
          const profiles = groupRows.map((row) => row.elective_profile);
          expect(new Set(profiles).size).toBe(profiles.length);
          for (const row of groupRows) {
            expect(row.total_events).toBeGreaterThan(0);
            expect(dailyIndexes.has(row.variant_index)).toBe(true);
            expect(blockIndexes.has(row.variant_index)).toBe(true);
          }
        }
      }
    }
  });

  describe.each(data.weeks)("week $week_start", (week) => {
    const baseline = emptyFilters(week.week_start);
    const weekRows = data.weeklyMetrics.filter((row) => row.week_start === week.week_start);

    it.each(FILTER_KEYS)("keeps %s options reachable and count-accurate", (sourceKey) => {
      const baselineRows = filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, baseline);
      expect(baselineRows).toHaveLength(week.variant_count);
      const options = buildSmartFilterOptions(data, intakeByCode, baseline);
      expect(options[sourceKey][0].value).toBe("");
      expect(optionCount(options[sourceKey][0].meta)).toBe(baselineRows.length);
      for (const sourceOption of options[sourceKey].slice(1)) {
        const sourceFilters = { ...baseline, [sourceKey]: sourceOption.value } as FilterState;
        const sourceRows = filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, sourceFilters);
        expect(sourceRows.length).toBeGreaterThan(0);
        expect(sourceRows).toHaveLength(optionCount(sourceOption.meta));
        const pairedOptions = buildSmartFilterOptions(data, intakeByCode, sourceFilters);
        for (const targetKey of FILTER_KEYS) {
          if (targetKey === sourceKey) continue;
          for (const targetOption of pairedOptions[targetKey].slice(1)) {
            const pairedRows = filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, {
              ...sourceFilters, [targetKey]: targetOption.value,
            } as FilterState);
            expect(pairedRows.length).toBeGreaterThan(0);
            expect(pairedRows).toHaveLength(optionCount(targetOption.meta));
          }
        }
      }
    });

    it("keeps every exported row reachable by its own filters", () => {
      for (const row of weekRows) {
        const intake = intakeByCode.get(row.intake_code);
        expect(intake).toBeDefined();
        const filtered = filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, rowFilters(row, intake!));
        expect(filtered.some((candidate) => candidate.variant_index === row.variant_index &&
          candidate.week_start === row.week_start)).toBe(true);
      }
    });

    describe.each(SCOPES)("checker scope %s", (scope) => {
      it.each([false, true])("respects its boundary with sameSchool=%s", (sameSchool) => {
        for (const intakeCode of new Set(weekRows.map((row) => row.intake_code))) {
          const selectedIntake = intakeByCode.get(intakeCode);
          expect(selectedIntake).toBeDefined();
          const selectedRows = weekRows.filter((row) => row.intake_code === intakeCode);
          const peers = filterCheckerComparisonRows(weekRows, intakeByCode, selectedIntake!, scope, sameSchool);
          if (peers.length === 0) throw new Error(`${intakeCode} was excluded from an available checker scope.`);
          const peerIndexes = new Set(peers.map((peer) => peer.variant_index));
          for (const selected of selectedRows) {
            if (!peerIndexes.has(selected.variant_index)) {
              throw new Error(`${intakeCode} lost one of its configurations in the checker.`);
            }
          }
          for (const peer of peers) {
            const peerIntake = intakeByCode.get(peer.intake_code)!;
            if (scope === "level" || scope === "similar") {
              if (peerIntake.programme_level !== selectedIntake!.programme_level) {
                throw new Error(`Programme level escaped the ${scope} scope.`);
              }
            }
            if (scope === "similar" && peerIntake.academic_level !== selectedIntake!.academic_level) {
              throw new Error("Academic year escaped the similar scope.");
            }
            if (sameSchool && selectedIntake!.school && peerIntake.school !== selectedIntake!.school) {
              throw new Error("School escaped the same-school refinement.");
            }
          }
        }
      });
    });

    it.each(RECIPES)(
      "maintains valid ranks: $timePreference short=$emphasizeShortDays long=$emphasizeLongDays",
      (recipe) => {
        const options = buildSmartFilterOptions(data, intakeByCode, baseline);
        expectValidRanks(filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, baseline), recipe);
        for (const key of FILTER_KEYS) {
          for (const option of options[key].slice(1)) {
            const peers = filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, {
              ...baseline, [key]: option.value,
            } as FilterState);
            expectValidRanks(peers, recipe);
          }
        }
      },
    );

    // A valid live feed may have one worst row or several. Ties themselves
    // have deterministic coverage in ranking.test.ts and the fixed UI fixture.
    it("keeps all worst scores in their correct rank range, tied or unique", () => {
      const ranked = rankVariants(weekRows, data.scoring, RECIPES[0], scoringContext);
      const worst = ranked.filter((row) => row.recalculatedIsWorst);
      expect(worst.length).toBeGreaterThan(0);
      expect(new Set(worst.map((row) => row.recalculatedScore)).size).toBe(1);
      expect(new Set(worst.map((row) => row.variant_index)).size).toBe(worst.length);
      for (const row of worst) {
        expect(summarizeRankPosition(row)).toMatchObject({
          betterCount: weekRows.length - worst.length,
          firstPosition: weekRows.length - worst.length + 1,
          lastPosition: weekRows.length,
          tiedCount: worst.length,
          worseCount: 0,
        });
      }
    });
  });

  it("contains no duplicate exported comparison rows", () => {
    const keys = data.weeklyMetrics.map((row) => [
      row.week_start, row.intake_code, row.grouping, row.elective_profile,
    ].join("|"));
    expect(new Set(keys).size).toBe(keys.length);
    expect(data.weeklyMetrics.every((row) => row.total_events > 0 && intakeByCode.has(row.intake_code))).toBe(true);
  });
});
