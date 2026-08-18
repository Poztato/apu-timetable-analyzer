from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_dashboard_data import DashboardDataError, build_dashboard_data
from scripts.calculate_daily_metrics import calculate_daily_metrics
from scripts.calculate_weekly_metrics import calculate_weekly_metrics
from scripts.rank_timetables import rank_weekly_metrics
from tests.scoring_fixtures import SCORING_CONFIG, SCORING_MODEL
from tests.test_calculate_daily_metrics import make_variant_events, source_event


SNAPSHOT_ID = "snapshot-one"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def table_records(table: dict[str, object]) -> list[dict[str, object]]:
    columns = table["columns"]
    rows = table["rows"]
    assert isinstance(columns, list)
    assert isinstance(rows, list)
    return [dict(zip(columns, row)) for row in rows]


def pipeline_frames():
    variants = make_variant_events(
        source_event(
            "GOOD",
            "2026-08-03T11:00:00+08:00",
            "2026-08-03T14:00:00+08:00",
            grouping="G1",
        ),
        source_event(
            "BAD-EARLY",
            "2026-08-03T08:30:00+08:00",
            "2026-08-03T09:30:00+08:00",
            grouping="G2",
        ),
        source_event(
            "BAD-LATE",
            "2026-08-03T17:00:00+08:00",
            "2026-08-03T18:00:00+08:00",
            grouping="G2",
        ),
    )
    daily = calculate_daily_metrics(variants, SCORING_MODEL)
    weekly = calculate_weekly_metrics(daily, SCORING_MODEL)
    rankings = rank_weekly_metrics(weekly, SCORING_MODEL)
    return variants, daily, weekly, rankings


def create_repository(repository_root: Path) -> None:
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
    variants, daily, weekly, rankings = pipeline_frames()
    processed = repository_root / "data/processed" / SNAPSHOT_ID
    processed.mkdir(parents=True)
    variants.to_parquet(processed / "variant_events.parquet", index=False)
    daily.to_parquet(processed / "daily_metrics.parquet", index=False)
    weekly.to_parquet(processed / "intake_week_metrics.parquet", index=False)
    rankings.to_parquet(processed / "default_rankings.parquet", index=False)


class DashboardExportTests(unittest.TestCase):
    def test_exports_schema_four_with_one_scoring_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_repository(root)
            summary = build_dashboard_data(root)
            latest = json.loads(
                (root / "web/public/data/latest.json").read_text(encoding="utf-8")
            )
            history = json.loads(
                (
                    root
                    / f"web/public/data/history/{SNAPSHOT_ID}.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(summary["schema_version"], 4)
        self.assertEqual(latest["schema_version"], 4)
        self.assertEqual(latest["scoring"]["profile_id"], SCORING_MODEL.profile_id)
        self.assertEqual(
            latest["scoring"]["score_method"], "absolute_daily_cost_v1"
        )
        self.assertEqual(latest["scoring"]["physical_day_minimum"], 20)
        self.assertEqual(latest["scoring"]["online_day_maximum"], 19)
        self.assertNotIn("default_criterion_order", latest["scoring"])
        self.assertNotIn("ranking.json", json.dumps(latest))
        self.assertIn("daily_metrics", latest)
        self.assertIn("timetable_blocks", latest)
        self.assertNotIn("daily_metrics", history)
        self.assertNotIn("timetable_blocks", history)

        weekly_rows = table_records(latest["weekly_metrics"])
        self.assertEqual(len(weekly_rows), 2)
        self.assertIn("overall_score", weekly_rows[0])
        self.assertIn("total_campus_waiting_minutes", weekly_rows[0])
        self.assertNotIn("total_gap_minutes", weekly_rows[0])

        daily_rows = table_records(latest["daily_metrics"])
        self.assertIn("placement_deviation_minutes", daily_rows[0])
        self.assertIn("balanced_day_score", daily_rows[0])
        self.assertNotIn("early_only_flag", daily_rows[0])

    def test_export_is_deterministic_and_excludes_private_event_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_repository(root)
            first = build_dashboard_data(root)
            first_bytes = (root / "web/public/data/latest.json").read_bytes()
            second = build_dashboard_data(root)
            second_bytes = (root / "web/public/data/latest.json").read_bytes()

        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first["files"], second["files"])
        payload_text = first_bytes.decode("utf-8")
        for private_field in (
            "lecturer_id",
            "lecturer_name",
            "lecturer_account",
            "variant_event_id",
        ):
            self.assertNotIn(private_field, payload_text)

    def test_rejects_rankings_from_another_scoring_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_repository(root)
            ranking_path = (
                root
                / "data/processed"
                / SNAPSHOT_ID
                / "default_rankings.parquet"
            )
            rankings = rank_weekly_metrics(pipeline_frames()[2], SCORING_MODEL)
            rankings.loc[:, "scoring_profile_id"] = "wrong-profile"
            rankings.to_parquet(ranking_path, index=False)

            with self.assertRaisesRegex(
                DashboardDataError, "configured scoring model"
            ):
                build_dashboard_data(root)

    def test_rejects_missing_scoring_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_repository(root)
            (root / "config/scoring.json").unlink()
            with self.assertRaisesRegex(DashboardDataError, "Cannot find"):
                build_dashboard_data(root)


if __name__ == "__main__":
    unittest.main()
