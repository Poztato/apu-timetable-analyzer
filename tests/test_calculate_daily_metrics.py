from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime

import pandas as pd

from scripts.build_timetable_variants import construct_timetable_variants
from scripts.calculate_daily_metrics import DailyMetricError, calculate_daily_metrics
from scripts.process_snapshots import normalize_events
from tests.scoring_fixtures import SCORING_MODEL
from tests.test_fetch_timetable import BASE_EVENT


INTAKE_CONFIG = {
    "programme_routes": {"APD": "Dual-degree programme"},
    "courses": {"CS": {"name": "Computer Science", "school": None}},
    "specialisms": {"DA": "Data Analytics"},
}


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
        make_variant_events(*source_events), SCORING_MODEL
    )
    if len(metrics) != 1:
        raise AssertionError(f"Expected one daily row, found {len(metrics)}.")
    return metrics.iloc[0]


class DailyMeasureTests(unittest.TestCase):
    def test_edge_classes_create_span_waiting_and_placement_costs(self) -> None:
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

        self.assertEqual(row["day_type"], "physical")
        self.assertEqual(row["teaching_minutes"], 120)
        self.assertEqual(row["physical_span_minutes"], 570)
        self.assertEqual(row["campus_waiting_minutes"], 450)
        self.assertEqual(row["longest_campus_wait_minutes"], 450)
        self.assertEqual(row["placement_deviation_minutes"], 180)
        self.assertEqual(row["span_penalty"], 1)
        self.assertEqual(row["waiting_penalty"], 1)

    def test_middle_clump_scores_better_than_equal_teaching_on_edges(self) -> None:
        middle = metric_row(
            source_event(
                "MIDDLE",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T14:00:00+08:00",
            )
        )
        edge = metric_row(
            source_event(
                "EDGE",
                "2026-08-03T08:30:00+08:00",
                "2026-08-03T11:30:00+08:00",
            )
        )

        self.assertEqual(middle["physical_teaching_minutes"], 180)
        self.assertEqual(edge["physical_teaching_minutes"], 180)
        self.assertLess(middle["balanced_day_score"], edge["balanced_day_score"])

    def test_one_hour_trip_uses_a_smooth_short_day_cost(self) -> None:
        row = metric_row(
            source_event(
                "SHORT",
                "2026-08-03T12:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
            )
        )

        self.assertEqual(row["campus_waiting_minutes"], 0)
        self.assertEqual(row["short_day_penalty"], 1)
        self.assertEqual(row["short_day_score"], 10)
        self.assertEqual(row["balanced_day_score"], 30)

    def test_online_only_day_stays_below_a_physical_trip(self) -> None:
        row = metric_row(
            source_event(
                "ONLINE",
                "2026-08-03T12:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
                delivery_mode="online",
            )
        )

        self.assertEqual(row["day_type"], "online")
        self.assertEqual(row["physical_teaching_minutes"], 0)
        self.assertEqual(row["online_commitment_score"], 5)
        self.assertEqual(row["balanced_day_score"], 5)

    def test_online_class_after_campus_window_does_not_create_waiting(self) -> None:
        row = metric_row(
            source_event(
                "CAMPUS",
                "2026-08-03T11:00:00+08:00",
                "2026-08-03T12:00:00+08:00",
            ),
            source_event(
                "ONLINE",
                "2026-08-03T18:00:00+08:00",
                "2026-08-03T19:00:00+08:00",
                delivery_mode="online",
            ),
        )

        self.assertEqual(row["physical_span_minutes"], 60)
        self.assertEqual(row["campus_waiting_minutes"], 0)

    def test_online_class_inside_physical_window_counts_as_occupied(self) -> None:
        row = metric_row(
            source_event(
                "FIRST",
                "2026-08-03T09:00:00+08:00",
                "2026-08-03T10:00:00+08:00",
            ),
            source_event(
                "ONLINE",
                "2026-08-03T12:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
                delivery_mode="online",
            ),
            source_event(
                "LAST",
                "2026-08-03T15:00:00+08:00",
                "2026-08-03T16:00:00+08:00",
            ),
        )

        self.assertEqual(row["physical_span_minutes"], 420)
        self.assertEqual(row["campus_waiting_minutes"], 240)
        self.assertEqual(row["longest_campus_wait_minutes"], 120)

    def test_merges_overlap_and_reports_diagnostics(self) -> None:
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

    def test_unknown_delivery_is_treated_as_physical_conservatively(self) -> None:
        events = make_variant_events(
            source_event(
                "UNKNOWN",
                "2026-08-03T12:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
            )
        )
        events.loc[:, "delivery_mode"] = "unknown"
        row = calculate_daily_metrics(events, SCORING_MODEL).iloc[0]

        self.assertEqual(row["day_type"], "physical")
        self.assertEqual(row["physical_event_count"], 1)
        self.assertEqual(row["unknown_event_count"], 1)

    def test_rejects_duplicate_variant_event(self) -> None:
        events = make_variant_events(
            source_event(
                "DUPLICATE",
                "2026-08-03T12:00:00+08:00",
                "2026-08-03T13:00:00+08:00",
            )
        )
        duplicated = pd.concat([events, events], ignore_index=True)
        with self.assertRaisesRegex(DailyMetricError, "duplicate variant events"):
            calculate_daily_metrics(duplicated, SCORING_MODEL)


if __name__ == "__main__":
    unittest.main()
