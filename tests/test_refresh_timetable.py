from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.refresh_timetable import (
    RefreshError,
    Step,
    build_steps,
    main,
    run_fetch_step,
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

    @patch("scripts.refresh_timetable.subprocess.run")
    def test_fetch_step_returns_changed_flag_from_json_output(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "status": "unchanged",
                "changed": False,
                "snapshot_id": "snapshot-one",
            }
        )
        step = Step("Fetch", ("python-test", "fetch.py"), Path("repository"))

        with redirect_stdout(StringIO()):
            changed = run_fetch_step(step, total_steps=10)

        self.assertFalse(changed)
        self.assertEqual(run.call_args.kwargs["stdout"], -1)
        self.assertTrue(run.call_args.kwargs["text"])

    @patch("scripts.refresh_timetable.subprocess.run")
    def test_fetch_step_rejects_missing_changed_flag(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"status": "unchanged"})
        step = Step("Fetch", ("python-test", "fetch.py"), Path("repository"))

        with redirect_stdout(StringIO()):
            with self.assertRaisesRegex(RefreshError, "boolean 'changed'"):
                run_fetch_step(step, total_steps=10)


class MainTests(unittest.TestCase):
    @patch("scripts.refresh_timetable.verify_publishable_outputs")
    @patch("scripts.refresh_timetable.run_steps")
    @patch("scripts.refresh_timetable.run_fetch_step", return_value=False)
    @patch("scripts.refresh_timetable.validate_repository")
    def test_unchanged_fetch_skips_the_downstream_pipeline(
        self,
        validate_repository,
        run_fetch,
        run_pipeline,
        verify_outputs,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--repository-root", directory])

        self.assertEqual(exit_code, 0)
        validate_repository.assert_called_once()
        run_fetch.assert_called_once()
        run_pipeline.assert_not_called()
        verify_outputs.assert_not_called()
        self.assertIn("Timetable feed is unchanged", output.getvalue())
        self.assertIn("No downstream processing", output.getvalue())


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
