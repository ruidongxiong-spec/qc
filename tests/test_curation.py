import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from scope.curation import (QUOTAS, SEED, csv_bytes, curate, digest, finalize,
                            json_bytes, normalize_train, ordered_groups)


def row(uid, level, question=None):
    return dict(id=uid, dataset="hotpotqa", split="train", question=question or f"Question about {uid}?",
                answer=f"Answer for {uid}", answer_aliases_json="[]", level=level, hops="",
                question_type="bridge", supporting_facts_json="[]")


def decision(keep=True, reason="Entities and requested answer are clear"):
    return dict(decision="keep" if keep else "exclude", reason=reason)


class CurationTests(unittest.TestCase):
    def test_shuffle_is_seeded_and_independent_of_source_order(self):
        rows = [row(f"{level}{i}", level) for level in QUOTAS for i in range(10)]
        self.assertEqual(ordered_groups(rows), ordered_groups(rows[::-1]))
        self.assertNotEqual(ordered_groups(rows), ordered_groups(rows, seed="another seed"))

    def test_replacements_keep_original_difficulty_and_text(self):
        rows = [row("e1", "easy"), row("e2", "easy"), row("e3", "easy"),
                row("m1", "medium"), row("h1", "hard")]
        reviews = {"decisions": {r["id"]: decision() for r in rows}}
        reviews["decisions"]["e1"] = decision(False, "Unresolved pronoun")
        selected, audit, replacements = curate(rows, reviews, {"easy": 2, "medium": 1, "hard": 1})
        self.assertEqual(selected, rows[1:])
        self.assertEqual(replacements[0]["removed_id"], "e1")
        self.assertEqual(replacements[0]["added_id"], "e3")
        self.assertEqual(len(audit), 5)

    def test_unreviewed_questions_cannot_be_skipped_or_accepted(self):
        rows = [row("e1", "easy"), row("e2", "easy")]
        with self.assertRaisesRegex(ValueError, "Pending individual review"):
            curate(rows, {"decisions": {"e2": decision()}}, {"easy": 1})
        with self.assertRaisesRegex(ValueError, "Insufficient reviewed"):
            curate(rows, {"decisions": {r["id"]: decision(False) for r in rows}}, {"easy": 1})

    def test_review_requires_reason_and_known_id(self):
        rows = [row("e1", "easy")]
        for reviews in [{"e1": decision(reason=" ")}, {"e1": {"decision": "pending", "reason": "?"}},
                        {"unknown": decision()}]:
            with self.assertRaises(ValueError):
                curate(rows, {"decisions": reviews}, {"easy": 1})

    def test_duplicate_question_requires_explicit_exclusion(self):
        rows = [row("e1", "easy", "Who was born first?"), row("e2", "easy", " who WAS born first? ")]
        with self.assertRaisesRegex(ValueError, "Duplicate selected question"):
            curate(rows, {"decisions": {r["id"]: decision() for r in rows}}, {"easy": 2})

    def test_wrong_split_cannot_masquerade_as_complete_train(self):
        source = [{"_id": "a", "question": "Which person?", "answer": "A", "level": "hard",
                   "type": "bridge", "supporting_facts": [], "context": []}]
        with self.assertRaisesRegex(ValueError, "complete original train"):
            normalize_train(json_bytes(source))

    def test_final_export_is_review_bound_and_reproducible(self):
        # Controlled fixture: this exercises export contracts, not real-data quality.
        rows = [row(f"{level}{i}", level) for level, n in QUOTAS.items() for i in range(n)]
        data = csv_bytes(rows)
        reviews = dict(seed=SEED, source_sha256="fixture", candidates_sha256=digest(data),
                       decisions={r["id"]: decision() for r in rows})
        provenance = dict(quotas=QUOTAS, seed=SEED, source_sha256="fixture",
                          candidates_sha256=digest(data), initial_sha256=digest(data))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, content in {"candidates.csv": data, "initial1000.csv": data,
                                  "reviews.json": json_bytes(reviews), "provenance.json": json_bytes(provenance)}.items():
                (directory / name).write_bytes(content)
            manifest = finalize(directory)
            self.assertEqual(manifest["final_counts"], QUOTAS)
            final_data = (directory / "hotpotqa_train_clean1000.csv").read_bytes()
            self.assertEqual(len(list(csv.DictReader(io.StringIO(final_data.decode("utf-8-sig"))))), 1000)
            self.assertEqual(finalize(directory), manifest)
            (directory / "candidates.csv").write_bytes(data + b"\n")
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                finalize(directory)
            self.assertEqual((directory / "hotpotqa_train_clean1000.csv").read_bytes(), final_data)

    def test_incomplete_review_writes_no_final_dataset(self):
        rows = [row("a", "easy")]
        data = csv_bytes(rows)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            provenance = dict(quotas=QUOTAS, seed=SEED, source_sha256="fixture",
                              candidates_sha256=digest(data), initial_sha256=digest(data))
            review = dict(seed=SEED, source_sha256="fixture", candidates_sha256=digest(data), decisions={})
            for name, content in {"candidates.csv": data, "initial1000.csv": data,
                                  "provenance.json": json_bytes(provenance), "reviews.json": json_bytes(review)}.items():
                (directory / name).write_bytes(content)
            with self.assertRaisesRegex(ValueError, "Pending individual review"):
                finalize(directory)
            self.assertFalse((directory / "hotpotqa_train_clean1000.csv").exists())


if __name__ == "__main__":
    unittest.main()
