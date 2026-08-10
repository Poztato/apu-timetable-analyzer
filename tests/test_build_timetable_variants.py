from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import pandas as pd

from scripts.build_timetable_variants import (
    VariantError,
    build_snapshot_variants,
    construct_timetable_variants,
)
from scripts.process_snapshots import normalize_events
from tests.test_fetch_timetable import BASE_EVENT


INTAKE_CONFIG = {
    "programme_routes": {"APD": "Dual-degree programme"},
    "courses": {"CS": {"name": "Computer Science", "school": None}},
    "specialisms": {"DA": "Data Analytics"},
}


def make_clean_events(*source_events: dict[str, object]) -> pd.DataFrame:
    frame, _ = normalize_events(
        list(source_events), "snapshot-one", INTAKE_CONFIG
    )
    return frame


def move_event(
    event: dict[str, object],
    start_at: str,
    end_at: str,
    source_date: str,
) -> dict[str, object]:
    moved = deepcopy(event)
    moved["TIME_FROM_ISO"] = start_at
    moved["TIME_TO_ISO"] = end_at
    moved["DATESTAMP_ISO"] = source_date
    return moved


class ConstructTimetableVariantsTests(unittest.TestCase):
    def test_keeps_groups_separate_and_marks_shared_slot(self) -> None:
        group_one = deepcopy(BASE_EVENT)
        group_one["GROUPING"] = "G1"
        group_two = deepcopy(BASE_EVENT)
        group_two["GROUPING"] = "G2"

        variants, statistics = construct_timetable_variants(
            make_clean_events(group_one, group_two)
        )

        self.assertEqual(statistics["variant_count"], 2)
        self.assertEqual(set(variants["grouping"]), {"G1", "G2"})
        self.assertEqual(variants["variant_id"].nunique(), 2)
        self.assertEqual(variants["slot_id"].nunique(), 1)
        self.assertTrue(variants["is_shared_slot"].all())
        self.assertEqual(set(variants["shared_group_count"]), {2})

    def test_different_group_slots_are_not_combined(self) -> None:
        group_one = deepcopy(BASE_EVENT)
        group_one["GROUPING"] = "G1"
        group_two = move_event(
            BASE_EVENT,
            "2026-08-03T13:30:00+08:00",
            "2026-08-03T15:30:00+08:00",
            "2026-08-03",
        )
        group_two["GROUPING"] = "G2"

        variants, _ = construct_timetable_variants(
            make_clean_events(group_one, group_two)
        )

        self.assertEqual(variants["slot_id"].nunique(), 2)
        self.assertFalse(variants["is_shared_slot"].any())
        self.assertEqual(len(variants.loc[variants["grouping"] == "G1"]), 1)
        self.assertEqual(len(variants.loc[variants["grouping"] == "G2"]), 1)

    def test_expands_common_event_into_each_explicit_group(self) -> None:
        common = deepcopy(BASE_EVENT)
        common["GROUPING"] = ""
        group_one = move_event(
            BASE_EVENT,
            "2026-08-04T10:30:00+08:00",
            "2026-08-04T12:30:00+08:00",
            "2026-08-04",
        )
        group_one["GROUPING"] = "G1"
        group_one["MODID"] = "GROUP-ONE"
        group_one["CLASS_CODE"] = "GROUP-ONE-CLASS"
        group_two = move_event(
            BASE_EVENT,
            "2026-08-05T10:30:00+08:00",
            "2026-08-05T12:30:00+08:00",
            "2026-08-05",
        )
        group_two["GROUPING"] = "G2"
        group_two["MODID"] = "GROUP-TWO"
        group_two["CLASS_CODE"] = "GROUP-TWO-CLASS"

        variants, statistics = construct_timetable_variants(
            make_clean_events(common, group_one, group_two)
        )

        common_assignments = variants.loc[variants["is_common_event"]]
        self.assertEqual(statistics["source_event_count"], 3)
        self.assertEqual(statistics["variant_event_count"], 4)
        self.assertEqual(len(common_assignments), 2)
        self.assertEqual(set(common_assignments["grouping"]), {"G1", "G2"})
        self.assertEqual(set(common_assignments["source_grouping"]), {"ALL"})
        self.assertNotIn("ALL", set(variants["grouping"]))

    def test_uses_groups_seen_in_other_weeks_for_common_events(self) -> None:
        common = deepcopy(BASE_EVENT)
        common["GROUPING"] = "ALL"
        group_one = move_event(
            BASE_EVENT,
            "2026-08-10T10:30:00+08:00",
            "2026-08-10T12:30:00+08:00",
            "2026-08-10",
        )
        group_one["GROUPING"] = "G1"
        group_two = deepcopy(group_one)
        group_two["GROUPING"] = "G2"

        variants, _ = construct_timetable_variants(
            make_clean_events(common, group_one, group_two)
        )

        first_week = variants.loc[
            variants["week_start"] == pd.Timestamp("2026-08-03").date()
        ]
        self.assertEqual(set(first_week["grouping"]), {"G1", "G2"})
        self.assertTrue(first_week["is_common_event"].all())

    def test_keeps_all_variant_when_no_explicit_groups_exist(self) -> None:
        common = deepcopy(BASE_EVENT)
        common["GROUPING"] = "ALL"

        variants, statistics = construct_timetable_variants(
            make_clean_events(common)
        )

        self.assertEqual(statistics["variant_count"], 1)
        self.assertEqual(set(variants["grouping"]), {"ALL"})
        self.assertEqual(set(variants["source_grouping"]), {"ALL"})
        self.assertFalse(variants["is_shared_slot"].any())

    def test_co_teachers_share_slot_without_sharing_event_identity(self) -> None:
        first_lecturer = deepcopy(BASE_EVENT)
        second_lecturer = deepcopy(BASE_EVENT)
        second_lecturer["LECTID"] = "SECOND"
        second_lecturer["NAME"] = "SECOND LECTURER"
        second_lecturer["SAMACCOUNTNAME"] = "second.lecturer"

        variants, statistics = construct_timetable_variants(
            make_clean_events(first_lecturer, second_lecturer)
        )

        self.assertEqual(variants["event_id"].nunique(), 2)
        self.assertEqual(variants["variant_event_id"].nunique(), 2)
        self.assertEqual(variants["slot_id"].nunique(), 1)
        self.assertFalse(variants["is_shared_slot"].any())
        self.assertEqual(statistics["multi_record_variant_slot_count"], 1)

    def test_rejects_duplicate_source_event_ids(self) -> None:
        events = make_clean_events(BASE_EVENT)
        duplicated = pd.concat([events, events], ignore_index=True)

        with self.assertRaisesRegex(VariantError, "duplicate event IDs"):
            construct_timetable_variants(duplicated)


class BuildSnapshotVariantsTests(unittest.TestCase):
    def test_writes_readable_variant_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            snapshot_directory = (
                repository_root / "data/processed/snapshot-one"
            )
            snapshot_directory.mkdir(parents=True)
            make_clean_events(BASE_EVENT).to_parquet(
                snapshot_directory / "events.parquet",
                index=False,
                engine="pyarrow",
            )

            summary = build_snapshot_variants("snapshot-one", repository_root)

            output_path = repository_root / summary["output_path"]
            round_trip = pd.read_parquet(output_path, engine="pyarrow")
            self.assertTrue(output_path.is_file())
            self.assertEqual(len(round_trip), 1)
            self.assertEqual(round_trip.loc[0, "grouping"], "G1")
            self.assertIn("variant_id", round_trip.columns)
            self.assertIn("slot_id", round_trip.columns)


if __name__ == "__main__":
    unittest.main()
