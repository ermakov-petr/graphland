#!/usr/bin/env python3
"""Build the static GraphLand leaderboard into a GitHub Pages artifact."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate import LeaderboardValidationError, validate_repository  # noqa: E402


CSV_COLUMNS = [
    "submission_id",
    "model_name",
    "model_variant",
    "setting",
    "task",
    "dataset",
    "metric",
    "value",
    "std",
    "num_runs",
    "method_type",
    "hparam_trials",
    "code_availability",
    "paper_url",
    "code_url",
    "provenance",
    "verification",
    "submitted_at",
    "source_issue",
]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_output_path(output: Path) -> Path:
    if output.is_symlink():
        raise LeaderboardValidationError(f"Refusing symlink build output path: {output}")
    resolved = output.resolve()
    forbidden = {Path(resolved.anchor), ROOT.resolve(), Path.home().resolve()}
    if resolved in forbidden:
        raise LeaderboardValidationError(f"Refusing unsafe build output path: {resolved}")
    return resolved


def _check_relative_asset_paths(site_source: Path) -> None:
    checks = {
        "index.html": [r"(?:href|src)=[\"']/(?!/)", r"url\(/"],
        "assets/styles.css": [r"url\(/"],
        "assets/app.js": [r"fetch\(\s*[\"']/"],
    }
    for relative_path, patterns in checks.items():
        path = site_source / relative_path
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if re.search(pattern, text):
                raise LeaderboardValidationError(
                    f"{relative_path} contains a root-absolute asset reference that breaks /graphland/"
                )


def csv_rows(
    submissions: Sequence[Mapping[str, Any]],
    datasets_by_id: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    setting_order = {setting: index for index, setting in enumerate(("RL", "RH", "TH", "THI"))}
    rows: List[Dict[str, Any]] = []
    for submission in sorted(submissions, key=lambda item: item["id"]):
        results = sorted(
            submission["results"],
            key=lambda result: (setting_order[result["setting"]], result["dataset"]),
        )
        for result in results:
            dataset = datasets_by_id[result["dataset"]]
            rows.append(
                {
                    "submission_id": submission["id"],
                    "model_name": submission["model_name"],
                    "model_variant": submission["model_variant"],
                    "setting": result["setting"],
                    "task": dataset["task"],
                    "dataset": result["dataset"],
                    "metric": dataset["metric"],
                    "value": result["value"],
                    "std": result.get("std", ""),
                    "num_runs": submission["num_runs"],
                    "method_type": submission["method_type"],
                    "hparam_trials": submission["hparam_trials"],
                    "code_availability": submission["code_availability"],
                    "paper_url": submission["paper_url"],
                    "code_url": submission["training_code_url"] or "",
                    "provenance": submission["provenance"],
                    "verification": submission["verification"],
                    "submitted_at": submission["submitted_at"],
                    "source_issue": submission["source_issue"],
                }
            )
    return rows


def write_csv(
    destination: Path,
    submissions: Sequence[Mapping[str, Any]],
    datasets_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_rows(submissions, datasets_by_id))


def build_site(
    output: Path,
    *,
    root: Path = ROOT,
    submissions_dir: Optional[Path] = None,
    allow_pending: bool = False,
) -> Dict[str, Any]:
    root = root.resolve()
    output = _safe_output_path(output)
    validated = validate_repository(
        root=root,
        submissions_dir=submissions_dir,
        allow_pending=allow_pending,
    )
    site_source = root / "site"
    _check_relative_asset_paths(site_source)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    shutil.copy2(site_source / "index.html", output / "index.html")
    shutil.copy2(site_source / "favicon.svg", output / "favicon.svg")
    shutil.copytree(site_source / "assets", output / "assets")

    data_dir = output / "data"
    data_dir.mkdir()
    payload = {
        "schema_version": "1.0",
        "config": validated["config"],
        "datasets": validated["datasets"],
        "submissions": validated["submissions"],
    }
    (data_dir / "leaderboard.json").write_bytes(_json_bytes(payload))
    write_csv(output / "leaderboard.csv", validated["submissions"], validated["datasets_by_id"])

    schema_dir = output / "schema"
    schema_dir.mkdir()
    (schema_dir / "submission.schema.json").write_bytes(_json_bytes(validated["schema"]))
    (output / ".nojekyll").write_bytes(b"")
    return validated


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_site", help="Build output directory")
    parser.add_argument("--submissions-dir", type=Path, help="Trusted local submissions directory (test/local QA only)")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="Build pending candidate or demo submissions; never use for a production artifact",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        validated = build_site(
            args.output,
            submissions_dir=args.submissions_dir,
            allow_pending=args.allow_pending,
        )
    except (LeaderboardValidationError, OSError) as exc:
        print(f"Leaderboard build failed: {exc}")
        return 1
    print(
        f"Built {args.output.resolve()} with {len(validated['datasets'])} datasets and "
        f"{len(validated['submissions'])} submissions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
