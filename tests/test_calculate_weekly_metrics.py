from __future__ import annotations

import unittest

import pandas as pd

from scripts.calculate_daily_metrics import calculate_daily_metrics
from scripts.calculate_weekly_metrics import WeeklyMetricError, calculate_weekly_metrics
from tests.scoring_fixtures import SCORING_MODEL
from tests.test_calculate_daily_metrics import make_variant_events, source_event


def daily_metrics(*events: dict[str, object]) -> pd.DataFrame:
    return calculate_daily_metrics(make_variant_events(*events), SCORING_MODEL)


class WeeklyAggregationTests(unittest.TestCase):
    def test_aggregates_active_empty_physical_and_online_days(self) -> None:
        daily = daily_metrics(
            source_event(
                "MONDAY",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T14:00:00+08:00",
            ),
            source_event(
                "TUESDAY-ONLINE",
                "2026-08-04T12:00:00+08:00",
                "2026-08-04T13:00:00+08:00",
                delivery_mode="online",
            ),
        )
        row = calculate_weekly_metrics(daily, SCORING_MODEL).iloc[0]

        self.assertEqual(row["active_days"], 2)
        self.assertEqual(row["empty_days"], 5)
        self.assertEqual(row["physical_days"], 1)
        self.assertEqual(row["online_only_days"], 1)
        self.assertEqual(row["total_teaching_minutes"], 240)
        self.assertEqual(row["total_physical_teaching_minutes"], 180)
        self.assertEqual(row["total_physical_span_minutes"], 180)
        self.assertAlmostEqual(
            row["balanced_score"],
            daily["balanced_day_score"].sum() / 7,
            places=6,
        )
        self.assertAlmostEqual(
            row["balanced_score"],
            sum(row[column] for column in (
                "campus_trip_score",
                "online_commitment_score",
                "placement_score",
                "span_score",
                "waiting_score",
                "short_day_score",
                "long_day_score",
            )),
            places=5,
        )

    def test_aggregates_campus_waiting_and_weighted_placement(self) -> None:
        daily = daily_metrics(
            source_event(
                "MONDAY-EARLY",
                "2026-08-03T08:30:00+08:00",
                "2026-08-03T09:30:00+08:00",
            ),
            source_event(
                "MONDAY-LATE",
                "2026-08-03T17:00:00+08:00",
                "2026-08-03T18:00:00+08:00",
            ),
            source_event(
                "TUESDAY-MIDDLE",
                "2026-08-04T11:00:00+08:00",
                "2026-08-04T14:00:00+08:00",
            ),
        )
        row = calculate_weekly_metrics(daily, SCORING_MODEL).iloc[0]

        self.assertEqual(row["total_campus_waiting_minutes"], 450)
        self.assertEqual(row["longest_campus_wait_minutes"], 450)
        self.assertEqual(row["days_with_campus_waiting"], 1)
        expected_deviation = (
            daily["placement_deviation_minutes"]
            * daily["physical_teaching_minutes"]
        ).sum() / daily["physical_teaching_minutes"].sum()
        self.assertAlmostEqual(
            row["average_placement_deviation_minutes"], expected_deviation
        )

    def test_keeps_group_variants_separate(self) -> None:
        daily = daily_metrics(
            source_event(
                "GROUP-ONE",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T12:00:00+08:00",
                grouping="G1",
            ),
            source_event(
                "GROUP-TWO",
                "2026-08-03T15:00:00+08:00",
                "2026-08-03T16:00:00+08:00",
                grouping="G2",
            ),
        )
        weekly = calculate_weekly_metrics(daily, SCORING_MODEL)

        self.assertEqual(set(weekly["grouping"]), {"G1", "G2"})
        self.assertEqual(len(weekly), 2)

    def test_preserves_overlap_diagnostics(self) -> None:
        daily = daily_metrics(
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
        row = calculate_weekly_metrics(daily, SCORING_MODEL).iloc[0]

        self.assertEqual(row["days_with_overlaps"], 1)
        self.assertEqual(row["overlap_pair_count"], 1)

    def test_rejects_duplicate_daily_key(self) -> None:
        daily = daily_metrics(
            source_event(
                "MONDAY",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T12:00:00+08:00",
            )
        )
        duplicated = pd.concat([daily, daily], ignore_index=True)
        with self.assertRaisesRegex(WeeklyMetricError, "duplicate daily keys"):
            calculate_weekly_metrics(duplicated, SCORING_MODEL)

    def test_rejects_component_total_that_does_not_match_day_score(self) -> None:
        daily = daily_metrics(
            source_event(
                "MONDAY",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T12:00:00+08:00",
            )
        )
        daily.loc[:, "placement_score"] = 999
        with self.assertRaisesRegex(WeeklyMetricError, "component scores"):
            calculate_weekly_metrics(daily, SCORING_MODEL)


if __name__ == "__main__":
    unittest.main()
