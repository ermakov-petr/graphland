#!/usr/bin/env python3
"""Convert one reviewed GitHub Issue Form submission into canonical JSON.

The command-line paths are trusted workflow configuration. All values read from
the issue document are untrusted data and are never used to construct a path.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate import (  # noqa: E402
    LeaderboardValidationError,
    load_json,
    validate_datasets,
    validate_submission,
)


TITLE_PREFIX = "[Leaderboard submission]"
REQUIRED_LABELS = {"leaderboard-submission", "leaderboard-ready"}
NO_RESPONSE = "_No response_"

MAX_BODY_BYTES = 64 * 1024
MAX_BODY_LINES = 500
MAX_LINE_BYTES = 4096
MAX_RESULTS_BYTES = 20_000
MAX_RESULTS = 48
MAX_NUMERIC_TOKEN = 64

SECTION_LABELS = (
    "Model name",
    "Model variant or version",
    "GitHub username",
    "Paper URL",
    "Code availability",
    "Training code URL",
    "GraphLand release, tag, or commit",
    "Method type",
    "Hyperparameter trials",
    "Tuning protocol",
    "Number of runs or seeds",
    "External data or pretraining",
    "Results",
    "Additional notes",
    "Confirmations",
)

CONFIRMATIONS = (
    "I used only the official GraphLand datasets and splits.",
    "I did not use test labels for training or hyperparameter tuning.",
    "I followed the information-access protocol for every reported setting.",
    "I confirm that these submission details and results may be published.",
    "I understand that the results will be marked as self-reported unless independently reproduced.",
    "I have not included secrets or confidential data in this public issue.",
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
INTEGER_RE = re.compile(r"^(?:0|[1-9][0-9]{0,6})$")
NUMBER_RE = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
CHECKBOX_RE = re.compile(r"^- \[([xX ])\] (.+)$")


class IssueSubmissionError(ValueError):
    """Raised when an issue cannot safely become a leaderboard submission."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IssueSubmissionError(message)


def _reject_unsafe_characters(value: str, field: str) -> None:
    for character in value:
        if character in "\n\t":
            continue
        category = unicodedata.category(character)
        if category == "Cc" or character in {
            "\u061c",
            "\u200e",
            "\u200f",
            "\u202a",
            "\u202b",
            "\u202c",
            "\u202d",
            "\u202e",
            "\u2066",
            "\u2067",
            "\u2068",
            "\u2069",
        }:
            raise IssueSubmissionError(f"{field} contains an unsupported control character")


def _normalize(value: str, field: str) -> str:
    _reject_unsafe_characters(value, field)
    return unicodedata.normalize("NFC", value)


def _bounded_text(
    value: str,
    field: str,
    *,
    minimum: int = 1,
    maximum: int,
    single_line: bool = False,
) -> str:
    _require(isinstance(value, str), f"{field} must be text")
    value = _normalize(value, field).strip()
    _require(minimum <= len(value) <= maximum, f"{field} must contain {minimum} to {maximum} characters")
    if single_line:
        _require("\n" not in value and "\r" not in value, f"{field} must be a single line")
    return value


def parse_issue_body(body: str) -> Dict[str, str]:
    """Parse the exact Markdown layout emitted by the leaderboard Issue Form."""

    _require(isinstance(body, str), "issue body must be a string")
    _require(len(body.encode("utf-8")) <= MAX_BODY_BYTES, "issue body is too large")
    body = _normalize(body.replace("\r\n", "\n").replace("\r", "\n"), "issue body")
    lines = body.split("\n")
    _require(len(lines) <= MAX_BODY_LINES, "issue body contains too many lines")
    for line in lines:
        _require(len(line.encode("utf-8")) <= MAX_LINE_BYTES, "issue body contains an overlong line")

    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    expected_index = 0
    for line in lines:
        if line.startswith("### "):
            label = line[4:].strip()
            _require(label in SECTION_LABELS, f"unexpected issue-form heading: {label!r}")
            _require(label not in sections, f"duplicate issue-form heading: {label!r}")
            _require(
                expected_index < len(SECTION_LABELS) and label == SECTION_LABELS[expected_index],
                f"issue-form heading {label!r} is missing or out of order",
            )
            sections[label] = []
            current = label
            expected_index += 1
            continue
        if current is None:
            _require(not line.strip(), "unexpected content before the first issue-form heading")
        else:
            sections[current].append(line)

    _require(expected_index == len(SECTION_LABELS), "issue body is missing one or more form sections")
    parsed = {label: "\n".join(sections[label]).strip() for label in SECTION_LABELS}
    for label, value in parsed.items():
        if value == NO_RESPONSE and label not in {"Training code URL", "Additional notes"}:
            raise IssueSubmissionError(f"{label} is required")
    return parsed


def _parse_username(value: str, field: str) -> str:
    value = _bounded_text(value, field, maximum=40, single_line=True)
    if value.startswith("@"):
        value = value[1:]
    _require(USERNAME_RE.fullmatch(value) is not None, f"{field} is not a valid GitHub username")
    return value


def _parse_https_url(value: str, field: str) -> str:
    value = _bounded_text(value, field, maximum=500, single_line=True)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise IssueSubmissionError(f"{field} is not a valid URL") from exc
    _require(
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None,
        f"{field} must be an HTTPS URL without embedded credentials",
    )
    return value


def _parse_integer(value: str, field: str, *, minimum: int) -> int:
    value = _bounded_text(value, field, maximum=7, single_line=True)
    _require(INTEGER_RE.fullmatch(value) is not None, f"{field} must be a decimal integer")
    parsed = int(value)
    _require(minimum <= parsed <= 1_000_000, f"{field} must be from {minimum} to 1000000")
    return parsed


def _parse_number(value: str, field: str) -> float:
    value = value.strip()
    _require(0 < len(value) <= MAX_NUMERIC_TOKEN, f"{field} has an invalid numeric length")
    _require(NUMBER_RE.fullmatch(value) is not None, f"{field} must be a finite decimal number")
    try:
        decimal_value = Decimal(value)
        parsed = float(decimal_value)
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise IssueSubmissionError(f"{field} must be a finite decimal number") from exc
    _require(decimal_value.is_finite() and math.isfinite(parsed), f"{field} must be finite")
    return parsed


def _parse_results(value: str) -> List[Dict[str, Any]]:
    _require(len(value.encode("utf-8")) <= MAX_RESULTS_BYTES, "Results is too large")
    lines = value.splitlines()
    _require(len(lines) >= 3, "Results must be a rendered csv code block")
    _require(lines[0].strip() == "```csv" and lines[-1].strip() == "```", "Results must use the csv code block from the Issue Form")
    csv_text = "\n".join(lines[1:-1])
    try:
        rows = list(csv.reader(io.StringIO(csv_text, newline=""), strict=True))
    except csv.Error as exc:
        raise IssueSubmissionError(f"Results contains invalid CSV: {exc}") from exc
    _require(bool(rows), "Results must include a header and at least one result")
    _require(rows[0] == ["setting", "dataset", "value", "std"], "Results must use the exact setting,dataset,value,std header")
    data_rows = rows[1:]
    _require(1 <= len(data_rows) <= MAX_RESULTS, f"Results must contain 1 to {MAX_RESULTS} rows")

    results: List[Dict[str, Any]] = []
    seen = set()
    for index, row in enumerate(data_rows, start=2):
        _require(len(row) == 4, f"Results row {index} must contain exactly four columns")
        setting, dataset, raw_value, raw_std = (cell.strip() for cell in row)
        _require(setting in {"RL", "RH", "TH", "THI"}, f"Results row {index} has an unknown setting")
        dataset = _bounded_text(dataset, f"Results row {index} dataset", maximum=100, single_line=True)
        key = (setting, dataset)
        _require(key not in seen, f"Results repeats {setting} / {dataset}")
        seen.add(key)
        result: Dict[str, Any] = {
            "setting": setting,
            "dataset": dataset,
            "value": _parse_number(raw_value, f"Results row {index} value"),
        }
        if raw_std:
            std = _parse_number(raw_std, f"Results row {index} std")
            _require(std >= 0, f"Results row {index} std must be non-negative")
            result["std"] = std
        results.append(result)
    return results


def _validate_confirmations(value: str) -> None:
    observed: Dict[str, bool] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        match = CHECKBOX_RE.fullmatch(line.strip())
        _require(match is not None, "Confirmations contains an unexpected line")
        checked, text = match.groups()
        _require(text in CONFIRMATIONS, f"Confirmations contains an unexpected item: {text!r}")
        _require(text not in observed, f"Confirmations repeats an item: {text!r}")
        observed[text] = checked.lower() == "x"
    _require(set(observed) == set(CONFIRMATIONS), "Confirmations is missing one or more required items")
    _require(all(observed.values()), "Every confirmation must be checked")


def _labels(issue: Mapping[str, Any]) -> set[str]:
    labels = issue.get("labels")
    _require(isinstance(labels, list), "issue labels must be a list")
    names = set()
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
        else:
            raise IssueSubmissionError("issue contains an invalid label entry")
    return names


def _submitted_date(value: Any) -> str:
    _require(isinstance(value, str) and len(value) <= 40, "issue created_at is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IssueSubmissionError("issue created_at is invalid") from exc
    _require(parsed.tzinfo is not None, "issue created_at must include a timezone")
    return parsed.date().isoformat()


def submission_from_issue(issue: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate an issue API object and return its canonical submission object."""

    _require(isinstance(issue, Mapping), "issue JSON must contain an object")
    number = issue.get("number")
    _require(isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= 2_147_483_647, "issue number is invalid")
    _require(issue.get("state") == "open", "only open issues can update a submission")
    title = issue.get("title")
    _require(isinstance(title, str) and title.startswith(TITLE_PREFIX), f"issue title must start with {TITLE_PREFIX}")
    _require(len(title.encode("utf-8")) <= 256, "issue title is too long")
    _require(REQUIRED_LABELS.issubset(_labels(issue)), "issue must have leaderboard-submission and leaderboard-ready labels")

    author = issue.get("user")
    _require(isinstance(author, Mapping), "issue author is missing")
    author_login = _parse_username(author.get("login", ""), "issue author")
    fields = parse_issue_body(issue.get("body"))
    declared_login = _parse_username(fields["GitHub username"], "GitHub username")
    _require(declared_login.casefold() == author_login.casefold(), "GitHub username must match the authenticated issue author")

    code_availability = _bounded_text(fields["Code availability"], "Code availability", maximum=20, single_line=True)
    _require(code_availability in {"available", "unavailable"}, "Code availability must be available or unavailable")
    code_text = fields["Training code URL"]
    if code_text == NO_RESPONSE:
        code_text = ""
    if code_availability == "available":
        training_code_url: Optional[str] = _parse_https_url(code_text, "Training code URL")
    else:
        _require(not code_text.strip(), "Training code URL must be empty when code is unavailable")
        training_code_url = None

    method_type = _bounded_text(fields["Method type"], "Method type", maximum=20, single_line=True)
    _require(method_type in {"trained", "in_context"}, "Method type must be trained or in_context")
    hparam_trials = _parse_integer(fields["Hyperparameter trials"], "Hyperparameter trials", minimum=0)
    if method_type == "in_context":
        _require(hparam_trials == 0, "in-context learning must use zero hyperparameter trials")
    _validate_confirmations(fields["Confirmations"])

    notes_text = fields["Additional notes"]
    notes: Optional[str]
    if notes_text == NO_RESPONSE or not notes_text.strip():
        notes = None
    else:
        notes = _bounded_text(notes_text, "Additional notes", maximum=4000)

    submission: Dict[str, Any] = {
        "schema_version": "1.0",
        "id": f"issue-{number}",
        "model_name": _bounded_text(fields["Model name"], "Model name", maximum=120, single_line=True),
        "model_variant": _bounded_text(fields["Model variant or version"], "Model variant or version", maximum=120, single_line=True),
        "paper_url": _parse_https_url(fields["Paper URL"], "Paper URL"),
        "code_availability": code_availability,
        "training_code_url": training_code_url,
        "submitter_github": author_login,
        "provenance": "author_submission",
        "source_issue": number,
        "graphland_ref": _bounded_text(fields["GraphLand release, tag, or commit"], "GraphLand release, tag, or commit", maximum=100, single_line=True),
        "method_type": method_type,
        "hparam_trials": hparam_trials,
        "tuning_protocol": _bounded_text(fields["Tuning protocol"], "Tuning protocol", maximum=4000),
        "num_runs": _parse_integer(fields["Number of runs or seeds"], "Number of runs or seeds", minimum=1),
        "external_data_pretraining": _bounded_text(fields["External data or pretraining"], "External data or pretraining", maximum=4000),
        "submitted_at": _submitted_date(issue.get("created_at")),
        "verification": "self_reported",
        "review": {
            "status": "pending",
            "reviewer_github": None,
            "reviewed_at": None,
            "notes": None,
        },
        "notes": notes,
        "results": _parse_results(fields["Results"]),
    }

    try:
        datasets_document = load_json(ROOT / "leaderboard" / "datasets.json")
        datasets = validate_datasets(datasets_document)
        schema = load_json(ROOT / "leaderboard" / "schema" / "submission.schema.json")
        validate_submission(submission, {item["id"]: item for item in datasets}, schema)
    except LeaderboardValidationError as exc:
        raise IssueSubmissionError(str(exc)) from exc
    return submission


def write_submission(issue: Mapping[str, Any], submissions_dir: Path) -> Path:
    """Write a canonical submission beneath a trusted submissions directory."""

    supplied_dir = Path(submissions_dir)
    _require(not supplied_dir.is_symlink(), "submissions directory must not be a symlink")
    try:
        supplied_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IssueSubmissionError(f"could not create submissions directory: {supplied_dir}") from exc
    root = supplied_dir.resolve()
    _require(root.is_dir(), f"submissions directory does not exist: {root}")
    submission = submission_from_issue(issue)
    target = root / f"issue-{submission['source_issue']}.json"
    _require(target.parent.resolve() == root, "derived submission path escapes the submissions directory")
    _require(not target.is_symlink(), "submission target must not be a symlink")
    payload = json.dumps(submission, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"

    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".issue-{submission['source_issue']}.",
            suffix=".tmp",
            dir=root,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, target)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
    return target


def _reject_json_constant(value: str) -> None:
    raise IssueSubmissionError(f"issue JSON contains non-standard numeric value {value!r}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-json", type=Path, required=True, help="Trusted local path containing the GitHub issue API response")
    parser.add_argument("--submissions-dir", type=Path, required=True, help="Trusted leaderboard submissions directory")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        with args.issue_json.open("r", encoding="utf-8") as handle:
            issue = json.load(handle, parse_constant=_reject_json_constant)
        target = write_submission(issue, args.submissions_dir)
    except (IssueSubmissionError, OSError, json.JSONDecodeError) as exc:
        print(f"Issue submission conversion failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote validated submission: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
