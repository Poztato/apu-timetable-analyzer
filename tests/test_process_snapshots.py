from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.process_snapshots import (
    ProcessingError,
    normalize_events,
    parse_intake_code,
    process_snapshot,
)
from tests.test_fetch_timetable import BASE_EVENT, make_feed


INTAKE_CONFIG = {
    "programme_routes": {
        "APD": "Dual-degree programme",
        "APU": "APU programme",
    },
    "courses": {"CS": {"name": "Computer Science", "school": None}},
    "specialisms": {"DA": "Data Analytics"},
}


class IntakeParsingTests(unittest.TestCase):
    def test_parses_known_degree_intake(self) -> None:
        parsed = parse_intake_code("APD3F2605CS(DA)", INTAKE_CONFIG)

        self.assertEqual(parsed["programme_route"], "APD")
        self.assertEqual(parsed["academic_level"], 3)
        self.assertEqual(parsed["intake_year"], 2026)
        self.assertEqual(parsed["intake_month"], 5)
        self.assertEqual(parsed["course_code"], "CS")
        self.assertEqual(parsed["course_name"], "Computer Science")
        self.assertEqual(parsed["specialism_code"], "DA")
        self.assertEqual(parsed["specialism_name"], "Data Analytics")
        self.assertEqual(parsed["parse_status"], "parsed")

    def test_leaves_unknown_format_unparsed(self) -> None:
        parsed = parse_intake_code("APDMF2508MBA(BA)(PR)", INTAKE_CONFIG)

        self.assertEqual(parsed["parse_status"], "unparsed")
        self.assertIsNone(parsed["programme_route"])
        self.assertIsNone(parsed["course_code"])

    def test_parses_hyphenated_specialism_code(self) -> None:
        parsed = parse_intake_code("APD2F2602BM(E-BUS)", INTAKE_CONFIG)

        self.assertEqual(parsed["parse_status"], "parsed")
        self.assertEqual(parsed["course_code"], "BM")
        self.assertEqual(parsed["specialism_code"], "E-BUS")


class NormalizeEventsTests(unittest.TestCase):
    def test_normalizes_event_and_removes_exact_duplicate(self) -> None:
        event = deepcopy(BASE_EVENT)
        event["GROUPING"] = "  "

        frame, statistics = normalize_events(
            [event, deepcopy(event)], "snapshot-one", INTAKE_CONFIG
        )

        self.assertEqual(statistics["source_row_count"], 2)
        self.assertEqual(statistics["output_row_count"], 1)
        self.assertEqual(statistics["duplicates_removed"], 1)
        self.assertEqual(frame.loc[0, "grouping"], "ALL")
        self.assertEqual(frame.loc[0, "duration_minutes"], 120)
        self.assertEqual(frame.loc[0, "event_date"], date(2026, 8, 3))
        self.assertEqual(frame.loc[0, "week_start"], date(2026, 8, 3))
        self.assertEqual(frame.loc[0, "delivery_mode"], "campus")
        self.assertEqual(frame.loc[0, "lecturer_name"], "EXAMPLE LECTURER")
        self.assertEqual(str(frame.loc[0, "start_at"].tz), "Asia/Kuala_Lumpur")

    def test_event_id_is_stable_across_snapshots(self) -> None:
        first, _ = normalize_events([BASE_EVENT], "snapshot-one", INTAKE_CONFIG)
        second, _ = normalize_events([BASE_EVENT], "snapshot-two", INTAKE_CONFIG)

        self.assertEqual(first.loc[0, "event_id"], second.loc[0, "event_id"])
        self.assertNotEqual(first.loc[0, "snapshot_id"], second.loc[0, "snapshot_id"])

    def test_co_teachers_receive_distinct_event_ids(self) -> None:
        second_lecturer = deepcopy(BASE_EVENT)
        second_lecturer["LECTID"] = "SECOND"
        second_lecturer["NAME"] = "SECOND LECTURER"
        second_lecturer["SAMACCOUNTNAME"] = "second.lecturer"

        frame, statistics = normalize_events(
            [BASE_EVENT, second_lecturer], "snapshot-one", INTAKE_CONFIG
        )

        self.assertEqual(statistics["output_row_count"], 2)
        self.assertEqual(statistics["duplicates_removed"], 0)
        self.assertTrue(frame["event_id"].is_unique)

    def test_classifies_online_room(self) -> None:
        event = deepcopy(BASE_EVENT)
        event["LOCATION"] = "APU CAMPUS"
        event["ROOM"] = "ONLMCO3-10"

        frame, _ = normalize_events([event], "snapshot-one", INTAKE_CONFIG)

        self.assertEqual(frame.loc[0, "delivery_mode"], "online")

    def test_rejects_missing_source_column(self) -> None:
        event = deepcopy(BASE_EVENT)
        del event["ROOM"]

        with self.assertRaisesRegex(ProcessingError, "ROOM"):
            normalize_events([event], "snapshot-one", INTAKE_CONFIG)


class ProcessSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.temporary_directory.name)
        self.snapshot_id = "2026-08-10T13-30-57Z_example"
        self.feed = make_feed(BASE_EVENT)
        self.snapshot_path = (
            self.repository_root / "data/snapshots/raw/example.json.gz"
        )
        self.snapshot_path.parent.mkdir(parents=True)
        with gzip.open(self.snapshot_path, "wb") as snapshot_file:
            snapshot_file.write(self.feed)
        self.index_entry = {
            "snapshot_id": self.snapshot_id,
            "sha256": hashlib.sha256(self.feed).hexdigest(),
            "path": "data/snapshots/raw/example.json.gz",
            "row_count": 1,
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_writes_readable_parquet_with_lecturer_fields(self) -> None:
        summary = process_snapshot(
            self.index_entry, self.repository_root, INTAKE_CONFIG
        )

        output_path = self.repository_root / summary["output_path"]
        self.assertTrue(output_path.is_file())
        round_trip = pd.read_parquet(output_path)
        self.assertEqual(len(round_trip), 1)
        self.assertEqual(round_trip.loc[0, "lecturer_id"], "KST")
        self.assertEqual(round_trip.loc[0, "lecturer_name"], "EXAMPLE LECTURER")
        self.assertEqual(round_trip.loc[0, "lecturer_account"], "example.lecturer")

    def test_rejects_snapshot_hash_mismatch(self) -> None:
        bad_entry = {**self.index_entry, "sha256": "0" * 64}

        with self.assertRaisesRegex(ProcessingError, "hash mismatch"):
            process_snapshot(bad_entry, self.repository_root, INTAKE_CONFIG)


if __name__ == "__main__":
    unittest.main()
