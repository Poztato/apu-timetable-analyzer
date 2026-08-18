import type {
  DailyMetric,
  IntakeMetadata,
  ScoringComponentKey,
  ScoringDefinition,
  TimetableBlock,
  TimePreferenceDefinition,
  TimePreferenceKey,
  WeeklyMetric,
} from "./types";

export const SCORING_COMPONENT_KEYS: ScoringComponentKey[] = [
  "campus_trip",
  "online_commitment",
  "placement",
  "span",
  "waiting",
  "short_day",
  "long_day",
];

export const COMPONENT_DETAILS: Record<
  ScoringComponentKey,
  { label: string; shortLabel: string; description: string }
> = {
  campus_trip: {
    label: "Campus trips",
    shortLabel: "Trips",
    description: "A fixed cost for each day that requires physical attendance.",
  },
  online_commitment: {
    label: "Online-only days",
    shortLabel: "Online",
    description: "A smaller base cost for days that can be attended remotely.",
  },
  placement: {
    label: "Time placement",
    shortLabel: "Placement",
    description: "How far physical teaching sits from your preferred time band.",
  },
  span: {
    label: "Day span",
    shortLabel: "Span",
    description: "How much of the day is claimed from first class to last class.",
  },
  waiting: {
    label: "Campus waiting",
    shortLabel: "Waiting",
    description: "Unoccupied time between physical classes on the same trip.",
  },
  short_day: {
    label: "Short campus days",
    shortLabel: "Short days",
    description: "Smoothly discourages trips with very little physical teaching.",
  },
  long_day: {
    label: "Heavy teaching days",
    shortLabel: "Heavy days",
    description: "Smoothly discourages days with a high total teaching load.",
  },
};

export interface ScoringPreferences {
  timePreference: TimePreferenceKey;
  emphasizeShortDays: boolean;
  emphasizeLongDays: boolean;
}

export interface ComponentScore {
  raw: number;
  averagePenalty: number;
  dailyCap: number;
  contribution: number;
}

export interface RankedVariant extends WeeklyMetric {
  components: Record<ScoringComponentKey, ComponentScore>;
  recalculatedScore: number;
  recalculatedBestRank: number;
  recalculatedWorstRank: number;
  recalculatedMedian: number;
  recalculatedDistanceFromMedian: number;
  recalculatedIsBest: boolean;
  recalculatedIsWorst: boolean;
  recalculatedIsMostAverage: boolean;
  peerCount: number;
}

export interface RankPositionSummary {
  betterCount: number;
  worseCount: number;
  firstPosition: number;
  lastPosition: number;
  tiedCount: number;
  isTied: boolean;
}

export interface FilterState {
  weekStart: string;
  grouping: string;
  programmeLevel: string;
  programmeRoute: string;
  academicLevel: string;
  courseCode: string;
  specialismCode: string;
  school: string;
  studyMode: string;
  deliveryMode: string;
}

export interface ScoringContext {
  dailyByVariant: Map<number, DailyMetric[]>;
  blocksByVariantDate: Map<string, TimetableBlock[]>;
}

type MinuteInterval = [number, number];
type PenaltyKey = "placement" | "span" | "waiting" | "short_day" | "long_day";

function round6(value: number): number {
  return Math.round((value + Number.EPSILON) * 1e6) / 1e6;
}

function contextKey(variantIndex: number, eventDate: string): string {
  return `${variantIndex}|${eventDate}`;
}

export function createScoringContext(
  dailyMetrics: DailyMetric[],
  timetableBlocks: TimetableBlock[],
): ScoringContext {
  const dailyByVariant = new Map<number, DailyMetric[]>();
  for (const day of dailyMetrics) {
    const rows = dailyByVariant.get(day.variant_index) ?? [];
    rows.push(day);
    dailyByVariant.set(day.variant_index, rows);
  }
  for (const rows of dailyByVariant.values()) {
    rows.sort((left, right) => left.event_date.localeCompare(right.event_date));
  }

  const blocksByVariantDate = new Map<string, TimetableBlock[]>();
  for (const block of timetableBlocks) {
    const key = contextKey(block.variant_index, block.event_date);
    const rows = blocksByVariantDate.get(key) ?? [];
    rows.push(block);
    blocksByVariantDate.set(key, rows);
  }
  return { dailyByVariant, blocksByVariantDate };
}

function parseClock(value: string): number {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error(`Invalid scoring time ${value}.`);
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) throw new Error(`Invalid scoring time ${value}.`);
  return hours * 60 + minutes;
}

function timestampMinute(value: string): number {
  const match = /T(\d{2}):(\d{2})(?::\d{2})?/.exec(value);
  if (!match) throw new Error(`Invalid timetable timestamp ${value}.`);
  return Number(match[1]) * 60 + Number(match[2]);
}

function mergeIntervals(intervals: MinuteInterval[]): MinuteInterval[] {
  const ordered = [...intervals].sort(
    (left, right) => left[0] - right[0] || left[1] - right[1],
  );
  const merged: MinuteInterval[] = [];
  for (const [start, end] of ordered) {
    if (start < 0 || end > 24 * 60 || start >= end) {
      throw new Error("A timetable block has an invalid interval.");
    }
    const previous = merged.at(-1);
    if (!previous || start > previous[1]) {
      merged.push([start, end]);
    } else if (end > previous[1]) {
      previous[1] = end;
    }
  }
  return merged;
}

function durationWeightedDeviation(
  intervals: MinuteInterval[],
  preference: TimePreferenceDefinition,
): number {
  const lower = parseClock(preference.start);
  const upper = parseClock(preference.end);
  let duration = 0;
  let distanceArea = 0;
  for (const [start, end] of intervals) {
    duration += end - start;
    const beforeEnd = Math.min(end, lower);
    if (start < beforeEnd) {
      distanceArea +=
        lower * (beforeEnd - start) -
        (beforeEnd ** 2 - start ** 2) / 2;
    }
    const afterStart = Math.max(start, upper);
    if (afterStart < end) {
      distanceArea +=
        (end ** 2 - afterStart ** 2) / 2 -
        upper * (end - afterStart);
    }
  }
  return duration === 0 ? 0 : distanceArea / duration;
}

function smoothRamp(
  value: number,
  ramp: { low: number; high: number; reverse: boolean },
): number {
  if (!Number.isFinite(value) || value < 0 || ramp.high <= ramp.low) {
    throw new Error("The scoring model contains an invalid smooth ramp.");
  }
  const progress = Math.min(1, Math.max(0, (value - ramp.low) / (ramp.high - ramp.low)));
  const smoothed = progress * progress * (3 - 2 * progress);
  return ramp.reverse ? 1 - smoothed : smoothed;
}

function physicalComponentCaps(
  scoring: ScoringDefinition,
  preferences: ScoringPreferences,
): Record<Exclude<ScoringComponentKey, "online_commitment">, number> {
  const trip = scoring.component_weights.campus_trip;
  const raw = {
    placement: scoring.component_weights.placement,
    span: scoring.component_weights.span,
    waiting: scoring.component_weights.waiting,
    short_day:
      scoring.component_weights.short_day +
      (preferences.emphasizeShortDays ? scoring.emphasis_bonus.short_day : 0),
    long_day:
      scoring.component_weights.long_day +
      (preferences.emphasizeLongDays ? scoring.emphasis_bonus.long_day : 0),
  };
  const variableBudget = 100 - trip;
  const rawTotal = Object.values(raw).reduce((sum, value) => sum + value, 0);
  const scale = variableBudget / rawTotal;
  return {
    campus_trip: trip,
    placement: raw.placement * scale,
    span: raw.span * scale,
    waiting: raw.waiting * scale,
    short_day: raw.short_day * scale,
    long_day: raw.long_day * scale,
  };
}

function onlineCaps(
  scoring: ScoringDefinition,
  preferences: ScoringPreferences,
): { span: number; long_day: number } {
  const rawSpan = scoring.online_day.span_points;
  const rawLoad =
    scoring.online_day.load_points +
    (preferences.emphasizeLongDays
      ? (scoring.online_day.load_points * scoring.emphasis_bonus.long_day) /
        scoring.component_weights.long_day
      : 0);
  const budget = scoring.online_day.span_points + scoring.online_day.load_points;
  const scale = budget / (rawSpan + rawLoad);
  return { span: rawSpan * scale, long_day: rawLoad * scale };
}

function preferenceByKey(
  scoring: ScoringDefinition,
  key: TimePreferenceKey,
): TimePreferenceDefinition {
  const preference = scoring.time_preferences.find((item) => item.key === key);
  if (!preference) throw new Error(`Unknown time preference ${key}.`);
  return preference;
}

function placementForDay(
  day: DailyMetric,
  preference: TimePreferenceDefinition,
  context: ScoringContext,
): number {
  if (day.day_type !== "physical") return 0;
  const blocks = context.blocksByVariantDate.get(
    contextKey(day.variant_index, day.event_date),
  );
  if (!blocks) throw new Error("A physical day has no timetable blocks.");
  const intervals = mergeIntervals(
    blocks
      .filter((block) => block.delivery_mode !== "online")
      .map((block) => [
        timestampMinute(block.start_at),
        timestampMinute(block.end_at),
      ] as MinuteInterval),
  );
  if (intervals.length === 0) {
    throw new Error("A physical day has no physical teaching intervals.");
  }
  return durationWeightedDeviation(intervals, preference);
}

function emptyComponents(): Record<ScoringComponentKey, number> {
  return {
    campus_trip: 0,
    online_commitment: 0,
    placement: 0,
    span: 0,
    waiting: 0,
    short_day: 0,
    long_day: 0,
  };
}

function scoreVariant(
  row: WeeklyMetric,
  scoring: ScoringDefinition,
  preferences: ScoringPreferences,
  context: ScoringContext,
): { components: Record<ScoringComponentKey, ComponentScore>; score: number } {
  const days = context.dailyByVariant.get(row.variant_index) ?? [];
  if (days.length !== row.active_days) {
    throw new Error("Weekly and daily timetable records do not match.");
  }
  const preference = preferenceByKey(scoring, preferences.timePreference);
  const physicalCaps = physicalComponentCaps(scoring, preferences);
  const remoteCaps = onlineCaps(scoring, preferences);
  const contributions = emptyComponents();
  const penaltySums = emptyComponents();
  let placementDistanceArea = 0;
  let scoredSpanMinutes = 0;
  let shortCampusDayCount = 0;
  let heavyTeachingDayCount = 0;
  let dailyScoreTotal = 0;

  for (const day of days) {
    const longPenalty = smoothRamp(day.teaching_minutes, scoring.ramps.long_day);
    const dayComponents = emptyComponents();
    if (day.day_type === "online") {
      const spanPenalty = smoothRamp(day.span_minutes, scoring.ramps.span);
      scoredSpanMinutes += day.span_minutes;
      if (longPenalty > 0) heavyTeachingDayCount += 1;
      dayComponents.online_commitment = scoring.online_day.base_points;
      dayComponents.span = remoteCaps.span * spanPenalty;
      dayComponents.long_day = remoteCaps.long_day * longPenalty;
      penaltySums.online_commitment += 1;
      penaltySums.span += spanPenalty;
      penaltySums.long_day += longPenalty;
      for (const key of SCORING_COMPONENT_KEYS) {
        contributions[key] += round6(dayComponents[key]);
      }
      dailyScoreTotal += round6(
        SCORING_COMPONENT_KEYS.reduce(
          (sum, key) => sum + dayComponents[key],
          0,
        ),
      );
      continue;
    }

    const placementDeviation = placementForDay(day, preference, context);
    placementDistanceArea += placementDeviation * day.physical_teaching_minutes;
    scoredSpanMinutes += day.physical_span_minutes;
    const penalties: Record<PenaltyKey, number> = {
      placement: smoothRamp(placementDeviation, scoring.ramps.placement),
      span: smoothRamp(day.physical_span_minutes, scoring.ramps.span),
      waiting: smoothRamp(day.campus_waiting_minutes, scoring.ramps.waiting),
      short_day: smoothRamp(
        day.physical_teaching_minutes,
        scoring.ramps.short_day,
      ),
      long_day: longPenalty,
    };
    if (penalties.short_day > 0) shortCampusDayCount += 1;
    if (penalties.long_day > 0) heavyTeachingDayCount += 1;
    dayComponents.campus_trip = physicalCaps.campus_trip;
    penaltySums.campus_trip += 1;
    for (const key of Object.keys(penalties) as PenaltyKey[]) {
      dayComponents[key] = physicalCaps[key] * penalties[key];
      penaltySums[key] += penalties[key];
    }
    for (const key of SCORING_COMPONENT_KEYS) {
      contributions[key] += round6(dayComponents[key]);
    }
    dailyScoreTotal += round6(
      SCORING_COMPONENT_KEYS.reduce(
        (sum, key) => sum + dayComponents[key],
        0,
      ),
    );
  }

  const divisor = scoring.weekly_divisor_days;
  if (divisor !== 7 || days.length > divisor) {
    throw new Error("The scoring model must average across all seven days.");
  }
  const rawValues: Record<ScoringComponentKey, number> = {
    campus_trip: row.physical_days,
    online_commitment: row.online_only_days,
    placement:
      row.total_physical_teaching_minutes > 0
        ? placementDistanceArea / row.total_physical_teaching_minutes
        : 0,
    span: scoredSpanMinutes,
    waiting: row.total_campus_waiting_minutes,
    short_day: shortCampusDayCount,
    long_day: heavyTeachingDayCount,
  };
  const dailyCaps: Record<ScoringComponentKey, number> = {
    campus_trip: physicalCaps.campus_trip,
    online_commitment: scoring.online_day.base_points,
    placement: physicalCaps.placement,
    span: row.physical_days > 0 ? physicalCaps.span : remoteCaps.span,
    waiting: physicalCaps.waiting,
    short_day: physicalCaps.short_day,
    long_day:
      row.physical_days > 0 ? physicalCaps.long_day : remoteCaps.long_day,
  };
  const components = Object.fromEntries(
    SCORING_COMPONENT_KEYS.map((key) => [
      key,
      {
        raw: round6(rawValues[key]),
        averagePenalty: round6(penaltySums[key] / divisor),
        dailyCap: round6(dailyCaps[key]),
        contribution: round6(contributions[key] / divisor),
      },
    ]),
  ) as Record<ScoringComponentKey, ComponentScore>;
  return {
    components,
    score: round6(dailyScoreTotal / divisor),
  };
}

function ranksWithTies(values: number[], ascending: boolean): number[] {
  const frequencies = new Map<number, number>();
  for (const value of values) {
    frequencies.set(value, (frequencies.get(value) ?? 0) + 1);
  }
  const ordered = [...frequencies.keys()].sort((left, right) =>
    ascending ? left - right : right - left,
  );
  const rankByValue = new Map<number, number>();
  let preceding = 0;
  for (const value of ordered) {
    rankByValue.set(value, preceding + 1);
    preceding += frequencies.get(value) ?? 0;
  }
  return values.map((value) => rankByValue.get(value) ?? 1);
}

function median(values: number[]): number {
  const ordered = [...values].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1
    ? ordered[middle]
    : (ordered[middle - 1] + ordered[middle]) / 2;
}

export function rankVariants(
  rows: WeeklyMetric[],
  scoring: ScoringDefinition,
  preferences: ScoringPreferences,
  context: ScoringContext,
): RankedVariant[] {
  if (rows.length === 0) return [];
  const scored = rows.map((row) => scoreVariant(row, scoring, preferences, context));
  const scores = scored.map((item) => item.score);
  const rawMedianScore = median(scores);
  const medianScore = round6(rawMedianScore);
  const distances = scores.map((score) => round6(Math.abs(score - rawMedianScore)));
  const minimumDistance = Math.min(...distances);
  const minimumScore = Math.min(...scores);
  const maximumScore = Math.max(...scores);
  const bestRanks = ranksWithTies(scores, true);
  const worstRanks = ranksWithTies(scores, false);

  return rows.map((row, index) => ({
    ...row,
    components: scored[index].components,
    recalculatedScore: scores[index],
    recalculatedBestRank: bestRanks[index],
    recalculatedWorstRank: worstRanks[index],
    recalculatedMedian: medianScore,
    recalculatedDistanceFromMedian: distances[index],
    recalculatedIsBest: scores[index] === minimumScore,
    recalculatedIsWorst: scores[index] === maximumScore,
    recalculatedIsMostAverage: distances[index] === minimumDistance,
    peerCount: rows.length,
  }));
}

export function strongestComponent(row: RankedVariant): ScoringComponentKey {
  const variableComponents: ScoringComponentKey[] = [
    "placement",
    "span",
    "waiting",
    "short_day",
    "long_day",
  ];
  const variableLeader = variableComponents.sort(
    (left, right) =>
      row.components[right].contribution - row.components[left].contribution,
  )[0];
  if (row.components[variableLeader].contribution > 0) return variableLeader;
  return row.components.campus_trip.contribution >=
    row.components.online_commitment.contribution
    ? "campus_trip"
    : "online_commitment";
}

export function summarizeRankPosition(
  row: Pick<
    RankedVariant,
    "peerCount" | "recalculatedBestRank" | "recalculatedWorstRank"
  >,
): RankPositionSummary {
  const firstPosition = row.recalculatedBestRank;
  const lastPosition = row.peerCount - row.recalculatedWorstRank + 1;
  const tiedCount = lastPosition - firstPosition + 1;
  if (
    row.peerCount < 1 ||
    firstPosition < 1 ||
    lastPosition < firstPosition ||
    lastPosition > row.peerCount
  ) {
    throw new Error("The ranked timetable position is invalid.");
  }
  return {
    betterCount: firstPosition - 1,
    worseCount: row.recalculatedWorstRank - 1,
    firstPosition,
    lastPosition,
    tiedCount,
    isTied: tiedCount > 1,
  };
}

function matchesDeliveryMode(row: WeeklyMetric, mode: string): boolean {
  if (mode === "campus") return row.total_campus_events > 0;
  if (mode === "online") return row.total_online_events > 0;
  if (mode === "unknown") return row.total_unknown_events > 0;
  return true;
}

export function filterWeeklyMetrics(
  rows: WeeklyMetric[],
  intakeByCode: Map<string, IntakeMetadata>,
  filters: FilterState,
): WeeklyMetric[] {
  return rows.filter((row) => {
    if (row.week_start !== filters.weekStart) return false;
    if (filters.grouping && row.grouping !== filters.grouping) return false;
    if (filters.deliveryMode && !matchesDeliveryMode(row, filters.deliveryMode)) {
      return false;
    }
    const intake = intakeByCode.get(row.intake_code);
    if (!intake) return false;
    if (filters.programmeLevel && intake.programme_level !== filters.programmeLevel) return false;
    if (filters.programmeRoute && intake.programme_route !== filters.programmeRoute) return false;
    if (filters.academicLevel && intake.academic_level !== Number(filters.academicLevel)) return false;
    if (filters.courseCode && intake.course_code !== filters.courseCode) return false;
    if (filters.specialismCode && intake.specialism_code !== filters.specialismCode) return false;
    if (filters.school && intake.school !== filters.school) return false;
    if (filters.studyMode && intake.study_mode !== filters.studyMode) return false;
    return true;
  });
}
