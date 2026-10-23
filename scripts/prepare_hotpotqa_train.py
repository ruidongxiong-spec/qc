"""Prepare a 500/300/200 training sample, then finalize explicit per-question reviews."""
import argparse
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scope.curation import (SEED, csv_bytes, digest, finalize, json_bytes, normalize_records,
                            prepare, prepare_rows, preserve_write)

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_URL = "https://curtis.ml.cmu.edu/datasets/hotpot/hotpot_train_v1.1.json"


def prepare_hf(offline, reserve):
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Install optional preparation dependency: pip install -e '.[dataset-prep]'") from error
    config = json.loads((ROOT / "datasets/hotpotqa_train_sources.json").read_text())
    directory = ROOT / "datasets/raw/hotpotqa_fullwiki_train"
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for source in config["files"]:
        path = directory / Path(source["path"]).name
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
                            raise ValueError("Source larger than pinned size")
                        out.write(chunk)
                if partial.stat().st_size != source["bytes"] or digest(partial.read_bytes()) != source["sha256"]:
                    raise ValueError(f"Source checksum mismatch: {path.name}")
                partial.replace(path)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
        if path.stat().st_size != source["bytes"] or digest(path.read_bytes()) != source["sha256"]:
            raise ValueError(f"Cached source checksum mismatch: {path.name}")
        paths.append(path)

    def records():
        for path in paths:
            parquet = pq.ParquetFile(path)
            if "context" not in parquet.schema_arrow.names:
                raise ValueError("Original parquet is missing its context column")
            columns = ["id", "question", "answer", "level", "type", "supporting_facts"]
            for batch in parquet.iter_batches(batch_size=512, columns=columns):
                for r in batch.to_pylist():
                    sf = r["supporting_facts"]
                    yield dict(_id=r["id"], question=r["question"], answer=r["answer"],
                               level=r["level"], type=r["type"],
                               supporting_facts=[list(x) for x in zip(sf["title"], sf["sent_id"], strict=True)])
    rows = normalize_records(records(), require_context=False)
    metadata = dict(source_url=f"https://huggingface.co/datasets/{config['repository']}/tree/{config['revision']}/fullwiki",
                    source_sha256=digest(json_bytes(config)), source_bytes=sum(x["bytes"] for x in config["files"]),
                    source_format="huggingface_parquet", source_manifest=config,
                    source_hash_scope="SHA256 of canonical source manifest JSON; each original file separately verified")
    return prepare_rows(rows, metadata, reserve)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "finalize"])
    parser.add_argument("--source", type=Path, help="Optional original CMU JSON; default is pinned HF fullwiki train")
    parser.add_argument("--source-url", default=OFFICIAL_URL, help="Original download URL recorded in provenance")
    parser.add_argument("--directory", type=Path, default=ROOT / "datasets/hotpotqa_train_review")
    parser.add_argument("--reserve", type=int, default=200, help="Additional candidates per level; not automatically accepted")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.command == "finalize":
        manifest = finalize(args.directory)
        print("Finalized", manifest["final_counts"], "replacements:", manifest["replacements"])
        return
    if args.source is None:
        initial, candidates, provenance, review = prepare_hf(args.offline, args.reserve)
    elif not args.source.exists():
        if args.offline:
            parser.error(f"Missing local source: {args.source}")
        args.source.parent.mkdir(parents=True, exist_ok=True)
        partial = args.source.with_name(args.source.name + ".partial")
        try:
            request = urllib.request.Request(args.source_url, headers={"User-Agent": "SCOPE-Dataset-Preparation/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as out:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 800 * 1024 * 1024:
                        raise ValueError("Source exceeds 800 MiB limit")
                    out.write(chunk)
            # Validate the full training split before retaining a completed download.
            initial, candidates, provenance, review = prepare(partial.read_bytes(), args.source_url, args.reserve)
            partial.replace(args.source)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    else:
        initial, candidates, provenance, review = prepare(args.source.read_bytes(), args.source_url, args.reserve)
    args.directory.mkdir(parents=True, exist_ok=True)
    outputs = {"initial1000.csv": csv_bytes(initial), "candidates.csv": csv_bytes(candidates),
               "provenance.json": json_bytes(provenance)}
    for name, data in outputs.items():
        path = args.directory / name
        if path.exists() and path.read_bytes() != data:
            raise FileExistsError(f"Different existing snapshot: {path}")
    for name, data in outputs.items():
        preserve_write(args.directory / name, data)
    # A repeated preparation must preserve decisions already entered by a reviewer.
    if not (args.directory / "reviews.json").exists():
        preserve_write(args.directory / "reviews.json", json_bytes(review))
    print(f"Prepared initial 1000 + reserves with seed {SEED}.")
    print(f"Pending: individually review questions in {args.directory / 'candidates.csv'}.")
    print("No reviewed final dataset has been generated.")


if __name__ == "__main__":
    main()
