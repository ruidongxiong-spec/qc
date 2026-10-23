import ast
import ipaddress
import json
import operator
import os
import socket
import urllib.parse
import urllib.request


def arithmetic(expression):
    """Restricted arithmetic, deliberately not arbitrary Python or a security sandbox."""
    if not isinstance(expression, str) or len(expression) > 256:
        raise ValueError("Arithmetic expression too long")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            value = n.value
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            value = (-1 if isinstance(n.op, ast.USub) else 1) * visit(n.operand)
        elif isinstance(n, ast.BinOp) and type(n.op) in ops:
            value = ops[type(n.op)](visit(n.left), visit(n.right))
        else:
            raise ValueError("Only numeric arithmetic is supported")
        if abs(value) > 1e100:
            raise ValueError("Numeric limit exceeded")
        return value
    return str(visit(ast.parse(expression, mode="eval").body))


def public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Visit requires a public HTTPS URL without credentials")
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(parsed.hostname, 443)):
        raise ValueError("Private/local destinations are forbidden")
    return url


class WebTools:
    def execute(self, name, arguments):
        if name == "python_repl":
            return arithmetic(arguments["expression"])
        if name == "search":
            key = os.environ.get("SERPER_API_KEY")
            if not key:
                raise ValueError("SERPER_API_KEY is missing")
            req = urllib.request.Request("https://google.serper.dev/search",
                json.dumps({"q": arguments["query"], "num": 5}).encode(),
                {"X-API-KEY": key, "Content-Type": "application/json"})
        elif name == "visit":
            url = public_url(arguments["url"])
            headers = {}
            if os.environ.get("JINA_API_KEY"):
                headers["Authorization"] = "Bearer " + os.environ["JINA_API_KEY"]
            req = urllib.request.Request("https://r.jina.ai/" + url, headers=headers)
        else:
            raise ValueError(f"Unknown tool {name}")
        with urllib.request.urlopen(req, timeout=45) as response:
            return response.read(64000).decode("utf-8", errors="replace")


class DemoTools:
    def execute(self, name, arguments):
        if name == "search":
            return "Ada Lovelace was born in 1815 (offline fixture)."
        if name == "python_repl":
            return arithmetic(arguments["expression"])
        raise ValueError(name)
