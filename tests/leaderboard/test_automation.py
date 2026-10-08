from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from support import FIXTURES, ROOT, load_module

automation = load_module("leaderboard_automation_tests", "scripts/leaderboard/automation.py")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def issue_fixture() -> dict:
    return {
        "number": 321, "state": "open", "title": "[Leaderboard submission] Fixture Model",
        "body": (FIXTURES / "valid_issue_body.md").read_text(encoding="utf-8"),
        "user": {"login": "fixture-user"}, "created_at": "2026-07-13T12:30:00Z",
        "labels": [{"name": "leaderboard-submission"}, {"name": "leaderboard-ready"}],
    }


class FakeGitHub(automation.GitHub):
    """API fake only; branch contents/pushes use actual isolated Git repositories."""
    def __init__(self, source_sha: str):
        super().__init__("fixture-owner/graphland")
        self.source_sha = source_sha
        self.issue = issue_fixture()
        self.comments = []
        self.pull_records = []
        self.writes = []
        self.fail_create = False
        self.on_prepared = None

    def permission(self, login):
        return {"permission": "write"}

    def pages(self, path):
        return copy.deepcopy(self.pull_records if path.startswith("pulls?") else self.comments)

    def api(self, path, method="GET", payload=None):
        if method != "GET":
            self.writes.append((path, method, copy.deepcopy(payload)))
        if path == "commits/main":
            return {"sha": self.source_sha}
        if path == "issues/321" and method == "GET":
            return copy.deepcopy(self.issue)
        if path.endswith("/labels/leaderboard-ready") and method == "DELETE":
            self.issue["labels"] = [item for item in self.issue["labels"] if item["name"] != "leaderboard-ready"]
            return None
        if path == "pulls" and method == "POST":
            if self.fail_create:
                self.fail_create = False
                raise RuntimeError("Synthetic transient PR-create failure")
            pull = {"number": 6, "state": "open", "draft": True,
                    "head": {"ref": payload["head"], "repo": {"full_name": self.repository}}}
            self.pull_records.append(pull)
            return copy.deepcopy(pull)
        if "/comments" in path and method in {"POST", "PATCH"}:
            if method == "PATCH":
                comment = next(item for item in self.comments if item["id"] == int(path.rsplit("/", 1)[1]))
                comment["body"] = payload["body"]
            else:
                comment = {"id": len(self.comments) + 1, "user": {"login": automation.BOT, "type": "Bot"}, "body": payload["body"]}
                self.comments.append(comment)
            result = copy.deepcopy(comment)
            if '"state":"prepared"' in payload["body"] and self.on_prepared:
                callback, self.on_prepared = self.on_prepared, None
                callback()
            return result
        raise AssertionError((path, method, payload))


class AutomationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="graphland-lifecycle-test-")
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name)
        self.seed = self.temp / "seed"
        self.seed.mkdir()
        self.remote = self.temp / "origin.git"
        git(self.temp, "init", "--bare", str(self.remote))
        git(self.seed, "init", "-b", "main")
        shutil.copytree(ROOT / "leaderboard", self.seed / "leaderboard")
        shutil.copyfile(ROOT / ".gitignore", self.seed / ".gitignore")
        git(self.seed, "config", "user.name", "Synthetic auditor")
        git(self.seed, "config", "user.email", "audit@example.test")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "Synthetic trusted main")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "origin", "main")
        self.source_sha = git(self.seed, "rev-parse", "HEAD")
        self.client = FakeGitHub(self.source_sha)
        self.run_count = 0

    def event(self):
        if "leaderboard-ready" not in automation.labels(self.client.issue):
            self.client.issue["labels"].append({"name": "leaderboard-ready"})
        return {"action": "labeled", "label": {"name": "leaderboard-ready"},
                "sender": {"login": "fixture-maintainer", "type": "User"}, "issue": copy.deepcopy(self.client.issue)}

    def candidate(self, event=None):
        event = event or self.event()
        destination = self.temp / ("candidate-" + str(self.run_count))
        self.run_count += 1
        manifest = automation.prepare(event, self.client.issue, self.client.permission("fixture-maintainer"), destination, self.source_sha)
        self.assertEqual(manifest["outcome"], "ready", manifest)
        return event, destination

    def runner(self):
        destination = self.temp / ("runner-" + str(self.run_count))
        self.run_count += 1
        git(self.temp, "clone", "--branch", "main", str(self.remote), str(destination))
        return destination

    def generate(self):
        event, artifact = self.candidate()
        result = automation.mutate(self.client, event, artifact, self.runner())
        self.assertEqual(result["outcome"], "generated")
        return result

    def remote_head(self):
        return git(self.seed, "ls-remote", "--heads", "origin", "refs/heads/leaderboard/issue-321").split()[0]

    def human_change(self, extra=False):
        human = self.temp / ("human-" + str(self.run_count))
        self.run_count += 1
        git(self.temp, "clone", "--branch", "leaderboard/issue-321", str(self.remote), str(human))
        git(human, "config", "user.name", "Maintainer")
        git(human, "config", "user.email", "reviewer@example.test")
        path = human / "leaderboard/submissions/issue-321.json"
        if extra:
            (human / "human-file.txt").write_text("Human work\n")
        else:
            record = json.loads(path.read_text())
            record["results"][0]["value"] = 0.777
            record["review"] = {"status": "approved", "reviewer_github": "fixture-maintainer", "reviewed_at": "2026-10-08", "notes": "Manual evidence"}
            record["verification"] = "reproduced"
            path.write_bytes(automation.json_bytes(record))
        git(human, "add", ".")
        git(human, "commit", "-m", "Human correction and handover")
        git(human, "push", "origin", "HEAD:refs/heads/leaderboard/issue-321")
        return git(human, "rev-parse", "HEAD")

    def test_generated_draft_consumes_gate_and_writes_one_authenticated_marker(self):
        result = self.generate()
        self.assertEqual(self.remote_head(), result["head_sha"])
        self.assertNotIn("leaderboard-ready", automation.labels(self.client.issue))
        marker, identifier = automation.trusted_marker(self.client.comments, 321)
        self.assertEqual(identifier, 1)
        self.assertEqual(marker["state"], "generated")
        self.assertEqual(marker["head_sha"], result["head_sha"])
        self.assertTrue(self.client.pull_records[0]["draft"])

    def test_human_metadata_and_result_commit_are_never_overwritten(self):
        self.generate()
        human_sha = self.human_change()
        event, artifact = self.candidate()
        writes_before = len(self.client.writes)
        with self.assertRaisesRegex(automation.Skip, "Human commits"):
            automation.mutate(self.client, event, artifact, self.runner())
        self.assertEqual(self.remote_head(), human_sha)
        self.assertEqual(len(self.client.writes), writes_before)

    def test_non_draft_and_closed_pr_are_neutral_frozen_outcomes(self):
        self.generate()
        initial = self.remote_head()
        for update in ({"draft": False}, {"draft": True, "state": "closed"}):
            self.client.pull_records[0].update(update)
            event, artifact = self.candidate()
            with self.assertRaisesRegex(automation.Skip, "handed over|closed"):
                automation.mutate(self.client, event, artifact, self.runner())
            self.assertEqual(self.remote_head(), initial)

    def test_extra_remote_path_is_refused(self):
        self.generate()
        human_sha = self.human_change(extra=True)
        event, artifact = self.candidate()
        with self.assertRaisesRegex(automation.Skip, "unexpected paths"):
            automation.mutate(self.client, event, artifact, self.runner())
        self.assertEqual(self.remote_head(), human_sha)

    def test_transient_pr_create_failure_recovers_without_another_push(self):
        self.client.fail_create = True
        event, artifact = self.candidate()
        with self.assertRaisesRegex(RuntimeError, "transient"):
            automation.mutate(self.client, event, artifact, self.runner())
        original_head = self.remote_head()
        marker, _ = automation.trusted_marker(self.client.comments, 321)
        self.assertEqual(marker["state"], "prepared")
        result = automation.mutate(self.client, event, artifact, self.runner())
        self.assertEqual(result["head_sha"], original_head)
        self.assertEqual(len(self.client.comments), 1)
        self.assertEqual(automation.trusted_marker(self.client.comments, 321)[0]["state"], "generated")

    def test_initial_push_failure_can_retry_prepared_marker_without_branch(self):
        event, artifact = self.candidate()
        real_run = automation.run
        def fail_push(args, *rest, **kwargs):
            if "push" in args:
                raise RuntimeError("Synthetic push failure")
            return real_run(args, *rest, **kwargs)
        with patch.object(automation, "run", side_effect=fail_push):
            with self.assertRaisesRegex(RuntimeError, "push failure"):
                automation.mutate(self.client, event, artifact, self.runner())
        self.assertFalse(git(self.seed, "ls-remote", "--heads", "origin", "refs/heads/leaderboard/issue-321"))
        result = automation.mutate(self.client, event, artifact, self.runner())
        self.assertEqual(self.remote_head(), result["head_sha"])

    def test_force_with_lease_preserves_concurrent_human_commit(self):
        self.generate()
        self.client.issue["body"] += "\n"
        event, artifact = self.candidate()
        human_head = []
        self.client.on_prepared = lambda: human_head.append(self.human_change())
        with self.assertRaises(RuntimeError):
            automation.mutate(self.client, event, artifact, self.runner())
        self.assertEqual(self.remote_head(), human_head[0])

    def test_handover_during_push_discloses_committed_head_and_keeps_pending_marker(self):
        original = self.generate()
        self.client.issue["body"] = self.client.issue["body"].replace("### Additional notes\n\n", "### Additional notes\n\nA newly approved source note. ", 1)
        event, artifact = self.candidate()
        real_run = automation.run
        def handover(args, *rest, **kwargs):
            result = real_run(args, *rest, **kwargs)
            if "push" in args:
                self.client.pull_records[0]["draft"] = False
            return result
        with patch.object(automation, "run", side_effect=handover):
            with self.assertRaisesRegex(automation.Skip, "Branch was already pushed") as failure:
                automation.mutate(self.client, event, artifact, self.runner())
        head = self.remote_head()
        self.assertNotEqual(head, original["head_sha"])
        self.assertIn(head, str(failure.exception))
        marker, _ = automation.trusted_marker(self.client.comments, 321)
        self.assertEqual(marker["state"], "prepared")
        self.assertEqual(marker["head_sha"], head)
        self.assertIn("leaderboard-ready", automation.labels(self.client.issue))
        git(self.seed, "fetch", "origin", "refs/heads/leaderboard/issue-321")
        record = json.loads(git(self.seed, "show", head + ":leaderboard/submissions/issue-321.json"))
        self.assertEqual(record["review"]["status"], "pending")
        self.assertEqual(record["verification"], "self_reported")

    def test_source_changes_before_push_skip_without_creating_branch(self):
        event, artifact = self.candidate()
        self.client.on_prepared = lambda: self.client.issue.update(body=self.client.issue["body"] + "\n")
        with self.assertRaisesRegex(automation.Skip, "changed after the label"):
            automation.mutate(self.client, event, artifact, self.runner())
        self.assertFalse(git(self.seed, "ls-remote", "--heads", "origin", "refs/heads/leaderboard/issue-321"))

    def test_closed_removed_gate_or_revoked_actor_before_push_are_neutral(self):
        for scenario in ("closed", "removed", "revoked"):
            event, artifact = self.candidate()
            def change():
                if scenario == "closed":
                    self.client.issue["state"] = "closed"
                elif scenario == "removed":
                    self.client.issue["labels"] = [{"name": "leaderboard-submission"}]
                else:
                    self.client.permission = lambda _: {"permission": "read"}
            self.client.on_prepared = change
            with self.assertRaises(automation.Skip):
                automation.mutate(self.client, event, artifact, self.runner())
            self.assertFalse(git(self.seed, "ls-remote", "--heads", "origin", "refs/heads/leaderboard/issue-321"))
            self.client.issue["state"] = "open"
            self.client.permission = lambda _: {"permission": "write"}

    def test_repeat_of_identical_pending_snapshot_reuses_head_and_marker(self):
        first = self.generate()
        second = self.generate()
        self.assertEqual(first["head_sha"], second["head_sha"])
        self.assertEqual(len(self.client.comments), 1)
        self.assertEqual(len(self.client.pull_records), 1)

    def test_tampered_artifact_cannot_replace_fresh_parser_output(self):
        event, artifact = self.candidate()
        record = json.loads((artifact / "submission.json").read_text())
        record["results"][0]["value"] = 0.999
        data = automation.json_bytes(record)
        (artifact / "submission.json").write_bytes(data)
        manifest = json.loads((artifact / "manifest.json").read_text())
        manifest["submission_sha256"] = automation.digest(data)
        (artifact / "manifest.json").write_bytes(automation.json_bytes(manifest))
        with self.assertRaisesRegex(ValueError, "freshly parsed"):
            automation.read_candidate(artifact, event, self.client.issue, {"permission": "write"}, self.source_sha)

    def test_extra_symlink_and_oversized_artifacts_are_rejected(self):
        for mutation in ("extra", "symlink", "oversized"):
            event, artifact = self.candidate()
            if mutation == "extra":
                (artifact / "evil.py").write_text("ignored")
            elif mutation == "symlink":
                (artifact / "submission.json").unlink()
                (artifact / "submission.json").symlink_to(FIXTURES / "valid_submission.json")
            else:
                (artifact / "submission.json").write_bytes(b" " * (automation.MAX_JSON + 1))
            with self.assertRaises(ValueError):
                automation.read_candidate(artifact, event, self.client.issue, {"permission": "write"}, self.source_sha)

    def test_bot_marker_forgery_and_human_head_without_marker_are_frozen(self):
        self.generate()
        marker = copy.deepcopy(self.client.comments[0])
        marker["user"] = {"login": "outside-author", "type": "User"}
        self.assertEqual(automation.trusted_marker([marker], 321), (None, None))
        self.client.comments = [marker]
        event, artifact = self.candidate()
        with self.assertRaisesRegex(automation.Skip, "ownership"):
            automation.mutate(self.client, event, artifact, self.runner())

    def test_deleted_generated_branch_is_not_silently_resurrected(self):
        self.generate()
        git(self.seed, "push", "origin", ":refs/heads/leaderboard/issue-321")
        event, artifact = self.candidate()
        with self.assertRaisesRegex(automation.Skip, "missing"):
            automation.mutate(self.client, event, artifact, self.runner())

    def test_notification_is_deduplicated_and_closed_or_stale_events_are_quiet(self):
        event = self.event()
        automation.notify(self.client, event, "Invalid input")
        automation.notify(self.client, event, "Invalid input")
        self.assertEqual(len(self.client.comments), 1)
        self.client.issue["state"] = "closed"
        with self.assertRaises(automation.Skip):
            automation.notify(self.client, event, "Invalid input")
        self.assertEqual(len(self.client.comments), 1)

    def test_fork_pull_collision_and_deleted_head_repository_are_ignored(self):
        self.client.pull_records = [
            {"number": 10, "head": {"ref": "leaderboard/issue-321", "repo": {"full_name": "outsider/graphland"}}},
            {"number": 11, "head": {"ref": "leaderboard/issue-321", "repo": None}},
        ]
        self.assertEqual(self.client.pulls("leaderboard/issue-321"), [])


class GateAndDeployPolicyTests(unittest.TestCase):
    def command(self, args, current_sha, compare_status="ahead", output=None):
        client = unittest.mock.Mock()
        client.api.side_effect = lambda path: {"sha": current_sha} if path == "commits/main" else {"status": compare_status}
        stream = io.StringIO()
        with patch.object(automation, "GitHub", return_value=client), patch.object(automation.sys, "argv", ["automation.py", *args]), patch.object(automation.os, "environ", {"GITHUB_REPOSITORY": "fixture-owner/graphland"}), redirect_stdout(stream):
            self.assertEqual(automation.main(), 0)
        return json.loads(stream.getvalue())

    def test_command_level_rollback_requires_current_main_ancestry(self):
        old, new = "a" * 40, "b" * 40
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "build-info.json"
            args = ["build-info", "--source-sha", old, "--event-name", "workflow_dispatch", "--rollback", "--rollback-sha", old, "--output", str(path)]
            for status in ("behind", "diverged"):
                with self.subTest(status=status):
                    self.assertEqual(self.command(args, new, status)["outcome"], "skip")
                    self.assertFalse(path.exists())
            self.assertEqual(self.command(args, new, "ahead")["outcome"], "current")
            self.assertEqual(json.loads(path.read_text())["commit_sha"], old)
            self.assertTrue(json.loads(path.read_text())["rollback"])

    def test_main_drift_between_selection_and_metadata_writes_nothing(self):
        old, new = "a" * 40, "b" * 40
        selection = self.command(["select-source"], old)
        self.assertEqual(selection["source_sha"], old)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "build-info.json"
            result = self.command(["build-info", "--source-sha", selection["source_sha"], "--output", str(path)], new)
            self.assertEqual(result["outcome"], "skip")
            self.assertFalse(path.exists())

    def test_custom_role_name_alone_cannot_authorize_conversion(self):
        issue = issue_fixture()
        event = {"action": "labeled", "label": {"name": "leaderboard-ready"}, "issue": copy.deepcopy(issue), "sender": {"login": "fixture-maintainer", "type": "User"}}
        for role in ("write", "maintain", "admin"):
            with self.assertRaises(automation.Skip):
                automation.authorize(event, issue, {"permission": "read", "role_name": role})
        self.assertEqual(automation.authorize(event, issue, {"permission": "write", "role_name": "maintain"}), "fixture-maintainer")

    def test_edits_stale_snapshots_closed_issues_and_triage_cannot_generate(self):
        issue = issue_fixture()
        event = {"action": "labeled", "label": {"name": "leaderboard-ready"}, "issue": copy.deepcopy(issue),
                 "sender": {"login": "fixture-maintainer", "type": "User"}}
        cases = [(copy.deepcopy(event), copy.deepcopy(issue), {"permission": "triage"})]
        edited = copy.deepcopy(event); edited["action"] = "edited"
        cases.append((edited, copy.deepcopy(issue), {"permission": "write"}))
        stale = copy.deepcopy(issue); stale["body"] += "\n"
        cases.append((copy.deepcopy(event), stale, {"permission": "write"}))
        closed = copy.deepcopy(issue); closed["state"] = "closed"
        cases.append((copy.deepcopy(event), closed, {"permission": "write"}))
        bot = copy.deepcopy(event); bot["sender"]["type"] = "Bot"
        cases.append((bot, copy.deepcopy(issue), {"permission": "admin"}))
        for ev, current, permission in cases:
            with self.subTest(action=ev["action"], permission=permission, state=current["state"]):
                with self.assertRaises(automation.Skip):
                    automation.authorize(ev, current, permission)
        self.assertEqual(automation.authorize(event, issue, {"permission": "write"}), "fixture-maintainer")

    def test_snapshot_tracks_body_title_author_but_not_labels_profile_or_updated_at(self):
        original = issue_fixture()
        changed = copy.deepcopy(original)
        changed.update(labels=[], updated_at="2026-10-09T00:00:00Z")
        changed["user"]["avatar_url"] = "https://example.test/new-avatar"
        self.assertEqual(automation.snapshot(original), automation.snapshot(changed))
        for key in ("title", "body"):
            changed = copy.deepcopy(original); changed[key] += " changed"
            self.assertNotEqual(automation.snapshot(original), automation.snapshot(changed))

    def test_stale_deploy_requires_explicit_dispatch_exact_rollback_sha(self):
        old, new = "a" * 40, "b" * 40
        automation.deploy_check(new, new, False, "push", "refs/heads/main")
        automation.deploy_check(new, old, True, "workflow_dispatch", "refs/heads/main", old)
        for rollback, event, ref, requested in (
            (False, "push", "refs/heads/main", ""),
            (True, "push", "refs/heads/main", old),
            (True, "workflow_dispatch", "refs/heads/main", new),
            (True, "workflow_dispatch", "refs/heads/other", old),
        ):
            with self.assertRaises(automation.Skip):
                automation.deploy_check(new, old, rollback, event, ref, requested)


if __name__ == "__main__":
    unittest.main()
