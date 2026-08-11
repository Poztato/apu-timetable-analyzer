from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.build_dashboard_data import (
    DashboardDataError,
    build_dashboard_data,
)
from scripts.rank_timetables import parse_ranking_config, rank_weekly_metrics


SNAPSHOT_ID = "snapshot-one"
RANKING_CONFIG = {
    "default_criterion_order": [
        "gap_burden",
        "late_only",
        "early_only",
        "one_hour_only",
        "overloaded",
    ],
    "position_weights": [5, 4, 3, 2, 1],
}
SCORING_CONFIG = {
    "early_start": "09:30",
    "late_start": "15:00",
    "one_hour_max_teaching_minutes": 60,
    "overload_teaching_minutes": 360,
    "overload_event_count": 4,
}
PROFILE = parse_ranking_config(RANKING_CONFIG)


def weekly_record(
    variant_id: str,
    grouping: str,
    *,
    gap_minutes: int,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "snapshot_id": SNAPSHOT_ID,
        "week_start": date(2026, 8, 3),
        "intake_code": "APD3F2605CS(DA)",
        "grouping": grouping,
        "elective_profile": "ep-none",
        "elective_profile_name": "No active brochure elective",
        "elective_status": "not_active",
        "elective_rule_id": "test-rule",
        "active_days": 1,
        "campus_days": 1,
        "online_only_days": 0,
        "weekend_days": 0,
        "total_event_records": 1,
        "total_events": 1,
        "total_merged_blocks": 1,
        "total_teaching_minutes": 60,
        "total_gap_minutes": gap_minutes,
        "longest_gap_minutes": gap_minutes,
        "days_with_gaps": int(gap_minutes > 0),
        "days_with_exact_overlaps": 0,
        "days_with_overlaps": 0,
        "exact_overlap_pair_count": 0,
        "overlap_pair_count": 0,
        "total_campus_events": 1,
        "total_online_events": 0,
        "total_unknown_events": 0,
        "early_only_days": 0,
        "late_only_days": 0,
        "one_hour_only_days": 1,
        "overloaded_days": 0,
        "earliest_start": "10:00",
        "latest_end": "11:00",
        "maximum_daily_span": 60,
        "maximum_daily_teaching_minutes": 60,
        "programme_route": "APD",
        "programme_route_name": "APU dual degree",
        "academic_level": 3,
        "intake_year": 2026,
        "intake_month": 5,
        "course_code": "CS",
        "course_name": "Computer Science",
        "specialism_code": "DA",
        "specialism_name": "Data Analytics",
        "school": "Computing",
        "study_mode": "Full-time",
        "parse_status": "parsed",
        "parser_family": "degree",
    }


def daily_record(variant_id: str, start_hour: int) -> dict[str, object]:
    start_at = pd.Timestamp(
        year=2026,
        month=8,
        day=3,
        hour=start_hour,
        tz="Asia/Kuala_Lumpur",
    )
    end_at = start_at + pd.Timedelta(hours=1)
    return {
        "variant_id": variant_id,
        "snapshot_id": SNAPSHOT_ID,
        "event_date": date(2026, 8, 3),
        "day_of_week": "MON",
        "is_weekend": False,
        "event_record_count": 1,
        "event_count": 1,
        "merged_block_count": 1,
        "teaching_minutes": 60,
        "first_class_start": start_at,
        "last_class_end": end_at,
        "span_minutes": 60,
        "total_gap_minutes": 0,
        "longest_gap_minutes": 0,
        "exact_overlap_pair_count": 0,
        "overlap_pair_count": 0,
        "campus_event_count": 1,
        "online_event_count": 0,
        "unknown_event_count": 0,
        "early_only_flag": False,
        "late_only_flag": False,
        "one_hour_only_flag": True,
        "overloaded_flag": False,
    }


def variant_record(
    variant_id: str,
    slot_id: str,
    grouping: str,
    lecturer_name: str,
    lecturer_account: str,
) -> dict[str, object]:
    start_at = pd.Timestamp(
        year=2026,
        month=8,
        day=3,
        hour=10 if grouping == "G1" else 12,
        tz="Asia/Kuala_Lumpur",
    )
    return {
        "variant_id": variant_id,
        "slot_id": slot_id,
        "snapshot_id": SNAPSHOT_ID,
        "event_date": date(2026, 8, 3),
        "start_at": start_at,
        "end_at": start_at + pd.Timedelta(hours=1),
        "duration_minutes": 60,
        "module_id": "CT000-3-3-DATA",
        "module_name": "Data Analytics",
        "class_code": "CLASS-DATA",
        "location": "APU CAMPUS",
        "room": "B-01-01",
        "delivery_mode": "campus",
        "source_grouping": grouping,
        "is_common_event": False,
        "is_elective": False,
        "elective_group_id": None,
        "elective_option_id": None,
        "is_shared_slot": False,
        "shared_group_count": 1,
        "color": "yellow",
        "lecturer_id": lecturer_name[-1],
        "lecturer_name": lecturer_name,
        "lecturer_account": lecturer_account,
        "source_row_number": 1,
        "event_id": f"event-{lecturer_account}",
        "variant_event_id": f"variant-event-{lecturer_account}",
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def table_records(table: dict[str, object]) -> list[dict[str, object]]:
    columns = table["columns"]
    rows = table["rows"]
    assert isinstance(columns, list)
    assert isinstance(rows, list)
    return [dict(zip(columns, row)) for row in rows]


def create_repository(repository_root: Path) -> None:
    write_json(repository_root / "config/ranking.json", RANKING_CONFIG)
    write_json(repository_root / "config/scoring.json", SCORING_CONFIG)
    write_json(
        repository_root / "data/snapshots/index.json",
        [
            {
                "snapshot_id": SNAPSHOT_ID,
                "collected_at": "2026-08-10T13:30:57Z",
                "row_count": 3,
                "distinct_intake_count": 1,
                "minimum_event_date": "2026-08-03",
                "maximum_event_date": "2026-08-09",
                "last_modified": "Fri, 07 Aug 2026 04:46:27 GMT",
            }
        ],
    )

    processed = repository_root / "data/processed" / SNAPSHOT_ID
    processed.mkdir(parents=True)
    weekly = pd.DataFrame.from_records(
        [
            weekly_record("variant-g1", "G1", gap_minutes=0),
            weekly_record("variant-g2", "G2", gap_minutes=120),
        ]
    )
    rankings = rank_weekly_metrics(weekly, PROFILE)
    daily = pd.DataFrame.from_records(
        [
            daily_record("variant-g1", 10),
            daily_record("variant-g2", 12),
        ]
    )
    variants = pd.DataFrame.from_records(
        [
            variant_record(
                "variant-g1",
                "slot-g1",
                "G1",
                "PRIVATE LECTURER A",
                "private.account.a",
            ),
            variant_record(
                "variant-g1",
                "slot-g1",
                "G1",
                "PRIVATE LECTURER B",
                "private.account.b",
            ),
            variant_record(
                "variant-g2",
                "slot-g2",
                "G2",
                "PRIVATE LECTURER C",
                "private.account.c",
            ),
        ]
    )

    weekly.to_parquet(
        processed / "intake_week_metrics.parquet",
        index=False,
        engine="pyarrow",
    )
    rankings.to_parquet(
        processed / "default_rankings.parquet",
        index=False,
        engine="pyarrow",
    )
    daily.to_parquet(
        processed / "daily_metrics.parquet", index=False, engine="pyarrow"
    )
    variants.to_parquet(
        processed / "variant_events.parquet", index=False, engine="pyarrow"
    )


class BuildDashboardDataTests(unittest.TestCase):
    def test_builds_latest_manifest_and_compact_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            create_repository(repository_root)

            summary = build_dashboard_data(repository_root)

            output = repository_root / "web/public/data"
            manifest = json.loads(
                (output / "snapshots.json").read_text(encoding="utf-8")
            )
            latest = json.loads(
                (output / "latest.json").read_text(encoding="utf-8")
            )
            history = json.loads(
                (output / "history/snapshot-one.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(summary["status"], "exported")
            self.assertEqual(summary["latest_variant_count"], 2)
            self.assertEqual(summary["latest_timetable_block_count"], 2)
            self.assertEqual(manifest["latest_snapshot_id"], SNAPSHOT_ID)
            self.assertEqual(manifest["snapshot_count"], 1)
            self.assertEqual(
                manifest["snapshots"][0]["history_file"],
                "history/snapshot-one.json",
            )
            self.assertEqual(latest["dataset_kind"], "latest_snapshot")
            self.assertEqual(latest["table_encoding"], "columns_and_rows")
            self.assertEqual(history["dataset_kind"], "snapshot_metrics")
            self.assertNotIn("daily_metrics", history)
            self.assertNotIn("timetable_blocks", history)
            self.assertEqual(latest["intakes"][0]["groupings"], ["G1", "G2"])
            weekly_metrics = table_records(latest["weekly_metrics"])
            daily_metrics = table_records(latest["daily_metrics"])
            self.assertEqual(
                [row["variant_index"] for row in weekly_metrics], [0, 1]
            )
            self.assertTrue(weekly_metrics[0]["is_best"])
            self.assertTrue(weekly_metrics[1]["is_worst"])
            self.assertEqual(
                {row["variant_index"] for row in daily_metrics},
                {0, 1},
            )
            self.assertEqual(
                latest["scoring"]["criteria"][0]["metric"],
                "total_gap_minutes",
            )

    def test_excludes_lecturer_data_and_collapses_coteaching_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            create_repository(repository_root)

            build_dashboard_data(repository_root)

            latest_path = repository_root / "web/public/data/latest.json"
            text = latest_path.read_text(encoding="utf-8")
            latest = json.loads(text)
            self.assertEqual(len(latest["timetable_blocks"]["rows"]), 2)
            for private_value in (
                "PRIVATE LECTURER A",
                "PRIVATE LECTURER B",
                "PRIVATE LECTURER C",
                "private.account.a",
                "lecturer_name",
                "lecturer_id",
                "lecturer_account",
                "source_row_number",
            ):
                self.assertNotIn(private_value, text)

    def test_repeated_build_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            create_repository(repository_root)
            build_dashboard_data(repository_root)
            output = repository_root / "web/public/data"
            first = {
                path.relative_to(output): path.read_bytes()
                for path in output.rglob("*.json")
            }

            second_summary = build_dashboard_data(repository_root)
            second = {
                path.relative_to(output): path.read_bytes()
                for path in output.rglob("*.json")
            }

            self.assertEqual(first, second)
            self.assertEqual(
                second_summary["output_size_bytes"],
                sum(len(value) for value in second.values()),
            )

    def test_rejects_rankings_with_missing_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            create_repository(repository_root)
            ranking_path = (
                repository_root
                / "data/processed"
                / SNAPSHOT_ID
                / "default_rankings.parquet"
            )
            rankings = pd.read_parquet(ranking_path, engine="pyarrow").iloc[:1]
            rankings.to_parquet(ranking_path, index=False, engine="pyarrow")

            with self.assertRaisesRegex(
                DashboardDataError, "exactly the weekly metric variants"
            ):
                build_dashboard_data(repository_root)


if __name__ == "__main__":
    unittest.main()
