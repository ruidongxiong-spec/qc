import argparse
import json
import hashlib
import platform
import os
from pathlib import Path
import time
import traceback

from .backends import DemoBackend, OpenAIBackend
from .core import Config, Engine
from .evaluation import score
from .datasets import load_questions
from .tools import DemoTools, WebTools
from .progress import log, redact
from .structured import CONTRACT_VERSION


def evaluation_summary(records, instances, runs):
    completed = [r for r in records if r["status"] == "completed"]
    count = len(records)
    summary = {k: sum(r["metrics"][k] for r in completed) / count if count else 0.0 for k in ("em", "f1")}
    summary.update(instances=instances, runs=runs, expected=instances * runs, attempted=count,
                   completed=len(completed), failed=count - len(completed),
                   outcomes={name: sum(r.get("result", {}).get("outcome") == name for r in completed)
                             for name in ("answered", "partial", "abstained")})
    judged = [r for r in completed if "semantic_accuracy" in r["metrics"]]
    if judged:
        summary["semantic_accuracy"] = sum(r["metrics"]["semantic_accuracy"] for r in judged) / count
    return summary


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description="SCOPE research reproduction")
    parser.add_argument("command", choices=["demo", "run", "evaluate"])
    parser.add_argument("--query")
    parser.add_argument("--input", help="CSV or JSONL rows: id, question, answer")
    parser.add_argument("--output", default="runs/result.json")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ablation", choices=["full", "no-dag", "no-memory", "no-verification"], default="full")
    parser.add_argument("--judge-model", help="Optional semantic judge model at same endpoint")
    parser.add_argument("--search", choices=["ddgs", "searxng", "wikipedia", "serper"])
    parser.add_argument("--reader", choices=["direct", "jina"])
    parser.add_argument("--verbose", action="store_true", help="Print live stages, timings and tool errors")
    parser.add_argument("--log-file", help="Append diagnostics to a UTF-8 log file")
    parser.add_argument("--output-retries", type=int, default=1, help="Output correction attempts per model role (0-3); executor uses step budget")
    parser.add_argument("--tokenizer", default=os.environ.get("SCOPE_TOKENIZER_PATH"), help="Matching tokenizer.json for exact capsule token counting; omitted means legacy byte budget")
    parser.add_argument("--observation-chunk-chars", type=int, default=4000)
    args = parser.parse_args()
    if args.verbose:
        os.environ["SCOPE_VERBOSE"] = "1"
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        os.environ["SCOPE_LOG_FILE"] = args.log_file
    cfg = Config(max_steps=args.max_steps, dag=args.ablation != "no-dag",
                 structured_memory=args.ablation != "no-memory", verification=args.ablation != "no-verification",
                 output_retries=args.output_retries, tokenizer_path=args.tokenizer,
                 observation_chunk_chars=args.observation_chunk_chars)
    if args.tokenizer:
        os.environ["SCOPE_TOKENIZER_PATH"] = args.tokenizer
    backend = DemoBackend() if args.command == "demo" else OpenAIBackend()
    tools = DemoTools() if args.command == "demo" else WebTools(search=args.search, reader=args.reader)
    engine = Engine(backend, tools, cfg)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "evaluate":
        if not args.input or args.runs < 1 or (args.limit is not None and args.limit < 1):
            parser.error("evaluate needs --input, positive --runs and --limit")
        rows = load_questions(args.input)
        rows = rows[:args.limit] if args.limit else rows
        if not rows:
            parser.error("Empty evaluation dataset")
        judge = OpenAIBackend(model=args.judge_model) if args.judge_model else None
        summary_path = target.with_suffix(".summary.json")
        if target.exists() or summary_path.exists():
            parser.error("Evaluation output already exists; choose a fresh --output path")
        results = []
        provenance = {"dataset_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
                      "model": backend.model, "python": platform.python_version(), "ablation": args.ablation, "search": getattr(getattr(tools, "retrieval", None), "search", args.search or "ddgs"), "reader": getattr(getattr(tools, "retrieval", None), "reader", args.reader or "direct")}
        provenance["ddgs_backend"] = getattr(getattr(tools, "retrieval", None), "ddgs_backend", None)
        provenance["output_retries"] = cfg.output_retries
        provenance["code_commit"] = os.environ.get("SCOPE_CODE_COMMIT")
        provenance["output_contract_version"] = CONTRACT_VERSION
        provenance["openrouter_free_only"] = getattr(backend, 'is_openrouter', False)
        provenance["openrouter_reasoning"] = getattr(backend, 'reasoning_mode', None)
        provenance.update(model_api=getattr(backend, 'api_mode', None), response_format=getattr(backend, 'response_mode', None),
                          context_tokens=getattr(backend, 'context_tokens', None) if getattr(backend, 'api_mode', None) == 'ollama' else None,
                          max_output_tokens=getattr(backend, 'max_output_tokens', None),
                          capsule_budget=engine.capsule_budget, observation_chunk_chars=cfg.observation_chunk_chars)
        stopped = False
        with target.open("x", encoding="utf-8") as out:
            for run in range(args.runs):
                for row in rows:
                    log(f"sample id={row['id']} run={run + 1}/{args.runs} question={row['question']}")
                    log(f"output={target.resolve()}")
                    start = time.monotonic()
                    usage_start = len(backend.usage)
                    audit = getattr(getattr(tools, "retrieval", None), "audit", [])
                    audit_start = len(audit)
                    record = {"id": row["id"], "run": run, "question": row["question"],
                              "reference": row["answer"], "provenance": provenance}
                    phase = "engine"
                    try:
                        result = engine.run(row["question"])
                        metrics = score(result["answer"], row["answer"])
                        record.update(status="completed", metrics=metrics, result=result)
                        if judge:
                            phase = "judge"
                            verdict = judge.call("judge", {"question": row["question"], "prediction": result["answer"], "reference": row["answer"]}, 0.1)
                            if not isinstance(verdict, dict) or type(verdict.get("correct")) is not bool:
                                raise ValueError("Judge correct must be boolean")
                            metrics["semantic_accuracy"] = float(verdict["correct"])
                    except (Exception, KeyboardInterrupt) as exc:
                        stopped = isinstance(exc, KeyboardInterrupt)
                        error = {"type": type(exc).__name__, "phase": engine.phase if phase == "engine" else phase,
                                 "message": redact(exc)[:1500], "traceback": redact(traceback.format_exc(limit=8))[:6000]}
                        snapshot = dict(engine.last_result or {})
                        snapshot.update(status="error", error=error)
                        record.update(status="error", metrics=None, result=snapshot, error=error)
                        log(f"sample failed id={row['id']} phase={error['phase']} {error['type']}: {error['message']}")
                    record["seconds"] = time.monotonic() - start
                    record["result"]["usage"] = backend.usage[usage_start:]
                    record["retrieval_audit"] = audit[audit_start:]
                    results.append(record)
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    write_json(summary_path, evaluation_summary(results, len(rows), args.runs))
                    log(f"sample saved status={record['status']} outcome={record['result'].get('outcome')} elapsed={record['seconds']:.1f}s answer={record['result'].get('answer', '')}")
                    log(f"reference={row['answer']} metrics={record['metrics']}")
                    if stopped:
                        break
                if stopped:
                    break
        summary = evaluation_summary(results, len(rows), args.runs)
        print(json.dumps(summary, indent=2))
        log(f"saved results={target.resolve()} summary={summary_path.resolve()}")
        if summary["failed"]:
            raise SystemExit(130 if stopped else 1)
    else:
        if args.command == "run" and not args.query:
            parser.error("run requires --query")
        try:
            result = engine.run(args.query or "How many years separate Ada Lovelace's birth and 1843?")
        except Exception:
            write_json(target, engine.last_result)
            log(f"failed run saved to {target.resolve()}")
            raise
        if hasattr(backend, "usage"):
            result["usage"] = backend.usage
        write_json(target, result)
        print(result["answer"])


if __name__ == "__main__":
    main()
