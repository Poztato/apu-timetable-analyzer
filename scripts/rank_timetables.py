"""Score and rank weekly timetable variants within each comparison week."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
RANKING_CONFIG_RELATIVE_PATH = Path("config/ranking.json")
PROCESSED_DIRECTORY_RELATIVE_PATH = Path("data/processed")
INPUT_FILENAME = "intake_week_metrics.parquet"
OUTPUT_FILENAME = "default_rankings.parquet"
PERCENTILE_METHOD = "strict_lower_peer_v1"

COMPARISON_KEY = ["snapshot_id", "week_start"]
WEEKLY_KEY = [
    *COMPARISON_KEY,
    "intake_code",
    "grouping",
    "elective_profile",
]

CRITERION_COLUMNS = {
    "gap_burden": "total_gap_minutes",
    "late_only": "late_only_days",
    "early_only": "early_only_days",
    "one_hour_only": "one_hour_only_days",
    "overloaded": "overloaded_days",
}


class RankingError(RuntimeError):
    """Raised when timetable rankings cannot be calculated safely."""


@dataclass(frozen=True)
class RankingProfile:
    criterion_order: tuple[str, ...]
    position_weights: tuple[float, ...]

    @property
    def normalized_weights(self) -> dict[str, float]:
        total = sum(self.position_weights)
        return {
            criterion: weight / total
            for criterion, weight in zip(
                self.criterion_order, self.position_weights
            )
        }

    @property
    def description(self) -> str:
        return ">".join(self.criterion_order)

    @property
    def profile_id(self) -> str:
        value = {
            "criterion_order": self.criterion_order,
            "position_weights": self.position_weights,
            "percentile_method": PERCENTILE_METHOD,
        }
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _validate_criterion_order(criteria: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in criteria)
    if len(normalized) != len(CRITERION_COLUMNS):
        raise RankingError(
            f"Criterion order must contain exactly {len(CRITERION_COLUMNS)} items."
        )
    if len(set(normalized)) != len(normalized):
        raise RankingError("Criterion order cannot contain duplicates.")
    unknown = sorted(set(normalized).difference(CRITERION_COLUMNS))
    missing = sorted(set(CRITERION_COLUMNS).difference(normalized))
    if unknown or missing:
        details = []
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        if missing:
            details.append("missing: " + ", ".join(missing))
        raise RankingError("Invalid criterion order, " + "; ".join(details) + ".")
    return normalized


def _validate_position_weights(weights: Sequence[Any]) -> tuple[float, ...]:
    if len(weights) != len(CRITERION_COLUMNS):
        raise RankingError(
            f"Position weights must contain exactly {len(CRITERION_COLUMNS)} values."
        )
    normalized: list[float] = []
    for value in weights:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RankingError("Every position weight must be numeric.")
        numeric = float(value)
        if numeric <= 0:
            raise RankingError("Every position weight must be positive.")
        normalized.append(numeric)
    return tuple(normalized)


def parse_ranking_config(config: Mapping[str, Any]) -> RankingProfile:
    if "default_criterion_order" not in config or "position_weights" not in config:
        raise RankingError(
            "Ranking config requires default_criterion_order and position_weights."
        )
    order = config["default_criterion_order"]
    weights = config["position_weights"]
    if not isinstance(order, list) or not isinstance(weights, list):
        raise RankingError("Ranking order and weights must be JSON arrays.")
    return RankingProfile(
        criterion_order=_validate_criterion_order(order),
        position_weights=_validate_position_weights(weights),
    )


def load_ranking_config(path: Path) -> RankingProfile:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RankingError(f"Cannot find ranking config: {path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RankingError(f"Cannot read ranking config: {path}.") from exc
    if not isinstance(config, dict):
        raise RankingError("The ranking config must contain a JSON object.")
    return parse_ranking_config(config)


def profile_with_order(
    base_profile: RankingProfile, criterion_order: Sequence[str]
) -> RankingProfile:
    return RankingProfile(
        criterion_order=_validate_criterion_order(criterion_order),
        position_weights=base_profile.position_weights,
    )


def _validate_weekly_metrics(weekly: pd.DataFrame) -> None:
    if weekly.empty:
        raise RankingError("The weekly metric table contains no rows.")

    required = {
        "variant_id",
        *WEEKLY_KEY,
        "elective_profile_name",
        "elective_status",
        "elective_rule_id",
        *CRITERION_COLUMNS.values(),
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

    metric_columns = list(CRITERION_COLUMNS.values())
    if (weekly[metric_columns] < 0).any().any():
        raise RankingError("A frustration criterion contains a negative value.")


def _higher_is_worse_percentile(values: pd.Series) -> pd.Series:
    """Return the percentage of peers with a strictly lower raw value."""

    numeric = pd.to_numeric(values, errors="raise").astype("float64")
    if len(numeric) <= 1 or numeric.nunique(dropna=False) <= 1:
        return pd.Series(0.0, index=values.index, dtype="float64")
    ranks = numeric.rank(method="min", ascending=True)
    return ((ranks - 1.0) / (len(numeric) - 1.0) * 100.0).round(10)


def _score_comparison_set(
    comparison: pd.DataFrame, profile: RankingProfile
) -> pd.DataFrame:
    comparison_values = comparison[COMPARISON_KEY].drop_duplicates()
    if len(comparison_values) != 1:
        raise RankingError(
            "A scoring comparison set must contain exactly one snapshot and week."
        )

    scored = comparison.copy()
    weights = profile.normalized_weights
    contribution_columns: list[str] = []
    for criterion in profile.criterion_order:
        raw_column = CRITERION_COLUMNS[criterion]
        percentile_column = f"{criterion}_percentile"
        weight_column = f"{criterion}_weight"
        contribution_column = f"{criterion}_contribution"
        scored[percentile_column] = _higher_is_worse_percentile(
            scored[raw_column]
        )
        scored[weight_column] = weights[criterion]
        scored[contribution_column] = (
            scored[percentile_column] * weights[criterion]
        ).round(10)
        contribution_columns.append(contribution_column)

    scored["overall_frustration"] = (
        scored[contribution_columns].sum(axis=1).round(10)
    )
    median_score = float(scored["overall_frustration"].median())
    scored["comparison_set_size"] = len(scored)
    scored["comparison_median_score"] = round(median_score, 10)
    scored["distance_from_median"] = (
        scored["overall_frustration"] - median_score
    ).abs().round(10)
    scored["best_rank"] = (
        scored["overall_frustration"].rank(method="min", ascending=True).astype("int64")
    )
    scored["worst_rank"] = (
        scored["overall_frustration"]
        .rank(method="min", ascending=False)
        .astype("int64")
    )
    best_score = scored["overall_frustration"].min()
    worst_score = scored["overall_frustration"].max()
    average_distance = scored["distance_from_median"].min()
    scored["is_best"] = scored["overall_frustration"] == best_score
    scored["is_worst"] = scored["overall_frustration"] == worst_score
    scored["is_most_average"] = (
        scored["distance_from_median"] == average_distance
    )
    scored["scoring_profile"] = profile.description
    scored["scoring_profile_id"] = profile.profile_id
    scored["percentile_method"] = PERCENTILE_METHOD
    return scored


def rank_weekly_metrics(
    weekly_metrics: pd.DataFrame, profile: RankingProfile
) -> pd.DataFrame:
    """Rank every snapshot-week comparison set using the selected profile."""

    _validate_criterion_order(profile.criterion_order)
    _validate_position_weights(profile.position_weights)
    _validate_weekly_metrics(weekly_metrics)

    ranked_parts = [
        _score_comparison_set(comparison, profile)
        for _, comparison in weekly_metrics.groupby(
            COMPARISON_KEY, sort=False, dropna=False
        )
    ]
    ranked = pd.concat(ranked_parts, ignore_index=True)
    for column in ("scoring_profile", "scoring_profile_id", "percentile_method"):
        ranked[column] = ranked[column].astype("string")
    ranked = ranked.sort_values(
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
    return ranked


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
        "overall_frustration": float(row["overall_frustration"]),
        "total_gap_minutes": int(row["total_gap_minutes"]),
        "late_only_days": int(row["late_only_days"]),
        "early_only_days": int(row["early_only_days"]),
        "one_hour_only_days": int(row["one_hour_only_days"]),
        "overloaded_days": int(row["overloaded_days"]),
    }


def rank_snapshot(
    snapshot_id: str,
    repository_root: Path,
    profile: RankingProfile,
    output_filename: str = OUTPUT_FILENAME,
) -> dict[str, Any]:
    snapshot_directory = (
        repository_root / PROCESSED_DIRECTORY_RELATIVE_PATH / snapshot_id
    )
    input_path = snapshot_directory / INPUT_FILENAME
    output_path = snapshot_directory / output_filename
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

    ranked = rank_weekly_metrics(weekly_metrics, profile)
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
        "scoring_profile": profile.description,
        "scoring_profile_id": profile.profile_id,
        "percentile_method": PERCENTILE_METHOD,
        "normalized_weights": profile.normalized_weights,
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
        description="Rank Stage 5 weekly timetable metrics within each week."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing ranking config and processed data.",
    )
    parser.add_argument(
        "--criteria",
        help=(
            "Comma-separated criterion order. Available values: "
            + ", ".join(CRITERION_COLUMNS)
            + "."
        ),
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
        profile = load_ranking_config(
            repository_root / RANKING_CONFIG_RELATIVE_PATH
        )
        if arguments.criteria:
            profile = profile_with_order(
                profile, arguments.criteria.split(",")
            )
            output_filename = f"rankings_{profile.profile_id}.parquet"
        else:
            output_filename = OUTPUT_FILENAME
        index = load_snapshot_index(repository_root / INDEX_RELATIVE_PATH)
        snapshot_ids = _select_snapshot_ids(
            index, arguments.snapshot_id, arguments.all
        )
        summaries = [
            rank_snapshot(
                snapshot_id, repository_root, profile, output_filename
            )
            for snapshot_id in snapshot_ids
        ]
    except RankingError as exc:
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
