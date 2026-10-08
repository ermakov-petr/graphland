from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
import os
import zipfile
from unittest import mock
from xml.etree import ElementTree as ET
from pathlib import Path

from support import DEMO_SUBMISSIONS, ROOT, load_json, load_module, valid_submission, valid_v2_submission


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class BuildTests(unittest.TestCase):
    def test_source_overlap_and_unowned_output_are_refused_without_mutation(self) -> None:
        for output in (ROOT, ROOT.parent, ROOT / "site", ROOT / "leaderboard", ROOT / ".git", ROOT / "site/nested"):
            with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unsafe build output"):
                self.build._safe_output_path(output)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "unrelated"
            output.mkdir()
            sentinel = output / "user.txt"
            sentinel.write_text("preserve")
            with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unowned"):
                self.build._safe_output_path(output)
            self.assertEqual(sentinel.read_text(), "preserve")
            copied_root = root / "repo"
            with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unsafe"):
                self.build._safe_output_path(copied_root / "site", root=copied_root)
            self.assertEqual(self.build._safe_output_path(copied_root / "_site", root=copied_root), (copied_root / "_site").resolve())

    def test_staged_write_and_swap_failures_preserve_last_good_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submissions = root / "submissions"
            submissions.mkdir()
            output = root / "output"
            output.mkdir()  # A caller may supply an initially empty directory.
            self.build.build_site(output, submissions_dir=submissions)
            before = tree_digest(output)
            with mock.patch.object(self.build.shutil, "copy2", side_effect=OSError("write failed")):
                with self.assertRaisesRegex(OSError, "write failed"):
                    self.build.build_site(output, submissions_dir=submissions)
            self.assertEqual(tree_digest(output), before)
            real_replace = os.replace
            def fail_staged_swap(source, target):
                if ".build-" in Path(source).name:
                    raise OSError("swap failed")
                return real_replace(source, target)
            with mock.patch.object(self.build.os, "replace", side_effect=fail_staged_swap):
                with self.assertRaisesRegex(OSError, "swap failed"):
                    self.build.build_site(output, submissions_dir=submissions)
            self.assertEqual(tree_digest(output), before)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["output", "submissions"])
            self.build.build_site(output, submissions_dir=submissions)
            self.assertEqual(tree_digest(output), before)

    def test_xlsx_keeps_formula_like_text_as_strings_and_metrics_numeric(self) -> None:
        submission = valid_submission()
        submission.update(model_name="=1+1", model_variant="+SUM(1,2)")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "leaderboard.xlsx"
            self.build.write_xlsx(target, [submission], self.datasets_by_id)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                document = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                self.assertEqual(document.findall(".//s:f", ns), [])
                model = document.find(".//s:c[@r='B2']", ns)
                variant = document.find(".//s:c[@r='C2']", ns)
                self.assertEqual(model.get("t"), "inlineStr")
                self.assertEqual(model.find("s:is/s:t", ns).text, "=1+1")
                self.assertEqual(variant.find("s:is/s:t", ns).text, "+SUM(1,2)")
                score = document.find(".//s:c[@r='H2']", ns)
                self.assertNotEqual(score.get("t"), "inlineStr")
                self.assertEqual(float(score.find("s:v", ns).text), 0.8123)

    def test_v2_exports_effective_counts_without_inventing_unknown_budgets(self) -> None:
        submission = valid_v2_submission()
        rows = self.build.csv_rows([submission], self.datasets_by_id)
        by_dataset = {row["dataset"]: row for row in rows}
        self.assertEqual(by_dataset["hm-categories"]["num_runs"], 10)
        self.assertEqual(by_dataset["web-fraud"]["num_runs"], 5)
        self.assertEqual(by_dataset["web-fraud"]["hparam_trials"], "")
        normalized = self.build.normalized_submissions([valid_submission(), submission])
        self.assertEqual(normalized[0]["results"][0]["num_runs"], 3)
        self.assertEqual(normalized[0]["results"][0]["hparam_trials"], 4)
        self.assertNotIn("hparam_trials", normalized[1]["results"][1])
        self.assertNotIn("num_runs", valid_submission()["results"][0])

    def test_json_serializer_refuses_nonstandard_numeric_constants(self) -> None:
        with self.assertRaisesRegex(self.build.LeaderboardValidationError, "non-finite"):
            self.build._json_bytes({"value": float("inf")})
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
                "leaderboard.xlsx",
                ".graphland-build.json",
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

    def test_temporary_demo_data_is_removable_without_build_or_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            fixture_output = temp / "fixture-preview"
            production_submissions = temp / "production-submissions"
            production_submissions.mkdir()
            published_output = temp / "published"
            removed_output = temp / "removed"

            fixture_validated = self.build.build_site(
                fixture_output,
                root=ROOT,
                submissions_dir=DEMO_SUBMISSIONS,
                allow_pending=True,
            )
            demo_ids = {item["id"] for item in fixture_validated["submissions"]}
            self.assertEqual(
                demo_ids,
                {"demo-atlas", "demo-beacon", "demo-context", "demo-delta"},
            )
            self.assertTrue(all(submission_id.startswith("demo-") for submission_id in demo_ids))

            for source in sorted(DEMO_SUBMISSIONS.glob("demo-*.json")):
                submission = load_json(source)
                submission["review"] = {
                    "status": "approved",
                    "reviewer_github": "demo-reviewer",
                    "reviewed_at": "2026-07-13",
                    "notes": "Synthetic fixture approved only for temporary public UI QA.",
                }
                (production_submissions / source.name).write_text(
                    json.dumps(submission, indent=2) + "\n",
                    encoding="utf-8",
                )

            published_validated = self.build.build_site(
                published_output,
                root=ROOT,
                submissions_dir=production_submissions,
            )
            published_payload = load_json(published_output / "data" / "leaderboard.json")
            self.assertEqual(
                {item["id"] for item in published_validated["submissions"]},
                demo_ids,
            )
            self.assertEqual(
                {item["id"] for item in published_payload["submissions"]},
                demo_ids,
            )
            with (published_output / "leaderboard.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 48)

            for path in production_submissions.glob("demo-*.json"):
                path.unlink()

            removed_validated = self.build.build_site(
                removed_output,
                root=ROOT,
                submissions_dir=production_submissions,
            )
            removed_payload = load_json(removed_output / "data" / "leaderboard.json")
            self.assertEqual(removed_validated["submissions"], [])
            self.assertEqual(removed_payload["submissions"], [])
            with (removed_output / "leaderboard.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])
            self.assertIn(
                "No results yet",
                (removed_output / "index.html").read_text(encoding="utf-8"),
            )

    def test_site_sources_pass_project_pages_asset_check(self) -> None:
        self.build._check_relative_asset_paths(ROOT / "site")
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="leaderboard.csv"', index)
        self.assertIn('src="assets/app.js"', index)
        self.assertIn('href="assets/styles.css"', index)
        self.assertNotIn('href="/leaderboard.csv"', index)
        self.assertNotIn('src="/assets/', index)

    def _write_asset_url_fixture(self, site: Path, location: str, expression: str) -> None:
        css = f".example {{ background-image: {expression}; }}"
        (site / "assets").mkdir(exist_ok=True)
        (site / "assets" / "app.js").write_text("", encoding="utf-8")
        (site / "assets" / "styles.css").write_text(
            css if location == "stylesheet" else "", encoding="utf-8"
        )
        if location == "style_element":
            html = f"<style>{css}</style>"
        elif location == "style_attribute":
            quote = "'" if '"' in expression else '"'
            html = f"<div style={quote}background-image: {expression};{quote}></div>"
        else:
            html = "<html></html>"
        (site / "index.html").write_text(html, encoding="utf-8")

    def test_root_absolute_css_urls_are_rejected_in_stylesheet_and_inline_html(self) -> None:
        expressions = (
            "url(/fonts/example.woff2)",
            'url("/fonts/example.woff2")',
            "url('/assets/brand/example.svg')",
            "url( /fonts/example.woff2 )",
            'url(  "/assets/brand/example.svg"  )',
            "url(\n\t'/fonts/example.woff2'\n)",
            "URL( '/fonts/example.woff2' )",
            'url(" /fonts/example.woff2")',
            "url(' \t /assets/brand/example.svg ')",
            'url("\t/fonts/example.woff2")',
        )
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            for location in ("stylesheet", "style_element", "style_attribute"):
                for expression in expressions:
                    with self.subTest(location=location, expression=expression):
                        self._write_asset_url_fixture(site, location, expression)
                        expected_path = "assets/styles.css" if location == "stylesheet" else "index.html"
                        with self.assertRaisesRegex(
                            self.build.LeaderboardValidationError, expected_path
                        ):
                            self.build._check_relative_asset_paths(site)

    def test_relative_and_protocol_relative_css_urls_remain_allowed(self) -> None:
        expressions = (
            "url(assets/brand/ys-text-regular.woff2)",
            'url("brand/merriweather-light.woff2")',
            "url( './brand/research.svg' )",
            'url( "../images/example.svg" )',
            "url(//cdn.example.org/example.woff2)",
            'url( "//cdn.example.org/example.svg" )',
            "URL( '//cdn.example.org/example.woff2' )",
            'url(" ./brand/research.svg")',
            "url(' //cdn.example.org/example.woff2')",
            'url("\u00a0/fonts/example.woff2")',
        )
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            for location in ("stylesheet", "style_element", "style_attribute"):
                for expression in expressions:
                    with self.subTest(location=location, expression=expression):
                        self._write_asset_url_fixture(site, location, expression)
                        self.build._check_relative_asset_paths(site)

    def test_unsafe_output_paths_are_refused(self) -> None:
        with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unsafe build output"):
            self.build._safe_output_path(ROOT)
        with self.assertRaisesRegex(self.build.LeaderboardValidationError, "unsafe build output"):
            self.build._safe_output_path(Path.home())


if __name__ == "__main__":
    unittest.main()
