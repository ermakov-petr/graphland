"""Check the public seed's provenance and scale against retained source cells."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PaperBaselineTests(unittest.TestCase):
    def test_attributable_paper_cells_and_failed_cells(self):
        ledger = json.loads((ROOT / "leaderboard/sources/paper-v5.json").read_text())
        self.assertEqual(ledger["source_url"], "https://arxiv.org/html/2409.14500v5")
        self.assertRegex(ledger["source_sha256"], r"^[0-9a-f]{64}$")
        expected_counts = {"resmlp": 48, "graphsage": 48, "catboost": 34}
        for slug, count in expected_counts.items():
            entry = json.loads((ROOT / ("leaderboard/submissions/paper-v5-" + slug + ".json")).read_text())
            self.assertEqual(entry["verification"], "self_reported")
            self.assertEqual(entry["provenance"], "maintainer_seeded")
            self.assertIsNone(entry["source_issue"])
            self.assertEqual(len(entry["results"]), count)
            cells = { (c["setting"], c["dataset"]): c for c in ledger["cells"] if c["model"] == entry["model_name"] }
            for result in entry["results"]:
                cell = cells[(result["setting"], result["dataset"])]
                raw = re.search(r"\{\$(-?[0-9.]+)\$\}\\pm\s*([0-9.]+)", cell["source_cell"])
                self.assertIsNotNone(raw)
                self.assertAlmostEqual(result["value"], float(raw[1]) / 100)
                self.assertAlmostEqual(result["std"], float(raw[2]) / 100)
                self.assertEqual(result["num_runs"], 5 if result["dataset"] in {"pokec-regions", "web-topics", "web-fraud", "web-traffic"} else 10)
            omitted = {key for key, cell in cells.items() if "omitted_reason" in cell}
            self.assertFalse(omitted & {(v["setting"], v["dataset"]) for v in entry["results"]})
        catboost = json.loads((ROOT / "leaderboard/submissions/paper-v5-catboost.json").read_text())
        negative = next(r for r in catboost["results"] if r["setting"] == "TH" and r["dataset"] == "twitch-views")
        self.assertEqual(negative["value"], -0.0876)
        self.assertFalse(list((ROOT / "leaderboard/submissions").glob("demo-*.json")))
