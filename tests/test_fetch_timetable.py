from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from scripts.fetch_timetable import (
    SnapshotError,
    collect_snapshot,
    decode_response_body,
    validate_feed,
)


BASE_EVENT = {
    "INTAKE": "APD3F2605CS(DA)",
    "MODID": "CT120-3-3-UX-T-7",
    "MODULE_NAME": "User Experience",
    "DAY": "MON",
    "LOCATION": "APU CAMPUS",
    "ROOM": "D-07-11",
    "LECTID": "KST",
    "NAME": "EXAMPLE LECTURER",
    "SAMACCOUNTNAME": "example.lecturer",
    "DATESTAMP": "03-AUG-26",
    "DATESTAMP_ISO": "2026-08-03",
    "TIME_FROM": "10:30 AM",
    "TIME_TO": "12:30 PM",
    "TIME_FROM_ISO": "2026-08-03T10:30:00+08:00",
    "TIME_TO_ISO": "2026-08-03T12:30:00+08:00",
    "GROUPING": "G1",
    "CLASS_CODE": "MAV___CT120-3-3-UX-T-7___2026-05-11",
    "COLOR": "yellow",
}


def make_feed(*records: dict[str, object]) -> bytes:
    return json.dumps(list(records), separators=(",", ":")).encode("utf-8")


class ValidateFeedTests(unittest.TestCase):
    def test_decodes_http_gzip_response(self) -> None:
        compressed = gzip.compress(make_feed(BASE_EVENT))

        decoded = decode_response_body(compressed, {"Content-Encoding": "gzip"})

        self.assertEqual(decoded, make_feed(BASE_EVENT))

    def test_rejects_unsupported_content_encoding(self) -> None:
        with self.assertRaisesRegex(SnapshotError, "Unsupported"):
            decode_response_body(b"payload", {"Content-Encoding": "br"})

    def test_rejects_non_array_payload(self) -> None:
        with self.assertRaisesRegex(SnapshotError, "JSON array"):
            validate_feed(b"{}")

    def test_rejects_missing_required_field(self) -> None:
        event = deepcopy(BASE_EVENT)
        del event["ROOM"]
        with self.assertRaisesRegex(SnapshotError, "ROOM"):
            validate_feed(make_feed(event))

    def test_rejects_end_not_later_than_start(self) -> None:
        event = deepcopy(BASE_EVENT)
        event["TIME_TO_ISO"] = event["TIME_FROM_ISO"]
        with self.assertRaisesRegex(SnapshotError, "must be later"):
            validate_feed(make_feed(event))

    def test_returns_feed_metadata(self) -> None:
        second_event = deepcopy(BASE_EVENT)
        second_event["INTAKE"] = "APU1F2607CS"
        second_event["DATESTAMP_ISO"] = "2026-08-04"
        second_event["TIME_FROM_ISO"] = "2026-08-04T08:30:00+08:00"
        second_event["TIME_TO_ISO"] = "2026-08-04T09:30:00+08:00"

        metadata = validate_feed(make_feed(BASE_EVENT, second_event))

        self.assertEqual(metadata["row_count"], 2)
        self.assertEqual(metadata["distinct_intake_count"], 2)
        self.assertEqual(metadata["minimum_event_date"], "2026-08-03")
        self.assertEqual(metadata["maximum_event_date"], "2026-08-04")


class CollectSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.temporary_directory.name)
        self.feed = make_feed(BASE_EVENT)
        self.first_collection_time = datetime(2026, 8, 10, 11, 9, 52, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_first_collection_writes_snapshot_and_index(self) -> None:
        result = collect_snapshot(
            self.feed,
            {"ETag": '"example-etag"', "X-Amz-Version-Id": "version-1"},
            self.repository_root,
            self.first_collection_time,
            source_url="https://example.test/timetable",
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["status"], "created")
        snapshot_path = self.repository_root / result["path"]
        self.assertTrue(snapshot_path.is_file())
        with gzip.open(snapshot_path, "rb") as snapshot_file:
            self.assertEqual(snapshot_file.read(), self.feed)

        index = json.loads(
            (self.repository_root / "data/snapshots/index.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["snapshot_id"], result["snapshot_id"])
        self.assertEqual(index[0]["etag"], '"example-etag"')
        self.assertEqual(index[0]["s3_version_id"], "version-1")
        self.assertEqual(index[0]["source_url"], "https://example.test/timetable")

    def test_identical_second_collection_creates_no_duplicate(self) -> None:
        first = collect_snapshot(
            self.feed,
            {},
            self.repository_root,
            self.first_collection_time,
        )
        index_path = self.repository_root / "data/snapshots/index.json"
        index_after_first = index_path.read_bytes()

        second = collect_snapshot(
            self.feed,
            {},
            self.repository_root,
            datetime(2026, 8, 11, 11, 9, 52, tzinfo=timezone.utc),
        )

        self.assertFalse(second["changed"])
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertEqual(index_path.read_bytes(), index_after_first)
        snapshots = list((self.repository_root / "data/snapshots/raw").glob("*.json.gz"))
        self.assertEqual(len(snapshots), 1)

    def test_changed_feed_creates_second_snapshot(self) -> None:
        collect_snapshot(
            self.feed,
            {},
            self.repository_root,
            self.first_collection_time,
        )
        changed_event = deepcopy(BASE_EVENT)
        changed_event["ROOM"] = "B-08-01"

        second = collect_snapshot(
            make_feed(changed_event),
            {},
            self.repository_root,
            datetime(2026, 8, 24, 4, 30, 10, tzinfo=timezone.utc),
        )

        self.assertTrue(second["changed"])
        index = json.loads(
            (self.repository_root / "data/snapshots/index.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(index), 2)


if __name__ == "__main__":
    unittest.main()
