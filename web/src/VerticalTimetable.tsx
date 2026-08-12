import { useMemo, type CSSProperties } from "react";

import type { RankedVariant } from "./ranking";
import type { DailyMetric, TimetableBlock } from "./types";

export interface ScheduleBlockWithGap {
  block: TimetableBlock;
  gapBefore: number;
}

function formatMinutes(value: number): string {
  if (value < 60) return `${value} min`;
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return minutes === 0 ? `${hours} hr` : `${hours} hr ${minutes} min`;
}

function formatDay(value: string): { short: string; long: string; date: string } {
  const date = new Date(`${value}T00:00:00Z`);
  return {
    short: new Intl.DateTimeFormat("en-MY", {
      weekday: "short",
      timeZone: "UTC",
    }).format(date),
    long: new Intl.DateTimeFormat("en-MY", {
      weekday: "long",
      timeZone: "UTC",
    }).format(date),
    date: new Intl.DateTimeFormat("en-MY", {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    }).format(date),
  };
}

function addDays(value: string, count: number): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}

function minutesFromIso(value: string): number {
  const match = value.match(/T(\d{2}):(\d{2})/);
  if (!match) return 0;
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatClock(value: string): string {
  const minutes = minutesFromIso(value);
  const hour24 = Math.floor(minutes / 60);
  const minute = minutes % 60;
  const period = hour24 >= 12 ? "PM" : "AM";
  const hour12 = hour24 % 12 || 12;
  return `${hour12}:${String(minute).padStart(2, "0")} ${period}`;
}

function formatTick(minutes: number): string {
  const hour24 = Math.floor(minutes / 60);
  const period = hour24 >= 12 ? "PM" : "AM";
  return `${hour24 % 12 || 12}:00 ${period}`;
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

function dayFlags(day: DailyMetric | undefined): string[] {
  if (!day) return [];
  const flags: string[] = [];
  if (day.total_gap_minutes > 0) flags.push("Gaps");
  if (day.early_only_flag) flags.push("Early");
  if (day.late_only_flag) flags.push("Late");
  if (day.one_hour_only_flag) flags.push("Short trip");
  if (day.overloaded_flag) flags.push("Overloaded");
  return flags;
}

export function VerticalTimetable({
  weekStart,
  row,
  days,
  blocks,
  ariaLabel = "Weekly timetable",
}: {
  weekStart: string;
  row: RankedVariant;
  days: DailyMetric[];
  blocks: TimetableBlock[];
  ariaLabel?: string;
}) {
  const daysByDate = useMemo(
    () => new Map(days.map((day) => [day.event_date, day])),
    [days],
  );
  const blocksByDate = useMemo(() => {
    const grouped = new Map<string, TimetableBlock[]>();
    for (const block of blocks) {
      const current = grouped.get(block.event_date) ?? [];
      current.push(block);
      grouped.set(block.event_date, current);
    }
    return grouped;
  }, [blocks]);

  const showWeekend =
    row.weekend_days > 0 ||
    blocks.some((block) => {
      const offset = Math.round(
        (Date.parse(`${block.event_date}T00:00:00Z`) -
          Date.parse(`${weekStart}T00:00:00Z`)) /
          86_400_000,
      );
      return offset >= 5;
    });
  const dates = Array.from({ length: showWeekend ? 7 : 5 }, (_, index) =>
    addDays(weekStart, index),
  );
  const activeBlocks = blocks.filter((block) =>
    dates.includes(block.event_date),
  );
  const minimumStart = activeBlocks.length
    ? Math.min(...activeBlocks.map((block) => minutesFromIso(block.start_at)))
    : 8 * 60;
  const maximumEnd = activeBlocks.length
    ? Math.max(...activeBlocks.map((block) => minutesFromIso(block.end_at)))
    : 18 * 60;
  const axisStart = Math.max(0, Math.floor(minimumStart / 60) * 60 - 30);
  const axisEnd = Math.min(24 * 60, Math.ceil(maximumEnd / 60) * 60 + 30);
  const pixelsPerMinute = 1.05;
  const chartHeight = Math.max(600, (axisEnd - axisStart) * pixelsPerMinute);
  const hourTicks: number[] = [];
  for (
    let tick = Math.ceil(axisStart / 60) * 60;
    tick <= axisEnd;
    tick += 60
  ) {
    hourTicks.push(tick);
  }

  const chartStyle = {
    "--day-count": dates.length,
    "--chart-height": `${chartHeight}px`,
  } as CSSProperties;

  return (
    <div
      className="tn-schedule-scroll"
      role="region"
      tabIndex={0}
      aria-label={ariaLabel}
    >
      <div className="tn-schedule-canvas" style={chartStyle}>
        <div className="tn-schedule-header" aria-hidden="true">
          <div className="tn-axis-corner">TIME</div>
          {dates.map((date) => {
            const label = formatDay(date);
            const flags = dayFlags(daysByDate.get(date));
            return (
              <div className="tn-day-heading" key={date}>
                <strong>{label.short}</strong>
                <span>{label.long}</span>
                <small>{label.date}</small>
                {flags.length > 0 && <i>{flags.join(", ")}</i>}
              </div>
            );
          })}
        </div>
        <div className="tn-schedule-body">
          <div className="tn-time-axis" aria-label="Time axis">
            {hourTicks.map((tick) => (
              <span
                key={tick}
                style={{ top: `${(tick - axisStart) * pixelsPerMinute}px` }}
              >
                {formatTick(tick)}
              </span>
            ))}
          </div>
          <div className="tn-schedule-grid">
            {hourTicks.map((tick) => (
              <span
                className="tn-hour-line"
                key={tick}
                style={{ top: `${(tick - axisStart) * pixelsPerMinute}px` }}
              />
            ))}
            {dates.map((date, dayIndex) => (
              <div
                className={`tn-day-lane ${dayFlags(daysByDate.get(date))
                  .map((flag) =>
                    `flag-${flag.toLowerCase().replaceAll(" ", "-")}`,
                  )
                  .join(" ")}`}
                key={date}
                style={{ "--day": dayIndex } as CSSProperties}
              />
            ))}
            {dates.flatMap((date, dayIndex) =>
              scheduleBlocksWithGaps(
                blocksByDate.get(date) ?? [],
              ).flatMap(({ block, gapBefore }) => {
                const start = minutesFromIso(block.start_at);
                const end = minutesFromIso(block.end_at);
                const elements = [];
                if (gapBefore > 0) {
                  elements.push(
                    <div
                      className="tn-gap-block"
                      key={`gap-${block.variant_index}-${block.start_at}`}
                      style={
                        {
                          "--day": dayIndex,
                          top: `${(start - gapBefore - axisStart) * pixelsPerMinute}px`,
                          height: `${gapBefore * pixelsPerMinute}px`,
                        } as CSSProperties
                      }
                      aria-label={`${formatDay(date).long}, ${formatMinutes(gapBefore)} campus gap`}
                    >
                      {gapBefore >= 45 && (
                        <span>{formatMinutes(gapBefore)} gap</span>
                      )}
                    </div>,
                  );
                }
                elements.push(
                  <article
                    className={`tn-class-block is-${block.delivery_mode}`}
                    key={`${block.variant_index}-${block.start_at}-${block.module_id}`}
                    style={
                      {
                        "--day": dayIndex,
                        top: `${(start - axisStart) * pixelsPerMinute}px`,
                        height: `${Math.max(28, (end - start) * pixelsPerMinute)}px`,
                      } as CSSProperties
                    }
                    title={`${block.module_name ?? block.module_id}, ${formatClock(block.start_at)} to ${formatClock(block.end_at)}`}
                  >
                    <strong>{block.module_name ?? block.module_id}</strong>
                    <span>
                      {formatClock(block.start_at)} to {formatClock(block.end_at)}
                    </span>
                    <small>
                      {block.delivery_mode === "online"
                        ? "Online"
                        : block.room ?? block.location ?? "Campus"}
                    </small>
                  </article>,
                );
                return elements;
              }),
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
