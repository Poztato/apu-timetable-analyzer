import { useEffect, useMemo, useState } from "react";

import {
  DashboardSelect,
  type DashboardOption,
} from "./DashboardSelect";
import {
  CRITERION_DETAILS,
  CRITERION_KEYS,
  filterWeeklyMetrics,
  rankVariants,
  type FilterState,
  type RankedVariant,
} from "./ranking";
import { rankIntakeMatches } from "./CampusNotebook";
import { VerticalTimetable } from "./VerticalTimetable";
import type {
  CodeNameOption,
  CriterionKey,
  DashboardData,
  IntakeMetadata,
  WeeklyMetric,
} from "./types";

const PAGE_SIZE = 15;

type DashboardView = "rankings" | "inspect" | "compare";
type SortKey =
  | "rank"
  | "intake"
  | "score"
  | "gap"
  | "campusDays"
  | "teaching";
type SortDirection = "ascending" | "descending";
type NonWeekFilterKey = Exclude<keyof FilterState, "weekStart">;

const FILTER_LABELS: Record<NonWeekFilterKey, string> = {
  grouping: "Group",
  programmeLevel: "Programme level",
  programmeRoute: "Programme route",
  academicLevel: "Year",
  courseCode: "Course",
  specialismCode: "Specialism",
  school: "School",
  studyMode: "Study mode",
  deliveryMode: "Delivery mode",
};

const FILTER_KEYS = Object.keys(FILTER_LABELS) as NonWeekFilterKey[];

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-MY").format(value);
}

function formatScore(value: number): string {
  return value.toFixed(2);
}

function formatMinutes(value: number): string {
  if (value < 60) return `${value} min`;
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return minutes === 0 ? `${hours} hr` : `${hours} hr ${minutes} min`;
}

function formatDate(value: string, withWeekday = true): string {
  return new Intl.DateTimeFormat("en-MY", {
    ...(withWeekday ? { weekday: "short" } : {}),
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-MY", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kuala_Lumpur",
  }).format(new Date(value));
}

function chooseDefaultWeek(data: DashboardData): string {
  const today = new Date();
  const todayIso = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");
  return (
    data.weeks.find(
      (week) => week.week_start <= todayIso && todayIso <= week.week_end,
    )?.week_start ?? data.weeks[0]?.week_start ?? ""
  );
}

function groupByVariant<T extends { variant_index: number }>(
  records: T[],
): Map<number, T[]> {
  const grouped = new Map<number, T[]>();
  for (const record of records) {
    const current = grouped.get(record.variant_index) ?? [];
    current.push(record);
    grouped.set(record.variant_index, current);
  }
  return grouped;
}

function programmeTitle(intake: IntakeMetadata | null): string {
  if (!intake) return "Programme details unavailable";
  const course = intake.course_name ?? intake.course_code ?? intake.intake_code;
  return intake.specialism_name
    ? `${course} with a specialism in ${intake.specialism_name}`
    : course;
}

function programmeMeta(intake: IntakeMetadata | null): string {
  if (!intake) return "Programme details unavailable";
  const parts = [intake.programme_level_name];
  if (intake.academic_level !== null) parts.push(`Year ${intake.academic_level}`);
  if (intake.programme_route_name) parts.push(intake.programme_route_name);
  return parts.filter(Boolean).join(", ");
}

function isResolvedElective(row: WeeklyMetric): boolean {
  return row.elective_status === "resolved" || row.elective_status === "fixed";
}

function variantLabel(row: RankedVariant): string {
  const base = `${row.intake_code} · ${row.grouping}`;
  return isResolvedElective(row)
    ? `${base} · ${row.elective_profile_name}`
    : base;
}

function optionName(option: CodeNameOption): string {
  return option.name ?? option.code;
}

function courseName(option: CodeNameOption): string {
  return option.name ? `${option.name} (${option.code})` : option.code;
}

function clearFilter(
  filters: FilterState,
  key: NonWeekFilterKey,
): FilterState {
  return { ...filters, [key]: "" };
}

function countedOptions(
  rows: WeeklyMetric[],
  intakeByCode: Map<string, IntakeMetadata>,
  valueFor: (row: WeeklyMetric, intake: IntakeMetadata) => string | null,
  labelFor: (value: string) => string,
  allLabel: string,
): DashboardOption[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const intake = intakeByCode.get(row.intake_code);
    if (!intake) continue;
    const value = valueFor(row, intake);
    if (!value) continue;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [
    { value: "", label: allLabel, meta: `${formatNumber(rows.length)} variants` },
    ...[...counts.entries()]
      .sort((left, right) => labelFor(left[0]).localeCompare(labelFor(right[0])))
      .map(([value, count]) => ({
        value,
        label: labelFor(value),
        meta: `${formatNumber(count)} variants`,
      })),
  ];
}

function deliveryOptions(
  rows: WeeklyMetric[],
  allLabel: string,
): DashboardOption[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (row.total_campus_events > 0) {
      counts.set("campus", (counts.get("campus") ?? 0) + 1);
    }
    if (row.total_online_events > 0) {
      counts.set("online", (counts.get("online") ?? 0) + 1);
    }
    if (row.total_unknown_events > 0) {
      counts.set("unknown", (counts.get("unknown") ?? 0) + 1);
    }
  }
  const labels: Record<string, string> = {
    campus: "Includes campus classes",
    online: "Includes online classes",
    unknown: "Includes unclassified classes",
  };
  return [
    { value: "", label: allLabel, meta: `${formatNumber(rows.length)} variants` },
    ...[...counts.entries()].map(([value, count]) => ({
      value,
      label: labels[value] ?? value,
      meta: `${formatNumber(count)} variants`,
    })),
  ];
}

export function buildSmartFilterOptions(
  data: DashboardData,
  intakeByCode: Map<string, IntakeMetadata>,
  filters: FilterState,
): Record<NonWeekFilterKey, DashboardOption[]> {
  const rowsFor = (key: NonWeekFilterKey) =>
    filterWeeklyMetrics(
      data.weeklyMetrics,
      intakeByCode,
      clearFilter(filters, key),
    );
  const courseLabels = new Map(
    data.filters.courses.map((option) => [option.code, courseName(option)]),
  );
  const specialismLabels = new Map(
    data.filters.specialisms.map((option) => [option.code, optionName(option)]),
  );
  const programmeLabels = new Map(
    data.filters.programme_levels.map((option) => [option.code, optionName(option)]),
  );
  const routeLabels = new Map(
    data.filters.programme_routes.map((option) => [option.code, optionName(option)]),
  );

  return {
    programmeLevel: countedOptions(
      rowsFor("programmeLevel"),
      intakeByCode,
      (_, intake) => intake.programme_level,
      (value) => programmeLabels.get(value) ?? value,
      "All programme levels",
    ),
    academicLevel: countedOptions(
      rowsFor("academicLevel"),
      intakeByCode,
      (_, intake) =>
        intake.academic_level === null ? null : String(intake.academic_level),
      (value) => `Year ${value}`,
      "All years",
    ),
    courseCode: countedOptions(
      rowsFor("courseCode"),
      intakeByCode,
      (_, intake) => intake.course_code,
      (value) => courseLabels.get(value) ?? value,
      "All courses",
    ),
    specialismCode: countedOptions(
      rowsFor("specialismCode"),
      intakeByCode,
      (_, intake) => intake.specialism_code,
      (value) => specialismLabels.get(value) ?? value,
      "All specialisms",
    ),
    school: countedOptions(
      rowsFor("school"),
      intakeByCode,
      (_, intake) => intake.school,
      (value) => value,
      "All schools",
    ),
    programmeRoute: countedOptions(
      rowsFor("programmeRoute"),
      intakeByCode,
      (_, intake) => intake.programme_route,
      (value) => routeLabels.get(value) ?? value,
      "All programme routes",
    ),
    studyMode: countedOptions(
      rowsFor("studyMode"),
      intakeByCode,
      (_, intake) => intake.study_mode,
      (value) => value,
      "All study modes",
    ),
    grouping: countedOptions(
      rowsFor("grouping"),
      intakeByCode,
      (row) => row.grouping,
      (value) => value,
      "All groups",
    ),
    deliveryMode: deliveryOptions(
      rowsFor("deliveryMode"),
      "Any delivery mode",
    ),
  };
}

function sortValue(row: RankedVariant, key: SortKey): string | number {
  switch (key) {
    case "rank":
      return row.recalculatedBestRank;
    case "intake":
      return row.intake_code;
    case "score":
      return row.recalculatedScore;
    case "gap":
      return row.total_gap_minutes;
    case "campusDays":
      return row.campus_days;
    case "teaching":
      return row.total_teaching_minutes;
  }
}

function compareRows(
  left: RankedVariant,
  right: RankedVariant,
  key: SortKey,
  direction: SortDirection,
): number {
  const leftValue = sortValue(left, key);
  const rightValue = sortValue(right, key);
  let comparison = 0;
  if (typeof leftValue === "string" && typeof rightValue === "string") {
    comparison = leftValue.localeCompare(rightValue);
  } else {
    comparison = Number(leftValue) - Number(rightValue);
  }
  if (comparison === 0) {
    comparison =
      left.intake_code.localeCompare(right.intake_code) ||
      left.grouping.localeCompare(right.grouping);
  }
  return direction === "ascending" ? comparison : -comparison;
}

function strongestCriterion(
  row: RankedVariant,
  criteria: CriterionKey[],
): CriterionKey | null {
  return (
    [...criteria].sort(
      (left, right) =>
        row.components[right].contribution - row.components[left].contribution,
    )[0] ?? null
  );
}

function criterionValue(criterion: CriterionKey, value: number): string {
  return criterion === "gap_burden"
    ? formatMinutes(value)
    : `${value} ${value === 1 ? "day" : "days"}`;
}

function normalizeSearch(value: string): string {
  return value.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
}

function SortHeader({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  activeKey: SortKey;
  direction: SortDirection;
  onSort: (key: SortKey) => void;
}) {
  const active = activeKey === sortKey;
  return (
    <th
      aria-sort={
        active ? (direction === "ascending" ? "ascending" : "descending") : "none"
      }
    >
      <button
        className={`db-sort-button ${active ? "is-active" : ""}`}
        type="button"
        onClick={() => onSort(sortKey)}
      >
        {label}
        <span aria-hidden="true">
          {active ? (direction === "ascending" ? "↑" : "↓") : "↕"}
        </span>
      </button>
    </th>
  );
}

function Landmark({
  label,
  tone,
  rows,
  onInspect,
}: {
  label: string;
  tone: "best" | "middle" | "worst";
  rows: RankedVariant[];
  onInspect: (variantIndex: number) => void;
}) {
  const row = rows[0] ?? null;
  return (
    <button
      className={`db-landmark is-${tone}`}
      type="button"
      disabled={!row}
      onClick={() => row && onInspect(row.variant_index)}
    >
      <span>{label}</span>
      {row ? (
        <>
          <strong>{row.intake_code}</strong>
          <small>
            #{formatNumber(row.recalculatedBestRank)} · Score {formatScore(row.recalculatedScore)}
          </small>
          {rows.length > 1 && <i>{rows.length} tied timetables</i>}
        </>
      ) : (
        <strong>No timetable</strong>
      )}
    </button>
  );
}

interface ComparisonMetric {
  label: string;
  value: (row: RankedVariant) => string;
  raw: (row: RankedVariant) => number;
  lowerIsBetter: boolean;
}

const COMPARISON_METRICS: ComparisonMetric[] = [
  {
    label: "Weighted score",
    value: (row) => formatScore(row.recalculatedScore),
    raw: (row) => row.recalculatedScore,
    lowerIsBetter: true,
  },
  {
    label: "Position",
    value: (row) => `${formatNumber(row.recalculatedBestRank)} of ${formatNumber(row.peerCount)}`,
    raw: (row) => row.recalculatedBestRank,
    lowerIsBetter: true,
  },
  {
    label: "Waiting between campus classes",
    value: (row) => formatMinutes(row.total_gap_minutes),
    raw: (row) => row.total_gap_minutes,
    lowerIsBetter: true,
  },
  {
    label: "Longest single gap",
    value: (row) => formatMinutes(row.longest_gap_minutes),
    raw: (row) => row.longest_gap_minutes,
    lowerIsBetter: true,
  },
  {
    label: "Campus days",
    value: (row) => String(row.campus_days),
    raw: (row) => row.campus_days,
    lowerIsBetter: true,
  },
  {
    label: "Late-only days",
    value: (row) => String(row.late_only_days),
    raw: (row) => row.late_only_days,
    lowerIsBetter: true,
  },
  {
    label: "Early-only days",
    value: (row) => String(row.early_only_days),
    raw: (row) => row.early_only_days,
    lowerIsBetter: true,
  },
  {
    label: "One-hour-only trips",
    value: (row) => String(row.one_hour_only_days),
    raw: (row) => row.one_hour_only_days,
    lowerIsBetter: true,
  },
  {
    label: "Overloaded days",
    value: (row) => String(row.overloaded_days),
    raw: (row) => row.overloaded_days,
    lowerIsBetter: true,
  },
  {
    label: "Teaching time",
    value: (row) => formatMinutes(row.total_teaching_minutes),
    raw: (row) => row.total_teaching_minutes,
    lowerIsBetter: false,
  },
];

export function Dashboard({
  data,
  onBack,
}: {
  data: DashboardData;
  onBack?: () => void;
}) {
  const defaultWeek = chooseDefaultWeek(data);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [activeView, setActiveView] = useState<DashboardView>("rankings");
  const [filters, setFilters] = useState<FilterState>({
    weekStart: defaultWeek,
    grouping: "",
    programmeLevel: "",
    programmeRoute: "",
    academicLevel: "",
    courseCode: "",
    specialismCode: "",
    school: "",
    studyMode: "",
    deliveryMode: "",
  });
  const [criterionOrder, setCriterionOrder] = useState<CriterionKey[]>(
    data.scoring.default_criterion_order,
  );
  const [equalWeight, setEqualWeight] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDirection, setSortDirection] =
    useState<SortDirection>("ascending");
  const [page, setPage] = useState(1);
  const [selectedVariant, setSelectedVariant] = useState<number | null>(null);
  const [comparisonA, setComparisonA] = useState<number | null>(null);
  const [comparisonB, setComparisonB] = useState<number | null>(null);
  const [comparisonChart, setComparisonChart] = useState<"a" | "b">("a");

  const intakeByCode = useMemo(
    () => new Map(data.intakes.map((intake) => [intake.intake_code, intake])),
    [data.intakes],
  );
  const dailyByVariant = useMemo(
    () => groupByVariant(data.dailyMetrics),
    [data.dailyMetrics],
  );
  const blocksByVariant = useMemo(
    () => groupByVariant(data.timetableBlocks),
    [data.timetableBlocks],
  );
  const smartOptions = useMemo(
    () => buildSmartFilterOptions(data, intakeByCode, filters),
    [data, filters, intakeByCode],
  );
  const peerRows = useMemo(
    () => filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, filters),
    [data.weeklyMetrics, filters, intakeByCode],
  );
  const rankingWeights = useMemo(
    () =>
      equalWeight
        ? criterionOrder.map(() => 1)
        : data.scoring.position_weights.slice(0, criterionOrder.length),
    [criterionOrder, data.scoring.position_weights, equalWeight],
  );
  const rankedRows = useMemo(
    () => rankVariants(peerRows, criterionOrder, rankingWeights),
    [criterionOrder, peerRows, rankingWeights],
  );
  const bestRows = useMemo(
    () =>
      rankedRows
        .filter((row) => row.recalculatedIsBest)
        .sort((left, right) => left.intake_code.localeCompare(right.intake_code)),
    [rankedRows],
  );
  const worstRows = useMemo(
    () =>
      rankedRows
        .filter((row) => row.recalculatedIsWorst)
        .sort((left, right) => left.intake_code.localeCompare(right.intake_code)),
    [rankedRows],
  );
  const averageRows = useMemo(
    () =>
      rankedRows
        .filter((row) => row.recalculatedIsMostAverage)
        .sort((left, right) => left.intake_code.localeCompare(right.intake_code)),
    [rankedRows],
  );
  const selected =
    rankedRows.find((row) => row.variant_index === selectedVariant) ?? null;
  const selectedIntake = selected
    ? intakeByCode.get(selected.intake_code) ?? null
    : null;

  const searchResult = useMemo(() => {
    const query = search.trim();
    if (!query) return { rows: rankedRows, fuzzy: false };
    const normalizedQuery = normalizeSearch(query);
    const direct = rankedRows.filter((row) => {
      const intake = intakeByCode.get(row.intake_code) ?? null;
      return normalizeSearch(
        [
          row.intake_code,
          row.grouping,
          row.elective_profile_name,
          intake?.course_name,
          intake?.specialism_name,
          intake?.programme_level_name,
        ]
          .filter(Boolean)
          .join(" "),
      ).includes(normalizedQuery);
    });
    if (direct.length > 0) return { rows: direct, fuzzy: false };

    const availableCodes = new Set(rankedRows.map((row) => row.intake_code));
    const availableIntakes = data.intakes.filter((intake) =>
      availableCodes.has(intake.intake_code),
    );
    const closeCodes = new Set(
      rankIntakeMatches(availableIntakes, query, filters.weekStart, 30).map(
        (match) => match.intake.intake_code,
      ),
    );
    return {
      rows: rankedRows.filter((row) => closeCodes.has(row.intake_code)),
      fuzzy: closeCodes.size > 0,
    };
  }, [data.intakes, filters.weekStart, intakeByCode, rankedRows, search]);

  const sortedRows = useMemo(
    () =>
      [...searchResult.rows].sort((left, right) =>
        compareRows(left, right, sortKey, sortDirection),
      ),
    [searchResult.rows, sortDirection, sortKey],
  );
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE));
  const pageRows = sortedRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const comparisonLeft =
    rankedRows.find((row) => row.variant_index === comparisonA) ?? null;
  const comparisonRight =
    rankedRows.find((row) => row.variant_index === comparisonB) ?? null;
  const timetableOptions = useMemo<DashboardOption[]>(
    () =>
      [...rankedRows]
        .sort((left, right) =>
          compareRows(left, right, "rank", "ascending"),
        )
        .map((row) => ({
          value: String(row.variant_index),
          label: `#${formatNumber(row.recalculatedBestRank)} · ${variantLabel(row)}`,
          meta: programmeTitle(intakeByCode.get(row.intake_code) ?? null),
        })),
    [intakeByCode, rankedRows],
  );
  const weekOptions = data.weeks.map((week) => ({
    value: week.week_start,
    label: `Week of ${formatDate(week.week_start, false)}`,
    meta: `${formatNumber(week.variant_count)} variants`,
  }));
  const activeFilterKeys = FILTER_KEYS.filter((key) => filters[key]);
  const inactiveCriteria = CRITERION_KEYS.filter(
    (criterion) => !criterionOrder.includes(criterion),
  );
  const weightTotal = rankingWeights.reduce((total, weight) => total + weight, 0);

  useEffect(() => {
    setPage(1);
  }, [filters, search, sortDirection, sortKey]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  useEffect(() => {
    const available = new Set(rankedRows.map((row) => row.variant_index));
    if (selectedVariant !== null && !available.has(selectedVariant)) {
      setSelectedVariant(null);
    }
    if (comparisonA !== null && !available.has(comparisonA)) {
      setComparisonA(null);
    }
    if (comparisonB !== null && !available.has(comparisonB)) {
      setComparisonB(null);
    }
  }, [
    comparisonA,
    comparisonB,
    rankedRows,
    selectedVariant,
  ]);

  function updateFilter<Key extends keyof FilterState>(
    key: Key,
    value: FilterState[Key],
  ) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function resetFilters() {
    setFilters({
      weekStart: filters.weekStart,
      grouping: "",
      programmeLevel: "",
      programmeRoute: "",
      academicLevel: "",
      courseCode: "",
      specialismCode: "",
      school: "",
      studyMode: "",
      deliveryMode: "",
    });
  }

  function moveCriterion(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= criterionOrder.length) return;
    setCriterionOrder((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function resetRankingRecipe() {
    setCriterionOrder([...data.scoring.default_criterion_order]);
    setEqualWeight(false);
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDirection((current) =>
        current === "ascending" ? "descending" : "ascending",
      );
    } else {
      setSortKey(key);
      setSortDirection("ascending");
    }
  }

  function inspectVariant(variantIndex: number) {
    setSelectedVariant(variantIndex);
    setActiveView("inspect");
  }

  const comparisonLead =
    comparisonLeft && comparisonRight
      ? comparisonLeft.recalculatedBestRank === comparisonRight.recalculatedBestRank
        ? null
        : comparisonLeft.recalculatedBestRank < comparisonRight.recalculatedBestRank
          ? { row: comparisonLeft, difference: comparisonRight.recalculatedBestRank - comparisonLeft.recalculatedBestRank }
          : { row: comparisonRight, difference: comparisonLeft.recalculatedBestRank - comparisonRight.recalculatedBestRank }
      : null;
  const comparisonChartRow =
    comparisonChart === "a" ? comparisonLeft : comparisonRight;

  return (
    <div className="db-root" data-theme={theme}>
      <a className="db-skip-link" href="#db-main">
        Skip to dashboard
      </a>
      <header className="db-topbar">
        <button
          className="db-brand"
          type="button"
          onClick={onBack}
          disabled={!onBack}
          aria-label={onBack ? "Back to timetable check" : "Timetable Check dashboard"}
        >
          <span className="db-brand-mark">T</span>
          <span>
            <strong>Timetable Check</strong>
            <small>Comparison desk</small>
          </span>
        </button>
        <p>
          Snapshot from {formatDateTime(data.snapshot.collected_at)}
        </p>
        <div className="db-topbar-actions">
          {onBack && (
            <button className="db-text-button" type="button" onClick={onBack}>
              ← Back to my result
            </button>
          )}
          <button
            className="db-theme-button"
            type="button"
            onClick={() =>
              setTheme((current) => (current === "light" ? "dark" : "light"))
            }
          >
            {theme === "light" ? "Dark view" : "Light view"}
          </button>
        </div>
      </header>

      <main className="db-main" id="db-main">
        <section className="db-hero" aria-labelledby="db-title">
          <div className="db-hero-copy">
            <p className="db-kicker" style={{ fontSize: "16px" }}>APU Timetable Analyzer</p>
            <h1 id="db-title">Dashboard</h1>
            <p>
              Directly compare timetables based on your own configurations.
            </p>
          </div>
          <dl className="db-hero-stats">
            <div>
              <dt>Current pool</dt>
              <dd>{formatNumber(rankedRows.length)}</dd>
              <small>timetable variants</small>
            </div>
            <div>
              <dt>Scheduled intakes</dt>
              <dd>{formatNumber(new Set(rankedRows.map((row) => row.intake_code)).size)}</dd>
              <small>after smart filters</small>
            </div>
            <div>
              <dt>Active criteria</dt>
              <dd>{criterionOrder.length}</dd>
              <small>{equalWeight ? "equal influence" : "ranked influence"}</small>
            </div>
          </dl>
        </section>

        <section className="db-control-deck" aria-label="Dashboard controls">
          <article className="db-control-card db-pool-card">
            <header className="db-control-heading">
              <div className="db-step-number" aria-hidden="true">01</div>
              <div>
                <span>Comparison pool</span>
                <h2>Timetable Filter</h2>
                <p>Choose which timetables will be considered in the ranking.</p>
              </div>
              <button className="db-reset-button" type="button" onClick={resetFilters}>
                Reset pool
              </button>
            </header>

            <div className="db-filter-primary">
              <DashboardSelect
                label="Week"
                value={filters.weekStart}
                options={weekOptions}
                placeholder="Choose a week"
                onChange={(value) => updateFilter("weekStart", value)}
              />
              <DashboardSelect
                label="Programme level"
                value={filters.programmeLevel}
                options={smartOptions.programmeLevel}
                placeholder="All programme levels"
                onChange={(value) => updateFilter("programmeLevel", value)}
              />
              <DashboardSelect
                label="Year"
                value={filters.academicLevel}
                options={smartOptions.academicLevel}
                placeholder={
                  smartOptions.academicLevel.length <= 1
                    ? "No year detected"
                    : "All years"
                }
                disabled={smartOptions.academicLevel.length <= 1}
                onChange={(value) => updateFilter("academicLevel", value)}
              />
              <DashboardSelect
                label="Course"
                value={filters.courseCode}
                options={smartOptions.courseCode}
                placeholder="All courses"
                searchable
                onChange={(value) => updateFilter("courseCode", value)}
              />
              <DashboardSelect
                label="Specialism"
                value={filters.specialismCode}
                options={smartOptions.specialismCode}
                placeholder={
                  smartOptions.specialismCode.length <= 1
                    ? "No specialisms detected"
                    : "All specialisms"
                }
                helper={
                  smartOptions.specialismCode.length <= 1
                    ? "No specialism choices exist for the current programme pool."
                    : undefined
                }
                disabled={smartOptions.specialismCode.length <= 1}
                searchable
                onChange={(value) => updateFilter("specialismCode", value)}
              />
            </div>

            <details className="db-more-filters">
              <summary>
                <span>More filters</span>
                <small>School, route, mode, group, and delivery</small>
              </summary>
              <div className="db-filter-secondary">
                <DashboardSelect
                  label="School"
                  value={filters.school}
                  options={smartOptions.school}
                  placeholder="All schools"
                  disabled={smartOptions.school.length <= 1}
                  searchable
                  onChange={(value) => updateFilter("school", value)}
                />
                <DashboardSelect
                  label="Programme route"
                  value={filters.programmeRoute}
                  options={smartOptions.programmeRoute}
                  placeholder="All programme routes"
                  disabled={smartOptions.programmeRoute.length <= 1}
                  onChange={(value) => updateFilter("programmeRoute", value)}
                />
                <DashboardSelect
                  label="Study mode"
                  value={filters.studyMode}
                  options={smartOptions.studyMode}
                  placeholder="All study modes"
                  disabled={smartOptions.studyMode.length <= 1}
                  onChange={(value) => updateFilter("studyMode", value)}
                />
                <DashboardSelect
                  label="Group"
                  value={filters.grouping}
                  options={smartOptions.grouping}
                  placeholder="All groups"
                  disabled={smartOptions.grouping.length <= 1}
                  onChange={(value) => updateFilter("grouping", value)}
                />
                <DashboardSelect
                  label="Delivery mode"
                  value={filters.deliveryMode}
                  options={smartOptions.deliveryMode}
                  placeholder="Any delivery mode"
                  disabled={smartOptions.deliveryMode.length <= 1}
                  onChange={(value) => updateFilter("deliveryMode", value)}
                />
              </div>
            </details>

            <div className="db-filter-footer">
              <div className="db-filter-summary">
                <strong>{formatNumber(rankedRows.length)} variants</strong>
                <span>are being ranked against one another</span>
              </div>
              <div className="db-filter-chips" aria-label="Active comparison filters">
                {activeFilterKeys.length === 0 ? (
                  <span className="db-no-filters">No extra filters applied</span>
                ) : (
                  activeFilterKeys.map((key) => {
                    const selectedOption = smartOptions[key].find(
                      (option) => option.value === filters[key],
                    );
                    return (
                      <button
                        type="button"
                        key={key}
                        aria-label={`Remove ${FILTER_LABELS[key]} filter`}
                        onClick={() => updateFilter(key, "")}
                      >
                        <span>{FILTER_LABELS[key]}</span>
                        {selectedOption?.label ?? filters[key]}
                        <i aria-hidden="true">×</i>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          </article>

          <article className="db-control-card db-recipe-card">
            <header className="db-control-heading">
              <div className="db-step-number" aria-hidden="true">02</div>
              <div>
                <span>Ranking configuration</span>
                <h2>Frustration Filter</h2>
                <p>Rerank or remove frustration points.</p>
                <br></br>
              </div>
              <button
                className="db-reset-button"
                type="button"
                onClick={resetRankingRecipe}
              >
                Use defaults
              </button>
            </header>

            <ol className="db-priority-list" aria-label="Frustration priority order">
              {criterionOrder.map((criterion, index) => (
                <li key={criterion}>
                  <span className="db-priority-position">{index + 1}</span>
                  <span className="db-priority-copy">
                    <strong>{CRITERION_DETAILS[criterion].label}</strong>
                    <small>
                      {equalWeight
                        ? "Equal weight"
                        : `${weightTotal > 0 ? ((rankingWeights[index] / weightTotal) * 100).toFixed(1) : "0.0"}% influence`}
                    </small>
                  </span>
                  <span className="db-priority-actions">
                    <button
                      type="button"
                      disabled={index === 0}
                      aria-label={`Move ${CRITERION_DETAILS[criterion].label} up`}
                      onClick={() => moveCriterion(index, -1)}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      disabled={index === criterionOrder.length - 1}
                      aria-label={`Move ${CRITERION_DETAILS[criterion].label} down`}
                      onClick={() => moveCriterion(index, 1)}
                    >
                      ↓
                    </button>
                    <button
                      className="is-remove"
                      type="button"
                      aria-label={`Remove ${CRITERION_DETAILS[criterion].label}`}
                      onClick={() =>
                        setCriterionOrder((current) =>
                          current.filter((item) => item !== criterion),
                        )
                      }
                    >
                      ×
                    </button>
                  </span>
                </li>
              ))}
            </ol>

            {inactiveCriteria.length > 0 && (
              <div className="db-restore-criteria">
                <span>Removed</span>
                {inactiveCriteria.map((criterion) => (
                  <button
                    type="button"
                    key={criterion}
                    onClick={() =>
                      setCriterionOrder((current) => [...current, criterion])
                    }
                  >
                    + {CRITERION_DETAILS[criterion].shortLabel}
                  </button>
                ))}
              </div>
            )}

            <label className="db-equal-toggle">
              <input
                type="checkbox"
                checked={equalWeight}
                disabled={criterionOrder.length === 0}
                onChange={(event) => setEqualWeight(event.target.checked)}
              />
              <span aria-hidden="true" />
              <strong>Treat everything equally</strong>
              <small>Turns off priority weighting without removing any criteria.</small>
            </label>
          </article>
        </section>

        <section className="db-workspace" aria-labelledby="db-workspace-title">
          <header className="db-workspace-heading">
            <div className="db-step-number" aria-hidden="true">03</div>
            <div>
              <span>Results workspace</span>
              <h2 id="db-workspace-title">View Results</h2>
            </div>
          </header>
          <nav className="db-view-tabs" role="tablist" aria-label="Dashboard views">
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "rankings"}
              aria-controls="db-rankings-view"
              className={activeView === "rankings" ? "is-active" : ""}
              onClick={() => setActiveView("rankings")}
            >
              <span>Ranked list</span>
              <small>{formatNumber(rankedRows.length)} timetables</small>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "inspect"}
              aria-controls="db-inspect-view"
              className={activeView === "inspect" ? "is-active" : ""}
              onClick={() => setActiveView("inspect")}
            >
              <span>Inspect timetable</span>
              <small>{selected?.intake_code ?? "Choose a timetable"}</small>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "compare"}
              aria-controls="db-compare-view"
              className={activeView === "compare" ? "is-active" : ""}
              onClick={() => setActiveView("compare")}
            >
              <span>Compare timetables</span>
              <small>
                {comparisonLeft && comparisonRight
                  ? `#${comparisonLeft.recalculatedBestRank} beside #${comparisonRight.recalculatedBestRank}`
                  : comparisonLeft || comparisonRight
                    ? "Choose one more timetable"
                    : "Choose two timetables"}
              </small>
            </button>
          </nav>

          {activeView === "rankings" && (
            <div
              className="db-view-panel db-rankings-view"
              id="db-rankings-view"
              role="tabpanel"
            >
              {rankedRows.length === 0 ? (
                <div className="db-empty-state">
                  <span aria-hidden="true">0</span>
                  <h3>No timetables fit this comparison pool.</h3>
                  <p>Remove a filter or reset the pool to continue.</p>
                  <button type="button" onClick={resetFilters}>Reset comparison pool</button>
                </div>
              ) : (
                <>
                  <section className="db-range" aria-labelledby="db-range-title">
                    <header>
                      <div>
                        <span>Best vs Median vs Worst</span>
                        <h3 id="db-range-title">Overall Summary</h3>
                      </div>
                    </header>
                    <div className="db-range-track">
                      <Landmark label="Best" tone="best" rows={bestRows} onInspect={inspectVariant} />
                      <div className="db-range-line" aria-hidden="true"><span /></div>
                      <Landmark label="Middle" tone="middle" rows={averageRows} onInspect={inspectVariant} />
                      <div className="db-range-line" aria-hidden="true"><span /></div>
                      <Landmark label="Worst" tone="worst" rows={worstRows} onInspect={inspectVariant} />
                    </div>
                  </section>

                  <section className="db-ranking-list" aria-labelledby="db-ranking-title">
                    <header className="db-ranking-header">
                      <div>
                        <br></br>
                        <span>All ranked timetables</span>
                        <h3 id="db-ranking-title">Find and inspect a timetable</h3>
                      </div>
                      <div className="db-ranking-search">
                        <label htmlFor="db-ranking-search-input">
                          Search intake or programme
                        </label>
                        <div>
                          <i aria-hidden="true">⌕</i>
                          <input
                            id="db-ranking-search-input"
                            type="search"
                            value={search}
                            placeholder="Try APD3F2605CS(DA)"
                            onChange={(event) => setSearch(event.target.value)}
                          />
                          {search && (
                            <button
                              type="button"
                              aria-label="Clear timetable search"
                              onClick={() => setSearch("")}
                            >
                              ×
                            </button>
                          )}
                        </div>
                      </div>
                    </header>

                    <div className="db-list-status" aria-live="polite">
                      <strong>
                        {formatNumber(sortedRows.length)} {sortedRows.length === 1 ? "match" : "matches"}
                      </strong>
                      <span>
                        {searchResult.fuzzy
                          ? "Showing the closest intake-code matches to your search."
                          : `Scores still use ${formatNumber(rankedRows.length)} peers.`}
                      </span>
                    </div>

                    {sortedRows.length === 0 ? (
                      <div className="db-search-empty">
                        <h4>No intake or programme matches “{search}”.</h4>
                        <p>Your comparison pool is unchanged. Clear the search to see every ranked timetable.</p>
                        <button type="button" onClick={() => setSearch("")}>Clear search</button>
                      </div>
                    ) : (
                      <>
                        <div className="db-table-scroll" tabIndex={0}>
                          <table className="db-ranking-table">
                            <thead>
                              <tr>
                                <SortHeader label="Position" sortKey="rank" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                                <SortHeader label="Timetable" sortKey="intake" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                                <SortHeader label="Score" sortKey="score" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                                <SortHeader label="Campus waiting" sortKey="gap" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                                <SortHeader label="Campus days" sortKey="campusDays" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                                <th>Biggest score driver</th>
                                <th><span className="db-sr-only">Actions</span></th>
                              </tr>
                            </thead>
                            <tbody>
                              {pageRows.map((row) => {
                                const intake = intakeByCode.get(row.intake_code) ?? null;
                                const driver = strongestCriterion(row, criterionOrder);
                                return (
                                  <tr
                                    className={row.variant_index === selected?.variant_index ? "is-selected" : ""}
                                    key={row.variant_index}
                                  >
                                    <td className="db-rank-cell">
                                      <strong>#{formatNumber(row.recalculatedBestRank)}</strong>
                                      <small>of {formatNumber(row.peerCount)}</small>
                                    </td>
                                    <th scope="row" className="db-timetable-cell">
                                      <strong>{row.intake_code}</strong>
                                      <span>{row.grouping}{isResolvedElective(row) ? ` · ${row.elective_profile_name}` : ""}</span>
                                      <small>{programmeTitle(intake)}</small>
                                    </th>
                                    <td className="db-score-cell">{formatScore(row.recalculatedScore)}</td>
                                    <td>{formatMinutes(row.total_gap_minutes)}</td>
                                    <td>{row.campus_days}</td>
                                    <td className="db-driver-cell">
                                      {driver ? (
                                        <>
                                          <strong>{CRITERION_DETAILS[driver].shortLabel}</strong>
                                          <small>{criterionValue(driver, row.components[driver].raw)}</small>
                                        </>
                                      ) : (
                                        <span>Ranking criteria removed</span>
                                      )}
                                    </td>
                                    <td className="db-row-actions">
                                      <button type="button" onClick={() => inspectVariant(row.variant_index)}>
                                        Inspect
                                      </button>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                        <nav className="db-pagination" aria-label="Ranking table pages">
                          <button
                            type="button"
                            disabled={page === 1}
                            onClick={() => setPage((current) => current - 1)}
                          >
                            ← Previous
                          </button>
                          <span>
                            Page <strong>{page}</strong> of {pageCount}
                          </span>
                          <button
                            type="button"
                            disabled={page === pageCount}
                            onClick={() => setPage((current) => current + 1)}
                          >
                            Next →
                          </button>
                        </nav>
                      </>
                    )}
                  </section>
                </>
              )}
            </div>
          )}

          {activeView === "inspect" && !selected && (
            <div
              className="db-view-panel db-inspect-view"
              id="db-inspect-view"
              role="tabpanel"
            >
              <section className="db-inspector-empty" aria-labelledby="db-inspector-empty-title">
                <div className="db-inspector-empty-copy">
                  <span>Timetable inspector</span>
                  <h3 id="db-inspector-empty-title">Choose a timetable to inspect.</h3>
                  <p>
                    Search by intake code, group, elective, or programme. The full score explanation and vertical week will appear after selection.
                  </p>
                </div>
                <div className="db-inspector-picker">
                  <span className="db-inspector-picker-mark" aria-hidden="true">T</span>
                  <DashboardSelect
                    label="Timetable to inspect"
                    value=""
                    options={timetableOptions}
                    placeholder="Choose a timetable"
                    searchable
                    onChange={(value) => inspectVariant(Number(value))}
                  />
                </div>
              </section>
            </div>
          )}

          {activeView === "inspect" && selected && (
            <div
              className="db-view-panel db-inspect-view"
              id="db-inspect-view"
              role="tabpanel"
            >
              <section className="db-inspector-hero" aria-labelledby="db-inspector-title">
                <div>
                  <span>Selected timetable</span>
                  <h3 id="db-inspector-title">{programmeTitle(selectedIntake)}</h3>
                  <p>{variantLabel(selected)} · {programmeMeta(selectedIntake)}</p>
                </div>
                <aside className="db-inspector-rank" aria-label="Selected timetable position">
                  <span>Your position</span>
                  <div><strong>{formatNumber(selected.recalculatedBestRank)}</strong><small>of {formatNumber(selected.peerCount)}</small></div>
                  <p>Lower is better</p>
                </aside>
              </section>

              <section className="db-reading-strip" aria-label="Timetable summary">
                <div className="db-reading-verdict">
                  <span aria-hidden="true">~</span>
                  <div>
                    <strong>
                      {selected.total_gap_minutes > 0
                        ? "Campus waiting has the clearest visible impact."
                        : "This week avoids long campus waits."}
                    </strong>
                    <p>
                      {strongestCriterion(selected, criterionOrder)
                        ? `Biggest score driver: ${CRITERION_DETAILS[strongestCriterion(selected, criterionOrder)!].label.toLowerCase()}.`
                        : "No frustration criteria are active."}
                    </p>
                  </div>
                </div>
                <div>
                  <span>WEIGHTED SCORE</span>
                  <strong className="db-score-total">
                    {formatScore(selected.recalculatedScore)}<small>/100</small>
                  </strong>
                  <small>lower is better</small>
                </div>
                <div><span>CAMPUS WAITING</span><strong>{formatMinutes(selected.total_gap_minutes)}</strong><small>across the week</small></div>
                <div><span>CAMPUS DAYS</span><strong>{selected.campus_days}</strong><small>{formatMinutes(selected.total_teaching_minutes)} teaching</small></div>
              </section>

              <section className="db-timetable-section" aria-labelledby="db-timetable-title">
                <header className="db-section-heading">
                  <div>
                    <span>Vertical week view</span>
                    <h3 id="db-timetable-title">See exactly where the frustration comes from.</h3>
                    <p>Days run across the top. Time runs down the left.</p>
                  </div>
                  <div className="tn-chart-legend" aria-label="Timetable legend">
                    <span><i className="legend-campus" /> Campus class</span>
                    <span><i className="legend-online" /> Online class</span>
                    <span><i className="legend-gap" /> Campus gap</span>
                  </div>
                </header>
                <VerticalTimetable
                  weekStart={selected.week_start}
                  row={selected}
                  days={dailyByVariant.get(selected.variant_index) ?? []}
                  blocks={blocksByVariant.get(selected.variant_index) ?? []}
                  ariaLabel={`Weekly timetable for ${selected.intake_code}`}
                />
                <p className="db-chart-note">
                  Gap colour appears only between the first and last campus class. Online classes outside that campus window do not create waiting time.
                </p>
              </section>

              <section className="db-score-section" aria-labelledby="db-score-title">
                <header className="db-section-heading">
                  <div>
                    <span>Score explanation</span>
                    <h3 id="db-score-title">How this timetable reached {formatScore(selected.recalculatedScore)}.</h3>
                    <p>Each active frustration is measured against the current comparison pool.</p>
                  </div>
                </header>
                {criterionOrder.length === 0 ? (
                  <div className="db-score-empty">Restore a frustration criterion to rebuild the score.</div>
                ) : (
                  <div className="db-table-scroll" tabIndex={0}>
                    <table className="db-score-table">
                      <thead>
                        <tr><th>Priority</th><th>Frustration</th><th>Your value</th><th>Peer percentile</th><th>Weight</th><th>Score impact</th></tr>
                      </thead>
                      <tbody>
                        {criterionOrder.map((criterion, index) => {
                          const component = selected.components[criterion];
                          return (
                            <tr key={criterion}>
                              <td>{equalWeight ? "=" : index + 1}</td>
                              <th scope="row">{CRITERION_DETAILS[criterion].label}</th>
                              <td>{criterionValue(criterion, component.raw)}</td>
                              <td>{formatScore(component.percentile)}%</td>
                              <td>{(component.weight * 100).toFixed(1)}%</td>
                              <td>{formatScore(component.contribution)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </div>
          )}

          {activeView === "compare" && (
            <div
              className="db-view-panel db-compare-view"
              id="db-compare-view"
              role="tabpanel"
            >
              <section className="db-compare-heading" aria-labelledby="db-compare-title">
                <div>
                  <span>Direct comparison</span>
                  <h3 id="db-compare-title">Compare two timetables.</h3>
                  <p>Both variants use the same configuration, so the results are directly comparable.</p>
                </div>
              </section>

              <div className="db-compare-pickers">
                <article className="is-a">
                  <span className="db-compare-label">A</span>
                  <DashboardSelect
                    label="Timetable A"
                    value={comparisonLeft ? String(comparisonLeft.variant_index) : ""}
                    options={timetableOptions}
                    placeholder="Choose timetable A"
                    searchable
                    onChange={(value) => setComparisonA(Number(value))}
                  />
                  <button
                    type="button"
                    disabled={!comparisonLeft}
                    onClick={() => comparisonLeft && inspectVariant(comparisonLeft.variant_index)}
                  >
                    Inspect A
                  </button>
                </article>
                <div className="db-versus" aria-hidden="true">VS</div>
                <article className="is-b">
                  <span className="db-compare-label">B</span>
                  <DashboardSelect
                    label="Timetable B"
                    value={comparisonRight ? String(comparisonRight.variant_index) : ""}
                    options={timetableOptions}
                    placeholder="Choose timetable B"
                    searchable
                    onChange={(value) => setComparisonB(Number(value))}
                  />
                  <button
                    type="button"
                    disabled={!comparisonRight}
                    onClick={() => comparisonRight && inspectVariant(comparisonRight.variant_index)}
                  >
                    Inspect B
                  </button>
                </article>
              </div>

              {comparisonLeft && comparisonRight ? (
                <>
              <section className="db-compare-verdict" aria-label="Comparison summary">
                <span>At a glance</span>
                <h4  style={{ fontSize: "36px"}}>
                  {comparisonLead
                    ? `${comparisonLead.row.intake_code} ranks ${formatNumber(comparisonLead.difference)} ${comparisonLead.difference === 1 ? "place" : "places"} better.`
                    : "These timetables share the same position."}
                </h4>
                <br></br>
              </section>

              <div className="db-table-scroll" tabIndex={0}>
                <table className="db-comparison-table">
                  <thead>
                    <tr>
                      <th>Measure</th>
                      <th><span>A</span>{variantLabel(comparisonLeft)}</th>
                      <th><span>B</span>{variantLabel(comparisonRight)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {COMPARISON_METRICS.map((metric) => {
                      const leftRaw = metric.raw(comparisonLeft);
                      const rightRaw = metric.raw(comparisonRight);
                      const leftBetter = metric.lowerIsBetter && leftRaw < rightRaw;
                      const rightBetter = metric.lowerIsBetter && rightRaw < leftRaw;
                      return (
                        <tr key={metric.label}>
                          <th scope="row">{metric.label}</th>
                          <td className={leftBetter ? "is-better" : ""}>
                            <strong>{metric.value(comparisonLeft)}</strong>
                            {leftBetter && <small>Better</small>}
                          </td>
                          <td className={rightBetter ? "is-better" : ""}>
                            <strong>{metric.value(comparisonRight)}</strong>
                            {rightBetter && <small>Better</small>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <section className="db-compare-chart" aria-labelledby="db-compare-chart-title">
                <header className="db-section-heading">
                  <div>
                    <span>Visual inspection</span>
                    <h3 id="db-compare-chart-title">Switch between the full weeks.</h3>
                    <p>The chart stays at full size so class blocks and campus gaps remain readable.</p>
                  </div>
                  <div className="db-chart-toggle" role="group" aria-label="Compared timetable chart">
                    <button
                      type="button"
                      className={comparisonChart === "a" ? "is-active" : ""}
                      onClick={() => setComparisonChart("a")}
                    >
                      View A
                    </button>
                    <button
                      type="button"
                      className={comparisonChart === "b" ? "is-active" : ""}
                      onClick={() => setComparisonChart("b")}
                    >
                      View B
                    </button>
                  </div>
                </header>
                {comparisonChartRow && (
                  <>
                    <div className="db-chart-identity">
                      <strong>{variantLabel(comparisonChartRow)}</strong>
                      <span>Position #{formatNumber(comparisonChartRow.recalculatedBestRank)} · Score {formatScore(comparisonChartRow.recalculatedScore)}</span>
                    </div>
                    <VerticalTimetable
                      weekStart={comparisonChartRow.week_start}
                      row={comparisonChartRow}
                      days={dailyByVariant.get(comparisonChartRow.variant_index) ?? []}
                      blocks={blocksByVariant.get(comparisonChartRow.variant_index) ?? []}
                      ariaLabel={`Compared weekly timetable for ${comparisonChartRow.intake_code}`}
                    />
                  </>
                )}
              </section>
                </>
              ) : (
                <section className="db-comparison-empty" aria-live="polite">
                  <div aria-hidden="true">
                    <span className={comparisonLeft ? "is-ready" : ""}>A</span>
                    <i>+</i>
                    <span className={comparisonRight ? "is-ready" : ""}>B</span>
                  </div>
                  <h4>Choose two timetables to begin.</h4>
                  <p>
                    {comparisonLeft || comparisonRight
                      ? "One timetable is ready. Choose the other timetable to reveal the comparison."
                      : "The verdict, score table, and vertical timetable will appear after both fields are selected."}
                  </p>
                </section>
              )}
            </div>
          )}
        </section>

        <details className="db-method-note">
          <summary>How these comparisons work</summary>
          <div>
            <p>Filters rebuild the peer group. Search only changes which ranked rows are visible.</p>
            <p>Online classes remain visible, but only gaps inside the first-to-last campus window count as campus waiting.</p>
            <p>Rankings recalculate in this browser from the latest static timetable snapshot.</p>
          </div>
        </details>
      </main>
    </div>
  );
}
