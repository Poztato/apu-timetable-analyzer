import {
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import {
  SCORING_COMPONENT_KEYS,
  type ScoringPreferences,
} from "./ranking";
import type {
  ScoringComponentKey,
  ScoringDefinition,
  TimePreferenceDefinition,
} from "./types";

type HelpTopic = ScoringComponentKey | null;
type ExampleBlockMode = "campus" | "online" | "gap";

interface ExampleBlock {
  start: number;
  end: number;
  label: string;
  mode: ExampleBlockMode;
}

interface ExampleScenario {
  title: string;
  note: string;
  blocks: ExampleBlock[];
}

interface ComponentGuide {
  title: string;
  menuDescription: string;
  mainExplanation: string;
  description: string;
  good: ExampleScenario;
  bad: ExampleScenario;
}

interface ScoringHelpProps {
  scoring: ScoringDefinition;
  preferences: ScoringPreferences;
  triggerLabel?: string;
  className?: string;
}

const TIMELINE_START = 8 * 60;
const TIMELINE_END = 18 * 60;
const TIMELINE_MINUTES = TIMELINE_END - TIMELINE_START;
const TIMELINE_MARKS = [8, 10, 12, 14, 16, 18];

function parseClock(value: string): number {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function formatClock(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  const suffix = hours >= 12 ? "PM" : "AM";
  const displayHour = hours % 12 || 12;
  return `${displayHour}${remainder ? `:${String(remainder).padStart(2, "0")}` : ""} ${suffix}`;
}

function block(
  start: number,
  end: number,
  label: string,
  mode: ExampleBlockMode = "campus",
): ExampleBlock {
  return { start, end, label, mode };
}

function FactorIcon({ component }: { component: ScoringComponentKey }) {
  let artwork: ReactNode = null;

  switch (component) {
    case "campus_trip":
      artwork = (
        <>
          <path d="M7 35h18c7 0 12-4.5 12-11" />
          <path d="m7 35 5-5M7 35l5 5" />
          <path d="M37 7a7 7 0 0 0-7 7c0 5 7 11 7 11s7-6 7-11a7 7 0 0 0-7-7Z" />
          <circle cx="37" cy="14" r="2.3" />
        </>
      );
      break;
    case "online_commitment":
      artwork = (
        <>
          <rect x="8" y="8" width="32" height="26" rx="3" />
          <path d="M5 39h38M18 39l2-5h8l2 5" />
          <path d="m21 17 8 4.5-8 4.5Z" />
        </>
      );
      break;
    case "placement":
      artwork = (
        <>
          <circle cx="24" cy="24" r="16" />
          <path d="M24 14v10l7 4" />
          <path d="M24 5v3M43 24h-3M24 43v-3M5 24h3" />
        </>
      );
      break;
    case "span":
      artwork = (
        <>
          <path d="M9 11v26M39 11v26" />
          <path d="M13 24h22M13 24l5-5M13 24l5 5M35 24l-5-5M35 24l-5 5" />
          <circle cx="9" cy="11" r="2" />
          <circle cx="39" cy="37" r="2" />
        </>
      );
      break;
    case "waiting":
      artwork = (
        <>
          <path d="M14 7h20M14 41h20" />
          <path d="M17 7c0 8 7 10 7 17s-7 9-7 17M31 7c0 8-7 10-7 17s7 9 7 17" />
          <path d="M20 15h8M20 34h8" />
        </>
      );
      break;
    case "short_day":
      artwork = (
        <>
          <rect x="12" y="6" width="24" height="36" rx="4" />
          <path d="M18 13h12" />
          <rect x="17" y="21" width="14" height="7" rx="2" />
          <path d="M24 32v3" />
        </>
      );
      break;
    case "long_day":
      artwork = (
        <>
          <rect x="12" y="6" width="24" height="36" rx="4" />
          <path d="M18 12h12" />
          <rect x="17" y="17" width="14" height="5" rx="1.5" />
          <rect x="17" y="25" width="14" height="5" rx="1.5" />
          <rect x="17" y="33" width="14" height="5" rx="1.5" />
        </>
      );
      break;
  }

  return (
    <span className="score-help-factor-icon" aria-hidden="true">
      <svg
        viewBox="0 0 48 48"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        focusable="false"
        data-factor-icon={component}
      >
        {artwork}
      </svg>
    </span>
  );
}

function componentGuide(
  component: ScoringComponentKey,
  preference: TimePreferenceDefinition,
): ComponentGuide {
  const preferredStart = parseClock(preference.start);
  const preferredEnd = parseClock(preference.end);
  const preferredExampleStart = Math.max(
    TIMELINE_START,
    Math.min(preferredStart, preferredEnd - 60, TIMELINE_END - 60),
  );
  const farEdgeStart = preferredStart >= 12 * 60 ? 8 * 60 + 30 : 17 * 60;

  switch (component) {
    case "campus_trip":
      return {
        title: "Campus trips",
        menuDescription: "Fewer days travelling to campus usually make the week easier.",
        mainExplanation: "A day without a campus trip is easier than a day that needs one.",
        description:
          "Even one physical class means travelling to campus. If several physical classes happen on the same day, they still share one trip.",
        good: {
          title: "Good Timetable (no classes that day)",
          note: "There is no teaching and no need to travel.",
          blocks: [],
        },
        bad: {
          title: "Bad Timetable (a physical class day)",
          note: "Even one physical class requires a campus trip.",
          blocks: [block(11 * 60, 13 * 60, "Physical class")],
        },
      };
    case "online_commitment":
      return {
        title: "Online-only days",
        menuDescription: "Online classes are easier than travelling, but they still take time.",
        mainExplanation: "Studying from home is easier than travelling to campus, but it still uses part of your day.",
        description:
          "A completely free day is best. A day with only online classes is still treated as busier than a day with no classes.",
        good: {
          title: "Good Timetable (no classes that day)",
          note: "The whole day is free.",
          blocks: [],
        },
        bad: {
          title: "Bad Timetable (an online class day)",
          note: "There is no campus trip, but the class still takes time.",
          blocks: [block(11 * 60, 13 * 60, "Online class", "online")],
        },
      };
    case "placement":
      return {
        title: "Time placement",
        menuDescription: "Classes closer to your preferred part of the day are ranked better.",
        mainExplanation: "Classes near the part of the day you chose are better than classes at the opposite end of the day.",
        description:
          `Your selected time is ${preference.short_label.toLowerCase()}. The farther a physical class is from that choice, the more it affects the result. Early and late classes both count, so they do not cancel each other out.`,
        good: {
          title: "Good Timetable (class near your chosen time)",
          note: "This class is close to the part of the day you selected.",
          blocks: [
            block(
              preferredExampleStart,
              preferredExampleStart + 60,
              "Physical class",
            ),
          ],
        },
        bad: {
          title: "Bad Timetable (class far from your chosen time)",
          note: "This class is at the opposite end of the day from your choice.",
          blocks: [block(farEdgeStart, farEdgeStart + 60, "Physical class")],
        },
      };
    case "span":
      return {
        title: "Day span",
        menuDescription: "A compact day is better than classes spread from morning to evening.",
        mainExplanation: "Classes close together leave more of your day free.",
        description:
          "We look at the time from your first physical class to your last one. A long break between them means your day is still taken up.",
        good: {
          title: "Good Timetable (classes close together)",
          note: "The classes use one compact part of the day.",
          blocks: [block(11 * 60, 14 * 60, "Classes together")],
        },
        bad: {
          title: "Bad Timetable (classes spread across the day)",
          note: "The first and last class take up most of the day.",
          blocks: [
            block(8 * 60 + 30, 9 * 60 + 30, "Morning class"),
            block(9 * 60 + 30, 16 * 60 + 30, "Long break", "gap"),
            block(16 * 60 + 30, 17 * 60 + 30, "Evening class"),
          ],
        },
      };
    case "waiting":
      return {
        title: "Campus waiting",
        menuDescription: "Long empty gaps between campus classes make a timetable worse.",
        mainExplanation: "Less waiting between campus classes means a better timetable.",
        description:
          "Only free time between your first and last physical class counts. If an online class happens during that break, it fills part of the waiting time.",
        good: {
          title: "Good Timetable (back-to-back classes)",
          note: "The second class starts when the first one ends.",
          blocks: [
            block(10 * 60, 11 * 60 + 30, "Class one"),
            block(11 * 60 + 30, 13 * 60, "Class two"),
          ],
        },
        bad: {
          title: "Bad Timetable (a long campus wait)",
          note: "There is a long empty gap before the next class.",
          blocks: [
            block(9 * 60, 10 * 60, "Morning class"),
            block(10 * 60, 15 * 60, "Waiting on campus", "gap"),
            block(15 * 60, 16 * 60, "Afternoon class"),
          ],
        },
      };
    case "short_day":
      return {
        title: "Short campus days",
        menuDescription: "Travelling for only a small amount of teaching is less convenient.",
        mainExplanation: "A campus trip feels less worthwhile when the class is very short.",
        description:
          "A one-hour physical day affects the result most. This gradually fades away when the day reaches two hours of physical teaching.",
        good: {
          title: "Good Timetable (two hours on campus)",
          note: "The trip includes a more useful amount of teaching.",
          blocks: [block(11 * 60, 13 * 60, "Two-hour class")],
        },
        bad: {
          title: "Bad Timetable (one hour on campus)",
          note: "The journey is for only one hour of teaching.",
          blocks: [block(11 * 60, 12 * 60, "One-hour class")],
        },
      };
    case "long_day":
      return {
        title: "Heavy teaching days",
        menuDescription: "Very long teaching days are harder than balanced days.",
        mainExplanation: "A very long teaching day is harder than a balanced one.",
        description:
          "Physical and online classes both count. Four hours is treated as manageable, then the effect gradually grows until six hours.",
        good: {
          title: "Good Timetable (a balanced teaching day)",
          note: "Four hours leaves more time to rest or study.",
          blocks: [block(10 * 60, 14 * 60, "Four hours of classes")],
        },
        bad: {
          title: "Bad Timetable (a very long teaching day)",
          note: "Six hours of classes makes the day much heavier.",
          blocks: [block(9 * 60, 15 * 60, "Six hours of classes")],
        },
      };
  }
}

function SyntheticDay({
  scenario,
  kind,
}: {
  scenario: ExampleScenario;
  kind: "good" | "bad";
}) {
  const visibleBlocks = scenario.blocks.filter(
    (item) => item.end > TIMELINE_START && item.start < TIMELINE_END,
  );
  const headingMatch = /^(.+?) \((.+)\)$/.exec(scenario.title);
  const headingLabel = headingMatch?.[1] ?? scenario.title;
  const headingDetail = headingMatch?.[2] ?? "";

  return (
    <article className={`score-help-scenario is-${kind}`}>
      <header>
        <h4 aria-label={scenario.title}>
          <span>{headingLabel}</span>
          {headingDetail && <small>{headingDetail}</small>}
        </h4>
        <p>{scenario.note}</p>
      </header>
      <div
        className="score-help-day-visual"
        role="img"
        aria-label={`${scenario.title}. ${scenario.note}`}
      >
        <div className="score-help-time-axis" aria-hidden="true">
          {TIMELINE_MARKS.map((hour) => (
            <span
              key={hour}
              style={{ top: `${((hour * 60 - TIMELINE_START) / TIMELINE_MINUTES) * 100}%` }}
            >
              {formatClock(hour * 60)}
            </span>
          ))}
        </div>
        <div className="score-help-day-lane" aria-hidden="true">
          {TIMELINE_MARKS.map((hour) => (
            <i
              className="score-help-grid-line"
              key={hour}
              style={{ top: `${((hour * 60 - TIMELINE_START) / TIMELINE_MINUTES) * 100}%` }}
            />
          ))}
          {visibleBlocks.length === 0 ? (
            <div className="score-help-empty-day">
              <strong>No classes that day</strong>
            </div>
          ) : (
            visibleBlocks.map((item, index) => {
              const start = Math.max(item.start, TIMELINE_START);
              const end = Math.min(item.end, TIMELINE_END);
              const style = {
                "--score-help-start": `${((start - TIMELINE_START) / TIMELINE_MINUTES) * 100}%`,
                "--score-help-duration": `${((end - start) / TIMELINE_MINUTES) * 100}%`,
              } as CSSProperties;

              return (
                <div
                  className={`score-help-example-block is-${item.mode}`}
                  key={`${item.label}-${index}`}
                  style={style}
                >
                  <strong>{item.label}</strong>
                  {item.mode !== "gap" && (
                    <small>{formatClock(item.start)} to {formatClock(item.end)}</small>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </article>
  );
}

function GuideMenu({
  preference,
  onSelect,
}: {
  preference: TimePreferenceDefinition;
  onSelect: (topic: ScoringComponentKey) => void;
}) {
  return (
    <section className="score-help-menu">
      <p className="score-help-menu-intro">
        By default, your timetable is ranked based on these factors. Click on
        one to learn more:
      </p>
      <nav className="score-help-factor-grid" aria-label="Timetable ranking factors">
        {SCORING_COMPONENT_KEYS.map((component) => {
          const guide = componentGuide(component, preference);
          return (
            <button
              type="button"
              key={component}
              onClick={() => onSelect(component)}
            >
              <FactorIcon component={component} />
              <span className="score-help-factor-copy">
                <strong>{guide.title}</strong>
                <span>{guide.menuDescription}</span>
              </span>
              <span className="score-help-factor-cue" aria-hidden="true">
                <svg viewBox="0 0 20 20" focusable="false">
                  <path d="m7 4 6 6-6 6" />
                </svg>
              </span>
            </button>
          );
        })}
      </nav>
      <p className="score-help-menu-note">
        A timetable with fewer inconveniences is placed higher in the results.
        Your preferred time and optional choices help personalize the order.
      </p>
    </section>
  );
}

function ComponentDetail({
  guide,
  onBack,
}: {
  guide: ComponentGuide;
  onBack: () => void;
}) {
  return (
    <section className="score-help-component-detail">
      <button className="score-help-back" type="button" onClick={onBack}>
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="m10 6-6 6 6 6M5 12h15" />
        </svg>
        <span>Back to all factors</span>
      </button>

      <header className="score-help-detail-heading">
        <h3>{guide.title}</h3>
        <p className="score-help-main-explanation">{guide.mainExplanation}</p>
        <p className="score-help-short-description">{guide.description}</p>
      </header>

      <div
        className="score-help-examples"
        aria-label={`${guide.title} timetable examples`}
      >
        <SyntheticDay scenario={guide.good} kind="good" />
        <SyntheticDay scenario={guide.bad} kind="bad" />
      </div>
    </section>
  );
}

export function ScoringHelp({
  scoring,
  preferences,
  triggerLabel = "Scoring guide",
  className = "",
}: ScoringHelpProps) {
  const [open, setOpen] = useState(false);
  const [topic, setTopic] = useState<HelpTopic>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const preference =
    scoring.time_preferences.find(
      (item) => item.key === preferences.timePreference,
    ) ?? scoring.time_preferences[0];

  function closeHelp() {
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => closeRef.current?.focus(), 0);

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeHelp();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const guide = topic ? componentGuide(topic, preference) : null;
  const portalHost =
    triggerRef.current?.closest(".tn-root, .db-root") ?? document.body;

  const overlay = open ? (
    <div
      className="score-help-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeHelp();
      }}
    >
      <section
        className="score-help-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialogRef}
      >
        <header className="score-help-dialog-header">
          <div>
            <span>TIMETABLE GUIDE</span>
            <h2 id={titleId}>How is my timetable ranked?</h2>
          </div>
          <button
            className="score-help-close"
            type="button"
            aria-label="Close scoring guide"
            ref={closeRef}
            onClick={closeHelp}
          >
            <span aria-hidden="true">&times;</span>
          </button>
        </header>

        <div className="score-help-dialog-body" aria-live="polite">
          {guide ? (
            <ComponentDetail guide={guide} onBack={() => setTopic(null)} />
          ) : (
            <GuideMenu preference={preference} onSelect={setTopic} />
          )}
        </div>
      </section>
    </div>
  ) : null;

  return (
    <>
      <button
        className={`score-help-trigger ${className}`.trim()}
        type="button"
        ref={triggerRef}
        aria-haspopup="dialog"
        onClick={() => {
          setTopic(null);
          setOpen(true);
        }}
      >
        <span className="score-help-question" aria-hidden="true">?</span>
        <span>{triggerLabel}</span>
      </button>

      {overlay ? createPortal(overlay, portalHost) : null}
    </>
  );
}
