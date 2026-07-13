from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from support import DEMO_SUBMISSIONS, ROOT, load_json, load_module, valid_submission


class SubmissionValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validation = load_module("leaderboard_validation_tests", "scripts/leaderboard/validate.py")
        datasets_document = load_json(ROOT / "leaderboard" / "datasets.json")
        cls.datasets = cls.validation.validate_datasets(datasets_document)
        cls.datasets_by_id = {item["id"]: item for item in cls.datasets}
        cls.schema = load_json(ROOT / "leaderboard" / "schema" / "submission.schema.json")

    def assert_invalid(self, submission: dict, message: str) -> None:
        with self.assertRaisesRegex(self.validation.LeaderboardValidationError, message):
            self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

    def test_partial_submission_is_valid(self) -> None:
        submission = valid_submission()
        submission["results"] = submission["results"][:1]
        self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

    def test_task_and_metric_are_not_accepted_in_result_rows(self) -> None:
        submission = valid_submission()
        submission["results"][0]["task"] = "multiclass_node_classification"
        self.assert_invalid(submission, "Additional properties are not allowed")

        submission = valid_submission()
        submission["results"][0]["metric"] = "accuracy"
        self.assert_invalid(submission, "Additional properties are not allowed")

    def test_unknown_dataset_and_setting_are_rejected(self) -> None:
        submission = valid_submission()
        submission["results"][0]["dataset"] = "not-graphland"
        self.assert_invalid(submission, "unknown dataset")

        submission = valid_submission()
        submission["results"][0]["setting"] = "ALL"
        self.assert_invalid(submission, "schema validation failed")

    def test_temporal_results_for_all_four_exceptions_are_rejected(self) -> None:
        for dataset_id in ("city-reviews", "city-roads-M", "city-roads-L", "web-traffic"):
            for setting in ("TH", "THI"):
                with self.subTest(dataset=dataset_id, setting=setting):
                    submission = valid_submission()
                    submission["results"] = [
                        {"setting": setting, "dataset": dataset_id, "value": 0.2}
                    ]
                    self.assert_invalid(submission, "is not available")

    def test_duplicate_dataset_setting_pair_is_rejected(self) -> None:
        submission = valid_submission()
        submission["results"] = [copy.deepcopy(submission["results"][0])] * 2
        self.assert_invalid(submission, "duplicate result")

    def test_accuracy_and_average_precision_use_raw_unit_scale(self) -> None:
        for dataset_id in ("hm-categories", "web-fraud"):
            for accepted in (0, 0.5, 1):
                submission = valid_submission()
                submission["results"] = [{"setting": "RL", "dataset": dataset_id, "value": accepted}]
                self.validation.validate_submission(submission, self.datasets_by_id, self.schema)
            for rejected in (-0.0001, 1.0001, 81.23):
                submission = valid_submission()
                submission["results"] = [{"setting": "RL", "dataset": dataset_id, "value": rejected}]
                self.assert_invalid(submission, r"must be in \[0, 1\]")

    def test_r2_accepts_negative_and_values_above_one_but_not_non_finite(self) -> None:
        for value in (-100.0, -0.25, 0.0, 1.0, 1.1):
            submission = valid_submission()
            submission["results"] = [{"setting": "RL", "dataset": "hm-prices", "value": value}]
            self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

        for value in (math.nan, math.inf, -math.inf):
            submission = valid_submission()
            submission["results"] = [{"setting": "RL", "dataset": "hm-prices", "value": value}]
            self.assert_invalid(submission, "finite")

    def test_standard_deviation_is_optional_finite_and_non_negative(self) -> None:
        submission = valid_submission()
        submission["results"] = [{"setting": "RL", "dataset": "hm-prices", "value": -0.5}]
        self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

        for std, message in ((-0.1, "less than|non-negative"), (math.nan, "finite"), (math.inf, "finite")):
            submission = valid_submission()
            submission["results"] = [
                {"setting": "RL", "dataset": "hm-prices", "value": -0.5, "std": std}
            ]
            self.assert_invalid(submission, message)

    def test_in_context_learning_requires_zero_trials(self) -> None:
        submission = valid_submission()
        submission["method_type"] = "in_context"
        submission["hparam_trials"] = 0
        self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

        submission["hparam_trials"] = 1
        self.assert_invalid(submission, "0 was expected|zero trials")

    def test_code_availability_and_url_must_agree(self) -> None:
        submission = valid_submission()
        submission["code_availability"] = "unavailable"
        submission["training_code_url"] = None
        self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

        submission = valid_submission()
        submission["training_code_url"] = None
        self.assert_invalid(submission, "string")

        submission = valid_submission()
        submission["code_availability"] = "unavailable"
        self.assert_invalid(submission, "null")

    def test_external_urls_must_be_https_without_credentials(self) -> None:
        for url in (
            "http://example.test/paper",
            "https://user:password@example.test/paper",
            "javascript:alert(1)",
            "/relative/paper",
        ):
            with self.subTest(url=url):
                submission = valid_submission()
                submission["paper_url"] = url
                self.assert_invalid(submission, "HTTPS")

    def test_iso_dates_and_review_state_are_cross_validated(self) -> None:
        submission = valid_submission()
        submission["submitted_at"] = "2026-7-3"
        self.assert_invalid(submission, "schema validation failed|ISO date")

        submission = valid_submission()
        submission["review"] = {
            "status": "approved",
            "reviewer_github": "graphml-reviewer",
            "reviewed_at": "2026-07-14",
            "notes": "Format and protocol reviewed.",
        }
        self.validation.validate_submission(submission, self.datasets_by_id, self.schema)

        submission["review"]["reviewer_github"] = None
        self.assert_invalid(submission, "needs a reviewer")

        submission = valid_submission()
        submission["review"]["reviewer_github"] = "graphml-reviewer"
        self.assert_invalid(submission, "pending review cannot name")

    def test_schema_rejects_missing_required_fields_and_unknown_fields(self) -> None:
        submission = valid_submission()
        del submission["tuning_protocol"]
        self.assert_invalid(submission, "required property")

        submission = valid_submission()
        submission["arbitrary"] = "not allowed"
        self.assert_invalid(submission, "Additional properties are not allowed")

    def test_json_loader_rejects_nan_and_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"value": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "non-standard numeric"):
                self.validation.load_json(path)

            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "Could not read JSON"):
                self.validation.load_json(path)

    def test_submission_filename_is_safe_and_matches_id(self) -> None:
        submission = valid_submission()
        self.validation.validate_submission(
            submission,
            self.datasets_by_id,
            self.schema,
            source_path=Path("fixture-model.json"),
        )
        with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "filename must match"):
            self.validation.validate_submission(
                submission,
                self.datasets_by_id,
                self.schema,
                source_path=Path("someone-else.json"),
            )

    def test_submission_discovery_rejects_unsafe_names_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unsafe name.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "Unsafe submission filename"):
                self.validation.discover_submission_paths(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("{}", encoding="utf-8")
            link = root / "issue-1.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks are unavailable on this platform")
            with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "regular JSON files"):
                self.validation.discover_submission_paths(root)

    def test_repository_validation_accepts_fixture_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            submission = valid_submission()
            path = Path(directory) / f"{submission['id']}.json"
            path.write_text(json.dumps(submission), encoding="utf-8")
            validated = self.validation.validate_repository(
                root=ROOT,
                submissions_dir=Path(directory),
                allow_pending=True,
            )
            self.assertEqual([item["id"] for item in validated["submissions"]], ["fixture-model"])

    def test_repository_validation_rejects_pending_records_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            submission = valid_submission()
            path = Path(directory) / f"{submission['id']}.json"
            path.write_text(json.dumps(submission), encoding="utf-8")
            with self.assertRaisesRegex(
                self.validation.LeaderboardValidationError,
                "pending submissions cannot be published",
            ):
                self.validation.validate_repository(root=ROOT, submissions_dir=Path(directory))

            submission["review"] = {
                "status": "approved",
                "reviewer_github": "graphml-reviewer",
                "reviewed_at": "2026-07-13",
                "notes": "Reviewed for publication.",
            }
            path.write_text(json.dumps(submission), encoding="utf-8")
            validated = self.validation.validate_repository(
                root=ROOT,
                submissions_dir=Path(directory),
            )
            self.assertEqual([item["id"] for item in validated["submissions"]], ["fixture-model"])

    def test_demo_submission_catalog_is_valid_and_exercises_ui_states(self) -> None:
        validated = self.validation.validate_repository(
            root=ROOT,
            submissions_dir=DEMO_SUBMISSIONS,
            allow_pending=True,
        )
        submissions = validated["submissions"]
        self.assertEqual(
            [item["id"] for item in submissions],
            ["demo-atlas", "demo-beacon", "demo-context", "demo-delta"],
        )
        self.assertTrue(all(item["model_name"].startswith("Demo ") for item in submissions))
        self.assertTrue(all("not benchmark claims" in str(item["notes"]) for item in submissions))
        self.assertEqual(sum(len(item["results"]) for item in submissions), 48)
        self.assertEqual(
            {item["code_availability"] for item in submissions},
            {"available", "unavailable"},
        )
        self.assertEqual(
            {item["provenance"] for item in submissions},
            {"author_submission", "maintainer_seeded"},
        )
        self.assertEqual(
            {item["verification"] for item in submissions},
            {"self_reported", "reproduced"},
        )
        self.assertEqual(
            {item["method_type"] for item in submissions},
            {"trained", "in_context"},
        )
        self.assertEqual(
            {item["review"]["status"] for item in submissions},
            {"pending", "approved"},
        )

        results = [result for item in submissions for result in item["results"]]
        self.assertEqual({result["setting"] for result in results}, {"RL", "RH", "TH", "THI"})
        self.assertEqual({result["dataset"] for result in results}, set(self.datasets_by_id))
        self.assertEqual(
            {self.datasets_by_id[result["dataset"]]["task"] for result in results},
            {
                "multiclass_node_classification",
                "binary_node_classification",
                "node_regression",
            },
        )
        self.assertEqual(
            {
                (result["setting"], self.datasets_by_id[result["dataset"]]["task"])
                for result in results
            },
            {
                (setting, task)
                for setting in ("RL", "RH", "TH", "THI")
                for task in (
                    "multiclass_node_classification",
                    "binary_node_classification",
                    "node_regression",
                )
            },
        )
        self.assertTrue(any("std" not in result for result in results))
        self.assertTrue(
            any(
                self.datasets_by_id[result["dataset"]]["metric"] == "r2"
                and result["value"] < 0
                for result in results
            )
        )
        for submission in submissions:
            self.assertFalse((ROOT / "leaderboard" / "submissions" / f"{submission['id']}.json").exists())


if __name__ == "__main__":
    unittest.main()
