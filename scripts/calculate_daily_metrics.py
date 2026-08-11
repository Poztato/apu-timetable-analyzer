"""Calculate daily timetable measures for each student schedule variant."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


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
    "first_class_start",
    "last_class_end",
    "span_minutes",
    "total_gap_minutes",
    "longest_gap_minutes",
    "exact_overlap_pair_count",
    "overlap_pair_count",
    "campus_event_count",
    "online_event_count",
    "unknown_event_count",
    "early_only_flag",
    "late_only_flag",
    "one_hour_only_flag",
    "overloaded_flag",
    *METADATA_COLUMNS,
]


class DailyMetricError(RuntimeError):
    """Raised when daily timetable measures cannot be calculated safely."""


@dataclass(frozen=True)
class DailyThresholds:
    early_start: time
    late_start: time
    one_hour_max_teaching_minutes: int
    overload_teaching_minutes: int
    overload_event_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "early_start": self.early_start.strftime("%H:%M"),
            "late_start": self.late_start.strftime("%H:%M"),
            "one_hour_max_teaching_minutes": self.one_hour_max_teaching_minutes,
            "overload_teaching_minutes": self.overload_teaching_minutes,
            "overload_event_count": self.overload_event_count,
        }


def _parse_clock(value: Any, field: str) -> time:
    if not isinstance(value, str):
        raise DailyMetricError(f"Scoring field {field} must be a time string.")
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise DailyMetricError(
            f"Scoring field {field} is not a valid time: {value!r}."
        ) from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise DailyMetricError(
            f"Scoring field {field} must use local HH:MM precision."
        )
    return parsed


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DailyMetricError(f"Scoring field {field} must be a positive integer.")
    return value


def parse_scoring_config(config: Mapping[str, Any]) -> DailyThresholds:
    required = {
        "early_start",
        "late_start",
        "one_hour_max_teaching_minutes",
        "overload_teaching_minutes",
        "overload_event_count",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise DailyMetricError(
            "Scoring config is missing fields: " + ", ".join(missing) + "."
        )

    thresholds = DailyThresholds(
        early_start=_parse_clock(config["early_start"], "early_start"),
        late_start=_parse_clock(config["late_start"], "late_start"),
        one_hour_max_teaching_minutes=_positive_integer(
            config["one_hour_max_teaching_minutes"],
            "one_hour_max_teaching_minutes",
        ),
        overload_teaching_minutes=_positive_integer(
            config["overload_teaching_minutes"], "overload_teaching_minutes"
        ),
        overload_event_count=_positive_integer(
            config["overload_event_count"], "overload_event_count"
        ),
    )
    if thresholds.early_start >= thresholds.late_start:
        raise DailyMetricError("early_start must be earlier than late_start.")
    return thresholds


def load_scoring_config(path: Path) -> DailyThresholds:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DailyMetricError(f"Cannot find scoring config: {path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyMetricError(f"Cannot read scoring config: {path}.") from exc
    if not isinstance(config, dict):
        raise DailyMetricError("The scoring config must contain a JSON object.")
    return parse_scoring_config(config)


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


def _local_clock(timestamp: pd.Timestamp) -> time:
    return timestamp.timetz().replace(tzinfo=None)


def _calculate_day(
    day_events: pd.DataFrame, thresholds: DailyThresholds
) -> dict[str, Any]:
    slots = day_events.drop_duplicates("slot_id").sort_values(
        ["start_at", "end_at", "slot_id"], kind="stable"
    )
    intervals = list(zip(slots["start_at"], slots["end_at"]))
    exact_overlap_pairs, overlap_pairs = _overlap_pair_counts(intervals)
    merged = _merge_intervals(intervals)

    teaching_minutes = sum(
        _whole_minutes(start_at, end_at, "Teaching duration")
        for start_at, end_at in merged
    )
    gaps = [
        _whole_minutes(previous_end, next_start, "Gap duration")
        for (_, previous_end), (next_start, _) in zip(merged, merged[1:])
    ]
    first_class_start = merged[0][0]
    last_class_end = merged[-1][1]
    event_count = len(slots)
    delivery_counts = slots["delivery_mode"].value_counts().to_dict()
    campus_slots = slots.loc[slots["delivery_mode"] == "campus"]
    campus_intervals = list(zip(campus_slots["start_at"], campus_slots["end_at"]))
    merged_campus_intervals = _merge_intervals(campus_intervals)
    campus_teaching_minutes = sum(
        _whole_minutes(start_at, end_at, "Campus teaching duration")
        for start_at, end_at in merged_campus_intervals
    )
    campus_event_count = len(campus_slots)
    first_campus_start = (
        merged_campus_intervals[0][0] if merged_campus_intervals else None
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
        "first_class_start": first_class_start,
        "last_class_end": last_class_end,
        "span_minutes": _whole_minutes(
            first_class_start, last_class_end, "Daily span"
        ),
        "total_gap_minutes": sum(gaps),
        "longest_gap_minutes": max(gaps, default=0),
        "exact_overlap_pair_count": exact_overlap_pairs,
        "overlap_pair_count": overlap_pairs,
        "campus_event_count": campus_event_count,
        "online_event_count": int(delivery_counts.get("online", 0)),
        "unknown_event_count": int(
            event_count
            - delivery_counts.get("campus", 0)
            - delivery_counts.get("online", 0)
        ),
        "early_only_flag": (
            campus_event_count == 1
            and _local_clock(first_campus_start) <= thresholds.early_start
        ),
        "late_only_flag": (
            campus_event_count == 1
            and _local_clock(first_campus_start) >= thresholds.late_start
        ),
        "one_hour_only_flag": (
            campus_event_count > 0
            and campus_teaching_minutes
            <= thresholds.one_hour_max_teaching_minutes
        ),
        "overloaded_flag": (
            teaching_minutes >= thresholds.overload_teaching_minutes
            or event_count >= thresholds.overload_event_count
        ),
    }
    for column in METADATA_COLUMNS:
        result[column] = day_events[column].iloc[0] if column in day_events else None
    return result


def calculate_daily_metrics(
    variant_events: pd.DataFrame, thresholds: DailyThresholds
) -> pd.DataFrame:
    """Return one metric row for each active intake, week, group, and day."""

    _validate_variant_events(variant_events)
    records = [
        _calculate_day(day_events, thresholds)
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
        "span_minutes",
        "total_gap_minutes",
        "longest_gap_minutes",
        "exact_overlap_pair_count",
        "overlap_pair_count",
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
    thresholds: DailyThresholds,
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

    metrics = calculate_daily_metrics(variant_events, thresholds)
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
        "total_gap_minutes": int(metrics["total_gap_minutes"].sum()),
        "days_with_gaps": int((metrics["total_gap_minutes"] > 0).sum()),
        "days_with_exact_overlaps": int(
            (metrics["exact_overlap_pair_count"] > 0).sum()
        ),
        "days_with_overlaps": int((metrics["overlap_pair_count"] > 0).sum()),
        "early_only_day_count": int(metrics["early_only_flag"].sum()),
        "late_only_day_count": int(metrics["late_only_flag"].sum()),
        "one_hour_only_day_count": int(metrics["one_hour_only_flag"].sum()),
        "overloaded_day_count": int(metrics["overloaded_flag"].sum()),
        "weekend_day_count": int(metrics["is_weekend"].sum()),
        "maximum_total_gap_minutes": int(metrics["total_gap_minutes"].max()),
        "maximum_longest_gap_minutes": int(metrics["longest_gap_minutes"].max()),
        "maximum_teaching_minutes": int(metrics["teaching_minutes"].max()),
        "thresholds": thresholds.as_dict(),
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
        thresholds = load_scoring_config(
            repository_root / SCORING_CONFIG_RELATIVE_PATH
        )
        index = load_snapshot_index(repository_root / INDEX_RELATIVE_PATH)
        snapshot_ids = _select_snapshot_ids(
            index, arguments.snapshot_id, arguments.all
        )
        summaries = [
            calculate_snapshot_daily_metrics(
                snapshot_id, repository_root, thresholds
            )
            for snapshot_id in snapshot_ids
        ]
    except DailyMetricError as exc:
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
