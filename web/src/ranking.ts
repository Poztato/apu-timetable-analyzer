import type {
  CriterionKey,
  IntakeMetadata,
  WeeklyMetric,
} from "./types";

export const CRITERION_KEYS: CriterionKey[] = [
  "gap_burden",
  "late_only",
  "early_only",
  "one_hour_only",
  "overloaded",
];

export const CRITERION_DETAILS: Record<
  CriterionKey,
  { label: string; shortLabel: string; rawField: keyof WeeklyMetric; unit: string }
> = {
  gap_burden: {
    label: "Gap burden",
    shortLabel: "Gaps",
    rawField: "total_gap_minutes",
    unit: "minutes",
  },
  late_only: {
    label: "Late-only campus days",
    shortLabel: "Late-only",
    rawField: "late_only_days",
    unit: "days",
  },
  early_only: {
    label: "Early-only campus days",
    shortLabel: "Early-only",
    rawField: "early_only_days",
    unit: "days",
  },
  one_hour_only: {
    label: "One-hour-only campus days",
    shortLabel: "One-hour-only",
    rawField: "one_hour_only_days",
    unit: "days",
  },
  overloaded: {
    label: "Overloaded days",
    shortLabel: "Overloaded",
    rawField: "overloaded_days",
    unit: "days",
  },
};

export interface ComponentScore {
  raw: number;
  percentile: number;
  weight: number;
  contribution: number;
}

export interface RankedVariant extends WeeklyMetric {
  components: Record<CriterionKey, ComponentScore>;
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

function round10(value: number): number {
  return Math.round((value + Number.EPSILON) * 1e10) / 1e10;
}

export function strictLowerPercentiles(values: number[]): number[] {
  if (values.length <= 1 || new Set(values).size <= 1) {
    return values.map(() => 0);
  }

  const frequencies = new Map<number, number>();
  for (const value of values) {
    frequencies.set(value, (frequencies.get(value) ?? 0) + 1);
  }
  const percentileByValue = new Map<number, number>();
  let lowerCount = 0;
  for (const value of [...frequencies.keys()].sort((a, b) => a - b)) {
    percentileByValue.set(
      value,
      round10((lowerCount / (values.length - 1)) * 100),
    );
    lowerCount += frequencies.get(value) ?? 0;
  }
  return values.map((value) => percentileByValue.get(value) ?? 0);
}

function ranksWithTies(values: number[], ascending: boolean): number[] {
  const frequencies = new Map<number, number>();
  for (const value of values) {
    frequencies.set(value, (frequencies.get(value) ?? 0) + 1);
  }
  const ordered = [...frequencies.keys()].sort((a, b) =>
    ascending ? a - b : b - a,
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
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  if (ordered.length % 2 === 1) {
    return ordered[middle];
  }
  return (ordered[middle - 1] + ordered[middle]) / 2;
}

function criterionRawValue(row: WeeklyMetric, criterion: CriterionKey): number {
  const value = row[CRITERION_DETAILS[criterion].rawField];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`Invalid raw value for ${criterion}.`);
  }
  return value;
}

export function rankVariants(
  rows: WeeklyMetric[],
  criterionOrder: CriterionKey[],
  positionWeights: number[],
): RankedVariant[] {
  if (rows.length === 0) {
    return [];
  }
  if (
    criterionOrder.length > CRITERION_KEYS.length ||
    new Set(criterionOrder).size !== criterionOrder.length ||
    criterionOrder.some((criterion) => !CRITERION_KEYS.includes(criterion))
  ) {
    throw new Error("The frustration criterion order is invalid.");
  }
  if (
    positionWeights.length !== criterionOrder.length ||
    positionWeights.some((weight) => !Number.isFinite(weight) || weight <= 0)
  ) {
    throw new Error("The ranking position weights are invalid.");
  }

  const weightTotal = positionWeights.reduce((total, weight) => total + weight, 0);
  const normalizedWeights = positionWeights.map((weight) => weight / weightTotal);
  const componentsByRow = rows.map(
    () => ({}) as Record<CriterionKey, ComponentScore>,
  );

  CRITERION_KEYS.forEach((criterion) => {
    const rawValues = rows.map((row) => criterionRawValue(row, criterion));
    const percentiles = strictLowerPercentiles(rawValues);
    const activeIndex = criterionOrder.indexOf(criterion);
    rows.forEach((_, rowIndex) => {
      const weight = activeIndex >= 0 ? normalizedWeights[activeIndex] : 0;
      componentsByRow[rowIndex][criterion] = {
        raw: rawValues[rowIndex],
        percentile: percentiles[rowIndex],
        weight,
        contribution: round10(percentiles[rowIndex] * weight),
      };
    });
  });

  const scores = componentsByRow.map((components) =>
    round10(
      criterionOrder.reduce(
        (total, criterion) => total + components[criterion].contribution,
        0,
      ),
    ),
  );
  const medianScore = round10(median(scores));
  const distances = scores.map((score) => round10(Math.abs(score - medianScore)));
  const minimumDistance = Math.min(...distances);
  const minimumScore = Math.min(...scores);
  const maximumScore = Math.max(...scores);
  const bestRanks = ranksWithTies(scores, true);
  const worstRanks = ranksWithTies(scores, false);

  return rows.map((row, index) => ({
    ...row,
    components: componentsByRow[index],
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
  if (mode === "campus") {
    return row.total_campus_events > 0;
  }
  if (mode === "online") {
    return row.total_online_events > 0;
  }
  if (mode === "unknown") {
    return row.total_unknown_events > 0;
  }
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
    if (
      filters.programmeLevel &&
      intake.programme_level !== filters.programmeLevel
    ) {
      return false;
    }
    if (
      filters.programmeRoute &&
      intake.programme_route !== filters.programmeRoute
    ) {
      return false;
    }
    if (
      filters.academicLevel &&
      intake.academic_level !== Number(filters.academicLevel)
    ) {
      return false;
    }
    if (filters.courseCode && intake.course_code !== filters.courseCode) {
      return false;
    }
    if (
      filters.specialismCode &&
      intake.specialism_code !== filters.specialismCode
    ) {
      return false;
    }
    if (filters.school && intake.school !== filters.school) return false;
    if (filters.studyMode && intake.study_mode !== filters.studyMode) return false;
    return true;
  });
}
