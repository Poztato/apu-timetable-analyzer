export type JsonScalar = string | number | boolean | null;

export type TimePreferenceKey = "balanced" | "morning" | "afternoon";

export type ScoringComponentKey =
  | "campus_trip"
  | "online_commitment"
  | "placement"
  | "span"
  | "waiting"
  | "short_day"
  | "long_day";

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
  programme_levels: CodeNameOption[];
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

export interface TimePreferenceDefinition {
  key: TimePreferenceKey;
  label: string;
  short_label: string;
  start: string;
  end: string;
  description: string;
}

export interface RampDefinition {
  low: number;
  high: number;
  reverse: boolean;
}

export interface ScoringDefinition {
  model_version: string;
  weekly_divisor_days: number;
  default_time_preference: TimePreferenceKey;
  time_preferences: TimePreferenceDefinition[];
  component_weights: Record<
    Exclude<ScoringComponentKey, "online_commitment">,
    number
  >;
  emphasis_bonus: {
    short_day: number;
    long_day: number;
  };
  online_day: {
    base_points: number;
    span_points: number;
    load_points: number;
  };
  ramps: Record<
    "placement" | "span" | "waiting" | "short_day" | "long_day",
    RampDefinition
  >;
  profile_id: string;
  score_method: string;
  physical_day_minimum: number;
  online_day_maximum: number;
}

export interface IntakeMetadata {
  intake_code: string;
  programme_route: string | null;
  programme_route_name: string | null;
  programme_level: string;
  programme_level_name: string;
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
  elective_profile: string;
  elective_profile_name: string;
  elective_status: string;
  elective_rule_id: string;
  active_days: number;
  empty_days: number;
  physical_days: number;
  online_only_days: number;
  weekend_days: number;
  total_event_records: number;
  total_events: number;
  total_merged_blocks: number;
  total_teaching_minutes: number;
  total_physical_teaching_minutes: number;
  total_span_minutes: number;
  total_physical_span_minutes: number;
  total_campus_waiting_minutes: number;
  longest_campus_wait_minutes: number;
  days_with_campus_waiting: number;
  average_placement_deviation_minutes: number;
  days_with_exact_overlaps: number;
  days_with_overlaps: number;
  exact_overlap_pair_count: number;
  overlap_pair_count: number;
  total_physical_events: number;
  total_campus_events: number;
  total_online_events: number;
  total_unknown_events: number;
  earliest_start: string;
  latest_end: string;
  maximum_daily_span: number;
  maximum_physical_span: number;
  maximum_daily_teaching_minutes: number;
  maximum_physical_teaching_minutes: number;
  campus_trip_score: number;
  online_commitment_score: number;
  placement_score: number;
  span_score: number;
  waiting_score: number;
  short_day_score: number;
  long_day_score: number;
  balanced_score: number;
  overall_score: number;
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
  physical_teaching_minutes: number;
  first_class_start: string;
  last_class_end: string;
  span_minutes: number;
  first_physical_start: string | null;
  last_physical_end: string | null;
  physical_span_minutes: number;
  campus_waiting_minutes: number;
  longest_campus_wait_minutes: number;
  placement_deviation_minutes: number;
  exact_overlap_pair_count: number;
  overlap_pair_count: number;
  physical_event_count: number;
  campus_event_count: number;
  online_event_count: number;
  unknown_event_count: number;
  day_type: "physical" | "online";
  placement_penalty: number;
  span_penalty: number;
  waiting_penalty: number;
  short_day_penalty: number;
  long_day_penalty: number;
  campus_trip_score: number;
  online_commitment_score: number;
  placement_score: number;
  span_score: number;
  waiting_score: number;
  short_day_score: number;
  long_day_score: number;
  balanced_day_score: number;
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
  is_elective: boolean;
  elective_group_id: string | null;
  elective_option_id: string | null;
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
