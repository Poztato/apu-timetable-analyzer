from __future__ import annotations

import unittest
from copy import deepcopy

from scripts.scoring_model import (
    ScoringModelError,
    duration_weighted_deviation,
    parse_scoring_model,
    score_day,
    smooth_ramp,
)
from tests.scoring_fixtures import SCORING_CONFIG, SCORING_MODEL


class ScoringModelTests(unittest.TestCase):
    def test_smooth_ramps_are_continuous_and_capped(self) -> None:
        ramp = SCORING_MODEL.ramps["span"]
        self.assertEqual(smooth_ramp(0, ramp), 0)
        self.assertEqual(smooth_ramp(180, ramp), 0)
        self.assertAlmostEqual(smooth_ramp(360, ramp), 0.5)
        self.assertEqual(smooth_ramp(540, ramp), 1)
        self.assertEqual(smooth_ramp(900, ramp), 1)

        reverse = SCORING_MODEL.ramps["short_day"]
        self.assertEqual(smooth_ramp(30, reverse), 1)
        self.assertAlmostEqual(smooth_ramp(90, reverse), 0.5)
        self.assertEqual(smooth_ramp(120, reverse), 0)

    def test_duration_weighted_deviation_does_not_cancel_edges(self) -> None:
        preference = SCORING_MODEL.preferences_by_key["balanced"]
        deviation = duration_weighted_deviation(
            [(8 * 60 + 30, 9 * 60 + 30), (17 * 60, 18 * 60)],
            preference,
        )
        self.assertEqual(deviation, 180)

    def test_empty_online_and_physical_days_keep_their_order(self) -> None:
        empty = score_day(
            SCORING_MODEL,
            teaching_minutes=0,
            physical_teaching_minutes=0,
            span_minutes=0,
            physical_span_minutes=0,
            waiting_minutes=0,
            placement_deviation_minutes=0,
        )
        online = score_day(
            SCORING_MODEL,
            teaching_minutes=60,
            physical_teaching_minutes=0,
            span_minutes=60,
            physical_span_minutes=0,
            waiting_minutes=0,
            placement_deviation_minutes=0,
        )
        physical = score_day(
            SCORING_MODEL,
            teaching_minutes=180,
            physical_teaching_minutes=180,
            span_minutes=180,
            physical_span_minutes=180,
            waiting_minutes=0,
            placement_deviation_minutes=0,
        )

        self.assertEqual(empty.total, 0)
        self.assertEqual(online.total, 5)
        self.assertEqual(physical.total, 20)
        self.assertLess(online.total, physical.total)

    def test_optional_emphasis_strengthens_named_curve_without_changing_bounds(self) -> None:
        ordinary = score_day(
            SCORING_MODEL,
            teaching_minutes=60,
            physical_teaching_minutes=60,
            span_minutes=60,
            physical_span_minutes=60,
            waiting_minutes=0,
            placement_deviation_minutes=0,
        )
        emphasized = score_day(
            SCORING_MODEL,
            teaching_minutes=60,
            physical_teaching_minutes=60,
            span_minutes=60,
            physical_span_minutes=60,
            waiting_minutes=0,
            placement_deviation_minutes=0,
            emphasize_short_days=True,
        )

        self.assertGreater(
            emphasized.component_caps["short_day"],
            ordinary.component_caps["short_day"],
        )
        self.assertGreater(emphasized.total, ordinary.total)
        self.assertEqual(emphasized.component_caps["campus_trip"], 20)
        self.assertAlmostEqual(
            sum(
                emphasized.component_caps[key]
                for key in (
                    "campus_trip",
                    "placement",
                    "span",
                    "waiting",
                    "short_day",
                    "long_day",
                )
            ),
            100,
        )

    def test_config_rejects_online_day_that_can_outscore_a_campus_trip(self) -> None:
        invalid = deepcopy(SCORING_CONFIG)
        invalid["online_day"] = {
            "base_points": 5,
            "span_points": 8,
            "load_points": 7,
        }
        with self.assertRaisesRegex(ScoringModelError, "below the campus trip"):
            parse_scoring_model(invalid)


if __name__ == "__main__":
    unittest.main()
