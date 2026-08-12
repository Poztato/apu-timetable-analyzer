"""Aggregate daily timetable measures into weekly schedule variants."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
INPUT_FILENAME = "daily_metrics.parquet"
OUTPUT_FILENAME = "intake_week_metrics.parquet"

WEEKLY_KEY = [
    "snapshot_id",
    "week_start",
    "intake_code",
    "grouping",
    "elective_profile",
]
DAILY_KEY = [*WEEKLY_KEY, "event_date"]

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

REQUIRED_COLUMNS = {
    "variant_id",
    *DAILY_KEY,
    "elective_profile_name",
    "elective_status",
    "elective_rule_id",
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
}

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
    "active_days",
    "campus_days",
    "online_only_days",
    "weekend_days",
    "total_event_records",
    "total_events",
    "total_merged_blocks",
    "total_teaching_minutes",
    "total_gap_minutes",
    "longest_gap_minutes",
    "days_with_gaps",
    "days_with_exact_overlaps",
    "days_with_overlaps",
    "exact_overlap_pair_count",
    "overlap_pair_count",
    "total_campus_events",
    "total_online_events",
    "total_unknown_events",
    "early_only_days",
    "late_only_days",
    "one_hour_only_days",
    "overloaded_days",
    "earliest_start",
    "latest_end",
    "maximum_daily_span",
    "maximum_daily_teaching_minutes",
    *METADATA_COLUMNS,
]

NONNEGATIVE_COLUMNS = [
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

FLAG_COLUMNS = [
    "is_weekend",
    "early_only_flag",
    "late_only_flag",
    "one_hour_only_flag",
    "overloaded_flag",
]


class WeeklyMetricError(RuntimeError):
    """Raised when weekly timetable measures cannot be calculated safely."""


def _validate_daily_metrics(daily: pd.DataFrame) -> None:
    if daily.empty:
        raise WeeklyMetricError("The daily metric table contains no rows.")

    missing = sorted(REQUIRED_COLUMNS.difference(daily.columns))
    if missing:
        raise WeeklyMetricError(
            "The daily metric table is missing required columns: "
            + ", ".join(missing)
            + "."
        )

    for column in REQUIRED_COLUMNS:
        if daily[column].isna().any():
            raise WeeklyMetricError(
                f"The daily metric table contains a blank {column}."
            )

    if daily.duplicated(DAILY_KEY).any():
        raise WeeklyMetricError("The daily metric table has duplicate daily keys.")

    variant_keys = daily[["variant_id", *WEEKLY_KEY]].drop_duplicates()
    if (variant_keys.groupby("variant_id").size() != 1).any():
        raise WeeklyMetricError("A variant ID maps to more than one weekly key.")
    if (variant_keys.groupby(WEEKLY_KEY).size() != 1).any():
        raise WeeklyMetricError("A weekly key maps to more than one variant ID.")

    if (daily[NONNEGATIVE_COLUMNS] < 0).any().any():
        raise WeeklyMetricError("The daily metric table contains a negative measure.")
    if (daily["event_count"] < 1).any():
        raise WeeklyMetricError("Every daily metric row must contain an event.")
    if (
        (daily["merged_block_count"] < 1)
        | (daily["merged_block_count"] > daily["event_count"])
    ).any():
        raise WeeklyMetricError("A daily merged block count is invalid.")
    if (
        daily["event_count"]
        != daily["campus_event_count"]
        + daily["online_event_count"]
        + daily["unknown_event_count"]
    ).any():
        raise WeeklyMetricError("Daily delivery counts do not match event counts.")
    idle_span_minutes = daily["span_minutes"] - daily["teaching_minutes"]
    if (idle_span_minutes < 0).any():
        raise WeeklyMetricError("Daily teaching time exceeds the daily span.")
    if (daily["total_gap_minutes"] > idle_span_minutes).any():
        raise WeeklyMetricError("A campus-bound gap exceeds all daily idle time.")
    if (daily["longest_gap_minutes"] > daily["total_gap_minutes"]).any():
        raise WeeklyMetricError("A longest daily gap exceeds the total daily gap.")
    if (
        daily["exact_overlap_pair_count"] > daily["overlap_pair_count"]
    ).any():
        raise WeeklyMetricError("Exact overlap pairs exceed all overlap pairs.")
    if (daily["last_class_end"] <= daily["first_class_start"]).any():
        raise WeeklyMetricError("A daily first or last class timestamp is invalid.")

    for row in daily[["week_start", "event_date"]].itertuples(index=False):
        day_offset = (row.event_date - row.week_start).days
        if row.week_start.weekday() != 0 or not 0 <= day_offset <= 6:
            raise WeeklyMetricError(
                "An event date does not belong to its Monday-based week."
            )

    for row in daily[
        ["event_date", "first_class_start", "last_class_end"]
    ].itertuples(index=False):
        if (
            row.first_class_start.date() != row.event_date
            or row.last_class_end.date() != row.event_date
        ):
            raise WeeklyMetricError(
                "Weekly clock measures do not support a class crossing midnight."
            )
        if (
            row.first_class_start.second
            or row.first_class_start.microsecond
            or row.last_class_end.second
            or row.last_class_end.microsecond
        ):
            raise WeeklyMetricError(
                "Weekly clock measures require whole-minute timestamps."
            )

    for column in FLAG_COLUMNS:
        if not pd.api.types.is_bool_dtype(daily[column]):
            raise WeeklyMetricError(f"Daily flag {column} must be boolean.")

    available_metadata = [
        column for column in METADATA_COLUMNS if column in daily.columns
    ]
    if available_metadata:
        metadata_consistency = daily.groupby(
            "variant_id", sort=False, dropna=False
        )[available_metadata].nunique(dropna=False)
        if (metadata_consistency > 1).any().any():
            raise WeeklyMetricError(
                "Intake metadata is inconsistent within a weekly variant."
            )


def _clock_minutes(timestamp: pd.Timestamp) -> int:
    return timestamp.hour * 60 + timestamp.minute


def _format_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _aggregate_week(week: pd.DataFrame) -> dict[str, Any]:
    earliest_start_minutes = min(
        _clock_minutes(value) for value in week["first_class_start"]
    )
    latest_end_minutes = max(
        _clock_minutes(value) for value in week["last_class_end"]
    )

    result = {
        "variant_id": week["variant_id"].iloc[0],
        "snapshot_id": week["snapshot_id"].iloc[0],
        "week_start": week["week_start"].iloc[0],
        "intake_code": week["intake_code"].iloc[0],
        "grouping": week["grouping"].iloc[0],
        "elective_profile": week["elective_profile"].iloc[0],
        "elective_profile_name": week["elective_profile_name"].iloc[0],
        "elective_status": week["elective_status"].iloc[0],
        "elective_rule_id": week["elective_rule_id"].iloc[0],
        "active_days": len(week),
        "campus_days": int((week["campus_event_count"] > 0).sum()),
        "online_only_days": int(
            (
                (week["online_event_count"] > 0)
                & (week["campus_event_count"] == 0)
                & (week["unknown_event_count"] == 0)
            ).sum()
        ),
        "weekend_days": int(week["is_weekend"].sum()),
        "total_event_records": int(week["event_record_count"].sum()),
        "total_events": int(week["event_count"].sum()),
        "total_merged_blocks": int(week["merged_block_count"].sum()),
        "total_teaching_minutes": int(week["teaching_minutes"].sum()),
        "total_gap_minutes": int(week["total_gap_minutes"].sum()),
        "longest_gap_minutes": int(week["longest_gap_minutes"].max()),
        "days_with_gaps": int((week["total_gap_minutes"] > 0).sum()),
        "days_with_exact_overlaps": int(
            (week["exact_overlap_pair_count"] > 0).sum()
        ),
        "days_with_overlaps": int((week["overlap_pair_count"] > 0).sum()),
        "exact_overlap_pair_count": int(week["exact_overlap_pair_count"].sum()),
        "overlap_pair_count": int(week["overlap_pair_count"].sum()),
        "total_campus_events": int(week["campus_event_count"].sum()),
        "total_online_events": int(week["online_event_count"].sum()),
        "total_unknown_events": int(week["unknown_event_count"].sum()),
        "early_only_days": int(week["early_only_flag"].sum()),
        "late_only_days": int(week["late_only_flag"].sum()),
        "one_hour_only_days": int(week["one_hour_only_flag"].sum()),
        "overloaded_days": int(week["overloaded_flag"].sum()),
        "earliest_start": _format_clock(earliest_start_minutes),
        "latest_end": _format_clock(latest_end_minutes),
        "maximum_daily_span": int(week["span_minutes"].max()),
        "maximum_daily_teaching_minutes": int(week["teaching_minutes"].max()),
    }
    for column in METADATA_COLUMNS:
        result[column] = week[column].iloc[0] if column in week else None
    return result


def calculate_weekly_metrics(daily_metrics: pd.DataFrame) -> pd.DataFrame:
    """Return one factual metric row per snapshot, week, intake, and group."""

    _validate_daily_metrics(daily_metrics)
    records = [
        _aggregate_week(week)
        for _, week in daily_metrics.groupby(
            [*WEEKLY_KEY, "variant_id"], sort=False, dropna=False
        )
    ]
    weekly = pd.DataFrame.from_records(records, columns=OUTPUT_COLUMNS)

    integer_columns = [
        "active_days",
        "campus_days",
        "online_only_days",
        "weekend_days",
        "total_event_records",
        "total_events",
        "total_merged_blocks",
        "total_teaching_minutes",
        "total_gap_minutes",
        "longest_gap_minutes",
        "days_with_gaps",
        "days_with_exact_overlaps",
        "days_with_overlaps",
        "exact_overlap_pair_count",
        "overlap_pair_count",
        "total_campus_events",
        "total_online_events",
        "total_unknown_events",
        "early_only_days",
        "late_only_days",
        "one_hour_only_days",
        "overloaded_days",
        "maximum_daily_span",
        "maximum_daily_teaching_minutes",
    ]
    for column in integer_columns:
        weekly[column] = weekly[column].astype("int64")
    for column in ("academic_level", "intake_year", "intake_month"):
        weekly[column] = weekly[column].astype("Int64")
    string_columns = [
        "variant_id",
        "snapshot_id",
        "intake_code",
        "grouping",
        "elective_profile",
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        "earliest_start",
        "latest_end",
        *[
            column
            for column in METADATA_COLUMNS
            if column not in {"academic_level", "intake_year", "intake_month"}
        ],
    ]
    for column in string_columns:
        weekly[column] = weekly[column].astype("string")

    weekly = weekly.sort_values(
        [
            "snapshot_id",
            "week_start",
            "intake_code",
            "grouping",
            "elective_profile",
        ],
        kind="stable",
    ).reset_index(drop=True)
    if weekly.duplicated(WEEKLY_KEY).any():
        raise WeeklyMetricError("Weekly metric keys are not unique.")
    return weekly


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
        raise WeeklyMetricError(
            "Writing Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise WeeklyMetricError(f"Cannot write weekly metrics: {target}.") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def calculate_snapshot_weekly_metrics(
    snapshot_id: str, repository_root: Path
) -> dict[str, Any]:
    snapshot_directory = (
        repository_root / PROCESSED_DIRECTORY_RELATIVE_PATH / snapshot_id
    )
    input_path = snapshot_directory / INPUT_FILENAME
    output_path = snapshot_directory / OUTPUT_FILENAME
    try:
        daily_metrics = pd.read_parquet(input_path, engine="pyarrow")
    except FileNotFoundError as exc:
        raise WeeklyMetricError(
            f"Cannot find Stage 4 daily metrics for {snapshot_id}: {input_path}."
        ) from exc
    except ImportError as exc:
        raise WeeklyMetricError(
            "Reading Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise WeeklyMetricError(f"Cannot read Stage 4 metrics: {input_path}.") from exc

    snapshot_values = daily_metrics["snapshot_id"].drop_duplicates().tolist()
    if snapshot_values != [snapshot_id]:
        raise WeeklyMetricError(
            f"Stage 4 metrics do not belong only to snapshot {snapshot_id}."
        )

    weekly = calculate_weekly_metrics(daily_metrics)
    _write_parquet_atomically(weekly, output_path)

    weekly_keys = weekly[[*WEEKLY_KEY, "variant_id"]]
    intake_week_group_counts = weekly_keys.groupby(
        ["snapshot_id", "week_start", "intake_code"], sort=False, dropna=False
    )["grouping"].nunique()
    return {
        "status": "processed",
        "snapshot_id": snapshot_id,
        "weekly_record_count": len(weekly),
        "intake_week_count": len(intake_week_group_counts),
        "multi_group_intake_week_count": int(
            (intake_week_group_counts > 1).sum()
        ),
        "distinct_intake_count": int(weekly["intake_code"].nunique()),
        "active_day_count": int(weekly["active_days"].sum()),
        "total_event_count": int(weekly["total_events"].sum()),
        "total_teaching_minutes": int(weekly["total_teaching_minutes"].sum()),
        "total_gap_minutes": int(weekly["total_gap_minutes"].sum()),
        "weeks_with_gaps": int((weekly["days_with_gaps"] > 0).sum()),
        "weeks_with_early_only_days": int((weekly["early_only_days"] > 0).sum()),
        "weeks_with_late_only_days": int((weekly["late_only_days"] > 0).sum()),
        "weeks_with_one_hour_only_days": int(
            (weekly["one_hour_only_days"] > 0).sum()
        ),
        "weeks_with_overloaded_days": int(
            (weekly["overloaded_days"] > 0).sum()
        ),
        "maximum_weekly_gap_minutes": int(weekly["total_gap_minutes"].max()),
        "maximum_single_gap_minutes": int(weekly["longest_gap_minutes"].max()),
        "maximum_active_days": int(weekly["active_days"].max()),
        "earliest_observed_start": str(weekly["earliest_start"].min()),
        "latest_observed_end": str(weekly["latest_end"].max()),
        "output_path": output_path.relative_to(repository_root).as_posix(),
        "output_size_bytes": output_path.stat().st_size,
    }


def load_snapshot_index(index_path: Path) -> list[dict[str, Any]]:
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WeeklyMetricError(f"Cannot find snapshot index: {index_path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WeeklyMetricError(f"Cannot read snapshot index: {index_path}.") from exc
    if not isinstance(index, list) or not index:
        raise WeeklyMetricError("The snapshot index must contain at least one entry.")
    for position, entry in enumerate(index, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("snapshot_id"), str):
            raise WeeklyMetricError(
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
        raise WeeklyMetricError(f"Snapshot ID is not in the index: {snapshot_id}.")
    return [snapshot_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate Stage 4 daily measures into weekly intake metrics."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing processed snapshot data.",
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
        index = load_snapshot_index(repository_root / INDEX_RELATIVE_PATH)
        snapshot_ids = _select_snapshot_ids(
            index, arguments.snapshot_id, arguments.all
        )
        summaries = [
            calculate_snapshot_weekly_metrics(snapshot_id, repository_root)
            for snapshot_id in snapshot_ids
        ]
    except WeeklyMetricError as exc:
        print(f"Stage 5 processing failed: {exc}", file=sys.stderr)
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
