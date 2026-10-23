import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scope.datasets import load_questions

ROOT = Path(__file__).resolve().parents[1]


class DatasetLoadingTests(unittest.TestCase):
    def test_csv_bom_quotes_newline_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'questions.csv'
            buffer = io.StringIO(newline='')
            writer = csv.DictWriter(buffer, fieldnames=['id', 'question', 'answer', 'answer_aliases_json'])
            writer.writeheader()
            writer.writerow({'id': 'x', 'question': 'Who said "hello",\nand when?', 'answer': 'Primary',
                             'answer_aliases_json': '["Alias", "Primary"]'})
            path.write_bytes(buffer.getvalue().encode('utf-8-sig'))
            rows = load_questions(path)
            self.assertEqual(rows[0]['answer'], ['Primary', 'Alias'])
            self.assertIn('\n', rows[0]['question'])

    def test_invalid_records_fail_before_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data.jsonl'
            for rows in [[], [{'id': 'x', 'question': 'q', 'answer': ''}],
                         [{'id': 'x', 'question': 'q', 'answer': 'a'}] * 2]:
                path.write_text('\n'.join(json.dumps(r) for r in rows))
                with self.assertRaises(ValueError):
                    load_questions(path)

    def test_public_exports_match_manifest(self):
        manifest = json.loads((ROOT / 'datasets/manifest.json').read_text())
        for export in manifest['outputs']:
            path = ROOT / 'datasets' / export['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), export['sha256'], export['file'])
            self.assertEqual(len(load_questions(path)), export['rows'], export['file'])

    def test_samples_and_combined_membership(self):
        load = lambda name: load_questions(ROOT / 'datasets' / name)
        hotpot = load('hotpotqa_dev.csv')
        hotpot1000 = load('hotpotqa_sample1000.csv')
        musique = load('musique_dev.csv')
        musique1000 = load('musique_sample1000.csv')
        self.assertEqual({r['level'] for r in hotpot}, {'hard'})
        self.assertTrue({r['id'] for r in hotpot1000} <= {r['id'] for r in hotpot})
        self.assertTrue({r['id'] for r in musique1000} <= {r['id'] for r in musique})
        self.assertEqual(sum(r['hops'] == '2' for r in musique1000), 500)
        self.assertEqual(sum(r['hops'] == '3' for r in musique1000), 500)
        self.assertEqual(len(load('scope_public_baseline2125.csv')), 2125)
        self.assertEqual(len(load('bamboogle.csv')), 125)
