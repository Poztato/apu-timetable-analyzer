"""Resolve brochure-backed elective choices into student timetable profiles."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


PROFILE_COLUMNS = [
    "elective_profile",
    "elective_profile_name",
    "elective_status",
    "elective_rule_id",
    "is_elective",
    "elective_group_id",
    "elective_option_id",
]

REQUIRED_EVENT_COLUMNS = {
    "snapshot_id",
    "intake_code",
    "programme_level",
    "course_code",
    "specialism_code",
    "academic_level",
    "intake_year",
    "intake_month",
    "module_name",
}


class ElectiveRuleError(RuntimeError):
    """Raised when elective rules or event matching are unsafe."""


def normalize_module_name(value: str) -> str:
    """Return a conservative comparison key for a brochure or feed module name."""

    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
        .replace("&", " and ")
    )
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value))


def programme_key(course_code: Any, specialism_code: Any) -> str | None:
    if pd.isna(course_code):
        return None
    course = str(course_code).strip().upper()
    if not course:
        return None
    if pd.isna(specialism_code) or not str(specialism_code).strip():
        return course
    return f"{course}({str(specialism_code).strip().upper()})"


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ElectiveRuleError(f"{label} must be a non-blank string.")
    return value.strip()


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ElectiveRuleError(f"{label} must be a positive integer.")
    return value


def _validate_year_month(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ElectiveRuleError(
            "applicability.minimum_intake_year_month must be an integer."
        )
    year, month = divmod(value, 100)
    if year < 2000 or not 1 <= month <= 12:
        raise ElectiveRuleError(
            "applicability.minimum_intake_year_month is not a valid YYYYMM value."
        )
    return value


def validate_elective_config(config: Mapping[str, Any]) -> None:
    """Validate the compact, versioned elective-rule configuration."""

    if config.get("schema_version") != 1:
        raise ElectiveRuleError("Elective config schema_version must be 1.")

    source = config.get("source")
    if not isinstance(source, Mapping):
        raise ElectiveRuleError("Elective config source must be an object.")
    _required_string(source.get("edition"), "source.edition")
    _required_string(source.get("sha256"), "source.sha256")
    _required_string(config.get("programme_level"), "programme_level")

    applicability = config.get("applicability")
    if not isinstance(applicability, Mapping):
        raise ElectiveRuleError("Elective config applicability must be an object.")
    _validate_year_month(applicability.get("minimum_intake_year_month"))
    verified = applicability.get("verified_legacy_intakes")
    if not isinstance(verified, list) or any(
        not isinstance(value, str) or not value.strip() for value in verified
    ):
        raise ElectiveRuleError(
            "applicability.verified_legacy_intakes must be an array of intake codes."
        )
    if len(set(verified)) != len(verified):
        raise ElectiveRuleError("Verified legacy intake codes cannot be duplicated.")

    covered = config.get("covered_programmes")
    if not isinstance(covered, list) or not covered:
        raise ElectiveRuleError("covered_programmes must be a non-empty array.")
    covered_values = {
        _required_string(value, "covered programme") for value in covered
    }
    if len(covered_values) != len(covered):
        raise ElectiveRuleError("covered_programmes cannot contain duplicates.")

    issues = config.get("known_issues", [])
    if not isinstance(issues, list):
        raise ElectiveRuleError("known_issues must be an array.")
    issue_codes: set[str] = set()
    for position, issue in enumerate(issues, start=1):
        if not isinstance(issue, Mapping):
            raise ElectiveRuleError(f"Known issue {position} must be an object.")
        code = _required_string(issue.get("code"), f"known issue {position} code")
        if code in issue_codes:
            raise ElectiveRuleError(f"Duplicate known issue code: {code}.")
        issue_codes.add(code)
        pattern = _required_string(
            issue.get("intake_pattern"), f"known issue {code} intake_pattern"
        )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ElectiveRuleError(
                f"Known issue {code} has an invalid intake pattern."
            ) from exc
        _required_string(issue.get("message"), f"known issue {code} message")

    rules = config.get("rules")
    if not isinstance(rules, list):
        raise ElectiveRuleError("rules must be an array.")
    rule_ids: set[str] = set()
    indexed_keys: set[tuple[str, int]] = set()
    group_ids: set[str] = set()
    for position, rule in enumerate(rules, start=1):
        if not isinstance(rule, Mapping):
            raise ElectiveRuleError(f"Rule {position} must be an object.")
        rule_id = _required_string(rule.get("id"), f"rule {position} id")
        if rule_id in rule_ids:
            raise ElectiveRuleError(f"Duplicate elective rule ID: {rule_id}.")
        rule_ids.add(rule_id)

        programme_keys = rule.get("programme_keys")
        if not isinstance(programme_keys, list) or not programme_keys:
            raise ElectiveRuleError(
                f"Elective rule {rule_id} must have programme_keys."
            )
        academic_level = _positive_integer(
            rule.get("academic_level"), f"rule {rule_id} academic_level"
        )
        for key_value in programme_keys:
            key = _required_string(key_value, f"rule {rule_id} programme key")
            if key not in covered_values:
                raise ElectiveRuleError(
                    f"Rule {rule_id} uses uncovered programme {key}."
                )
            index_key = (key, academic_level)
            if index_key in indexed_keys:
                raise ElectiveRuleError(
                    f"More than one rule matches programme {key} at level {academic_level}."
                )
            indexed_keys.add(index_key)

        heading_choose = _positive_integer(
            rule.get("heading_choose"), f"rule {rule_id} heading_choose"
        )
        groups = rule.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ElectiveRuleError(f"Elective rule {rule_id} must have groups.")

        rule_aliases: dict[str, tuple[str, str]] = {}
        group_choose_total = 0
        for group_position, group in enumerate(groups, start=1):
            if not isinstance(group, Mapping):
                raise ElectiveRuleError(
                    f"Rule {rule_id} group {group_position} must be an object."
                )
            group_id = _required_string(
                group.get("id"), f"rule {rule_id} group {group_position} id"
            )
            if group_id in group_ids:
                raise ElectiveRuleError(f"Duplicate elective group ID: {group_id}.")
            group_ids.add(group_id)
            choose = _positive_integer(
                group.get("choose"), f"elective group {group_id} choose"
            )
            group_choose_total += choose
            options = group.get("options")
            if not isinstance(options, list) or len(options) <= choose:
                raise ElectiveRuleError(
                    f"Elective group {group_id} must have more options than its choose value."
                )
            option_ids: set[str] = set()
            for option_position, option in enumerate(options, start=1):
                if not isinstance(option, Mapping):
                    raise ElectiveRuleError(
                        f"Group {group_id} option {option_position} must be an object."
                    )
                option_id = _required_string(
                    option.get("id"), f"group {group_id} option ID"
                )
                if option_id in option_ids:
                    raise ElectiveRuleError(
                        f"Duplicate option ID {option_id} in group {group_id}."
                    )
                option_ids.add(option_id)
                canonical_name = _required_string(
                    option.get("name"), f"option {option_id} name"
                )
                aliases = option.get("aliases", [])
                if not isinstance(aliases, list) or any(
                    not isinstance(alias, str) or not alias.strip()
                    for alias in aliases
                ):
                    raise ElectiveRuleError(
                        f"Option {option_id} aliases must be an array of names."
                    )
                for name in [canonical_name, *aliases]:
                    normalized = normalize_module_name(name)
                    previous = rule_aliases.get(normalized)
                    identity = (group_id, option_id)
                    if previous is not None and previous != identity:
                        raise ElectiveRuleError(
                            f"Rule {rule_id} maps module name {name!r} to multiple options."
                        )
                    rule_aliases[normalized] = identity

        if group_choose_total != heading_choose:
            raise ElectiveRuleError(
                f"Rule {rule_id} heading chooses {heading_choose}, but its groups choose {group_choose_total}."
            )


def load_elective_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ElectiveRuleError(f"Cannot find elective config: {path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ElectiveRuleError(f"Cannot read elective config: {path}.") from exc
    if not isinstance(value, dict):
        raise ElectiveRuleError("The elective config must contain a JSON object.")
    validate_elective_config(value)
    return value


def _stable_profile_id(rule_id: str, selected: Sequence[str]) -> str:
    payload = json.dumps(
        {"rule_id": rule_id, "selected": list(selected)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "ep-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _rule_index(config: Mapping[str, Any]) -> dict[tuple[str, int], Mapping[str, Any]]:
    result: dict[tuple[str, int], Mapping[str, Any]] = {}
    for rule in config.get("rules", []):
        for key in rule["programme_keys"]:
            result[(str(key), int(rule["academic_level"]))] = rule
    return result


def _known_issues(
    intake_code: str, config: Mapping[str, Any]
) -> list[dict[str, str]]:
    matches = []
    for issue in config.get("known_issues", []):
        if re.fullmatch(str(issue["intake_pattern"]), intake_code):
            matches.append(
                {"code": str(issue["code"]), "message": str(issue["message"])}
            )
    return matches


def _single_metadata_value(events: pd.DataFrame, column: str) -> Any:
    values = events[column].drop_duplicates()
    if len(values) != 1:
        raise ElectiveRuleError(
            f"Intake {events['intake_code'].iloc[0]} has inconsistent {column}."
        )
    return values.iloc[0]


def _base_profile(
    events: pd.DataFrame,
    *,
    status: str,
    profile_name: str,
    rule_id: str,
) -> pd.DataFrame:
    result = events.copy()
    result["elective_profile"] = _stable_profile_id(rule_id, [])
    result["elective_profile_name"] = profile_name
    result["elective_status"] = status
    result["elective_rule_id"] = rule_id
    result["is_elective"] = False
    result["elective_group_id"] = pd.NA
    result["elective_option_id"] = pd.NA
    return result


def _option_lookup(
    rule: Mapping[str, Any],
) -> tuple[
    dict[str, tuple[str, str, str]],
    dict[str, list[tuple[str, str]]],
]:
    aliases: dict[str, tuple[str, str, str]] = {}
    group_options: dict[str, list[tuple[str, str]]] = {}
    for group in rule["groups"]:
        group_id = str(group["id"])
        group_options[group_id] = []
        for option in group["options"]:
            option_id = str(option["id"])
            name = str(option["name"])
            composite = f"{group_id}:{option_id}"
            group_options[group_id].append((composite, name))
            for alias in [name, *option.get("aliases", [])]:
                aliases[normalize_module_name(str(alias))] = (
                    group_id,
                    option_id,
                    name,
                )
    return aliases, group_options


def _resolve_rule_events(
    events: pd.DataFrame, rule: Mapping[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    aliases, group_options = _option_lookup(rule)
    normalized_names = events["module_name"].astype("string").map(
        lambda value: normalize_module_name(str(value))
    )
    matched = normalized_names.map(lambda value: aliases.get(str(value)))

    observed_by_group: dict[str, list[tuple[str, str]]] = {}
    group_reports = []
    profile_choices: list[list[tuple[str, ...]]] = []
    for group in rule["groups"]:
        group_id = str(group["id"])
        choose = int(group["choose"])
        observed = [
            (composite, name)
            for composite, name in group_options[group_id]
            if any(
                match is not None
                and f"{match[0]}:{match[1]}" == composite
                for match in matched
            )
        ]
        observed_by_group[group_id] = observed
        if len(observed) > choose:
            choices = [
                tuple(composite for composite, _ in combination)
                for combination in itertools.combinations(observed, choose)
            ]
        else:
            choices = [tuple(composite for composite, _ in observed)]
        profile_choices.append(choices)
        group_reports.append(
            {
                "group_id": group_id,
                "choose": choose,
                "observed_options": [name for _, name in observed],
                "profile_choice_count": len(choices),
                "insufficient_active_options": 0 < len(observed) < choose,
            }
        )

    profile_selections = [
        tuple(itertools.chain.from_iterable(selection_parts))
        for selection_parts in itertools.product(*profile_choices)
    ]
    if not profile_selections:
        profile_selections = [tuple()]

    option_name_by_composite = {
        composite: name
        for options in group_options.values()
        for composite, name in options
    }
    active_option_count = sum(len(values) for values in observed_by_group.values())
    split_group_count = sum(
        len(group_report["observed_options"]) > group_report["choose"]
        for group_report in group_reports
    )
    if split_group_count:
        status = "resolved"
    elif active_option_count:
        status = "fixed"
    else:
        status = "not_active"

    expanded_parts: list[pd.DataFrame] = []
    for selected in profile_selections:
        selected_set = set(selected)
        row_option_keys = matched.map(
            lambda match: None if match is None else f"{match[0]}:{match[1]}"
        )
        keep = row_option_keys.isna() | row_option_keys.isin(selected_set)
        profile = events.loc[keep].copy()
        selected_names = [option_name_by_composite[key] for key in selected]
        profile["elective_profile"] = _stable_profile_id(str(rule["id"]), selected)
        profile["elective_profile_name"] = (
            " + ".join(selected_names)
            if selected_names
            else "No active brochure elective"
        )
        profile["elective_status"] = status
        profile["elective_rule_id"] = str(rule["id"])

        selected_matches = profile["module_name"].astype("string").map(
            lambda value: aliases.get(normalize_module_name(str(value)))
        )
        profile["is_elective"] = selected_matches.notna()
        profile["elective_group_id"] = selected_matches.map(
            lambda match: pd.NA if match is None else match[0]
        )
        profile["elective_option_id"] = selected_matches.map(
            lambda match: pd.NA if match is None else match[1]
        )
        expanded_parts.append(profile)

    expanded = pd.concat(expanded_parts, ignore_index=True)
    report = {
        "status": status,
        "rule_id": str(rule["id"]),
        "brochure_page": int(rule["brochure_page"]),
        "profile_count": len(profile_selections),
        "active_option_count": active_option_count,
        "split_group_count": split_group_count,
        "groups": group_reports,
        "issues": [
            {
                "code": "insufficient-active-options",
                "message": (
                    f"Group {group['group_id']} requires {group['choose']} options, "
                    f"but only {len(group['observed_options'])} appeared in the snapshot."
                ),
            }
            for group in group_reports
            if group["insufficient_active_options"]
        ],
    }
    return expanded, report


def resolve_elective_profiles(
    events: pd.DataFrame, config: Mapping[str, Any] | None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Expand group variants into valid brochure-backed elective profiles."""

    if events.empty:
        raise ElectiveRuleError("Cannot infer electives from an empty event table.")
    missing = sorted(REQUIRED_EVENT_COLUMNS.difference(events.columns))
    if missing:
        raise ElectiveRuleError(
            "Elective inference is missing event columns: " + ", ".join(missing) + "."
        )

    if config is None:
        result = _base_profile(
            events,
            status="programme_uncovered",
            profile_name="Elective rules unavailable",
            rule_id="unconfigured",
        )
        return result, {
            "schema_version": 1,
            "source": None,
            "status_counts": {
                "programme_uncovered": int(events["intake_code"].nunique())
            },
            "covered_intake_count": 0,
            "uncovered_intake_count": int(events["intake_code"].nunique()),
            "uncovered_programme_counts": {},
            "intakes": [],
        }

    validate_elective_config(config)
    rules = _rule_index(config)
    covered = {str(value) for value in config["covered_programmes"]}
    covered_programme_level = str(config["programme_level"])
    applicability = config["applicability"]
    minimum_year_month = int(applicability["minimum_intake_year_month"])
    verified_legacy = {
        str(value).strip().upper()
        for value in applicability["verified_legacy_intakes"]
    }

    expanded_parts: list[pd.DataFrame] = []
    reports: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    uncovered_programme_counts: Counter[str] = Counter()
    covered_intake_count = 0
    uncovered_intake_count = 0

    for (_, intake_code), intake_events in events.groupby(
        ["snapshot_id", "intake_code"], sort=False, dropna=False
    ):
        intake_text = str(intake_code).strip().upper()
        intake_programme_level = _single_metadata_value(
            intake_events, "programme_level"
        )
        course_code = _single_metadata_value(intake_events, "course_code")
        specialism_code = _single_metadata_value(intake_events, "specialism_code")
        level_value = _single_metadata_value(intake_events, "academic_level")
        intake_year = _single_metadata_value(intake_events, "intake_year")
        intake_month = _single_metadata_value(intake_events, "intake_month")
        key = programme_key(course_code, specialism_code)
        level = None if pd.isna(level_value) else int(level_value)
        year_month = (
            None
            if pd.isna(intake_year) or pd.isna(intake_month)
            else int(intake_year) * 100 + int(intake_month)
        )

        if (
            pd.isna(intake_programme_level)
            or str(intake_programme_level) != covered_programme_level
            or key not in covered
        ):
            uncovered_intake_count += 1
            uncovered_programme_counts[key or "UNPARSED"] += 1
            status = "programme_uncovered"
            expanded = _base_profile(
                intake_events,
                status=status,
                profile_name="Elective rules unavailable",
                rule_id="unconfigured",
            )
            status_counts[status] += 1
            expanded_parts.append(expanded)
            continue

        covered_intake_count += 1
        issue_matches = _known_issues(intake_text, config)
        is_applicable = (
            year_month is not None and year_month >= minimum_year_month
        ) or intake_text in verified_legacy
        if not is_applicable:
            status = "source_version_unverified"
            generic_issue = {
                "code": "source-version-unverified",
                "message": (
                    "This intake predates the July 2026 brochure and has not been "
                    "manually validated against that curriculum version."
                ),
            }
            expanded = _base_profile(
                intake_events,
                status=status,
                profile_name="Unresolved curriculum version",
                rule_id="unverified",
            )
            report = {
                "intake_code": intake_text,
                "programme_key": key,
                "academic_level": level,
                "intake_year_month": year_month,
                "status": status,
                "rule_id": None,
                "profile_count": 1,
                "active_option_count": 0,
                "split_group_count": 0,
                "groups": [],
                "issues": [generic_issue, *issue_matches],
            }
        else:
            rule = rules.get((key, level)) if level is not None else None
            if rule is None:
                status = "no_electives"
                expanded = _base_profile(
                    intake_events,
                    status=status,
                    profile_name="No brochure electives",
                    rule_id="none",
                )
                report = {
                    "intake_code": intake_text,
                    "programme_key": key,
                    "academic_level": level,
                    "intake_year_month": year_month,
                    "status": status,
                    "rule_id": None,
                    "profile_count": 1,
                    "active_option_count": 0,
                    "split_group_count": 0,
                    "groups": [],
                    "issues": issue_matches,
                }
            else:
                expanded, rule_report = _resolve_rule_events(intake_events, rule)
                status = str(rule_report["status"])
                report = {
                    "intake_code": intake_text,
                    "programme_key": key,
                    "academic_level": level,
                    "intake_year_month": year_month,
                    **rule_report,
                }
                report["issues"] = [*rule_report["issues"], *issue_matches]

        status_counts[status] += 1
        reports.append(report)
        expanded_parts.append(expanded)

    result = pd.concat(expanded_parts, ignore_index=True)
    result["elective_profile"] = result["elective_profile"].astype("string")
    result["elective_profile_name"] = result["elective_profile_name"].astype(
        "string"
    )
    result["elective_status"] = result["elective_status"].astype("string")
    result["elective_rule_id"] = result["elective_rule_id"].astype("string")
    result["is_elective"] = result["is_elective"].astype("bool")
    result["elective_group_id"] = result["elective_group_id"].astype("string")
    result["elective_option_id"] = result["elective_option_id"].astype("string")

    report_payload = {
        "schema_version": 1,
        "source": dict(config["source"]),
        "status_counts": dict(sorted(status_counts.items())),
        "covered_intake_count": covered_intake_count,
        "uncovered_intake_count": uncovered_intake_count,
        "uncovered_programme_counts": dict(
            sorted(uncovered_programme_counts.items())
        ),
        "intakes": sorted(reports, key=lambda value: value["intake_code"]),
    }
    return result, report_payload
