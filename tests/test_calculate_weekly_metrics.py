from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.calculate_weekly_metrics import (
    WeeklyMetricError,
    calculate_snapshot_weekly_metrics,
    calculate_weekly_metrics,
)


def daily_record(
    event_date: date,
    first_time: str,
    last_time: str,
    *,
    grouping: str = "G1",
    elective_profile: str = "ep-none",
    event_count: int = 1,
    merged_block_count: int = 1,
    teaching_minutes: int,
    gap_minutes: int = 0,
    longest_gap_minutes: int = 0,
    campus_events: int = 1,
    online_events: int = 0,
    unknown_events: int = 0,
    exact_overlap_pairs: int = 0,
    overlap_pairs: int = 0,
    early_only: bool = False,
    late_only: bool = False,
    one_hour_only: bool = False,
    overloaded: bool = False,
) -> dict[str, object]:
    first_class_start = pd.Timestamp(
        f"{event_date.isoformat()}T{first_time}:00+08:00"
    )
    last_class_end = pd.Timestamp(
        f"{event_date.isoformat()}T{last_time}:00+08:00"
    )
    span_minutes = int(
        (last_class_end - first_class_start).total_seconds() // 60
    )
    idle_span_minutes = span_minutes - teaching_minutes
    if idle_span_minutes < 0:
        raise AssertionError("Fixture teaching time cannot exceed its span.")
    if gap_minutes > idle_span_minutes:
        raise AssertionError("Fixture gap cannot exceed all idle span time.")
    return {
        "variant_id": f"variant-{grouping}-{elective_profile}",
        "snapshot_id": "snapshot-one",
        "week_start": date(2026, 8, 3),
        "intake_code": "APD3F2605CS(DA)",
        "grouping": grouping,
        "elective_profile": elective_profile,
        "elective_profile_name": "No active brochure elective",
        "elective_status": "not_active",
        "elective_rule_id": "test-rule",
        "event_date": event_date,
        "day_of_week": event_date.strftime("%a").upper(),
        "is_weekend": event_date.weekday() >= 5,
        "event_record_count": event_count,
        "event_count": event_count,
        "merged_block_count": merged_block_count,
        "teaching_minutes": teaching_minutes,
        "first_class_start": first_class_start,
        "last_class_end": last_class_end,
        "span_minutes": span_minutes,
        "total_gap_minutes": gap_minutes,
        "longest_gap_minutes": longest_gap_minutes,
        "exact_overlap_pair_count": exact_overlap_pairs,
        "overlap_pair_count": overlap_pairs,
        "campus_event_count": campus_events,
        "online_event_count": online_events,
        "unknown_event_count": unknown_events,
        "early_only_flag": early_only,
        "late_only_flag": late_only,
        "one_hour_only_flag": one_hour_only,
        "overloaded_flag": overloaded,
        "programme_route": "APD",
        "programme_route_name": "Dual-degree programme",
        "programme_level": "degree",
        "programme_level_name": "Degree",
        "academic_level": 3,
        "intake_year": 2026,
        "intake_month": 5,
        "course_code": "CS",
        "course_name": "Computer Science",
        "specialism_code": "DA",
        "specialism_name": "Data Analytics",
        "school": None,
        "study_mode": None,
        "parse_status": "parsed",
        "parser_family": "apu_degree",
    }


def daily_frame(*records: dict[str, object]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    for column in (
        "is_weekend",
        "early_only_flag",
        "late_only_flag",
        "one_hour_only_flag",
        "overloaded_flag",
    ):
        frame[column] = frame[column].astype("bool")
    return frame


class WeeklyAggregationTests(unittest.TestCase):
    def test_keeps_elective_profiles_separate_within_one_group(self) -> None:
        first = daily_record(
            date(2026, 8, 3),
            "10:00",
            "11:00",
            elective_profile="ep-cloud",
            teaching_minutes=60,
        )
        second = daily_record(
            date(2026, 8, 3),
            "12:00",
            "13:00",
            elective_profile="ep-iot",
            teaching_minutes=60,
        )

        weekly = calculate_weekly_metrics(daily_frame(first, second))

        self.assertEqual(len(weekly), 2)
        self.assertEqual(set(weekly["elective_profile"]), {"ep-cloud", "ep-iot"})

    def test_aggregates_planned_weekly_measures(self) -> None:
        daily = daily_frame(
            daily_record(
                date(2026, 8, 3),
                "08:30",
                "18:00",
                event_count=2,
                merged_block_count=2,
                teaching_minutes=120,
                gap_minutes=450,
                longest_gap_minutes=450,
                campus_events=2,
            ),
            daily_record(
                date(2026, 8, 4),
                "15:00",
                "17:00",
                teaching_minutes=120,
                late_only=True,
            ),
            daily_record(
                date(2026, 8, 8),
                "08:30",
                "09:30",
                teaching_minutes=60,
                early_only=True,
                one_hour_only=True,
            ),
            daily_record(
                date(2026, 8, 5),
                "12:00",
                "13:00",
                teaching_minutes=60,
                campus_events=0,
                online_events=1,
            ),
            daily_record(
                date(2026, 8, 6),
                "09:00",
                "13:00",
                event_count=4,
                merged_block_count=1,
                teaching_minutes=240,
                campus_events=4,
                overloaded=True,
            ),
        )

        row = calculate_weekly_metrics(daily).iloc[0]

        self.assertEqual(row["active_days"], 5)
        self.assertEqual(row["campus_days"], 4)
        self.assertEqual(row["online_only_days"], 1)
        self.assertEqual(row["weekend_days"], 1)
        self.assertEqual(row["total_events"], 9)
        self.assertEqual(row["total_teaching_minutes"], 600)
        self.assertEqual(row["total_gap_minutes"], 450)
        self.assertEqual(row["longest_gap_minutes"], 450)
        self.assertEqual(row["days_with_gaps"], 1)
        self.assertEqual(row["early_only_days"], 1)
        self.assertEqual(row["late_only_days"], 1)
        self.assertEqual(row["one_hour_only_days"], 1)
        self.assertEqual(row["overloaded_days"], 1)
        self.assertEqual(row["earliest_start"], "08:30")
        self.assertEqual(row["latest_end"], "18:00")
        self.assertEqual(row["maximum_daily_span"], 570)

    def test_compares_clock_times_instead_of_calendar_dates(self) -> None:
        daily = daily_frame(
            daily_record(
                date(2026, 8, 3),
                "11:00",
                "13:00",
                teaching_minutes=120,
            ),
            daily_record(
                date(2026, 8, 4),
                "08:30",
                "09:30",
                teaching_minutes=60,
            ),
        )

        row = calculate_weekly_metrics(daily).iloc[0]

        self.assertEqual(row["earliest_start"], "08:30")
        self.assertEqual(row["latest_end"], "13:00")

    def test_accepts_campus_bound_gap_smaller_than_all_idle_time(self) -> None:
        daily = daily_frame(
            daily_record(
                date(2026, 8, 3),
                "08:00",
                "16:00",
                event_count=3,
                merged_block_count=3,
                teaching_minutes=180,
                gap_minutes=180,
                longest_gap_minutes=180,
                campus_events=2,
                online_events=1,
            )
        )

        row = calculate_weekly_metrics(daily).iloc[0]

        self.assertEqual(row["total_gap_minutes"], 180)
        self.assertEqual(row["maximum_daily_span"], 480)

    def test_keeps_group_weeks_separate(self) -> None:
        group_one = daily_record(
            date(2026, 8, 3),
            "08:30",
            "18:00",
            grouping="G1",
            event_count=2,
            merged_block_count=2,
            teaching_minutes=120,
            gap_minutes=450,
            longest_gap_minutes=450,
            campus_events=2,
        )
        group_two = daily_record(
            date(2026, 8, 3),
            "08:30",
            "10:30",
            grouping="G2",
            event_count=2,
            merged_block_count=1,
            teaching_minutes=120,
            campus_events=2,
        )

        weekly = calculate_weekly_metrics(
            daily_frame(group_one, group_two)
        ).set_index("grouping")

        self.assertEqual(len(weekly), 2)
        self.assertEqual(weekly.loc["G1", "total_gap_minutes"], 450)
        self.assertEqual(weekly.loc["G2", "total_gap_minutes"], 0)

    def test_aggregates_overlap_diagnostics(self) -> None:
        daily = daily_frame(
            daily_record(
                date(2026, 8, 3),
                "10:00",
                "11:00",
                event_count=2,
                merged_block_count=1,
                teaching_minutes=60,
                campus_events=2,
                exact_overlap_pairs=1,
                overlap_pairs=1,
            ),
            daily_record(
                date(2026, 8, 4),
                "10:00",
                "13:00",
                event_count=2,
                merged_block_count=1,
                teaching_minutes=180,
                campus_events=2,
                overlap_pairs=1,
            ),
        )

        row = calculate_weekly_metrics(daily).iloc[0]

        self.assertEqual(row["days_with_exact_overlaps"], 1)
        self.assertEqual(row["days_with_overlaps"], 2)
        self.assertEqual(row["exact_overlap_pair_count"], 1)
        self.assertEqual(row["overlap_pair_count"], 2)

    def test_preserves_nullable_metadata_types(self) -> None:
        weekly = calculate_weekly_metrics(
            daily_frame(
                daily_record(
                    date(2026, 8, 3),
                    "10:00",
                    "11:00",
                    teaching_minutes=60,
                )
            )
        )

        self.assertEqual(str(weekly["academic_level"].dtype), "Int64")
        self.assertEqual(str(weekly["school"].dtype), "string")

    def test_rejects_duplicate_daily_key(self) -> None:
        row = daily_record(
            date(2026, 8, 3),
            "10:00",
            "11:00",
            teaching_minutes=60,
        )
        duplicated = daily_frame(row, row)

        with self.assertRaisesRegex(WeeklyMetricError, "duplicate daily keys"):
            calculate_weekly_metrics(duplicated)


class CalculateSnapshotWeeklyMetricsTests(unittest.TestCase):
    def test_writes_readable_weekly_metric_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            snapshot_directory = (
                repository_root / "data/processed/snapshot-one"
            )
            snapshot_directory.mkdir(parents=True)
            daily_frame(
                daily_record(
                    date(2026, 8, 3),
                    "10:00",
                    "11:00",
                    teaching_minutes=60,
                )
            ).to_parquet(
                snapshot_directory / "daily_metrics.parquet",
                index=False,
                engine="pyarrow",
            )

            summary = calculate_snapshot_weekly_metrics(
                "snapshot-one", repository_root
            )

            output_path = repository_root / summary["output_path"]
            round_trip = pd.read_parquet(output_path, engine="pyarrow")
            self.assertTrue(output_path.is_file())
            self.assertEqual(len(round_trip), 1)
            self.assertEqual(round_trip.loc[0, "active_days"], 1)
            self.assertEqual(round_trip.loc[0, "total_events"], 1)


if __name__ == "__main__":
    unittest.main()
