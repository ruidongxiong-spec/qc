import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.export_official_hotpotqa import RAW_FIELDS, raw_row, write_shards
from scope.curation import QUOTAS

ROOT = Path(__file__).resolve().parents[1]


class OfficialExportTests(unittest.TestCase):
    def test_full_csv_shards_round_trip_every_field_and_null(self):
        original = dict(id="a", question='Which "city",\n在哪里?', answer=None, type="", level=None,
                        supporting_facts={"title": ["A"], "sent_id": [0]},
                        context={"title": ["A"], "sentences": [['Quotation: "x"\n第二行']]})
        expected = raw_row(original)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            one = write_shards(path, [expected], max_bytes=2000, manifest_root=path)[0]
            count = one["bytes"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            outputs = write_shards(path, [expected] * 3, max_bytes=count + 1, manifest_root=path)
            self.assertEqual(len(outputs), 3)
            for item in outputs:
                raw = (path / item["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
                record = next(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
                restored = {k: record[k] for k in ("id", "question", "answer", "type", "level")}
                for key in json.loads(record["null_fields_json"]):
                    restored[key] = None
                restored["context"] = json.loads(record["context_json"])
                restored["supporting_facts"] = json.loads(record["supporting_facts_json"])
                self.assertEqual(restored, original)

    def test_committed_official_counts_and_checksums(self):
        manifest = json.loads((ROOT / "datasets/hotpotqa_fullwiki_manifest.json").read_text())
        self.assertEqual({k: v["rows"] for k, v in manifest["counts"].items()},
                         {"train": 90447, "validation": 7405, "test": 7405})
        for item in manifest["outputs"]:
            path = ROOT / "datasets" / item["file"]
            self.assertEqual(path.stat().st_size, item["bytes"], item["file"])
            with path.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            self.assertEqual(actual, item["sha256"], item["file"])
            if item["file"].startswith("official/"):
                self.assertLessEqual(item["bytes"], 20 * 1024 * 1024)

    def test_selected_sample_and_bamboogle_are_preserved(self):
        def load(name):
            with (ROOT / "datasets" / name).open(encoding="utf-8-sig", newline="") as source:
                return list(csv.DictReader(source))
        selected = load("hotpotqa_train_sample1000.csv")
        self.assertEqual(len({r["id"] for r in selected}), 1000)
        self.assertEqual({level: sum(r["level"] == level for r in selected) for level in QUOTAS}, QUOTAS)
        self.assertEqual({r["split"] for r in selected}, {"train"})
        self.assertEqual((ROOT / "datasets/hotpotqa_train_sample1000.csv").read_bytes(),
                         (ROOT / "datasets/hotpotqa_train_review/initial1000.csv").read_bytes())
        combined = load("scope_selected2125.csv")
        self.assertEqual(combined[:1000], load("hotpotqa_train_clean1000.csv"))
        self.assertEqual(combined[1000:2000], load("musique_ans_dev_clean1000.csv"))
        self.assertEqual(combined[2000:], load("bamboogle.csv"))
        self.assertFalse((ROOT / "datasets/hotpotqa_train_review/hotpotqa_train_clean1000.csv").exists())


if __name__ == "__main__":
    unittest.main()
