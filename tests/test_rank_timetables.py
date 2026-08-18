from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.calculate_daily_metrics import calculate_daily_metrics
from scripts.calculate_weekly_metrics import calculate_weekly_metrics
from scripts.rank_timetables import (
    RankingError,
    rank_snapshot,
    rank_weekly_metrics,
)
from scripts.scoring_model import SCORE_METHOD
from tests.scoring_fixtures import SCORING_MODEL
from tests.test_calculate_daily_metrics import make_variant_events, source_event


def comparison_week() -> pd.DataFrame:
    events = make_variant_events(
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
    daily = calculate_daily_metrics(events, SCORING_MODEL)
    return calculate_weekly_metrics(daily, SCORING_MODEL)


class RankWeeklyMetricsTests(unittest.TestCase):
    def test_ranks_by_absolute_weekly_score(self) -> None:
        weekly = comparison_week()
        ranked = rank_weekly_metrics(weekly, SCORING_MODEL).set_index("grouping")

        self.assertLess(
            ranked.loc["G1", "overall_score"],
            ranked.loc["G2", "overall_score"],
        )
        self.assertEqual(ranked.loc["G1", "best_rank"], 1)
        self.assertEqual(ranked.loc["G2", "best_rank"], 2)
        self.assertTrue(ranked.loc["G1", "is_best"])
        self.assertTrue(ranked.loc["G2", "is_worst"])
        self.assertEqual(set(ranked["score_method"]), {SCORE_METHOD})
        self.assertEqual(
            set(ranked["scoring_profile_id"]), {SCORING_MODEL.profile_id}
        )

    def test_score_does_not_change_when_comparison_pool_changes(self) -> None:
        weekly = comparison_week()
        full = rank_weekly_metrics(weekly, SCORING_MODEL).set_index("grouping")
        subset = rank_weekly_metrics(
            weekly.loc[weekly["grouping"] == "G2"], SCORING_MODEL
        ).iloc[0]

        self.assertEqual(subset["overall_score"], full.loc["G2", "overall_score"])
        self.assertEqual(subset["best_rank"], 1)
        self.assertEqual(subset["comparison_set_size"], 1)

    def test_identical_scores_form_a_tie_range(self) -> None:
        weekly = comparison_week().loc[lambda frame: frame["grouping"] == "G1"]
        twin = weekly.copy()
        twin.loc[:, "variant_id"] = "twin-variant"
        twin.loc[:, "intake_code"] = "TWIN"
        twin.loc[:, "grouping"] = "G9"
        combined = pd.concat([weekly, twin], ignore_index=True)
        ranked = rank_weekly_metrics(combined, SCORING_MODEL)

        self.assertTrue(ranked["is_best"].all())
        self.assertTrue(ranked["is_worst"].all())
        self.assertTrue((ranked["best_rank"] == 1).all())
        self.assertTrue((ranked["worst_rank"] == 1).all())

    def test_rejects_negative_score(self) -> None:
        weekly = comparison_week()
        weekly.loc[0, "balanced_score"] = -1
        with self.assertRaisesRegex(RankingError, "negative"):
            rank_weekly_metrics(weekly, SCORING_MODEL)

    def test_rejects_component_total_mismatch(self) -> None:
        weekly = comparison_week()
        weekly.loc[0, "placement_score"] += 1
        with self.assertRaisesRegex(RankingError, "component scores"):
            rank_weekly_metrics(weekly, SCORING_MODEL)


class RankSnapshotTests(unittest.TestCase):
    def test_writes_readable_default_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_id = "snapshot-one"
            processed = root / "data/processed" / snapshot_id
            processed.mkdir(parents=True)
            comparison_week().to_parquet(
                processed / "intake_week_metrics.parquet", index=False
            )

            summary = rank_snapshot(snapshot_id, root, SCORING_MODEL)
            written = pd.read_parquet(processed / "default_rankings.parquet")

        self.assertEqual(summary["ranked_record_count"], 2)
        self.assertEqual(summary["score_method"], SCORE_METHOD)
        self.assertIn("overall_score", written.columns)


if __name__ == "__main__":
    unittest.main()
