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
