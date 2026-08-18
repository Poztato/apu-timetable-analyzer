import { describe, expect, it } from "vitest";

import {
  createScoringContext,
  rankVariants,
  strongestComponent,
  summarizeRankPosition,
} from "./ranking";
import type {
  DailyMetric,
  ScoringDefinition,
  TimetableBlock,
  WeeklyMetric,
} from "./types";

const scoring: ScoringDefinition = {
  model_version: "daily_convenience_v1",
  weekly_divisor_days: 7,
  default_time_preference: "balanced",
  time_preferences: [
    {
      key: "balanced",
      label: "Balanced midday",
      short_label: "Midday",
      start: "11:00",
      end: "13:30",
      description: "Midday",
    },
    {
      key: "morning",
      label: "Prefer mornings",
      short_label: "Morning",
      start: "09:00",
      end: "11:30",
      description: "Morning",
    },
    {
      key: "afternoon",
      label: "Prefer afternoons",
      short_label: "Afternoon",
      start: "13:30",
      end: "16:00",
      description: "Afternoon",
    },
  ],
  component_weights: {
    campus_trip: 20,
    placement: 30,
    span: 20,
    waiting: 10,
    short_day: 10,
    long_day: 10,
  },
  emphasis_bonus: { short_day: 5, long_day: 5 },
  online_day: { base_points: 5, span_points: 7, load_points: 7 },
  ramps: {
    placement: { low: 0, high: 240, reverse: false },
    span: { low: 180, high: 540, reverse: false },
    waiting: { low: 0, high: 180, reverse: false },
    short_day: { low: 60, high: 120, reverse: true },
    long_day: { low: 240, high: 360, reverse: false },
  },
  profile_id: "test",
  score_method: "absolute_daily_cost_v1",
  physical_day_minimum: 20,
  online_day_maximum: 19,
};

function weekly(
  variantIndex: number,
  overrides: Partial<WeeklyMetric> = {},
): WeeklyMetric {
  return {
    variant_index: variantIndex,
    week_start: "2026-08-10",
    intake_code: `TEST-${variantIndex}`,
    grouping: "G1",
    elective_profile: "none",
    elective_profile_name: "None",
    elective_status: "not_active",
    elective_rule_id: "test",
    active_days: 1,
    empty_days: 6,
    physical_days: 1,
    online_only_days: 0,
    weekend_days: 0,
    total_event_records: 1,
    total_events: 1,
    total_merged_blocks: 1,
    total_teaching_minutes: 180,
    total_physical_teaching_minutes: 180,
    total_span_minutes: 180,
    total_physical_span_minutes: 180,
    total_campus_waiting_minutes: 0,
    longest_campus_wait_minutes: 0,
    days_with_campus_waiting: 0,
    average_placement_deviation_minutes: 0,
    days_with_exact_overlaps: 0,
    days_with_overlaps: 0,
    exact_overlap_pair_count: 0,
    overlap_pair_count: 0,
    total_physical_events: 1,
    total_campus_events: 1,
    total_online_events: 0,
    total_unknown_events: 0,
    earliest_start: "11:00",
    latest_end: "14:00",
    maximum_daily_span: 180,
    maximum_physical_span: 180,
    maximum_daily_teaching_minutes: 180,
    maximum_physical_teaching_minutes: 180,
    campus_trip_score: 0,
    online_commitment_score: 0,
    placement_score: 0,
    span_score: 0,
    waiting_score: 0,
    short_day_score: 0,
    long_day_score: 0,
    balanced_score: 0,
    overall_score: 0,
    comparison_set_size: 0,
    comparison_median_score: 0,
    distance_from_median: 0,
    best_rank: 0,
    worst_rank: 0,
    is_best: false,
    is_worst: false,
    is_most_average: false,
    ...overrides,
  };
}

function day(
  variantIndex: number,
  overrides: Partial<DailyMetric> = {},
): DailyMetric {
  return {
    variant_index: variantIndex,
    event_date: "2026-08-10",
    day_of_week: "MON",
    is_weekend: false,
    event_record_count: 1,
    event_count: 1,
    merged_block_count: 1,
    teaching_minutes: 180,
    physical_teaching_minutes: 180,
    first_class_start: "2026-08-10T11:00:00+08:00",
    last_class_end: "2026-08-10T14:00:00+08:00",
    span_minutes: 180,
    first_physical_start: "2026-08-10T11:00:00+08:00",
    last_physical_end: "2026-08-10T14:00:00+08:00",
    physical_span_minutes: 180,
    campus_waiting_minutes: 0,
    longest_campus_wait_minutes: 0,
    placement_deviation_minutes: 0,
    exact_overlap_pair_count: 0,
    overlap_pair_count: 0,
    physical_event_count: 1,
    campus_event_count: 1,
    online_event_count: 0,
    unknown_event_count: 0,
    day_type: "physical",
    placement_penalty: 0,
    span_penalty: 0,
    waiting_penalty: 0,
    short_day_penalty: 0,
    long_day_penalty: 0,
    campus_trip_score: 20,
    online_commitment_score: 0,
    placement_score: 0,
    span_score: 0,
    waiting_score: 0,
    short_day_score: 0,
    long_day_score: 0,
    balanced_day_score: 20,
    ...overrides,
  };
}

function block(
  variantIndex: number,
  start: string,
  end: string,
  deliveryMode: TimetableBlock["delivery_mode"] = "campus",
): TimetableBlock {
  return {
    variant_index: variantIndex,
    event_date: start.slice(0, 10),
    start_at: start,
    end_at: end,
    duration_minutes: 60,
    module_id: `M-${variantIndex}-${start}`,
    module_name: null,
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

const balanced = {
  timePreference: "balanced" as const,
  emphasizeShortDays: false,
  emphasizeLongDays: false,
};

describe("absolute convenience ranking", () => {
  it("punishes edge placement, a wide span, and campus waiting without peer percentiles", () => {
    const rows = [
      weekly(0),
      weekly(1, {
        total_events: 2,
        total_merged_blocks: 2,
        total_teaching_minutes: 120,
        total_physical_teaching_minutes: 120,
        total_span_minutes: 570,
        total_physical_span_minutes: 570,
        total_campus_waiting_minutes: 450,
        longest_campus_wait_minutes: 450,
        days_with_campus_waiting: 1,
        maximum_daily_span: 570,
        maximum_physical_span: 570,
        maximum_daily_teaching_minutes: 120,
        maximum_physical_teaching_minutes: 120,
      }),
    ];
    const days = [
      day(0),
      day(1, {
        event_count: 2,
        merged_block_count: 2,
        teaching_minutes: 120,
        physical_teaching_minutes: 120,
        first_class_start: "2026-08-10T08:30:00+08:00",
        last_class_end: "2026-08-10T18:00:00+08:00",
        span_minutes: 570,
        first_physical_start: "2026-08-10T08:30:00+08:00",
        last_physical_end: "2026-08-10T18:00:00+08:00",
        physical_span_minutes: 570,
        campus_waiting_minutes: 450,
        longest_campus_wait_minutes: 450,
        physical_event_count: 2,
        campus_event_count: 2,
      }),
    ];
    const blocks = [
      block(0, "2026-08-10T11:00:00+08:00", "2026-08-10T14:00:00+08:00"),
      block(1, "2026-08-10T08:30:00+08:00", "2026-08-10T09:30:00+08:00"),
      block(1, "2026-08-10T17:00:00+08:00", "2026-08-10T18:00:00+08:00"),
    ];
    const context = createScoringContext(days, blocks);
    const ranked = rankVariants(rows, scoring, balanced, context);

    expect(ranked[0].recalculatedBestRank).toBe(1);
    expect(ranked[1].recalculatedBestRank).toBe(2);
    expect(ranked[1].components.placement.raw).toBeGreaterThan(170);
    expect(ranked[1].components.span.raw).toBe(570);
    expect(ranked[1].components.short_day.raw).toBe(0);
    expect(ranked[1].components.span.contribution).toBeGreaterThan(0);
    expect(ranked[1].components.waiting.contribution).toBeGreaterThan(0);
    expect(strongestComponent(ranked[1])).not.toBe("campus_trip");

    const alone = rankVariants([rows[1]], scoring, balanced, context)[0];
    expect(alone.recalculatedScore).toBe(ranked[1].recalculatedScore);
    expect(alone.recalculatedBestRank).toBe(1);
  });

  it("moves only the preferred time band", () => {
    const rows = [weekly(0), weekly(1)];
    const days = [day(0), day(1)];
    const blocks = [
      block(0, "2026-08-10T09:00:00+08:00", "2026-08-10T11:00:00+08:00"),
      block(1, "2026-08-10T14:00:00+08:00", "2026-08-10T16:00:00+08:00"),
    ];
    const context = createScoringContext(days, blocks);
    const morning = rankVariants(
      rows,
      scoring,
      { ...balanced, timePreference: "morning" },
      context,
    );
    const afternoon = rankVariants(
      rows,
      scoring,
      { ...balanced, timePreference: "afternoon" },
      context,
    );

    expect(morning[0].recalculatedScore).toBeLessThan(morning[1].recalculatedScore);
    expect(afternoon[1].recalculatedScore).toBeLessThan(afternoon[0].recalculatedScore);
    expect(morning[0].components.campus_trip.dailyCap).toBe(20);
    expect(afternoon[0].components.campus_trip.dailyCap).toBe(20);
  });

  it("keeps empty and online-only days better than a physical trip", () => {
    const rows = [
      weekly(0, {
        active_days: 0,
        empty_days: 7,
        physical_days: 0,
        total_events: 0,
        total_teaching_minutes: 0,
        total_physical_teaching_minutes: 0,
        total_span_minutes: 0,
        total_physical_span_minutes: 0,
        total_physical_events: 0,
        total_campus_events: 0,
      }),
      weekly(1, {
        physical_days: 0,
        online_only_days: 1,
        total_teaching_minutes: 60,
        total_physical_teaching_minutes: 0,
        total_span_minutes: 60,
        total_physical_span_minutes: 0,
        total_physical_events: 0,
        total_campus_events: 0,
        total_online_events: 1,
      }),
      weekly(2),
    ];
    const days = [
      day(1, {
        teaching_minutes: 60,
        physical_teaching_minutes: 0,
        first_class_start: "2026-08-10T12:00:00+08:00",
        last_class_end: "2026-08-10T13:00:00+08:00",
        span_minutes: 60,
        first_physical_start: null,
        last_physical_end: null,
        physical_span_minutes: 0,
        physical_event_count: 0,
        campus_event_count: 0,
        online_event_count: 1,
        day_type: "online",
      }),
      day(2),
    ];
    const blocks = [
      block(1, "2026-08-10T12:00:00+08:00", "2026-08-10T13:00:00+08:00", "online"),
      block(2, "2026-08-10T11:00:00+08:00", "2026-08-10T14:00:00+08:00"),
    ];
    const ranked = rankVariants(
      rows,
      scoring,
      balanced,
      createScoringContext(days, blocks),
    );

    expect(ranked.map((row) => row.recalculatedScore)).toEqual([
      0,
      0.714286,
      expect.any(Number),
    ]);
    expect(ranked[2].recalculatedScore).toBeGreaterThan(ranked[1].recalculatedScore);
  });

  it("strengthens short and heavy day curves without lifting the 100 point ceiling", () => {
    const shortRow = weekly(0, {
      total_teaching_minutes: 60,
      total_physical_teaching_minutes: 60,
      total_span_minutes: 60,
      total_physical_span_minutes: 60,
      maximum_daily_span: 60,
      maximum_physical_span: 60,
      maximum_daily_teaching_minutes: 60,
      maximum_physical_teaching_minutes: 60,
    });
    const shortDay = day(0, {
      teaching_minutes: 60,
      physical_teaching_minutes: 60,
      last_class_end: "2026-08-10T12:00:00+08:00",
      span_minutes: 60,
      last_physical_end: "2026-08-10T12:00:00+08:00",
      physical_span_minutes: 60,
    });
    const context = createScoringContext(
      [shortDay],
      [block(0, "2026-08-10T11:00:00+08:00", "2026-08-10T12:00:00+08:00")],
    );
    const normal = rankVariants([shortRow], scoring, balanced, context)[0];
    const emphasized = rankVariants(
      [shortRow],
      scoring,
      { ...balanced, emphasizeShortDays: true },
      context,
    )[0];

    expect(emphasized.components.short_day.dailyCap).toBeGreaterThan(
      normal.components.short_day.dailyCap,
    );
    expect(emphasized.components.short_day.raw).toBe(1);
    expect(emphasized.recalculatedScore).toBeGreaterThan(normal.recalculatedScore);
    expect(
      Object.values(emphasized.components).reduce(
        (sum, component) => sum + component.dailyCap,
        0,
      ),
    ).toBeLessThanOrEqual(105);
  });

  it("summarizes tied positions using both rank directions", () => {
    const rows = [weekly(0), weekly(1)];
    const days = [day(0), day(1)];
    const blocks = [
      block(0, "2026-08-10T11:00:00+08:00", "2026-08-10T14:00:00+08:00"),
      block(1, "2026-08-10T11:00:00+08:00", "2026-08-10T14:00:00+08:00"),
    ];
    const ranked = rankVariants(
      rows,
      scoring,
      balanced,
      createScoringContext(days, blocks),
    );
    expect(summarizeRankPosition(ranked[0])).toMatchObject({
      firstPosition: 1,
      lastPosition: 2,
      tiedCount: 2,
      isTied: true,
    });
  });
});
