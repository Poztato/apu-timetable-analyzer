"""Generate deterministic, lecturer-free JSON for the static dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.calculate_daily_metrics import DailyMetricError, load_scoring_config
from scripts.rank_timetables import (
    CRITERION_COLUMNS,
    PERCENTILE_METHOD,
    RankingError,
    RankingProfile,
    load_ranking_config,
)


SCHEMA_VERSION = 3
CALENDAR_TIMEZONE = "Asia/Kuala_Lumpur"

INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
SCORING_CONFIG_RELATIVE_PATH = Path("config/scoring.json")
RANKING_CONFIG_RELATIVE_PATH = Path("config/ranking.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
OUTPUT_DIRECTORY_RELATIVE_PATH = Path("web/public/data")

WEEKLY_INPUT_FILENAME = "intake_week_metrics.parquet"
RANKING_INPUT_FILENAME = "default_rankings.parquet"
DAILY_INPUT_FILENAME = "daily_metrics.parquet"
VARIANT_INPUT_FILENAME = "variant_events.parquet"

SNAPSHOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

INTAKE_METADATA_COLUMNS = [
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

WEEKLY_METRIC_COLUMNS = [
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
]

RANKING_RESULT_COLUMNS = [
    *[
        f"{criterion}_{suffix}"
        for criterion in CRITERION_COLUMNS
        for suffix in ("percentile", "weight", "contribution")
    ],
    "overall_frustration",
    "comparison_set_size",
    "comparison_median_score",
    "distance_from_median",
    "best_rank",
    "worst_rank",
    "is_best",
    "is_worst",
    "is_most_average",
]

DAILY_METRIC_COLUMNS = [
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
]

BLOCK_SOURCE_COLUMNS = [
    "variant_id",
    "slot_id",
    "event_date",
    "start_at",
    "end_at",
    "duration_minutes",
    "module_id",
    "module_name",
    "class_code",
    "location",
    "room",
    "delivery_mode",
    "source_grouping",
    "is_common_event",
    "is_elective",
    "elective_group_id",
    "elective_option_id",
    "is_shared_slot",
    "shared_group_count",
    "color",
]

BLOCK_EXPORT_COLUMNS = [
    "event_date",
    "start_at",
    "end_at",
    "duration_minutes",
    "module_id",
    "module_name",
    "class_code",
    "location",
    "room",
    "delivery_mode",
    "source_grouping",
    "is_common_event",
    "is_elective",
    "elective_group_id",
    "elective_option_id",
    "is_shared_slot",
    "shared_group_count",
    "color",
]

PRIVATE_EVENT_FIELDS = {
    "lecturer_id",
    "lecturer_name",
    "lecturer_account",
    "source_row_number",
    "event_id",
    "variant_event_id",
}


class DashboardDataError(RuntimeError):
    """Raised when safe dashboard data cannot be generated."""


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DashboardDataError(f"Cannot find {label}: {path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardDataError(f"Cannot read {label}: {path}.") from exc


def load_snapshot_index(path: Path) -> list[dict[str, Any]]:
    index = _read_json(path, "snapshot index")
    if not isinstance(index, list) or not index:
        raise DashboardDataError(
            "The snapshot index must contain at least one entry."
        )

    required_strings = {
        "snapshot_id",
        "collected_at",
        "minimum_event_date",
        "maximum_event_date",
    }
    required_counts = {"row_count", "distinct_intake_count"}
    seen_ids: set[str] = set()

    for position, entry in enumerate(index, start=1):
        if not isinstance(entry, dict):
            raise DashboardDataError(
                f"Snapshot index entry {position} is not a JSON object."
            )
        for field in required_strings:
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise DashboardDataError(
                    f"Snapshot index entry {position} has no valid {field}."
                )
        snapshot_id = entry["snapshot_id"]
        if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
            raise DashboardDataError(
                f"Snapshot index entry {position} has an unsafe snapshot_id."
            )
        if snapshot_id in seen_ids:
            raise DashboardDataError(
                f"Snapshot index contains duplicate snapshot ID {snapshot_id}."
            )
        seen_ids.add(snapshot_id)

        for field in required_counts:
            value = entry.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DashboardDataError(
                    f"Snapshot index entry {position} has no valid {field}."
                )
        try:
            minimum_date = date.fromisoformat(entry["minimum_event_date"])
            maximum_date = date.fromisoformat(entry["maximum_event_date"])
        except ValueError as exc:
            raise DashboardDataError(
                f"Snapshot index entry {position} has an invalid coverage date."
            ) from exc
        if maximum_date < minimum_date:
            raise DashboardDataError(
                f"Snapshot index entry {position} has reversed coverage dates."
            )

    return index


def _read_parquet(path: Path, label: str) -> pd.DataFrame:
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except FileNotFoundError as exc:
        raise DashboardDataError(f"Cannot find {label}: {path}.") from exc
    except ImportError as exc:
        raise DashboardDataError(
            "Reading Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise DashboardDataError(f"Cannot read {label}: {path}.") from exc


def _require_columns(
    frame: pd.DataFrame, required: Sequence[str] | set[str], label: str
) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise DashboardDataError(
            f"{label} is missing required columns: {', '.join(missing)}."
        )


def _validate_snapshot_frame(
    frame: pd.DataFrame, snapshot_id: str, label: str
) -> None:
    if frame.empty:
        raise DashboardDataError(f"{label} contains no rows.")
    _require_columns(frame, {"snapshot_id"}, label)
    values = frame["snapshot_id"].drop_duplicates().tolist()
    if values != [snapshot_id]:
        raise DashboardDataError(
            f"{label} does not belong only to snapshot {snapshot_id}."
        )


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise DashboardDataError("Dashboard data contains a non-finite number.")
    if isinstance(value, (str, int, float, bool)):
        return value
    raise DashboardDataError(
        f"Dashboard data contains unsupported value type {type(value).__name__}."
    )


def _frame_table(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    first_field: tuple[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    _require_columns(frame, columns, "Dashboard export frame")
    names = list(columns)
    prefix_name: str | None = None
    prefix_values: Sequence[Any] | None = None
    if first_field is not None:
        prefix_name, prefix_values = first_field
        if len(prefix_values) != len(frame):
            raise DashboardDataError(
                f"Dashboard field {prefix_name} has the wrong number of values."
            )

    output_names = [prefix_name, *names] if prefix_name is not None else names
    rows: list[list[Any]] = []
    for position, values in enumerate(
        frame[names].itertuples(index=False, name=None)
    ):
        row = [_json_scalar(value) for value in values]
        if prefix_name is not None and prefix_values is not None:
            row.insert(0, _json_scalar(prefix_values[position]))
        rows.append(row)
    return {"columns": output_names, "rows": rows}


def _series_mapping(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    return {
        str(variant_id): _json_scalar(value)
        for variant_id, value in frame[["variant_id", column]].itertuples(
            index=False, name=None
        )
    }


def _validate_weekly_and_rankings(
    weekly: pd.DataFrame,
    rankings: pd.DataFrame,
    snapshot_id: str,
    profile: RankingProfile,
) -> None:
    weekly_required = {
        "variant_id",
        "snapshot_id",
        "week_start",
        "intake_code",
        "grouping",
        "elective_profile",
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        *WEEKLY_METRIC_COLUMNS,
        *INTAKE_METADATA_COLUMNS,
    }
    ranking_required = {
        "variant_id",
        "snapshot_id",
        "week_start",
        "intake_code",
        "grouping",
        "elective_profile",
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        *CRITERION_COLUMNS.values(),
        *RANKING_RESULT_COLUMNS,
        "scoring_profile",
        "scoring_profile_id",
        "percentile_method",
    }
    _require_columns(weekly, weekly_required, "Weekly metrics")
    _require_columns(rankings, ranking_required, "Default rankings")
    _validate_snapshot_frame(weekly, snapshot_id, "Weekly metrics")
    _validate_snapshot_frame(rankings, snapshot_id, "Default rankings")

    weekly_key = [
        "snapshot_id",
        "week_start",
        "intake_code",
        "grouping",
        "elective_profile",
    ]
    if weekly["variant_id"].isna().any() or weekly["variant_id"].duplicated().any():
        raise DashboardDataError("Weekly metrics has invalid variant IDs.")
    if weekly.duplicated(weekly_key).any():
        raise DashboardDataError("Weekly metrics has duplicate timetable variants.")
    if rankings["variant_id"].isna().any() or rankings[
        "variant_id"
    ].duplicated().any():
        raise DashboardDataError("Default rankings has invalid variant IDs.")

    weekly_ids = set(weekly["variant_id"].astype(str))
    ranking_ids = set(rankings["variant_id"].astype(str))
    if weekly_ids != ranking_ids:
        raise DashboardDataError(
            "Default rankings does not contain exactly the weekly metric variants."
        )

    for column in [
        "week_start",
        "intake_code",
        "grouping",
        "elective_profile",
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        *CRITERION_COLUMNS.values(),
    ]:
        if _series_mapping(weekly, column) != _series_mapping(rankings, column):
            raise DashboardDataError(
                f"Default rankings does not match weekly metric column {column}."
            )

    profile_ids = set(rankings["scoring_profile_id"].dropna().astype(str))
    profile_names = set(rankings["scoring_profile"].dropna().astype(str))
    percentile_methods = set(rankings["percentile_method"].dropna().astype(str))
    if profile_ids != {profile.profile_id} or profile_names != {
        profile.description
    }:
        raise DashboardDataError(
            "Default rankings was not generated with the configured ranking profile."
        )
    if percentile_methods != {PERCENTILE_METHOD}:
        raise DashboardDataError(
            "Default rankings uses an unexpected percentile method."
        )


def _build_scoring_payload(
    profile: RankingProfile, thresholds: Mapping[str, Any]
) -> dict[str, Any]:
    normalized_weights = profile.normalized_weights
    criteria = [
        {
            "key": criterion,
            "metric": CRITERION_COLUMNS[criterion],
            "position_weight": profile.position_weights[position],
            "normalized_weight": normalized_weights[criterion],
        }
        for position, criterion in enumerate(profile.criterion_order)
    ]
    return {
        "default_criterion_order": list(profile.criterion_order),
        "position_weights": list(profile.position_weights),
        "profile": profile.description,
        "profile_id": profile.profile_id,
        "percentile_method": PERCENTILE_METHOD,
        "criteria": criteria,
        "thresholds": dict(thresholds),
    }


def _build_intake_records(weekly: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for intake_code, intake_rows in weekly.groupby(
        "intake_code", sort=True, dropna=False
    ):
        if pd.isna(intake_code):
            raise DashboardDataError("Weekly metrics contains a blank intake code.")
        metadata = intake_rows[INTAKE_METADATA_COLUMNS].drop_duplicates()
        if len(metadata) != 1:
            raise DashboardDataError(
                f"Intake metadata is inconsistent for {intake_code}."
            )
        metadata_values = metadata.iloc[0]
        record = {
            "intake_code": str(intake_code),
            **{
                column: _json_scalar(metadata_values[column])
                for column in INTAKE_METADATA_COLUMNS
            },
            "week_starts": sorted(
                {_json_scalar(value) for value in intake_rows["week_start"]}
            ),
            "groupings": sorted(
                {str(value) for value in intake_rows["grouping"] if not pd.isna(value)}
            ),
        }
        records.append(record)
    return records


def _paired_filter_options(
    intakes: Sequence[Mapping[str, Any]], code_field: str, name_field: str
) -> list[dict[str, Any]]:
    options: dict[str, set[str]] = {}
    for intake in intakes:
        code = intake.get(code_field)
        name = intake.get(name_field)
        if code is None:
            continue
        code_text = str(code)
        options.setdefault(code_text, set())
        if name is not None:
            options[code_text].add(str(name))

    result = []
    for code in sorted(options):
        names = options[code]
        if len(names) > 1:
            raise DashboardDataError(
                f"Filter label is inconsistent for {code_field} {code}."
            )
        result.append(
            {"code": code, "name": next(iter(names)) if names else None}
        )
    return result


def _sorted_filter_values(
    intakes: Sequence[Mapping[str, Any]], field: str
) -> list[Any]:
    values = {intake.get(field) for intake in intakes}
    values.discard(None)
    return sorted(values)


def _build_filter_options(
    intakes: Sequence[Mapping[str, Any]], weekly: pd.DataFrame
) -> dict[str, Any]:
    delivery_modes = []
    if int(weekly["total_campus_events"].sum()) > 0:
        delivery_modes.append("campus")
    if int(weekly["total_online_events"].sum()) > 0:
        delivery_modes.append("online")
    if int(weekly["total_unknown_events"].sum()) > 0:
        delivery_modes.append("unknown")

    return {
        "programme_levels": _paired_filter_options(
            intakes, "programme_level", "programme_level_name"
        ),
        "programme_routes": _paired_filter_options(
            intakes, "programme_route", "programme_route_name"
        ),
        "academic_levels": _sorted_filter_values(intakes, "academic_level"),
        "intake_years": _sorted_filter_values(intakes, "intake_year"),
        "intake_months": _sorted_filter_values(intakes, "intake_month"),
        "courses": _paired_filter_options(intakes, "course_code", "course_name"),
        "specialisms": _paired_filter_options(
            intakes, "specialism_code", "specialism_name"
        ),
        "schools": _sorted_filter_values(intakes, "school"),
        "study_modes": _sorted_filter_values(intakes, "study_mode"),
        "parse_statuses": _sorted_filter_values(intakes, "parse_status"),
        "groupings": sorted(
            {
                grouping
                for intake in intakes
                for grouping in intake["groupings"]
            }
        ),
        "delivery_modes": delivery_modes,
    }


def _as_date(value: Any, label: str) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DashboardDataError(f"{label} contains an invalid date.") from exc


def _build_week_records(weekly: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    for week_start, week_rows in weekly.groupby(
        "week_start", sort=True, dropna=False
    ):
        parsed_start = _as_date(week_start, "Weekly metrics")
        records.append(
            {
                "week_start": parsed_start.isoformat(),
                "week_end": (parsed_start + timedelta(days=6)).isoformat(),
                "intake_count": int(week_rows["intake_code"].nunique()),
                "variant_count": len(week_rows),
            }
        )
    return records


def _build_snapshot_metadata(
    entry: Mapping[str, Any], weekly: pd.DataFrame
) -> dict[str, Any]:
    return {
        "snapshot_id": entry["snapshot_id"],
        "collected_at": entry["collected_at"],
        "feed_last_modified": entry.get("last_modified"),
        "minimum_event_date": entry["minimum_event_date"],
        "maximum_event_date": entry["maximum_event_date"],
        "source_row_count": entry["row_count"],
        "source_intake_count": entry["distinct_intake_count"],
        "active_intake_count": int(weekly["intake_code"].nunique()),
        "week_count": int(weekly["week_start"].nunique()),
        "variant_count": len(weekly),
    }


def _build_compact_snapshot(
    entry: Mapping[str, Any],
    repository_root: Path,
    profile: RankingProfile,
    scoring: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, int], pd.DataFrame]:
    snapshot_id = str(entry["snapshot_id"])
    snapshot_directory = (
        repository_root / PROCESSED_DIRECTORY_RELATIVE_PATH / snapshot_id
    )
    weekly = _read_parquet(
        snapshot_directory / WEEKLY_INPUT_FILENAME,
        f"Stage 5 weekly metrics for {snapshot_id}",
    )
    rankings = _read_parquet(
        snapshot_directory / RANKING_INPUT_FILENAME,
        f"Stage 6 default rankings for {snapshot_id}",
    )
    _validate_weekly_and_rankings(weekly, rankings, snapshot_id, profile)

    weekly = weekly.sort_values(
        ["week_start", "intake_code", "grouping", "elective_profile"],
        kind="stable",
    ).reset_index(drop=True)
    variant_indices = {
        str(variant_id): position
        for position, variant_id in enumerate(weekly["variant_id"])
    }
    ranking_by_variant = rankings.set_index("variant_id")
    ranking_rows = ranking_by_variant.loc[list(weekly["variant_id"])]

    weekly_export = weekly[
        [
            "week_start",
            "intake_code",
            "grouping",
            "elective_profile",
            "elective_profile_name",
            "elective_status",
            "elective_rule_id",
            *WEEKLY_METRIC_COLUMNS,
        ]
    ].copy()
    for column in RANKING_RESULT_COLUMNS:
        weekly_export[column] = ranking_rows[column].to_numpy()

    intakes = _build_intake_records(weekly)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": "snapshot_metrics",
        "table_encoding": "columns_and_rows",
        "timezone": CALENDAR_TIMEZONE,
        "snapshot": _build_snapshot_metadata(entry, weekly),
        "scoring": scoring,
        "weeks": _build_week_records(weekly),
        "filters": _build_filter_options(intakes, weekly),
        "intakes": intakes,
        "weekly_metrics": _frame_table(
            weekly_export,
            weekly_export.columns,
            first_field=("variant_index", range(len(weekly_export))),
        ),
    }
    return payload, variant_indices, weekly


def _validate_variant_coverage(
    frame: pd.DataFrame,
    variant_indices: Mapping[str, int],
    label: str,
) -> None:
    frame_ids = set(frame["variant_id"].astype(str))
    expected_ids = set(variant_indices)
    if frame_ids != expected_ids:
        raise DashboardDataError(
            f"{label} does not contain exactly the weekly metric variants."
        )


def _build_daily_records(
    daily: pd.DataFrame, variant_indices: Mapping[str, int]
) -> dict[str, Any]:
    required = {"variant_id", "snapshot_id", *DAILY_METRIC_COLUMNS}
    _require_columns(daily, required, "Daily metrics")
    _validate_variant_coverage(daily, variant_indices, "Daily metrics")
    if daily.duplicated(["variant_id", "event_date"]).any():
        raise DashboardDataError("Daily metrics has duplicate variant dates.")

    export = daily.copy()
    export["variant_index"] = export["variant_id"].astype(str).map(variant_indices)
    export = export.sort_values(
        ["variant_index", "event_date"], kind="stable"
    ).reset_index(drop=True)
    return _frame_table(
        export,
        DAILY_METRIC_COLUMNS,
        first_field=("variant_index", export["variant_index"].tolist()),
    )


def _build_timetable_blocks(
    variants: pd.DataFrame, variant_indices: Mapping[str, int]
) -> dict[str, Any]:
    _require_columns(
        variants,
        {"snapshot_id", *BLOCK_SOURCE_COLUMNS},
        "Variant events",
    )
    _validate_variant_coverage(variants, variant_indices, "Variant events")
    if variants[["variant_id", "slot_id", "start_at", "end_at"]].isna().any().any():
        raise DashboardDataError("Variant events contains a blank block identity.")
    if (variants["end_at"] <= variants["start_at"]).any():
        raise DashboardDataError("Variant events contains an invalid time interval.")

    public_blocks = variants[BLOCK_SOURCE_COLUMNS].drop_duplicates().copy()
    conflicting = public_blocks.duplicated(
        ["variant_id", "slot_id"], keep=False
    )
    if conflicting.any():
        raise DashboardDataError(
            "Variant events contains conflicting public data for one timetable block."
        )

    public_blocks["variant_index"] = (
        public_blocks["variant_id"].astype(str).map(variant_indices)
    )
    public_blocks = public_blocks.sort_values(
        ["variant_index", "start_at", "end_at", "module_id", "room"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)
    return _frame_table(
        public_blocks,
        BLOCK_EXPORT_COLUMNS,
        first_field=(
            "variant_index", public_blocks["variant_index"].tolist()
        ),
    )


def _build_latest_snapshot(
    compact: Mapping[str, Any],
    snapshot_id: str,
    repository_root: Path,
    variant_indices: Mapping[str, int],
) -> dict[str, Any]:
    snapshot_directory = (
        repository_root / PROCESSED_DIRECTORY_RELATIVE_PATH / snapshot_id
    )
    daily = _read_parquet(
        snapshot_directory / DAILY_INPUT_FILENAME,
        f"Stage 4 daily metrics for {snapshot_id}",
    )
    variants = _read_parquet(
        snapshot_directory / VARIANT_INPUT_FILENAME,
        f"Stage 3 variant events for {snapshot_id}",
    )
    _validate_snapshot_frame(daily, snapshot_id, "Daily metrics")
    _validate_snapshot_frame(variants, snapshot_id, "Variant events")

    daily_records = _build_daily_records(daily, variant_indices)
    timetable_blocks = _build_timetable_blocks(variants, variant_indices)
    latest = dict(compact)
    latest["dataset_kind"] = "latest_snapshot"
    latest["snapshot"] = {
        **compact["snapshot"],
        "daily_record_count": len(daily_records["rows"]),
        "timetable_block_count": len(timetable_blocks["rows"]),
    }
    latest["daily_metrics"] = daily_records
    latest["timetable_blocks"] = timetable_blocks
    return latest


def _assert_no_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        exposed = PRIVATE_EVENT_FIELDS.intersection(value)
        if exposed:
            raise DashboardDataError(
                "Dashboard payload exposes private event fields: "
                + ", ".join(sorted(exposed))
                + "."
            )
        for child in value.values():
            _assert_no_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_private_fields(child)


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DashboardDataError("Cannot serialize dashboard data as JSON.") from exc
    return (text + "\n").encode("utf-8")


def _write_json_atomically(payload: Mapping[str, Any], target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = _json_bytes(payload)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.stem}.",
            suffix=".json",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(encoded)
        os.replace(temporary_path, target)
    except OSError as exc:
        raise DashboardDataError(f"Cannot write dashboard JSON: {target}.") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return {
        "path": target,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _display_path(path: Path, repository_root: Path) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return str(path)


def build_dashboard_data(repository_root: Path) -> dict[str, Any]:
    """Build all Stage 8 files from indexed, processed snapshots."""

    repository_root = repository_root.resolve()
    try:
        profile = load_ranking_config(
            repository_root / RANKING_CONFIG_RELATIVE_PATH
        )
        thresholds = load_scoring_config(
            repository_root / SCORING_CONFIG_RELATIVE_PATH
        )
    except (RankingError, DailyMetricError) as exc:
        raise DashboardDataError(str(exc)) from exc
    scoring = _build_scoring_payload(profile, thresholds.as_dict())
    index = load_snapshot_index(repository_root / INDEX_RELATIVE_PATH)

    compact_snapshots: list[tuple[dict[str, Any], dict[str, int]]] = []
    manifest_entries: list[dict[str, Any]] = []
    for entry in index:
        compact, variant_indices, _ = _build_compact_snapshot(
            entry, repository_root, profile, scoring
        )
        _assert_no_private_fields(compact)
        snapshot_id = str(entry["snapshot_id"])
        compact_snapshots.append((compact, variant_indices))
        manifest_entries.append(
            {
                **compact["snapshot"],
                "history_file": f"history/{snapshot_id}.json",
            }
        )

    latest_compact, latest_variant_indices = compact_snapshots[-1]
    latest_snapshot_id = str(index[-1]["snapshot_id"])
    latest = _build_latest_snapshot(
        latest_compact,
        latest_snapshot_id,
        repository_root,
        latest_variant_indices,
    )
    _assert_no_private_fields(latest)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "latest_snapshot_id": latest_snapshot_id,
        "snapshot_count": len(index),
        "snapshots": manifest_entries,
    }

    output_directory = repository_root / OUTPUT_DIRECTORY_RELATIVE_PATH
    written_files: list[dict[str, Any]] = []
    for compact, _ in compact_snapshots:
        snapshot_id = compact["snapshot"]["snapshot_id"]
        written_files.append(
            _write_json_atomically(
                compact,
                output_directory / "history" / f"{snapshot_id}.json",
            )
        )
    written_files.append(
        _write_json_atomically(latest, output_directory / "latest.json")
    )
    written_files.append(
        _write_json_atomically(manifest, output_directory / "snapshots.json")
    )

    return {
        "status": "exported",
        "schema_version": SCHEMA_VERSION,
        "snapshot_count": len(index),
        "latest_snapshot_id": latest_snapshot_id,
        "latest_week_count": len(latest["weeks"]),
        "latest_intake_count": len(latest["intakes"]),
        "latest_variant_count": len(latest["weekly_metrics"]["rows"]),
        "latest_daily_record_count": len(latest["daily_metrics"]["rows"]),
        "latest_timetable_block_count": len(
            latest["timetable_blocks"]["rows"]
        ),
        "output_size_bytes": sum(item["size_bytes"] for item in written_files),
        "files": [
            {
                "path": _display_path(item["path"], repository_root),
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for item in written_files
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate static dashboard JSON from processed snapshots."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing config and processed snapshot data.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        summary = build_dashboard_data(arguments.repository_root)
    except DashboardDataError as exc:
        print(f"Stage 8 export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
