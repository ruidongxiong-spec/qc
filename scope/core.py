from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
import time
from typing import Protocol
from .progress import log, redact
from .errors import ModelOutputError
from .context import observation_chunks


class Verdict(str, Enum):
    PASS = "Pass"
    UNCERTAIN = "Uncertain"
    REJECTED = "Rejected"


@dataclass(frozen=True)
class Node:
    id: str
    description: str
    dependencies: tuple[str, ...] = ()
    output_format: str = "text"
    constraints: tuple[str, ...] = ()


@dataclass
class Plan:
    nodes: list[Node]

    def ordered(self) -> list[Node]:
        for n in self.nodes:
            if not isinstance(n.id, str) or not isinstance(n.description, str) or not n.description.strip():
                raise ValueError("Node IDs and descriptions must be nonempty strings")
            if any(not isinstance(d, str) for d in (*n.dependencies, *n.constraints)):
                raise ValueError("Dependencies and constraints must be strings")
        index = {n.id: n for n in self.nodes}
        if not index or len(index) != len(self.nodes) or any(not n.id for n in self.nodes):
            raise ValueError("Plan needs nonempty, unique node IDs")
        for n in self.nodes:
            if any(d not in index for d in n.dependencies):
                raise ValueError(f"Unknown dependency in {n.id}: {[d for d in n.dependencies if d not in index]}; valid IDs: {list(index)}")
        ordered, pending = [], list(self.nodes)
        while pending:
            ready = [n for n in pending if set(n.dependencies) <= {x.id for x in ordered}]
            if not ready:
                raise ValueError("Plan contains a cycle")
            ordered.extend(ready)
            pending = [n for n in pending if n not in ready]
        return ordered


@dataclass
class State:
    new_findings: list[dict] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    new_leads: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    negative_constraints: list[str] = field(default_factory=list)

    def update(self, delta: dict) -> None:
        if not isinstance(delta, dict):
            raise ValueError("Extractor output must be an object")
        findings = delta.get("new_findings", [])
        if not isinstance(findings, list) or any(
            not isinstance(f, dict) or not isinstance(f.get("fact"), str)
            or not isinstance(f.get("source"), str) for f in findings
        ):
            raise ValueError("Findings must be objects with fact and source strings")
        for f in findings:
            if f not in self.new_findings:
                self.new_findings.append(f)
        for key in ("missing_info", "new_leads"):
            if key in delta:
                values = delta[key]
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError(f"{key} must be a list of strings")
                setattr(self, key, list(dict.fromkeys(values)))


@dataclass
class Capsule:
    node_id: str
    result: str
    evidence: list[dict]
    confidence: float
    rationale: str
    status: Verdict


@dataclass(frozen=True)
class Config:
    max_steps: int = 8
    capsule_tokens: int = 512
    verification_log_chars: int = 15000
    verification: bool = True
    structured_memory: bool = True
    dag: bool = True
    max_nodes: int = 32
    state_chars: int = 12000
    output_retries: int = 1
    tokenizer_path: str | None = None
    observation_chunk_chars: int = 4000

    def __post_init__(self):
        if min(self.max_steps, self.capsule_tokens, self.verification_log_chars, self.max_nodes, self.state_chars) < 1:
            raise ValueError("Budgets must be positive")
        if type(self.output_retries) is not int or not 0 <= self.output_retries <= 3:
            raise ValueError("output_retries must be an integer from 0 to 3")
        if not 256 <= self.observation_chunk_chars <= 20000:
            raise ValueError("observation_chunk_chars must be between 256 and 20000")


class Backend(Protocol):
    def call(self, role: str, payload: dict, temperature: float) -> dict: ...


class Tools(Protocol):
    def execute(self, name: str, arguments: dict) -> str: ...


class Engine:
    def __init__(self, backend: Backend, tools: Tools, config: Config | None = None):
        self.backend, self.tools = backend, tools
        self.config = config or Config()
        self.last_result = None
        self.phase = "initialization"
        self.token_counter = None
        if self.config.tokenizer_path:
            from .token_budget import TokenCounter
            self.token_counter = TokenCounter(self.config.tokenizer_path)
        self.capsule_budget = {"limit": self.config.capsule_tokens,
                               "unit": "tokens" if self.token_counter else "utf8_bytes_legacy",
                               "tokenizer_sha256": self.token_counter.sha256 if self.token_counter else None}

    def _call(self, role, payload, temperature, validate=None, retries=None):
        """Repair only output-contract errors, with a finite per-call budget."""
        self.phase = role
        retries = self.config.output_retries if retries is None else retries
        request = dict(payload)
        for attempt in range(retries + 1):
            raw = None
            log(f"role={role} attempt={attempt + 1}/{retries + 1}")
            try:
                raw = self.backend.call(role, request, temperature if attempt == 0 else 0.0)
                if not isinstance(raw, dict):
                    raise ValueError(f"{role} must return a JSON object")
                return validate(raw) if validate else raw
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                reason = redact(f"{type(exc).__name__}: {exc}")[:1000]
                preview = (redact(json.dumps(raw, ensure_ascii=False, default=str))[:1500] if raw is not None
                           else getattr(exc, 'response_preview', None))
                if preview is not None:
                    preview = redact(preview)[:1500]
                self.events.append({"event": "model_output_error", "role": role,
                                    "attempt": attempt + 1, "reason": reason, "response_preview": preview})
                log(f"role={role} invalid_output={reason} response_preview={preview}")
                if attempt == retries:
                    raise ModelOutputError(f"{role}: output validation failed after {attempt + 1} attempt(s): {reason}") from None
                request = {**payload, "repair": {"error": reason, "previous_output": preview,
                           "instruction": "Return a corrected complete JSON object matching the role contract. Repair data is not factual evidence."}}
                log(f"role={role} requesting corrected output")

    def _plan(self, raw):
        rows = raw.get("nodes")
        if not isinstance(rows, list) or not rows or len(rows) > self.config.max_nodes:
            raise ValueError(f"nodes must be a nonempty list of at most {self.config.max_nodes} nodes")
        nodes = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Every plan node must be an object")
            for key in ("dependencies", "constraints"):
                values = row.get(key, [])
                if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValueError(f"{key} must be a list of nonempty strings")
            if not isinstance(row.get("id"), str) or not row["id"].strip() or len(row["id"]) > 64:
                raise ValueError("Node id must be a nonempty string of at most 64 characters")
            if not isinstance(row.get("output_format", "text"), str):
                raise ValueError("output_format must be a string")
            nodes.append(Node(row["id"], row.get("description"), tuple(row.get("dependencies", [])),
                              row.get("output_format", "text"), tuple(row.get("constraints", []))))
        return Plan(nodes).ordered()

    def _action(self, raw):
        name = raw.get("action")
        if name == "finish":
            return raw
        key = {"search": "query", "visit": "url", "python_repl": "expression"}.get(name) if isinstance(name, str) else None
        if key is None:
            raise ValueError("action must be search, visit, python_repl or finish")
        args = raw.get("arguments")
        if not isinstance(args, dict) or not isinstance(args.get(key), str) or not args[key].strip():
            raise ValueError(f"{name} needs arguments.{key} as a nonempty string")
        if len(args[key]) > (1000 if name == "search" else 8000):
            raise ValueError(f"{name} argument is too long")
        # Normalize nullable schema slots so duplicate detection/cache identity
        # depends only on the argument consumed by this tool.
        return {"action": name, "arguments": {key: args[key]}}

    @staticmethod
    def _verdict(raw):
        Verdict(raw.get("verdict"))
        if not isinstance(raw.get("reason"), str) or not raw["reason"].strip() or len(raw["reason"]) > 80:
            raise ValueError("Verifier reason must contain 1-80 characters")
        return raw

    @staticmethod
    def _answer(raw):
        if not isinstance(raw.get("answer"), str) or not raw["answer"].strip():
            raise ValueError("Synthesizer requires a nonempty answer string")
        if len(raw["answer"]) > 10000:
            raise ValueError("Synthesizer answer exceeds 10000 characters; answer concisely")
        return raw

    def run(self, query: str) -> dict:
        started = time.monotonic()
        self.events = []
        self.last_result = {"query": query, "answer": "", "status": "running", "outcome": None,
                            "plan": [], "memory": {}, "partials": {}, "events": self.events,
                            "config": asdict(self.config), "capsule_budget": self.capsule_budget}
        log(f"capsule budget={self.capsule_budget}")
        self.phase = "input"
        try:
            result = self._run(query)
            self.last_result.update(result, status="completed")
            return self.last_result
        except Exception as exc:
            error = {"type": type(exc).__name__, "phase": self.phase, "message": redact(exc)[:1500]}
            self.last_result.update(status="error", error=error)
            self.events.append({"event": "run_error", **error})
            log(f"run failed phase={self.phase} {error['type']}: {error['message']}")
            raise
        finally:
            self.last_result["seconds"] = time.monotonic() - started

    def _run(self, query: str) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must not be empty")
        started = time.monotonic()
        cfg = self.config
        if cfg.dag:
            ordered = self._call("planner", {"query": query, "max_nodes": cfg.max_nodes}, 0.7, self._plan)
        else:
            ordered = [Node("task", query, constraints=("Answer the user's question with evidence",))]
        self.last_result["plan"] = [asdict(n) for n in ordered]
        log(f"plan nodes={len(ordered)} max_steps_per_node={cfg.max_steps}")
        memory, partials, events, histories = {}, {}, self.events, {}
        for node in ordered:
            log(f"node={node.id} task={node.description}")
            missing = [d for d in node.dependencies if d not in memory]
            if missing:
                log(f"node={node.id} blocked missing_dependencies={missing}")
                events.append({"node": node.id, "event": "blocked", "dependencies": missing})
                continue
            parents = {d: asdict(memory[d]) for d in node.dependencies}
            state, history, completed_actions = State(), [], []
            visited_urls, failed_actions = set(), []
            repeated_actions = 0
            recover_output = False
            observed_sources = {e["source"] for c in parents.values() for e in c["evidence"]}
            base = {"query": query, "node": asdict(node), "parents": parents}
            accepted = False
            for step in range(cfg.max_steps):
                state.feedback = [s[:500] for s in state.feedback[-8:]]
                state.negative_constraints = [s[:500] for s in state.negative_constraints[-8:]]
                log(f"node={node.id} step={step + 1}/{cfg.max_steps}")
                finish_requested = bool(state.new_findings) and (step == cfg.max_steps - 1 or repeated_actions >= 2)
                allowed_actions = ["finish"] if finish_requested else ["search", "visit", "python_repl", "finish"]
                context = {**base, "state": asdict(state), "step": step,
                           "steps_remaining": cfg.max_steps - step,
                           "completed_actions": list(completed_actions), "capsule_budget": self.capsule_budget,
                           "failed_actions": failed_actions[-4:], "visited_urls": sorted(visited_urls),
                           "allowed_sources": sorted(observed_sources), "allowed_actions": allowed_actions,
                           "finish_requested": finish_requested,
                           "output_recovery": recover_output}
                if not cfg.structured_memory:
                    context["history"] = history
                    context["parent_histories"] = {d: histories[d] for d in node.dependencies}
                try:
                    def validate_action(raw):
                        action = self._action(raw)
                        if action['action'] not in allowed_actions:
                            raise ValueError("A finish candidate is required now; cite existing findings for verification")
                        return action
                    action = self._call("executor", context, 0.0 if recover_output else 0.7, validate_action, retries=0)
                    recover_output = False
                    name = action.get("action")
                    log(f"node={node.id} action={name}")
                    if name == "finish":
                        repeated_actions = 0
                        candidate = self._candidate(node, action)
                        report = self._call("verifier", {
                            **base, "state": asdict(state), "candidate": asdict(candidate),
                            "execution_log": json.dumps(history, ensure_ascii=False)[-cfg.verification_log_chars:]
                        }, 0.7, self._verdict) if cfg.verification else {
                            "verdict": "Pass", "reason": "Verification disabled: ablation only"}
                        verdict = Verdict(report["verdict"])
                        candidate.status, candidate.rationale = verdict, str(report["reason"])
                        events.append({"node": node.id, "step": step, "event": "verification",
                                       "verdict": verdict.value, "reason": candidate.rationale})
                        if verdict == Verdict.PASS and cfg.verification:
                            if not candidate.evidence or any(e["source"] not in observed_sources for e in candidate.evidence):
                                verdict = Verdict.REJECTED
                                candidate.status = verdict
                                candidate.rationale = "Evidence must cite observed tool or verified parent sources"
                                events[-1].update(verdict=verdict.value, reason=candidate.rationale)
                        if verdict == Verdict.PASS:
                            self._check_budget(candidate)
                            log(f"node={node.id} verification=Pass; committing verified result")
                            memory[node.id] = candidate
                            self.last_result["memory"][node.id] = asdict(candidate)
                            accepted = True
                            break
                        target = state.negative_constraints if verdict == Verdict.REJECTED else state.feedback
                        log(f"node={node.id} verification={verdict.value} reason={candidate.rationale}")
                        target.append(candidate.rationale)
                    elif name in ("search", "visit", "python_repl"):
                        if any(a.get("action") == name and a.get("arguments", {}) == action.get("arguments", {}) for a in completed_actions):
                            repeated_actions += 1
                            reason = "This action already succeeded; use existing findings, choose a different query/URL, or finish."
                            state.feedback.append(reason)
                            events.append({"node": node.id, "step": step, "event": "duplicate_action", "tool": name})
                            log(f"node={node.id} duplicate action skipped: {reason}")
                            continue
                        repeated_actions = 0
                        try:
                            self.phase = f"tool:{name}"
                            observation = self.tools.execute(name, action.get("arguments", {}))
                        except (ValueError, OSError, TimeoutError) as exc:
                            failed_actions.append({"action": name, "arguments": action.get('arguments', {}), "error": str(exc)[:300]})
                            log(f"node={node.id} tool_error={type(exc).__name__}: {exc}; retry within remaining steps")
                            state.feedback.append(f"Tool error: {type(exc).__name__}: {exc}")
                            events.append({"node": node.id, "step": step, "event": "tool_error", "tool": name,
                                           "reason": state.feedback[-1]})
                            continue
                        observed = json.loads(observation)
                        if not isinstance(observed, dict) or not isinstance(observed.get("results"), list) or not observed["results"]:
                            raise ValueError("Tool observation requires a nonempty results list")
                        if any(not isinstance(r, dict) or not isinstance(r.get("source"), str) or not isinstance(r.get("text"), str) for r in observed["results"]):
                            raise ValueError("Tool results need source and text strings")
                        observed_sources.update(r["source"] for r in observed["results"])
                        history.append({"action": action, "observation": observation})
                        if name == 'visit':
                            visited_urls.update([action['arguments']['url'], *(r['source'] for r in observed['results'])])
                        def extract_state(delta):
                            if "new_findings" not in delta:
                                raise ValueError("Extractor must include new_findings (use [] when no facts were found)")
                            updated = State(**json.loads(json.dumps(asdict(state))))
                            updated.update(delta)
                            if any(f['source'] not in chunk_sources for f in delta['new_findings']):
                                raise ValueError("New findings must cite a source in this observation chunk")
                            if any(f["source"] not in observed_sources for f in updated.new_findings):
                                raise ValueError("Extractor cited an unobserved source; copy exact source strings from allowed_sources")
                            if len(json.dumps(asdict(updated), ensure_ascii=False)) > cfg.state_chars:
                                raise ValueError("Compact state exceeds state_chars budget; reduce new findings")
                            updated.new_leads = [url for url in updated.new_leads if url not in visited_urls]
                            return updated
                        chunks = observation_chunks(observed, cfg.observation_chunk_chars)
                        extraction_complete = True
                        for chunk_index, chunk in enumerate(chunks, 1):
                            chunk_sources = sorted({r['source'] for r in json.loads(chunk)['results']})
                            log(f"node={node.id} extraction chunk={chunk_index}/{len(chunks)} observation_chars={len(chunk)}")
                            try:
                                previous_count = len(state.new_findings)
                                state = self._call("extractor", {
                                    **base, "state": asdict(state), "action": action, "observation": chunk,
                                    "chunk": {"index": chunk_index, "total": len(chunks)},
                                    "allowed_sources": chunk_sources
                                }, 0.0, extract_state)
                                events.append({"node": node.id, "step": step, "event": "extraction_chunk",
                                               "index": chunk_index, "total": len(chunks), "status": "ok",
                                               "added_findings": len(state.new_findings) - previous_count})
                                log(f"node={node.id} state findings={len(state.new_findings)} added={len(state.new_findings) - previous_count} gaps={len(state.missing_info)} unvisited_leads={len(state.new_leads)}")
                            except ModelOutputError as exc:
                                extraction_complete = False
                                state.feedback.append(f"Chunk {chunk_index} extraction failed: {exc}"[:500])
                                events.append({"node": node.id, "step": step, "event": "schema_error",
                                               "chunk": chunk_index, "reason": str(exc)[:500]})
                        if extraction_complete:
                            completed_actions.append(action)
                        events.append({"node": node.id, "step": step, "event": "tool", "tool": name})
                    else:
                        raise ValueError(f"Invalid action: {name}")
                except (ValueError, KeyError, TypeError, SyntaxError, ZeroDivisionError) as exc:
                    recover_output = True
                    log(f"node={node.id} invalid_output={type(exc).__name__}: {exc}")
                    reason = f"Invalid local output: {type(exc).__name__}: {exc}"
                    state.feedback.append(reason[:500])
                    # Bound accumulated diagnostic context independently of tool findings.
                    state.feedback = state.feedback[-8:]
                    events.append({"node": node.id, "step": step, "event": "schema_error", "reason": reason[:500]})
            histories[node.id] = history
            if not accepted:
                log(f"node={node.id} step budget exhausted; extracting partial result")
                try:
                    def make_partial(raw):
                        partial = self._candidate(node, raw)
                        partial.rationale = "Step budget exhausted; unverified partial result"
                        self._check_budget(partial)
                        return partial
                    partial = self._call("forced_extractor", {**base, "state": asdict(state),
                                         "capsule_budget": self.capsule_budget, "allowed_sources": sorted(observed_sources)}, 0.0, make_partial)
                    partials[node.id] = partial
                    self.last_result["partials"][node.id] = asdict(partial)
                except (ValueError, KeyError, TypeError) as exc:
                    reason = f"Invalid partial result: {type(exc).__name__}: {exc}"
                    log(f"node={node.id} {reason}; leaving node unresolved")
                    events.append({"node": node.id, "event": "partial_error", "reason": reason[:500]})
                events.append({"node": node.id, "event": "timeout"})
        try:
            final = self._call("synthesizer", {
                "query": query, "verified_memory": {k: asdict(v) for k, v in memory.items()},
                "unresolved": [n.id for n in ordered if n.id not in memory]
            }, 0.0, self._answer)
        except (ValueError, KeyError, TypeError) as exc:
            reason = f"Invalid synthesis: {type(exc).__name__}: {exc}"
            log(f"{reason}; saving explicit abstention")
            events.append({"event": "synthesis_error", "reason": reason[:500], "fallback": "Insufficient evidence"})
            final = {"answer": "Insufficient evidence"}
        if not memory:
            final = {"answer": "Insufficient evidence"}
            events.append({"event": "abstention", "reason": "No verified node results"})
        outcome = ("abstained" if final["answer"].strip().lower() == "insufficient evidence" else
                   "answered" if len(memory) == len(ordered) else "partial")
        return {"query": query, "answer": final["answer"], "seconds": time.monotonic() - started, "config": asdict(cfg),
                "outcome": outcome,
                "plan": [asdict(n) for n in ordered],
                "memory": {k: asdict(v) for k, v in memory.items()},
                "partials": {k: asdict(v) for k, v in partials.items()}, "events": events}

    def _candidate(self, node: Node, raw: dict) -> Capsule:
        if not isinstance(raw, dict):
            raise ValueError("Candidate must be an object")
        if isinstance(raw.get("confidence"), bool):
            raise ValueError("Confidence must be numeric, not boolean")
        confidence = float(raw.get("confidence", 0))
        result, evidence = raw.get("result", ""), raw.get("evidence", [])
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Confidence must be finite and between zero and one")
        if not isinstance(result, str) or not result.strip() or not isinstance(evidence, list):
            raise ValueError("Candidate requires a result and evidence list")
        if any(not isinstance(e, dict) or not isinstance(e.get("source"), str)
               or not isinstance(e.get("fact"), str) for e in evidence):
            raise ValueError("Evidence requires source and fact strings")
        capsule = Capsule(node.id, result, evidence, confidence, "", Verdict.UNCERTAIN)
        self._check_budget(capsule)
        return capsule

    def _check_budget(self, capsule: Capsule) -> None:
        # Count the complete capsule, including validation metadata. Legacy byte
        # mode is explicit; token mode requires a supplied, hash-recorded tokenizer.
        serialized = json.dumps(asdict(capsule), ensure_ascii=False)
        size = self.token_counter.count(serialized) if self.token_counter else len(serialized.encode())
        log(f"node={capsule.node_id} capsule_size={size}/{self.config.capsule_tokens} unit={self.capsule_budget['unit']}")
        if size > self.config.capsule_tokens:
            raise ValueError(f"Capsule uses {size} {self.capsule_budget['unit']}, exceeds {self.config.capsule_tokens} budget; shorten result/evidence")
