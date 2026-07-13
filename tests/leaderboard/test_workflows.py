from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

from support import ROOT


WORKFLOWS = ROOT / ".github" / "workflows"
ISSUE_FORM = ROOT / ".github" / "ISSUE_TEMPLATE" / "leaderboard-submission.yml"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a YAML mapping")
    return value


def workflow_steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in document["jobs"].values() for step in job.get("steps", [])]


class WorkflowAndIssueFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validate_path = WORKFLOWS / "leaderboard-validate.yml"
        cls.issue_path = WORKFLOWS / "leaderboard-issue-to-pr.yml"
        cls.deploy_path = WORKFLOWS / "deploy-pages.yml"
        cls.validate = load_yaml(cls.validate_path)
        cls.issue = load_yaml(cls.issue_path)
        cls.deploy = load_yaml(cls.deploy_path)
        cls.form = load_yaml(ISSUE_FORM)

    def test_issue_form_has_stable_prefix_label_and_all_parser_fields(self) -> None:
        self.assertEqual(self.form["title"], "[Leaderboard submission] ")
        self.assertEqual(self.form["labels"], ["leaderboard-submission"])
        fields = {item.get("id"): item for item in self.form["body"] if item.get("id")}
        self.assertEqual(
            set(fields),
            {
                "model_name", "model_variant", "github_username", "paper_url",
                "code_availability", "training_code_url", "graphland_ref", "method_type",
                "hparam_trials", "tuning_protocol", "num_runs", "external_data_pretraining",
                "results", "notes", "confirmations",
            },
        )
        self.assertEqual(fields["results"]["attributes"].get("render"), "csv")
        self.assertEqual(fields["code_availability"]["attributes"]["options"], ["available", "unavailable"])
        self.assertEqual(fields["method_type"]["attributes"]["options"], ["trained", "in_context"])

    def test_issue_form_requires_six_public_protocol_confirmations(self) -> None:
        fields = {item.get("id"): item for item in self.form["body"] if item.get("id")}
        options = fields["confirmations"]["attributes"]["options"]
        self.assertEqual(len(options), 6)
        self.assertTrue(all(option.get("required") is True for option in options))
        labels = "\n".join(option["label"] for option in options).lower()
        for concept in ("official graphland", "test labels", "information-access", "published", "self-reported", "secrets"):
            self.assertIn(concept, labels)

    def test_validation_workflow_covers_pr_merge_queue_and_main(self) -> None:
        triggers = self.validate["on"]
        self.assertIn("pull_request", triggers)
        self.assertIn("merge_group", triggers)
        self.assertEqual(triggers["push"]["branches"], ["main"])
        runs = "\n".join(str(step.get("run", "")) for step in workflow_steps(self.validate))
        self.assertIn("unittest discover -s tests/leaderboard", runs)
        self.assertIn("scripts/leaderboard/validate.py", runs)
        self.assertEqual(runs.count("scripts/leaderboard/build.py"), 2)
        self.assertIn("diff -ruN --no-dereference", runs)

    def test_issue_automation_is_label_gated_and_creates_only_a_draft_pr(self) -> None:
        self.assertEqual(self.issue["on"]["issues"]["types"], ["labeled", "edited"])
        job = self.issue["jobs"]["issue-to-pr"]
        gate = str(job["if"])
        self.assertIn("leaderboard-ready", gate)
        self.assertIn("github.event.issue.pull_request == null", gate)
        runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
        self.assertIn("scripts/leaderboard/issue_to_submission.py", runs)
        self.assertIn("scripts/leaderboard/validate.py", runs)
        self.assertIn("scripts/leaderboard/build.py", runs)
        self.assertGreaterEqual(runs.count("--allow-pending"), 2)
        self.assertIn("--draft", runs)
        self.assertIn("Closes #${ISSUE_NUMBER}", runs)
        self.assertNotRegex(runs, r"\bgh\s+pr\s+(?:merge|review)\b")

    def test_issue_automation_uses_fixed_number_derived_branch_and_path(self) -> None:
        env = self.issue["jobs"]["issue-to-pr"]["env"]
        self.assertEqual(env["BRANCH_NAME"], "leaderboard/issue-${{ github.event.issue.number }}")
        self.assertEqual(env["OUTPUT_FILE"], "leaderboard/submissions/issue-${{ github.event.issue.number }}.json")
        runs = "\n".join(str(step.get("run", "")) for step in workflow_steps(self.issue))
        self.assertIn('"${RUNNER_TEMP}/issue.json"', runs)
        self.assertIn("--submissions-dir leaderboard/submissions", runs)

    def test_issue_automation_never_interpolates_untrusted_issue_text_into_shell(self) -> None:
        self.assertNotIn("pull_request_target", self.issue_path.read_text(encoding="utf-8"))
        runs = "\n".join(str(step.get("run", "")) for step in workflow_steps(self.issue))
        for untrusted in (
            "github.event.issue.body",
            "github.event.issue.title",
            "github.event.issue.user.login",
            "github.event.comment.body",
        ):
            self.assertNotIn(untrusted, runs)
        self.assertIn("gh api", runs)
        self.assertIn('> "${RUNNER_TEMP}/issue.json"', runs)

    def test_issue_automation_rejects_fork_pr_branch_collisions(self) -> None:
        runs = "\n".join(str(step.get("run", "")) for step in workflow_steps(self.issue))
        self.assertIn("headRepositoryOwner", runs)
        self.assertIn("isCrossRepository", runs)
        self.assertIn('pull.get("isCrossRepository") is False', runs)
        self.assertIn('pull.get("headRepositoryOwner", {}).get("login") == owner', runs)

    def test_workflows_do_not_persist_checkout_credentials(self) -> None:
        for path in (self.validate_path, self.issue_path, self.deploy_path):
            document = load_yaml(path)
            for step in workflow_steps(document):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    with self.subTest(workflow=path.name):
                        self.assertIs(step.get("with", {}).get("persist-credentials"), False)

    def test_issue_push_receives_token_only_in_the_mutation_step(self) -> None:
        job = self.issue["jobs"]["issue-to-pr"]
        push_step = next(step for step in job["steps"] if step["name"] == "Commit and push validated submission")
        self.assertEqual(push_step["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertIn("https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git", push_step["run"])
        checkout = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
        self.assertIs(checkout["with"]["persist-credentials"], False)

    def test_workflow_permissions_are_explicit_and_scoped(self) -> None:
        self.assertEqual(self.validate["permissions"], {"contents": "read"})
        self.assertEqual(self.issue["permissions"], {})
        self.assertEqual(
            self.issue["jobs"]["issue-to-pr"]["permissions"],
            {"contents": "write", "pull-requests": "write", "issues": "write"},
        )
        self.assertEqual(self.deploy["permissions"], {})
        self.assertEqual(self.deploy["jobs"]["build"]["permissions"], {"contents": "read"})
        self.assertEqual(
            self.deploy["jobs"]["deploy"]["permissions"],
            {"pages": "write", "id-token": "write"},
        )

    def test_pages_workflow_builds_and_deploys_official_static_artifact(self) -> None:
        triggers = self.deploy["on"]
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(self.deploy["concurrency"], {"group": "pages", "cancel-in-progress": False})
        self.assertEqual(self.deploy["jobs"]["deploy"]["environment"]["name"], "github-pages")
        steps = workflow_steps(self.deploy)
        uses = [str(step.get("uses", "")) for step in steps]
        self.assertFalse(any(value.startswith("actions/configure-pages@") for value in uses))
        self.assertTrue(any(value.startswith("actions/upload-pages-artifact@") for value in uses))
        self.assertTrue(any(value.startswith("actions/deploy-pages@") for value in uses))
        runs = "\n".join(str(step.get("run", "")) for step in steps)
        self.assertIn("unittest discover -s tests/leaderboard", runs)
        self.assertIn("scripts/leaderboard/validate.py", runs)
        self.assertIn("scripts/leaderboard/build.py --output _site", runs)
        self.assertNotIn("--allow-pending", runs)

    def test_workflow_dependency_installs_require_prebuilt_pinned_packages(self) -> None:
        requirements = (ROOT / "requirements-leaderboard.txt").read_text(encoding="utf-8")
        package_lines = [line for line in requirements.splitlines() if line and not line.startswith("#")]
        self.assertTrue(package_lines)
        self.assertTrue(all("==" in line for line in package_lines))
        for path in (self.validate_path, self.issue_path, self.deploy_path):
            document = load_yaml(path)
            installs = [
                str(step.get("run", ""))
                for step in workflow_steps(document)
                if "pip install" in str(step.get("run", ""))
            ]
            with self.subTest(workflow=path.name):
                self.assertTrue(installs)
                self.assertTrue(all("--only-binary=:all:" in command for command in installs))

    def test_all_external_actions_are_pinned_to_full_commit_shas(self) -> None:
        for path in (self.validate_path, self.issue_path, self.deploy_path):
            document = load_yaml(path)
            for step in workflow_steps(document):
                action = step.get("uses")
                if not action or str(action).startswith("./"):
                    continue
                with self.subTest(workflow=path.name, action=action):
                    self.assertRegex(str(action), r"^[^@\s]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
