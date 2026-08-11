import type {
  DailyMetric,
  DashboardData,
  EncodedTable,
  RawDashboardPayload,
  TimetableBlock,
  WeeklyMetric,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function decodeTable<T extends object>(
  table: EncodedTable,
  label: string,
): T[] {
  if (!Array.isArray(table.columns) || !Array.isArray(table.rows)) {
    throw new Error(`${label} does not use the expected table encoding.`);
  }
  if (new Set(table.columns).size !== table.columns.length) {
    throw new Error(`${label} contains duplicate columns.`);
  }

  return table.rows.map((row, rowIndex) => {
    if (!Array.isArray(row) || row.length !== table.columns.length) {
      throw new Error(`${label} row ${rowIndex + 1} has the wrong width.`);
    }
    return Object.fromEntries(
      table.columns.map((column, columnIndex) => [column, row[columnIndex]]),
    ) as T;
  });
}

function validatePayload(value: unknown): RawDashboardPayload {
  if (!isRecord(value)) {
    throw new Error("The dashboard data is not a JSON object.");
  }
  if (value.schema_version !== 2) {
    throw new Error(`Unsupported dashboard schema: ${String(value.schema_version)}.`);
  }
  if (value.dataset_kind !== "latest_snapshot") {
    throw new Error("The dashboard data is not the latest-snapshot export.");
  }
  if (value.table_encoding !== "columns_and_rows") {
    throw new Error("The dashboard data uses an unsupported table encoding.");
  }
  for (const field of [
    "snapshot",
    "scoring",
    "filters",
    "weekly_metrics",
    "daily_metrics",
    "timetable_blocks",
  ]) {
    if (!isRecord(value[field])) {
      throw new Error(`Dashboard field ${field} is missing or invalid.`);
    }
  }
  if (!Array.isArray(value.weeks) || !Array.isArray(value.intakes)) {
    throw new Error("Dashboard weeks or intake metadata is invalid.");
  }
  return value as unknown as RawDashboardPayload;
}

export function parseDashboardData(value: unknown): DashboardData {
  const payload = validatePayload(value);
  return {
    schemaVersion: payload.schema_version,
    timezone: payload.timezone,
    snapshot: payload.snapshot,
    scoring: payload.scoring,
    weeks: payload.weeks,
    filters: payload.filters,
    intakes: payload.intakes,
    weeklyMetrics: decodeTable<WeeklyMetric>(
      payload.weekly_metrics,
      "Weekly metrics",
    ),
    dailyMetrics: decodeTable<DailyMetric>(
      payload.daily_metrics,
      "Daily metrics",
    ),
    timetableBlocks: decodeTable<TimetableBlock>(
      payload.timetable_blocks,
      "Timetable blocks",
    ),
  };
}

export async function loadDashboardData(signal?: AbortSignal): Promise<DashboardData> {
  const response = await fetch(`${import.meta.env.BASE_URL}data/latest.json`, {
    signal,
  });
  if (!response.ok) {
    throw new Error(
      `Could not load dashboard data, HTTP ${response.status}. Run Stage 8 first.`,
    );
  }
  return parseDashboardData((await response.json()) as unknown);
}
