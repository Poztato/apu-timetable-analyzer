"""Calculate daily timetable measures for each student schedule variant."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.scoring_model import (
    ScoringModel,
    ScoringModelError,
    duration_weighted_deviation,
    load_scoring_model,
    score_day,
)


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
SCORING_CONFIG_RELATIVE_PATH = Path("config/scoring.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
INPUT_FILENAME = "variant_events.parquet"
OUTPUT_FILENAME = "daily_metrics.parquet"

VARIANT_KEY = [
    "snapshot_id",
    "week_start",
    "intake_code",
    "grouping",
    "elective_profile",
]
DAILY_KEY = [*VARIANT_KEY, "event_date"]

REQUIRED_COLUMNS = {
    "variant_id",
    "variant_event_id",
    "slot_id",
    "snapshot_id",
    "week_start",
    "intake_code",
    "grouping",
    "elective_profile",
    "elective_profile_name",
    "elective_status",
    "elective_rule_id",
    "event_date",
    "start_at",
    "end_at",
    "delivery_mode",
}

METADATA_COLUMNS = [
    "programme_route",
    "programme_route_name",
    "programme_level",
    "programme_level_name",
    "academic_level",
    "intake_year",
    "intake_month",
    "course_code",
    "course_name",
    "specialism_code",
    "specialism_name",
    "school",
    "study_mode",
    "parse_status",
    "parser_family",
]

OUTPUT_COLUMNS = [
    "variant_id",
    "snapshot_id",
    "week_start",
    "intake_code",
    "grouping",
    "elective_profile",
    "elective_profile_name",
    "elective_status",
    "elective_rule_id",
    "event_date",
    "day_of_week",
    "is_weekend",
    "event_record_count",
    "event_count",
    "merged_block_count",
    "teaching_minutes",
    "physical_teaching_minutes",
    "first_class_start",
    "last_class_end",
    "span_minutes",
    "first_physical_start",
    "last_physical_end",
    "physical_span_minutes",
    "campus_waiting_minutes",
    "longest_campus_wait_minutes",
    "placement_deviation_minutes",
    "exact_overlap_pair_count",
    "overlap_pair_count",
    "physical_event_count",
    "campus_event_count",
    "online_event_count",
    "unknown_event_count",
    "day_type",
    "placement_penalty",
    "span_penalty",
    "waiting_penalty",
    "short_day_penalty",
    "long_day_penalty",
    "campus_trip_score",
    "online_commitment_score",
    "placement_score",
    "span_score",
    "waiting_score",
    "short_day_score",
    "long_day_score",
    "balanced_day_score",
    *METADATA_COLUMNS,
]


class DailyMetricError(RuntimeError):
    """Raised when daily timetable measures cannot be calculated safely."""


def _validate_variant_events(events: pd.DataFrame) -> None:
    if events.empty:
        raise DailyMetricError("The variant event table contains no rows.")

    missing = sorted(REQUIRED_COLUMNS.difference(events.columns))
    if missing:
        raise DailyMetricError(
            "The variant event table is missing required columns: "
            + ", ".join(missing)
            + "."
        )

    for column in REQUIRED_COLUMNS:
        if events[column].isna().any():
            raise DailyMetricError(
                f"The variant event table contains a blank {column}."
            )

    if events["variant_event_id"].duplicated().any():
        raise DailyMetricError("The variant event table has duplicate variant events.")
    if (events["end_at"] <= events["start_at"]).any():
        raise DailyMetricError("The variant event table has an invalid time interval.")

    variant_keys = events[["variant_id", *VARIANT_KEY]].drop_duplicates()
    if (variant_keys.groupby("variant_id").size() != 1).any():
        raise DailyMetricError("A variant ID maps to more than one timetable key.")
    if (variant_keys.groupby(VARIANT_KEY).size() != 1).any():
        raise DailyMetricError("A timetable key maps to more than one variant ID.")

    slot_consistency_columns = [
        "start_at",
        "end_at",
        "event_date",
        "delivery_mode",
    ]
    slot_consistency = events.groupby(
        ["variant_id", "slot_id"], sort=False, dropna=False
    )[slot_consistency_columns].nunique(dropna=False)
    if (slot_consistency > 1).any().any():
        raise DailyMetricError(
            "A slot ID has conflicting time, date, or delivery information."
        )


def _whole_minutes(start: pd.Timestamp, end: pd.Timestamp, label: str) -> int:
    seconds = (end - start).total_seconds()
    if seconds < 0 or seconds % 60 != 0:
        raise DailyMetricError(f"{label} is not a non-negative whole minute value.")
    return int(seconds // 60)


def _merge_intervals(
    intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]],
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    merged: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start_at, end_at in intervals:
        if not merged or start_at > merged[-1][1]:
            merged.append((start_at, end_at))
            continue
        previous_start, previous_end = merged[-1]
        if end_at > previous_end:
            merged[-1] = (previous_start, end_at)
    return merged


def _physical_bound_waits(
    intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]],
    physical_intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]],
) -> list[int]:
    merged_physical = _merge_intervals(physical_intervals)
    if len(merged_physical) < 2:
        return []

    physical_window_start = merged_physical[0][0]
    physical_window_end = merged_physical[-1][1]
    bounded_intervals = [
        (
            max(start_at, physical_window_start),
            min(end_at, physical_window_end),
        )
        for start_at, end_at in intervals
        if start_at < physical_window_end and end_at > physical_window_start
    ]
    occupied_blocks = _merge_intervals(bounded_intervals)
    return [
        _whole_minutes(previous_end, next_start, "Campus waiting duration")
        for (_, previous_end), (next_start, _) in zip(
            occupied_blocks, occupied_blocks[1:]
        )
    ]


def _overlap_pair_counts(
    intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[int, int]:
    exact_overlap_pairs = 0
    overlap_pairs = 0
    for left_index, (left_start, left_end) in enumerate(intervals):
        for right_start, right_end in intervals[left_index + 1 :]:
            if right_start >= left_end:
                break
            if right_end > left_start:
                overlap_pairs += 1
                if right_start == left_start and right_end == left_end:
                    exact_overlap_pairs += 1
    return exact_overlap_pairs, overlap_pairs


def _minute_of_day(timestamp: pd.Timestamp) -> int:
    if timestamp.second or timestamp.microsecond:
        raise DailyMetricError("Timetable times must use whole-minute precision.")
    return timestamp.hour * 60 + timestamp.minute


def _calculate_day(
    day_events: pd.DataFrame, scoring_model: ScoringModel
) -> dict[str, Any]:
    slots = day_events.drop_duplicates("slot_id").sort_values(
        ["start_at", "end_at", "slot_id"], kind="stable"
    )
    intervals = list(zip(slots["start_at"], slots["end_at"]))
    exact_overlap_pairs, overlap_pairs = _overlap_pair_counts(intervals)
    merged = _merge_intervals(intervals)
    physical_slots = slots.loc[slots["delivery_mode"] != "online"]
    physical_intervals = list(
        zip(physical_slots["start_at"], physical_slots["end_at"])
    )
    merged_physical_intervals = _merge_intervals(physical_intervals)

    teaching_minutes = sum(
        _whole_minutes(start_at, end_at, "Teaching duration")
        for start_at, end_at in merged
    )
    waits = _physical_bound_waits(intervals, physical_intervals)
    first_class_start = merged[0][0]
    last_class_end = merged[-1][1]
    event_count = len(slots)
    delivery_counts = slots["delivery_mode"].value_counts().to_dict()
    physical_teaching_minutes = sum(
        _whole_minutes(start_at, end_at, "Physical teaching duration")
        for start_at, end_at in merged_physical_intervals
    )
    first_physical_start = (
        merged_physical_intervals[0][0] if merged_physical_intervals else None
    )
    last_physical_end = (
        merged_physical_intervals[-1][1] if merged_physical_intervals else None
    )
    physical_span_minutes = (
        _whole_minutes(
            first_physical_start,
            last_physical_end,
            "Physical daily span",
        )
        if first_physical_start is not None and last_physical_end is not None
        else 0
    )
    placement_deviation_minutes = duration_weighted_deviation(
        [
            (_minute_of_day(start_at), _minute_of_day(end_at))
            for start_at, end_at in merged_physical_intervals
        ],
        scoring_model.preferences_by_key[
            scoring_model.default_time_preference
        ],
    )
    daily_score = score_day(
        scoring_model,
        teaching_minutes=teaching_minutes,
        physical_teaching_minutes=physical_teaching_minutes,
        span_minutes=_whole_minutes(
            first_class_start, last_class_end, "Daily span"
        ),
        physical_span_minutes=physical_span_minutes,
        waiting_minutes=sum(waits),
        placement_deviation_minutes=placement_deviation_minutes,
    )

    result = {
        "variant_id": day_events["variant_id"].iloc[0],
        "snapshot_id": day_events["snapshot_id"].iloc[0],
        "week_start": day_events["week_start"].iloc[0],
        "intake_code": day_events["intake_code"].iloc[0],
        "grouping": day_events["grouping"].iloc[0],
        "elective_profile": day_events["elective_profile"].iloc[0],
        "elective_profile_name": day_events["elective_profile_name"].iloc[0],
        "elective_status": day_events["elective_status"].iloc[0],
        "elective_rule_id": day_events["elective_rule_id"].iloc[0],
        "event_date": day_events["event_date"].iloc[0],
        "day_of_week": day_events["event_date"].iloc[0].strftime("%a").upper(),
        "is_weekend": day_events["event_date"].iloc[0].weekday() >= 5,
        "event_record_count": len(day_events),
        "event_count": event_count,
        "merged_block_count": len(merged),
        "teaching_minutes": teaching_minutes,
        "physical_teaching_minutes": physical_teaching_minutes,
        "first_class_start": first_class_start,
        "last_class_end": last_class_end,
        "span_minutes": _whole_minutes(
            first_class_start, last_class_end, "Daily span"
        ),
        "first_physical_start": first_physical_start,
        "last_physical_end": last_physical_end,
        "physical_span_minutes": physical_span_minutes,
        "campus_waiting_minutes": sum(waits),
        "longest_campus_wait_minutes": max(waits, default=0),
        "placement_deviation_minutes": round(placement_deviation_minutes, 6),
        "exact_overlap_pair_count": exact_overlap_pairs,
        "overlap_pair_count": overlap_pairs,
        "physical_event_count": len(physical_slots),
        "campus_event_count": int(delivery_counts.get("campus", 0)),
        "online_event_count": int(delivery_counts.get("online", 0)),
        "unknown_event_count": int(
            event_count
            - delivery_counts.get("campus", 0)
            - delivery_counts.get("online", 0)
        ),
        "day_type": daily_score.day_type,
        **{
            f"{key}_penalty": round(value, 6)
            for key, value in daily_score.penalties.items()
        },
        **{
            f"{key}_score": round(value, 6)
            for key, value in daily_score.component_points.items()
        },
        "balanced_day_score": round(daily_score.total, 6),
    }
    for column in METADATA_COLUMNS:
        result[column] = day_events[column].iloc[0] if column in day_events else None
    return result


def calculate_daily_metrics(
    variant_events: pd.DataFrame, scoring_model: ScoringModel
) -> pd.DataFrame:
    """Return one metric row for each active intake, week, group, and day."""

    _validate_variant_events(variant_events)
    records = [
        _calculate_day(day_events, scoring_model)
        for _, day_events in variant_events.groupby(
            [*DAILY_KEY, "variant_id"], sort=False, dropna=False
        )
    ]
    metrics = pd.DataFrame.from_records(records, columns=OUTPUT_COLUMNS)

    integer_columns = [
        "event_record_count",
        "event_count",
        "merged_block_count",
        "teaching_minutes",
        "physical_teaching_minutes",
        "span_minutes",
        "physical_span_minutes",
        "campus_waiting_minutes",
        "longest_campus_wait_minutes",
        "exact_overlap_pair_count",
        "overlap_pair_count",
        "physical_event_count",
        "campus_event_count",
        "online_event_count",
        "unknown_event_count",
    ]
    for column in integer_columns:
        metrics[column] = metrics[column].astype("int64")
    for column in ("academic_level", "intake_year", "intake_month"):
        metrics[column] = metrics[column].astype("Int64")
    string_columns = [
        "variant_id",
        "snapshot_id",
        "intake_code",
        "grouping",
        "elective_profile",
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        "day_of_week",
        "day_type",
        *[
            column
            for column in METADATA_COLUMNS
            if column not in {"academic_level", "intake_year", "intake_month"}
        ],
    ]
    for column in string_columns:
        metrics[column] = metrics[column].astype("string")

    metrics = metrics.sort_values(
        [
            "snapshot_id",
            "week_start",
            "intake_code",
            "grouping",
            "elective_profile",
            "event_date",
        ],
        kind="stable",
    ).reset_index(drop=True)
    if metrics.duplicated(DAILY_KEY).any():
        raise DailyMetricError("Daily metric keys are not unique.")
    return metrics


def _write_parquet_atomically(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.stem}.",
            suffix=".parquet",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        frame.to_parquet(
            temporary_path,
            index=False,
            engine="pyarrow",
            compression="zstd",
        )
        os.replace(temporary_path, target)
    except ImportError as exc:
        raise DailyMetricError(
            "Writing Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise DailyMetricError(f"Cannot write daily metrics: {target}.") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def calculate_snapshot_daily_metrics(
    snapshot_id: str,
    repository_root: Path,
    scoring_model: ScoringModel,
) -> dict[str, Any]:
    snapshot_directory = (
        repository_root / PROCESSED_DIRECTORY_RELATIVE_PATH / snapshot_id
    )
    input_path = snapshot_directory / INPUT_FILENAME
    output_path = snapshot_directory / OUTPUT_FILENAME
    try:
        variant_events = pd.read_parquet(input_path, engine="pyarrow")
    except FileNotFoundError as exc:
        raise DailyMetricError(
            f"Cannot find Stage 3 variants for {snapshot_id}: {input_path}."
        ) from exc
    except ImportError as exc:
        raise DailyMetricError(
            "Reading Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise DailyMetricError(f"Cannot read Stage 3 variants: {input_path}.") from exc

    snapshot_values = variant_events["snapshot_id"].drop_duplicates().tolist()
    if snapshot_values != [snapshot_id]:
        raise DailyMetricError(
            f"Stage 3 variants do not belong only to snapshot {snapshot_id}."
        )

    metrics = calculate_daily_metrics(variant_events, scoring_model)
    _write_parquet_atomically(metrics, output_path)

    return {
        "status": "processed",
        "snapshot_id": snapshot_id,
        "daily_record_count": len(metrics),
        "variant_count": int(metrics["variant_id"].nunique()),
        "intake_week_count": len(
            metrics[["snapshot_id", "week_start", "intake_code"]].drop_duplicates()
        ),
        "event_count": int(metrics["event_count"].sum()),
        "teaching_minutes": int(metrics["teaching_minutes"].sum()),
        "physical_teaching_minutes": int(
            metrics["physical_teaching_minutes"].sum()
        ),
        "campus_waiting_minutes": int(metrics["campus_waiting_minutes"].sum()),
        "days_with_campus_waiting": int(
            (metrics["campus_waiting_minutes"] > 0).sum()
        ),
        "physical_day_count": int((metrics["day_type"] == "physical").sum()),
        "online_only_day_count": int((metrics["day_type"] == "online").sum()),
        "days_with_exact_overlaps": int(
            (metrics["exact_overlap_pair_count"] > 0).sum()
        ),
        "days_with_overlaps": int((metrics["overlap_pair_count"] > 0).sum()),
        "weekend_day_count": int(metrics["is_weekend"].sum()),
        "maximum_campus_waiting_minutes": int(
            metrics["campus_waiting_minutes"].max()
        ),
        "maximum_single_campus_wait_minutes": int(
            metrics["longest_campus_wait_minutes"].max()
        ),
        "maximum_teaching_minutes": int(metrics["teaching_minutes"].max()),
        "maximum_physical_span_minutes": int(
            metrics["physical_span_minutes"].max()
        ),
        "maximum_balanced_day_score": float(
            metrics["balanced_day_score"].max()
        ),
        "scoring_profile_id": scoring_model.profile_id,
        "output_path": output_path.relative_to(repository_root).as_posix(),
        "output_size_bytes": output_path.stat().st_size,
    }


def load_snapshot_index(index_path: Path) -> list[dict[str, Any]]:
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DailyMetricError(f"Cannot find snapshot index: {index_path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyMetricError(f"Cannot read snapshot index: {index_path}.") from exc
    if not isinstance(index, list) or not index:
        raise DailyMetricError("The snapshot index must contain at least one entry.")
    for position, entry in enumerate(index, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("snapshot_id"), str):
            raise DailyMetricError(
                f"Snapshot index entry {position} has no valid snapshot_id."
            )
    return index


def _select_snapshot_ids(
    index: Sequence[Mapping[str, Any]], snapshot_id: str | None, process_all: bool
) -> list[str]:
    indexed_ids = [entry["snapshot_id"] for entry in index]
    if process_all:
        return indexed_ids
    if snapshot_id is None:
        return [indexed_ids[-1]]
    if snapshot_id not in indexed_ids:
        raise DailyMetricError(f"Snapshot ID is not in the index: {snapshot_id}.")
    return [snapshot_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate daily timetable measures from Stage 3 variants."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing config and processed snapshot data.",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--snapshot-id", help="Process one indexed snapshot ID.")
    selection.add_argument(
        "--all", action="store_true", help="Process every indexed snapshot."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository_root = arguments.repository_root.resolve()

    try:
        scoring_model = load_scoring_model(
            repository_root / SCORING_CONFIG_RELATIVE_PATH
        )
        index = load_snapshot_index(repository_root / INDEX_RELATIVE_PATH)
        snapshot_ids = _select_snapshot_ids(
            index, arguments.snapshot_id, arguments.all
        )
        summaries = [
            calculate_snapshot_daily_metrics(
                snapshot_id, repository_root, scoring_model
            )
            for snapshot_id in snapshot_ids
        ]
    except (DailyMetricError, ScoringModelError) as exc:
        print(f"Stage 4 processing failed: {exc}", file=sys.stderr)
        return 1

    if len(summaries) == 1:
        result: dict[str, Any] = summaries[0]
    else:
        result = {
            "status": "processed",
            "snapshot_count": len(summaries),
            "snapshots": summaries,
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
