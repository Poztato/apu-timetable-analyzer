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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.scoring_model import ScoringModel, ScoringModelError, load_scoring_model


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
SCORING_CONFIG_RELATIVE_PATH = Path("config/scoring.json")
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
}

NULLABLE_COLUMNS = {"first_physical_start", "last_physical_end"}

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
    "empty_days",
    "physical_days",
    "online_only_days",
    "weekend_days",
    "total_event_records",
    "total_events",
    "total_merged_blocks",
    "total_teaching_minutes",
    "total_physical_teaching_minutes",
    "total_span_minutes",
    "total_physical_span_minutes",
    "total_campus_waiting_minutes",
    "longest_campus_wait_minutes",
    "days_with_campus_waiting",
    "average_placement_deviation_minutes",
    "days_with_exact_overlaps",
    "days_with_overlaps",
    "exact_overlap_pair_count",
    "overlap_pair_count",
    "total_physical_events",
    "total_campus_events",
    "total_online_events",
    "total_unknown_events",
    "earliest_start",
    "latest_end",
    "maximum_daily_span",
    "maximum_physical_span",
    "maximum_daily_teaching_minutes",
    "maximum_physical_teaching_minutes",
    "campus_trip_score",
    "online_commitment_score",
    "placement_score",
    "span_score",
    "waiting_score",
    "short_day_score",
    "long_day_score",
    "balanced_score",
    *METADATA_COLUMNS,
]

NONNEGATIVE_COLUMNS = [
    "event_record_count",
    "event_count",
    "merged_block_count",
    "teaching_minutes",
    "physical_teaching_minutes",
    "span_minutes",
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
]

PENALTY_COLUMNS = [
    "placement_penalty",
    "span_penalty",
    "waiting_penalty",
    "short_day_penalty",
    "long_day_penalty",
]

DAILY_COMPONENT_COLUMNS = [
    "campus_trip_score",
    "online_commitment_score",
    "placement_score",
    "span_score",
    "waiting_score",
    "short_day_score",
    "long_day_score",
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

    for column in REQUIRED_COLUMNS.difference(NULLABLE_COLUMNS):
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
        != daily["physical_event_count"] + daily["online_event_count"]
    ).any():
        raise WeeklyMetricError("Daily delivery counts do not match event counts.")
    if (
        daily["physical_event_count"]
        != daily["campus_event_count"] + daily["unknown_event_count"]
    ).any():
        raise WeeklyMetricError("Daily physical counts do not match delivery counts.")
    idle_span_minutes = daily["span_minutes"] - daily["teaching_minutes"]
    if (idle_span_minutes < 0).any():
        raise WeeklyMetricError("Daily teaching time exceeds the daily span.")
    if (daily["physical_teaching_minutes"] > daily["teaching_minutes"]).any():
        raise WeeklyMetricError("Physical teaching exceeds all daily teaching.")
    physical_idle_minutes = (
        daily["physical_span_minutes"] - daily["physical_teaching_minutes"]
    )
    if (physical_idle_minutes < 0).any():
        raise WeeklyMetricError("Physical teaching exceeds the physical span.")
    if (daily["campus_waiting_minutes"] > physical_idle_minutes).any():
        raise WeeklyMetricError("Campus waiting exceeds physical idle time.")
    if (
        daily["longest_campus_wait_minutes"]
        > daily["campus_waiting_minutes"]
    ).any():
        raise WeeklyMetricError(
            "A longest campus wait exceeds total daily campus waiting."
        )
    if (
        daily["exact_overlap_pair_count"] > daily["overlap_pair_count"]
    ).any():
        raise WeeklyMetricError("Exact overlap pairs exceed all overlap pairs.")
    if (daily["last_class_end"] <= daily["first_class_start"]).any():
        raise WeeklyMetricError("A daily first or last class timestamp is invalid.")
    if not set(daily["day_type"].astype(str)).issubset({"physical", "online"}):
        raise WeeklyMetricError("A daily metric row has an invalid day type.")
    expected_physical = daily["physical_teaching_minutes"] > 0
    if (expected_physical != (daily["day_type"] == "physical")).any():
        raise WeeklyMetricError("Daily type does not match physical teaching.")
    physical_time_missing = daily["first_physical_start"].isna() | daily[
        "last_physical_end"
    ].isna()
    if (physical_time_missing == expected_physical).any():
        raise WeeklyMetricError("Physical day timestamps are incomplete.")
    if ((daily[PENALTY_COLUMNS] < 0) | (daily[PENALTY_COLUMNS] > 1)).any().any():
        raise WeeklyMetricError("A smooth daily penalty falls outside 0 to 1.")
    component_total = daily[DAILY_COMPONENT_COLUMNS].sum(axis=1)
    if not (component_total - daily["balanced_day_score"]).abs().le(0.00001).all():
        raise WeeklyMetricError("Daily component scores do not match the day score.")
    if (daily["balanced_day_score"] > 100.000001).any():
        raise WeeklyMetricError("A balanced daily score exceeds 100.")

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

    if not pd.api.types.is_bool_dtype(daily["is_weekend"]):
        raise WeeklyMetricError("Daily flag is_weekend must be boolean.")

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


def _aggregate_week(
    week: pd.DataFrame, scoring_model: ScoringModel
) -> dict[str, Any]:
    earliest_start_minutes = min(
        _clock_minutes(value) for value in week["first_class_start"]
    )
    latest_end_minutes = max(
        _clock_minutes(value) for value in week["last_class_end"]
    )

    physical_teaching_total = int(week["physical_teaching_minutes"].sum())
    placement_deviation = (
        float(
            (
                week["placement_deviation_minutes"]
                * week["physical_teaching_minutes"]
            ).sum()
            / physical_teaching_total
        )
        if physical_teaching_total
        else 0.0
    )
    component_scores = {
        column: round(
            float(week[column].sum()) / scoring_model.weekly_divisor_days,
            6,
        )
        for column in DAILY_COMPONENT_COLUMNS
    }
    active_days = len(week)
    if active_days > scoring_model.weekly_divisor_days:
        raise WeeklyMetricError("A weekly variant contains more than seven days.")

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
        "active_days": active_days,
        "empty_days": scoring_model.weekly_divisor_days - active_days,
        "physical_days": int((week["day_type"] == "physical").sum()),
        "online_only_days": int((week["day_type"] == "online").sum()),
        "weekend_days": int(week["is_weekend"].sum()),
        "total_event_records": int(week["event_record_count"].sum()),
        "total_events": int(week["event_count"].sum()),
        "total_merged_blocks": int(week["merged_block_count"].sum()),
        "total_teaching_minutes": int(week["teaching_minutes"].sum()),
        "total_physical_teaching_minutes": physical_teaching_total,
        "total_span_minutes": int(week["span_minutes"].sum()),
        "total_physical_span_minutes": int(
            week["physical_span_minutes"].sum()
        ),
        "total_campus_waiting_minutes": int(
            week["campus_waiting_minutes"].sum()
        ),
        "longest_campus_wait_minutes": int(
            week["longest_campus_wait_minutes"].max()
        ),
        "days_with_campus_waiting": int(
            (week["campus_waiting_minutes"] > 0).sum()
        ),
        "average_placement_deviation_minutes": round(placement_deviation, 6),
        "days_with_exact_overlaps": int(
            (week["exact_overlap_pair_count"] > 0).sum()
        ),
        "days_with_overlaps": int((week["overlap_pair_count"] > 0).sum()),
        "exact_overlap_pair_count": int(week["exact_overlap_pair_count"].sum()),
        "overlap_pair_count": int(week["overlap_pair_count"].sum()),
        "total_physical_events": int(week["physical_event_count"].sum()),
        "total_campus_events": int(week["campus_event_count"].sum()),
        "total_online_events": int(week["online_event_count"].sum()),
        "total_unknown_events": int(week["unknown_event_count"].sum()),
        "earliest_start": _format_clock(earliest_start_minutes),
        "latest_end": _format_clock(latest_end_minutes),
        "maximum_daily_span": int(week["span_minutes"].max()),
        "maximum_physical_span": int(week["physical_span_minutes"].max()),
        "maximum_daily_teaching_minutes": int(week["teaching_minutes"].max()),
        "maximum_physical_teaching_minutes": int(
            week["physical_teaching_minutes"].max()
        ),
        **component_scores,
        "balanced_score": round(
            float(week["balanced_day_score"].sum())
            / scoring_model.weekly_divisor_days,
            6,
        ),
    }
    for column in METADATA_COLUMNS:
        result[column] = week[column].iloc[0] if column in week else None
    return result


def calculate_weekly_metrics(
    daily_metrics: pd.DataFrame, scoring_model: ScoringModel
) -> pd.DataFrame:
    """Return one factual metric row per snapshot, week, intake, and group."""

    _validate_daily_metrics(daily_metrics)
    records = [
        _aggregate_week(week, scoring_model)
        for _, week in daily_metrics.groupby(
            [*WEEKLY_KEY, "variant_id"], sort=False, dropna=False
        )
    ]
    weekly = pd.DataFrame.from_records(records, columns=OUTPUT_COLUMNS)

    integer_columns = [
        "active_days",
        "empty_days",
        "physical_days",
        "online_only_days",
        "weekend_days",
        "total_event_records",
        "total_events",
        "total_merged_blocks",
        "total_teaching_minutes",
        "total_physical_teaching_minutes",
        "total_span_minutes",
        "total_physical_span_minutes",
        "total_campus_waiting_minutes",
        "longest_campus_wait_minutes",
        "days_with_campus_waiting",
        "days_with_exact_overlaps",
        "days_with_overlaps",
        "exact_overlap_pair_count",
        "overlap_pair_count",
        "total_physical_events",
        "total_campus_events",
        "total_online_events",
        "total_unknown_events",
        "maximum_daily_span",
        "maximum_physical_span",
        "maximum_daily_teaching_minutes",
        "maximum_physical_teaching_minutes",
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

    weekly = calculate_weekly_metrics(daily_metrics, scoring_model)
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
        "total_physical_teaching_minutes": int(
            weekly["total_physical_teaching_minutes"].sum()
        ),
        "total_campus_waiting_minutes": int(
            weekly["total_campus_waiting_minutes"].sum()
        ),
        "weeks_with_campus_waiting": int(
            (weekly["days_with_campus_waiting"] > 0).sum()
        ),
        "maximum_weekly_campus_waiting_minutes": int(
            weekly["total_campus_waiting_minutes"].max()
        ),
        "maximum_single_campus_wait_minutes": int(
            weekly["longest_campus_wait_minutes"].max()
        ),
        "maximum_active_days": int(weekly["active_days"].max()),
        "maximum_balanced_score": float(weekly["balanced_score"].max()),
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
        scoring_model = load_scoring_model(
            repository_root / SCORING_CONFIG_RELATIVE_PATH
        )
        index = load_snapshot_index(repository_root / INDEX_RELATIVE_PATH)
        snapshot_ids = _select_snapshot_ids(
            index, arguments.snapshot_id, arguments.all
        )
        summaries = [
            calculate_snapshot_weekly_metrics(
                snapshot_id, repository_root, scoring_model
            )
            for snapshot_id in snapshot_ids
        ]
    except (WeeklyMetricError, ScoringModelError) as exc:
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
