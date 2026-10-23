import argparse
import json
import hashlib
import platform
from pathlib import Path
import time

from .backends import DemoBackend, OpenAIBackend
from .core import Config, Engine
from .evaluation import score
from .tools import DemoTools, WebTools


def main():
    parser = argparse.ArgumentParser(description="SCOPE research reproduction")
    parser.add_argument("command", choices=["demo", "run", "evaluate"])
    parser.add_argument("--query")
    parser.add_argument("--input", help="JSONL rows: id, question, answer (string or list)")
    parser.add_argument("--output", default="runs/result.json")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ablation", choices=["full", "no-dag", "no-memory", "no-verification"], default="full")
    parser.add_argument("--judge-model", help="Optional semantic judge model at same endpoint")
    args = parser.parse_args()
    cfg = Config(max_steps=args.max_steps, dag=args.ablation != "no-dag",
                 structured_memory=args.ablation != "no-memory", verification=args.ablation != "no-verification")
    backend = DemoBackend() if args.command == "demo" else OpenAIBackend()
    tools = DemoTools() if args.command == "demo" else WebTools()
    engine = Engine(backend, tools, cfg)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "evaluate":
        if not args.input or args.runs < 1 or (args.limit is not None and args.limit < 1):
            parser.error("evaluate needs --input, positive --runs and --limit")
        rows = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
        rows = rows[:args.limit] if args.limit else rows
        if not rows:
            parser.error("Empty evaluation dataset")
        judge = OpenAIBackend(model=args.judge_model) if args.judge_model else None
        summary_path = target.with_suffix(".summary.json")
        if target.exists() or summary_path.exists():
            parser.error("Evaluation output already exists; choose a fresh --output path")
        results = []
        provenance = {"dataset_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
                      "model": backend.model, "python": platform.python_version(), "ablation": args.ablation}
        with target.open("x") as out:
            for run in range(args.runs):
                for row in rows:
                    start = time.monotonic()
                    usage_start = len(backend.usage)
                    result = engine.run(row["question"])
                    result["usage"] = backend.usage[usage_start:]
                    metrics = score(result["answer"], row["answer"])
                    if judge:
                        verdict = judge.call("judge", {"question": row["question"], "prediction": result["answer"], "reference": row["answer"]}, 0.1)
                        if type(verdict.get("correct")) is not bool:
                            raise ValueError("Judge correct must be boolean")
                        metrics["semantic_accuracy"] = float(verdict["correct"])
                    record = {"id": row["id"], "run": run, "seconds": time.monotonic() - start,
                              "metrics": metrics, "result": result, "provenance": provenance}
                    results.append(record)
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
        summary = {k: sum(r["metrics"][k] for r in results) / len(results) for k in results[0]["metrics"]}
        summary.update(instances=len(rows), runs=args.runs, completed=len(results))
        summary_path.write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    else:
        if args.command == "run" and not args.query:
            parser.error("run requires --query")
        result = engine.run(args.query or "How many years separate Ada Lovelace's birth and 1843?")
        if hasattr(backend, "usage"):
            result["usage"] = backend.usage
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(result["answer"])


if __name__ == "__main__":
    main()
