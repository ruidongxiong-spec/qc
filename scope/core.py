from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
import time
from typing import Protocol
from .progress import log


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
                raise ValueError(f"Unknown dependency in {n.id}")
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

    def __post_init__(self):
        if min(self.max_steps, self.capsule_tokens, self.verification_log_chars, self.max_nodes, self.state_chars) < 1:
            raise ValueError("Budgets must be positive")


class Backend(Protocol):
    def call(self, role: str, payload: dict, temperature: float) -> dict: ...


class Tools(Protocol):
    def execute(self, name: str, arguments: dict) -> str: ...


class Engine:
    def __init__(self, backend: Backend, tools: Tools, config: Config | None = None):
        self.backend, self.tools = backend, tools
        self.config = config or Config()

    def run(self, query: str) -> dict:
        if not query.strip():
            raise ValueError("Query must not be empty")
        started = time.monotonic()
        cfg = self.config
        if cfg.dag:
            raw = self.backend.call("planner", {"query": query}, 0.7)
            nodes = [Node(n["id"], n["description"], tuple(n.get("dependencies", [])),
                          n.get("output_format", "text"), tuple(n.get("constraints", [])))
                     for n in raw["nodes"]]
        else:
            nodes = [Node("task", query, constraints=("Answer the user's question with evidence",))]
        if len(nodes) > cfg.max_nodes:
            raise ValueError("Planner exceeds max_nodes budget")
        plan = Plan(nodes)
        ordered = plan.ordered()
        log(f"plan nodes={len(ordered)} max_steps_per_node={cfg.max_steps}")
        memory, partials, events, histories = {}, {}, [], {}
        for node in ordered:
            log(f"node={node.id} task={node.description}")
            missing = [d for d in node.dependencies if d not in memory]
            if missing:
                log(f"node={node.id} blocked missing_dependencies={missing}")
                events.append({"node": node.id, "event": "blocked", "dependencies": missing})
                continue
            parents = {d: asdict(memory[d]) for d in node.dependencies}
            state, history, completed_actions = State(), [], []
            observed_sources = {e["source"] for c in parents.values() for e in c["evidence"]}
            base = {"query": query, "node": asdict(node), "parents": parents}
            accepted = False
            for step in range(cfg.max_steps):
                log(f"node={node.id} step={step + 1}/{cfg.max_steps}")
                context = {**base, "state": asdict(state), "step": step,
                           "steps_remaining": cfg.max_steps - step,
                           "completed_actions": list(completed_actions)}
                if not cfg.structured_memory:
                    context["history"] = history
                    context["parent_histories"] = {d: histories[d] for d in node.dependencies}
                try:
                    action = self.backend.call("executor", context, 0.7)
                    name = action.get("action")
                    log(f"node={node.id} action={name}")
                    if name == "finish":
                        candidate = self._candidate(node, action)
                        report = self.backend.call("verifier", {
                            **base, "state": asdict(state), "candidate": asdict(candidate),
                            "execution_log": json.dumps(history, ensure_ascii=False)[-cfg.verification_log_chars:]
                        }, 0.7) if cfg.verification else {
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
                            log(f"node={node.id} verification=Pass")
                            self._check_budget(candidate)
                            memory[node.id] = candidate
                            accepted = True
                            break
                        target = state.negative_constraints if verdict == Verdict.REJECTED else state.feedback
                        log(f"node={node.id} verification={verdict.value} reason={candidate.rationale}")
                        target.append(candidate.rationale)
                    elif name in ("search", "visit", "python_repl"):
                        if any(a.get("action") == name and a.get("arguments", {}) == action.get("arguments", {}) for a in completed_actions):
                            reason = "This action already succeeded; use existing findings, choose a different query/URL, or finish."
                            state.feedback.append(reason)
                            events.append({"node": node.id, "step": step, "event": "duplicate_action", "tool": name})
                            log(f"node={node.id} duplicate action skipped: {reason}")
                            continue
                        try:
                            observation = self.tools.execute(name, action.get("arguments", {}))
                        except (ValueError, OSError, TimeoutError) as exc:
                            log(f"node={node.id} tool_error={type(exc).__name__}: {exc}; retry within remaining steps")
                            state.feedback.append(f"Tool error: {type(exc).__name__}: {exc}")
                            events.append({"node": node.id, "step": step, "event": "tool_error", "tool": name,
                                           "reason": state.feedback[-1]})
                            continue
                        observed = json.loads(observation)
                        observed_sources.update(r["source"] for r in observed["results"])
                        history.append({"action": action, "observation": observation})
                        delta = self.backend.call("extractor", {
                            **base, "state": asdict(state), "action": action, "observation": observation
                        }, 0.0)
                        next_state = State(**json.loads(json.dumps(asdict(state))))
                        next_state.update(delta)
                        if any(f["source"] not in observed_sources for f in next_state.new_findings):
                            raise ValueError("Extractor cited an unobserved source")
                        if len(json.dumps(asdict(next_state), ensure_ascii=False)) > cfg.state_chars:
                            raise ValueError("Compact state exceeds state_chars budget; reduce new findings")
                        state = next_state
                        completed_actions.append(action)
                        events.append({"node": node.id, "step": step, "event": "tool", "tool": name})
                    else:
                        raise ValueError(f"Invalid action: {name}")
                except (ValueError, KeyError, TypeError, SyntaxError, ZeroDivisionError) as exc:
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
                    raw = self.backend.call("forced_extractor", {**base, "state": asdict(state)}, 0.0)
                    partial = self._candidate(node, raw)
                    partial.status = Verdict.UNCERTAIN
                    partial.rationale = "Step budget exhausted; unverified partial result"
                    self._check_budget(partial)
                    partials[node.id] = partial
                except (ValueError, KeyError, TypeError) as exc:
                    reason = f"Invalid partial result: {type(exc).__name__}: {exc}"
                    log(f"node={node.id} {reason}; leaving node unresolved")
                    events.append({"node": node.id, "event": "partial_error", "reason": reason[:500]})
                events.append({"node": node.id, "event": "timeout"})
        try:
            final = self.backend.call("synthesizer", {
                "query": query, "verified_memory": {k: asdict(v) for k, v in memory.items()},
                "unresolved": [n.id for n in ordered if n.id not in memory]
            }, 0.0)
            if not isinstance(final, dict) or not isinstance(final.get("answer"), str) or not final["answer"].strip():
                raise ValueError("Synthesizer requires a nonempty answer string")
        except (ValueError, KeyError, TypeError) as exc:
            reason = f"Invalid synthesis: {type(exc).__name__}: {exc}"
            log(f"{reason}; saving explicit abstention")
            events.append({"event": "synthesis_error", "reason": reason[:500], "fallback": "Insufficient evidence"})
            final = {"answer": "Insufficient evidence"}
        return {"query": query, "answer": final["answer"], "seconds": time.monotonic() - started, "config": asdict(cfg),
                "plan": [asdict(n) for n in ordered],
                "memory": {k: asdict(v) for k, v in memory.items()},
                "partials": {k: asdict(v) for k, v in partials.items()}, "events": events}

    def _candidate(self, node: Node, raw: dict) -> Capsule:
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
        # Byte count is a conservative bound for byte-level tokenizers, not an
        # exact model tokenizer. Include rationale and status in the wire budget.
        if len(json.dumps(asdict(capsule), ensure_ascii=False).encode()) > self.config.capsule_tokens:
            raise ValueError("Capsule exceeds conservative byte/token budget; shorten result/evidence")
