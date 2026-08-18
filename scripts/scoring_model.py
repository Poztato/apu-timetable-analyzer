"""Shared timetable convenience scoring model.

The model turns one calendar day into an absolute inconvenience score. Lower
scores are better. Empty days score 0, online-only days stay below 20, and any
day that requires a campus trip starts at 20.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Mapping, Sequence


SCORE_METHOD = "absolute_daily_cost_v1"
PHYSICAL_COMPONENT_KEYS = (
    "campus_trip",
    "placement",
    "span",
    "waiting",
    "short_day",
    "long_day",
)
VARIABLE_PHYSICAL_COMPONENT_KEYS = PHYSICAL_COMPONENT_KEYS[1:]
EMPHASIS_KEYS = ("short_day", "long_day")
RAMP_KEYS = ("placement", "span", "waiting", "short_day", "long_day")


class ScoringModelError(RuntimeError):
    """Raised when the convenience scoring contract is invalid."""


@dataclass(frozen=True)
class TimePreference:
    key: str
    label: str
    short_label: str
    start_minutes: int
    end_minutes: int
    description: str

    @property
    def start(self) -> str:
        return _format_clock(self.start_minutes)

    @property
    def end(self) -> str:
        return _format_clock(self.end_minutes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "short_label": self.short_label,
            "start": self.start,
            "end": self.end,
            "description": self.description,
        }


@dataclass(frozen=True)
class Ramp:
    low: float
    high: float
    reverse: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "reverse": self.reverse,
        }


@dataclass(frozen=True)
class OnlineDayConfig:
    base_points: float
    span_points: float
    load_points: float

    @property
    def maximum_points(self) -> float:
        return self.base_points + self.span_points + self.load_points

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_points": self.base_points,
            "span_points": self.span_points,
            "load_points": self.load_points,
        }


@dataclass(frozen=True)
class ScoringModel:
    model_version: str
    weekly_divisor_days: int
    default_time_preference: str
    time_preferences: tuple[TimePreference, ...]
    component_weights: Mapping[str, float]
    emphasis_bonus: Mapping[str, float]
    online_day: OnlineDayConfig
    ramps: Mapping[str, Ramp]

    @property
    def preferences_by_key(self) -> dict[str, TimePreference]:
        return {preference.key: preference for preference in self.time_preferences}

    @property
    def profile_id(self) -> str:
        canonical = json.dumps(
            self.as_config_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()[:16]

    @property
    def physical_day_minimum(self) -> float:
        return self.component_weights["campus_trip"]

    def as_config_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "weekly_divisor_days": self.weekly_divisor_days,
            "default_time_preference": self.default_time_preference,
            "time_preferences": [
                preference.as_dict() for preference in self.time_preferences
            ],
            "component_weights": dict(self.component_weights),
            "emphasis_bonus": dict(self.emphasis_bonus),
            "online_day": self.online_day.as_dict(),
            "ramps": {
                key: self.ramps[key].as_dict()
                for key in RAMP_KEYS
            },
        }

    def as_payload(self) -> dict[str, Any]:
        return {
            **self.as_config_dict(),
            "profile_id": self.profile_id,
            "score_method": SCORE_METHOD,
            "physical_day_minimum": self.physical_day_minimum,
            "online_day_maximum": self.online_day.maximum_points,
        }


@dataclass(frozen=True)
class DayScore:
    day_type: str
    total: float
    penalties: Mapping[str, float]
    component_points: Mapping[str, float]
    component_caps: Mapping[str, float]


def _parse_clock(value: Any, field: str) -> int:
    if not isinstance(value, str):
        raise ScoringModelError(f"Scoring field {field} must be a time string.")
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ScoringModelError(
            f"Scoring field {field} is not a valid time: {value!r}."
        ) from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ScoringModelError(
            f"Scoring field {field} must use local HH:MM precision."
        )
    return parsed.hour * 60 + parsed.minute


def _format_clock(value: int) -> str:
    hours, minutes = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}"


def _number(value: Any, field: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoringModelError(f"Scoring field {field} must be numeric.")
    converted = float(value)
    if not math.isfinite(converted) or converted < minimum:
        raise ScoringModelError(
            f"Scoring field {field} must be at least {minimum}."
        )
    return converted


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScoringModelError(f"Scoring field {field} must be a positive integer.")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScoringModelError(f"Scoring field {field} must be an object.")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoringModelError(f"Scoring field {field} must be non-empty text.")
    return value.strip()


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], field: str
) -> None:
    missing = sorted(expected.difference(value))
    extra = sorted(set(value).difference(expected))
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("unexpected " + ", ".join(extra))
        raise ScoringModelError(f"Scoring field {field} has " + "; ".join(parts) + ".")


def parse_scoring_model(config: Mapping[str, Any]) -> ScoringModel:
    """Validate and parse the complete scoring configuration."""

    top_level_keys = {
        "model_version",
        "weekly_divisor_days",
        "default_time_preference",
        "time_preferences",
        "component_weights",
        "emphasis_bonus",
        "online_day",
        "ramps",
    }
    _require_exact_keys(config, top_level_keys, "root")

    raw_preferences = config["time_preferences"]
    if not isinstance(raw_preferences, list) or not raw_preferences:
        raise ScoringModelError(
            "Scoring field time_preferences must be a non-empty list."
        )
    preferences: list[TimePreference] = []
    preference_keys: set[str] = set()
    preference_fields = {
        "key",
        "label",
        "short_label",
        "start",
        "end",
        "description",
    }
    for index, raw_preference in enumerate(raw_preferences):
        item = _mapping(raw_preference, f"time_preferences[{index}]")
        _require_exact_keys(item, preference_fields, f"time_preferences[{index}]")
        key = _text(item["key"], f"time_preferences[{index}].key")
        if key in preference_keys:
            raise ScoringModelError(f"Time preference key {key!r} is duplicated.")
        start_minutes = _parse_clock(
            item["start"], f"time_preferences[{index}].start"
        )
        end_minutes = _parse_clock(
            item["end"], f"time_preferences[{index}].end"
        )
        if start_minutes >= end_minutes:
            raise ScoringModelError(
                f"Time preference {key!r} must start before it ends."
            )
        preferences.append(
            TimePreference(
                key=key,
                label=_text(item["label"], f"time_preferences[{index}].label"),
                short_label=_text(
                    item["short_label"],
                    f"time_preferences[{index}].short_label",
                ),
                start_minutes=start_minutes,
                end_minutes=end_minutes,
                description=_text(
                    item["description"],
                    f"time_preferences[{index}].description",
                ),
            )
        )
        preference_keys.add(key)

    default_preference = _text(
        config["default_time_preference"], "default_time_preference"
    )
    if default_preference not in preference_keys:
        raise ScoringModelError(
            "default_time_preference does not match an available preference."
        )

    raw_weights = _mapping(config["component_weights"], "component_weights")
    _require_exact_keys(
        raw_weights, set(PHYSICAL_COMPONENT_KEYS), "component_weights"
    )
    component_weights = {
        key: _number(raw_weights[key], f"component_weights.{key}", minimum=0.000001)
        for key in PHYSICAL_COMPONENT_KEYS
    }
    if not math.isclose(sum(component_weights.values()), 100.0):
        raise ScoringModelError("Physical component weights must total 100 points.")

    raw_bonus = _mapping(config["emphasis_bonus"], "emphasis_bonus")
    _require_exact_keys(raw_bonus, set(EMPHASIS_KEYS), "emphasis_bonus")
    emphasis_bonus = {
        key: _number(raw_bonus[key], f"emphasis_bonus.{key}")
        for key in EMPHASIS_KEYS
    }

    raw_online = _mapping(config["online_day"], "online_day")
    _require_exact_keys(
        raw_online, {"base_points", "span_points", "load_points"}, "online_day"
    )
    online_day = OnlineDayConfig(
        base_points=_number(raw_online["base_points"], "online_day.base_points"),
        span_points=_number(raw_online["span_points"], "online_day.span_points"),
        load_points=_number(raw_online["load_points"], "online_day.load_points"),
    )
    if online_day.maximum_points >= component_weights["campus_trip"]:
        raise ScoringModelError(
            "The maximum online-only score must stay below the campus trip score."
        )

    raw_ramps = _mapping(config["ramps"], "ramps")
    _require_exact_keys(raw_ramps, set(RAMP_KEYS), "ramps")
    ramps: dict[str, Ramp] = {}
    for key in RAMP_KEYS:
        raw_ramp = _mapping(raw_ramps[key], f"ramps.{key}")
        _require_exact_keys(raw_ramp, {"low", "high", "reverse"}, f"ramps.{key}")
        low = _number(raw_ramp["low"], f"ramps.{key}.low")
        high = _number(raw_ramp["high"], f"ramps.{key}.high")
        reverse = raw_ramp["reverse"]
        if high <= low:
            raise ScoringModelError(f"Scoring ramp {key} must have high above low.")
        if not isinstance(reverse, bool):
            raise ScoringModelError(f"Scoring ramp {key}.reverse must be boolean.")
        ramps[key] = Ramp(low=low, high=high, reverse=reverse)

    model = ScoringModel(
        model_version=_text(config["model_version"], "model_version"),
        weekly_divisor_days=_positive_integer(
            config["weekly_divisor_days"], "weekly_divisor_days"
        ),
        default_time_preference=default_preference,
        time_preferences=tuple(preferences),
        component_weights=component_weights,
        emphasis_bonus=emphasis_bonus,
        online_day=online_day,
        ramps=ramps,
    )
    if model.weekly_divisor_days != 7:
        raise ScoringModelError(
            "weekly_divisor_days must be 7 so empty days receive their reward."
        )
    return model


def load_scoring_model(path: Path) -> ScoringModel:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScoringModelError(f"Cannot find scoring config: {path}.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ScoringModelError(f"Cannot read scoring config: {path}.") from exc
    if not isinstance(config, dict):
        raise ScoringModelError("The scoring config must contain a JSON object.")
    return parse_scoring_model(config)


def smooth_ramp(value: float, ramp: Ramp) -> float:
    """Return a capped smoothstep penalty between 0 and 1."""

    if not math.isfinite(value) or value < 0:
        raise ScoringModelError("A scoring measurement must be non-negative.")
    progress = min(1.0, max(0.0, (value - ramp.low) / (ramp.high - ramp.low)))
    smoothed = progress * progress * (3.0 - 2.0 * progress)
    return 1.0 - smoothed if ramp.reverse else smoothed


def duration_weighted_deviation(
    intervals: Sequence[tuple[int, int]], preference: TimePreference
) -> float:
    """Average minute distance of occupied teaching from a preferred band."""

    duration = 0
    distance_area = 0.0
    lower = preference.start_minutes
    upper = preference.end_minutes
    for start, end in intervals:
        if start < 0 or end > 24 * 60 or start >= end:
            raise ScoringModelError("A placement interval is invalid.")
        duration += end - start

        before_end = min(end, lower)
        if start < before_end:
            distance_area += (
                lower * (before_end - start)
                - (before_end**2 - start**2) / 2
            )

        after_start = max(start, upper)
        if after_start < end:
            distance_area += (
                (end**2 - after_start**2) / 2
                - upper * (end - after_start)
            )

    if duration == 0:
        return 0.0
    return distance_area / duration


def _component_caps(
    model: ScoringModel,
    *,
    emphasize_short_days: bool,
    emphasize_long_days: bool,
) -> dict[str, float]:
    trip_points = model.component_weights["campus_trip"]
    variable_budget = 100.0 - trip_points
    raw_caps = {
        key: model.component_weights[key]
        + (
            model.emphasis_bonus[key]
            if (
                key == "short_day" and emphasize_short_days
                or key == "long_day" and emphasize_long_days
            )
            else 0.0
        )
        for key in VARIABLE_PHYSICAL_COMPONENT_KEYS
    }
    scale = variable_budget / sum(raw_caps.values())
    return {
        "campus_trip": trip_points,
        **{key: raw_caps[key] * scale for key in VARIABLE_PHYSICAL_COMPONENT_KEYS},
    }


def score_day(
    model: ScoringModel,
    *,
    teaching_minutes: int,
    physical_teaching_minutes: int,
    span_minutes: int,
    physical_span_minutes: int,
    waiting_minutes: int,
    placement_deviation_minutes: float,
    emphasize_short_days: bool = False,
    emphasize_long_days: bool = False,
) -> DayScore:
    """Score one empty, online-only, or campus day."""

    values = (
        teaching_minutes,
        physical_teaching_minutes,
        span_minutes,
        physical_span_minutes,
        waiting_minutes,
        placement_deviation_minutes,
    )
    if any(not math.isfinite(float(value)) or value < 0 for value in values):
        raise ScoringModelError("Day scoring measurements must be non-negative.")
    if physical_teaching_minutes > teaching_minutes:
        raise ScoringModelError("Physical teaching cannot exceed total teaching.")
    if waiting_minutes > physical_span_minutes:
        raise ScoringModelError("Campus waiting cannot exceed the campus span.")

    zero_penalties = {key: 0.0 for key in RAMP_KEYS}
    zero_components = {
        "campus_trip": 0.0,
        "online_commitment": 0.0,
        "placement": 0.0,
        "span": 0.0,
        "waiting": 0.0,
        "short_day": 0.0,
        "long_day": 0.0,
    }
    zero_caps = dict(zero_components)
    if teaching_minutes == 0:
        return DayScore("empty", 0.0, zero_penalties, zero_components, zero_caps)

    long_penalty = smooth_ramp(teaching_minutes, model.ramps["long_day"])
    if physical_teaching_minutes == 0:
        span_penalty = smooth_ramp(span_minutes, model.ramps["span"])
        raw_span_cap = model.online_day.span_points
        raw_load_cap = model.online_day.load_points + (
            model.online_day.load_points
            * model.emphasis_bonus["long_day"]
            / model.component_weights["long_day"]
            if emphasize_long_days
            else 0.0
        )
        variable_budget = (
            model.online_day.span_points + model.online_day.load_points
        )
        scale = variable_budget / (raw_span_cap + raw_load_cap)
        span_cap = raw_span_cap * scale
        load_cap = raw_load_cap * scale
        components = {
            **zero_components,
            "online_commitment": model.online_day.base_points,
            "span": span_cap * span_penalty,
            "long_day": load_cap * long_penalty,
        }
        caps = {
            **zero_caps,
            "online_commitment": model.online_day.base_points,
            "span": span_cap,
            "long_day": load_cap,
        }
        penalties = {
            **zero_penalties,
            "span": span_penalty,
            "long_day": long_penalty,
        }
        return DayScore(
            "online",
            sum(components.values()),
            penalties,
            components,
            caps,
        )

    penalties = {
        "placement": smooth_ramp(
            placement_deviation_minutes, model.ramps["placement"]
        ),
        "span": smooth_ramp(physical_span_minutes, model.ramps["span"]),
        "waiting": smooth_ramp(waiting_minutes, model.ramps["waiting"]),
        "short_day": smooth_ramp(
            physical_teaching_minutes, model.ramps["short_day"]
        ),
        "long_day": long_penalty,
    }
    caps = _component_caps(
        model,
        emphasize_short_days=emphasize_short_days,
        emphasize_long_days=emphasize_long_days,
    )
    components = {
        **zero_components,
        "campus_trip": caps["campus_trip"],
        **{
            key: caps[key] * penalties[key]
            for key in VARIABLE_PHYSICAL_COMPONENT_KEYS
        },
    }
    all_caps = {**zero_caps, **caps}
    return DayScore(
        "physical",
        sum(components.values()),
        penalties,
        components,
        all_caps,
    )
