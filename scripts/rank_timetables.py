"""Rank weekly timetable variants using absolute convenience scores."""

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
    SCORE_METHOD,
    ScoringModel,
    ScoringModelError,
    load_scoring_model,
)


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
SCORING_CONFIG_RELATIVE_PATH = Path("config/scoring.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
INPUT_FILENAME = "intake_week_metrics.parquet"
OUTPUT_FILENAME = "default_rankings.parquet"

COMPARISON_KEY = ["snapshot_id", "week_start"]
WEEKLY_KEY = [
    *COMPARISON_KEY,
    "intake_code",
    "grouping",
    "elective_profile",
]
COMPONENT_SCORE_COLUMNS = [
    "campus_trip_score",
    "online_commitment_score",
    "placement_score",
    "span_score",
    "waiting_score",
    "short_day_score",
    "long_day_score",
]


class RankingError(RuntimeError):
    """Raised when timetable rankings cannot be calculated safely."""


def _validate_weekly_metrics(weekly: pd.DataFrame) -> None:
    if weekly.empty:
        raise RankingError("The weekly metric table contains no rows.")

    required = {
        "variant_id",
        *WEEKLY_KEY,
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        "balanced_score",
        *COMPONENT_SCORE_COLUMNS,
    }
    missing = sorted(required.difference(weekly.columns))
    if missing:
        raise RankingError(
            "The weekly metric table is missing required columns: "
            + ", ".join(missing)
            + "."
        )
    for column in required:
        if weekly[column].isna().any():
            raise RankingError(f"The weekly metric table contains a blank {column}.")
    if weekly.duplicated(WEEKLY_KEY).any():
        raise RankingError("The weekly metric table has duplicate comparison rows.")
    if weekly["variant_id"].duplicated().any():
        raise RankingError("The weekly metric table has duplicate variant IDs.")

    score_columns = ["balanced_score", *COMPONENT_SCORE_COLUMNS]
    if (weekly[score_columns] < 0).any().any():
        raise RankingError("A weekly score contains a negative value.")
    if (weekly["balanced_score"] > 100.000001).any():
        raise RankingError("A weekly convenience score exceeds 100.")
    component_total = weekly[COMPONENT_SCORE_COLUMNS].sum(axis=1)
    if not (component_total - weekly["balanced_score"]).abs().le(0.00001).all():
        raise RankingError("Weekly component scores do not match the total score.")


def _score_comparison_set(
    comparison: pd.DataFrame, scoring_model: ScoringModel
) -> pd.DataFrame:
    comparison_values = comparison[COMPARISON_KEY].drop_duplicates()
    if len(comparison_values) != 1:
        raise RankingError(
            "A ranking comparison set must contain exactly one snapshot and week."
        )

    scored = comparison.copy()
    scored["overall_score"] = scored["balanced_score"].astype("float64")
    median_score = float(scored["overall_score"].median())
    scored["comparison_set_size"] = len(scored)
    scored["comparison_median_score"] = round(median_score, 6)
    scored["distance_from_median"] = (
        scored["overall_score"] - median_score
    ).abs().round(6)
    scored["best_rank"] = (
        scored["overall_score"].rank(method="min", ascending=True).astype("int64")
    )
    scored["worst_rank"] = (
        scored["overall_score"].rank(method="min", ascending=False).astype("int64")
    )
    best_score = scored["overall_score"].min()
    worst_score = scored["overall_score"].max()
    average_distance = scored["distance_from_median"].min()
    scored["is_best"] = scored["overall_score"] == best_score
    scored["is_worst"] = scored["overall_score"] == worst_score
    scored["is_most_average"] = (
        scored["distance_from_median"] == average_distance
    )
    scored["scoring_profile"] = (
        scoring_model.model_version
        + ":"
        + scoring_model.default_time_preference
    )
    scored["scoring_profile_id"] = scoring_model.profile_id
    scored["score_method"] = SCORE_METHOD
    return scored


def rank_weekly_metrics(
    weekly_metrics: pd.DataFrame, scoring_model: ScoringModel
) -> pd.DataFrame:
    """Rank each comparison week without changing its absolute scores."""

    _validate_weekly_metrics(weekly_metrics)
    ranked_parts = [
        _score_comparison_set(comparison, scoring_model)
        for _, comparison in weekly_metrics.groupby(
            COMPARISON_KEY, sort=False, dropna=False
        )
    ]
    ranked = pd.concat(ranked_parts, ignore_index=True)
    for column in ("scoring_profile", "scoring_profile_id", "score_method"):
        ranked[column] = ranked[column].astype("string")
    return ranked.sort_values(
        [
            "snapshot_id",
            "week_start",
            "best_rank",
            "intake_code",
            "grouping",
            "elective_profile",
        ],
        kind="stable",
    ).reset_index(drop=True)


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
        raise RankingError(
            "Writing Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise RankingError(f"Cannot write timetable rankings: {target}.") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _ranking_example(row: pd.Series) -> dict[str, Any]:
    return {
        "intake_code": str(row["intake_code"]),
        "grouping": str(row["grouping"]),
        "elective_profile_name": str(row["elective_profile_name"]),
        "elective_status": str(row["elective_status"]),
        "overall_score": float(row["overall_score"]),
        "physical_days": int(row["physical_days"]),
        "total_physical_span_minutes": int(
            row["total_physical_span_minutes"]
        ),
        "total_campus_waiting_minutes": int(
            row["total_campus_waiting_minutes"]
        ),
    }


def rank_snapshot(
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
        weekly_metrics = pd.read_parquet(input_path, engine="pyarrow")
    except FileNotFoundError as exc:
        raise RankingError(
            f"Cannot find Stage 5 weekly metrics for {snapshot_id}: {input_path}."
        ) from exc
    except ImportError as exc:
        raise RankingError(
            "Reading Parquet requires pyarrow. Install requirements.txt first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise RankingError(f"Cannot read Stage 5 metrics: {input_path}.") from exc

    snapshot_values = weekly_metrics["snapshot_id"].drop_duplicates().tolist()
    if snapshot_values != [snapshot_id]:
        raise RankingError(
            f"Stage 5 metrics do not belong only to snapshot {snapshot_id}."
        )

    ranked = rank_weekly_metrics(weekly_metrics, scoring_model)
    _write_parquet_atomically(ranked, output_path)

    comparison_summaries = []
    for (_, week_start), comparison in ranked.groupby(
        COMPARISON_KEY, sort=True, dropna=False
    ):
        best = comparison.loc[comparison["is_best"]].sort_values(
            ["intake_code", "grouping", "elective_profile"], kind="stable"
        )
        worst = comparison.loc[comparison["is_worst"]].sort_values(
            ["intake_code", "grouping", "elective_profile"], kind="stable"
        )
        average = comparison.loc[comparison["is_most_average"]].sort_values(
            ["intake_code", "grouping", "elective_profile"], kind="stable"
        )
        comparison_summaries.append(
            {
                "week_start": week_start.isoformat(),
                "peer_count": len(comparison),
                "best_count": len(best),
                "best_example": _ranking_example(best.iloc[0]),
                "worst_count": len(worst),
                "worst_example": _ranking_example(worst.iloc[0]),
                "most_average_count": len(average),
                "most_average_example": _ranking_example(average.iloc[0]),
            }
        )

    return {
        "status": "processed",
        "snapshot_id": snapshot_id,
        "ranked_record_count": len(ranked),
        "comparison_set_count": len(comparison_summaries),
        "scoring_profile": (
            scoring_model.model_version
            + ":"
            + scoring_model.default_time_preference
        ),
        "scoring_profile_id": scoring_model.profile_id,
        "score_method": SCORE_METHOD,
        "comparison_sets": comparison_summaries,
        "output_path": output_path.relative_to(repository_root).as_posix(),
        "output_size_bytes": output_path.stat().st_size,
    }


def load_snapshot_index(index_path: Path) -> list[dict[str, Any]]:
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RankingError(f"Cannot find snapshot index: {index_path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RankingError(f"Cannot read snapshot index: {index_path}.") from exc
    if not isinstance(index, list) or not index:
        raise RankingError("The snapshot index must contain at least one entry.")
    for position, entry in enumerate(index, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("snapshot_id"), str):
            raise RankingError(
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
        raise RankingError(f"Snapshot ID is not in the index: {snapshot_id}.")
    return [snapshot_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank Stage 5 weekly timetables by absolute convenience score."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing scoring config and processed data.",
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
            rank_snapshot(snapshot_id, repository_root, scoring_model)
            for snapshot_id in snapshot_ids
        ]
    except (RankingError, ScoringModelError) as exc:
        print(f"Stage 6 processing failed: {exc}", file=sys.stderr)
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
