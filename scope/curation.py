"""Reproducible stratified selection; semantic decisions must be recorded explicitly."""
from collections import Counter
import csv
import hashlib
import io
import json

from scope.datasets import load_questions

FIELDS = ["id", "dataset", "split", "question", "answer", "answer_aliases_json",
          "level", "hops", "question_type", "supporting_facts_json"]
QUOTAS = {"easy": 500, "medium": 300, "hard": 200}
TRAIN_COUNTS = {"easy": 17972, "medium": 56814, "hard": 15661}
SEED = "scope-hotpotqa-train-v1-seed42"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def csv_bytes(rows, fields=FIELDS):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def preserve_write(path, data):
    if path.exists() and path.read_bytes() != data:
        raise FileExistsError(f"Refusing to overwrite a different snapshot: {path}")
    path.write_bytes(data)


def normalize_train(raw, expected_counts=TRAIN_COUNTS):
    """Read the official CMU JSON array, not an arbitrary mirror's demo records."""
    records = json.loads(raw)
    if not isinstance(records, list):
        raise ValueError("Expected the official HotpotQA training JSON array")
    return normalize_records(records, expected_counts)


def normalize_records(records, expected_counts=TRAIN_COUNTS, require_context=True):
    """Validate wire-format records; also accepts a generator converted from HF columns."""
    rows = []
    seen = set()
    for record in records:
        uid, question, answer = (record.get(k) for k in ("_id", "question", "answer"))
        if not all(isinstance(x, str) and x.strip() for x in (uid, question, answer)):
            raise ValueError("Missing original ID, question or answer")
        if uid in seen:
            raise ValueError(f"Duplicate original ID: {uid}")
        seen.add(uid)
        if record.get("level") not in QUOTAS or record.get("type") not in {"bridge", "comparison"}:
            raise ValueError(f"Missing/invalid original level or type: {uid}")
        if not isinstance(record.get("supporting_facts"), list) or (require_context and not isinstance(record.get("context"), list)):
            raise ValueError(f"Missing original support/context: {uid}")
        rows.append(dict(id=uid, dataset="hotpotqa", split="train", question=question, answer=answer,
                         answer_aliases_json="[]", level=record["level"], hops="",
                         question_type=record["type"],
                         supporting_facts_json=json.dumps(record["supporting_facts"], ensure_ascii=False)))
    if Counter(row["level"] for row in rows) != Counter(expected_counts):
        raise ValueError("Training level counts do not match the complete original train split")
    return rows


def ordered_groups(rows, seed=SEED):
    return {level: sorted([r for r in rows if r["level"] == level],
                          key=lambda r: (digest((seed + ":" + r["id"]).encode()), r["id"]))
            for level in QUOTAS}


def prepare(raw, source_url, reserve=200):
    return prepare_rows(normalize_train(raw), dict(source_url=source_url, source_sha256=digest(raw),
                                                 source_bytes=len(raw), source_format="cmu_json"), reserve)


def prepare_rows(rows, source_metadata, reserve=200):
    if reserve < 0:
        raise ValueError("reserve must be nonnegative")
    groups = ordered_groups(rows)
    initial = [r for level, n in QUOTAS.items() for r in groups[level][:n]]
    candidates = [r for level, n in QUOTAS.items() for r in groups[level][:n + reserve]]
    candidate_data = csv_bytes(candidates)
    provenance = {
        "schema_version": 1, **source_metadata,
        "source_split": "train", "hf_configuration": "fullwiki",
        "train_shared_between_configurations": True, "source_counts": TRAIN_COUNTS,
        "seed": SEED, "selection": "ascending SHA256(seed + ':' + original ID), per original level",
        "quotas": QUOTAS, "reserve_per_level": reserve,
        "candidates_sha256": digest(candidate_data), "initial_sha256": digest(csv_bytes(initial)),
        "status": "awaiting_individual_semantic_review", "paper_exact_subset": False,
    }
    review = {"schema_version": 1, "source_sha256": provenance["source_sha256"],
              "candidates_sha256": digest(candidate_data), "seed": SEED,
              "review_method": "Individual question clarity review; not exhaustive factual verification",
              "decisions": {}}
    return initial, candidates, provenance, review


def curate(candidates, review, quotas=QUOTAS):
    """Consume only reviewed prefixes; never silently treat an unreviewed row as good."""
    if len({r["id"] for r in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate ID")
    decisions = review.get("decisions", {})
    by_id = {r["id"]: r for r in candidates}
    unknown = set(decisions) - set(by_id)
    if unknown:
        raise ValueError(f"Review contains unknown candidate IDs: {sorted(unknown)[:3]}")
    for uid, d in decisions.items():
        if not isinstance(d, dict) or d.get("decision") not in {"keep", "exclude"} or not isinstance(d.get("reason"), str) or not d["reason"].strip():
            raise ValueError(f"Review requires keep/exclude and a nonempty reason: {uid}")
    final, audit, replacements = [], [], []
    selected_questions = set()
    for level, quota in quotas.items():
        group = [r for r in candidates if r["level"] == level]
        chosen, removed_initial, added = [], [], []
        for index, row in enumerate(group):
            if len(chosen) == quota:
                break
            uid = row["id"]
            decision = decisions.get(uid)
            if decision is None:
                raise ValueError(f"Pending individual review at {level} candidate {index + 1}: {uid}")
            normalized_question = " ".join(row["question"].casefold().split())
            if decision["decision"] == "keep" and normalized_question in selected_questions:
                raise ValueError(f"Duplicate selected question; record an explicit exclusion: {uid}")
            accepted = decision["decision"] == "keep"
            if accepted:
                chosen.append(row)
                selected_questions.add(normalized_question)
                if index >= quota:
                    added.append(row)
            elif index < quota:
                removed_initial.append(row)
            audit.append(dict(id=uid, level=level, initial_sample=str(index < quota).lower(),
                              question=row["question"], decision=decision["decision"],
                              reason=decision["reason"], selected=str(accepted).lower()))
        if len(chosen) != quota:
            raise ValueError(f"Insufficient reviewed acceptable {level} questions; need {quota}, got {len(chosen)}")
        for old, new in zip(removed_initial, added, strict=True):
            replacements.append(dict(level=level, removed_id=old["id"], removed_question=old["question"],
                                     reason=decisions[old["id"]]["reason"], added_id=new["id"],
                                     added_question=new["question"]))
        final.extend(chosen)
    return final, audit, replacements


def finalize(directory):
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    review_data = (directory / "reviews.json").read_bytes()
    review = json.loads(review_data)
    candidates_raw = (directory / "candidates.csv").read_bytes()
    initial_raw = (directory / "initial1000.csv").read_bytes()
    if (provenance["quotas"] != QUOTAS or provenance["seed"] != SEED
            or review.get("seed") != SEED or review.get("source_sha256") != provenance["source_sha256"]
            or digest(candidates_raw) != provenance["candidates_sha256"]
            or review.get("candidates_sha256") != digest(candidates_raw)
            or digest(initial_raw) != provenance["initial_sha256"]):
        raise ValueError("Source/seed/snapshot/review binding mismatch")
    candidates = list(csv.DictReader(io.StringIO(candidates_raw.decode("utf-8-sig"))))
    if any(r["split"] != "train" or r["dataset"] != "hotpotqa" or r["level"] not in QUOTAS for r in candidates):
        raise ValueError("Unexpected source split, dataset or difficulty")
    final, audit, replacements = curate(candidates, review)
    outputs = {
        "hotpotqa_train_clean1000.csv": csv_bytes(final),
        "question_review.csv": csv_bytes(audit, ["id", "level", "initial_sample", "question", "decision", "reason", "selected"]),
        "replacements.csv": csv_bytes(replacements, ["level", "removed_id", "removed_question", "reason", "added_id", "added_question"]),
    }
    manifest = dict(provenance, status="individual_clarity_review_complete", reviews_sha256=digest(review_data),
                    final_counts=dict(Counter(r["level"] for r in final)), replacements=len(replacements),
                    reviewed_prefix_rows=len(audit), answer_fact_check_complete=False,
                    outputs=[dict(file=name, sha256=digest(data), bytes=len(data)) for name, data in outputs.items()])
    # Validate everything before writing the first final artifact.
    outputs["manifest.json"] = json_bytes(manifest)
    for name, data in outputs.items():
        path = directory / name
        if path.exists() and path.read_bytes() != data:
            raise FileExistsError(f"Refusing to overwrite a different final snapshot: {path}")
    for name, data in outputs.items():
        preserve_write(directory / name, data)
    assert len(load_questions(directory / "hotpotqa_train_clean1000.csv")) == 1000
    return manifest
