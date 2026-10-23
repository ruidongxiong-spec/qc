from collections import Counter
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import unittest

from scope.curation import csv_bytes
from scope.datasets import load_questions
from scripts.export_musique import RAW_FIELDS, raw_row, select, validate

ROOT = Path(__file__).resolve().parents[1] / "datasets"


def load(name):
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def restore(row):
    result = {k: row[k] for k in ("id", "question", "answer")}
    for key in ("answer_aliases", "answerable", "paragraphs", "question_decomposition"):
        result[key] = json.loads(row[key + "_json"])
    for key in json.loads(row["missing_fields_json"]):
        del result[key]
    return result


class MusiqueExportTests(unittest.TestCase):
    def test_every_split_and_output_checksum(self):
        manifest = json.loads((ROOT / "musique_manifest.json").read_text())
        self.assertEqual({k: v["rows"] for k, v in manifest["counts"].items()},
                         {"ans/train": 19938, "ans/dev": 2417, "ans/test": 2459,
                          "full/train": 39876, "full/dev": 4834, "full/test": 4918})
        for item in manifest["outputs"]:
            path = ROOT / item["file"]
            self.assertEqual(path.stat().st_size, item["bytes"], item["file"])
            with path.open("rb") as source:
                self.assertEqual(hashlib.file_digest(source, "sha256").hexdigest(), item["sha256"], item["file"])
            if "/part-" in item["file"]:
                self.assertLessEqual(item["bytes"], 20 * 1024 * 1024)

    def test_dev_sample_is_reproducible_and_has_original_answers(self):
        dev = load("musique_ans_dev.csv")
        selected = load("musique_ans_dev_sample1000.csv")
        self.assertEqual(len(selected), 1000)
        self.assertEqual(Counter(r["hops"] for r in selected), {"2": 600, "3": 400})
        self.assertEqual(selected, select(dev))
        self.assertEqual(selected, select(list(reversed(dev))))
        self.assertEqual(len(load_questions(ROOT / "musique_ans_dev_sample1000.csv")), 1000)
        self.assertEqual(len(load("musique_ans_dev_2_3hop.csv")), 2012)
        self.assertFalse({r["id"] for r in dev} & {r["id"] for r in load("musique_ans_train.csv")})
        original = {}
        for path in sorted((ROOT / "official/musique_ans/dev").glob("part-*.csv")):
            for row in load(path):
                original[row["id"]] = restore(row)
        for row in dev:
            raw = original[row["id"]]
            self.assertEqual(row["question"], raw["question"])
            self.assertEqual(row["answer"], raw["answer"])
            self.assertEqual(json.loads(row["answer_aliases_json"]), raw["answer_aliases"])
            self.assertEqual(int(row["hops"]), len(raw["question_decomposition"]))
        self.assertEqual({r["split"] for r in selected}, {"dev_ans_v1.0"})

    def test_full_field_round_trip_and_unlabeled_test(self):
        original = dict(id="2hop__x_y", question='Which "city",\n在哪里?', answer="City",
                        answer_aliases=["市"], answerable=True,
                        paragraphs=[dict(idx=0, title="T", paragraph_text="Quote: \"a\"\n段落", is_supporting=True)],
                        question_decomposition=[dict(id=i, question="q", answer="a", paragraph_support_idx=0) for i in (1, 2)])
        for record in (original, {k: original[k] for k in ("id", "question", "paragraphs")}):
            text = csv_bytes([raw_row(record)], RAW_FIELDS).decode("utf-8-sig")
            self.assertEqual(restore(next(csv.DictReader(io.StringIO(text)))), record)
        for row in load("official/musique_ans/test/part-0001.csv"):
            restored = restore(row)
            self.assertEqual(set(restored), {"id", "question", "paragraphs"})
        with self.assertRaisesRegex(ValueError, "reference answers"):
            load_questions(ROOT / "musique_ans_test.csv")

    def test_invalid_hop_and_support_fail_closed(self):
        with (ROOT / "official/musique_ans/dev/part-0001.csv").open(encoding="utf-8-sig", newline="") as source:
            original = restore(next(csv.DictReader(source)))
        self.assertEqual(validate(original, True, True), "2")
        bad = copy.deepcopy(original)
        bad["question_decomposition"].pop()
        with self.assertRaisesRegex(ValueError, "Hop count"):
            validate(bad, True, True)
        bad = copy.deepcopy(original)
        bad["question_decomposition"][0]["paragraph_support_idx"] = 999999
        with self.assertRaisesRegex(ValueError, "support reference"):
            validate(bad, True, True)
        bad = copy.deepcopy(original)
        bad["answerable"] = False
        with self.assertRaisesRegex(ValueError, "answerable Ans"):
            validate(bad, True, True)

    def test_combined_manifest_tracks_current_inputs(self):
        manifest = json.loads((ROOT / "selection_manifest.json").read_text())
        for item in manifest["inputs"] + [manifest["output"]]:
            self.assertEqual(hashlib.sha256((ROOT / item["file"]).read_bytes()).hexdigest(), item["sha256"])
        combined = load("scope_selected2125.csv")
        self.assertEqual(combined[1000:2000], load("musique_ans_dev_sample1000.csv"))
        self.assertEqual(combined[2000:], load("bamboogle.csv"))
        self.assertEqual(len(combined), 2125)


if __name__ == "__main__":
    unittest.main()
