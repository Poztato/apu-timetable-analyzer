"""Construct group-specific timetable variants from cleaned event snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
INPUT_FILENAME = "events.parquet"
OUTPUT_FILENAME = "variant_events.parquet"

VARIANT_KEY = ["snapshot_id", "week_start", "intake_code", "grouping"]
INTAKE_WEEK_KEY = ["snapshot_id", "week_start", "intake_code"]
SLOT_SCOPE_KEY = ["snapshot_id", "week_start", "intake_code", "slot_id"]

REQUIRED_COLUMNS = {
    "snapshot_id",
    "event_id",
    "week_start",
    "intake_code",
    "grouping",
    "start_at",
    "end_at",
    "module_id",
    "location",
    "room",
    "class_code",
}

IDENTIFIER_COLUMNS = [
    "variant_id",
    "variant_event_id",
    "slot_id",
    "event_id",
    "snapshot_id",
    "week_start",
    "intake_code",
    "grouping",
    "source_grouping",
    "is_common_event",
    "is_shared_slot",
    "shared_group_count",
]


class VariantError(RuntimeError):
    """Raised when timetable variants cannot be constructed safely."""


def _stable_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _optional_string(value: Any) -> str | None:
    return None if pd.isna(value) else str(value)


def _variant_id(row: Any) -> str:
    return _stable_hash(
        {
            "snapshot_id": row.snapshot_id,
            "week_start": row.week_start.isoformat(),
            "intake_code": row.intake_code,
            "grouping": row.grouping,
        }
    )


def _slot_id(row: Any) -> str:
    """Identify the same scheduled slot across groups and snapshots."""

    return _stable_hash(
        {
            "intake_code": row.intake_code,
            "start_at": row.start_at.isoformat(),
            "end_at": row.end_at.isoformat(),
            "module_id": row.module_id,
            "location": _optional_string(row.location),
            "room": _optional_string(row.room),
            "class_code": _optional_string(row.class_code),
        }
    )


def _variant_event_id(row: Any) -> str:
    return _stable_hash(
        {
            "variant_id": row.variant_id,
            "source_event_id": row.event_id,
        }
    )


def _validate_events(events: pd.DataFrame) -> None:
    if events.empty:
        raise VariantError("The cleaned event table contains no rows.")

    missing_columns = sorted(REQUIRED_COLUMNS.difference(events.columns))
    if missing_columns:
        raise VariantError(
            "The cleaned event table is missing required columns: "
            + ", ".join(missing_columns)
            + "."
        )

    required_nonblank = [
        "snapshot_id",
        "event_id",
        "week_start",
        "intake_code",
        "grouping",
        "start_at",
        "end_at",
        "module_id",
    ]
    for column in required_nonblank:
        if events[column].isna().any():
            raise VariantError(f"The cleaned event table contains a blank {column}.")

    if events["event_id"].duplicated().any():
        duplicates = events.loc[
            events["event_id"].duplicated(keep=False), "event_id"
        ].head(5)
        raise VariantError(
            "The cleaned event table contains duplicate event IDs: "
            + ", ".join(duplicates)
            + "."
        )

    if (events["end_at"] <= events["start_at"]).any():
        raise VariantError("The cleaned event table contains an invalid time interval.")


def _known_explicit_groups(
    events: pd.DataFrame,
) -> dict[tuple[str, str], tuple[str, ...]]:
    explicit = events.loc[events["source_grouping"] != "ALL"]
    if explicit.empty:
        return {}

    grouped = explicit.groupby(
        ["snapshot_id", "intake_code"], sort=False, dropna=False
    )["source_grouping"].agg(lambda values: tuple(sorted(set(values))))
    return grouped.to_dict()


def _expand_group_variants(events: pd.DataFrame) -> pd.DataFrame:
    known_groups = _known_explicit_groups(events)
    expanded_parts: list[pd.DataFrame] = []

    for intake_week, intake_week_events in events.groupby(
        INTAKE_WEEK_KEY, sort=False, dropna=False
    ):
        snapshot_id, _, intake_code = intake_week
        common_mask = intake_week_events["source_grouping"] == "ALL"
        has_common_events = bool(common_mask.any())
        present_groups = sorted(
            set(intake_week_events.loc[~common_mask, "source_grouping"])
        )

        if has_common_events:
            target_groups = list(
                known_groups.get((snapshot_id, intake_code), tuple(present_groups))
            )
            if not target_groups:
                target_groups = ["ALL"]
        else:
            target_groups = present_groups or ["ALL"]

        for target_group in target_groups:
            if target_group == "ALL":
                selected = intake_week_events.loc[common_mask].copy()
            else:
                selected = intake_week_events.loc[
                    common_mask
                    | (intake_week_events["source_grouping"] == target_group)
                ].copy()
            if selected.empty:
                continue
            selected["grouping"] = target_group
            selected["is_common_event"] = selected["source_grouping"] == "ALL"
            expanded_parts.append(selected)

    if not expanded_parts:
        raise VariantError("No timetable variants could be constructed.")
    return pd.concat(expanded_parts, ignore_index=True)


def construct_timetable_variants(
    events: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build one event-membership table for every intake, week, and group."""

    _validate_events(events)
    source = events.copy()
    source["source_grouping"] = source["grouping"].astype("string").str.strip()
    if source["source_grouping"].isna().any() or (
        source["source_grouping"] == ""
    ).any():
        raise VariantError("The cleaned event table contains a blank grouping.")
    source.loc[
        source["source_grouping"].str.upper() == "ALL", "source_grouping"
    ] = "ALL"

    variants = _expand_group_variants(source)
    variants["variant_id"] = [_variant_id(row) for row in variants.itertuples()]
    variants["slot_id"] = [_slot_id(row) for row in variants.itertuples()]
    variants["variant_event_id"] = [
        _variant_event_id(row) for row in variants.itertuples()
    ]
    if variants["variant_event_id"].duplicated().any():
        raise VariantError("Variant event IDs are not unique.")

    variants["shared_group_count"] = variants.groupby(
        SLOT_SCOPE_KEY, sort=False, dropna=False
    )["grouping"].transform("nunique").astype("int64")
    variants["is_shared_slot"] = variants["shared_group_count"] > 1

    remaining_columns = [
        column
        for column in events.columns
        if column not in set(IDENTIFIER_COLUMNS)
    ]
    variants = variants[[*IDENTIFIER_COLUMNS, *remaining_columns]]
    variants = variants.sort_values(
        [
            "snapshot_id",
            "week_start",
            "intake_code",
            "grouping",
            "start_at",
            "end_at",
            "module_id",
            "event_id",
        ],
        kind="stable",
    ).reset_index(drop=True)

    variant_keys = variants[VARIANT_KEY].drop_duplicates()
    intake_week_group_counts = variant_keys.groupby(
        INTAKE_WEEK_KEY, sort=False, dropna=False
    )["grouping"].nunique()
    shared_slots = variants.loc[variants["is_shared_slot"], SLOT_SCOPE_KEY]
    variant_slots = variants[["variant_id", "slot_id"]].drop_duplicates()
    multi_record_slot_counts = variants.groupby(
        ["variant_id", "slot_id"], sort=False, dropna=False
    )["event_id"].nunique()

    statistics = {
        "source_event_count": len(events),
        "variant_event_count": len(variants),
        "variant_count": len(variant_keys),
        "intake_week_count": len(intake_week_group_counts),
        "multi_group_intake_week_count": int(
            (intake_week_group_counts > 1).sum()
        ),
        "variant_slot_count": len(variant_slots),
        "shared_slot_count": len(shared_slots.drop_duplicates()),
        "shared_variant_slot_count": len(
            variants.loc[variants["is_shared_slot"], ["variant_id", "slot_id"]]
            .drop_duplicates()
        ),
        "common_source_event_count": int((source["source_grouping"] == "ALL").sum()),
        "common_event_assignment_count": int(variants["is_common_event"].sum()),
        "multi_record_variant_slot_count": int((multi_record_slot_counts > 1).sum()),
    }
    return variants, statistics


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
        raise VariantError(
            "Writing Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise VariantError(f"Cannot write variant Parquet file: {target}.") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_snapshot_variants(
    snapshot_id: str, repository_root: Path
) -> dict[str, Any]:
    """Read Stage 2 events and write the Stage 3 variant event table."""

    snapshot_directory = (
        repository_root / PROCESSED_DIRECTORY_RELATIVE_PATH / snapshot_id
    )
    input_path = snapshot_directory / INPUT_FILENAME
    output_path = snapshot_directory / OUTPUT_FILENAME
    try:
        events = pd.read_parquet(input_path, engine="pyarrow")
    except FileNotFoundError as exc:
        raise VariantError(
            f"Cannot find Stage 2 events for {snapshot_id}: {input_path}."
        ) from exc
    except ImportError as exc:
        raise VariantError(
            "Reading Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise VariantError(f"Cannot read Stage 2 events: {input_path}.") from exc

    snapshot_values = events["snapshot_id"].drop_duplicates().tolist()
    if snapshot_values != [snapshot_id]:
        raise VariantError(
            f"Stage 2 events do not belong only to snapshot {snapshot_id}."
        )

    variants, statistics = construct_timetable_variants(events)
    _write_parquet_atomically(variants, output_path)

    variant_group_counts = (
        variants[["variant_id", "grouping"]]
        .drop_duplicates()["grouping"]
        .value_counts()
        .sort_index()
    )
    return {
        "status": "processed",
        "snapshot_id": snapshot_id,
        **statistics,
        "minimum_week_start": min(variants["week_start"]).isoformat(),
        "maximum_week_start": max(variants["week_start"]).isoformat(),
        "variant_group_counts": {
            str(grouping): int(count)
            for grouping, count in variant_group_counts.items()
        },
        "output_path": output_path.relative_to(repository_root).as_posix(),
        "output_size_bytes": output_path.stat().st_size,
    }


def load_snapshot_index(index_path: Path) -> list[dict[str, Any]]:
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VariantError(f"Cannot find snapshot index: {index_path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise VariantError(f"Cannot read snapshot index: {index_path}.") from exc

    if not isinstance(index, list) or not index:
        raise VariantError("The snapshot index must contain at least one entry.")
    for position, entry in enumerate(index, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("snapshot_id"), str):
            raise VariantError(
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
        raise VariantError(f"Snapshot ID is not in the index: {snapshot_id}.")
    return [snapshot_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build group-specific timetable variants from Stage 2 events."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing data/snapshots and data/processed.",
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
            build_snapshot_variants(snapshot_id, repository_root)
            for snapshot_id in snapshot_ids
        ]
    except VariantError as exc:
        print(f"Stage 3 processing failed: {exc}", file=sys.stderr)
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
