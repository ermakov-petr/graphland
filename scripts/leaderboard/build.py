#!/usr/bin/env python3
"""Build the static GraphLand leaderboard into a GitHub Pages artifact."""

from __future__ import annotations

import argparse
import csv
import json
import copy
import os
import re
import shutil
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate import LeaderboardValidationError, validate_repository  # noqa: E402
from common import load_json, result_hparam_trials, result_num_runs

BUILD_MARKER = ".graphland-build.json"
MARKER_CONTENT = {"builder": "graphland-leaderboard", "format": 1}


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
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, OverflowError) as exc:
        raise LeaderboardValidationError(f"Cannot serialize non-finite leaderboard JSON: {exc}") from exc


def _safe_output_path(output: Path, root: Path = ROOT, submissions_dir: Optional[Path] = None) -> Path:
    if output.is_symlink():
        raise LeaderboardValidationError(f"Refusing symlink build output path: {output}")
    resolved = output.resolve()
    root = root.resolve()
    forbidden = {Path(resolved.anchor), Path.home().resolve()}
    protected = [root / name for name in ("site", "leaderboard", "scripts", "tests", ".git", ".agents", ".codex", ".aws")]
    if submissions_dir is not None:
        protected.append(submissions_dir.resolve())
    if resolved in forbidden or root.is_relative_to(resolved) or any(
        resolved.is_relative_to(source.resolve()) or source.resolve().is_relative_to(resolved)
        for source in protected
    ):
        raise LeaderboardValidationError(f"Refusing unsafe build output path: {resolved}")
    if resolved.exists():
        if not resolved.is_dir():
            raise LeaderboardValidationError(f"Build output is not a directory: {resolved}")
        if any(resolved.iterdir()):
            marker = resolved / BUILD_MARKER
            if marker.is_symlink() or not marker.is_file() or load_json(marker) != MARKER_CONTENT:
                raise LeaderboardValidationError(f"Refusing nonempty unowned build output: {resolved}; choose an empty directory or a marked GraphLand artifact")
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
                    "num_runs": result_num_runs(submission, result),
                    "method_type": submission["method_type"],
                    "hparam_trials": result_hparam_trials(submission, result) if result_hparam_trials(submission, result) is not None else "",
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


def normalized_submissions(submissions: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Public payload includes effective per-result counts without guessing unknown budgets."""
    normalized = copy.deepcopy(list(submissions))
    for submission in normalized:
        for result in submission["results"]:
            result["num_runs"] = result_num_runs(submission, result)
            trials = result_hparam_trials(submission, result)
            if trials is not None:
                result["hparam_trials"] = trials
    return normalized


def write_xlsx(destination: Path, submissions: Sequence[Mapping[str, Any]], datasets_by_id: Mapping[str, Mapping[str, Any]]) -> None:
    """Deterministic OOXML: display strings stay strings, never formula cells."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace("", namespace)
    worksheet = ET.Element(f"{{{namespace}}}worksheet")
    sheet_data = ET.SubElement(worksheet, f"{{{namespace}}}sheetData")
    records = [dict(zip(CSV_COLUMNS, CSV_COLUMNS))] + csv_rows(submissions, datasets_by_id)
    for index, record in enumerate(records, 1):
        row = ET.SubElement(sheet_data, f"{{{namespace}}}row", r=str(index))
        for column_index, key in enumerate(CSV_COLUMNS):
            value = record[key]
            letter = chr(ord("A") + column_index)
            attrs = {"r": f"{letter}{index}"}
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cell = ET.SubElement(row, f"{{{namespace}}}c", attrs)
                ET.SubElement(cell, f"{{{namespace}}}v").text = str(value)
            else:
                cell = ET.SubElement(row, f"{{{namespace}}}c", {**attrs, "t": "inlineStr"})
                inline = ET.SubElement(cell, f"{{{namespace}}}is")
                ET.SubElement(inline, f"{{{namespace}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}).text = str(value or "")
    parts = {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": b'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="GraphLand" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(parts.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)


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
    output = _safe_output_path(output, root, submissions_dir)
    validated = validate_repository(
        root=root,
        submissions_dir=submissions_dir,
        allow_pending=allow_pending,
    )
    site_source = root / "site"
    _check_relative_asset_paths(site_source)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.build-", dir=output.parent))
    backup: Optional[Path] = None
    try:
        _write_artifact(staging, site_source, validated)
        # Recheck ownership immediately before replacement; validation/write errors preserve old output.
        _safe_output_path(output, root, submissions_dir)
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.previous-", dir=output.parent))
            backup.rmdir()
            os.replace(output, backup)
        try:
            os.replace(staging, output)
        except OSError:
            if backup is not None and backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return validated


def _write_artifact(output: Path, site_source: Path, validated: Mapping[str, Any]) -> None:

    shutil.copy2(site_source / "index.html", output / "index.html")
    shutil.copy2(site_source / "favicon.svg", output / "favicon.svg")
    shutil.copytree(site_source / "assets", output / "assets")

    data_dir = output / "data"
    data_dir.mkdir()
    payload = {
        "schema_version": "1.0",
        "config": validated["config"],
        "datasets": validated["datasets"],
        "submissions": normalized_submissions(validated["submissions"]),
    }
    (data_dir / "leaderboard.json").write_bytes(_json_bytes(payload))
    write_csv(output / "leaderboard.csv", validated["submissions"], validated["datasets_by_id"])
    write_xlsx(output / "leaderboard.xlsx", validated["submissions"], validated["datasets_by_id"])

    schema_dir = output / "schema"
    schema_dir.mkdir()
    (schema_dir / "submission.schema.json").write_bytes(_json_bytes(validated["schema"]))
    (output / ".nojekyll").write_bytes(b"")
    (output / BUILD_MARKER).write_bytes(_json_bytes(MARKER_CONTENT))


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
