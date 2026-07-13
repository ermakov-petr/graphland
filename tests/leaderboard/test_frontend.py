from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser

from support import ROOT


class DocumentInventory(HTMLParser):
    """Small, dependency-free inventory of the static page contract."""

    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if attributes.get("id"):
            self.by_id[str(attributes["id"])] = (tag, attributes)


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        cls.javascript = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
        cls.favicon = (ROOT / "site" / "favicon.svg").read_text(encoding="utf-8")
        cls.document = DocumentInventory()
        cls.document.feed(cls.html)

    def test_required_interactive_elements_have_stable_ids(self) -> None:
        required = {
            "setting-tabs",
            "task-tabs",
            "model-search",
            "code-filter",
            "setting-description",
            "result-summary",
            "leaderboard-table",
            "leaderboard-head",
            "leaderboard-body",
            "empty-state",
            "load-error",
            "model-dialog",
            "dialog-content",
        }
        self.assertEqual(required - self.document.by_id.keys(), set())

    def test_page_has_exact_setting_and_task_tabs_without_aggregate_view(self) -> None:
        settings = [attrs["data-setting"] for _, attrs in self.document.elements if "data-setting" in attrs]
        tasks = [attrs["data-task"] for _, attrs in self.document.elements if "data-task" in attrs]
        self.assertEqual(settings, ["RL", "RH", "TH", "THI"])
        self.assertEqual(
            tasks,
            ["multiclass_node_classification", "binary_node_classification", "node_regression"],
        )
        self.assertNotRegex(self.html, r'data-(?:setting|task)=["\']All["\']')
        self.assertEqual(self.document.by_id["setting-tabs"][1].get("role"), "tablist")
        self.assertEqual(self.document.by_id["task-tabs"][1].get("role"), "tablist")

    def test_static_assets_data_and_csv_use_base_path_safe_relative_urls(self) -> None:
        self.assertIn('href="assets/styles.css"', self.html)
        self.assertIn('src="assets/app.js"', self.html)
        self.assertIn('fetch("data/leaderboard.json"', self.javascript)
        self.assertGreaterEqual(self.html.count('href="leaderboard.csv"'), 2)
        for forbidden in ('href="/assets/', 'src="/assets/', 'fetch("/data/', 'href="/leaderboard.csv'):
            self.assertNotIn(forbidden, self.html + self.javascript)

    def test_user_supplied_content_is_inserted_as_text_not_html(self) -> None:
        for unsafe_sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(unsafe_sink, self.javascript)
        self.assertIn("element.textContent = String(text)", self.javascript)
        self.assertIn("elements.dialogTitle.textContent = submission.model_name", self.javascript)
        self.assertIn("elements.dialogContent.replaceChildren()", self.javascript)

    def test_external_urls_are_https_validated_and_open_safely(self) -> None:
        self.assertIn('parsed.protocol !== "https:"', self.javascript)
        self.assertIn("parsed.username || parsed.password", self.javascript)
        external_links = [
            attrs for tag, attrs in self.document.elements
            if tag == "a" and str(attrs.get("href", "")).startswith("https://")
        ]
        self.assertTrue(external_links)
        for link in external_links:
            if link.get("target") == "_blank":
                self.assertEqual(set(str(link.get("rel", "")).split()), {"noopener", "noreferrer"})

    def test_metric_formatting_preserves_canonical_data_scale(self) -> None:
        self.assertIn('display.style === "percentage" ? 100 : 1', self.javascript)
        self.assertIn('display.style === "percentage" ? "%" : ""', self.javascript)
        self.assertIn("result.value * multiplier", self.javascript)
        self.assertIn("result.std * multiplier", self.javascript)
        self.assertIn('return "—"', self.javascript)
        self.assertIn("±", self.javascript)

    def test_unavailable_precedes_missing_and_missing_values_sort_last(self) -> None:
        result_cell = self.javascript.index("function renderResultCell")
        unavailable = self.javascript.index("!isSettingAvailable(dataset, state.setting)", result_cell)
        result_lookup = self.javascript.index("const result = getResult", result_cell)
        self.assertLess(unavailable, result_lookup)
        self.assertIn('cell.textContent = "N/A"', self.javascript)
        self.assertIn('cell.textContent = "—"', self.javascript)
        self.assertIn("return leftValue.missing ? 1 : -1", self.javascript)

    def test_filtering_sorting_and_deep_link_state_are_client_side(self) -> None:
        self.assertIn("new URLSearchParams(window.location.search)", self.javascript)
        self.assertIn('url.searchParams.set("setting", state.setting)', self.javascript)
        self.assertIn('url.searchParams.set("task", state.task)', self.javascript)
        self.assertIn('submission.code_availability !== "available"', self.javascript)
        self.assertIn("state.search", self.javascript)
        self.assertIn('setAttribute(\n      "aria-sort"', self.javascript)

    def test_accessibility_and_empty_error_states_are_present(self) -> None:
        dialog_tag, dialog_attrs = self.document.by_id["model-dialog"]
        self.assertEqual(dialog_tag, "dialog")
        self.assertEqual(dialog_attrs.get("aria-labelledby"), "dialog-title")
        self.assertEqual(self.document.by_id["load-error"][1].get("role"), "alert")
        self.assertIn('event.key === "ArrowRight"', self.javascript)
        self.assertIn('event.key === "Escape"', self.javascript)
        self.assertIn("state.dialogTrigger.focus()", self.javascript)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)

    def test_only_submit_results_uses_a_chromatic_accent(self) -> None:
        artifacts = self.css + self.html + self.favicon
        yellow = (0xFE, 0xD4, 0x2B)
        for literal in re.findall(r"#[0-9a-fA-F]{3,8}\b", artifacts):
            value = literal[1:]
            if len(value) in {3, 4}:
                value = "".join(character * 2 for character in value)
            red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
            self.assertTrue(
                red == green == blue or (red, green, blue) == yellow,
                f"unexpected chromatic color {literal}",
            )

        for red, green, blue in re.findall(
            r"rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)", artifacts
        ):
            self.assertEqual(red, green, f"unexpected chromatic rgb({red} {green} {blue})")
            self.assertEqual(green, blue, f"unexpected chromatic rgb({red} {green} {blue})")

        self.assertNotIn("--blue", self.css)
        self.assertNotIn("--yellow-soft", self.css)
        self.assertEqual(self.css.count("var(--yellow)"), 1)
        self.assertRegex(
            self.css,
            r"#submit-results-link\s*\{[^}]*background:\s*var\(--yellow\)",
        )
        self.assertEqual(self.html.count('id="submit-results-link"'), 1)
        self.assertNotIn('content="#fed42b"', self.html)

    def test_testable_public_api_exposes_core_pure_behaviors(self) -> None:
        match = re.search(r"window\.GraphLandLeaderboard\s*=\s*Object\.freeze\(\{(?P<body>.*?)\}\);", self.javascript, re.DOTALL)
        self.assertIsNotNone(match)
        exported = {item.strip().rstrip(",") for item in match.group("body").splitlines() if item.strip()}
        self.assertEqual(exported, {"compareRows", "formatMetric", "init", "isSettingAvailable", "safeExternalUrl"})


if __name__ == "__main__":
    unittest.main()
