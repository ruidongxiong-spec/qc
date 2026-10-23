"""Live DDGS/Jina checks with a fresh cache; no model credentials needed."""
import argparse
import json
import tempfile

from .retrieval import Retrieval


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="Python programming language official website")
    parser.add_argument("--url", default="https://www.python.org")
    args = parser.parse_args()
    failed = False
    rows = None
    with tempfile.TemporaryDirectory() as cache:
        tools = Retrieval(search="ddgs", reader="jina", cache_dir=cache)

        def check(label, action, arguments):
            nonlocal failed
            try:
                results = json.loads(tools.execute(action, arguments))["results"]
                print(f"[PASS] {label}: {len(results)} results; {results[0]['source']}")
                print(results[0]["text"][:200])
                return results
            except Exception as exc:
                failed = True
                print(f"[FAIL] {label}: {exc}")
                return None

        rows = check("DDGS", "search", {"query": args.query})
        check("Jina", "visit", {"url": args.url})
        if rows:
            check("Search -> Jina", "visit", {"url": rows[0]["source"]})
        else:
            print("[SKIP] Search -> Jina: search failed")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
