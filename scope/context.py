"""Bound raw tool observations without dropping their tail or changing sources."""
import json


def observation_chunks(observed, max_chars):
    if max_chars < 256:
        raise ValueError("Observation chunk budget must be at least 256 characters")
    chunks, current, used = [], [], 0
    for row in observed['results']:
        text = row['text']
        start = 0
        overlap = min(128, max_chars // 8)
        while True:
            piece = text[start:start + max_chars]
            if current and used + len(piece) > max_chars:
                chunks.append({'results': current})
                current, used = [], 0
            current.append({**row, 'text': piece, 'offset': start, 'source_chars': len(text)})
            used += len(piece)
            if start + len(piece) >= len(text):
                break
            # Small overlap keeps short boundary-spanning facts readable. State
            # merging deduplicates identical findings; offsets preserve provenance.
            start += max_chars - overlap
    if current:
        chunks.append({'results': current})
    return [json.dumps(c, ensure_ascii=False) for c in chunks]
