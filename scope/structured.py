"""Role-specific decoding schemas; runtime semantic validation remains mandatory."""

CONTRACT_VERSION = 2


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def text(limit=1000):
    return {"type": "string", "maxLength": limit}


def array(items, limit=16):
    return {"type": "array", "items": items, "maxItems": limit}


def schema_for(role, payload):
    sources = payload.get("allowed_sources", [])
    source = {"type": "string", "enum": sources} if sources else text(2000)
    evidence = array(obj({"fact": text(350), "source": source}), 4)
    candidate = {"result": text(500), "evidence": evidence,
                 "confidence": {"type": "number", "enum": [i / 10 for i in range(11)]}}
    if role == "planner":
        return obj({"nodes": array(obj({"id": text(64), "description": text(1000),
                    "dependencies": array(text(64)), "output_format": text(100),
                    "constraints": array(text(300))}), payload.get("max_nodes", 32))})
    if role == "executor":
        # Avoid nested nullable unions in the constrained decoder. Use empty
        # strings/lists and zero for inactive slots; runtime normalizes arguments.
        return obj({"action": {"type": "string", "enum": payload.get("allowed_actions", ["search", "visit", "python_repl", "finish"])},
                    "arguments": obj({"query": text(300), "url": text(2000), "expression": text(300)}),
                    "result": candidate["result"],
                    "evidence": evidence,
                    "confidence": candidate["confidence"]})
    if role == "extractor":
        return obj({"new_findings": array(obj({"fact": text(350), "source": source}), 8),
                    "missing_info": array(text(250), 6), "new_leads": array(text(2000), 6)})
    if role == "verifier":
        return obj({"verdict": {"type": "string", "enum": ["Pass", "Uncertain", "Rejected"]}, "reason": text(80)})
    if role == "forced_extractor":
        return obj(candidate)
    if role == "synthesizer":
        return obj({"answer": {"type": "string", "minLength": 1, "maxLength": 2000}})
    if role == "judge":
        return obj({"correct": {"type": "boolean"}, "reason": text(500)})
    raise ValueError(f"Unknown model role: {role}")
