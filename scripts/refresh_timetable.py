"""Run the complete timetable refresh and validation workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
LATEST_EXPORT_RELATIVE_PATH = Path("web/public/data/latest.json")


class RefreshError(RuntimeError):
    """Raised when the refresh cannot safely continue."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Step:
    """One command in the refresh workflow."""

    label: str
    command: tuple[str, ...]
    working_directory: Path


def build_steps(
    repository_root: Path,
    python_executable: str,
    skip_fetch: bool = False,
) -> list[Step]:
    """Build the ordered refresh commands."""

    repository_root = repository_root.resolve()
    npm_executable = "npm.cmd" if os.name == "nt" else "npm"
    steps: list[Step] = []

    if not skip_fetch:
        steps.append(
            Step(
                "Fetch and retain the latest timetable feed",
                (python_executable, "scripts/fetch_timetable.py"),
                repository_root,
            )
        )

    pipeline_scripts = (
        ("Normalize the latest snapshot", "process_snapshots.py"),
        ("Build timetable variants", "build_timetable_variants.py"),
        ("Calculate daily metrics", "calculate_daily_metrics.py"),
        ("Calculate weekly metrics", "calculate_weekly_metrics.py"),
        ("Score and rank timetables", "rank_timetables.py"),
    )
    for label, script_name in pipeline_scripts:
        steps.append(
            Step(
                label,
                (python_executable, f"scripts/{script_name}"),
                repository_root,
            )
        )

    steps.extend(
        [
            Step(
                "Export dashboard JSON",
                (python_executable, "scripts/build_dashboard_data.py"),
                repository_root,
            ),
            Step(
                "Run Python tests",
                (
                    python_executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ),
                repository_root,
            ),
            Step(
                "Run frontend tests",
                (npm_executable, "run", "test"),
                repository_root / "web",
            ),
            Step(
                "Build the production website",
                (npm_executable, "run", "build"),
                repository_root / "web",
            ),
        ]
    )
    return steps


def validate_repository(repository_root: Path) -> None:
    """Confirm that the command is running against this repository."""

    required_paths = (
        repository_root / "scripts/fetch_timetable.py",
        repository_root / "scripts/build_dashboard_data.py",
        repository_root / "web/package.json",
    )
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise RefreshError(
            "Repository root is missing required files: " + ", ".join(missing)
        )


def _run_step(
    step: Step,
    position: int,
    total: int,
    capture_stdout: bool = False,
) -> str | None:
    """Run one workflow step and optionally return its standard output."""

    print(f"\n[{position}/{total}] {step.label}", flush=True)
    print("  " + " ".join(step.command), flush=True)
    try:
        result = subprocess.run(
            step.command,
            cwd=step.working_directory,
            check=False,
            stdout=subprocess.PIPE if capture_stdout else None,
            text=capture_stdout,
        )
    except FileNotFoundError as exc:
        raise RefreshError(
            f"Cannot run {step.label}: command not found: {step.command[0]}"
        ) from exc
    if result.returncode != 0:
        raise RefreshError(
            f"{step.label} failed with exit code {result.returncode}.",
            result.returncode,
        )

    if not capture_stdout:
        return None

    output = result.stdout
    if not isinstance(output, str):
        raise RefreshError(f"{step.label} did not return readable output.")
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    return output


def run_fetch_step(step: Step, total_steps: int) -> bool:
    """Run the fetcher and return whether it retained a changed snapshot."""

    output = _run_step(step, position=1, total=total_steps, capture_stdout=True)
    try:
        result = json.loads(output or "")
    except json.JSONDecodeError as exc:
        raise RefreshError(
            "The timetable fetcher did not return a valid JSON result."
        ) from exc

    if not isinstance(result, dict) or type(result.get("changed")) is not bool:
        raise RefreshError(
            "The timetable fetcher result has no valid boolean 'changed' field."
        )
    return result["changed"]


def run_steps(
    steps: Sequence[Step],
    start_position: int = 1,
    total_steps: int | None = None,
) -> None:
    """Run each workflow step in order and stop on the first failure."""

    total = len(steps) if total_steps is None else total_steps
    for position, step in enumerate(steps, start=start_position):
        _run_step(step, position, total)


def verify_publishable_outputs(repository_root: Path) -> list[Path]:
    """Verify that the retained snapshot and public export refer to the same data."""

    index_path = repository_root / INDEX_RELATIVE_PATH
    latest_export_path = repository_root / LATEST_EXPORT_RELATIVE_PATH
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        latest_export = json.loads(latest_export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshError(f"Cannot verify publishable outputs: {exc}") from exc

    if not isinstance(index, list) or not index or not isinstance(index[-1], dict):
        raise RefreshError("Snapshot index has no valid latest entry.")

    latest_entry = index[-1]
    snapshot_id = latest_entry.get("snapshot_id")
    snapshot_relative_path = latest_entry.get("path")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise RefreshError("The latest snapshot index entry has no snapshot ID.")
    if not isinstance(snapshot_relative_path, str) or not snapshot_relative_path:
        raise RefreshError("The latest snapshot index entry has no snapshot path.")

    exported_snapshot = latest_export.get("snapshot")
    exported_snapshot_id = (
        exported_snapshot.get("snapshot_id")
        if isinstance(exported_snapshot, dict)
        else None
    )
    if exported_snapshot_id != snapshot_id:
        raise RefreshError(
            "Dashboard export does not match the latest retained snapshot: "
            f"expected {snapshot_id}, found {exported_snapshot_id}."
        )

    snapshot_path = repository_root / Path(snapshot_relative_path)
    if not snapshot_path.is_file():
        raise RefreshError(f"Latest retained snapshot is missing: {snapshot_path}")

    return [index_path, snapshot_path, latest_export_path]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch, rebuild, export, test, and validate the complete timetable "
            "dashboard data pipeline."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing the scripts, data, tests, and web folders.",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Use the latest snapshot already in the index without contacting the feed.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository_root = arguments.repository_root.resolve()

    try:
        validate_repository(repository_root)
        steps = build_steps(
            repository_root,
            sys.executable,
            skip_fetch=arguments.skip_fetch,
        )
        total_steps = len(steps)
        if not arguments.skip_fetch:
            if not run_fetch_step(steps[0], total_steps):
                print("\nTimetable feed is unchanged.")
                print(
                    "No downstream processing, tests, or production build were run."
                )
                return 0
            steps = steps[1:]
            run_steps(steps, start_position=2, total_steps=total_steps)
        else:
            run_steps(steps)
        publishable_paths = verify_publishable_outputs(repository_root)
    except RefreshError as exc:
        print(f"\nTimetable refresh failed: {exc}", file=sys.stderr)
        return exc.exit_code

    print("\nTimetable refresh completed successfully.")
    print("Review and commit these publishable files:")
    for path in publishable_paths:
        print(f"  {path.relative_to(repository_root).as_posix()}")
    print("Generated processed data, history exports, and web/dist remain ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
