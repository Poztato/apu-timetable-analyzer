import { describe, expect, it } from "vitest";

import {
  CRITERION_KEYS,
  filterWeeklyMetrics,
  rankVariants,
  strictLowerPercentiles,
} from "./ranking";
import type { FilterState } from "./ranking";
import type { IntakeMetadata, WeeklyMetric } from "./types";

function weeklyMetric(
  intakeCode: string,
  gap: number,
  overloaded = 0,
): WeeklyMetric {
  return {
    variant_index: gap + overloaded,
    week_start: "2026-08-10",
    intake_code: intakeCode,
    grouping: "G1",
    active_days: 1,
    campus_days: 1,
    online_only_days: 0,
    weekend_days: 0,
    total_event_records: 1,
    total_events: 1,
    total_merged_blocks: 1,
    total_teaching_minutes: 60,
    total_gap_minutes: gap,
    longest_gap_minutes: gap,
    days_with_gaps: Number(gap > 0),
    days_with_exact_overlaps: 0,
    days_with_overlaps: 0,
    exact_overlap_pair_count: 0,
    overlap_pair_count: 0,
    total_campus_events: 1,
    total_online_events: 0,
    total_unknown_events: 0,
    early_only_days: 0,
    late_only_days: 0,
    one_hour_only_days: 0,
    overloaded_days: overloaded,
    earliest_start: "10:00",
    latest_end: "11:00",
    maximum_daily_span: 60,
    maximum_daily_teaching_minutes: 60,
    overall_frustration: 0,
    comparison_set_size: 0,
    comparison_median_score: 0,
    distance_from_median: 0,
    best_rank: 0,
    worst_rank: 0,
    is_best: false,
    is_worst: false,
    is_most_average: false,
  };
}

function intake(code: string, course: string): IntakeMetadata {
  return {
    intake_code: code,
    programme_route: "APU",
    programme_route_name: "APU programme",
    academic_level: 1,
    intake_year: 2026,
    intake_month: 5,
    course_code: course,
    course_name: null,
    specialism_code: null,
    specialism_name: null,
    school: null,
    study_mode: null,
    parse_status: "parsed",
    parser_family: "degree",
    week_starts: ["2026-08-10"],
    groupings: ["G1"],
  };
}

describe("strictLowerPercentiles", () => {
  it("uses endpoint scaling and preserves tied minima", () => {
    expect(strictLowerPercentiles([0, 0, 100])).toEqual([0, 0, 100]);
    expect(strictLowerPercentiles([0, 50, 100])).toEqual([0, 50, 100]);
  });

  it("treats a constant criterion as neutral", () => {
    expect(strictLowerPercentiles([10, 10])).toEqual([0, 0]);
  });
});

describe("rankVariants", () => {
  it("reproduces best, worst, and average ranking behavior", () => {
    const ranked = rankVariants(
      [weeklyMetric("LOW", 0), weeklyMetric("MID", 50), weeklyMetric("HIGH", 100)],
      CRITERION_KEYS,
      [5, 4, 3, 2, 1],
    );

    expect(ranked[0].recalculatedIsBest).toBe(true);
    expect(ranked[1].recalculatedIsMostAverage).toBe(true);
    expect(ranked[2].recalculatedIsWorst).toBe(true);
    expect(ranked[1].components.gap_burden.percentile).toBe(50);
  });

  it("changes the result when priorities are reversed", () => {
    const rows = [
      weeklyMetric("GAP", 100, 0),
      weeklyMetric("LOAD", 0, 1),
    ];
    const defaultRanked = rankVariants(rows, CRITERION_KEYS, [5, 4, 3, 2, 1]);
    const reversedRanked = rankVariants(
      rows,
      [...CRITERION_KEYS].reverse(),
      [5, 4, 3, 2, 1],
    );

    expect(defaultRanked[0].recalculatedIsWorst).toBe(true);
    expect(reversedRanked[1].recalculatedIsWorst).toBe(true);
  });
});

describe("filterWeeklyMetrics", () => {
  it("filters the peer set by structured metadata", () => {
    const rows = [weeklyMetric("CS", 0), weeklyMetric("IT", 10)];
    const metadata = new Map([
      ["CS", intake("CS", "CS")],
      ["IT", intake("IT", "IT")],
    ]);
    const filters: FilterState = {
      weekStart: "2026-08-10",
      grouping: "",
      programmeRoute: "",
      academicLevel: "",
      courseCode: "CS",
      specialismCode: "",
      school: "",
      studyMode: "",
      deliveryMode: "",
    };

    expect(filterWeeklyMetrics(rows, metadata, filters).map((row) => row.intake_code)).toEqual([
      "CS",
    ]);
  });
});
