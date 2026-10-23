"""Run the same cases against an explicit model matrix, with resumable records."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time

from .backends import DemoBackend, OpenAIBackend
from .core import Config, Engine
from .evaluation import score
from .datasets import load_questions
from .tools import DemoTools, WebTools
from .progress import redact


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def percentile(values, q):
    values = sorted(values)
    return values[max(0, math.ceil(q * len(values)) - 1)] if values else None


def summarize(records):
    groups = {}
    for record in records:
        groups.setdefault(record["model_name"], []).append(record)
    result = []
    for name, rows in groups.items():
        ok = [r for r in rows if r["status"] == "ok"]
        tokens = [r["total_tokens"] for r in ok if r["total_tokens"] is not None]
        seconds = [r["seconds"] for r in ok]
        result.append({"model": name, "attempted": len(rows), "succeeded": len(ok),
                       "outcomes": {state: sum(r.get("result", {}).get("outcome") == state for r in ok)
                                    for state in ("answered", "partial", "abstained")},
                       "errors": len(rows) - len(ok),
                       "em_success_only": statistics.mean(r["metrics"]["em"] for r in ok) if ok else None,
                       "f1_success_only": statistics.mean(r["metrics"]["f1"] for r in ok) if ok else None,
                       "em_all_attempts": sum(r["metrics"]["em"] for r in ok) / len(rows),
                       "mean_seconds": statistics.mean(seconds) if seconds else None,
                       "p95_seconds": percentile(seconds, .95),
                       "verified_node_rate": statistics.mean(r["verified_node_rate"] for r in ok) if ok else None,
                       "mean_tokens_reported": statistics.mean(tokens) if tokens else None,
                       "token_usage_coverage": len(tokens) / len(ok) if ok else 0,
                       "verification_failures": sum(r.get("verification_failures", 0) for r in ok)})
    return result


def run_matrix(models, rows, output, runs=3, config=None, search="ddgs", reader="direct", resume=False, demo=False):
    config = config or Config()
    if runs < 1 or not rows or not models:
        raise ValueError("Nonempty models/data and positive runs are required")
    names = [m["name"] for m in models]
    if len(set(names)) != len(names) or any(not isinstance(n, str) or not n for n in names):
        raise ValueError("Model names must be nonempty and unique")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Dataset IDs must be unique")
    for row in rows:
        if not isinstance(row["question"], str) or not row["question"].strip():
            raise ValueError("Every row needs a question")
        score("", row["answer"])
    # URLs are not recorded, to avoid exposing credential-bearing query strings.
    identity = fingerprint({"version": 1, "models": models, "rows": rows, "runs": runs,
                            "config": asdict(config), "search": search, "reader": reader, "demo": demo,
                            "ddgs_backend": os.environ.get("SCOPE_DDGS_BACKEND", "duckduckgo") if search == "ddgs" else None})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "manifest.json"
    log = output / "records.jsonl"
    records = []
    if manifest.exists():
        if not resume or json.loads(manifest.read_text())["fingerprint"] != identity:
            raise ValueError("Existing run: use --resume with exactly the same configuration/data")
        if log.exists():
            # Invalid/truncated records fail closed, never silently discard experiment data.
            records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    else:
        if any(output.iterdir()):
            raise ValueError("Output directory must be empty for a new experiment")
        manifest.write_text(json.dumps({"fingerprint": identity, "demo": demo, "runs": runs,
                                       "dataset_hash": fingerprint(rows), "models": names,
                                       "config": asdict(config), "search": search, "reader": reader,
                                       "ddgs_backend": os.environ.get("SCOPE_DDGS_BACKEND", "duckduckgo") if search == "ddgs" else None}, indent=2), encoding="utf-8")
    done = {(r["model_name"], r["run"], r["id"]) for r in records}
    with log.open("a", encoding="utf-8") as handle:
        for model in models:
            for run in range(runs):
                for row in rows:
                    key = (model["name"], run, row["id"])
                    if key in done:
                        continue
                    started = time.monotonic()
                    record = {"model_name": model["name"], "run": run, "id": row["id"], "demo": demo}
                    engine = None
                    try:
                        if demo:
                            backend, tools = DemoBackend(), DemoTools()
                        else:
                            backend = OpenAIBackend(base_url=model["base_url"], model=model["model"],
                                                    key=os.environ.get(model.get("api_key_env", "SCOPE_API_KEY"), ""))
                            tools = WebTools(search=search, reader=reader, cache_dir=output / "retrieval-cache")
                        engine = Engine(backend, tools, config)
                        result = engine.run(row["question"])
                        usage = getattr(backend, "usage", [])
                        complete_usage = bool(usage) and all(type(u.get("total_tokens")) is int for u in usage)
                        metrics = score(result["answer"], row["answer"])
                        record.update(status="ok", metrics=metrics, result=result, usage=usage,
                                      total_tokens=sum(u["total_tokens"] for u in usage) if complete_usage else None,
                                      retrieval_audit=tools.retrieval.audit if hasattr(tools, "retrieval") else [],
                                      verified_node_rate=len(result["memory"]) / len(result["plan"]),
                                      verification_failures=sum(e["event"] == "verification" and e["verdict"] != "Pass" for e in result["events"]))
                    except Exception as exc:
                        record.update(status="error", error_type=type(exc).__name__, error=redact(exc)[:1500])
                        if engine is not None:
                            record.update(result=engine.last_result, error_phase=engine.phase)
                    record["seconds"] = time.monotonic() - started
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    records.append(record)
                    done.add(key)
    report = {"demo": demo, "expected": len(models) * len(rows) * runs, "recorded": len(records),
              "models": summarize(records)}
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Only test the runner with fixtures; no real model performance")
    parser.add_argument("--search", default="ddgs", choices=["ddgs", "searxng", "wikipedia", "serper"])
    parser.add_argument("--reader", default="direct", choices=["direct", "jina"])
    args = parser.parse_args()
    models = json.loads(Path(args.models).read_text(encoding="utf-8"))["models"]
    rows = load_questions(args.input)
    report = run_matrix(models, rows, args.output, runs=args.runs, search=args.search, reader=args.reader,
                        resume=args.resume, demo=args.demo)
    print(json.dumps(report, indent=2))
    if any(m["errors"] for m in report["models"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
