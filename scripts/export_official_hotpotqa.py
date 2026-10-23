"""Export all pinned official fullwiki rows, plus the fixed 500/300/200 sample."""
import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scope.curation import (FIELDS, QUOTAS, SEED, csv_bytes, digest, json_bytes,
                            ordered_groups, preserve_write)

ROOT = Path(__file__).resolve().parents[1]
RAW_FIELDS = ["id", "question", "answer", "type", "level", "supporting_facts_json", "context_json", "null_fields_json"]


def raw_row(record):
    """Keep every original HF field; JSON columns and null markers round-trip."""
    return {"id": record["id"], "question": record["question"], "answer": record["answer"],
            "type": record["type"], "level": record["level"],
            "supporting_facts_json": json.dumps(record["supporting_facts"], ensure_ascii=False),
            "context_json": json.dumps(record["context"], ensure_ascii=False),
            "null_fields_json": json.dumps([k for k in ("id", "question", "answer", "type", "level") if record[k] is None])}


def qa_row(record, split):
    sf = record["supporting_facts"]
    return dict(id=record["id"], dataset="hotpotqa", split=split, question=record["question"],
                answer=record["answer"] or "", answer_aliases_json="[]", level=record["level"] or "",
                hops="", question_type=record["type"] or "",
                supporting_facts_json=json.dumps([list(x) for x in zip(sf["title"], sf["sent_id"], strict=True)], ensure_ascii=False))


def download(source, config, offline):
    path = ROOT / "datasets/raw/hotpotqa_fullwiki_train" / Path(source["path"]).name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if offline:
            raise FileNotFoundError(path)
        url = f"https://huggingface.co/datasets/{config['repository']}/resolve/{config['revision']}/{source['path']}"
        partial = path.with_suffix(".partial")
        try:
            with urllib.request.urlopen(url, timeout=90) as response, partial.open("wb") as out:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > source["bytes"]:
                        raise ValueError("Download exceeds pinned source size")
                    out.write(chunk)
            if partial.stat().st_size != source["bytes"] or digest(partial.read_bytes()) != source["sha256"]:
                raise ValueError(f"Download checksum mismatch: {path}")
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    if path.stat().st_size != source["bytes"] or digest(path.read_bytes()) != source["sha256"]:
        raise ValueError(f"Cached checksum mismatch: {path}")
    return path


def write_shards(directory, rows, max_bytes=20 * 1024 * 1024, manifest_root=ROOT / "datasets", fields=RAW_FIELDS):
    directory.mkdir(parents=True, exist_ok=True)
    header = csv_bytes([], fields)
    buffer, count, outputs = bytearray(header), 0, []

    def flush():
        path = directory / f"part-{len(outputs) + 1:04}.csv"
        data = bytes(buffer)
        preserve_write(path, data)
        outputs.append(dict(file=path.relative_to(manifest_root).as_posix(), rows=count,
                            bytes=len(data), sha256=digest(data)))

    for row in rows:
        out = io.StringIO(newline="")
        csv.DictWriter(out, fieldnames=fields, lineterminator="\n").writerow(row)
        data = out.getvalue().encode("utf-8")
        if len(data) + len(header) > max_bytes:
            raise ValueError("One record exceeds shard size")
        if count and len(buffer) + len(data) > max_bytes:
            flush()
            buffer, count = bytearray(header), 0
        buffer.extend(data)
        count += 1
    if count:
        flush()
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("pip install -e '.[dataset-prep]' is required only to rebuild the CSVs") from error
    config = json.loads((ROOT / "datasets/hotpotqa_fullwiki_sources.json").read_text())
    expected = {"train": 90447, "validation": 7405, "test": 7405}
    outputs, tables, counts, known_ids = [], {}, {}, set()
    for split, sources in config["splits"].items():
        paths = [download(s, config, args.offline) for s in sources]
        qa = []
        levels, missing = Counter(), Counter()

        def records():
            for path in paths:
                for batch in pq.ParquetFile(path).iter_batches(batch_size=256):
                    for record in batch.to_pylist():
                        if record["id"] in known_ids:
                            raise ValueError("Duplicate official ID across splits")
                        known_ids.add(record["id"])
                        qa.append(qa_row(record, split))
                        levels[record["level"] or "unspecified"] += 1
                        for k in ("answer", "level", "type"):
                            if record[k] is None or record[k] == "":
                                missing[k] += 1
                        yield raw_row(record)
        full = write_shards(ROOT / "datasets/official/hotpotqa_fullwiki" / split, records())
        if len(qa) != expected[split]:
            raise ValueError(f"Unexpected official {split} count: {len(qa)}")
        name = f"hotpotqa_fullwiki_{split}.csv"
        data = csv_bytes(qa)
        preserve_write(ROOT / "datasets" / name, data)
        outputs.extend(full)
        outputs.append(dict(file=name, rows=len(qa), bytes=len(data), sha256=digest(data)))
        tables[split] = qa
        counts[split] = dict(rows=len(qa), levels=dict(levels), missing=dict(missing))
        print(split, counts[split], "full-field CSV shards:", len(full), flush=True)
    groups = ordered_groups(tables["train"])
    selected = [r for level, n in QUOTAS.items() for r in groups[level][:n]]
    data = csv_bytes(selected)
    initial = ROOT / "datasets/hotpotqa_train_review/initial1000.csv"
    if initial.exists() and initial.read_bytes() != data:
        raise ValueError("Selected sample differs from the original review snapshot")
    preserve_write(ROOT / "datasets/hotpotqa_train_sample1000.csv", data)
    outputs.append(dict(file="hotpotqa_train_sample1000.csv", rows=1000, bytes=len(data), sha256=digest(data)))
    manifest = dict(schema_version=1, source=config, counts=counts, encoding="utf-8-sig", newline="LF",
                    full_view="official/hotpotqa_fullwiki/<split>/part-*.csv; every original column including context",
                    qa_view="root hotpotqa_fullwiki_<split>.csv; all rows, context omitted",
                    seed=SEED, quotas=QUOTAS, semantic_review_status="paused_by_user; sample is not a cleaned benchmark",
                    paper_exact_subset=False, outputs=outputs)
    preserve_write(ROOT / "datasets/hotpotqa_fullwiki_manifest.json", json_bytes(manifest))


if __name__ == "__main__":
    main()
