"""Opt-in, flushed progress messages; never dump model prompts or credentials."""
from contextlib import contextmanager
from datetime import datetime
import os
import re
import sys
import time


def log(message):
    if os.environ.get("SCOPE_VERBOSE", "").lower() not in {"1", "true", "yes"}:
        return
    message = str(message)
    for name in ("SCOPE_API_KEY", "SERPER_API_KEY", "JINA_API_KEY"):
        if os.environ.get(name):
            message = message.replace(os.environ[name], "[redacted]")
    message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", message)
    message = message.replace("\r", "\\r").replace("\n", "\\n")
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message[:1500]}",
          file=sys.stderr, flush=True)


@contextmanager
def stage(label):
    started = time.monotonic()
    log(f"START {label}")
    try:
        yield
    except Exception as exc:
        log(f"FAIL {label} elapsed={time.monotonic() - started:.1f}s {type(exc).__name__}: {exc}")
        raise
    else:
        log(f"DONE {label} elapsed={time.monotonic() - started:.1f}s")
