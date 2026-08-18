import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import { BrandIcon } from "./BrandIcon";
import {
  rankVariants,
  summarizeRankPosition,
  type RankedVariant,
} from "./ranking";
import { VerticalTimetable } from "./VerticalTimetable";
import type {
  CriterionKey,
  DailyMetric,
  DashboardData,
  IntakeMetadata,
  TimetableBlock,
  WeeklyMetric,
} from "./types";

interface IntakeMatch {
  intake: IntakeMetadata;
  score: number;
  kind: "Exact match" | "Strong match" | "Close match" | "Possible match";
}

interface PrioritySnapshot {
  criteria: CriterionKey[];
  equalWeight: boolean;
  useDefaults: boolean;
}

interface PointerDrag {
  criterion: CriterionKey;
  pointerId: number;
  left: number;
  top: number;
  width: number;
  offsetX: number;
  offsetY: number;
  dropIndex: number;
}

export type ComparisonScope = "similar" | "level" | "all";
type Theme = "light" | "dark";

const JOURNEY = [
  { short: "Find", label: "Find intake" },
  { short: "Config", label: "Configure" },
  { short: "Priorities", label: "Priorities" },
  { short: "Compare", label: "Compare" },
  { short: "Result", label: "Result" },
] as const;

const CRITERION_COPY: Record<
  CriterionKey,
  { title: string; description: string; tone: string }
> = {
  gap_burden: {
    title: "Long gaps between classes",
    description: "Waiting on campus between one class and the next.",
    tone: "gap",
  },
  late_only: {
    title: "Late-only campus days",
    description: "Travelling in only for classes that finish late.",
    tone: "late",
  },
  early_only: {
    title: "Early-only campus days",
    description: "Starting early when there is nothing else that day.",
    tone: "early",
  },
  one_hour_only: {
    title: "One-hour-only campus trips",
    description: "Making the commute for very little teaching time.",
    tone: "short",
  },
  overloaded: {
    title: "Overloaded days",
    description: "Too many teaching hours packed into one day.",
    tone: "overload",
  },
};

const NO_ELECTIVE_STATUSES = new Set(["no_electives", "not_active"]);
const MAX_PRIORITY_HISTORY = 30;

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-MY").format(value);
}

function formatMinutes(value: number): string {
  if (value < 60) return `${value} min`;
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return minutes === 0 ? `${hours} hr` : `${hours} hr ${minutes} min`;
}

function formatScore(value: number): string {
  return value.toFixed(2);
}

function formatCriterionValue(criterion: CriterionKey, value: number): string {
  if (criterion === "gap_burden") return formatMinutes(value);
  return `${value} ${value === 1 ? "day" : "days"}`;
}

function formatWeekDate(value: string): string {
  return new Intl.DateTimeFormat("en-MY", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function chooseCurrentWeek(data: DashboardData): string {
  const todayParts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone: data.timezone,
    })
      .formatToParts(new Date())
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const todayIso = `${todayParts.year}-${todayParts.month}-${todayParts.day}`;
  return (
    data.weeks.find(
      (week) => week.week_start <= todayIso && todayIso <= week.week_end,
    )?.week_start ?? data.weeks[0]?.week_start ?? ""
  );
}

function bestWeekForIntake(
  intake: IntakeMetadata,
  preferredWeek: string,
): string {
  const weeks = [...intake.week_starts].sort();
  if (weeks.includes(preferredWeek)) return preferredWeek;
  return weeks.find((week) => week >= preferredWeek) ?? weeks.at(-1) ?? preferredWeek;
}

function programmeTitle(intake: IntakeMetadata): string {
  const course = intake.course_name ?? intake.course_code ?? intake.intake_code;
  return intake.specialism_name
    ? `${course} with a specialism in ${intake.specialism_name}`
    : course;
}

function programmeMeta(intake: IntakeMetadata): string {
  const parts = [intake.programme_level_name];
  if (intake.academic_level !== null) parts.push(`Year ${intake.academic_level}`);
  if (intake.programme_route_name) parts.push(intake.programme_route_name);
  return parts.filter(Boolean).join(", ");
}

export function normalizeIntakeSearch(value: string): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function damerauLevenshtein(left: string, right: string): number {
  const rows = left.length + 1;
  const columns = right.length + 1;
  const matrix = Array.from({ length: rows }, () => Array<number>(columns).fill(0));

  for (let row = 0; row < rows; row += 1) matrix[row][0] = row;
  for (let column = 0; column < columns; column += 1) matrix[0][column] = column;

  for (let row = 1; row < rows; row += 1) {
    for (let column = 1; column < columns; column += 1) {
      const substitutionCost = left[row - 1] === right[column - 1] ? 0 : 1;
      matrix[row][column] = Math.min(
        matrix[row - 1][column] + 1,
        matrix[row][column - 1] + 1,
        matrix[row - 1][column - 1] + substitutionCost,
      );
      if (
        row > 1 &&
        column > 1 &&
        left[row - 1] === right[column - 2] &&
        left[row - 2] === right[column - 1]
      ) {
        matrix[row][column] = Math.min(
          matrix[row][column],
          matrix[row - 2][column - 2] + 1,
        );
      }
    }
  }
  return matrix[left.length][right.length];
}

function intakeMatchScore(
  intake: IntakeMetadata,
  rawQuery: string,
  preferredWeek: string,
): number {
  const query = normalizeIntakeSearch(rawQuery);
  const code = normalizeIntakeSearch(intake.intake_code);
  if (!query) return Number.POSITIVE_INFINITY;
  if (code === query) return 0;

  let score = Number.POSITIVE_INFINITY;
  if (code.startsWith(query)) {
    score = 0.8 + (code.length - query.length) * 0.04;
  }
  const containedAt = code.indexOf(query);
  if (containedAt >= 0) score = Math.min(score, 2.2 + containedAt * 0.35);

  const comparablePrefix = code.slice(0, Math.min(code.length, query.length));
  const prefixDistance = damerauLevenshtein(query, comparablePrefix);
  const fullDistance = damerauLevenshtein(query, code);
  score = Math.min(
    score,
    3.5 + prefixDistance * 2.15 + Math.abs(code.length - query.length) * 0.06,
    7 + fullDistance * 1.35,
  );

  const metadata = [
    intake.course_name,
    intake.specialism_name,
    intake.programme_level_name,
    intake.school,
  ]
    .filter(Boolean)
    .join(" ")
    .toUpperCase();
  if (rawQuery.trim().length >= 3 && metadata.includes(rawQuery.trim().toUpperCase())) {
    score = Math.min(score, 5.5);
  }
  if (!intake.week_starts.includes(preferredWeek)) score += 0.35;
  return score;
}

export function rankIntakeMatches(
  intakes: IntakeMetadata[],
  query: string,
  preferredWeek: string,
  limit = 6,
): IntakeMatch[] {
  const normalized = normalizeIntakeSearch(query);
  if (!normalized && query.trim().length < 3) return [];
  const threshold = Math.max(8, normalized.length * 1.45 + 4);
  return intakes
    .map((intake) => ({
      intake,
      score: intakeMatchScore(intake, query, preferredWeek),
    }))
    .filter(({ score }) => score <= threshold)
    .sort(
      (left, right) =>
        left.score - right.score ||
        left.intake.intake_code.length - right.intake.intake_code.length ||
        left.intake.intake_code.localeCompare(right.intake.intake_code),
    )
    .slice(0, limit)
    .map(({ intake, score }) => ({
      intake,
      score,
      kind:
        score === 0
          ? "Exact match"
          : score < 1.8
            ? "Strong match"
            : score < 7
              ? "Close match"
              : "Possible match",
    }));
}

function prioritySnapshotEqual(
  left: PrioritySnapshot,
  right: PrioritySnapshot,
): boolean {
  return (
    left.equalWeight === right.equalWeight &&
    left.useDefaults === right.useDefaults &&
    left.criteria.join("|") === right.criteria.join("|")
  );
}

function isResolvedElective(row: WeeklyMetric): boolean {
  return row.elective_status === "resolved" || row.elective_status === "fixed";
}

export function filterCheckerComparisonRows(
  rowsForWeek: WeeklyMetric[],
  intakeByCode: Map<string, IntakeMetadata>,
  selectedIntake: IntakeMetadata | null,
  scope: ComparisonScope,
  sameSchool: boolean,
): WeeklyMetric[] {
  if (!selectedIntake) return rowsForWeek;

  return rowsForWeek.filter((row) => {
    const intake = intakeByCode.get(row.intake_code);
    if (!intake) return false;
    if (
      scope === "similar" &&
      (intake.programme_level !== selectedIntake.programme_level ||
        intake.academic_level !== selectedIntake.academic_level)
    ) {
      return false;
    }
    if (
      scope === "level" &&
      intake.programme_level !== selectedIntake.programme_level
    ) {
      return false;
    }
    if (
      sameSchool &&
      selectedIntake.school &&
      intake.school !== selectedIntake.school
    ) {
      return false;
    }
    return true;
  });
}

function configLabel(row: WeeklyMetric): string {
  return isResolvedElective(row)
    ? row.elective_profile_name
    : row.elective_status.replaceAll("_", " ");
}

export function CampusNotebook({
  data,
  onOpenDashboard,
}: {
  data: DashboardData;
  onOpenDashboard: () => void;
}) {
  const defaultWeek = useMemo(() => chooseCurrentWeek(data), [data]);
  const defaultCriteria = useMemo(
    () => [...data.scoring.default_criterion_order],
    [data.scoring.default_criterion_order],
  );
  const [theme, setTheme] = useState<Theme>("light");
  const [step, setStep] = useState(0);
  const [furthestStep, setFurthestStep] = useState(0);
  const [query, setQuery] = useState("");
  const [selectedIntakeCode, setSelectedIntakeCode] = useState<string | null>(
    null,
  );
  const [highlightedSuggestion, setHighlightedSuggestion] = useState(0);
  const [selectedWeek, setSelectedWeek] = useState(defaultWeek);
  const [selectedGrouping, setSelectedGrouping] = useState<string | null>(null);
  const [selectedElective, setSelectedElective] = useState<string | null>(null);
  const [criteria, setCriteria] = useState<CriterionKey[]>(defaultCriteria);
  const [equalWeight, setEqualWeight] = useState(false);
  const [useDefaults, setUseDefaults] = useState(true);
  const [priorityHistory, setPriorityHistory] = useState<PrioritySnapshot[]>([]);
  const [drag, setDrag] = useState<PointerDrag | null>(null);
  const dragRef = useRef<PointerDrag | null>(null);
  const priorityListRef = useRef<HTMLOListElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const [scope, setScope] = useState<ComparisonScope>("all");
  const [sameSchool, setSameSchool] = useState(false);
  const [toast, setToast] = useState("");
  const toastTimerRef = useRef<number | null>(null);

  const intakeByCode = useMemo(
    () => new Map(data.intakes.map((intake) => [intake.intake_code, intake])),
    [data.intakes],
  );
  const selectedIntake = selectedIntakeCode
    ? intakeByCode.get(selectedIntakeCode) ?? null
    : null;
  const suggestions = useMemo(
    () => rankIntakeMatches(data.intakes, query, defaultWeek),
    [data.intakes, query, defaultWeek],
  );

  const intakeVariants = useMemo(
    () =>
      selectedIntakeCode
        ? data.weeklyMetrics.filter(
            (row) =>
              row.intake_code === selectedIntakeCode &&
              row.week_start === selectedWeek,
          )
        : [],
    [data.weeklyMetrics, selectedIntakeCode, selectedWeek],
  );
  const groupings = useMemo(
    () => [...new Set(intakeVariants.map((row) => row.grouping))].sort(),
    [intakeVariants],
  );
  const groupVariants = useMemo(
    () =>
      intakeVariants.filter(
        (row) => row.grouping === (selectedGrouping ?? groupings[0]),
      ),
    [intakeVariants, selectedGrouping, groupings],
  );
  const selectedVariant =
    groupVariants.find((row) => row.elective_profile === selectedElective) ??
    groupVariants[0] ??
    null;

  const dailyByVariant = useMemo(() => {
    const grouped = new Map<number, DailyMetric[]>();
    for (const day of data.dailyMetrics) {
      const current = grouped.get(day.variant_index) ?? [];
      current.push(day);
      grouped.set(day.variant_index, current);
    }
    return grouped;
  }, [data.dailyMetrics]);
  const blocksByVariant = useMemo(() => {
    const grouped = new Map<number, TimetableBlock[]>();
    for (const block of data.timetableBlocks) {
      const current = grouped.get(block.variant_index) ?? [];
      current.push(block);
      grouped.set(block.variant_index, current);
    }
    return grouped;
  }, [data.timetableBlocks]);

  const rowsForWeek = useMemo(
    () => data.weeklyMetrics.filter((row) => row.week_start === selectedWeek),
    [data.weeklyMetrics, selectedWeek],
  );
  const peerRows = useMemo(() => {
    return filterCheckerComparisonRows(
      rowsForWeek,
      intakeByCode,
      selectedIntake,
      scope,
      sameSchool,
    );
  }, [rowsForWeek, selectedIntake, intakeByCode, scope, sameSchool]);
  const activeWeights = useMemo(
    () =>
      equalWeight
        ? criteria.map(() => 1)
        : data.scoring.position_weights.slice(0, criteria.length),
    [criteria, equalWeight, data.scoring.position_weights],
  );
  const rankedRows = useMemo(
    () => rankVariants(peerRows, criteria, activeWeights),
    [peerRows, criteria, activeWeights],
  );
  const resultRow = selectedVariant
    ? rankedRows.find(
        (row) => row.variant_index === selectedVariant.variant_index,
      ) ?? null
    : null;

  const scopeCounts = useMemo(() => {
    if (!selectedIntake) return { similar: 0, level: 0, all: rowsForWeek.length };
    const countFor = (target: ComparisonScope) =>
      filterCheckerComparisonRows(
        rowsForWeek,
        intakeByCode,
        selectedIntake,
        target,
        sameSchool,
      ).length;
    return {
      similar: countFor("similar"),
      level: countFor("level"),
      all: countFor("all"),
    };
  }, [rowsForWeek, selectedIntake, intakeByCode, sameSchool]);

  useEffect(() => {
    if (highlightedSuggestion >= suggestions.length) {
      setHighlightedSuggestion(Math.max(0, suggestions.length - 1));
    }
  }, [highlightedSuggestion, suggestions.length]);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    };
  }, []);

  function showToast(message: string) {
    setToast(message);
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => setToast(""), 2400);
  }

  function updateConfiguration(
    intakeCode: string,
    weekStart: string,
    preferredGrouping?: string,
  ) {
    const variants = data.weeklyMetrics.filter(
      (row) => row.intake_code === intakeCode && row.week_start === weekStart,
    );
    const grouping = variants.some((row) => row.grouping === preferredGrouping)
      ? preferredGrouping ?? variants[0]?.grouping ?? null
      : variants[0]?.grouping ?? null;
    const first = variants.find((row) => row.grouping === grouping) ?? variants[0];
    setSelectedGrouping(grouping);
    setSelectedElective(first?.elective_profile ?? null);
  }

  function selectIntake(intake: IntakeMetadata) {
    const week = bestWeekForIntake(intake, defaultWeek);
    setSelectedIntakeCode(intake.intake_code);
    setQuery(intake.intake_code);
    setSelectedWeek(week);
    updateConfiguration(intake.intake_code, week);
    setFurthestStep((current) => Math.max(current, 1));
    setHighlightedSuggestion(0);
    showToast(`${intake.intake_code} selected. Press Enter again to continue.`);
    window.requestAnimationFrame(() => searchInputRef.current?.focus());
  }

  function goToStep(target: number) {
    if (target > furthestStep) {
      showToast("Finish the current task before moving ahead.");
      return;
    }
    setStep(Math.max(0, Math.min(4, target)));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function continueForward() {
    if (step === 0 && !selectedIntake) {
      showToast("Choose an intake before continuing.");
      return;
    }
    if (step === 1 && !selectedVariant) {
      showToast("No timetable configuration is available for this week.");
      return;
    }
    const next = Math.min(4, step + 1);
    setFurthestStep((current) => Math.max(current, next));
    setStep(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function commitSearch() {
    if (
      selectedIntake &&
      normalizeIntakeSearch(query) ===
        normalizeIntakeSearch(selectedIntake.intake_code)
    ) {
      continueForward();
      return;
    }
    const match = suggestions[highlightedSuggestion] ?? suggestions[0];
    if (match) selectIntake(match.intake);
    else showToast("No close intake match was found. Try a shorter code.");
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlightedSuggestion((current) =>
        suggestions.length ? (current + 1) % suggestions.length : 0,
      );
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlightedSuggestion((current) =>
        suggestions.length
          ? (current - 1 + suggestions.length) % suggestions.length
          : 0,
      );
    } else if (event.key === "Enter") {
      event.preventDefault();
      commitSearch();
    } else if (event.key === "Escape") {
      setHighlightedSuggestion(0);
    }
  }

  function currentPrioritySnapshot(): PrioritySnapshot {
    return {
      criteria: [...criteria],
      equalWeight,
      useDefaults,
    };
  }

  function commitPriority(next: PrioritySnapshot) {
    const current = currentPrioritySnapshot();
    if (prioritySnapshotEqual(current, next)) return;
    setPriorityHistory((history) =>
      [...history, current].slice(-MAX_PRIORITY_HISTORY),
    );
    setCriteria([...next.criteria]);
    setEqualWeight(next.equalWeight);
    setUseDefaults(next.useDefaults);
  }

  function moveCriterion(criterion: CriterionKey, direction: -1 | 1) {
    const index = criteria.indexOf(criterion);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= criteria.length || equalWeight) return;
    const next = [...criteria];
    [next[index], next[target]] = [next[target], next[index]];
    commitPriority({ criteria: next, equalWeight: false, useDefaults: false });
  }

  function removeCriterion(criterion: CriterionKey) {
    commitPriority({
      criteria: criteria.filter((item) => item !== criterion),
      equalWeight,
      useDefaults: false,
    });
    showToast(`${CRITERION_COPY[criterion].title} removed.`);
  }

  function undoPriority() {
    const previous = priorityHistory.at(-1);
    if (!previous) return;
    setPriorityHistory((history) => history.slice(0, -1));
    setCriteria([...previous.criteria]);
    setEqualWeight(previous.equalWeight);
    setUseDefaults(previous.useDefaults);
    showToast("Last priority change undone.");
  }

  function restoreDefaults(checked: boolean) {
    if (!checked) {
      commitPriority({ criteria, equalWeight, useDefaults: false });
      return;
    }
    commitPriority({
      criteria: [...defaultCriteria],
      equalWeight: false,
      useDefaults: true,
    });
  }

  function beginPointerDrag(
    event: ReactPointerEvent<HTMLLIElement>,
    criterion: CriterionKey,
  ) {
    const target = event.target as Element;
    if (
      equalWeight ||
      (event.pointerType === "mouse" && event.button !== 0) ||
      target.closest("button")
    ) {
      return;
    }
    event.preventDefault();
    const card = event.currentTarget;
    const rect = card.getBoundingClientRect();
    const initial: PointerDrag = {
      criterion,
      pointerId: event.pointerId,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      dropIndex: criteria.indexOf(criterion),
    };
    dragRef.current = initial;
    setDrag(initial);
    document.body.classList.add("tn-is-pointer-dragging");

    const handleMove = (pointerEvent: PointerEvent) => {
      const current = dragRef.current;
      if (!current || pointerEvent.pointerId !== current.pointerId) return;
      pointerEvent.preventDefault();
      const cards = Array.from(
        priorityListRef.current?.querySelectorAll<HTMLElement>(
          "[data-priority-card]:not([data-drag-placeholder])",
        ) ?? [],
      );
      let dropIndex = cards.length;
      for (let index = 0; index < cards.length; index += 1) {
        const candidate = cards[index].getBoundingClientRect();
        if (pointerEvent.clientY < candidate.top + candidate.height / 2) {
          dropIndex = index;
          break;
        }
      }
      const next = {
        ...current,
        left: pointerEvent.clientX - current.offsetX,
        top: pointerEvent.clientY - current.offsetY,
        dropIndex,
      };
      dragRef.current = next;
      setDrag(next);
      if (pointerEvent.clientY < 90) window.scrollBy({ top: -14 });
      if (pointerEvent.clientY > window.innerHeight - 70) {
        window.scrollBy({ top: 14 });
      }
    };

    const finishDrag = (pointerEvent: PointerEvent) => {
      const current = dragRef.current;
      if (!current || pointerEvent.pointerId !== current.pointerId) return;
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", cancelDrag);
      document.body.classList.remove("tn-is-pointer-dragging");
      const remaining = criteria.filter((item) => item !== current.criterion);
      remaining.splice(current.dropIndex, 0, current.criterion);
      dragRef.current = null;
      setDrag(null);
      commitPriority({
        criteria: remaining,
        equalWeight: false,
        useDefaults: false,
      });
    };

    const cancelDrag = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId !== dragRef.current?.pointerId) return;
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", cancelDrag);
      document.body.classList.remove("tn-is-pointer-dragging");
      dragRef.current = null;
      setDrag(null);
    };

    window.addEventListener("pointermove", handleMove, { passive: false });
    window.addEventListener("pointerup", finishDrag);
    window.addEventListener("pointercancel", cancelDrag);
  }

  function renderPriorityCard(
    criterion: CriterionKey,
    index: number,
    overlay = false,
  ) {
    const copy = CRITERION_COPY[criterion];
    const influence = equalWeight
      ? "Equal influence"
      : index === 0
        ? "Biggest influence"
        : index === criteria.length - 1
          ? "Smallest influence"
          : index === 1
            ? "High influence"
            : "Medium influence";
    return (
      <li
        className={`tn-priority-card tone-${copy.tone} ${overlay ? "is-drag-overlay" : ""}`}
        data-priority-card
        key={criterion}
        onPointerDown={
          overlay ? undefined : (event) => beginPointerDrag(event, criterion)
        }
      >
        <span className="tn-priority-number">{equalWeight ? "=" : index + 1}</span>
        <span className="tn-drag-handle" aria-hidden="true">
          <span>⠿</span>
          <small>DRAG</small>
        </span>
        <div className="tn-priority-copy">
          <strong>{copy.title}</strong>
          <p>{copy.description}</p>
          <span>{influence}</span>
        </div>
        {!overlay && (
          <div className="tn-priority-controls">
            <div className="tn-move-controls" aria-label={`Move ${copy.title}`}>
              <button
                type="button"
                disabled={index === 0 || equalWeight}
                aria-label={`Move ${copy.title} up`}
                onClick={() => moveCriterion(criterion, -1)}
              >
                ↑
              </button>
              <button
                type="button"
                disabled={index === criteria.length - 1 || equalWeight}
                aria-label={`Move ${copy.title} down`}
                onClick={() => moveCriterion(criterion, 1)}
              >
                ↓
              </button>
            </div>
            <button
              className="tn-remove-priority"
              type="button"
              aria-label={`Remove ${copy.title}`}
              onClick={() => removeCriterion(criterion)}
            >
              <span aria-hidden="true">×</span> Remove
            </button>
          </div>
        )}
      </li>
    );
  }

  const searchConfirmed = Boolean(
    selectedIntake &&
      normalizeIntakeSearch(query) ===
        normalizeIntakeSearch(selectedIntake.intake_code),
  );
  const meaningfulElectives = groupVariants.length > 1;
  const onlyVariant = groupVariants[0] ?? null;
  const noElectives = Boolean(
    onlyVariant &&
      groupVariants.length === 1 &&
      NO_ELECTIVE_STATUSES.has(onlyVariant.elective_status),
  );

  function renderFindStep() {
    return (
      <section className="tn-step-panel tn-find-step" aria-labelledby="tn-find-title">
        <header className="tn-step-intro">
          <p className="tn-kicker">Step 1/5</p>
          <h1 id="tn-find-title">Which intake are you in?</h1>
          <p>
            Enter your intake code to load your timetable. 
          </p>
        </header>
        <div className="tn-search-wrap">
          <label htmlFor="tn-intake-search">Search intake code</label>
          <div className="tn-search-field">
            <input
              id="tn-intake-search"
              ref={searchInputRef}
              type="text"
              role="combobox"
              autoComplete="off"
              aria-autocomplete="list"
              aria-controls="tn-intake-suggestions"
              aria-expanded={suggestions.length > 0}
              aria-activedescendant={
                suggestions.length
                  ? `tn-suggestion-${highlightedSuggestion}`
                  : undefined
              }
              value={query}
              placeholder="For example, APD3F2605CS(DA)"
              onChange={(event) => {
                const value = event.target.value;
                setQuery(value);
                setHighlightedSuggestion(0);
                if (
                  selectedIntake &&
                  normalizeIntakeSearch(value) !==
                    normalizeIntakeSearch(selectedIntake.intake_code)
                ) {
                  setSelectedIntakeCode(null);
                  setSelectedGrouping(null);
                  setSelectedElective(null);
                  setFurthestStep(0);
                }
              }}
              onKeyDown={handleSearchKeyDown}
            />
            <button type="button" onClick={commitSearch}>
              <span>{searchConfirmed ? "Continue" : "Enter"}</span>
              <kbd>↵</kbd>
            </button>
          </div>
          <p className="tn-search-help">
            Use ↑ and ↓ to move through suggestions. Press Enter once to select,
            then again to continue.
          </p>
        </div>
        <div className="tn-suggestion-heading">
          <span>Closest matches</span>
          {suggestions.length > 0 && <small>{suggestions.length} suggestions</small>}
        </div>
        <div
          className="tn-suggestions"
          id="tn-intake-suggestions"
          role="listbox"
          aria-label="Intake suggestions"
        >
          {query.trim().length === 0 ? (
            <div className="tn-suggestion-empty">
              <strong>Start typing your intake code.</strong>
              <span>Suggestions will be ranked by the strongest match.</span>
            </div>
          ) : suggestions.length === 0 ? (
            <div className="tn-suggestion-empty">
              <strong>No close match yet.</strong>
              <span>Try removing the last few characters or check the intake year.</span>
            </div>
          ) : (
            suggestions.map((match, index) => {
              const active = index === highlightedSuggestion;
              const selected = match.intake.intake_code === selectedIntakeCode;
              return (
                <button
                  className={`tn-suggestion ${active ? "is-active" : ""} ${selected ? "is-selected" : ""}`}
                  id={`tn-suggestion-${index}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  key={match.intake.intake_code}
                  onMouseEnter={() => setHighlightedSuggestion(index)}
                  onClick={() => selectIntake(match.intake)}
                >
                  <span className="tn-suggestion-code">
                    {match.intake.intake_code}
                  </span>
                  <span className="tn-suggestion-course">
                    {programmeTitle(match.intake)}
                  </span>
                  <span className="tn-suggestion-meta">
                    {programmeMeta(match.intake)}
                  </span>
                  <span className="tn-match-kind">
                    {selected ? "Selected" : match.kind}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </section>
    );
  }

  function renderConfigStep() {
    if (!selectedIntake) return renderFindStep();
    const intakeWeeks = data.weeks.filter((week) =>
      selectedIntake.week_starts.includes(week.week_start),
    );
    return (
      <section className="tn-step-panel tn-config-step" aria-labelledby="tn-config-title">
        <header className="tn-step-intro">
          <p className="tn-kicker">Step 2/5</p>
          <h1 id="tn-config-title">Which timetable should we use?</h1>
          <p>
            The intake code tells us your course and specialism. This page 
            tells us which group and electives you took. 
          </p>
        </header>
        <div className="tn-identity-strip">
          <div>
            <span>INTAKE</span>
            <strong>{selectedIntake.intake_code}</strong>
          </div>
          <div>
            <span>PROGRAMME</span>
            <strong>{programmeMeta(selectedIntake)}</strong>
          </div>
          <div>
            <span>SPECIALISM</span>
            <strong>{selectedIntake.specialism_name ?? "None for this intake"}</strong>
          </div>
        </div>

        <div className="tn-config-sections">
          <section className="tn-config-section">
            <div className="tn-config-heading">
              <h2>Which timetable week?</h2>
              <p>The current week is selected when it exists for this intake.</p>
            </div>
            {intakeWeeks.length <= 1 ? (
              <div className="tn-detected-only">
                <strong>Only one timetable week detected</strong>
                <span>{formatWeekDate(selectedWeek)}</span>
              </div>
            ) : (
              <div className="tn-choice-row">
                {intakeWeeks.map((week) => (
                  <button
                    className={week.week_start === selectedWeek ? "is-selected" : ""}
                    type="button"
                    key={week.week_start}
                    aria-pressed={week.week_start === selectedWeek}
                    onClick={() => {
                      setSelectedWeek(week.week_start);
                      updateConfiguration(selectedIntake.intake_code, week.week_start);
                    }}
                  >
                    {formatWeekDate(week.week_start)}
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="tn-config-section">
            <div className="tn-config-heading">
              <h2>Which group are you in?</h2>
              <p>Only groups recorded for this intake and week are shown.</p>
            </div>
            {groupings.length <= 1 ? (
              <div className="tn-detected-only">
                <strong>Only one group detected</strong>
                <span>{groupings[0] ?? "No group data available"}</span>
              </div>
            ) : (
              <div className="tn-choice-row">
                {groupings.map((grouping) => (
                  <button
                    className={grouping === selectedGrouping ? "is-selected" : ""}
                    type="button"
                    key={grouping}
                    aria-pressed={grouping === selectedGrouping}
                    onClick={() => {
                      setSelectedGrouping(grouping);
                      setSelectedElective(
                        intakeVariants.find((row) => row.grouping === grouping)
                          ?.elective_profile ?? null,
                      );
                    }}
                  >
                    {grouping}
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="tn-config-section">
            <div className="tn-config-heading">
              <h2>Which elective did you choose?</h2>
              <p>Elective choices stay within the selected group.</p>
            </div>
            {!meaningfulElectives ? (
              <div className="tn-detected-only">
                <strong>
                  {noElectives ? "No electives detected" : "Only one elective route detected"}
                </strong>
                <span>
                  {noElectives
                    ? "No elective choice is needed for this timetable."
                    : onlyVariant
                      ? configLabel(onlyVariant)
                      : "No elective data available"}
                </span>
              </div>
            ) : (
              <div className="tn-elective-grid">
                {groupVariants.map((variant) => (
                  <button
                    className={
                      variant.elective_profile === selectedElective
                        ? "is-selected"
                        : ""
                    }
                    type="button"
                    key={variant.elective_profile}
                    aria-pressed={variant.elective_profile === selectedElective}
                    onClick={() => setSelectedElective(variant.elective_profile)}
                  >
                    <span>{variant.elective_profile_name}</span>
                    {variant.elective_profile === selectedElective && (
                      <small>Selected</small>
                    )}
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>
      </section>
    );
  }

  function renderPriorityStep() {
    const remaining = drag
      ? criteria.filter((criterion) => criterion !== drag.criterion)
      : criteria;
    const rendered: ReactNode[] = [];
    remaining.forEach((criterion, index) => {
      if (drag && drag.dropIndex === index) {
        rendered.push(
          <li
            className="tn-priority-slot"
            data-drag-placeholder
            key="drag-placeholder"
          >
            <span>Drop at priority {index + 1}</span>
          </li>,
        );
      }
      const originalIndex = criteria.indexOf(criterion);
      rendered.push(renderPriorityCard(criterion, originalIndex));
    });
    if (drag && drag.dropIndex >= remaining.length) {
      rendered.push(
        <li
          className="tn-priority-slot"
          data-drag-placeholder
          key="drag-placeholder-end"
        >
          <span>Drop at priority {remaining.length + 1}</span>
        </li>,
      );
    }

    return (
      <section className="tn-step-panel tn-priority-step" aria-labelledby="tn-priority-title">
        <header className="tn-step-intro tn-priority-intro">
          <div>
            <p className="tn-kicker">Step 3/5</p>
            <h1 id="tn-priority-title">What bothers you most?</h1>
            <p>
              The default settings takes into account all available frustration points, 
              but the resulting rank might not look accurate. It is advisable remove a few
              and rerank the rest. 
            </p>
          </div>
          <button
            className="tn-undo-button"
            type="button"
            disabled={priorityHistory.length === 0}
            onClick={undoPriority}
          >
            <span aria-hidden="true">↶</span> Undo
          </button>
        </header>

        <div className="tn-priority-options">
          <label>
            <input
              type="checkbox"
              checked={equalWeight}
              onChange={(event) =>
                commitPriority({
                  criteria,
                  equalWeight: event.target.checked,
                  useDefaults: false,
                })
              }
            />
            <span className="tn-checkbox" aria-hidden="true" />
            <span>
              <strong>Treat everything equally</strong>
              <small>Every remaining frustration has the same influence.</small>
            </span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={useDefaults}
              onChange={(event) => restoreDefaults(event.target.checked)}
            />
            <span className="tn-checkbox" aria-hidden="true" />
            <span>
              <strong>Use default settings</strong>
              <small>Restore all five frustrations and the standard order.</small>
            </span>
          </label>
        </div>

        <div className="tn-live-frustration" role="status" aria-live="polite">
          <span className="tn-live-mark" aria-hidden="true">
            {criteria.length === 0 ? "0" : equalWeight ? "=" : "1"}
          </span>
          <div>
            <span>LIVE SUMMARY</span>
            <strong>
              {criteria.length === 0
                ? "No frustrations will affect your result."
                : equalWeight
                  ? `All ${criteria.length} remaining frustrations count equally.`
                  : `Your biggest frustration: ${CRITERION_COPY[criteria[0]].title}`}
            </strong>
          </div>
        </div>

        {criteria.length === 0 ? (
          <div className="tn-priority-empty">
            <strong>Ranking is turned off.</strong>
            <p>You can continue to a timetable-only result or restore the defaults.</p>
            <button type="button" onClick={() => restoreDefaults(true)}>
              Restore default settings
            </button>
          </div>
        ) : (
          <>
            <div className="tn-stack-end is-top">
              <span>Most frustrating</span>
              <small>Top of the stack</small>
            </div>
            <ol
              className={`tn-priority-list ${equalWeight ? "is-equal" : ""}`}
              ref={priorityListRef}
              aria-label="Frustration priority order"
            >
              {rendered}
            </ol>
            <div className="tn-stack-end is-bottom">
              <span>Least frustrating</span>
              <small>Bottom of the stack</small>
            </div>
          </>
        )}

      </section>
    );
  }

  function renderCompareStep() {
    if (!selectedIntake) return renderFindStep();
    const scopeOptions: Array<{
      id: ComparisonScope;
      title: string;
      description: string;
      count: number;
    }> = [
      {
        id: "similar",
        title: "Students like me",
        description: `All ${selectedIntake.programme_level_name.toLowerCase()} timetables in ${selectedIntake.academic_level === null ? "the same academic stage" : `Year ${selectedIntake.academic_level}`}.`,
        count: scopeCounts.similar,
      },
      {
        id: "level",
        title: `All ${selectedIntake.programme_level_name.toLowerCase()} intakes`,
        description: `Every academic year and school at a ${selectedIntake.programme_level_name.toLowerCase()} level.`,
        count: scopeCounts.level,
      },
      {
        id: "all",
        title: "Everyone",
        description: "Every timetable available.",
        count: scopeCounts.all,
      },
    ];
    return (
      <section className="tn-step-panel tn-compare-step" aria-labelledby="tn-compare-title">
        <header className="tn-step-intro">
          <p className="tn-kicker">Step 4/5</p>
          <h1 id="tn-compare-title">Who should we compare you with?</h1>
          <p>
            Choose which other timetables we should compare yours to.  
          </p>
        </header>

        <fieldset className="tn-scope-fieldset">
          <legend>Comparison group</legend>
          <div className="tn-scope-options">
            {scopeOptions.map((option) => (
              <button
                className={scope === option.id ? "is-selected" : ""}
                type="button"
                key={option.id}
                aria-pressed={scope === option.id}
                onClick={() => setScope(option.id)}
              >
                <span className="tn-radio" aria-hidden="true" />
                <span className="tn-scope-copy">
                  <strong>{option.title}</strong>
                  <span>{option.description}</span>
                </span>
                <small>{formatNumber(option.count)} timetables</small>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="tn-refine-fieldset">
          <legend>Small refinements</legend>
          <p>Apply one final limit inside the comparison group above.</p>
          <label className={!selectedIntake.school ? "is-disabled" : ""}>
            <input
              type="checkbox"
              checked={sameSchool}
              disabled={!selectedIntake.school}
              onChange={(event) => setSameSchool(event.target.checked)}
            />
            <span className="tn-checkbox" aria-hidden="true" />
            <span>
              <strong>Stay within my school</strong>
              <small>
                {selectedIntake.school ?? "No school mapping is available for this intake."}
              </small>
            </span>
          </label>
        </fieldset>
      </section>
    );
  }

  function renderResultStep() {
    if (!selectedIntake || !selectedVariant || !resultRow) {
      return (
        <section className="tn-step-panel tn-result-unavailable">
          <p className="tn-kicker">Result unavailable</p>
          <h1>This timetable could not be ranked.</h1>
          <p>Return to configuration and choose another available week.</p>
        </section>
      );
    }
    const rankingActive = criteria.length > 0;
    const position = summarizeRankPosition(resultRow);
    const betterCount = position.betterCount;
    const days = dailyByVariant.get(resultRow.variant_index) ?? [];
    const blocks = blocksByVariant.get(resultRow.variant_index) ?? [];
    return (
      <section className="tn-step-panel tn-result-step" aria-labelledby="tn-result-title">
        <div className="tn-result-overview">
          <div className="tn-result-hero">
            <p className="tn-kicker">Step 5/5</p>
            <h1 id="tn-result-title">
              {!rankingActive ? (
                "You chose to view this timetable without a frustration ranking."
              ) : betterCount === 0 ? (
                <>
                  No timetable out of{" "}
                  <span className="tn-result-total-number">
                    {formatNumber(resultRow.peerCount)}
                  </span>{" "}
                  is better than yours.
                </>
              ) : (
                <>
                  <span className="tn-result-better-number">
                    {formatNumber(betterCount)}
                  </span>{" "}
                  out of{" "}
                  <span className="tn-result-total-number">
                    {formatNumber(resultRow.peerCount)}
                  </span>{" "}
                  timetables are better than yours.
                </>
              )}
            </h1>
            {rankingActive && position.isTied && (
              <p className="tn-result-tie-note">
                {formatNumber(position.tiedCount)} timetables share this score,
                so the tied positions run from {formatNumber(position.firstPosition)}
                {" "}to {formatNumber(position.lastPosition)}.
              </p>
            )}
            <div className="tn-result-tags">
              <span>{selectedIntake.intake_code}</span>
              <span>{selectedVariant.grouping}</span>
              <span>Week of {formatWeekDate(selectedWeek)}</span>
              {isResolvedElective(selectedVariant) && (
                <span>{selectedVariant.elective_profile_name}</span>
              )}
            </div>
          </div>
          <aside className="tn-result-aside" aria-label="Result summary">
            <div className={`tn-rank-card ${rankingActive ? "" : "no-rank"}`}>
              <span>{rankingActive ? "Your position" : "Ranking is off"}</span>
              {rankingActive ? (
                <div>
                  <strong>{formatNumber(resultRow.recalculatedBestRank)}</strong>
                  <small>of {formatNumber(resultRow.peerCount)}</small>
                </div>
              ) : (
                <strong>Timetable view</strong>
              )}
              <p>
                {rankingActive
                  ? "Lower is better"
                  : "No frustration criteria are active."}
              </p>
            </div>
          </aside>
        </div>

        <div className="tn-result-reading">
          <div className="tn-verdict">
            <span className="tn-verdict-mark" aria-hidden="true">
              {rankingActive ? "~" : "○"}
            </span>
            <div>
              <strong>
                {rankingActive
                  ? resultRow.total_gap_minutes > 0
                    ? "The waiting time has the clearest impact."
                    : "This timetable avoids long campus waits."
                  : "The timetable is shown without a verdict."}
              </strong>
              <p>
                {rankingActive
                  ? equalWeight
                    ? `All ${criteria.length} remaining frustrations have equal influence.`
                    : `Your biggest frustration is ${CRITERION_COPY[criteria[0]].title.toLowerCase()}.`
                  : "Restore a frustration if you want a comparison rank."}
              </p>
            </div>
          </div>
          <div className="tn-result-stat">
            <span>WAITING BETWEEN CAMPUS CLASSES</span>
            <strong>{formatMinutes(resultRow.total_gap_minutes)}</strong>
            <small>across this week</small>
          </div>
          <div className="tn-result-stat">
            <span>LONGEST SINGLE GAP</span>
            <strong>{formatMinutes(resultRow.longest_gap_minutes)}</strong>
            <small>{resultRow.days_with_gaps} gap days</small>
          </div>
          <div className="tn-result-stat">
            <span>CAMPUS DAYS</span>
            <strong>{resultRow.campus_days}</strong>
            <small>{formatMinutes(resultRow.total_teaching_minutes)} teaching</small>
          </div>
        </div>

        <div className="tn-timetable-section">
          <div className="tn-timetable-heading">
            <div>
              <span>YOUR PROGRAMME</span>
              <h2>{programmeTitle(selectedIntake)}</h2>
              <p>{programmeMeta(selectedIntake)}</p>
            </div>
            <div className="tn-chart-legend" aria-label="Timetable legend">
              <span><i className="legend-campus" /> Campus class</span>
              <span><i className="legend-online" /> Online class</span>
              <span><i className="legend-gap" /> Campus gap</span>
            </div>
          </div>
          <VerticalTimetable
            weekStart={selectedWeek}
            row={resultRow}
            days={days}
            blocks={blocks}
          />
          <p className="tn-chart-note">
            Gap colour appears only inside the first-to-last campus window. An
            online class outside that window does not create campus waiting time.
          </p>
        </div>

        <section className="tn-score-explanation" aria-labelledby="tn-score-title">
          <header className="tn-score-heading">
            <div>
              <span>DETAILED STATISTICS</span>
              <h2 id="tn-score-title">How your score was built.</h2>
              <p>
                Each active frustration is compared with the same peer group,
                then weighted using the order you chose.
              </p>
            </div>
            <div className="tn-score-badge">
              <span>WEIGHTED SCORE</span>
              <strong>{rankingActive ? formatScore(resultRow.recalculatedScore) : "Off"}</strong>
              <small>Lower is better</small>
            </div>
          </header>
          {rankingActive ? (
            <div className="tn-score-table-scroll" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>Frustration</th>
                    <th>Your value</th>
                    <th>Peer percentile</th>
                    <th>Weight</th>
                    <th>Score impact</th>
                  </tr>
                </thead>
                <tbody>
                  {criteria.map((criterion, index) => {
                    const component = resultRow.components[criterion];
                    return (
                      <tr key={criterion}>
                        <td>{equalWeight ? "=" : index + 1}</td>
                        <th scope="row">{CRITERION_COPY[criterion].title}</th>
                        <td>{formatCriterionValue(criterion, component.raw)}</td>
                        <td>{formatScore(component.percentile)}%</td>
                        <td>{(component.weight * 100).toFixed(1)}%</td>
                        <td>{formatScore(component.contribution)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="tn-score-empty">
              Restore at least one frustration to see a score explanation.
            </div>
          )}
        </section>

        <section className="tn-dashboard-next" aria-labelledby="tn-dashboard-next-title">
          <div className="tn-next-step-marker" aria-hidden="true">
            <span>UP NEXT</span>
            <strong>06</strong>
          </div>
          <div className="tn-next-step-copy">
            <span>COMPARISON DASHBOARD</span>
            <h2 id="tn-dashboard-next-title">
              Put your timetable beside the best and worst.
            </h2>
            <p>
              The dashboard starts with its default filters, so you can build a
              fresh comparison from the full timetable set.
            </p>
          </div>
          <button type="button" onClick={() => onOpenDashboard()}>
            View dashboard <span aria-hidden="true">→</span>
          </button>
        </section>
      </section>
    );
  }

  const activeStep =
    step === 0
      ? renderFindStep()
      : step === 1
        ? renderConfigStep()
        : step === 2
          ? renderPriorityStep()
          : step === 3
            ? renderCompareStep()
            : renderResultStep();

  const livePriority =
    criteria.length === 0
      ? "Ranking disabled"
      : equalWeight
        ? `${criteria.length} equal frustrations`
        : CRITERION_COPY[criteria[0]].title;
  const liveComparison =
    scope === "similar"
      ? "Students like me"
      : scope === "level"
        ? `All ${selectedIntake?.programme_level_name.toLowerCase() ?? "programme"} intakes`
        : "Everyone this week";

  return (
    <div className="tn-root" data-theme={theme}>
      <a className="tn-skip-link" href="#tn-main">Skip to the current task</a>
      <header className="tn-topbar">
        <button
          className="tn-brand"
          type="button"
          aria-label="Start a new timetable check"
          onClick={() => {
            setStep(0);
            setFurthestStep(selectedIntake ? 1 : 0);
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
        >
          <BrandIcon className="tn-brand-mark" />
          <span>
            <strong>APU Timetable Analyzer</strong>
            <small>by Leonard Su</small>
          </span>
        </button>
        <p>How bad is your timetable?</p>
        <div className="tn-topbar-actions">
          <button
            type="button"
            onClick={() => setTheme((current) => (current === "light" ? "dark" : "light"))}
          >
            {theme === "light" ? "Dark view" : "Light view"}
          </button>
          <button type="button" onClick={() => onOpenDashboard()}>
            View dashboard
          </button>
        </div>
      </header>

      <div className={`tn-workspace ${step === 4 ? "has-result" : ""}`}>
        <nav className="tn-journey" aria-label="Timetable check progress">
          <div className="tn-journey-heading">
            <span>YOUR CHECK</span>
            <strong>{step + 1} of 5</strong>
          </div>
          <div className="tn-journey-steps">
            {JOURNEY.map((item, index) => (
              <button
                className={`${index === step ? "is-active" : ""} ${index < step ? "is-complete" : ""}`}
                type="button"
                key={item.label}
                disabled={index > furthestStep}
                aria-current={index === step ? "step" : undefined}
                onClick={() => goToStep(index)}
              >
                <span className="tn-journey-index" aria-hidden="true">
                  {index < step ? "✓" : index + 1}
                </span>
                <span className="tn-journey-label">{item.label}</span>
                <span className="tn-journey-short">{item.short}</span>
              </button>
            ))}
          </div>
        </nav>

        <main className="tn-stage" id="tn-main" tabIndex={-1}>
          {activeStep}
          <footer className="tn-stage-footer">
            {step > 0 ? (
              <button className="tn-secondary-action" type="button" onClick={() => goToStep(step - 1)}>
                <span aria-hidden="true">←</span> Back
              </button>
            ) : (
              <span />
            )}
            {step < 4 ? (
              <button
                className={`tn-primary-action ${step === 0 ? "is-find" : ""}`}
                type="button"
                disabled={(step === 0 && !selectedIntake) || (step === 1 && !selectedVariant)}
                onClick={continueForward}
              >
                {step === 0
                  ? "Continue to configuration"
                  : step === 1
                    ? "Continue to frustrations"
                    : step === 2
                      ? "Continue to comparison"
                      : "Show my timetable"}
                <span aria-hidden="true">→</span>
              </button>
            ) : (
              <button
                className="tn-text-action"
                type="button"
                onClick={() => {
                  setStep(0);
                  setFurthestStep(0);
                  setQuery("");
                  setSelectedIntakeCode(null);
                  setSelectedGrouping(null);
                  setSelectedElective(null);
                  setSelectedWeek(defaultWeek);
                  setCriteria([...defaultCriteria]);
                  setEqualWeight(false);
                  setUseDefaults(true);
                  setPriorityHistory([]);
                  setScope("all");
                  setSameSchool(false);
                }}
              >
                Start a new check
              </button>
            )}
          </footer>
        </main>

        {step !== 4 && (
          <aside className="tn-live-panel" aria-label="Live setup summary">
            <div className="tn-concept-card">
              <BrandIcon className="tn-concept-mark" />
              <div>
                <strong>APU Timetable Analyzer</strong>
                <p>
                  Everyone says that their timetable is bad.
                  But how bad is it?
                  <br/>
                  <br/>
                  This project analyzes timetables 
                  in APU and ranks them to figure out
                  how "bad" they really are.
                </p>
              </div>
            </div>
            <div className="tn-live-summary">
              <div className="tn-live-summary-heading">
                <span>LIVE SETUP</span>
                <strong>Updates as you go</strong>
              </div>
              <dl>
                <div>
                  <dt>Intake</dt>
                  <dd>{selectedIntake?.intake_code ?? "Not chosen yet"}</dd>
                </div>
                <div>
                  <dt>Configuration</dt>
                  <dd>
                    {selectedVariant
                      ? `${selectedVariant.grouping}, ${configLabel(selectedVariant)}`
                      : "Waiting for intake"}
                  </dd>
                </div>
                <div>
                  <dt>Most important</dt>
                  <dd>{livePriority}</dd>
                </div>
                <div>
                  <dt>Compared with</dt>
                  <dd>{liveComparison}</dd>
                </div>
              </dl>
              <div className="tn-mini-progress" aria-label="Wizard completion">
                {JOURNEY.map((item, index) => (
                  <span className={index <= step ? "is-filled" : ""} key={item.label} />
                ))}
              </div>
            </div>
            {selectedIntake ? (
              <div className="tn-course-note">
                <span>COURSE FOUND</span>
                <strong>{programmeTitle(selectedIntake)}</strong>
                <p>{selectedIntake.school ?? programmeMeta(selectedIntake)}</p>
              </div>
            ) : (
              <div className="tn-course-note is-empty">
                <span>NOTHING ASSUMED</span>
                <strong>Your code decides what comes next.</strong>
                <p>No unrelated groups, electives, or specialisms will appear.</p>
              </div>
            )}
          </aside>
        )}
      </div>
      {drag && (
        <ol
          className="tn-drag-overlay-list"
          style={
            {
              left: `${drag.left}px`,
              top: `${drag.top}px`,
              width: `${drag.width}px`,
            } as CSSProperties
          }
        >
          {renderPriorityCard(
            drag.criterion,
            criteria.indexOf(drag.criterion),
            true,
          )}
        </ol>
      )}
      <div className={`tn-toast ${toast ? "is-visible" : ""}`} role="status" aria-live="polite">
        {toast}
      </div>
    </div>
  );
}
