import ast
import ipaddress
import json
import operator
import math
import socket
import urllib.parse


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
        if not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("Numeric limit exceeded")
        return value
    return str(visit(ast.parse(expression, mode="eval").body))


def public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Visit requires a public HTTPS URL without credentials")
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(parsed.hostname, 443)):
        raise ValueError(f"Private/local destinations are forbidden: {parsed.hostname}; check system/Clash DNS resolution")
    return url


class WebTools:
    def __init__(self, search=None, reader=None, cache_dir=None):
        from .retrieval import Retrieval
        self.retrieval = Retrieval(search, reader, cache_dir)

    def execute(self, name, arguments):
        if name == "python_repl":
            expression = arguments["expression"]
            return json.dumps({"results": [{"source": "python_repl:" + expression,
                                           "text": arithmetic(expression)}]})
        return self.retrieval.execute(name, arguments)


class DemoTools:
    def execute(self, name, arguments):
        if name == "search":
            return json.dumps({"results": [{"source": "fixture", "text": "Ada Lovelace was born in 1815 (offline fixture)."}]})
        if name == "python_repl":
            return json.dumps({"results": [{"source": "fixture", "text": arithmetic(arguments["expression"])}]})
        raise ValueError(name)
