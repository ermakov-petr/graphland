from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import FIXTURES, ROOT, load_module


EXPECTED_HEADINGS = [
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
]


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def valid_issue(body: str | None = None) -> dict:
    return {
        "number": 321,
        "state": "open",
        "title": "[Leaderboard submission] Fixture Model",
        "body": body if body is not None else fixture_text("valid_issue_body.md"),
        "user": {"login": "fixture-user"},
        "created_at": "2026-07-13T12:30:00Z",
        "labels": [
            {"name": "leaderboard-submission"},
            {"name": "leaderboard-ready"},
        ],
    }


class IssueParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parser = load_module(
            "leaderboard_issue_parser_tests", "scripts/leaderboard/issue_to_submission.py"
        )

    def assert_issue_invalid(self, issue: dict, message: str = ".*") -> None:
        with self.assertRaisesRegex(self.parser.IssueSubmissionError, message):
            self.parser.submission_from_issue(issue)

    def test_heading_parser_accepts_exact_ordered_form(self) -> None:
        fields = self.parser.parse_issue_body(fixture_text("valid_issue_body.md"))
        self.assertEqual(list(fields), EXPECTED_HEADINGS)
        self.assertEqual(fields["Model name"], "Fixture Model")
        self.assertIn("setting,dataset,value,std", fields["Results"])

    def test_valid_issue_maps_to_a_pending_self_reported_submission(self) -> None:
        submission = self.parser.submission_from_issue(valid_issue())
        self.assertEqual(submission["id"], "issue-321")
        self.assertEqual(submission["submitter_github"], "fixture-user")
        self.assertEqual(submission["source_issue"], 321)
        self.assertEqual(submission["submitted_at"], "2026-07-13")
        self.assertEqual(submission["verification"], "self_reported")
        self.assertEqual(submission["provenance"], "author_submission")
        self.assertEqual(
            submission["review"],
            {"status": "pending", "reviewer_github": None, "reviewed_at": None, "notes": None},
        )
        self.assertEqual(
            [(row["setting"], row["dataset"]) for row in submission["results"]],
            [("RL", "hm-categories"), ("RH", "web-fraud"), ("THI", "web-topics"), ("RL", "hm-prices")],
        )
        self.assertNotIn("std", submission["results"][2])
        self.assertEqual(submission["results"][3]["value"], -0.125)

    def test_unavailable_code_normalizes_no_response_to_null(self) -> None:
        body = fixture_text("valid_issue_body.md")
        body = body.replace("available\n\n### Training", "unavailable\n\n### Training")
        body = body.replace("https://example.test/code", "_No response_")
        submission = self.parser.submission_from_issue(valid_issue(body))
        self.assertEqual(submission["code_availability"], "unavailable")
        self.assertIsNone(submission["training_code_url"])

    def test_in_context_requires_zero_trials(self) -> None:
        body = fixture_text("valid_issue_body.md")
        body = body.replace("trained\n\n### Hyperparameter", "in_context\n\n### Hyperparameter")
        self.assert_issue_invalid(valid_issue(body), "trial|in-context|in_context")
        body = body.replace("### Hyperparameter trials\n\n4", "### Hyperparameter trials\n\n0")
        submission = self.parser.submission_from_issue(valid_issue(body))
        self.assertEqual(submission["method_type"], "in_context")
        self.assertEqual(submission["hparam_trials"], 0)

    def test_missing_duplicate_unknown_or_reordered_headings_are_rejected(self) -> None:
        original = fixture_text("valid_issue_body.md")
        missing = original.replace("### Additional notes", "### Missing additional notes")
        with self.assertRaises(self.parser.IssueSubmissionError):
            self.parser.parse_issue_body(missing)

        duplicate = original + "\n### Model name\n\nDuplicate\n"
        with self.assertRaises(self.parser.IssueSubmissionError):
            self.parser.parse_issue_body(duplicate)

        first, second = "### Model name", "### Model variant or version"
        reordered = original.replace(first, "### TEMP", 1).replace(second, first, 1).replace("### TEMP", second, 1)
        with self.assertRaises(self.parser.IssueSubmissionError):
            self.parser.parse_issue_body(reordered)

    def test_results_require_a_fenced_csv_with_exact_header(self) -> None:
        original = fixture_text("valid_issue_body.md")
        for body in (
            original.replace("```csv", "```text", 1),
            original.replace("setting,dataset,value,std", "dataset,setting,value,std", 1),
            original.replace("setting,dataset,value,std", "setting,dataset,value", 1),
        ):
            with self.subTest(body=body[:80]):
                self.assert_issue_invalid(valid_issue(body), "CSV|csv|header|fence")

    def test_invalid_result_semantics_are_rejected(self) -> None:
        self.assert_issue_invalid(
            valid_issue(fixture_text("invalid_issue_body.md")), "not available|temporal|TH"
        )
        original = fixture_text("valid_issue_body.md")
        mutations = [
            ("RL,hm-categories,0.8123,0.0041", "RL,not-a-dataset,0.5,"),
            ("RL,hm-categories,0.8123,0.0041", "RL,hm-categories,81.23,"),
            ("RL,hm-categories,0.8123,0.0041", "RL,hm-categories,0.5,-0.1"),
            ("RL,hm-categories,0.8123,0.0041", "RL,hm-categories,nan,"),
            (
                "RL,hm-categories,0.8123,0.0041",
                "RL,hm-categories,0.5,\nRL,hm-categories,0.6,",
            ),
        ]
        for old, new in mutations:
            with self.subTest(new=new):
                self.assert_issue_invalid(valid_issue(original.replace(old, new, 1)))

    def test_all_six_confirmations_must_be_checked(self) -> None:
        body = fixture_text("valid_issue_body.md").replace("- [x] I did not use test labels", "- [ ] I did not use test labels")
        self.assert_issue_invalid(valid_issue(body), "confirmation|checked")

    def test_issue_gate_state_title_author_and_timestamp_are_validated(self) -> None:
        cases = []
        issue = valid_issue()
        issue["state"] = "closed"
        cases.append(issue)
        issue = valid_issue()
        issue["title"] = "Fixture Model"
        cases.append(issue)
        issue = valid_issue()
        issue["labels"] = ["leaderboard-submission"]
        cases.append(issue)
        issue = valid_issue()
        issue["created_at"] = "2026-07-13"
        cases.append(issue)
        issue = valid_issue()
        issue["user"] = {"login": "different-user"}
        cases.append(issue)
        for issue in cases:
            with self.subTest(issue=issue):
                self.assert_issue_invalid(issue)

    def test_shell_like_user_text_remains_data_and_never_controls_output_path(self) -> None:
        issue = valid_issue(fixture_text("security_issue_body.md"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.parser.write_submission(issue, root)
            self.assertEqual(output, (root / "issue-321.json").resolve())
            self.assertTrue(output.is_file())
            self.assertEqual([path.name for path in root.iterdir()], ["issue-321.json"])
            submission = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(submission["id"], "issue-321")
            self.assertIn("../../", submission["model_name"])
            self.assertIn("<script>", submission["notes"])
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())

    def test_issue_number_not_user_text_determines_filename(self) -> None:
        for number in (0, -1, True, "1", 1.5):
            issue = valid_issue()
            issue["number"] = number
            with self.subTest(number=number):
                self.assert_issue_invalid(issue, "number|positive|integer")

    def test_cli_writes_only_issue_number_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issue_path = root / "issue.json"
            output_dir = root / "submissions"
            issue_path.write_text(json.dumps(valid_issue()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "leaderboard" / "issue_to_submission.py"),
                    "--issue-json",
                    str(issue_path),
                    "--submissions-dir",
                    str(output_dir),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual([path.name for path in output_dir.iterdir()], ["issue-321.json"])


if __name__ == "__main__":
    unittest.main()
