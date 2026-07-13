from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from support import DEMO_SUBMISSIONS, ROOT, load_json, load_module, valid_submission


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = load_module("leaderboard_build_tests", "scripts/leaderboard/build.py")
        cls.datasets_document = load_json(ROOT / "leaderboard" / "datasets.json")
        cls.datasets_by_id = {item["id"]: item for item in cls.datasets_document["datasets"]}

    def test_csv_has_exact_stable_columns(self) -> None:
        self.assertEqual(
            self.build.CSV_COLUMNS,
            [
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
            ],
        )

    def test_csv_derives_task_and_metric_from_metadata(self) -> None:
        submission = valid_submission()
        rows = self.build.csv_rows([submission], self.datasets_by_id)
        self.assertEqual(len(rows), len(submission["results"]))
        for row in rows:
            metadata = self.datasets_by_id[row["dataset"]]
            self.assertEqual(row["task"], metadata["task"])
            self.assertEqual(row["metric"], metadata["metric"])
        r2_row = next(row for row in rows if row["dataset"] == "hm-prices")
        self.assertEqual(r2_row["value"], -0.125)
        missing_std_row = next(row for row in rows if row["dataset"] == "web-topics")
        self.assertEqual(missing_std_row["std"], "")

    def test_csv_rows_are_deterministically_ordered(self) -> None:
        first = valid_submission()
        second = valid_submission()
        first["id"] = "z-model"
        second["id"] = "a-model"
        rows = self.build.csv_rows([first, second], self.datasets_by_id)
        self.assertEqual([row["submission_id"] for row in rows[:4]], ["a-model"] * 4)
        self.assertEqual(
            [(row["setting"], row["dataset"]) for row in rows[:4]],
            [
                ("RL", "hm-categories"),
                ("RL", "hm-prices"),
                ("RH", "web-fraud"),
                ("THI", "web-topics"),
            ],
        )

    def test_csv_writer_uses_header_and_empty_fields(self) -> None:
        submission = valid_submission()
        submission["code_availability"] = "unavailable"
        submission["training_code_url"] = None
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "leaderboard.csv"
            self.build.write_csv(destination, [submission], self.datasets_by_id)
            raw = destination.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            with destination.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(list(rows[0]), self.build.CSV_COLUMNS)
            self.assertTrue(all(row["code_url"] == "" for row in rows))
            self.assertEqual(next(row for row in rows if row["dataset"] == "web-topics")["std"], "")

    def test_full_build_contains_complete_pages_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            submissions = temp / "submissions"
            submissions.mkdir()
            submission = valid_submission()
            (submissions / f"{submission['id']}.json").write_text(
                json.dumps(submission), encoding="utf-8"
            )
            output = temp / "site"
            validated = self.build.build_site(
                output,
                root=ROOT,
                submissions_dir=submissions,
                allow_pending=True,
            )

            expected = {
                ".nojekyll",
                "index.html",
                "favicon.svg",
                "assets/styles.css",
                "assets/app.js",
                "data/leaderboard.json",
                "leaderboard.csv",
                "schema/submission.schema.json",
            }
            actual = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
            self.assertTrue(expected.issubset(actual))
            self.assertEqual((output / ".nojekyll").read_bytes(), b"")
            payload = load_json(output / "data" / "leaderboard.json")
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertEqual(len(payload["datasets"]), 14)
            self.assertEqual([item["id"] for item in payload["submissions"]], ["fixture-model"])
            self.assertEqual(len(validated["submissions"]), 1)

    def test_build_is_byte_for_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            submissions = temp / "submissions"
            submissions.mkdir()
            submission = valid_submission()
            (submissions / f"{submission['id']}.json").write_text(
                json.dumps(submission, indent=2), encoding="utf-8"
            )
            first = temp / "first"
            second = temp / "second"
            self.build.build_site(
                first,
                root=ROOT,
                submissions_dir=submissions,
                allow_pending=True,
            )
            self.build.build_site(
                second,
                root=ROOT,
                submissions_dir=submissions,
                allow_pending=True,
            )
            self.assertEqual(tree_digest(first), tree_digest(second))

    def test_empty_build_has_no_invented_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            submissions = temp / "submissions"
            submissions.mkdir()
            output = temp / "site"
            self.build.build_site(output, root=ROOT, submissions_dir=submissions)
            payload = load_json(output / "data" / "leaderboard.json")
            self.assertEqual(payload["submissions"], [])
            with (output / "leaderboard.csv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [])
            self.assertIn("No results yet", (output / "index.html").read_text(encoding="utf-8"))

    def test_demo_submissions_are_opt_in_and_never_leak_into_default_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            demo_output = temp / "demo"
            default_output = temp / "default"

            demo_validated = self.build.build_site(
                demo_output,
                root=ROOT,
                submissions_dir=DEMO_SUBMISSIONS,
                allow_pending=True,
            )
            self.build.build_site(default_output, root=ROOT)

            demo_ids = {item["id"] for item in demo_validated["submissions"]}
            self.assertEqual(
                demo_ids,
                {"demo-atlas", "demo-beacon", "demo-context", "demo-delta"},
            )
            self.assertTrue(all(submission_id.startswith("demo-") for submission_id in demo_ids))

            demo_payload = load_json(demo_output / "data" / "leaderboard.json")
            default_payload = load_json(default_output / "data" / "leaderboard.json")
            self.assertEqual(
                {item["id"] for item in demo_payload["submissions"]},
                demo_ids,
            )
            self.assertTrue(
                demo_ids.isdisjoint(item["id"] for item in default_payload["submissions"])
            )
            with (demo_output / "leaderboard.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 48)

    def test_site_sources_pass_project_pages_asset_check(self) -> None:
        self.build._check_relative_asset_paths(ROOT / "site")
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="leaderboard.csv"', index)
        self.assertIn('src="assets/app.js"', index)
        self.assertIn('href="assets/styles.css"', index)
        self.assertNotIn('href="/leaderboard.csv"', index)
        self.assertNotIn('src="/assets/', index)

    def test_unsafe_output_paths_are_refused(self) -> None:
        with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unsafe build output"):
            self.build._safe_output_path(ROOT)
        with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unsafe build output"):
            self.build._safe_output_path(Path.home())


if __name__ == "__main__":
    unittest.main()
