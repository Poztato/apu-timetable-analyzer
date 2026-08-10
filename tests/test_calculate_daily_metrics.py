from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd

from scripts.build_timetable_variants import construct_timetable_variants
from scripts.calculate_daily_metrics import (
    DailyMetricError,
    calculate_daily_metrics,
    calculate_snapshot_daily_metrics,
    parse_scoring_config,
)
from scripts.process_snapshots import normalize_events
from tests.test_fetch_timetable import BASE_EVENT


INTAKE_CONFIG = {
    "programme_routes": {"APD": "Dual-degree programme"},
    "courses": {"CS": {"name": "Computer Science", "school": None}},
    "specialisms": {"DA": "Data Analytics"},
}

SCORING_CONFIG = {
    "early_start": "09:30",
    "late_start": "15:00",
    "one_hour_max_teaching_minutes": 60,
    "overload_teaching_minutes": 360,
    "overload_event_count": 4,
}
THRESHOLDS = parse_scoring_config(SCORING_CONFIG)


def source_event(
    identifier: str,
    start_at: str,
    end_at: str,
    grouping: str = "G1",
    delivery_mode: str = "campus",
) -> dict[str, object]:
    event = deepcopy(BASE_EVENT)
    start = datetime.fromisoformat(start_at)
    end = datetime.fromisoformat(end_at)
    event["MODID"] = identifier
    event["MODULE_NAME"] = identifier
    event["GROUPING"] = grouping
    event["DATESTAMP"] = start.strftime("%d-%b-%y").upper()
    event["DATESTAMP_ISO"] = start.date().isoformat()
    event["DAY"] = start.strftime("%a").upper()
    event["TIME_FROM"] = start.strftime("%I:%M %p").lstrip("0")
    event["TIME_TO"] = end.strftime("%I:%M %p").lstrip("0")
    event["TIME_FROM_ISO"] = start.isoformat()
    event["TIME_TO_ISO"] = end.isoformat()
    event["CLASS_CODE"] = f"CLASS-{identifier}"
    if delivery_mode == "online":
        event["LOCATION"] = "ONL"
        event["ROOM"] = "ONLINE"
    else:
        event["LOCATION"] = "APU CAMPUS"
        event["ROOM"] = f"ROOM-{identifier}"
    return event


def make_variant_events(*source_events: dict[str, object]) -> pd.DataFrame:
    cleaned, _ = normalize_events(
        list(source_events), "snapshot-one", INTAKE_CONFIG
    )
    variants, _ = construct_timetable_variants(cleaned)
    return variants


def metric_row(*source_events: dict[str, object]) -> pd.Series:
    metrics = calculate_daily_metrics(
        make_variant_events(*source_events), THRESHOLDS
    )
    if len(metrics) != 1:
        raise AssertionError(f"Expected one daily row, found {len(metrics)}.")
    return metrics.iloc[0]


class DailyMeasureTests(unittest.TestCase):
    def test_calculates_450_minute_gap(self) -> None:
        row = metric_row(
            source_event(
                "EARLY",
                "2026-08-03T08:30:00+08:00",
                "2026-08-03T09:30:00+08:00",
            ),
            source_event(
                "LATE",
                "2026-08-03T17:00:00+08:00",
                "2026-08-03T18:00:00+08:00",
            ),
        )

        self.assertEqual(row["event_count"], 2)
        self.assertEqual(row["merged_block_count"], 2)
        self.assertEqual(row["teaching_minutes"], 120)
        self.assertEqual(row["span_minutes"], 570)
        self.assertEqual(row["total_gap_minutes"], 450)
        self.assertEqual(row["longest_gap_minutes"], 450)

    def test_merges_directly_touching_classes(self) -> None:
        row = metric_row(
            source_event(
                "FIRST",
                "2026-08-03T08:30:00+08:00",
                "2026-08-03T09:30:00+08:00",
            ),
            source_event(
                "SECOND",
                "2026-08-03T09:30:00+08:00",
                "2026-08-03T11:30:00+08:00",
            ),
        )

        self.assertEqual(row["merged_block_count"], 1)
        self.assertEqual(row["teaching_minutes"], 180)
        self.assertEqual(row["span_minutes"], 180)
        self.assertEqual(row["total_gap_minutes"], 0)
        self.assertEqual(row["overlap_pair_count"], 0)

    def test_merges_partial_overlap_and_reports_pair(self) -> None:
        row = metric_row(
            source_event(
                "FIRST",
                "2026-08-03T10:00:00+08:00",
                "2026-08-03T12:00:00+08:00",
            ),
            source_event(
                "SECOND",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
            ),
        )

        self.assertEqual(row["merged_block_count"], 1)
        self.assertEqual(row["teaching_minutes"], 180)
        self.assertEqual(row["overlap_pair_count"], 1)
        self.assertEqual(row["exact_overlap_pair_count"], 0)

    def test_reports_exact_overlap_between_distinct_slots(self) -> None:
        row = metric_row(
            source_event(
                "FIRST",
                "2026-08-03T10:00:00+08:00",
                "2026-08-03T11:00:00+08:00",
            ),
            source_event(
                "SECOND",
                "2026-08-03T10:00:00+08:00",
                "2026-08-03T11:00:00+08:00",
            ),
        )

        self.assertEqual(row["event_count"], 2)
        self.assertEqual(row["teaching_minutes"], 60)
        self.assertEqual(row["exact_overlap_pair_count"], 1)
        self.assertEqual(row["overlap_pair_count"], 1)
        self.assertTrue(row["one_hour_only_flag"])

    def test_co_teaching_record_does_not_inflate_event_count(self) -> None:
        first = source_event(
            "SHARED",
            "2026-08-03T10:00:00+08:00",
            "2026-08-03T11:00:00+08:00",
        )
        second = deepcopy(first)
        second["LECTID"] = "SECOND"
        second["NAME"] = "SECOND LECTURER"
        second["SAMACCOUNTNAME"] = "second.lecturer"

        row = metric_row(first, second)

        self.assertEqual(row["event_record_count"], 2)
        self.assertEqual(row["event_count"], 1)
        self.assertEqual(row["exact_overlap_pair_count"], 0)
        self.assertTrue(row["one_hour_only_flag"])

    def test_derives_daily_frustration_flags(self) -> None:
        events = [
            source_event(
                "EARLY-ONLY",
                "2026-08-03T08:30:00+08:00",
                "2026-08-03T10:30:00+08:00",
            ),
            source_event(
                "LATE-ONLY",
                "2026-08-04T15:00:00+08:00",
                "2026-08-04T17:00:00+08:00",
            ),
            source_event(
                "ONE-HOUR",
                "2026-08-05T12:00:00+08:00",
                "2026-08-05T13:00:00+08:00",
            ),
            source_event(
                "LOAD-1",
                "2026-08-06T09:00:00+08:00",
                "2026-08-06T10:00:00+08:00",
            ),
            source_event(
                "LOAD-2",
                "2026-08-06T10:00:00+08:00",
                "2026-08-06T11:00:00+08:00",
            ),
            source_event(
                "LOAD-3",
                "2026-08-06T11:00:00+08:00",
                "2026-08-06T12:00:00+08:00",
            ),
            source_event(
                "LOAD-4",
                "2026-08-06T12:00:00+08:00",
                "2026-08-06T13:00:00+08:00",
            ),
        ]
        metrics = calculate_daily_metrics(make_variant_events(*events), THRESHOLDS)
        by_date = metrics.set_index("event_date")

        self.assertTrue(
            by_date.loc[pd.Timestamp("2026-08-03").date(), "early_only_flag"]
        )
        self.assertTrue(
            by_date.loc[pd.Timestamp("2026-08-04").date(), "late_only_flag"]
        )
        self.assertTrue(
            by_date.loc[pd.Timestamp("2026-08-05").date(), "one_hour_only_flag"]
        )
        self.assertTrue(
            by_date.loc[pd.Timestamp("2026-08-06").date(), "overloaded_flag"]
        )

    def test_keeps_group_daily_metrics_separate(self) -> None:
        shared_g1 = source_event(
            "SHARED",
            "2026-08-03T08:30:00+08:00",
            "2026-08-03T09:30:00+08:00",
            grouping="G1",
        )
        shared_g2 = deepcopy(shared_g1)
        shared_g2["GROUPING"] = "G2"
        group_one_late = source_event(
            "G1-LATE",
            "2026-08-03T17:00:00+08:00",
            "2026-08-03T18:00:00+08:00",
            grouping="G1",
        )
        group_two_adjacent = source_event(
            "G2-ADJACENT",
            "2026-08-03T09:30:00+08:00",
            "2026-08-03T10:30:00+08:00",
            grouping="G2",
        )

        metrics = calculate_daily_metrics(
            make_variant_events(
                shared_g1, shared_g2, group_one_late, group_two_adjacent
            ),
            THRESHOLDS,
        ).set_index("grouping")

        self.assertEqual(metrics.loc["G1", "total_gap_minutes"], 450)
        self.assertEqual(metrics.loc["G2", "total_gap_minutes"], 0)
        self.assertEqual(metrics.loc["G1", "event_count"], 2)
        self.assertEqual(metrics.loc["G2", "event_count"], 2)

    def test_counts_campus_and_online_slots(self) -> None:
        row = metric_row(
            source_event(
                "CAMPUS",
                "2026-08-03T10:00:00+08:00",
                "2026-08-03T11:00:00+08:00",
                delivery_mode="campus",
            ),
            source_event(
                "ONLINE",
                "2026-08-03T12:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
                delivery_mode="online",
            ),
        )

        self.assertEqual(row["campus_event_count"], 1)
        self.assertEqual(row["online_event_count"], 1)
        self.assertEqual(row["unknown_event_count"], 0)
        self.assertTrue(row["one_hour_only_flag"])

    def test_online_only_day_gets_no_commute_flags(self) -> None:
        row = metric_row(
            source_event(
                "ONLINE-ONLY",
                "2026-08-03T08:30:00+08:00",
                "2026-08-03T09:30:00+08:00",
                delivery_mode="online",
            )
        )

        self.assertFalse(row["early_only_flag"])
        self.assertFalse(row["late_only_flag"])
        self.assertFalse(row["one_hour_only_flag"])

    def test_mixed_day_uses_only_campus_slot_for_commute_flags(self) -> None:
        row = metric_row(
            source_event(
                "ONLINE-EARLY",
                "2026-08-03T09:00:00+08:00",
                "2026-08-03T11:00:00+08:00",
                delivery_mode="online",
            ),
            source_event(
                "CAMPUS-LATE",
                "2026-08-03T15:00:00+08:00",
                "2026-08-03T16:00:00+08:00",
                delivery_mode="campus",
            ),
        )

        self.assertFalse(row["early_only_flag"])
        self.assertTrue(row["late_only_flag"])
        self.assertTrue(row["one_hour_only_flag"])

    def test_retains_nullable_intake_metadata_types(self) -> None:
        metrics = calculate_daily_metrics(
            make_variant_events(
                source_event(
                    "CLASS",
                    "2026-08-03T10:00:00+08:00",
                    "2026-08-03T11:00:00+08:00",
                )
            ),
            THRESHOLDS,
        )

        self.assertEqual(str(metrics["academic_level"].dtype), "Int64")
        self.assertEqual(str(metrics["intake_year"].dtype), "Int64")
        self.assertEqual(str(metrics["school"].dtype), "string")


class ScoringConfigTests(unittest.TestCase):
    def test_rejects_reversed_time_thresholds(self) -> None:
        invalid = {**SCORING_CONFIG, "early_start": "16:00"}

        with self.assertRaisesRegex(DailyMetricError, "earlier"):
            parse_scoring_config(invalid)


class CalculateSnapshotDailyMetricsTests(unittest.TestCase):
    def test_writes_readable_daily_metric_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            snapshot_directory = (
                repository_root / "data/processed/snapshot-one"
            )
            snapshot_directory.mkdir(parents=True)
            make_variant_events(
                source_event(
                    "CLASS",
                    "2026-08-03T10:00:00+08:00",
                    "2026-08-03T11:00:00+08:00",
                )
            ).to_parquet(
                snapshot_directory / "variant_events.parquet",
                index=False,
                engine="pyarrow",
            )

            summary = calculate_snapshot_daily_metrics(
                "snapshot-one", repository_root, THRESHOLDS
            )

            output_path = repository_root / summary["output_path"]
            round_trip = pd.read_parquet(output_path, engine="pyarrow")
            self.assertTrue(output_path.is_file())
            self.assertEqual(len(round_trip), 1)
            self.assertEqual(round_trip.loc[0, "event_count"], 1)
            self.assertEqual(round_trip.loc[0, "teaching_minutes"], 60)


if __name__ == "__main__":
    unittest.main()
