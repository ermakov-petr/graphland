"""Shared helpers for GraphLand leaderboard tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO_SUBMISSIONS = FIXTURES / "demo_submissions"


def load_module(module_name: str, relative_path: str) -> ModuleType:
    """Load a repository script without requiring package marker files."""

    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fixture_json(name: str) -> Any:
    return load_json(FIXTURES / name)


def valid_submission() -> dict[str, Any]:
    return copy.deepcopy(fixture_json("valid_submission.json"))


def valid_v2_submission() -> dict[str, Any]:
    submission = valid_submission()
    submission["schema_version"] = "2.0"
    submission["data_release"] = "v1"
    submission["evaluator_ref"] = "https://github.com/yandex-research/graphland/tree/7246fe3"
    submission.pop("graphland_ref")
    count = submission.pop("num_runs")
    submission.pop("hparam_trials")
    for result in submission["results"]:
        result["num_runs"] = count
    submission["results"][0]["num_runs"] = 10
    submission["results"][0]["hparam_trials"] = 20
    submission["results"][1]["num_runs"] = 5
    return submission
