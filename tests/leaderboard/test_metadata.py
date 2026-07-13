from __future__ import annotations

import copy
import json
import unittest
from collections import Counter

from support import ROOT, load_json, load_module


class MetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validation = load_module("leaderboard_validation_metadata", "scripts/leaderboard/validate.py")
        cls.datasets_document = load_json(ROOT / "leaderboard" / "datasets.json")
        cls.config = load_json(ROOT / "leaderboard" / "config.json")

    def test_exact_official_dataset_catalog_and_task_distribution(self) -> None:
        datasets = self.datasets_document["datasets"]
        expected = {
            "hm-categories": ("multiclass_node_classification", "accuracy"),
            "pokec-regions": ("multiclass_node_classification", "accuracy"),
            "web-topics": ("multiclass_node_classification", "accuracy"),
            "tolokers-2": ("binary_node_classification", "average_precision"),
            "city-reviews": ("binary_node_classification", "average_precision"),
            "artnet-exp": ("binary_node_classification", "average_precision"),
            "web-fraud": ("binary_node_classification", "average_precision"),
            "hm-prices": ("node_regression", "r2"),
            "avazu-ctr": ("node_regression", "r2"),
            "city-roads-M": ("node_regression", "r2"),
            "city-roads-L": ("node_regression", "r2"),
            "twitch-views": ("node_regression", "r2"),
            "artnet-views": ("node_regression", "r2"),
            "web-traffic": ("node_regression", "r2"),
        }
        actual = {item["id"]: (item["task"], item["metric"]) for item in datasets}
        self.assertEqual(actual, expected)
        self.assertEqual(
            Counter(item["task"] for item in datasets),
            Counter(
                {
                    "multiclass_node_classification": 3,
                    "binary_node_classification": 4,
                    "node_regression": 7,
                }
            ),
        )

    def test_dataset_metadata_passes_semantic_validation(self) -> None:
        validated = self.validation.validate_datasets(self.datasets_document)
        self.assertEqual(len(validated), 14)
        self.assertTrue(all(item["higher_is_better"] is True for item in validated))
        self.assertTrue(all(item["release_version"] == "v1" for item in validated))
        self.assertTrue(all(item["license"] == "Apache-2.0" for item in validated))
        self.assertTrue(all(item["source"]["url"].startswith("https://") for item in validated))

    def test_metric_storage_and_display_scales_are_separate(self) -> None:
        for dataset in self.datasets_document["datasets"]:
            if dataset["metric"] in {"accuracy", "average_precision"}:
                self.assertEqual(dataset["display"]["style"], "percentage")
            else:
                self.assertEqual(dataset["metric"], "r2")
                self.assertEqual(dataset["display"]["style"], "number")

    def test_setting_availability_is_exact(self) -> None:
        datasets = {item["id"]: item for item in self.datasets_document["datasets"]}
        unavailable = {"city-reviews", "city-roads-M", "city-roads-L", "web-traffic"}
        temporal = {dataset_id for dataset_id, item in datasets.items() if "TH" in item["available_settings"]}
        self.assertEqual(len(temporal), 10)
        self.assertEqual(set(datasets) - temporal, unavailable)
        for dataset_id, item in datasets.items():
            self.assertTrue({"RL", "RH"}.issubset(item["available_settings"]))
            self.assertEqual("TH" in item["available_settings"], "THI" in item["available_settings"])
            self.assertEqual(dataset_id in unavailable, "TH" not in item["available_settings"])

    def test_setting_definitions_and_thi_split_alias_are_exact(self) -> None:
        settings = {item["id"]: item for item in self.config["settings"]}
        self.assertEqual(list(settings), ["RL", "RH", "TH", "THI"])
        self.assertEqual(
            {
                key: (value["train_percent"], value["validation_percent"], value["test_percent"])
                for key, value in settings.items()
            },
            {
                "RL": (10, 10, 80),
                "RH": (50, 25, 25),
                "TH": (50, 25, 25),
                "THI": (50, 25, 25),
            },
        )
        self.assertEqual(settings["TH"]["split_file"], "split_masks_TH.csv")
        self.assertEqual(settings["THI"]["split_file"], "split_masks_TH.csv")
        self.assertEqual(settings["THI"]["information_access"], "inductive")
        for setting in ("RL", "RH", "TH"):
            self.assertEqual(settings[setting]["information_access"], "transductive")

    def test_validation_rejects_catalog_drift(self) -> None:
        wrong_count = copy.deepcopy(self.datasets_document)
        wrong_count["datasets"].pop()
        with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "exactly 14"):
            self.validation.validate_datasets(wrong_count)

        wrong_metric = copy.deepcopy(self.datasets_document)
        wrong_metric["datasets"][0]["metric"] = "average_precision"
        with self.assertRaisesRegex(self.validation.LeaderboardValidationError, "canonical metric"):
            self.validation.validate_datasets(wrong_metric)

    def test_production_code_never_references_a_thi_mask_file(self) -> None:
        inspected_suffixes = {".py", ".js", ".json", ".yml", ".yaml", ".html"}
        offenders = []
        for parent in (ROOT / "leaderboard", ROOT / "scripts" / "leaderboard", ROOT / "site", ROOT / ".github"):
            if not parent.exists():
                continue
            for path in parent.rglob("*"):
                if path.is_file() and path.suffix in inspected_suffixes:
                    if "split_masks_THI.csv" in path.read_text(encoding="utf-8"):
                        offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], f"THI must reuse split_masks_TH.csv; offenders: {offenders}")

    def test_config_has_one_boolean_code_filter_default(self) -> None:
        self.assertIs(type(self.config["default_filters"]["only_models_with_code"]), bool)
        self.assertFalse(self.config["default_filters"]["only_models_with_code"])
        serialized = json.dumps(self.config)
        self.assertEqual(serialized.count("only_models_with_code"), 1)


if __name__ == "__main__":
    unittest.main()
