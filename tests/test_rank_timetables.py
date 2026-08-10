from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.rank_timetables import (
    RankingError,
    RankingProfile,
    parse_ranking_config,
    profile_with_order,
    rank_snapshot,
    rank_weekly_metrics,
)


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
DEFAULT_PROFILE = parse_ranking_config(RANKING_CONFIG)


def weekly_record(
    intake_code: str,
    *,
    week_start: date = date(2026, 8, 3),
    grouping: str = "G1",
    gap: int = 0,
    late: int = 0,
    early: int = 0,
    one_hour: int = 0,
    overloaded: int = 0,
) -> dict[str, object]:
    return {
        "variant_id": f"{week_start.isoformat()}-{intake_code}-{grouping}",
        "snapshot_id": "snapshot-one",
        "week_start": week_start,
        "intake_code": intake_code,
        "grouping": grouping,
        "total_gap_minutes": gap,
        "late_only_days": late,
        "early_only_days": early,
        "one_hour_only_days": one_hour,
        "overloaded_days": overloaded,
    }


def weekly_frame(*records: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame.from_records(records)


class RankingProfileTests(unittest.TestCase):
    def test_normalizes_default_position_weights(self) -> None:
        weights = DEFAULT_PROFILE.normalized_weights

        self.assertAlmostEqual(weights["gap_burden"], 5 / 15)
        self.assertAlmostEqual(weights["late_only"], 4 / 15)
        self.assertAlmostEqual(weights["early_only"], 3 / 15)
        self.assertAlmostEqual(weights["one_hour_only"], 2 / 15)
        self.assertAlmostEqual(weights["overloaded"], 1 / 15)

    def test_rejects_incomplete_criterion_order(self) -> None:
        with self.assertRaisesRegex(RankingError, "exactly 5"):
            profile_with_order(DEFAULT_PROFILE, ["gap_burden", "late_only"])


class RankWeeklyMetricsTests(unittest.TestCase):
    def test_calculates_endpoint_scaled_percentiles_and_extremes(self) -> None:
        ranked = rank_weekly_metrics(
            weekly_frame(
                weekly_record("LOW", gap=0),
                weekly_record("MIDDLE", gap=50),
                weekly_record("HIGH", gap=100),
            ),
            DEFAULT_PROFILE,
        ).set_index("intake_code")

        self.assertEqual(ranked.loc["LOW", "gap_burden_percentile"], 0)
        self.assertEqual(ranked.loc["MIDDLE", "gap_burden_percentile"], 50)
        self.assertEqual(ranked.loc["HIGH", "gap_burden_percentile"], 100)
        self.assertAlmostEqual(ranked.loc["LOW", "overall_frustration"], 0)
        self.assertAlmostEqual(
            ranked.loc["MIDDLE", "overall_frustration"], 50 * (5 / 15)
        )
        self.assertTrue(ranked.loc["LOW", "is_best"])
        self.assertTrue(ranked.loc["HIGH", "is_worst"])
        self.assertTrue(ranked.loc["MIDDLE", "is_most_average"])

    def test_constant_criterion_is_neutral_and_preserves_ties(self) -> None:
        ranked = rank_weekly_metrics(
            weekly_frame(
                weekly_record("A", gap=10),
                weekly_record("B", gap=10),
            ),
            DEFAULT_PROFILE,
        )

        self.assertTrue((ranked["overall_frustration"] == 0).all())
        self.assertTrue(ranked["is_best"].all())
        self.assertTrue(ranked["is_worst"].all())
        self.assertTrue(ranked["is_most_average"].all())

    def test_tied_minimum_values_receive_zero_percentile(self) -> None:
        ranked = rank_weekly_metrics(
            weekly_frame(
                weekly_record("MIN-A", gap=0),
                weekly_record("MIN-B", gap=0),
                weekly_record("HIGH", gap=10),
            ),
            DEFAULT_PROFILE,
        ).set_index("intake_code")

        self.assertEqual(ranked.loc["MIN-A", "gap_burden_percentile"], 0)
        self.assertEqual(ranked.loc["MIN-B", "gap_burden_percentile"], 0)
        self.assertEqual(ranked.loc["HIGH", "gap_burden_percentile"], 100)

    def test_reordering_priorities_can_reverse_worst_result(self) -> None:
        weekly = weekly_frame(
            weekly_record("GAP-WORSE", gap=100, overloaded=0),
            weekly_record("LOAD-WORSE", gap=0, overloaded=1),
        )
        default_ranked = rank_weekly_metrics(
            weekly, DEFAULT_PROFILE
        ).set_index("intake_code")
        reversed_profile = profile_with_order(
            DEFAULT_PROFILE,
            [
                "overloaded",
                "one_hour_only",
                "early_only",
                "late_only",
                "gap_burden",
            ],
        )
        reversed_ranked = rank_weekly_metrics(
            weekly, reversed_profile
        ).set_index("intake_code")

        self.assertTrue(default_ranked.loc["GAP-WORSE", "is_worst"])
        self.assertTrue(reversed_ranked.loc["LOAD-WORSE", "is_worst"])

    def test_ranks_calendar_weeks_as_separate_comparison_sets(self) -> None:
        ranked = rank_weekly_metrics(
            weekly_frame(
                weekly_record("W1-BEST", week_start=date(2026, 8, 3), gap=0),
                weekly_record("W1-WORST", week_start=date(2026, 8, 3), gap=10),
                weekly_record("W2-BEST", week_start=date(2026, 8, 10), gap=100),
                weekly_record("W2-WORST", week_start=date(2026, 8, 10), gap=200),
            ),
            DEFAULT_PROFILE,
        ).set_index("intake_code")

        self.assertTrue(ranked.loc["W1-BEST", "is_best"])
        self.assertTrue(ranked.loc["W2-BEST", "is_best"])
        self.assertTrue(ranked.loc["W1-WORST", "is_worst"])
        self.assertTrue(ranked.loc["W2-WORST", "is_worst"])
        self.assertEqual(ranked.loc["W2-BEST", "gap_burden_percentile"], 0)

    def test_keeps_group_variants_as_separate_ranked_rows(self) -> None:
        ranked = rank_weekly_metrics(
            weekly_frame(
                weekly_record("INTAKE", grouping="G1", gap=0),
                weekly_record("INTAKE", grouping="G2", gap=100),
            ),
            DEFAULT_PROFILE,
        ).set_index("grouping")

        self.assertEqual(len(ranked), 2)
        self.assertTrue(ranked.loc["G1", "is_best"])
        self.assertTrue(ranked.loc["G2", "is_worst"])

    def test_rejects_negative_frustration_measure(self) -> None:
        with self.assertRaisesRegex(RankingError, "negative"):
            rank_weekly_metrics(
                weekly_frame(weekly_record("INVALID", gap=-1)),
                DEFAULT_PROFILE,
            )


class RankSnapshotTests(unittest.TestCase):
    def test_writes_readable_ranking_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            snapshot_directory = (
                repository_root / "data/processed/snapshot-one"
            )
            snapshot_directory.mkdir(parents=True)
            weekly_frame(
                weekly_record("BEST", gap=0),
                weekly_record("WORST", gap=100),
            ).to_parquet(
                snapshot_directory / "intake_week_metrics.parquet",
                index=False,
                engine="pyarrow",
            )

            summary = rank_snapshot(
                "snapshot-one", repository_root, DEFAULT_PROFILE
            )

            output_path = repository_root / summary["output_path"]
            round_trip = pd.read_parquet(output_path, engine="pyarrow")
            self.assertTrue(output_path.is_file())
            self.assertEqual(len(round_trip), 2)
            self.assertIn("overall_frustration", round_trip.columns)
            self.assertEqual(
                round_trip.loc[0, "percentile_method"], "strict_lower_peer_v1"
            )
            self.assertEqual(summary["comparison_set_count"], 1)


if __name__ == "__main__":
    unittest.main()
