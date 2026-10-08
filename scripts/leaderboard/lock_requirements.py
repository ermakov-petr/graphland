"""Regenerate portable wheel hashes from exact-version PyPI metadata.

The output is checked in and CI uses --require-hashes --only-binary=:all:.
Maintainers review version changes before running this networked command.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def generate(source):
    lines = ["# Generated from requirements-leaderboard.in by lock_requirements.py.",
             "# All supported wheel hashes retained for portable CI; source distributions excluded.",
             "--require-hashes", "--only-binary=:all:"]
    for line in source.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+)==([0-9][A-Za-z0-9.+-]*)", line)
        if not match:
            raise ValueError("Only exact pinned package lines are supported")
        package, version = match.groups()
        with urlopen("https://pypi.org/pypi/" + package + "/" + version + "/json", timeout=30) as response:
            metadata = json.load(response)
        hashes = sorted({wheel["digests"]["sha256"] for wheel in metadata["urls"] if wheel["packagetype"] == "bdist_wheel"})
        if not hashes or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
            raise ValueError("Missing or invalid published wheel hashes")
        lines.append(line + " \\")
        lines.extend("    --hash=sha256:" + value + (" \\" if index < len(hashes) - 1 else "") for index, value in enumerate(hashes))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "requirements-leaderboard.in")
    parser.add_argument("--output", type=Path, default=ROOT / "requirements-leaderboard.txt")
    args = parser.parse_args()
    args.output.write_text(generate(args.input))
