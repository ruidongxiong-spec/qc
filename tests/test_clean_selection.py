from collections import Counter
import csv
from pathlib import Path
import unittest

from scripts.build_selected_dataset import build

ROOT = Path(__file__).resolve().parents[1] / "datasets"


def load(name):
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class CleanSelectionTests(unittest.TestCase):
    def test_clean_rows_preserve_source_records_and_strata(self):
        cases = [
            ("hotpotqa_train", "hotpotqa_fullwiki_train.csv", "level",
             {"easy": 500, "medium": 300, "hard": 200}),
            ("musique_ans_dev", "musique_ans_dev.csv", "hops", {"2": 600, "3": 400}),
        ]
        for prefix, source_name, field, quotas in cases:
            with self.subTest(dataset=prefix):
                source = {r["id"]: r for r in load(source_name)}
                initial = load(prefix + "_sample1000.csv")
                cleaned = load(prefix + "_clean1000.csv")
                self.assertEqual(len(cleaned), 1000)
                self.assertEqual(len({r["id"] for r in cleaned}), 1000)
                self.assertEqual(len({" ".join(r["question"].casefold().split()) for r in cleaned}), 1000)
                self.assertEqual(Counter(r[field] for r in cleaned), quotas)
                for before, after in zip(initial, cleaned, strict=True):
                    self.assertEqual(after, source[after["id"]])
                    self.assertEqual(after[field], before[field])
                    self.assertNotIn("answer:", after["question"].casefold())

    def test_combined_snapshot_rebuilds_without_changes(self):
        before = (ROOT / "scope_selected2125.csv").read_bytes()
        build()
        self.assertEqual((ROOT / "scope_selected2125.csv").read_bytes(), before)
        self.assertEqual(load("scope_selected2125.csv")[2000:], load("bamboogle.csv"))
