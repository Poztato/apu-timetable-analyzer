export type JsonScalar = string | number | boolean | null;

export type CriterionKey =
  | "gap_burden"
  | "late_only"
  | "early_only"
  | "one_hour_only"
  | "overloaded";

export interface EncodedTable {
  columns: string[];
  rows: JsonScalar[][];
}

export interface SnapshotMetadata {
  snapshot_id: string;
  collected_at: string;
  feed_last_modified: string | null;
  minimum_event_date: string;
  maximum_event_date: string;
  source_row_count: number;
  source_intake_count: number;
  active_intake_count: number;
  week_count: number;
  variant_count: number;
  daily_record_count: number;
  timetable_block_count: number;
}

export interface WeekSummary {
  week_start: string;
  week_end: string;
  intake_count: number;
  variant_count: number;
}

export interface CodeNameOption {
  code: string;
  name: string | null;
}

export interface FilterOptions {
  programme_routes: CodeNameOption[];
  academic_levels: number[];
  intake_years: number[];
  intake_months: number[];
  courses: CodeNameOption[];
  specialisms: CodeNameOption[];
  schools: string[];
  study_modes: string[];
  parse_statuses: string[];
  groupings: string[];
  delivery_modes: string[];
}

export interface CriterionDefinition {
  key: CriterionKey;
  metric: string;
  position_weight: number;
  normalized_weight: number;
}

export interface ScoringDefinition {
  criteria: CriterionDefinition[];
  default_criterion_order: CriterionKey[];
  percentile_method: string;
  position_weights: number[];
  profile: string;
  profile_id: string;
  thresholds: {
    early_start: string;
    late_start: string;
    one_hour_max_teaching_minutes: number;
    overload_event_count: number;
    overload_teaching_minutes: number;
  };
}

export interface IntakeMetadata {
  intake_code: string;
  programme_route: string | null;
  programme_route_name: string | null;
  academic_level: number | null;
  intake_year: number | null;
  intake_month: number | null;
  course_code: string | null;
  course_name: string | null;
  specialism_code: string | null;
  specialism_name: string | null;
  school: string | null;
  study_mode: string | null;
  parse_status: string;
  parser_family: string | null;
  week_starts: string[];
  groupings: string[];
}

export interface WeeklyMetric {
  variant_index: number;
  week_start: string;
  intake_code: string;
  grouping: string;
  active_days: number;
  campus_days: number;
  online_only_days: number;
  weekend_days: number;
  total_event_records: number;
  total_events: number;
  total_merged_blocks: number;
  total_teaching_minutes: number;
  total_gap_minutes: number;
  longest_gap_minutes: number;
  days_with_gaps: number;
  days_with_exact_overlaps: number;
  days_with_overlaps: number;
  exact_overlap_pair_count: number;
  overlap_pair_count: number;
  total_campus_events: number;
  total_online_events: number;
  total_unknown_events: number;
  early_only_days: number;
  late_only_days: number;
  one_hour_only_days: number;
  overloaded_days: number;
  earliest_start: string;
  latest_end: string;
  maximum_daily_span: number;
  maximum_daily_teaching_minutes: number;
  overall_frustration: number;
  comparison_set_size: number;
  comparison_median_score: number;
  distance_from_median: number;
  best_rank: number;
  worst_rank: number;
  is_best: boolean;
  is_worst: boolean;
  is_most_average: boolean;
}

export interface DailyMetric {
  variant_index: number;
  event_date: string;
  day_of_week: string;
  is_weekend: boolean;
  event_record_count: number;
  event_count: number;
  merged_block_count: number;
  teaching_minutes: number;
  first_class_start: string;
  last_class_end: string;
  span_minutes: number;
  total_gap_minutes: number;
  longest_gap_minutes: number;
  exact_overlap_pair_count: number;
  overlap_pair_count: number;
  campus_event_count: number;
  online_event_count: number;
  unknown_event_count: number;
  early_only_flag: boolean;
  late_only_flag: boolean;
  one_hour_only_flag: boolean;
  overloaded_flag: boolean;
}

export interface TimetableBlock {
  variant_index: number;
  event_date: string;
  start_at: string;
  end_at: string;
  duration_minutes: number;
  module_id: string;
  module_name: string | null;
  class_code: string | null;
  location: string | null;
  room: string | null;
  delivery_mode: "campus" | "online" | "unknown";
  source_grouping: string;
  is_common_event: boolean;
  is_shared_slot: boolean;
  shared_group_count: number;
  color: string | null;
}

export interface RawDashboardPayload {
  schema_version: number;
  dataset_kind: string;
  table_encoding: string;
  timezone: string;
  snapshot: SnapshotMetadata;
  scoring: ScoringDefinition;
  weeks: WeekSummary[];
  filters: FilterOptions;
  intakes: IntakeMetadata[];
  weekly_metrics: EncodedTable;
  daily_metrics: EncodedTable;
  timetable_blocks: EncodedTable;
}

export interface DashboardData {
  schemaVersion: number;
  timezone: string;
  snapshot: SnapshotMetadata;
  scoring: ScoringDefinition;
  weeks: WeekSummary[];
  filters: FilterOptions;
  intakes: IntakeMetadata[];
  weeklyMetrics: WeeklyMetric[];
  dailyMetrics: DailyMetric[];
  timetableBlocks: TimetableBlock[];
}
