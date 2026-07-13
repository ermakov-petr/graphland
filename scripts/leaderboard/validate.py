#!/usr/bin/env python3
"""JSON Schema and semantic validation for the GraphLand leaderboard."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
DATASETS_PATH = ROOT / "leaderboard" / "datasets.json"
CONFIG_PATH = ROOT / "leaderboard" / "config.json"
SCHEMA_PATH = ROOT / "leaderboard" / "schema" / "submission.schema.json"
SUBMISSIONS_PATH = ROOT / "leaderboard" / "submissions"

SETTINGS = ("RL", "RH", "TH", "THI")
TEMPORAL_UNAVAILABLE = {
    "city-reviews",
    "city-roads-M",
    "city-roads-L",
    "web-traffic",
}

EXPECTED_DATASETS = {
    "hm-categories": ("multiclass_node_classification", "accuracy"),
    "pokec-regions": ("multiclass_node_classification", "accuracy"),
    "web-topics": ("multiclass_node_classification", "accuracy"),
    "tolokers-2": ("binary_node_classification", "average_precision"),
    "city-reviews": ("binary_node_classification", "average_precision"),
    "artnet-exp": ("binary_node_classification", "average_precision"),
    "web-fraud": ("binary_node_classification", "average_precision"),
    "hm-prices": ("node_regression", "r2"),
    "avazu-ctr": ("node_regression", "r2"),
    "city-roads-M": ("node_regression", "r2"),
    "city-roads-L": ("node_regression", "r2"),
    "twitch-views": ("node_regression", "r2"),
    "artnet-views": ("node_regression", "r2"),
    "web-traffic": ("node_regression", "r2"),
}

EXPECTED_TASK_COUNTS = {
    "multiclass_node_classification": 3,
    "binary_node_classification": 4,
    "node_regression": 7,
}


class LeaderboardValidationError(ValueError):
    """Raised when repository leaderboard data is invalid."""


def _reject_non_standard_number(value: str) -> None:
    raise LeaderboardValidationError(f"JSON contains non-standard numeric value {value!r}")


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, parse_constant=_reject_non_standard_number)
    except (OSError, json.JSONDecodeError) as exc:
        raise LeaderboardValidationError(f"Could not read JSON from {path}: {exc}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LeaderboardValidationError(message)


def _is_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


def _is_iso_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return parsed.isoformat() == value


def validate_config(config: Mapping[str, Any]) -> None:
    _require(config.get("schema_version") == "1.0", "config.json must use schema_version 1.0")
    site = config.get("site")
    _require(isinstance(site, dict), "config.json must contain a site object")
    _require(site.get("base_path") == "/graphland/", "Pages base_path must be /graphland/")
    for key in ("repository_url", "submission_url"):
        _require(_is_https_url(site.get(key, "")), f"site.{key} must be an HTTPS URL")

    default_filters = config.get("default_filters")
    _require(
        isinstance(default_filters, dict)
        and isinstance(default_filters.get("only_models_with_code"), bool),
        "default_filters.only_models_with_code must be a boolean",
    )

    task_families = config.get("task_families")
    _require(isinstance(task_families, list), "config.json task_families must be a list")
    _require(
        [family.get("id") for family in task_families] == list(EXPECTED_TASK_COUNTS),
        "config.json must define exactly the three canonical task families in display order",
    )

    settings = config.get("settings")
    _require(isinstance(settings, list), "config.json settings must be a list")
    _require([setting.get("id") for setting in settings] == list(SETTINGS), "Settings must be RL, RH, TH, THI")
    settings_by_id = {setting["id"]: setting for setting in settings}
    expected_splits = {
        "RL": "split_masks_RL.csv",
        "RH": "split_masks_RH.csv",
        "TH": "split_masks_TH.csv",
        "THI": "split_masks_TH.csv",
    }
    for setting_id, split_file in expected_splits.items():
        _require(
            settings_by_id[setting_id].get("split_file") == split_file,
            f"{setting_id} must use {split_file}",
        )
    _require(
        settings_by_id["THI"].get("information_access") == "inductive",
        "THI must use the inductive information-access protocol",
    )
    for setting_id in ("RL", "RH", "TH"):
        _require(
            settings_by_id[setting_id].get("information_access") == "transductive",
            f"{setting_id} must use the transductive information-access protocol",
        )


def validate_datasets(document: Mapping[str, Any]) -> List[Dict[str, Any]]:
    _require(document.get("schema_version") == "1.0", "datasets.json must use schema_version 1.0")
    datasets = document.get("datasets")
    _require(isinstance(datasets, list), "datasets.json must contain a datasets list")
    _require(len(datasets) == 14, "datasets.json must describe exactly 14 datasets")

    ids = [dataset.get("id") for dataset in datasets]
    _require(len(ids) == len(set(ids)), "Dataset IDs must be unique")
    _require(set(ids) == set(EXPECTED_DATASETS), "datasets.json must contain the 14 official GraphLand datasets")

    task_counts = Counter(dataset.get("task") for dataset in datasets)
    _require(task_counts == Counter(EXPECTED_TASK_COUNTS), "Task-family distribution must be 3 / 4 / 7")

    temporal_count = 0
    for dataset in datasets:
        dataset_id = dataset["id"]
        expected_task, expected_metric = EXPECTED_DATASETS[dataset_id]
        _require(dataset.get("task") == expected_task, f"{dataset_id} has the wrong task family")
        _require(dataset.get("metric") == expected_metric, f"{dataset_id} has the wrong canonical metric")
        _require(dataset.get("higher_is_better") is True, f"{dataset_id} must be higher-is-better")
        _require(isinstance(dataset.get("display_name"), str) and dataset["display_name"], f"{dataset_id} needs a display name")
        _require(dataset.get("release_version") == "v1", f"{dataset_id} must reference release v1")
        _require(dataset.get("license") == "Apache-2.0", f"{dataset_id} must use the dataset release license Apache-2.0")

        source = dataset.get("source")
        _require(isinstance(source, dict), f"{dataset_id}.source must be an object")
        _require(_is_https_url(source.get("url", "")), f"{dataset_id}.source.url must be HTTPS")

        display = dataset.get("display")
        _require(isinstance(display, dict), f"{dataset_id}.display must be an object")
        if expected_metric in {"accuracy", "average_precision"}:
            _require(display.get("style") == "percentage", f"{dataset_id} must display its [0, 1] metric as a percentage")
        else:
            _require(display.get("style") == "number", f"{dataset_id} must display R² without percentage scaling")
        _require(
            isinstance(display.get("decimals"), int) and 0 <= display["decimals"] <= 8,
            f"{dataset_id}.display.decimals must be an integer from 0 to 8",
        )

        available = dataset.get("available_settings")
        _require(isinstance(available, list), f"{dataset_id}.available_settings must be a list")
        _require(len(available) == len(set(available)), f"{dataset_id} repeats an available setting")
        _require(set(available).issubset(SETTINGS), f"{dataset_id} contains an unknown setting")
        _require({"RL", "RH"}.issubset(available), f"{dataset_id} must support RL and RH")
        supports_temporal = "TH" in available or "THI" in available
        _require(("TH" in available) == ("THI" in available), f"{dataset_id} must support TH and THI together")
        if dataset_id in TEMPORAL_UNAVAILABLE:
            _require(not supports_temporal, f"{dataset_id} must not support TH or THI")
        else:
            _require(supports_temporal, f"{dataset_id} must support all four settings")
            temporal_count += 1

    _require(temporal_count == 10, "Exactly 10 datasets must support TH and THI")
    return list(datasets)


def _schema_error_message(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def validate_submission(
    submission: Mapping[str, Any],
    datasets_by_id: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
    *,
    source_path: Optional[Path] = None,
) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    schema_errors = sorted(validator.iter_errors(submission), key=lambda error: list(error.absolute_path))
    if schema_errors:
        prefix = f"{source_path}: " if source_path else ""
        messages = "; ".join(_schema_error_message(error) for error in schema_errors[:8])
        raise LeaderboardValidationError(f"{prefix}schema validation failed: {messages}")

    submission_id = submission["id"]
    if source_path is not None:
        _require(source_path.name == f"{submission_id}.json", f"{source_path}: filename must match submission id")

    _require(_is_https_url(submission["paper_url"]), f"{submission_id}: paper_url must be HTTPS without credentials")
    code_url = submission["training_code_url"]
    if submission["code_availability"] == "available":
        _require(isinstance(code_url, str) and _is_https_url(code_url), f"{submission_id}: available code requires an HTTPS URL")
    else:
        _require(code_url is None, f"{submission_id}: unavailable code must use null for training_code_url")

    _require(_is_iso_date(submission["submitted_at"]), f"{submission_id}: submitted_at must be an ISO date")
    if submission["method_type"] == "in_context":
        _require(submission["hparam_trials"] == 0, f"{submission_id}: in-context submissions must use zero trials")

    review = submission["review"]
    if review["status"] == "approved":
        _require(review["reviewer_github"] is not None, f"{submission_id}: approved review needs a reviewer")
        _require(review["reviewed_at"] is not None and _is_iso_date(review["reviewed_at"]), f"{submission_id}: approved review needs an ISO review date")
    else:
        _require(review["reviewer_github"] is None, f"{submission_id}: pending review cannot name a reviewer")
        _require(review["reviewed_at"] is None, f"{submission_id}: pending review cannot have a review date")

    result_keys = set()
    for result in submission["results"]:
        dataset_id = result["dataset"]
        _require(dataset_id in datasets_by_id, f"{submission_id}: unknown dataset {dataset_id!r}")
        dataset = datasets_by_id[dataset_id]
        setting = result["setting"]
        key = (setting, dataset_id)
        _require(key not in result_keys, f"{submission_id}: duplicate result for {setting} / {dataset_id}")
        result_keys.add(key)
        _require(setting in dataset["available_settings"], f"{submission_id}: {setting} is not available for {dataset_id}")

        value = result["value"]
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), f"{submission_id}: result values must be finite numbers")
        if dataset["metric"] in {"accuracy", "average_precision"}:
            _require(0 <= value <= 1, f"{submission_id}: {dataset['metric']} for {dataset_id} must be in [0, 1]")

        if "std" in result:
            std = result["std"]
            _require(isinstance(std, (int, float)) and not isinstance(std, bool) and math.isfinite(std), f"{submission_id}: std must be finite")
            _require(std >= 0, f"{submission_id}: std must be non-negative")


def discover_submission_paths(submissions_dir: Path) -> List[Path]:
    _require(submissions_dir.is_dir(), f"Submission directory does not exist: {submissions_dir}")
    paths = sorted(path for path in submissions_dir.iterdir() if path.suffix == ".json")
    for path in paths:
        _require(path.is_file() and not path.is_symlink(), f"Submission entries must be regular JSON files: {path}")
        _require(re.fullmatch(r"(?:issue-[1-9][0-9]*|[a-z0-9]+(?:-[a-z0-9]+)*)\.json", path.name) is not None, f"Unsafe submission filename: {path.name}")
    return paths


def validate_repository(
    root: Path = ROOT,
    submissions_dir: Optional[Path] = None,
    *,
    allow_pending: bool = False,
) -> Dict[str, Any]:
    root = root.resolve()
    config = load_json(root / "leaderboard" / "config.json")
    datasets_document = load_json(root / "leaderboard" / "datasets.json")
    schema = load_json(root / "leaderboard" / "schema" / "submission.schema.json")
    validate_config(config)
    datasets = validate_datasets(datasets_document)
    datasets_by_id = {dataset["id"]: dataset for dataset in datasets}

    submission_root = (submissions_dir or root / "leaderboard" / "submissions").resolve()
    submissions = []
    ids = set()
    for path in discover_submission_paths(submission_root):
        submission = load_json(path)
        _require(isinstance(submission, dict), f"{path}: a submission must be a JSON object")
        validate_submission(submission, datasets_by_id, schema, source_path=path)
        if not allow_pending:
            _require(
                submission["review"]["status"] == "approved",
                f"{path}: pending submissions cannot be published; a maintainer must approve the review metadata",
            )
        _require(submission["id"] not in ids, f"Duplicate submission id: {submission['id']}")
        ids.add(submission["id"])
        submissions.append(submission)

    return {
        "config": config,
        "datasets": datasets,
        "datasets_by_id": datasets_by_id,
        "schema": schema,
        "submissions": submissions,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submissions-dir", type=Path, help="Trusted local directory of submission JSON files")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="Accept pending review records for candidate or demo validation; never use for publication",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the success message")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        validated = validate_repository(
            submissions_dir=args.submissions_dir,
            allow_pending=args.allow_pending,
        )
    except LeaderboardValidationError as exc:
        print(f"Leaderboard validation failed: {exc}")
        return 1
    if not args.quiet:
        print(
            "Leaderboard validation passed: "
            f"{len(validated['datasets'])} datasets, {len(validated['submissions'])} submissions."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
