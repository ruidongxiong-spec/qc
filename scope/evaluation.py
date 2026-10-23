"""Explicit JSONL evaluation; local EM/F1 and optional separate semantic judge."""
from collections import Counter
import re
import string


def normalize(text):
    text = str(text).lower().translate(str.maketrans("", "", string.punctuation))
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", text).split())


def score(prediction, references):
    if isinstance(references, str):
        references = [references]
    if not references:
        raise ValueError("At least one reference answer is required")
    p = normalize(prediction).split()
    scores = []
    for ref in references:
        r = normalize(ref).split()
        overlap = sum((Counter(p) & Counter(r)).values())
        f1 = 2 * overlap / (len(p) + len(r)) if p or r else 1.0
        scores.append((float(p == r), f1))
    return {"em": max(s[0] for s in scores), "f1": max(s[1] for s in scores)}
