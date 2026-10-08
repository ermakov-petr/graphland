from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

from support import ROOT


def load_yaml(path: Path):
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("Expected YAML mapping")
    return value


def steps(document):
    return [step for job in document["jobs"].values() for step in job.get("steps", [])]


class WorkflowAndIssueFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {name: load_yaml(ROOT / ".github/workflows" / (name + ".yml"))
                         for name in ("leaderboard-validate", "leaderboard-issue-to-pr", "deploy-pages")}
        cls.validate = cls.workflows["leaderboard-validate"]
        cls.issue = cls.workflows["leaderboard-issue-to-pr"]
        cls.deploy = cls.workflows["deploy-pages"]
        cls.form = load_yaml(ROOT / ".github/ISSUE_TEMPLATE/leaderboard-submission.yml")

    def test_form_contract_has_v2_release_and_per_result_protocol_fields(self):
        fields = {item.get("id"): item for item in self.form["body"] if item.get("id")}
        self.assertEqual(self.form["title"], "[Leaderboard submission] ")
        self.assertEqual(self.form["labels"], ["leaderboard-submission"])
        self.assertEqual(set(fields), {"model_name", "model_variant", "github_username", "paper_url",
                         "code_availability", "training_code_url", "data_release", "evaluator_ref",
                         "method_type", "tuning_protocol", "external_data_pretraining", "results", "notes", "confirmations"})
        self.assertEqual(fields["results"]["attributes"]["render"], "csv")
        self.assertIn("num_runs,hparam_trials", fields["results"]["attributes"]["placeholder"])
        confirmations = fields["confirmations"]["attributes"]["options"]
        self.assertEqual(len(confirmations), 6)
        self.assertTrue(all(item.get("required") for item in confirmations))

    def test_required_validate_check_is_strict_for_pr_queue_and_main(self):
        self.assertIn("ready_for_review", self.validate["on"]["pull_request"]["types"])
        self.assertIn("merge_group", self.validate["on"])
        self.assertEqual(self.validate["on"]["push"]["branches"], ["main"])
        self.assertEqual(self.validate["jobs"]["validate"]["name"], "validate")
        runs = "\n".join(step.get("run", "") for step in self.validate["jobs"]["validate"]["steps"])
        self.assertNotIn("--allow-pending", runs)
        self.assertIn("scripts/leaderboard/validate.py", runs)
        self.assertEqual(runs.count("scripts/leaderboard/build.py"), 2)
        self.assertIn("diff -ruN --no-dereference", runs)
        candidate = self.validate["jobs"]["candidate"]
        self.assertIn("draft == true", candidate["if"])
        self.assertIn("--allow-pending", "\n".join(step.get("run", "") for step in candidate["steps"]))

    def test_candidate_read_permission_is_separated_from_mutation(self):
        self.assertEqual(self.issue["permissions"], {})
        candidate, mutation = self.issue["jobs"]["candidate"], self.issue["jobs"]["mutation"]
        self.assertEqual(candidate["permissions"], {"contents": "read", "issues": "read", "pull-requests": "read"})
        self.assertEqual(mutation["permissions"], {"contents": "write", "pull-requests": "write", "issues": "write"})
        self.assertEqual(mutation["needs"], "candidate")
        mutation_checkout = next(step for step in mutation["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(mutation_checkout["with"]["ref"], "main")
        self.assertIn("github.event.issue.state == 'open'", candidate["if"])
        self.assertIn("needs.candidate.result == 'success'", mutation["if"])
        self.assertTrue(any("actions/upload-artifact@" in step.get("uses", "") for step in candidate["steps"]))
        self.assertTrue(any("actions/download-artifact@" in step.get("uses", "") for step in mutation["steps"]))
        for step in mutation["steps"]:
            if "automation.py mutate" in step.get("run", ""):
                self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")

    def test_failed_job_rerun_reuses_the_same_candidate_artifact(self):
        upload = next(step for step in self.issue["jobs"]["candidate"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
        download = next(step for step in self.issue["jobs"]["mutation"]["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
        def resolve(template, attempt):
            return template.replace("${{ github.run_id }}", "123").replace("${{ github.run_attempt }}", str(attempt))
        uploaded = {resolve(upload["with"]["name"], 1): b"original validated artifact"}
        self.assertEqual(uploaded[resolve(download["with"]["name"], 2)], b"original validated artifact")
        self.assertEqual(resolve(upload["with"]["name"], 1), resolve(upload["with"]["name"], 2))
        self.assertIs(upload["with"]["overwrite"], True)

    def test_check_names_are_unique_and_every_main_push_schedules_successor_deploy(self):
        workflows = [load_yaml(path) for path in (ROOT / ".github/workflows").glob("*.yml")]
        names = [job.get("name", identifier) for workflow in workflows for identifier, job in workflow["jobs"].items()]
        self.assertEqual(len(names), len(set(names)))
        # Main freshness includes every commit; a README-only successor cannot be filtered out.
        self.assertNotIn("paths", self.deploy["on"]["push"])
        self.assertNotIn("paths-ignore", self.deploy["on"]["push"])

    def test_issue_text_is_never_interpolated_into_shell_and_prs_are_not_merged(self):
        raw = (ROOT / ".github/workflows/leaderboard-issue-to-pr.yml").read_text()
        self.assertNotIn("pull_request_target", raw)
        runs = "\n".join(step.get("run", "") for step in steps(self.issue))
        for untrusted in ("github.event.issue.body", "github.event.issue.title", "github.event.issue.user.login", "github.event.comment.body"):
            self.assertNotIn(untrusted, runs)
        self.assertNotRegex(runs, r"\bgh\s+pr\s+(?:merge|review)\b")
        self.assertIn("automation.py prepare", runs)
        self.assertIn("automation.py mutate", runs)

    def test_pages_has_named_explicit_rollback_and_three_freshness_guards(self):
        self.assertEqual(self.deploy["on"]["push"]["branches"], ["main"])
        inputs = self.deploy["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["rollback"]["type"], "boolean")
        self.assertFalse(inputs["rollback"]["default"])
        self.assertEqual(inputs["rollback_sha"]["type"], "string")
        self.assertEqual(self.deploy["jobs"]["deploy"]["environment"]["name"], "github-pages")
        self.assertEqual(self.deploy["jobs"]["build"]["permissions"], {"contents": "read"})
        self.assertEqual(self.deploy["jobs"]["deploy"]["permissions"], {"contents": "read", "pages": "write", "id-token": "write"})
        runs = "\n".join(step.get("run", "") for step in steps(self.deploy))
        for command in ("select-source", "build-info", "deploy-check"):
            self.assertIn("automation.py " + command, runs)
        self.assertNotIn("--allow-pending", runs)
        deploy_step = next(step for step in self.deploy["jobs"]["deploy"]["steps"] if step.get("id") == "deployment")
        self.assertIn("freshness.outputs.outcome == 'current'", deploy_step["if"])

    def test_checkouts_do_not_persist_credentials_and_actions_are_sha_pinned(self):
        for name, workflow in self.workflows.items():
            for step in steps(workflow):
                action = step.get("uses", "")
                if not action:
                    continue
                with self.subTest(workflow=name, action=action):
                    self.assertRegex(action, r"^[^@\s]+@[0-9a-f]{40}$")
                    if action.startswith("actions/checkout@"):
                        self.assertIs(step["with"]["persist-credentials"], False)

    def test_dependency_installs_require_hash_locked_binary_packages(self):
        requirements = (ROOT / "requirements-leaderboard.txt").read_text()
        self.assertIn("--require-hashes", requirements)
        self.assertIn("--hash=sha256:", requirements)
        declarations = re.findall(r"^([A-Za-z0-9_-]+)==([^\s]+)", requirements, re.MULTILINE)
        self.assertTrue(declarations)
        for name, workflow in self.workflows.items():
            installs = [step["run"] for step in steps(workflow) if "pip install" in step.get("run", "")]
            self.assertTrue(installs, name)
            self.assertTrue(all("--only-binary=:all:" in command and "--require-hashes" in command for command in installs))


if __name__ == "__main__":
    unittest.main()
