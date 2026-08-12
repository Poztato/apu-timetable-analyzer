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
        "schema_version": 2,
        "sources": [
            {
                "id": "test-source",
                "title": "Test brochure",
                "edition": "Test",
                "local_path": "test.pdf",
                "url": "https://example.test/test.pdf",
                "sha256": "abc",
                "extraction_method": "Embedded text",
                "applicability": {
                    "minimum_intake_year_month": minimum_year_month,
                    "verified_legacy_intakes": [],
                },
            }
        ],
        "covered_programmes": [
            {
                "programme_level": "degree",
                "programme_keys": ["SE"],
                "source_id": "test-source",
                "status": "complete",
            }
        ],
        "known_issues": [],
        "rules": [
            {
                "id": "se-level-3-test",
                "source_id": "test-source",
                "programme_level": "degree",
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

        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(len(config["sources"]), 12)
        self.assertEqual(len(config["rules"]), 46)

    def test_repository_config_splits_ucff2511ct_electives(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        config = load_elective_config(
            repository_root / "config" / "elective_rules.json"
        )
        module_names = [
            "Academic Research Skills",
            "Perspectives In Technology",
            "Discovering Media In The Digital Age",
            "Psychology And Behavioral Science",
            "Fundamentals Of Hospitality And Tourism",
        ]
        events = pd.DataFrame.from_records(
            [
                {
                    "snapshot_id": "snapshot-one",
                    "intake_code": "UCFF2511CT",
                    "programme_level": "foundation",
                    "course_code": "CT",
                    "specialism_code": pd.NA,
                    "academic_level": pd.NA,
                    "intake_year": 2025,
                    "intake_month": 11,
                    "module_name": module_name,
                }
                for module_name in module_names
            ]
        )

        variants, report = resolve_elective_profiles(events, config)

        self.assertEqual(report["status_counts"], {"resolved": 1})
        self.assertEqual(variants["elective_profile"].nunique(), 4)
        for _, profile in variants.groupby("elective_profile"):
            self.assertIn("Academic Research Skills", set(profile["module_name"]))
            self.assertEqual(int(profile["is_elective"].sum()), 1)

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
        config["sources"][0]["applicability"]["verified_legacy_intakes"] = [
            "APD3F2605SE"
        ]
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
        self.assertEqual(
            report["uncovered_programme_counts"], {"degree/IT(DT)": 1}
        )

    def test_keeps_every_module_in_a_selected_pathway_package(self) -> None:
        config = rule_config(minimum_year_month=202601)
        rule = config["rules"][0]
        rule["heading_choose"] = 1
        rule["groups"] = [
            {
                "id": "pathway-group",
                "choose": 1,
                "options": [
                    {
                        "id": "analytics",
                        "name": "Analytics pathway",
                        "modules": [
                            {"name": "Shared Analytics"},
                            {"name": "Forecasting"},
                        ],
                    },
                    {
                        "id": "cloud",
                        "name": "Cloud pathway",
                        "modules": [
                            {"name": "Shared Analytics"},
                            {"name": "Cloud Platforms"},
                        ],
                    },
                ],
            }
        ]
        events = events_for(
            "Project Management",
            "Shared Analytics",
            "Forecasting",
            "Cloud Platforms",
        )

        variants, report = resolve_elective_profiles(events, config)

        self.assertEqual(variants["elective_profile"].nunique(), 2)
        self.assertEqual(report["status_counts"], {"resolved": 1})
        module_sets = {
            frozenset(profile["module_name"])
            for _, profile in variants.groupby("elective_profile")
        }
        self.assertEqual(
            module_sets,
            {
                frozenset(
                    ["Project Management", "Shared Analytics", "Forecasting"]
                ),
                frozenset(
                    ["Project Management", "Shared Analytics", "Cloud Platforms"]
                ),
            },
        )

    def test_collapses_pathways_with_identical_active_timetables(self) -> None:
        config = rule_config(minimum_year_month=202601)
        rule = config["rules"][0]
        rule["groups"] = [
            {
                "id": "shared-pathway-group",
                "choose": 1,
                "options": [
                    {
                        "id": "analytics",
                        "name": "Analytics pathway",
                        "modules": [{"name": "Shared Module"}],
                    },
                    {
                        "id": "cloud",
                        "name": "Cloud pathway",
                        "modules": [{"name": "Shared Module"}],
                    },
                ],
            }
        ]
        events = events_for("Project Management", "Shared Module")

        variants, report = resolve_elective_profiles(events, config)

        self.assertEqual(variants["elective_profile"].nunique(), 1)
        self.assertEqual(report["status_counts"], {"fixed": 1})
        self.assertEqual(report["intakes"][0]["candidate_profile_count"], 2)
        self.assertIn(
            "Analytics pathway or Cloud pathway",
            set(variants["elective_profile_name"]),
        )
