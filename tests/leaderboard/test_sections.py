"""Dependency-free content contracts for the four physical GraphLand pages."""
from __future__ import annotations

import ast
import builtins
import re
import unittest
from html.parser import HTMLParser

from support import ROOT, load_json
from test_frontend import DocumentInventory


PAGES = {"index.html": ("home", "Home", "GraphLand"),
         "datasets.html": ("datasets", "Datasets", "Datasets"),
         "leaderboard.html": ("leaderboard", "Leaderboard", "Leaderboard"),
         "getting-started.html": ("getting-started", "Getting Started", "Getting Started")}


class CodeInventory(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "code" and "language-python" in dict(attrs).get("class", "").split():
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "code" and self.current is not None:
            self.blocks.append("".join(self.current))
            self.current = None


class SectionsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {path: (ROOT / "site" / path).read_text(encoding="utf-8") for path in PAGES}
        cls.inventories = {}
        for path, source in cls.pages.items():
            inventory = DocumentInventory()
            inventory.feed(source)
            cls.inventories[path] = inventory
        cls.dataset_ids = {dataset["id"] for dataset in load_json(ROOT / "leaderboard/datasets.json")["datasets"]}

    def test_four_routes_have_one_heading_native_navigation_and_unique_ids(self):
        for path, (marker, label, heading) in PAGES.items():
            with self.subTest(page=path):
                source = self.pages[path]
                inventory = self.inventories[path]
                bodies = [attrs for tag, attrs in inventory.elements if tag == "body"]
                self.assertEqual(len(bodies), 1)
                self.assertEqual(bodies[0].get("data-page"), marker)
                h1s = re.findall(r"<h1\b[^>]*>(.*?)</h1>", source, re.DOTALL | re.IGNORECASE)
                self.assertEqual(len(h1s), 1)
                self.assertEqual(heading, re.sub(r"<[^>]+>", "", h1s[0]).strip())
                intro = re.search(r'<section\b[^>]*class="[^"]*page-intro[^\"]*"[^>]*>.*?</section>', source, re.DOTALL)
                self.assertIsNotNone(intro)
                self.assertEqual(len(re.findall(r"<h1\b", intro.group())), 1)
                intro_inventory = DocumentInventory()
                intro_inventory.feed(intro.group())
                leads = [attrs for tag, attrs in intro_inventory.elements
                         if tag == "p" and "page-lead" in str(attrs.get("class", "")).split()]
                self.assertEqual(len(leads), 1)
                nav = re.search(r'<nav\b[^>]*class="[^"]*project-navigation[^\"]*"[^>]*>.*?</nav>', source, re.DOTALL)
                self.assertIsNotNone(nav)
                header = re.search(r"<header\b[^>]*>.*?</header>", source, re.DOTALL)
                self.assertIsNotNone(header)
                self.assertGreaterEqual(nav.start(), header.end())
                nav_inventory = DocumentInventory()
                nav_inventory.feed(nav.group())
                anchors = [attrs for tag, attrs in nav_inventory.elements if tag == "a"]
                self.assertEqual([anchor.get("href") for anchor in anchors], list(PAGES))
                self.assertEqual([anchor.get("href") for anchor in anchors if anchor.get("aria-current") == "page"], [path])
                self.assertTrue(all(anchor.get("role") != "tab" for anchor in anchors))
                ids = [attrs["id"] for _, attrs in inventory.elements if attrs.get("id")]
                self.assertEqual(len(ids), len(set(ids)), "Page IDs must be unique for native anchor destinations")

    def test_home_catalog_has_fourteen_nine_column_rows_with_working_dataset_anchors(self):
        source = self.pages["index.html"]
        catalog = re.search(r'<table\b[^>]*class="[^"]*dataset-catalog[^\"]*"[^>]*>.*?</table>', source, re.DOTALL)
        self.assertIsNotNone(catalog)
        head = re.search(r"<thead\b[^>]*>(.*?)</thead>", catalog.group(), re.DOTALL)
        body = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", catalog.group(), re.DOTALL)
        self.assertEqual(len(re.findall(r"<th\b", head.group())), 9)
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", body.group(), re.DOTALL)
        self.assertEqual(len(rows), 14)
        for row in rows:
            self.assertEqual(len(re.findall(r"<(?:th|td)\b", row)), 9)
            self.assertRegex(row, r'<th\b[^>]*scope="row"')
        catalog_inventory = DocumentInventory()
        catalog_inventory.feed(catalog.group())
        targets = [attrs["href"].split("#", 1)[1] for tag, attrs in catalog_inventory.elements
                   if tag == "a" and str(attrs.get("href", "")).startswith("datasets.html#")]
        self.assertEqual(len(targets), 14)
        self.assertEqual(set(targets), self.dataset_ids)
        self.assertTrue(all(target in self.inventories["datasets.html"].by_id for target in targets))
        groups = [attrs for tag, attrs in self.inventories["datasets.html"].elements
                  if tag == "article" and "dataset-description" in str(attrs.get("class", "")).split()]
        self.assertEqual(len(groups), 9)
        self.assertIn("computed on undirected graphs", source)

    def test_loading_examples_are_complete_python_with_correct_masks_and_three_snapshots(self):
        codes = CodeInventory()
        codes.feed(self.pages["getting-started.html"])
        self.assertEqual(len(codes.blocks), 2)
        seen_splits = set()
        for source in codes.blocks:
            tree = ast.parse(source)
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "GraphLandDataset"]
            self.assertEqual(len(calls), 1)
            arguments = {keyword.arg: keyword.value for keyword in calls[0].keywords}
            self.assertTrue({"root", "name", "split"}.issubset(arguments))
            self.assertNotIn("snapshot", arguments)
            split = arguments["split"].value
            seen_splits.add(split)
            loaded = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
            stored = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
            imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
            self.assertEqual(loaded - stored - imported - set(dir(builtins)), set(), "Loading examples must not depend on undefined training objects")
            if split == "THI":
                attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
                self.assertIn("mask", attributes)
                self.assertFalse({"train_mask", "val_mask", "test_mask"} & attributes)
                unpacked = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Tuple) and len(target.elts) == 3 for target in node.targets)]
                self.assertTrue(unpacked, "THI must expose train, validation, and test snapshots")
            else:
                self.assertIn("_mask", source)
        self.assertEqual(seen_splits, {"RL", "THI"})

    def test_only_leaderboard_loads_the_interactive_app(self):
        self.assertNotIn('href="leaderboard.xlsx"', self.pages["index.html"])
        self.assertFalse(any(tag == "a" and "download" in attrs for tag, attrs in self.inventories["index.html"].elements))
        self.assertIn('href="leaderboard.xlsx"', self.pages["leaderboard.html"])
        for path, inventory in self.inventories.items():
            with self.subTest(page=path):
                scripts = [attrs.get("src") for tag, attrs in inventory.elements if tag == "script"]
                self.assertEqual(scripts.count("assets/app.js"), int(path == "leaderboard.html"))
                self.assertIn("assets/navigation.js", scripts)
                for tag, attrs in inventory.elements:
                    if tag in {"script", "link"}:
                        self.assertFalse(str(attrs.get("src", attrs.get("href", ""))).startswith("/"))


if __name__ == "__main__":
    unittest.main()
