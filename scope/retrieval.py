"""Interchangeable search/read providers; no implicit provider switching in experiments."""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from .tools import public_url
from .progress import log, stage


class ToolError(ValueError):
    pass


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, data=None, headers=None, jina_endpoint=False):
    if not jina_endpoint:
        public_url(url)
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "SCOPE-Research/0.2", **(headers or {})})
    try:
        redirect = urllib.request.HTTPRedirectHandler() if jina_endpoint else CheckedRedirect()
        with urllib.request.build_opener(redirect).open(req, timeout=30) as r:
            raw = r.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ToolError("Response exceeds 2 MB limit")
            return raw.decode(r.headers.get_content_charset() or "utf-8", errors="replace"), r.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        raise ToolError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).hostname}") from None
    except urllib.error.URLError as exc:
        log(f"HTTP network error host={urllib.parse.urlsplit(url).hostname}: {exc.reason}")
        raise ToolError(f"Network failure for {urllib.parse.urlsplit(url).hostname}") from None
    except http.client.HTTPException as exc:
        raise ToolError(f"HTTP transport failure ({type(exc).__name__}) for {urllib.parse.urlsplit(url).hostname}") from None


class Retrieval:
    def __init__(self, search=None, reader=None, cache_dir=None):
        self.search = search or os.environ.get("SCOPE_SEARCH_PROVIDER", "ddgs")
        self.reader = reader or os.environ.get("SCOPE_READER_PROVIDER", "direct")
        self.ddgs_backend = os.environ.get("SCOPE_DDGS_BACKEND", "duckduckgo")
        if self.search == "ddgs":
            try:
                from ddgs.engines import ENGINES
            except ImportError:
                pass  # The search call reports the missing optional dependency.
            else:
                requested = {name.strip() for name in self.ddgs_backend.split(",")}
                if not requested <= (set(ENGINES["text"]) | {"auto", "all"}):
                    raise ValueError(f"Unsupported DDGS backend: {self.ddgs_backend}. Use auto or: {', '.join(sorted(ENGINES['text']))}")
        self.cache_dir = Path(cache_dir or os.environ.get("SCOPE_CACHE_DIR", "runs/cache"))
        if self.search not in {"ddgs", "searxng", "wikipedia", "serper"}:
            raise ValueError("Unknown search provider")
        if self.reader not in {"direct", "jina"}:
            raise ValueError("Unknown reader provider")
        self.audit = []

    def execute(self, name, arguments):
        provider = self.search if name == "search" else self.reader
        with stage(f"tool action={name} provider={provider} arguments={json.dumps(arguments, ensure_ascii=False)}"):
            try:
                return self._execute(name, arguments)
            except Exception as exc:
                self.audit.append({"action": name, "provider": provider, "status": "error",
                                   "error_type": type(exc).__name__, "retrieved_at": datetime.now(timezone.utc).isoformat()})
                raise

    def _execute(self, name, arguments):
        if name not in {"search", "visit"}:
            raise ToolError("Unknown retrieval action")
        identity = {"version": 1, "action": name, "arguments": arguments,
                    "search": self.search, "reader": self.reader,
                    "searxng": os.environ.get("SEARXNG_BASE_URL", ""),
                    "ddgs_backend": self.ddgs_backend, "region": "us-en"}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / (digest + ".json")
        hit = path.exists()
        if hit:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or record.get("request") != identity or not isinstance(record.get("retrieved_at"), str):
                    raise ValueError("Cache identity/metadata mismatch")
                self._validate_results(record.get("results"))
            except (ValueError, TypeError, UnicodeError) as exc:
                log(f"tool cache invalid ({type(exc).__name__}); fetching fresh response")
                hit = False
        log(f"tool cache={'hit' if hit else 'miss'}")
        if not hit:
            results = self._search(arguments["query"]) if name == "search" else self._visit(arguments["url"])
            self._validate_results(results)
            record = {"request": identity, "retrieved_at": datetime.now(timezone.utc).isoformat(), "results": results}
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        self.audit.append({"action": name, "provider": self.search if name == "search" else self.reader,
                           "status": "ok", "cache_key": digest, "cache_hit": hit, "retrieved_at": record["retrieved_at"]})
        log(f"tool results={len(record['results'])} text_chars={sum(len(r.get('text', '')) for r in record['results'])}")
        # Provenance survives condensation. Page/search content remains untrusted data.
        return json.dumps(record, ensure_ascii=False)

    @staticmethod
    def _validate_results(results):
        if not isinstance(results, list) or not results or any(
            not isinstance(r, dict) or not isinstance(r.get("source"), str) or not r["source"].strip()
            or not isinstance(r.get("text"), str) for r in results
        ):
            raise ToolError("Provider/cache returned malformed or empty results")

    def _search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ToolError("Search query must contain 1-1000 characters")
        if self.search == "ddgs":
            try:
                from ddgs import DDGS
            except ImportError:
                raise ToolError('Install the free-web extra: pip install -e ".[free-web]"') from None
            try:
                proxy = (os.environ.get("DDGS_PROXY") or os.environ.get("HTTPS_PROXY")
                         or os.environ.get("https_proxy"))
                log(f"DDGS backend={self.ddgs_backend} proxy_configured={bool(proxy)}")
                rows = DDGS(timeout=20, proxy=proxy).text(query, max_results=5, backend=self.ddgs_backend, region="us-en")
            except Exception as exc:
                log(f"DDGS underlying error: {type(exc).__name__}: {exc}")
                raise ToolError(f"DDGS retrieval failed ({type(exc).__name__}); check proxy, connectivity and rate limits") from None
            return [{"source": r["href"], "title": r.get("title", ""), "text": r.get("body", "")[:4000]} for r in rows]
        if self.search == "serper":
            key = os.environ.get("SERPER_API_KEY")
            if not key:
                raise ToolError("SERPER_API_KEY is missing")
            text, _ = fetch("https://google.serper.dev/search", json.dumps({"q": query, "num": 5}).encode(),
                            {"X-API-KEY": key, "Content-Type": "application/json"})
            return [{"source": r["link"], "title": r.get("title", ""), "text": r.get("snippet", "")[:4000]}
                    for r in json.loads(text).get("organic", [])[:5]]
        if self.search == "searxng":
            base = os.environ.get("SEARXNG_BASE_URL", "").rstrip("/")
            if not base:
                raise ToolError("Set SEARXNG_BASE_URL to an instance with JSON enabled")
            text, _ = fetch(base + "/search?" + urllib.parse.urlencode({"q": query, "format": "json", "language": "en"}))
            return [{"source": r["url"], "title": r.get("title", ""), "text": r.get("content", "")[:4000]}
                    for r in json.loads(text).get("results", [])[:5]]
        text, _ = fetch("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json"}))
        return [{"source": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
                 "title": r["title"], "text": r["snippet"]} for r in json.loads(text)["query"]["search"]]

    def _visit(self, url):
        if not isinstance(url, str) or any(ord(c) < 32 or ord(c) == 127 for c in url):
            raise ToolError("Visit URL must be a string without control characters")
        if self.reader != "jina":
            public_url(url)
        parts = urllib.parse.urlsplit(url)
        # Models may emit readable spaces/Unicode; preserve existing escapes and
        # query separators, while sending a valid ASCII request target.
        url = urllib.parse.urlunsplit((
            parts.scheme, parts.netloc,
            urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
            urllib.parse.quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"),
            "",
        ))
        if self.reader == "jina":
            headers = {"Authorization": "Bearer " + os.environ["JINA_API_KEY"]} if os.environ.get("JINA_API_KEY") else {}
            text, _ = fetch("https://r.jina.ai/" + url, headers=headers, jina_endpoint=True)
        else:
            raw, content_type = fetch(url)
            if content_type == "text/plain":
                text = raw
            elif content_type in {"text/html", "application/xhtml+xml"}:
                try:
                    from trafilatura import extract
                except ImportError:
                    raise ToolError('Direct HTML reading needs pip install -e ".[free-web]"') from None
                text = extract(raw, url=url, include_tables=True, include_comments=False)
            else:
                raise ToolError(f"Unsupported content type {content_type}; use a suitable reader")
        if not text or not text.strip():
            raise ToolError("No readable text; page may require JavaScript or deny crawling")
        return [{"source": url, "title": "", "text": text[:20000]}]
