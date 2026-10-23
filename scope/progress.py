"""Opt-in, flushed progress messages; never dump model prompts or credentials."""
from contextlib import contextmanager
from datetime import datetime
import os
import re
import sys
import time
from pathlib import Path
from threading import Event, Lock, Thread

_lock = Lock()


def redact(message):
    message = str(message)
    for name in ("SCOPE_API_KEY", "OPENROUTER_API_KEY", "SERPER_API_KEY", "JINA_API_KEY"):
        if os.environ.get(name):
            message = message.replace(os.environ[name], "[redacted]")
    message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", message)
    return message.replace("\r", "\\r").replace("\n", "\\n")


def log(message):
    verbose = os.environ.get("SCOPE_VERBOSE", "").lower() in {"1", "true", "yes"}
    filename = os.environ.get("SCOPE_LOG_FILE")
    if not verbose and not filename:
        return
    line = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {redact(message)[:2000]}"
    with _lock:
        if verbose:
            # Windows PowerShell 5 treats stderr diagnostics as native errors.
            stream = sys.stdout if os.environ.get("SCOPE_LOG_STDOUT") == "1" else sys.stderr
            print(line, file=stream, flush=True)
        if filename:
            try:
                with Path(filename).open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                print(f"Log file write failed: {type(exc).__name__}", file=sys.stderr, flush=True)


@contextmanager
def stage(label, heartbeat_seconds=15):
    started = time.monotonic()
    log(f"START {label}")
    stopped = Event()
    def heartbeat():
        while not stopped.wait(heartbeat_seconds):
            log(f"WAIT {label} elapsed={time.monotonic() - started:.1f}s")
    worker = None
    if os.environ.get("SCOPE_VERBOSE") or os.environ.get("SCOPE_LOG_FILE"):
        worker = Thread(target=heartbeat, daemon=True)
        worker.start()
    try:
        yield
    except Exception as exc:
        log(f"FAIL {label} elapsed={time.monotonic() - started:.1f}s {type(exc).__name__}: {exc}")
        raise
    else:
        log(f"DONE {label} elapsed={time.monotonic() - started:.1f}s")
    finally:
        stopped.set()
        if worker:
            worker.join()
