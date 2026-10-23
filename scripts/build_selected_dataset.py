"""Build the current 1000 + 1000 + 125 evaluation input from pinned CSVs."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scope.curation import csv_bytes, digest, json_bytes, preserve_write

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ("hotpotqa_train_sample1000.csv", "musique_ans_dev_sample1000.csv", "bamboogle.csv")


def build(update=False):
    combined, inputs = [], []
    for name, expected in zip(INPUTS, (1000, 1000, 125), strict=True):
        path = ROOT / "datasets" / name
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        if len(rows) != expected:
            raise ValueError(f"Incorrect selection count: {name}")
        if name.startswith("hotpot") and Counter(r["level"] for r in rows) != {"easy": 500, "medium": 300, "hard": 200}:
            raise ValueError("Unexpected HotpotQA quotas")
        if name.startswith("musique") and Counter(r["hops"] for r in rows) != {"2": 600, "3": 400}:
            raise ValueError("Unexpected MuSiQue quotas")
        inputs.append(dict(file=name, rows=len(rows), sha256=digest(path.read_bytes())))
        combined.extend(rows)
    if len({r["id"] for r in combined}) != 2125:
        raise ValueError("Duplicate selection IDs")
    data = csv_bytes(combined)
    path = ROOT / "datasets/scope_selected2125.csv"
    manifest = dict(schema_version=1, inputs=inputs, quotas=dict(hotpotqa=dict(easy=500, medium=300, hard=200),
                    musique={"2": 600, "3": 400}, bamboogle=125),
                    semantic_review_status="paused_by_user; no exclusions applied",
                    output=dict(file=path.name, rows=2125, bytes=len(data), sha256=digest(data)))
    for target, content in ((path, data), (ROOT / "datasets/selection_manifest.json", json_bytes(manifest))):
        if update:
            target.write_bytes(content)
        else:
            preserve_write(target, content)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="Explicitly replace the current combined snapshot")
    print(json.dumps(build(parser.parse_args().update), indent=2))
