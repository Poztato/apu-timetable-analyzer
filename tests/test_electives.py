from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from scripts.electives import (
    load_elective_config,
    normalize_module_name,
    resolve_elective_profiles,
    validate_elective_config,
)


def rule_config(*, minimum_year_month: int = 202607) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": {"edition": "Test", "sha256": "abc"},
        "programme_level": "degree",
        "applicability": {
            "minimum_intake_year_month": minimum_year_month,
            "verified_legacy_intakes": [],
        },
        "covered_programmes": ["SE"],
        "known_issues": [],
        "rules": [
            {
                "id": "se-level-3-test",
                "programme_keys": ["SE"],
                "academic_level": 3,
                "brochure_page": 25,
                "heading_choose": 1,
                "groups": [
                    {
                        "id": "se-l3-g1",
                        "choose": 1,
                        "options": [
                            {
                                "id": "distributed",
                                "name": "Distributed Computer Systems",
                            },
                            {
                                "id": "enterprise",
                                "name": "Enterprise Programming for Distributed Applications",
                                "aliases": [
                                    "Enterprise Programming for Distributed Application"
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }


def events_for(*module_names: str, intake_month: int = 7) -> pd.DataFrame:
    return pd.DataFrame.from_records(
        [
            {
                "snapshot_id": "snapshot-one",
                "intake_code": f"APD3F26{intake_month:02d}SE",
                "programme_level": "degree",
                "course_code": "SE",
                "specialism_code": pd.NA,
                "academic_level": 3,
                "intake_year": 2026,
                "intake_month": intake_month,
                "module_name": module_name,
                "event_id": f"event-{position}",
            }
            for position, module_name in enumerate(module_names, start=1)
        ]
    )


class ElectiveConfigTests(unittest.TestCase):
    def test_repository_config_is_valid(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]

        config = load_elective_config(
            repository_root / "config" / "elective_rules.json"
        )

        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(len(config["rules"]), 15)

    def test_normalizes_ampersands_case_and_hyphens(self) -> None:
        self.assertEqual(
            normalize_module_name("Cloud Infrastructure & Services"),
            normalize_module_name("cloud infrastructure and services"),
        )
        self.assertEqual(
            normalize_module_name("Object-Oriented Programming"),
            normalize_module_name("object oriented programming"),
        )

    def test_validates_small_rule_config(self) -> None:
        validate_elective_config(rule_config())


class ElectiveResolutionTests(unittest.TestCase):
    def test_splits_active_choose_one_options_and_keeps_core(self) -> None:
        events = events_for(
            "Project Management",
            "Distributed Computer Systems",
            "Enterprise Programming for Distributed Application",
        )

        variants, report = resolve_elective_profiles(events, rule_config())

        self.assertEqual(variants["elective_profile"].nunique(), 2)
        self.assertEqual(report["status_counts"], {"resolved": 1})
        for _, profile in variants.groupby("elective_profile"):
            self.assertIn("Project Management", set(profile["module_name"]))
            selected = profile.loc[profile["is_elective"], "module_name"]
            self.assertEqual(len(selected), 1)

    def test_marks_older_unverified_intake_without_dropping_events(self) -> None:
        events = events_for(
            "Distributed Computer Systems",
            "Enterprise Programming for Distributed Application",
            intake_month=5,
        )

        variants, report = resolve_elective_profiles(events, rule_config())

        self.assertEqual(len(variants), len(events))
        self.assertEqual(set(variants["elective_status"]), {"source_version_unverified"})
        self.assertFalse(variants["is_elective"].any())
        self.assertEqual(
            report["intakes"][0]["issues"][0]["code"],
            "source-version-unverified",
        )

    def test_verified_legacy_intake_can_use_rule(self) -> None:
        config = rule_config()
        config["applicability"]["verified_legacy_intakes"] = ["APD3F2605SE"]
        events = events_for(
            "Distributed Computer Systems",
            "Enterprise Programming for Distributed Application",
            intake_month=5,
        )

        variants, report = resolve_elective_profiles(events, config)

        self.assertEqual(variants["elective_profile"].nunique(), 2)
        self.assertEqual(report["status_counts"], {"resolved": 1})

    def test_expands_choose_two_rule_into_valid_combinations(self) -> None:
        config = rule_config()
        rule = config["rules"][0]
        rule["heading_choose"] = 2
        group = rule["groups"][0]
        group["choose"] = 2
        group["options"].append(
            {
                "id": "blockchain",
                "name": "Blockchain Development",
            }
        )
        events = events_for(
            "Project Management",
            "Distributed Computer Systems",
            "Enterprise Programming for Distributed Application",
            "Blockchain Development",
        )

        variants, report = resolve_elective_profiles(events, config)

        self.assertEqual(variants["elective_profile"].nunique(), 3)
        self.assertEqual(report["status_counts"], {"resolved": 1})
        for _, profile in variants.groupby("elective_profile"):
            self.assertIn("Project Management", set(profile["module_name"]))
            self.assertEqual(int(profile["is_elective"].sum()), 2)

    def test_reports_programmes_outside_the_brochure(self) -> None:
        events = events_for("Digital Transformation")
        events["intake_code"] = "APD3F2607IT(DT)"
        events["course_code"] = "IT"
        events["specialism_code"] = "DT"

        variants, report = resolve_elective_profiles(events, rule_config())

        self.assertEqual(set(variants["elective_status"]), {"programme_uncovered"})
        self.assertEqual(report["uncovered_programme_counts"], {"IT(DT)": 1})
