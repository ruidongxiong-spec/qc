"""Role-specific decoding schemas; runtime semantic validation remains mandatory."""


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
                 "confidence": {"type": "number", "minimum": 0, "maximum": 1}}
    if role == "planner":
        return obj({"nodes": array(obj({"id": text(64), "description": text(1000),
                    "dependencies": array(text(64)), "output_format": text(100),
                    "constraints": array(text(300))}), payload.get("max_nodes", 32))})
    if role == "executor":
        # One object root works with both OpenAI strict schemas and Ollama.
        # Inapplicable fields must be null/empty; runtime checks match action to arguments.
        nullable = {"anyOf": [text(2000), {"type": "null"}]}
        return obj({"action": {"type": "string", "enum": payload.get("allowed_actions", ["search", "visit", "python_repl", "finish"])},
                    "arguments": {"anyOf": [obj({"query": nullable, "url": nullable, "expression": nullable}), {"type": "null"}]},
                    "result": {"anyOf": [candidate["result"], {"type": "null"}]},
                    "evidence": evidence,
                    "confidence": {"anyOf": [candidate["confidence"], {"type": "null"}]}})
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
