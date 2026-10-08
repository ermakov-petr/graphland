"""Shared, side-effect-free input and display policy for leaderboard tools."""
from __future__ import annotations

import json
import math
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class LeaderboardValidationError(ValueError):
    """Raised when repository leaderboard data is invalid."""


def finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def reject_json_constant(value: str) -> None:
    raise LeaderboardValidationError(f"JSON contains non-standard numeric value {value!r}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LeaderboardValidationError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def require_finite_tree(value: Any, location: str = "<root>") -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not finite_number(value):
            raise LeaderboardValidationError(f"{location}: JSON numbers must be finite and representable")
    elif isinstance(value, dict):
        for key, child in value.items():
            require_finite_tree(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            require_finite_tree(child, f"{location}[{index}]")


def load_json(path: Path) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle, parse_constant=reject_json_constant, object_pairs_hook=unique_object)
        require_finite_tree(value)
        return value
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise LeaderboardValidationError(f"Could not read JSON from {path}: {exc}") from exc


def is_https_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        return False
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        return False
    try:
        parsed = urlsplit(value)
        # Accessing port validates non-numeric and out-of-range port strings.
        port = parsed.port
        return (parsed.scheme == "https" and bool(parsed.netloc) and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and (port is None or 0 <= port <= 65535))
    except (TypeError, ValueError):
        return False


def normalize_display_text(value: str, field: str, *, single_line: bool = False) -> str:
    """NFC text; reject display spoofing, retaining legitimate ZWNJ/ZWJ and emoji selectors."""
    if not isinstance(value, str):
        raise LeaderboardValidationError(f"{field} must be text")
    for character in value:
        category = unicodedata.category(character)
        if character in "\n\t" and not single_line:
            continue
        if category in {"Cc", "Cs"} or (category == "Cf" and character not in {"\u200c", "\u200d"}):
            raise LeaderboardValidationError(f"{field} contains an unsupported control or invisible character")
    if single_line and any(character in value for character in ("\n", "\r", "\t", "\u2028", "\u2029")):
        raise LeaderboardValidationError(f"{field} must be a single line")
    return unicodedata.normalize("NFC", value)


def require_safe_text_tree(value: Any, location: str = "<root>") -> None:
    if isinstance(value, str):
        if normalize_display_text(value, location) != value:
            raise LeaderboardValidationError(f"{location}: text must use NFC normalization")
    elif isinstance(value, dict):
        for key, child in value.items():
            require_safe_text_tree(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            require_safe_text_tree(child, f"{location}[{index}]")


def result_num_runs(submission: dict[str, Any], result: dict[str, Any]) -> int:
    return result.get("num_runs", submission.get("num_runs"))


def result_hparam_trials(submission: dict[str, Any], result: dict[str, Any]) -> Any:
    return result.get("hparam_trials", submission.get("hparam_trials"))


def is_synthetic_demo(submission: dict[str, Any]) -> bool:
    variant = submission.get("model_variant", "").casefold()
    notes = (submission.get("notes") or "").casefold()
    return (submission.get("id", "").startswith("demo-") and "synthetic" in variant
            and ("not a benchmark" in variant or "not benchmark" in notes or "not a benchmark" in notes))
