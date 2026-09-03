from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.refresh_timetable import (
    RefreshError,
    Step,
    build_steps,
    run_steps,
    verify_publishable_outputs,
)


class BuildStepsTests(unittest.TestCase):
    def test_builds_complete_workflow_in_required_order(self) -> None:
        root = Path("repository")

        steps = build_steps(root, "python-test")

        self.assertEqual(
            [step.command[1] for step in steps[:7]],
            [
                "scripts/fetch_timetable.py",
                "scripts/process_snapshots.py",
                "scripts/build_timetable_variants.py",
                "scripts/calculate_daily_metrics.py",
                "scripts/calculate_weekly_metrics.py",
                "scripts/rank_timetables.py",
                "scripts/build_dashboard_data.py",
            ],
        )
        for step in steps[1:6]:
            self.assertEqual(len(step.command), 2)
            self.assertNotIn("--all", step.command)
        self.assertEqual(steps[7].command[1:4], ("-m", "unittest", "discover"))
        self.assertEqual(steps[-2].command[1:], ("run", "test"))
        self.assertEqual(steps[-1].command[1:], ("run", "build"))

    def test_skip_fetch_starts_with_snapshot_processing(self) -> None:
        steps = build_steps(Path("repository"), "python-test", skip_fetch=True)

        self.assertEqual(steps[0].command[1], "scripts/process_snapshots.py")
        self.assertNotIn(
            "scripts/fetch_timetable.py", [part for step in steps for part in step.command]
        )


class RunStepsTests(unittest.TestCase):
    @patch("scripts.refresh_timetable.subprocess.run")
    def test_stops_after_first_failed_step(self, run) -> None:
        run.return_value.returncode = 7
        steps = [
            Step("First", ("first",), Path("one")),
            Step("Second", ("second",), Path("two")),
        ]

        with self.assertRaisesRegex(RefreshError, "First failed") as context:
            run_steps(steps)

        self.assertEqual(context.exception.exit_code, 7)
        run.assert_called_once()


class VerifyPublishableOutputsTests(unittest.TestCase):
    def test_accepts_matching_index_snapshot_and_dashboard_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "data/snapshots/raw/example.json.gz"
            snapshot_path.parent.mkdir(parents=True)
            snapshot_path.write_bytes(b"snapshot")
            index_path = root / "data/snapshots/index.json"
            index_path.write_text(
                json.dumps(
                    [
                        {
                            "snapshot_id": "snapshot-one",
                            "path": "data/snapshots/raw/example.json.gz",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            export_path = root / "web/public/data/latest.json"
            export_path.parent.mkdir(parents=True)
            export_path.write_text(
                json.dumps({"snapshot": {"snapshot_id": "snapshot-one"}}),
                encoding="utf-8",
            )

            outputs = verify_publishable_outputs(root)

        self.assertEqual(
            [path.relative_to(root).as_posix() for path in outputs],
            [
                "data/snapshots/index.json",
                "data/snapshots/raw/example.json.gz",
                "web/public/data/latest.json",
            ],
        )

    def test_rejects_dashboard_export_for_an_older_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "data/snapshots/index.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(
                json.dumps(
                    [
                        {
                            "snapshot_id": "snapshot-new",
                            "path": "data/snapshots/raw/new.json.gz",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            export_path = root / "web/public/data/latest.json"
            export_path.parent.mkdir(parents=True)
            export_path.write_text(
                json.dumps({"snapshot": {"snapshot_id": "snapshot-old"}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RefreshError, "does not match"):
                verify_publishable_outputs(root)


if __name__ == "__main__":
    unittest.main()
