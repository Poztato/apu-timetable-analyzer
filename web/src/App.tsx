import { useEffect, useMemo, useState } from "react";

import { loadDashboardData } from "./data";
import {
  CRITERION_DETAILS,
  filterWeeklyMetrics,
  rankVariants,
} from "./ranking";
import type {
  FilterState,
  RankedVariant,
} from "./ranking";
import type {
  CodeNameOption,
  CriterionKey,
  DailyMetric,
  DashboardData,
  IntakeMetadata,
  TimetableBlock,
} from "./types";

const PAGE_SIZE = 50;

type SortKey =
  | "rank"
  | "intake"
  | "grouping"
  | "score"
  | "gap"
  | "late"
  | "early"
  | "oneHour"
  | "overloaded"
  | "teaching"
  | "activeDays";

type SortDirection = "ascending" | "descending";

interface SelectOption {
  value: string;
  label: string;
}

interface ScheduleBlockWithGap {
  block: TimetableBlock;
  gapBefore: number;
}

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

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-MY", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00`));
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-MY", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
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

function optionLabel(option: CodeNameOption): string {
  return option.name ? `${option.code}: ${option.name}` : option.code;
}

function courseOptionLabel(option: CodeNameOption): string {
  return option.name ? `${option.name} (${option.code})` : option.code;
}

function nameOnlyOptionLabel(option: CodeNameOption): string {
  return option.name ?? option.code;
}

function variantLabel(row: RankedVariant): string {
  const base = `${row.intake_code} (${row.grouping})`;
  if (row.elective_status === "resolved" || row.elective_status === "fixed") {
    return `${base}, ${row.elective_profile_name}`;
  }
  return base;
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

export function scheduleBlocksWithGaps(
  blocks: TimetableBlock[],
): ScheduleBlockWithGap[] {
  const sorted = [...blocks].sort(
    (left, right) => Date.parse(left.start_at) - Date.parse(right.start_at),
  );
  const campusBlocks = sorted.filter(
    (block) => block.delivery_mode === "campus",
  );
  if (campusBlocks.length < 2) {
    return sorted.map((block) => ({ block, gapBefore: 0 }));
  }

  const campusWindowStart = Math.min(
    ...campusBlocks.map((block) => Date.parse(block.start_at)),
  );
  const campusWindowEnd = Math.max(
    ...campusBlocks.map((block) => Date.parse(block.end_at)),
  );
  let occupiedUntil: number | null = null;
  return sorted.map((block) => {
    const start = Date.parse(block.start_at);
    const end = Date.parse(block.end_at);
    if (end <= campusWindowStart || start >= campusWindowEnd) {
      return { block, gapBefore: 0 };
    }

    const boundedStart = Math.max(start, campusWindowStart);
    const boundedEnd = Math.min(end, campusWindowEnd);
    const gapBefore =
      occupiedUntil === null || boundedStart <= occupiedUntil
        ? 0
        : Math.round((boundedStart - occupiedUntil) / 60_000);
    occupiedUntil =
      occupiedUntil === null
        ? boundedEnd
        : Math.max(occupiedUntil, boundedEnd);
    return { block, gapBefore };
  });
}

function sortValue(row: RankedVariant, key: SortKey): string | number {
  switch (key) {
    case "rank":
      return row.recalculatedBestRank;
    case "intake":
      return row.intake_code;
    case "grouping":
      return row.grouping;
    case "score":
      return row.recalculatedScore;
    case "gap":
      return row.total_gap_minutes;
    case "late":
      return row.late_only_days;
    case "early":
      return row.early_only_days;
    case "oneHour":
      return row.one_hour_only_days;
    case "overloaded":
      return row.overloaded_days;
    case "teaching":
      return row.total_teaching_minutes;
    case "activeDays":
      return row.active_days;
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

function SelectField({
  label,
  value,
  options,
  allLabel,
  disabled = false,
  onChange,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  allLabel: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{disabled ? `${label} unavailable` : allLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function SortableHeader({
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
      <button className="sort-button" type="button" onClick={() => onSort(sortKey)}>
        {label} {active ? (direction === "ascending" ? "▲" : "▼") : ""}
      </button>
    </th>
  );
}

function ResultCard({
  title,
  rows,
  onSelect,
}: {
  title: string;
  rows: RankedVariant[];
  onSelect: (variantIndex: number) => void;
}) {
  if (rows.length === 0) {
    return (
      <article className="result-card">
        <h3>{title}</h3>
        <p>No result in this comparison set.</p>
      </article>
    );
  }
  const first = rows[0];
  return (
    <article className="result-card">
      <h3>{title}</h3>
      <p className="result-code">{variantLabel(first)}</p>
      <p>
        Score <strong>{formatScore(first.recalculatedScore)}</strong>
        {rows.length > 1 ? `, tied with ${rows.length - 1} other variants` : ""}
      </p>
      <button type="button" onClick={() => onSelect(first.variant_index)}>
        Inspect timetable
      </button>
    </article>
  );
}

function flagsForDay(day: DailyMetric): string[] {
  const flags = [];
  if (day.early_only_flag) flags.push("Early-only");
  if (day.late_only_flag) flags.push("Late-only");
  if (day.one_hour_only_flag) flags.push("One-hour-only");
  if (day.overloaded_flag) flags.push("Overloaded");
  if (day.is_weekend) flags.push("Weekend");
  return flags;
}

function SchedulePanel({
  row,
  days,
  blocks,
}: {
  row: RankedVariant;
  days: DailyMetric[];
  blocks: TimetableBlock[];
}) {
  const blocksByDate = useMemo(() => {
    const grouped = new Map<string, TimetableBlock[]>();
    for (const block of blocks) {
      const current = grouped.get(block.event_date) ?? [];
      current.push(block);
      grouped.set(block.event_date, current);
    }
    return grouped;
  }, [blocks]);

  return (
    <section className="panel" aria-labelledby="schedule-heading">
      <div className="section-heading">
        <div>
          <h2 id="schedule-heading">Timetable for {variantLabel(row)}</h2>
          <p>{formatDate(row.week_start)} week</p>
        </div>
      </div>
      {days.length === 0 ? (
        <p>No daily records are available for this variant.</p>
      ) : (
        <div className="schedule-days">
          {[...days]
            .sort((left, right) => left.event_date.localeCompare(right.event_date))
            .map((day) => {
              const dayBlocks = scheduleBlocksWithGaps(
                blocksByDate.get(day.event_date) ?? [],
              );
              const flags = flagsForDay(day);
              return (
                <article className="schedule-day" key={day.event_date}>
                  <div className="schedule-day-heading">
                    <div>
                      <h3>{formatDate(day.event_date)}</h3>
                      <p>
                        {formatMinutes(day.teaching_minutes)} teaching, {" "}
                        {formatMinutes(day.total_gap_minutes)} gaps
                      </p>
                    </div>
                    <div className="badges">
                      {flags.map((flag) => (
                        <span className="badge warning" key={flag}>
                          {flag}
                        </span>
                      ))}
                      {day.online_event_count > 0 && (
                        <span className="badge">Online included</span>
                      )}
                    </div>
                  </div>
                  <ol className="schedule-list">
                    {dayBlocks.map(({ block, gapBefore }) => (
                      <li key={`${block.start_at}-${block.module_id}-${block.room ?? ""}`}>
                        {gapBefore > 0 && (
                          <div className="gap-marker">
                            Gap: {formatMinutes(gapBefore)}
                          </div>
                        )}
                        <div className="class-block">
                          <strong>
                            {formatTime(block.start_at)} to {formatTime(block.end_at)}
                          </strong>
                          <span>{block.module_name ?? block.module_id}</span>
                          <small>
                            {block.delivery_mode}
                            {block.room ? `, ${block.room}` : ""}
                            {block.location ? `, ${block.location}` : ""}
                            {block.is_shared_slot
                              ? `, shared across ${block.shared_group_count} groups`
                              : ""}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ol>
                </article>
              );
            })}
        </div>
      )}
    </section>
  );
}

const COMPARISON_METRICS: Array<{
  label: string;
  value: (row: RankedVariant) => string;
}> = [
  { label: "Score", value: (row) => formatScore(row.recalculatedScore) },
  {
    label: "Best-to-worst rank",
    value: (row) => `${row.recalculatedBestRank} of ${row.peerCount}`,
  },
  { label: "Active days", value: (row) => String(row.active_days) },
  {
    label: "Teaching time",
    value: (row) => formatMinutes(row.total_teaching_minutes),
  },
  { label: "Total gaps", value: (row) => formatMinutes(row.total_gap_minutes) },
  { label: "Longest gap", value: (row) => formatMinutes(row.longest_gap_minutes) },
  { label: "Early-only days", value: (row) => String(row.early_only_days) },
  { label: "Late-only days", value: (row) => String(row.late_only_days) },
  {
    label: "One-hour-only days",
    value: (row) => String(row.one_hour_only_days),
  },
  { label: "Overloaded days", value: (row) => String(row.overloaded_days) },
];

function ComparisonPanel({
  rows,
  comparisonA,
  comparisonB,
  onChangeA,
  onChangeB,
}: {
  rows: RankedVariant[];
  comparisonA: number | null;
  comparisonB: number | null;
  onChangeA: (variantIndex: number) => void;
  onChangeB: (variantIndex: number) => void;
}) {
  const orderedOptions = [...rows].sort(
    (left, right) =>
      left.intake_code.localeCompare(right.intake_code) ||
      left.grouping.localeCompare(right.grouping),
  );
  const left = rows.find((row) => row.variant_index === comparisonA) ?? null;
  const right = rows.find((row) => row.variant_index === comparisonB) ?? null;

  return (
    <section className="panel" aria-labelledby="comparison-heading">
      <h2 id="comparison-heading">Side-by-side comparison</h2>
      {rows.length === 0 ? (
        <p>No variants are available to compare.</p>
      ) : (
        <>
          <div className="comparison-selects">
            <label className="field">
              <span>Timetable A</span>
              <select
                value={comparisonA ?? ""}
                onChange={(event) => onChangeA(Number(event.target.value))}
              >
                {orderedOptions.map((row) => (
                  <option key={row.variant_index} value={row.variant_index}>
                    {variantLabel(row)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Timetable B</span>
              <select
                value={comparisonB ?? ""}
                onChange={(event) => onChangeB(Number(event.target.value))}
              >
                {orderedOptions.map((row) => (
                  <option key={row.variant_index} value={row.variant_index}>
                    {variantLabel(row)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {left && right && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Measure</th>
                    <th>{variantLabel(left)}</th>
                    <th>{variantLabel(right)}</th>
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON_METRICS.map((metric) => (
                    <tr key={metric.label}>
                      <th>{metric.label}</th>
                      <td>{metric.value(left)}</td>
                      <td>{metric.value(right)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function Dashboard({ data }: { data: DashboardData }) {
  const defaultWeek = chooseDefaultWeek(data);
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
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDirection, setSortDirection] =
    useState<SortDirection>("ascending");
  const [page, setPage] = useState(1);
  const [selectedVariant, setSelectedVariant] = useState<number | null>(null);
  const [comparisonA, setComparisonA] = useState<number | null>(null);
  const [comparisonB, setComparisonB] = useState<number | null>(null);

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

  const peerRows = useMemo(
    () => filterWeeklyMetrics(data.weeklyMetrics, intakeByCode, filters),
    [data.weeklyMetrics, filters, intakeByCode],
  );
  const rankedRows = useMemo(
    () => rankVariants(peerRows, criterionOrder, data.scoring.position_weights),
    [peerRows, criterionOrder, data.scoring.position_weights],
  );
  const searchText = search.trim().toUpperCase();
  const visibleRows = useMemo(
    () =>
      rankedRows.filter((row) =>
        searchText ? row.intake_code.toUpperCase().includes(searchText) : true,
      ),
    [rankedRows, searchText],
  );
  const sortedRows = useMemo(
    () =>
      [...visibleRows].sort((left, right) =>
        compareRows(left, right, sortKey, sortDirection),
      ),
    [visibleRows, sortKey, sortDirection],
  );
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE));
  const pageRows = sortedRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const bestRows = rankedRows
    .filter((row) => row.recalculatedIsBest)
    .sort((a, b) => a.intake_code.localeCompare(b.intake_code));
  const worstRows = rankedRows
    .filter((row) => row.recalculatedIsWorst)
    .sort((a, b) => a.intake_code.localeCompare(b.intake_code));
  const averageRows = rankedRows
    .filter((row) => row.recalculatedIsMostAverage)
    .sort((a, b) => a.intake_code.localeCompare(b.intake_code));
  const selected =
    rankedRows.find((row) => row.variant_index === selectedVariant) ??
    bestRows[0] ??
    null;
  const selectedIntake: IntakeMetadata | null = selected
    ? intakeByCode.get(selected.intake_code) ?? null
    : null;

  useEffect(() => {
    setPage(1);
  }, [filters, search, sortKey, sortDirection]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  useEffect(() => {
    const available = new Set(rankedRows.map((row) => row.variant_index));
    const first = bestRows[0]?.variant_index ?? rankedRows[0]?.variant_index ?? null;
    const last = worstRows[0]?.variant_index ?? rankedRows.at(-1)?.variant_index ?? null;
    if (selectedVariant === null || !available.has(selectedVariant)) {
      setSelectedVariant(first);
    }
    if (comparisonA === null || !available.has(comparisonA)) {
      setComparisonA(first);
    }
    if (comparisonB === null || !available.has(comparisonB)) {
      setComparisonB(last);
    }
  }, [rankedRows, bestRows, worstRows, selectedVariant, comparisonA, comparisonB]);

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

  const weekOptions = data.weeks.map((week) => ({
    value: week.week_start,
    label: `${formatDate(week.week_start)} to ${formatDate(week.week_end)} (${formatNumber(week.variant_count)} variants)`,
  }));
  const selectedWeek = data.weeks.find(
    (week) => week.week_start === filters.weekStart,
  );
  const weightTotal = data.scoring.position_weights.reduce(
    (total, value) => total + value,
    0,
  );

  return (
    <div className="app-shell">
      <header className="site-header">
        <div>
          <p className="eyebrow">Static timetable analysis</p>
          <h1>APU Timetable Analyzer</h1>
          <p>
            Snapshot collected {formatDateTime(data.snapshot.collected_at)}, covering {" "}
            {formatDate(data.snapshot.minimum_event_date)} to {" "}
            {formatDate(data.snapshot.maximum_event_date)}.
          </p>
        </div>
        <dl className="snapshot-stats">
          <div>
            <dt>Intakes</dt>
            <dd>{formatNumber(data.snapshot.active_intake_count)}</dd>
          </div>
          <div>
            <dt>Variants</dt>
            <dd>{formatNumber(data.snapshot.variant_count)}</dd>
          </div>
          <div>
            <dt>Weeks</dt>
            <dd>{data.snapshot.week_count}</dd>
          </div>
        </dl>
      </header>

      <main>
        <section className="panel" aria-labelledby="filters-heading">
          <div className="section-heading">
            <div>
              <h2 id="filters-heading">Comparison set</h2>
              <p>Structured filters change the peers used to calculate every percentile.</p>
            </div>
            <button type="button" onClick={resetFilters}>
              Reset filters
            </button>
          </div>
          <div className="filter-grid">
            <SelectField
              label="Week"
              value={filters.weekStart}
              options={weekOptions}
              allLabel="Choose a week"
              onChange={(value) => updateFilter("weekStart", value)}
            />
            <SelectField
              label="Grouping"
              value={filters.grouping}
              options={data.filters.groupings.map((value) => ({ value, label: value }))}
              allLabel="All groups"
              onChange={(value) => updateFilter("grouping", value)}
            />
            <SelectField
              label="Programme level"
              value={filters.programmeLevel}
              options={data.filters.programme_levels.map((option) => ({
                value: option.code,
                label: nameOnlyOptionLabel(option),
              }))}
              allLabel="All programme levels"
              onChange={(value) => updateFilter("programmeLevel", value)}
            />
            <SelectField
              label="Programme route"
              value={filters.programmeRoute}
              options={data.filters.programme_routes.map((option) => ({
                value: option.code,
                label: optionLabel(option),
              }))}
              allLabel="All routes"
              onChange={(value) => updateFilter("programmeRoute", value)}
            />
            <SelectField
              label="Degree level"
              value={filters.academicLevel}
              options={data.filters.academic_levels.map((value) => ({
                value: String(value),
                label: `Level ${value}`,
              }))}
              allLabel="All degree levels"
              onChange={(value) => updateFilter("academicLevel", value)}
            />
            <SelectField
              label="Course"
              value={filters.courseCode}
              options={data.filters.courses.map((option) => ({
                value: option.code,
                label: courseOptionLabel(option),
              }))}
              allLabel="All courses"
              onChange={(value) => updateFilter("courseCode", value)}
            />
            <SelectField
              label="Specialism"
              value={filters.specialismCode}
              options={data.filters.specialisms.map((option) => ({
                value: option.code,
                label: nameOnlyOptionLabel(option),
              }))}
              allLabel="All specialisms"
              onChange={(value) => updateFilter("specialismCode", value)}
            />
            <SelectField
              label="School"
              value={filters.school}
              options={data.filters.schools.map((value) => ({ value, label: value }))}
              allLabel="All schools"
              disabled={data.filters.schools.length === 0}
              onChange={(value) => updateFilter("school", value)}
            />
            <SelectField
              label="Study mode"
              value={filters.studyMode}
              options={data.filters.study_modes.map((value) => ({ value, label: value }))}
              allLabel="All study modes"
              disabled={data.filters.study_modes.length === 0}
              onChange={(value) => updateFilter("studyMode", value)}
            />
            <SelectField
              label="Delivery mode"
              value={filters.deliveryMode}
              options={data.filters.delivery_modes.map((value) => ({ value, label: value }))}
              allLabel="Any delivery mode"
              onChange={(value) => updateFilter("deliveryMode", value)}
            />
          </div>
          <p className="status-line">
            {formatNumber(rankedRows.length)} variants in the current comparison set
            {selectedWeek ? `, from ${formatNumber(selectedWeek.intake_count)} scheduled intakes before filters` : ""}.
          </p>
        </section>

        <section className="panel" aria-labelledby="priorities-heading">
          <div className="section-heading">
            <div>
              <h2 id="priorities-heading">Frustration priority</h2>
              <p>Move criteria up or down. Higher positions receive more weight.</p>
            </div>
            <button
              type="button"
              onClick={() => setCriterionOrder(data.scoring.default_criterion_order)}
            >
              Reset order
            </button>
          </div>
          <ol className="priority-list" aria-label="Frustration priority order">
            {criterionOrder.map((criterion, index) => (
              <li key={criterion}>
                <span className="priority-number">{index + 1}</span>
                <span className="priority-label">
                  <strong>{CRITERION_DETAILS[criterion].label}</strong>
                  <small>
                    Weight {" "}
                    {((data.scoring.position_weights[index] / weightTotal) * 100).toFixed(1)}%
                  </small>
                </span>
                <span className="priority-actions">
                  <button
                    type="button"
                    disabled={index === 0}
                    aria-label={`Move ${CRITERION_DETAILS[criterion].label} up`}
                    onClick={() => moveCriterion(index, -1)}
                  >
                    Up
                  </button>
                  <button
                    type="button"
                    disabled={index === criterionOrder.length - 1}
                    aria-label={`Move ${CRITERION_DETAILS[criterion].label} down`}
                    onClick={() => moveCriterion(index, 1)}
                  >
                    Down
                  </button>
                </span>
              </li>
            ))}
          </ol>
        </section>

        <section className="results-grid" aria-label="Ranking highlights">
          <ResultCard title="Best" rows={bestRows} onSelect={setSelectedVariant} />
          <ResultCard title="Worst" rows={worstRows} onSelect={setSelectedVariant} />
          <ResultCard
            title="Most average"
            rows={averageRows}
            onSelect={setSelectedVariant}
          />
        </section>

        <section className="panel" aria-labelledby="rankings-heading">
          <div className="section-heading ranking-heading">
            <div>
              <h2 id="rankings-heading">Ranking table</h2>
              <p>
                Search narrows the visible table but does not change the peer calculation.
              </p>
            </div>
            <label className="field search-field">
              <span>Search intake code</span>
              <input
                type="search"
                value={search}
                placeholder="For example, APD3F2605CS(DA)"
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
          </div>
          {rankedRows.length === 0 ? (
            <div className="empty-state">
              <h3>No comparable timetables</h3>
              <p>Change or reset the structured filters.</p>
            </div>
          ) : (
            <>
              <p className="status-line">
                Showing {sortedRows.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1} to {" "}
                {Math.min(page * PAGE_SIZE, sortedRows.length)} of {formatNumber(sortedRows.length)} visible rows. Scores use {formatNumber(rankedRows.length)} peers.
              </p>
              <div className="table-scroll">
                <table className="ranking-table">
                  <thead>
                    <tr>
                      <SortableHeader label="Rank" sortKey="rank" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Intake" sortKey="intake" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Group" sortKey="grouping" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <th>Electives</th>
                      <SortableHeader label="Score" sortKey="score" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Gaps" sortKey="gap" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Late" sortKey="late" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Early" sortKey="early" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="One hour" sortKey="oneHour" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Overload" sortKey="overloaded" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Teaching" sortKey="teaching" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <SortableHeader label="Days" sortKey="activeDays" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((row) => (
                      <tr
                        className={
                          row.variant_index === selected?.variant_index ? "selected-row" : ""
                        }
                        key={row.variant_index}
                      >
                        <td>{row.recalculatedBestRank}</td>
                        <th>{row.intake_code}</th>
                        <td>{row.grouping}</td>
                        <td>{
                          row.elective_status === "resolved" || row.elective_status === "fixed"
                            ? row.elective_profile_name
                            : row.elective_status.replaceAll("_", " ")
                        }</td>
                        <td>{formatScore(row.recalculatedScore)}</td>
                        <td>{formatMinutes(row.total_gap_minutes)}</td>
                        <td>{row.late_only_days}</td>
                        <td>{row.early_only_days}</td>
                        <td>{row.one_hour_only_days}</td>
                        <td>{row.overloaded_days}</td>
                        <td>{formatMinutes(row.total_teaching_minutes)}</td>
                        <td>{row.active_days}</td>
                        <td className="table-actions">
                          <button type="button" onClick={() => setSelectedVariant(row.variant_index)}>
                            Inspect
                          </button>
                          <button type="button" onClick={() => setComparisonA(row.variant_index)}>
                            Set A
                          </button>
                          <button type="button" onClick={() => setComparisonB(row.variant_index)}>
                            Set B
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <nav className="pagination" aria-label="Ranking table pages">
                <button type="button" disabled={page === 1} onClick={() => setPage((current) => current - 1)}>
                  Previous
                </button>
                <span>Page {page} of {pageCount}</span>
                <button type="button" disabled={page === pageCount} onClick={() => setPage((current) => current + 1)}>
                  Next
                </button>
              </nav>
            </>
          )}
        </section>

        {selected && (
          <section className="panel" aria-labelledby="details-heading">
            <div className="section-heading">
              <div>
                <h2 id="details-heading">Score explanation: {variantLabel(selected)}</h2>
                <p>
                  Rank {selected.recalculatedBestRank} of {selected.peerCount}, score {formatScore(selected.recalculatedScore)}.
                </p>
              </div>
              <div className="button-row">
                <button type="button" onClick={() => setComparisonA(selected.variant_index)}>
                  Use as A
                </button>
                <button type="button" onClick={() => setComparisonB(selected.variant_index)}>
                  Use as B
                </button>
              </div>
            </div>
            <dl className="metadata-list">
              <div><dt>Course</dt><dd>{selectedIntake?.course_name ?? selectedIntake?.course_code ?? "Unknown"}</dd></div>
              <div><dt>Specialism</dt><dd>{selectedIntake?.specialism_name ?? selectedIntake?.specialism_code ?? "None identified"}</dd></div>
              <div><dt>Programme level</dt><dd>{selectedIntake?.programme_level_name ?? "Unknown"}</dd></div>
              <div><dt>Degree level</dt><dd>{selectedIntake?.academic_level ?? "Not applicable"}</dd></div>
              <div><dt>Route</dt><dd>{selectedIntake?.programme_route_name ?? selectedIntake?.programme_route ?? "Unknown"}</dd></div>
              <div><dt>Electives</dt><dd>{selected.elective_profile_name}</dd></div>
              <div><dt>Elective status</dt><dd>{selected.elective_status.replaceAll("_", " ")}</dd></div>
              <div><dt>Active days</dt><dd>{selected.active_days}</dd></div>
              <div><dt>Teaching</dt><dd>{formatMinutes(selected.total_teaching_minutes)}</dd></div>
            </dl>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>Criterion</th>
                    <th>Raw value</th>
                    <th>Peer percentile</th>
                    <th>Weight</th>
                    <th>Contribution</th>
                  </tr>
                </thead>
                <tbody>
                  {criterionOrder.map((criterion, index) => {
                    const component = selected.components[criterion];
                    const detail = CRITERION_DETAILS[criterion];
                    return (
                      <tr key={criterion}>
                        <td>{index + 1}</td>
                        <th>{detail.label}</th>
                        <td>{component.raw} {detail.unit}</td>
                        <td>{formatScore(component.percentile)}</td>
                        <td>{(component.weight * 100).toFixed(1)}%</td>
                        <td>{formatScore(component.contribution)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {selected && (
          <SchedulePanel
            row={selected}
            days={dailyByVariant.get(selected.variant_index) ?? []}
            blocks={blocksByVariant.get(selected.variant_index) ?? []}
          />
        )}

        <ComparisonPanel
          rows={rankedRows}
          comparisonA={comparisonA}
          comparisonB={comparisonB}
          onChangeA={setComparisonA}
          onChangeB={setComparisonB}
        />

        <section className="panel notes-panel" aria-labelledby="notes-heading">
          <h2 id="notes-heading">MVP notes</h2>
          <ul>
            <li>Rankings are recalculated in this browser after structured filters and priority changes.</li>
            <li>Online-only days do not receive commute-related early, late, or one-hour flags.</li>
            <li>Historical comparisons are not shown because Stage 7 is postponed and only one retained snapshot is available.</li>
            <li>School mappings currently cover the programmes listed in the July 2026 computing brochure.</li>
            <li>Older curriculum versions remain marked as unresolved until a matching official source is added.</li>
          </ul>
        </section>
      </main>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadDashboardData(controller.signal)
      .then(setData)
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "Unknown loading error.");
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <main className="load-state error-state">
        <h1>Could not load the dashboard</h1>
        <p>{error}</p>
        <p>Generate the data with `python scripts/build_dashboard_data.py` and reload.</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="load-state">
        <h1>Loading APU timetable data</h1>
        <p>The first load parses the static timetable snapshot in your browser.</p>
      </main>
    );
  }
  return <Dashboard data={data} />;
}
