"""Export every official MuSiQue v1.0 split and a 600/400 Ans-dev sample."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scope.curation import csv_bytes, digest, json_bytes, preserve_write
from scripts.export_official_hotpotqa import write_shards

ROOT = Path(__file__).resolve().parents[1]
SEED = "scope-musique-ans-dev-v2-seed42"
QUOTAS = {"2": 600, "3": 400}
ORIGINAL_FIELDS = {"id", "question", "answer", "answer_aliases", "answerable", "paragraphs", "question_decomposition"}
RAW_FIELDS = ["id", "question", "answer", "answer_aliases_json", "answerable_json", "paragraphs_json",
              "question_decomposition_json", "missing_fields_json"]


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify(path, expected):
    if path.stat().st_size != expected["bytes"] or file_hash(path) != expected["sha256"]:
        raise ValueError(f"Source checksum mismatch: {path}")


def sources(config, offline):
    archive = ROOT / "datasets/raw/musique_data_v1.0.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        if offline:
            raise FileNotFoundError(archive)
        partial = archive.with_suffix(".partial")
        try:
            with urllib.request.urlopen(config["archive"]["url"], timeout=120) as response, partial.open("wb") as out:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > config["archive"]["bytes"]:
                        raise ValueError("Archive exceeds pinned byte count")
                    out.write(chunk)
            verify(partial, config["archive"])
            partial.replace(archive)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    verify(archive, config["archive"])
    directory = ROOT / "datasets/raw/musique_official"
    with zipfile.ZipFile(archive) as bundle:
        # Extract only explicitly pinned members, never arbitrary ZIP paths.
        for member, expected in config["members"].items():
            path = directory / member
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, path.open("wb") as out:
                    shutil.copyfileobj(source, out)
            verify(path, expected)
    return directory


def validate(record, labeled, answerable_only=False):
    required = ORIGINAL_FIELDS if labeled else {"id", "question", "paragraphs"}
    if set(record) != required:
        raise ValueError("Unexpected original schema; refuse to drop or invent fields")
    if not all(isinstance(record[k], str) and record[k].strip() for k in ("id", "question")):
        raise ValueError("Missing original ID or question")
    prefix = record["id"].split("__")[0]
    if not prefix.startswith(("2hop", "3hop", "4hop")):
        raise ValueError("Invalid hop prefix")
    hops = prefix[0]
    paragraphs = record["paragraphs"]
    indices = [p["idx"] for p in paragraphs]
    if not paragraphs or len(set(indices)) != len(indices):
        raise ValueError("Missing/duplicate paragraph indices")
    if labeled:
        if not isinstance(record["answerable"], bool) or (answerable_only and not record["answerable"]):
            raise ValueError("Expected answerable Ans record")
        if not isinstance(record["answer"], str) or not record["answer"].strip():
            raise ValueError("Missing reference answer")
        if not isinstance(record["answer_aliases"], list) or any(not isinstance(a, str) for a in record["answer_aliases"]):
            raise ValueError("Invalid aliases")
        decomposition = record["question_decomposition"]
        if len(decomposition) != int(hops):
            raise ValueError("Hop count disagrees with decomposition")
        for step in decomposition:
            support = step["paragraph_support_idx"]
            if support is not None and support not in indices:
                raise ValueError("Invalid paragraph support reference")
            if record["answerable"] and support is None:
                raise ValueError("Answerable decomposition lacks support")
    return hops


def raw_row(record):
    return dict(id=record["id"], question=record["question"], answer=record.get("answer", ""),
                answer_aliases_json=json.dumps(record.get("answer_aliases"), ensure_ascii=False),
                answerable_json=json.dumps(record.get("answerable")),
                paragraphs_json=json.dumps(record["paragraphs"], ensure_ascii=False),
                question_decomposition_json=json.dumps(record.get("question_decomposition"), ensure_ascii=False),
                missing_fields_json=json.dumps(sorted(ORIGINAL_FIELDS - set(record))))


def qa_row(record, split, hops):
    return dict(id=record["id"], dataset="musique", split=split, question=record["question"],
                answer=record.get("answer", ""), answer_aliases_json=json.dumps(record.get("answer_aliases", []), ensure_ascii=False),
                level="", hops=hops, question_type=record["id"].split("__")[0],
                supporting_facts_json=json.dumps([{"idx": p["idx"], "title": p["title"]}
                                                for p in record["paragraphs"] if p.get("is_supporting")], ensure_ascii=False))


def select(rows):
    selected = []
    for hop, count in QUOTAS.items():
        group = sorted((r for r in rows if r["hops"] == hop),
                       key=lambda r: (digest((SEED + ":" + r["id"]).encode()), r["id"]))
        if len(group) < count:
            raise ValueError("Insufficient dev rows for quota")
        selected.extend(group[:count])
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / "datasets/musique_sources.json").read_text())
    directory = sources(config, args.offline)
    outputs, counts, dev = [], {}, []

    def export(name, rows, fields=None):
        data = csv_bytes(rows) if fields is None else csv_bytes(rows, fields)
        preserve_write(ROOT / "datasets" / name, data)
        outputs.append(dict(file=name, rows=len(rows), bytes=len(data), sha256=digest(data)))

    for variant in ("ans", "full"):
        split_ids = set()
        for split in ("train", "dev", "test"):
            member = f"data/musique_{variant}_v1.0_{split}.jsonl"
            qa, seen, hops_count, answerability = [], set(), Counter(), Counter()

            def records():
                with (directory / member).open(encoding="utf-8") as source:
                    for line in source:
                        record = json.loads(line)
                        hops = validate(record, split != "test", variant == "ans" and split != "test")
                        # Full includes answerable and unanswerable versions of an ID.
                        discriminator = (digest(json_bytes(record)) if variant == "full" and split == "test"
                                         else record.get("answerable"))
                        key = (record["id"], discriminator)
                        if key in seen or record["id"] in split_ids:
                            raise ValueError("Duplicate ID/answerability or cross-split leakage")
                        seen.add(key)
                        hops_count[hops] += 1
                        answerability[str(record.get("answerable"))] += 1
                        if variant == "ans":
                            qa.append(qa_row(record, f"{split}_ans_v1.0", hops))
                        yield raw_row(record)

            shards = write_shards(ROOT / f"datasets/official/musique_{variant}" / split, records(), fields=RAW_FIELDS)
            n = sum(item["rows"] for item in shards)
            if n != config["members"][member]["rows"] or dict(hops_count) != config["members"][member]["hops"]:
                raise ValueError("Official split count mismatch")
            split_ids.update(uid for uid, _ in seen)
            outputs.extend(shards)
            counts[f"{variant}/{split}"] = dict(rows=n, hops=dict(hops_count), answerable=dict(answerability))
            if variant == "ans":
                export(f"musique_ans_{split}.csv", qa)
                if split == "dev":
                    dev = qa
            print(variant, split, n, dict(hops_count), "shards", len(shards), flush=True)
    export("musique_ans_dev_2_3hop.csv", [r for r in dev if r["hops"] in QUOTAS])
    export("musique_ans_dev_sample1000.csv", select(dev))
    singlehop = json.loads((directory / "data/dev_test_singlehop_questions_v1.0.json").read_text())
    export("official/musique_dev_test_singlehop_questions.csv",
           [dict(source=source, record_json=json.dumps(record, ensure_ascii=False))
            for source, records in singlehop.items() for record in records], ["source", "record_json"])
    manifest = dict(schema_version=1, source=config, counts=counts, outputs=outputs, encoding="utf-8-sig", newline="LF",
                    seed=SEED, quotas=QUOTAS, sample_source="ans/dev", semantic_review_status="paused_by_user; no exclusions applied",
                    full_view="official/musique_{ans,full}/<split>/part-*.csv; all original fields recoverable",
                    qa_view="musique_ans_{train,dev,test}.csv; all Ans rows, context omitted; test has no answers",
                    full_unanswerable="MuSiQue-Full deliberately includes unanswerable contexts; do not score as ordinary answerable QA",
                    paper_exact_subset=False)
    preserve_write(ROOT / "datasets/musique_manifest.json", json_bytes(manifest))


if __name__ == "__main__":
    main()
