"""Import attributable baseline cells from the fixed arXiv v5 HTML snapshot.

Usage: python scripts/leaderboard/import_paper_baselines.py --source paper.html
This is an explicit maintainer operation, never run by Issue automation.
"""
import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_URL = "https://arxiv.org/html/2409.14500v5"
TABLES = {
    "S5.T2.st1.5.1": "RL", "S5.T2.st2.5.1": "RL",
    "A3.T5.st1.5.1": "RH", "A3.T5.st2.5.1": "RH",
    "A3.T6.st1.5.1": "TH", "A3.T6.st2.5.1": "TH",
    "A3.T7.st1.5.1": "THI", "A3.T7.st2.5.1": "THI",
}
MODELS = {"ResMLP": "resmlp", "GraphSAGE": "graphsage", "CatBoost": "catboost"}
FIVE_RUNS = {"pokec-regions", "web-topics", "web-fraud", "web-traffic"}
CELL = re.compile(r"\{\$(-?\d+\.\d+)\$\}\\pm\s*(\d+\.\d+)")


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = {}
        self.table = None
        self.row = None
        self.cell = None
        self.math_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.table = attrs.get("id")
            if self.table in TABLES:
                self.tables[self.table] = []
        elif self.table in TABLES:
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = []
            elif tag == "math" and self.cell is not None:
                # alttext retains the single authoritative LaTeX cell, avoiding
                # duplicated MathML accessibility and rendered text fragments.
                self.cell.append(attrs.get("alttext", ""))
                self.math_depth += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self.table = None
        elif self.table in TABLES:
            if tag == "math":
                self.math_depth = max(0, self.math_depth - 1)
            elif tag in ("td", "th") and self.cell is not None:
                self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.tables[self.table].append(self.row)
                self.row = None

    def handle_data(self, data):
        if self.cell is not None and not self.math_depth:
            self.cell.append(data)


def import_baselines(source, output_dir, ledger_path):
    raw = source.read_bytes()
    parser = Tables()
    parser.feed(raw.decode("utf-8"))
    if set(parser.tables) != set(TABLES):
        raise ValueError("The fixed v5 source is missing expected tables")
    catalog = json.loads((ROOT / "leaderboard/datasets.json").read_text())
    datasets = {item["id"] for item in catalog["datasets"]}
    cells = []
    results = {name: [] for name in MODELS}
    for table_id, setting in TABLES.items():
        rows = parser.tables[table_id]
        header = next(row for row in rows if "hm-categories" in row or "hm-prices" in row)
        columns = header[1:]
        if not set(columns) <= datasets:
            raise ValueError("Unexpected dataset columns")
        for name in MODELS:
            matches = [row for row in rows if row and row[0] == name]
            if len(matches) != 1 or len(matches[0]) != len(header):
                raise ValueError("Missing or ambiguous model row: " + name)
            for dataset, text in zip(columns, matches[0][1:]):
                evidence = {"model": name, "setting": setting, "dataset": dataset,
                            "table": table_id, "source_cell": text}
                if text in {"TLE", "MLE", "RTE"}:
                    evidence["omitted_reason"] = text
                else:
                    match = CELL.fullmatch(re.sub(r"^\\makebox\[[^]]+\]\[r\]", "", text))
                    if match is None:
                        raise ValueError("Unrecognized numerical cell: " + text)
                    # Tables display every metric at 100 times its canonical
                    # scale, including R². Thus 62.66 means raw R² 0.6266.
                    value, std = (float(part) / 100 for part in match.groups())
                    result = {"setting": setting, "dataset": dataset,
                              "value": round(value, 6), "std": round(std, 6),
                              "num_runs": 5 if dataset in FIVE_RUNS else 10}
                    if name == "CatBoost":
                        result["hparam_trials"] = 100
                    results[name].append(result)
                    evidence.update(result)
                cells.append(evidence)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, slug in MODELS.items():
        submission = {
            "schema_version": "2.0", "id": "paper-v5-" + slug,
            "model_name": name, "model_variant": "GraphLand paper v5 baseline",
            "paper_url": "https://arxiv.org/abs/2409.14500v5",
            "code_availability": "available",
            "training_code_url": "https://github.com/gvbazhenov/graphland-baselines" if name == "CatBoost" else "https://github.com/yandex-research/graphland",
            "submitter_github": "ermakov-petr", "provenance": "maintainer_seeded",
            "source_issue": None, "data_release": "v1",
            "evaluator_ref": "arXiv:2409.14500v5 Appendix B; original experiment commit not reported",
            "method_type": "trained",
            "tuning_protocol": "Validation-only model selection as described in Appendix B.3. " + ("100 Optuna Bayesian trials per dataset/setting." if name == "CatBoost" else "Learning-rate/dropout grid plus dataset-specific preprocessing search; total trial counts omitted because the fixed paper does not state each complete budget."),
            "external_data_pretraining": "No external pretraining declared for these baselines in the cited paper.",
            "submitted_at": "2026-10-08", "verification": "self_reported",
            "review": {"status": "approved", "reviewer_github": "ermakov-petr",
                       "reviewed_at": "2026-10-08", "notes": "Source transcription and declared protocol reviewed by the authorized repository assistant. No independent rerun. See leaderboard/sources/paper-v5.json."},
            "notes": "Published source values, not new experiments. Tables 2, 5, 6, 7; all displayed metrics divided by 100 to obtain the canonical scale. Failed TLE/MLE/RTE cells omitted, not scored as zero. Appendix B.3 specifies 5 seeds for the four largest datasets and 10 otherwise. Original experiment commit is not reported.",
            "results": results[name],
        }
        (output_dir / (submission["id"] + ".json")).write_text(json.dumps(submission, indent=2, allow_nan=False) + "\n")
    ledger = {"source_url": SOURCE_URL, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "imported_at": "2026-10-08", "seed_count_source": SOURCE_URL + "#A2.SS3",
              "scale": "All means and deviations divided by 100; R2 stored as raw coefficient.",
              "not_independently_reproduced": True, "cells": cells}
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=2, allow_nan=False) + "\n")
    return {name: len(items) for name, items in results.items()}


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--source", type=Path, required=True)
    args.add_argument("--output-dir", type=Path, default=ROOT / "leaderboard/submissions")
    args.add_argument("--ledger", type=Path, default=ROOT / "leaderboard/sources/paper-v5.json")
    options = args.parse_args()
    print(import_baselines(options.source, options.output_dir, options.ledger))
