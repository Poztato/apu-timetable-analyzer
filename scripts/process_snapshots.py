"""Normalize retained APU timetable snapshots into event-level Parquet files."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
CONFIG_RELATIVE_PATH = Path("config/intake_codes.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
CALENDAR_TIMEZONE = "Asia/Kuala_Lumpur"

SOURCE_COLUMN_RENAMES = {
    "INTAKE": "intake_code",
    "MODID": "module_id",
    "MODULE_NAME": "module_name",
    "DAY": "source_day",
    "LOCATION": "location",
    "ROOM": "room",
    "LECTID": "lecturer_id",
    "NAME": "lecturer_name",
    "SAMACCOUNTNAME": "lecturer_account",
    "DATESTAMP": "source_date",
    "DATESTAMP_ISO": "source_date_iso",
    "TIME_FROM": "source_start_time",
    "TIME_TO": "source_end_time",
    "TIME_FROM_ISO": "start_at",
    "TIME_TO_ISO": "end_at",
    "GROUPING": "grouping",
    "CLASS_CODE": "class_code",
    "COLOR": "color",
}

OUTPUT_COLUMNS = [
    "snapshot_id",
    "event_id",
    "source_row_number",
    "intake_code",
    "grouping",
    "start_at",
    "end_at",
    "event_date",
    "week_start",
    "duration_minutes",
    "source_day",
    "source_date",
    "source_date_iso",
    "source_start_time",
    "source_end_time",
    "module_id",
    "module_name",
    "class_code",
    "location",
    "room",
    "delivery_mode",
    "lecturer_id",
    "lecturer_name",
    "lecturer_account",
    "color",
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

DEGREE_INTAKE_PATTERN = re.compile(
    r"^(?P<route>APD|APU)"
    r"(?P<level>[1-4])F"
    r"(?P<year>\d{2})"
    r"(?P<month>0[1-9]|1[0-2])"
    r"(?P<course>[A-Z0-9]+)"
    r"(?:\((?P<specialism>[^()]+)\))?$"
)


class ProcessingError(RuntimeError):
    """Raised when a snapshot cannot be normalized safely."""


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    """Read a JSON object or raise a processing error with context."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProcessingError(f"Cannot find {label}: {path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessingError(f"Cannot read {label}: {path}.") from exc

    if not isinstance(value, dict):
        raise ProcessingError(f"The {label} must contain a JSON object.")
    return value


def load_snapshot_index(index_path: Path) -> list[dict[str, Any]]:
    """Read and minimally validate the retained snapshot index."""

    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProcessingError(f"Cannot find snapshot index: {index_path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessingError(f"Cannot read snapshot index: {index_path}.") from exc

    if not isinstance(value, list) or not value:
        raise ProcessingError("The snapshot index must contain at least one entry.")

    for position, entry in enumerate(value, start=1):
        if not isinstance(entry, dict):
            raise ProcessingError(f"Snapshot index entry {position} must be an object.")
        for field in ("snapshot_id", "sha256", "path"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ProcessingError(
                    f"Snapshot index entry {position} has no valid {field}."
                )
    return value


def parse_intake_code(
    intake_code: str, intake_config: Mapping[str, Any]
) -> dict[str, Any]:
    """Parse a known degree intake format without guessing unknown formats."""

    empty_result = {
        "programme_route": None,
        "programme_route_name": None,
        "academic_level": None,
        "intake_year": None,
        "intake_month": None,
        "course_code": None,
        "course_name": None,
        "specialism_code": None,
        "specialism_name": None,
        "school": None,
        "study_mode": None,
        "parse_status": "unparsed",
        "parser_family": None,
    }

    match = DEGREE_INTAKE_PATTERN.fullmatch(intake_code.strip().upper())
    if match is None:
        return empty_result

    route = match.group("route")
    course_code = match.group("course")
    specialism_code = match.group("specialism")

    routes = intake_config.get("programme_routes", {})
    courses = intake_config.get("courses", {})
    specialisms = intake_config.get("specialisms", {})
    course_details = courses.get(course_code, {})
    if not isinstance(course_details, Mapping):
        course_details = {}

    return {
        "programme_route": route,
        "programme_route_name": routes.get(route),
        "academic_level": int(match.group("level")),
        "intake_year": 2000 + int(match.group("year")),
        "intake_month": int(match.group("month")),
        "course_code": course_code,
        "course_name": course_details.get("name"),
        "specialism_code": specialism_code,
        "specialism_name": (
            specialisms.get(specialism_code) if specialism_code else None
        ),
        "school": course_details.get("school"),
        "study_mode": None,
        "parse_status": "parsed",
        "parser_family": "apu_degree",
    }


def _read_verified_snapshot(
    repository_root: Path, index_entry: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Read a gzip snapshot and verify it against its index entry."""

    repository_root = repository_root.resolve()
    snapshot_path = (repository_root / Path(index_entry["path"])).resolve()
    if not snapshot_path.is_relative_to(repository_root):
        raise ProcessingError(
            f"Snapshot path is outside the repository: {snapshot_path}."
        )

    try:
        with gzip.open(snapshot_path, "rb") as snapshot_file:
            snapshot_bytes = snapshot_file.read()
    except FileNotFoundError as exc:
        raise ProcessingError(f"Cannot find snapshot: {snapshot_path}.") from exc
    except (EOFError, OSError) as exc:
        raise ProcessingError(f"Cannot decompress snapshot: {snapshot_path}.") from exc

    actual_hash = hashlib.sha256(snapshot_bytes).hexdigest()
    if actual_hash != index_entry["sha256"]:
        raise ProcessingError(
            f"Snapshot hash mismatch for {index_entry['snapshot_id']}: "
            f"expected {index_entry['sha256']}, found {actual_hash}."
        )

    try:
        records = json.loads(snapshot_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProcessingError(
            f"Snapshot {index_entry['snapshot_id']} is not valid UTF-8 JSON."
        ) from exc

    if not isinstance(records, list) or not records:
        raise ProcessingError(
            f"Snapshot {index_entry['snapshot_id']} must contain a non-empty array."
        )
    if any(not isinstance(record, dict) for record in records):
        raise ProcessingError(
            f"Snapshot {index_entry['snapshot_id']} contains a non-object row."
        )

    expected_count = index_entry.get("row_count")
    if expected_count is not None and len(records) != expected_count:
        raise ProcessingError(
            f"Snapshot row count mismatch for {index_entry['snapshot_id']}: "
            f"expected {expected_count}, found {len(records)}."
        )
    return records


def _deduplicate_exact_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Keep the first occurrence of each exact JSON object."""

    seen: set[str] = set()
    unique_records: list[dict[str, Any]] = []
    for source_row_number, record in enumerate(records, start=1):
        canonical = json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if canonical in seen:
            continue
        seen.add(canonical)
        copied_record = dict(record)
        copied_record["source_row_number"] = source_row_number
        unique_records.append(copied_record)
    return unique_records, len(records) - len(unique_records)


def _clean_string_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        frame[column] = frame[column].astype("string").str.strip()
        frame[column] = frame[column].replace("", pd.NA)


def _classify_delivery_mode(location: Any, room: Any) -> str:
    normalized_location = "" if pd.isna(location) else str(location).strip().upper()
    normalized_room = "" if pd.isna(room) else str(room).strip().upper()
    if (
        normalized_location in {"ONL", "ONLINE"}
        or normalized_room.startswith("ONL")
        or "ONLINE" in normalized_room
    ):
        return "online"
    if normalized_location or normalized_room:
        return "campus"
    return "unknown"


def _event_id(row: Any) -> str:
    identity = {
        "intake_code": row.intake_code,
        "grouping": row.grouping,
        "module_id": row.module_id,
        "start_at": row.start_at.isoformat(),
        "end_at": row.end_at.isoformat(),
        "location": None if pd.isna(row.location) else row.location,
        "room": None if pd.isna(row.room) else row.room,
        "class_code": None if pd.isna(row.class_code) else row.class_code,
        "lecturer_id": None if pd.isna(row.lecturer_id) else row.lecturer_id,
        "lecturer_account": (
            None if pd.isna(row.lecturer_account) else row.lecturer_account
        ),
    }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_events(
    records: Sequence[Mapping[str, Any]],
    snapshot_id: str,
    intake_config: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Return a cleaned event table and normalization statistics."""

    unique_records, duplicates_removed = _deduplicate_exact_records(records)
    frame = pd.DataFrame.from_records(unique_records)

    missing_columns = sorted(set(SOURCE_COLUMN_RENAMES).difference(frame.columns))
    if missing_columns:
        raise ProcessingError(
            "Snapshot is missing required event columns: "
            + ", ".join(missing_columns)
            + "."
        )

    frame = frame[[*SOURCE_COLUMN_RENAMES, "source_row_number"]].rename(
        columns=SOURCE_COLUMN_RENAMES
    )
    _clean_string_columns(frame, SOURCE_COLUMN_RENAMES.values())

    required_nonblank = [
        "intake_code",
        "module_id",
        "module_name",
        "start_at",
        "end_at",
    ]
    for column in required_nonblank:
        if frame[column].isna().any():
            bad_rows = frame.loc[frame[column].isna(), "source_row_number"].tolist()
            raise ProcessingError(
                f"Column {column} is blank in source rows: {bad_rows[:10]}."
            )

    frame["grouping"] = frame["grouping"].fillna("ALL")

    parsed_start = pd.to_datetime(frame["start_at"], errors="coerce", utc=True)
    parsed_end = pd.to_datetime(frame["end_at"], errors="coerce", utc=True)
    if parsed_start.isna().any() or parsed_end.isna().any():
        bad_mask = parsed_start.isna() | parsed_end.isna()
        bad_rows = frame.loc[bad_mask, "source_row_number"].tolist()
        raise ProcessingError(
            f"Invalid event timestamp in source rows: {bad_rows[:10]}."
        )

    frame["start_at"] = parsed_start.dt.tz_convert(CALENDAR_TIMEZONE)
    frame["end_at"] = parsed_end.dt.tz_convert(CALENDAR_TIMEZONE)
    duration_seconds = (frame["end_at"] - frame["start_at"]).dt.total_seconds()
    if (duration_seconds <= 0).any():
        bad_rows = frame.loc[
            duration_seconds <= 0, "source_row_number"
        ].tolist()
        raise ProcessingError(
            f"Event end must be later than start in source rows: {bad_rows[:10]}."
        )
    if (duration_seconds % 60 != 0).any():
        bad_rows = frame.loc[
            duration_seconds % 60 != 0, "source_row_number"
        ].tolist()
        raise ProcessingError(
            f"Event duration is not a whole minute in source rows: {bad_rows[:10]}."
        )
    frame["duration_minutes"] = (duration_seconds / 60).astype("int64")

    local_dates = frame["start_at"].dt.tz_localize(None).dt.normalize()
    frame["event_date"] = local_dates.dt.date
    frame["week_start"] = (
        local_dates - pd.to_timedelta(local_dates.dt.dayofweek, unit="D")
    ).dt.date
    frame["delivery_mode"] = [
        _classify_delivery_mode(location, room)
        for location, room in zip(frame["location"], frame["room"])
    ]
    frame["event_id"] = [_event_id(row) for row in frame.itertuples()]
    frame["snapshot_id"] = snapshot_id

    metadata_records = []
    for intake_code in frame["intake_code"].drop_duplicates().sort_values():
        metadata_records.append(
            {
                "intake_code": intake_code,
                **parse_intake_code(intake_code, intake_config),
            }
        )
    metadata = pd.DataFrame.from_records(metadata_records)
    frame = frame.merge(metadata, on="intake_code", how="left", validate="many_to_one")
    for column in ("academic_level", "intake_year", "intake_month"):
        frame[column] = frame[column].astype("Int64")
    metadata_text_columns = [
        "programme_route",
        "programme_route_name",
        "course_code",
        "course_name",
        "specialism_code",
        "specialism_name",
        "school",
        "study_mode",
        "parse_status",
        "parser_family",
    ]
    for column in metadata_text_columns:
        frame[column] = frame[column].astype("string")

    frame = frame[OUTPUT_COLUMNS].sort_values(
        ["start_at", "intake_code", "grouping", "module_id", "source_row_number"],
        kind="stable",
    )
    frame = frame.reset_index(drop=True)

    parse_counts = metadata["parse_status"].value_counts().to_dict()
    statistics = {
        "source_row_count": len(records),
        "output_row_count": len(frame),
        "duplicates_removed": duplicates_removed,
        "distinct_intake_count": len(metadata),
        "parsed_intake_count": int(parse_counts.get("parsed", 0)),
        "unparsed_intake_count": int(parse_counts.get("unparsed", 0)),
    }
    return frame, statistics


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
        raise ProcessingError(
            "Writing Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def process_snapshot(
    index_entry: Mapping[str, Any],
    repository_root: Path,
    intake_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify, normalize, and write one retained snapshot."""

    records = _read_verified_snapshot(repository_root, index_entry)
    frame, statistics = normalize_events(
        records, index_entry["snapshot_id"], intake_config
    )
    output_path = (
        repository_root
        / PROCESSED_DIRECTORY_RELATIVE_PATH
        / index_entry["snapshot_id"]
        / "events.parquet"
    )
    _write_parquet_atomically(frame, output_path)

    delivery_counts = {
        str(key): int(value)
        for key, value in frame["delivery_mode"].value_counts().sort_index().items()
    }
    grouping_counts = {
        str(key): int(value)
        for key, value in frame["grouping"].value_counts().sort_index().items()
    }
    return {
        "status": "processed",
        "snapshot_id": index_entry["snapshot_id"],
        **statistics,
        "minimum_event_date": min(frame["event_date"]).isoformat(),
        "maximum_event_date": max(frame["event_date"]).isoformat(),
        "delivery_mode_counts": delivery_counts,
        "grouping_counts": grouping_counts,
        "output_path": output_path.relative_to(repository_root).as_posix(),
        "output_size_bytes": output_path.stat().st_size,
    }


def _select_entries(
    index: Sequence[dict[str, Any]], snapshot_id: str | None, process_all: bool
) -> list[dict[str, Any]]:
    if process_all:
        return list(index)
    if snapshot_id is None:
        return [index[-1]]

    matches = [entry for entry in index if entry["snapshot_id"] == snapshot_id]
    if not matches:
        raise ProcessingError(f"Snapshot ID is not in the index: {snapshot_id}.")
    return matches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize retained timetable snapshots into Parquet event tables."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing data/snapshots and config.",
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
        intake_config = load_json_object(
            repository_root / CONFIG_RELATIVE_PATH, "intake-code config"
        )
        entries = _select_entries(index, arguments.snapshot_id, arguments.all)
        summaries = [
            process_snapshot(entry, repository_root, intake_config)
            for entry in entries
        ]
    except ProcessingError as exc:
        print(f"Stage 2 processing failed: {exc}", file=sys.stderr)
        return 1

    result: dict[str, Any]
    if len(summaries) == 1:
        result = summaries[0]
    else:
        result = {"status": "processed", "snapshot_count": len(summaries), "snapshots": summaries}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
