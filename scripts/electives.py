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


def _validated_applicability(value: Any, label: str) -> tuple[int, set[str]]:
    if not isinstance(value, Mapping):
        raise ElectiveRuleError(f"{label} must be an object.")
    minimum = _validate_year_month(value.get("minimum_intake_year_month"))
    verified = value.get("verified_legacy_intakes")
    if not isinstance(verified, list) or any(
        not isinstance(item, str) or not item.strip() for item in verified
    ):
        raise ElectiveRuleError(
            f"{label}.verified_legacy_intakes must be an array of intake codes."
        )
    normalized = {str(item).strip().upper() for item in verified}
    if len(normalized) != len(verified):
        raise ElectiveRuleError(
            f"{label}.verified_legacy_intakes cannot contain duplicates."
        )
    return minimum, normalized


def _option_module_names(
    option: Mapping[str, Any], option_id: str
) -> list[tuple[str, list[str]]]:
    """Return the feed module names represented by one selectable option."""

    modules = option.get("modules")
    if modules is None:
        name = _required_string(option.get("name"), f"option {option_id} name")
        aliases = option.get("aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ElectiveRuleError(
                f"Option {option_id} aliases must be an array of names."
            )
        return [(name, [str(alias).strip() for alias in aliases])]

    _required_string(option.get("name"), f"option {option_id} name")
    if "aliases" in option:
        raise ElectiveRuleError(
            f"Package option {option_id} must put aliases on its modules."
        )
    if not isinstance(modules, list) or not modules:
        raise ElectiveRuleError(
            f"Package option {option_id} modules must be a non-empty array."
        )
    result: list[tuple[str, list[str]]] = []
    for position, module in enumerate(modules, start=1):
        if not isinstance(module, Mapping):
            raise ElectiveRuleError(
                f"Package option {option_id} module {position} must be an object."
            )
        name = _required_string(
            module.get("name"), f"option {option_id} module {position} name"
        )
        aliases = module.get("aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ElectiveRuleError(
                f"Option {option_id} module {position} aliases must be an array."
            )
        result.append((name, [str(alias).strip() for alias in aliases]))
    return result


def validate_elective_config(config: Mapping[str, Any]) -> None:
    """Validate the multi-source, versioned elective-rule configuration."""

    if config.get("schema_version") != 2:
        raise ElectiveRuleError("Elective config schema_version must be 2.")

    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ElectiveRuleError("Elective config sources must be a non-empty array.")
    source_ids: set[str] = set()
    for position, source in enumerate(sources, start=1):
        if not isinstance(source, Mapping):
            raise ElectiveRuleError(f"Source {position} must be an object.")
        source_id = _required_string(source.get("id"), f"source {position} id")
        if source_id in source_ids:
            raise ElectiveRuleError(f"Duplicate elective source ID: {source_id}.")
        source_ids.add(source_id)
        _required_string(source.get("title"), f"source {source_id} title")
        _required_string(source.get("edition"), f"source {source_id} edition")
        _required_string(source.get("local_path"), f"source {source_id} local_path")
        _required_string(source.get("url"), f"source {source_id} url")
        _required_string(source.get("sha256"), f"source {source_id} sha256")
        _required_string(
            source.get("extraction_method"), f"source {source_id} extraction_method"
        )
        _validated_applicability(
            source.get("applicability"), f"source {source_id} applicability"
        )

    covered = config.get("covered_programmes")
    if not isinstance(covered, list) or not covered:
        raise ElectiveRuleError("covered_programmes must be a non-empty array.")
    coverage_statuses = {"complete", "source_ambiguous", "source_not_found"}
    covered_values: dict[tuple[str, str], Mapping[str, Any]] = {}
    for position, item in enumerate(covered, start=1):
        if not isinstance(item, Mapping):
            raise ElectiveRuleError(
                f"Covered programme {position} must be an object."
            )
        level = _required_string(
            item.get("programme_level"),
            f"covered programme {position} programme_level",
        )
        keys = item.get("programme_keys")
        if not isinstance(keys, list) or not keys:
            raise ElectiveRuleError(
                f"Covered programme group {position} must have programme_keys."
            )
        programme_keys = [
            _required_string(key, f"covered programme group {position} key")
            for key in keys
        ]
        if len(set(programme_keys)) != len(programme_keys):
            raise ElectiveRuleError(
                f"Covered programme group {position} has duplicate keys."
            )
        status = _required_string(
            item.get("status"), f"covered programme group {position} status"
        )
        if status not in coverage_statuses:
            raise ElectiveRuleError(
                f"Covered programme group {position} has unknown status {status}."
            )
        source_id = item.get("source_id")
        if status == "source_not_found":
            if source_id is not None:
                raise ElectiveRuleError(
                    f"Programme group {position} cannot name a source when none was found."
                )
        elif _required_string(
            source_id, f"covered programme group {position} source_id"
        ) not in source_ids:
            raise ElectiveRuleError(
                f"Programme group {position} references an unknown source."
            )
        if status != "complete":
            _required_string(
                item.get("note"), f"covered programme group {position} note"
            )
        for key in programme_keys:
            index_key = (level, key)
            if index_key in covered_values:
                raise ElectiveRuleError(
                    f"Covered programme {level}/{key} is duplicated."
                )
            covered_values[index_key] = item

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
        blocks_resolution = issue.get("blocks_resolution", False)
        if not isinstance(blocks_resolution, bool):
            raise ElectiveRuleError(
                f"Known issue {code} blocks_resolution must be a boolean."
            )

    rules = config.get("rules")
    if not isinstance(rules, list):
        raise ElectiveRuleError("rules must be an array.")
    rule_ids: set[str] = set()
    indexed_keys: set[tuple[str, str, int | None]] = set()
    group_ids: set[str] = set()
    for position, rule in enumerate(rules, start=1):
        if not isinstance(rule, Mapping):
            raise ElectiveRuleError(f"Rule {position} must be an object.")
        rule_id = _required_string(rule.get("id"), f"rule {position} id")
        if rule_id in rule_ids:
            raise ElectiveRuleError(f"Duplicate elective rule ID: {rule_id}.")
        rule_ids.add(rule_id)

        source_id = _required_string(
            rule.get("source_id"), f"rule {rule_id} source_id"
        )
        if source_id not in source_ids:
            raise ElectiveRuleError(
                f"Elective rule {rule_id} references unknown source {source_id}."
            )
        programme_level = _required_string(
            rule.get("programme_level"), f"rule {rule_id} programme_level"
        )

        programme_keys = rule.get("programme_keys")
        if not isinstance(programme_keys, list) or not programme_keys:
            raise ElectiveRuleError(
                f"Elective rule {rule_id} must have programme_keys."
            )
        academic_value = rule.get("academic_level")
        academic_level = (
            None
            if academic_value is None
            else _positive_integer(
                academic_value, f"rule {rule_id} academic_level"
            )
        )
        applicable_levels = rule.get("applicable_academic_levels")
        if applicable_levels is not None:
            if academic_level is not None:
                raise ElectiveRuleError(
                    f"Rule {rule_id} cannot combine academic_level with applicable_academic_levels."
                )
            if not isinstance(applicable_levels, list) or not applicable_levels:
                raise ElectiveRuleError(
                    f"Rule {rule_id} applicable_academic_levels must be a non-empty array."
                )
            normalized_levels = [
                _positive_integer(value, f"rule {rule_id} applicable academic level")
                for value in applicable_levels
            ]
            if len(set(normalized_levels)) != len(normalized_levels):
                raise ElectiveRuleError(
                    f"Rule {rule_id} applicable_academic_levels cannot contain duplicates."
                )
        for key_value in programme_keys:
            key = _required_string(key_value, f"rule {rule_id} programme key")
            coverage = covered_values.get((programme_level, key))
            if coverage is None:
                raise ElectiveRuleError(
                    f"Rule {rule_id} uses uncovered programme {programme_level}/{key}."
                )
            if coverage.get("status") != "complete":
                raise ElectiveRuleError(
                    f"Rule {rule_id} uses incomplete programme {programme_level}/{key}."
                )
            if coverage.get("source_id") != source_id:
                raise ElectiveRuleError(
                    f"Rule {rule_id} and programme {programme_level}/{key} use different sources."
                )
            index_key = (programme_level, key, academic_level)
            if index_key in indexed_keys:
                raise ElectiveRuleError(
                    f"More than one rule matches {programme_level}/{key} at level {academic_level}."
                )
            indexed_keys.add(index_key)

        heading_choose = _positive_integer(
            rule.get("heading_choose"), f"rule {rule_id} heading_choose"
        )
        groups = rule.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ElectiveRuleError(f"Elective rule {rule_id} must have groups.")

        rule_aliases: dict[str, set[tuple[str, str]]] = {}
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
                for canonical_name, aliases in _option_module_names(
                    option, option_id
                ):
                    for name in [canonical_name, *aliases]:
                        normalized = normalize_module_name(name)
                        identity = (group_id, option_id)
                        previous = rule_aliases.setdefault(normalized, set())
                        if previous and any(
                            previous_group != group_id
                            for previous_group, _ in previous
                        ):
                            raise ElectiveRuleError(
                                f"Rule {rule_id} maps module name {name!r} across multiple groups."
                            )
                        previous.add(identity)

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


def _rule_index(
    config: Mapping[str, Any]
) -> dict[tuple[str, str, int | None], Mapping[str, Any]]:
    result: dict[tuple[str, str, int | None], Mapping[str, Any]] = {}
    for rule in config.get("rules", []):
        for key in rule["programme_keys"]:
            academic_value = rule.get("academic_level")
            academic_level = (
                None if academic_value is None else int(academic_value)
            )
            result[
                (str(rule["programme_level"]), str(key), academic_level)
            ] = rule
    return result


def _coverage_index(
    config: Mapping[str, Any]
) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in config.get("covered_programmes", []):
        for key in item["programme_keys"]:
            result[(str(item["programme_level"]), str(key))] = item
    return result


def _source_index(config: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(source["id"]): source for source in config.get("sources", [])}


def _known_issues(
    intake_code: str, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    matches = []
    for issue in config.get("known_issues", []):
        if re.fullmatch(str(issue["intake_pattern"]), intake_code):
            matches.append(
                {
                    "code": str(issue["code"]),
                    "message": str(issue["message"]),
                    "blocks_resolution": bool(
                        issue.get("blocks_resolution", False)
                    ),
                }
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
    dict[str, list[tuple[str, str, str]]],
    dict[str, list[tuple[str, str]]],
]:
    aliases: dict[str, list[tuple[str, str, str]]] = {}
    group_options: dict[str, list[tuple[str, str]]] = {}
    for group in rule["groups"]:
        group_id = str(group["id"])
        group_options[group_id] = []
        for option in group["options"]:
            option_id = str(option["id"])
            name = str(option["name"])
            composite = f"{group_id}:{option_id}"
            group_options[group_id].append((composite, name))
            for module_name, module_aliases in _option_module_names(
                option, option_id
            ):
                for alias in [module_name, *module_aliases]:
                    aliases.setdefault(
                        normalize_module_name(str(alias)), []
                    ).append(
                        (group_id, option_id, name)
                    )
    return aliases, group_options


def _resolve_rule_events(
    events: pd.DataFrame, rule: Mapping[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    aliases, group_options = _option_lookup(rule)
    normalized_names = events["module_name"].astype("string").map(
        lambda value: normalize_module_name(str(value))
    )
    matched = normalized_names.map(
        lambda value: aliases.get(str(value), [])
    )

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
                any(f"{identity[0]}:{identity[1]}" == composite for identity in matches)
                for matches in matched
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
    unique_profiles: dict[tuple[bool, ...], list[tuple[str, ...]]] = {}
    for selected in profile_selections:
        selected_set = set(selected)
        keep = matched.map(
            lambda matches: not matches
            or any(
                f"{match[0]}:{match[1]}" in selected_set
                for match in matches
            )
        )
        signature = tuple(bool(value) for value in keep)
        unique_profiles.setdefault(signature, []).append(selected)

    active_option_count = sum(len(values) for values in observed_by_group.values())
    split_group_count = sum(
        len(group_report["observed_options"]) > group_report["choose"]
        for group_report in group_reports
    )
    if len(unique_profiles) > 1:
        status = "resolved"
    elif active_option_count:
        status = "fixed"
    else:
        status = "not_active"

    expanded_parts: list[pd.DataFrame] = []
    for signature, equivalent_selections in unique_profiles.items():
        selected_set = set(
            itertools.chain.from_iterable(equivalent_selections)
        )
        selected = tuple(sorted(selected_set))
        keep = pd.Series(signature, index=events.index, dtype="bool")
        profile = events.loc[keep].copy()
        profile_names = []
        for candidate in equivalent_selections:
            candidate_name = " + ".join(
                option_name_by_composite[key] for key in candidate
            )
            if candidate_name and candidate_name not in profile_names:
                profile_names.append(candidate_name)
        profile["elective_profile"] = _stable_profile_id(str(rule["id"]), selected)
        profile["elective_profile_name"] = (
            " or ".join(profile_names)
            if profile_names
            else "No active brochure elective"
        )
        profile["elective_status"] = status
        profile["elective_rule_id"] = str(rule["id"])

        selected_matches = profile["module_name"].astype("string").map(
            lambda value: next(
                (
                    match
                    for match in aliases.get(
                        normalize_module_name(str(value)), []
                    )
                    if f"{match[0]}:{match[1]}" in selected_set
                ),
                None,
            )
        )
        profile["is_elective"] = selected_matches.map(
            lambda match: match is not None
        )
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
        "profile_count": len(unique_profiles),
        "candidate_profile_count": len(profile_selections),
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
            "schema_version": 2,
            "sources": [],
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
    coverage = _coverage_index(config)
    sources = _source_index(config)

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
        intake_level = (
            None
            if pd.isna(intake_programme_level)
            else str(intake_programme_level)
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
        issue_matches = _known_issues(intake_text, config)
        coverage_item = (
            None
            if intake_level is None or key is None
            else coverage.get((intake_level, key))
        )
        source_id = (
            None
            if coverage_item is None
            else coverage_item.get("source_id")
        )
        report_base = {
            "intake_code": intake_text,
            "programme_level": intake_level,
            "programme_key": key,
            "academic_level": level,
            "intake_year_month": year_month,
            "source_id": source_id,
        }

        if coverage_item is None:
            uncovered_intake_count += 1
            uncovered_key = f"{intake_level or 'unparsed'}/{key or 'UNPARSED'}"
            uncovered_programme_counts[uncovered_key] += 1
            status = "programme_uncovered"
            expanded = _base_profile(
                intake_events,
                status=status,
                profile_name="Elective rules unavailable",
                rule_id="unconfigured",
            )
            report = {
                **report_base,
                "status": status,
                "rule_id": None,
                "profile_count": 1,
                "active_option_count": 0,
                "split_group_count": 0,
                "groups": [],
                "issues": [
                    {
                        "code": "programme-uncovered",
                        "message": "No programme-to-brochure mapping is configured.",
                    },
                    *issue_matches,
                ],
            }
        elif coverage_item["status"] != "complete":
            uncovered_intake_count += 1
            uncovered_key = f"{intake_level}/{key}"
            uncovered_programme_counts[uncovered_key] += 1
            status = str(coverage_item["status"])
            profile_names = {
                "source_ambiguous": "Brochure choice is ambiguous",
                "source_not_found": "No current brochure source found",
            }
            expanded = _base_profile(
                intake_events,
                status=status,
                profile_name=profile_names[status],
                rule_id=status,
            )
            report = {
                **report_base,
                "status": status,
                "rule_id": None,
                "profile_count": 1,
                "active_option_count": 0,
                "split_group_count": 0,
                "groups": [],
                "issues": [
                    {
                        "code": status.replace("_", "-"),
                        "message": str(coverage_item["note"]),
                    },
                    *issue_matches,
                ],
            }
        else:
            covered_intake_count += 1
            source = sources[str(source_id)]
            applicability = source["applicability"]
            minimum_year_month = int(
                applicability["minimum_intake_year_month"]
            )
            verified_legacy = {
                str(value).strip().upper()
                for value in applicability["verified_legacy_intakes"]
            }
            blocking_issues = [
                issue for issue in issue_matches if issue["blocks_resolution"]
            ]
            is_applicable = (
                year_month is not None and year_month >= minimum_year_month
            ) or intake_text in verified_legacy

            if blocking_issues:
                status = "known_issue_unresolved"
                expanded = _base_profile(
                    intake_events,
                    status=status,
                    profile_name="Known curriculum mismatch",
                    rule_id="known-issue",
                )
                report = {
                    **report_base,
                    "status": status,
                    "rule_id": None,
                    "profile_count": 1,
                    "active_option_count": 0,
                    "split_group_count": 0,
                    "groups": [],
                    "issues": issue_matches,
                }
            elif not is_applicable:
                status = "source_version_unverified"
                minimum_text = str(minimum_year_month)
                generic_issue = {
                    "code": "source-version-unverified",
                    "message": (
                        f"This intake predates the supported {minimum_text} intake "
                        f"range for the {source['edition']} source and has not been "
                        "validated against a matching curriculum edition."
                    ),
                }
                expanded = _base_profile(
                    intake_events,
                    status=status,
                    profile_name="Unresolved curriculum version",
                    rule_id="unverified",
                )
                report = {
                    **report_base,
                    "status": status,
                    "rule_id": None,
                    "profile_count": 1,
                    "active_option_count": 0,
                    "split_group_count": 0,
                    "groups": [],
                    "issues": [generic_issue, *issue_matches],
                }
            else:
                rule = rules.get((intake_level, key, level))
                if rule is None:
                    wildcard_rule = rules.get((intake_level, key, None))
                    allowed_levels = (
                        None
                        if wildcard_rule is None
                        else wildcard_rule.get("applicable_academic_levels")
                    )
                    if wildcard_rule is not None and (
                        allowed_levels is None or level in allowed_levels
                    ):
                        rule = wildcard_rule
                if rule is None:
                    status = "no_electives"
                    expanded = _base_profile(
                        intake_events,
                        status=status,
                        profile_name="No brochure elective choice",
                        rule_id="none",
                    )
                    report = {
                        **report_base,
                        "status": status,
                        "rule_id": None,
                        "profile_count": 1,
                        "active_option_count": 0,
                        "split_group_count": 0,
                        "groups": [],
                        "issues": issue_matches,
                    }
                else:
                    expanded, rule_report = _resolve_rule_events(
                        intake_events, rule
                    )
                    status = str(rule_report["status"])
                    report = {**report_base, **rule_report}
                    report["issues"] = [
                        *rule_report["issues"],
                        *issue_matches,
                    ]

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
        "schema_version": 2,
        "sources": [dict(source) for source in config["sources"]],
        "status_counts": dict(sorted(status_counts.items())),
        "covered_intake_count": covered_intake_count,
        "uncovered_intake_count": uncovered_intake_count,
        "uncovered_programme_counts": dict(
            sorted(uncovered_programme_counts.items())
        ),
        "intakes": sorted(reports, key=lambda value: value["intake_code"]),
    }
    return result, report_payload
