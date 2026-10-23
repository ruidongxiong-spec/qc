"""Load auditable QA exports without passing reference answers to the agent."""
import csv
import json
from pathlib import Path


def load_questions(path):
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        if path.suffix.lower() == ".csv":
            rows = list(csv.DictReader(handle))
            for row in rows:
                aliases = json.loads(row.get("answer_aliases_json") or "[]")
                if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
                    raise ValueError("answer_aliases_json must contain a string list")
                row["answer"] = list(dict.fromkeys([row.get("answer", ""), *aliases]))
        else:
            rows = [json.loads(line) for line in handle if line.strip()]
    seen = set()
    if not rows:
        raise ValueError("Dataset must not be empty")
    for row in rows:
        if not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            raise ValueError("Question IDs must be nonempty unique strings")
        seen.add(row["id"])
        if not isinstance(row.get("question"), str) or not row["question"].strip():
            raise ValueError("Every row requires a nonempty question")
        answers = row.get("answer")
        answers = [answers] if isinstance(answers, str) else answers
        if not isinstance(answers, list) or not answers or any(not isinstance(a, str) or not a.strip() for a in answers):
            raise ValueError("Every row requires nonempty reference answers")
    return rows
