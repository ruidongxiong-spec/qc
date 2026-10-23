"""Count with an explicit tokenizer.json; prepare a revision-pinned local artifact."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request
from .progress import stage


class TokenCounter:
    def __init__(self, path):
        from tokenizers import Tokenizer
        self.path = Path(path)
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.tokenizer = Tokenizer.from_file(str(self.path))
        # Count the full text, regardless of truncation/padding in a supplied file.
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

    def count(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)


def prepare(repo, destination):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Tokenizer repo must be owner/model")
    destination = Path(destination)
    manifest = destination.with_suffix('.manifest.json')
    if destination.exists():
        if not manifest.exists():
            raise ValueError("Tokenizer exists without manifest; provide it explicitly with -TokenizerPath")
        info = json.loads(manifest.read_text(encoding='utf-8'))
        counter = TokenCounter(destination)
        if info.get('repo') != repo or info.get('sha256') != counter.sha256:
            raise ValueError("Tokenizer cache provenance mismatch; use a new destination")
        return info
    with stage(f'tokenizer resolve revision repo={repo}'):
        with urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}', timeout=60) as response:
            revision = json.load(response)['sha']
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError("Invalid tokenizer repository revision")
    url = f'https://huggingface.co/{repo}/resolve/{revision}/tokenizer.json'
    with stage(f'tokenizer download repo={repo} revision={revision}'):
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read(32_000_001)
    if len(data) > 32_000_000:
        raise ValueError("Tokenizer exceeds 32 MB limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_bytes(data)
    counter = TokenCounter(temporary)
    info = {'repo': repo, 'revision': revision, 'sha256': counter.sha256, 'source': url}
    temporary.replace(destination)
    manifest.write_text(json.dumps(info, indent=2), encoding='utf-8')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.output), indent=2), flush=True)


if __name__ == '__main__':
    main()
