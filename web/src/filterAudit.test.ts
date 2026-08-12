/* @vitest-environment node */
/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import {
  filterCheckerComparisonRows,
  type ComparisonScope,
} from "./CampusNotebook";
import { buildSmartFilterOptions } from "./Dashboard";
import { parseDashboardData } from "./data";
import {
  CRITERION_KEYS,
  filterWeeklyMetrics,
  rankVariants,
  summarizeRankPosition,
  type FilterState,
} from "./ranking";
import type {
  CriterionKey,
  DashboardData,
  IntakeMetadata,
  WeeklyMetric,
} from "./types";

const FILTER_KEYS = [
  "grouping",
  "programmeLevel",
  "programmeRoute",
  "academicLevel",
  "courseCode",
  "specialismCode",
  "school",
  "studyMode",
  "deliveryMode",
] as const;

type FilterKey = (typeof FILTER_KEYS)[number];

let data: DashboardData;
let intakeByCode: Map<string, IntakeMetadata>;

beforeAll(() => {
  const path = resolve(process.cwd(), "public/data/latest.json");
  data = parseDashboardData(
    JSON.parse(readFileSync(path, "utf-8")) as unknown,
  );
  intakeByCode = new Map(
    data.intakes.map((intake) => [intake.intake_code, intake]),
  );
});

function emptyFilters(weekStart: string): FilterState {
  return {
    weekStart,
    grouping: "",
    programmeLevel: "",
    programmeRoute: "",
    academicLevel: "",
    courseCode: "",
    specialismCode: "",
    school: "",
    studyMode: "",
    deliveryMode: "",
  };
}

function optionCount(meta: string | undefined): number {
  const match = meta?.match(/[\d,]+/);
  if (!match) throw new Error(`Option count is missing from ${String(meta)}.`);
  return Number(match[0].replaceAll(",", ""));
}

function rowFilters(row: WeeklyMetric, intake: IntakeMetadata): FilterState {
  const deliveryMode =
    row.total_campus_events > 0
      ? "campus"
      : row.total_online_events > 0
        ? "online"
        : row.total_unknown_events > 0
          ? "unknown"
          : "";
  return {
    weekStart: row.week_start,
    grouping: row.grouping,
    programmeLevel: intake.programme_level,
    programmeRoute: intake.programme_route ?? "",
    academicLevel:
      intake.academic_level === null ? "" : String(intake.academic_level),
    courseCode: intake.course_code ?? "",
    specialismCode: intake.specialism_code ?? "",
    school: intake.school ?? "",
    studyMode: intake.study_mode ?? "",
    deliveryMode,
  };
}

function expectValidRanks(rows: WeeklyMetric[], criteria: CriterionKey[], weights: number[]) {
  const ranked = rankVariants(rows, criteria, weights);
  const frequencies = new Map<number, number>();
  for (const row of ranked) {
    frequencies.set(
      row.recalculatedScore,
      (frequencies.get(row.recalculatedScore) ?? 0) + 1,
    );
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
      !Number.isFinite(row.recalculatedScore) ||
      row.recalculatedScore < 0 ||
      row.recalculatedScore > 100 ||
      row.peerCount !== rows.length ||
      position.betterCount !== betterCount ||
      position.worseCount !== worseCount ||
      position.tiedCount !== tiedCount ||
      position.lastPosition !== position.firstPosition + tiedCount - 1 ||
      row.recalculatedIsBest !== (betterCount === 0) ||
      row.recalculatedIsWorst !== (worseCount === 0)
    ) {
      throw new Error(
        `Invalid rank for ${row.intake_code} in ${row.week_start}.`,
      );
    }
  }
}

describe("dashboard and checker filter audit", () => {
  it("maps every checker week, group, and elective option to one real variant", () => {
    const rowsByIntake = new Map<string, WeeklyMetric[]>();
    for (const row of data.weeklyMetrics) {
      const current = rowsByIntake.get(row.intake_code) ?? [];
      current.push(row);
      rowsByIntake.set(row.intake_code, current);
    }
    const dailyVariantIndexes = new Set(
      data.dailyMetrics.map((day) => day.variant_index),
    );
    const blockVariantIndexes = new Set(
      data.timetableBlocks.map((block) => block.variant_index),
    );

    for (const intake of data.intakes) {
      const intakeRows = rowsByIntake.get(intake.intake_code) ?? [];
      const exportedWeeks = [...new Set(intakeRows.map((row) => row.week_start))]
        .sort();
      const advertisedWeeks = [...intake.week_starts].sort();
      expect(exportedWeeks).toEqual(advertisedWeeks);

      const exportedGroups = [...new Set(intakeRows.map((row) => row.grouping))]
        .sort();
      expect(exportedGroups).toEqual([...intake.groupings].sort());

      for (const weekStart of advertisedWeeks) {
        const weekRows = intakeRows.filter(
          (row) => row.week_start === weekStart,
        );
        expect(weekRows.length).toBeGreaterThan(0);

        for (const grouping of new Set(weekRows.map((row) => row.grouping))) {
          const groupRows = weekRows.filter((row) => row.grouping === grouping);
          const electiveProfiles = groupRows.map((row) => row.elective_profile);
          expect(new Set(electiveProfiles).size).toBe(electiveProfiles.length);

          for (const row of groupRows) {
            expect(row.total_events).toBeGreaterThan(0);
            expect(dailyVariantIndexes.has(row.variant_index)).toBe(true);
            expect(blockVariantIndexes.has(row.variant_index)).toBe(true);
          }
        }
      }
    }
  });

  it("keeps every dashboard filter option reachable and count-accurate", () => {
    for (const week of data.weeks) {
      const baseline = emptyFilters(week.week_start);
      const baselineRows = filterWeeklyMetrics(
        data.weeklyMetrics,
        intakeByCode,
        baseline,
      );
      expect(baselineRows).toHaveLength(week.variant_count);

      const baselineOptions = buildSmartFilterOptions(
        data,
        intakeByCode,
        baseline,
      );
      for (const sourceKey of FILTER_KEYS) {
        expect(baselineOptions[sourceKey][0].value).toBe("");
        expect(optionCount(baselineOptions[sourceKey][0].meta)).toBe(
          baselineRows.length,
        );

        for (const sourceOption of baselineOptions[sourceKey].slice(1)) {
          const sourceFilters = {
            ...baseline,
            [sourceKey]: sourceOption.value,
          } as FilterState;
          const sourceRows = filterWeeklyMetrics(
            data.weeklyMetrics,
            intakeByCode,
            sourceFilters,
          );
          expect(sourceRows.length).toBeGreaterThan(0);
          expect(sourceRows).toHaveLength(optionCount(sourceOption.meta));

          const pairedOptions = buildSmartFilterOptions(
            data,
            intakeByCode,
            sourceFilters,
          );
          for (const targetKey of FILTER_KEYS) {
            if (targetKey === sourceKey) continue;
            for (const targetOption of pairedOptions[targetKey].slice(1)) {
              const pairedRows = filterWeeklyMetrics(
                data.weeklyMetrics,
                intakeByCode,
                {
                  ...sourceFilters,
                  [targetKey]: targetOption.value,
                } as FilterState,
              );
              expect(pairedRows.length).toBeGreaterThan(0);
              expect(pairedRows).toHaveLength(optionCount(targetOption.meta));
            }
          }
        }
      }
    }

    for (const row of data.weeklyMetrics) {
      const intake = intakeByCode.get(row.intake_code);
      expect(intake).toBeDefined();
      const filtered = filterWeeklyMetrics(
        data.weeklyMetrics,
        intakeByCode,
        rowFilters(row, intake!),
      );
      expect(
        filtered.some(
          (candidate) =>
            candidate.variant_index === row.variant_index &&
            candidate.week_start === row.week_start,
        ),
      ).toBe(true);
    }
  });

  it("keeps every checker comparison scope inside its stated boundary", () => {
    const scopes: ComparisonScope[] = ["similar", "level", "all"];

    for (const week of data.weeks) {
      const weekRows = data.weeklyMetrics.filter(
        (row) => row.week_start === week.week_start,
      );
      const activeCodes = new Set(weekRows.map((row) => row.intake_code));

      for (const intakeCode of activeCodes) {
        const selectedIntake = intakeByCode.get(intakeCode);
        expect(selectedIntake).toBeDefined();
        const selectedRows = weekRows.filter(
          (row) => row.intake_code === intakeCode,
        );

        for (const scope of scopes) {
          for (const sameSchool of [false, true]) {
            const peers = filterCheckerComparisonRows(
              weekRows,
              intakeByCode,
              selectedIntake!,
              scope,
              sameSchool,
            );
            if (peers.length === 0) {
              throw new Error(
                `${intakeCode} was excluded from an available checker scope.`,
              );
            }
            const peerVariantIndexes = new Set(
              peers.map((peer) => peer.variant_index),
            );
            for (const selectedRow of selectedRows) {
              if (!peerVariantIndexes.has(selectedRow.variant_index)) {
                throw new Error(
                  `${intakeCode} lost one of its configurations in the checker.`,
                );
              }
            }
            for (const peer of peers) {
              const peerIntake = intakeByCode.get(peer.intake_code)!;
              if (scope === "level" || scope === "similar") {
                if (
                  peerIntake.programme_level !== selectedIntake!.programme_level
                ) {
                  throw new Error(`Programme level escaped the ${scope} scope.`);
                }
              }
              if (scope === "similar") {
                if (
                  peerIntake.academic_level !== selectedIntake!.academic_level
                ) {
                  throw new Error("Academic year escaped the similar scope.");
                }
              }
              if (sameSchool && selectedIntake!.school) {
                if (peerIntake.school !== selectedIntake!.school) {
                  throw new Error("School escaped the same-school refinement.");
                }
              }
            }
          }
        }
      }
    }
  });

  it("maintains valid ranks under every filter and ranking mode", () => {
    const recipes: Array<{ criteria: CriterionKey[]; weights: number[] }> = [
      {
        criteria: data.scoring.default_criterion_order,
        weights: data.scoring.position_weights,
      },
      {
        criteria: [...data.scoring.default_criterion_order].reverse(),
        weights: data.scoring.position_weights,
      },
      { criteria: [...CRITERION_KEYS], weights: CRITERION_KEYS.map(() => 1) },
      ...CRITERION_KEYS.map((criterion) => ({
        criteria: [criterion],
        weights: [1],
      })),
      { criteria: [], weights: [] },
    ];

    for (const week of data.weeks) {
      const baseline = emptyFilters(week.week_start);
      const options = buildSmartFilterOptions(data, intakeByCode, baseline);
      const peerSets = [
        filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, baseline),
        ...FILTER_KEYS.flatMap((key) =>
          options[key].slice(1).map((option) =>
            filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, {
              ...baseline,
              [key]: option.value,
            } as FilterState),
          ),
        ),
      ];

      for (const peers of peerSets) {
        for (const recipe of recipes) {
          expectValidRanks(peers, recipe.criteria, recipe.weights);
        }
      }
    }
  });

  it("identifies the reported intake as a legitimate two-way tie for worst", () => {
    const weekStart = "2026-08-10";
    const weekRows = data.weeklyMetrics.filter(
      (row) => row.week_start === weekStart,
    );
    const ranked = rankVariants(weekRows, ["gap_burden"], [1]);
    const target = ranked.find(
      (row) => row.intake_code === "APU2F2602CS(DF)",
    );
    const twin = ranked.find(
      (row) => row.intake_code === "APD2F2602CS(DF)",
    );

    expect(target).toBeDefined();
    expect(twin).toBeDefined();
    expect(target!.total_gap_minutes).toBe(1275);
    expect(target!.recalculatedScore).toBe(twin!.recalculatedScore);
    expect(target!.recalculatedIsWorst).toBe(true);
    expect(summarizeRankPosition(target!)).toMatchObject({
      betterCount: 1021,
      firstPosition: 1022,
      lastPosition: 1023,
      tiedCount: 2,
      worseCount: 0,
    });

    const weekEnd = "2026-08-16";
    const signature = (variantIndex: number) =>
      data.timetableBlocks
        .filter(
          (block) =>
            block.variant_index === variantIndex &&
            block.event_date >= weekStart &&
            block.event_date <= weekEnd,
        )
        .map((block) =>
          JSON.stringify(
            Object.fromEntries(
              Object.entries(block).filter(([key]) => key !== "variant_index"),
            ),
          ),
        )
        .sort();

    expect(signature(target!.variant_index)).toEqual(
      signature(twin!.variant_index),
    );
  });

  it("contains no duplicate exported comparison rows", () => {
    const keys = data.weeklyMetrics.map((row) =>
      [
        row.week_start,
        row.intake_code,
        row.grouping,
        row.elective_profile,
      ].join("|"),
    );
    expect(new Set(keys).size).toBe(keys.length);
    expect(
      data.weeklyMetrics.every(
        (row) => row.total_events > 0 && intakeByCode.has(row.intake_code),
      ),
    ).toBe(true);
  });
});
