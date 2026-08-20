import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { BrandIcon } from "./BrandIcon";
import {
  COMPONENT_DETAILS,
  SCORING_COMPONENT_KEYS,
  createScoringContext,
  rankVariants,
  strongestComponent,
  summarizeRankPosition,
  type RankedVariant,
  type ScoringPreferences,
} from "./ranking";
import { ScoringHelp } from "./ScoringHelp";
import { VerticalTimetable } from "./VerticalTimetable";
import type { DashboardData, IntakeMetadata, ScoringComponentKey, WeeklyMetric } from "./types";

interface IntakeMatch {
  intake: IntakeMetadata;
  score: number;
  kind: "Exact match" | "Strong match" | "Close match" | "Possible match";
}

export type ComparisonScope = "similar" | "level" | "all";
type Theme = "light" | "dark";

const JOURNEY = [
  { short: "Find", label: "Find intake" },
  { short: "Config", label: "Configure" },
  { short: "Preferences", label: "Preferences" },
  { short: "Compare", label: "Compare" },
  { short: "Result", label: "Result" },
] as const;

const NO_ELECTIVE_STATUSES = new Set(["no_electives", "not_active"]);

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

function formatComponentValue(
  component: ScoringComponentKey,
  value: number,
): string {
  if (
    component === "campus_trip" ||
    component === "online_commitment" ||
    component === "short_day" ||
    component === "long_day"
  ) {
    return `${value} ${value === 1 ? "day" : "days"}`;
  }
  return formatMinutes(Math.round(value));
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
  const [timePreference, setTimePreference] = useState(
    data.scoring.default_time_preference,
  );
  const [emphasizeShortDays, setEmphasizeShortDays] = useState(false);
  const [emphasizeLongDays, setEmphasizeLongDays] = useState(false);
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

  const scoringContext = useMemo(
    () => createScoringContext(data.dailyMetrics, data.timetableBlocks),
    [data.dailyMetrics, data.timetableBlocks],
  );
  const dailyByVariant = scoringContext.dailyByVariant;
  const blocksByVariant = useMemo(() => {
    const grouped = new Map<number, typeof data.timetableBlocks>();
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
  const scoringPreferences = useMemo<ScoringPreferences>(
    () => ({
      timePreference,
      emphasizeShortDays,
      emphasizeLongDays,
    }),
    [timePreference, emphasizeShortDays, emphasizeLongDays],
  );
  const rankedRows = useMemo(
    () => rankVariants(peerRows, data.scoring, scoringPreferences, scoringContext),
    [peerRows, data.scoring, scoringPreferences, scoringContext],
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


  function renderPreferenceStep() {
    return (
      <section className="tn-step-panel tn-preference-step" aria-labelledby="tn-preference-title">
        <header className="tn-step-intro tn-preference-intro">
          <p className="tn-kicker">Step 3/5</p>
          <h1 id="tn-preference-title">When should your classes happen?</h1>
          <p>
            Choose where physical classes should gather, then add personal
            emphasis only if one of the two trade-offs matters more to you.
          </p>
        </header>

        <fieldset
          className="tn-time-fieldset"
          aria-describedby="tn-preferred-time-description"
        >
          <legend className="tn-fieldset-legend">Preferred time</legend>
          <div className="tn-time-heading-row">
            <div className="tn-time-heading-copy">
              <h2>Preferred time</h2>
              <p id="tn-preferred-time-description">
                Choose the part of the day your physical classes should gather around.
              </p>
            </div>
            <ScoringHelp
              scoring={data.scoring}
              preferences={scoringPreferences}
              triggerLabel="How scoring works"
            />
          </div>
          <div className="tn-time-options">
            {data.scoring.time_preferences.map((preference) => (
              <label
                className={preference.key === timePreference ? "is-selected" : ""}
                key={preference.key}
              >
                <input
                  type="radio"
                  name="time-preference"
                  value={preference.key}
                  checked={preference.key === timePreference}
                  onChange={() => setTimePreference(preference.key)}
                />
                <span className="tn-time-radio" aria-hidden="true" />
                <span>
                  <strong>{preference.label}</strong>
                  <b>{preference.start} to {preference.end}</b>
                  <small>{preference.description}</small>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="tn-emphasis-fieldset">
          <legend>Optional personal emphasis</legend>
          <p>These are independent checkboxes. They do not replace your time choice.</p>
          <div className="tn-emphasis-grid">
            <label className={emphasizeShortDays ? "is-selected" : ""}>
              <input
                type="checkbox"
                checked={emphasizeShortDays}
                onChange={(event) => setEmphasizeShortDays(event.target.checked)}
              />
              <span className="tn-checkbox" aria-hidden="true" />
              <span>
                <strong>Avoid short campus trips</strong>
                <small>Adds 5 raw weight points, while the daily cap stays at 100.</small>
              </span>
            </label>
            <label className={emphasizeLongDays ? "is-selected" : ""}>
              <input
                type="checkbox"
                checked={emphasizeLongDays}
                onChange={(event) => setEmphasizeLongDays(event.target.checked)}
              />
              <span className="tn-checkbox" aria-hidden="true" />
              <span>
                <strong>Avoid heavy teaching days</strong>
                <small>Adds 5 raw weight points, while the daily cap stays at 100.</small>
              </span>
            </label>
          </div>
        </fieldset>

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
    const position = summarizeRankPosition(resultRow);
    const betterCount = position.betterCount;
    const scoreDriver = strongestComponent(resultRow);
    const activePreference = data.scoring.time_preferences.find(
      (preference) => preference.key === timePreference,
    );
    const days = dailyByVariant.get(resultRow.variant_index) ?? [];
    const blocks = blocksByVariant.get(resultRow.variant_index) ?? [];
    return (
      <section className="tn-step-panel tn-result-step" aria-labelledby="tn-result-title">
        <div className="tn-result-overview">
          <div className="tn-result-hero">
            <p className="tn-kicker">Step 5/5</p>
            <h1 id="tn-result-title">
              {betterCount === 0 ? (
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
            {position.isTied && (
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
            <div className="tn-rank-card">
              <span>Your position</span>
              <div>
                <strong>{formatNumber(resultRow.recalculatedBestRank)}</strong>
                <small>of {formatNumber(resultRow.peerCount)}</small>
              </div>
              <p>Lower is better</p>
            </div>
          </aside>
        </div>

        <div className="tn-result-reading">
          <div className="tn-verdict">
            <span className="tn-verdict-mark" aria-hidden="true">
              ~
            </span>
            <div>
              <strong>{COMPONENT_DETAILS[scoreDriver].label} has the clearest impact.</strong>
              <p>
                Physical teaching is measured against the {activePreference?.start} to{" "}
                {activePreference?.end} preferred band, then averaged across all seven days.
              </p>
            </div>
          </div>
          <div className="tn-result-stat">
            <span>WAITING BETWEEN CAMPUS CLASSES</span>
            <strong>{formatMinutes(resultRow.total_campus_waiting_minutes)}</strong>
            <small>across this week</small>
          </div>
          <div className="tn-result-stat">
            <span>PHYSICAL DAY SPAN</span>
            <strong>{formatMinutes(resultRow.total_physical_span_minutes)}</strong>
            <small>{resultRow.physical_days} campus days</small>
          </div>
          <div className="tn-result-stat">
            <span>EMPTY DAYS</span>
            <strong>{resultRow.empty_days}</strong>
            <small>{formatMinutes(resultRow.total_teaching_minutes)} total teaching</small>
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
              <div className="tn-score-title-row">
                <h2 id="tn-score-title">How your score was built.</h2>
                <ScoringHelp
                  scoring={data.scoring}
                  preferences={scoringPreferences}
                  triggerLabel="Explain score components"
                />
              </div>
              <p>
                Each day receives an absolute inconvenience score. Empty days
                contribute 0, then all seven days are averaged for the week.
              </p>
            </div>
            <div className="tn-score-badge">
              <span>WEEKLY SCORE</span>
              <strong>{formatScore(resultRow.recalculatedScore)}</strong>
              <small>Lower is better</small>
            </div>
          </header>
          <div className="tn-score-table-scroll" tabIndex={0}>
            <table>
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Week measurement</th>
                  <th>Daily cap</th>
                  <th>Score impact</th>
                </tr>
              </thead>
              <tbody>
                {SCORING_COMPONENT_KEYS.map((componentKey) => {
                  const component = resultRow.components[componentKey];
                  return (
                    <tr key={componentKey}>
                      <th scope="row">{COMPONENT_DETAILS[componentKey].label}</th>
                      <td>{formatComponentValue(componentKey, component.raw)}</td>
                      <td>{formatScore(component.dailyCap)}</td>
                      <td>{formatScore(component.contribution)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
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
          ? renderPreferenceStep()
          : step === 3
            ? renderCompareStep()
            : renderResultStep();

  const livePreference =
    data.scoring.time_preferences.find(
      (preference) => preference.key === timePreference,
    )?.label ?? "Balanced midday";
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
                    ? "Continue to preferences"
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
                  setTimePreference(data.scoring.default_time_preference);
                  setEmphasizeShortDays(false);
                  setEmphasizeLongDays(false);
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
                  See how much of your week is claimed by campus trips, awkward
                  timing, long spans, waiting, and teaching load. The score stays
                  absolute, while the rank shows where you sit in your chosen group.
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
                  <dt>Time preference</dt>
                  <dd>{livePreference}</dd>
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
      <div className={`tn-toast ${toast ? "is-visible" : ""}`} role="status" aria-live="polite">
        {toast}
      </div>
    </div>
  );
}
