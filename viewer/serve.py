#!/usr/bin/env python3
"""serve.py — a window onto the phylogeny.

A dependency-free web app for stepping through the generations of the
ouroboros repository. Serves a single-page UI and a small JSON API over the
git history of sandbox/organism.el.

    ./viewer/serve.py                  # http://127.0.0.1:7777
    ./viewer/serve.py --port 8000
    ./viewer/serve.py --host 0.0.0.0   # if you need to reach it remotely

Asking Claude about a generation requires ANTHROPIC_API_KEY in the
environment (the same key the kernel uses). Everything else works without it.

Like the kernel, this file lives outside the sandbox and the organism cannot
reach it. Unlike the kernel, it never writes into the sandbox on its own;
the only mutating operations are the ones you click.
"""

import argparse
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ORGANISM_PATH = "sandbox/organism.el"
JOURNAL_PATH = "sandbox/journal.md"
KERNEL_LOG = os.path.join(ROOT, "kernel.log")

SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
FALLBACK_MODEL = "claude-opus-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Cache of immutable per-commit detail, keyed by full sha.
_detail_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- git helpers

def git(*args: str, check: bool = True) -> str:
    """Run git in the repo root and return stdout as text."""
    result = subprocess.run(
        ["git", "-C", ROOT, *args],
        capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def git_show(sha: str, path: str) -> str | None:
    """File contents at a commit, or None if it did not exist yet."""
    result = subprocess.run(
        ["git", "-C", ROOT, "show", f"{sha}:{path}"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else None


def list_generations() -> dict:
    """Every commit, oldest first, annotated with what the organism did."""
    log = git("log", "--reverse", "--format=%H%x1f%h%x1f%aI%x1f%s")
    touched = set(git("log", "--format=%H", "--", ORGANISM_PATH).split())
    entries = []
    for line in log.splitlines():
        sha, short, date, subject = line.split("\x1f", 3)
        detail = commit_detail(sha)
        entries.append({
            "sha": sha,
            "short": short,
            "date": date,
            "subject": subject,
            "organism_changed": sha in touched,
            "generation": detail["parsed"]["generation"],
            "lines": detail["organism"].count("\n") if detail["organism"] else 0,
        })
    head = git("rev-parse", "HEAD").strip()
    return {"generations": entries, "head": head, "worktree_dirty": worktree_dirty()}


def worktree_dirty() -> bool:
    out = git("status", "--porcelain", "--", ORGANISM_PATH, JOURNAL_PATH)
    return bool(out.strip())


def commit_detail(sha: str) -> dict:
    with _cache_lock:
        cached = _detail_cache.get(sha)
    if cached is not None:
        return cached
    organism = git_show(sha, ORGANISM_PATH) or ""
    journal = git_show(sha, JOURNAL_PATH)
    # `git show --format= <sha> -- <path>` yields the patch, root commit included.
    diff = git("show", "--format=", sha, "--", ORGANISM_PATH)
    meta = git("show", "--no-patch", "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s", sha).strip()
    full, short, author, date, subject = meta.split("\x1f", 4)
    detail = {
        "sha": full,
        "short": short,
        "author": author,
        "date": date,
        "subject": subject,
        "organism": organism,
        "journal": journal,
        "diff": diff,
        "parsed": parse_organism(organism),
    }
    with _cache_lock:
        _detail_cache[full] = detail
    return detail


def worktree_detail() -> dict:
    """The uncommitted state of the sandbox, presented like a generation."""
    def read(path):
        try:
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
    organism = read(ORGANISM_PATH) or ""
    return {
        "sha": "WORKTREE",
        "short": "worktree",
        "author": None,
        "date": None,
        "subject": "uncommitted working tree",
        "organism": organism,
        "journal": read(JOURNAL_PATH),
        "diff": git("diff", "HEAD", "--", ORGANISM_PATH),
        "parsed": parse_organism(organism),
    }


# ------------------------------------------------------------- elisp parsing
# The organism is free to become unrecognisable, so all of this is best-effort:
# every field is None when the shape we expect is gone.

def _read_lisp_string(text: str, idx: int) -> str | None:
    """Read one double-quoted elisp string starting at or after IDX."""
    start = text.find('"', idx)
    if start < 0:
        return None
    out, i = [], start + 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        elif c == '"':
            return "".join(out)
        else:
            out.append(c)
            i += 1
    return None


def _read_balanced(text: str, idx: int) -> str | None:
    """Read one balanced sexp starting at the first ( at or after IDX."""
    start = text.find("(", idx)
    if start < 0:
        return None
    depth, i, in_string = 0, start, False
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\":
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def parse_organism(text: str) -> dict:
    def after(marker):
        pos = text.find(marker)
        return pos + len(marker) if pos >= 0 else None

    parsed = {"generation": None, "model": None, "prompt": None,
              "note": None, "log": None}

    pos = after("(defvar organism-generation")
    if pos is not None:
        m = re.match(r"\s+(\d+)", text[pos:])
        if m:
            parsed["generation"] = int(m.group(1))

    pos = after("(defvar organism-model")
    if pos is not None:
        parsed["model"] = _read_lisp_string(text, pos)

    pos = after("(defvar organism-prompt")
    if pos is not None:
        parsed["prompt"] = _read_lisp_string(text, pos)

    # The note the *next* journal entry will carry usually lives in a setq at
    # the bottom; fall back to the defvar if that is all there is.
    pos = after("(setq organism-note")
    if pos is None:
        pos = after("(defvar organism-note")
    if pos is not None:
        parsed["note"] = _read_lisp_string(text, pos)

    pos = after("(defvar organism-log")
    if pos is not None:
        quote = text.find("'(", pos)
        if quote >= 0:
            parsed["log"] = _read_balanced(text, quote)

    return parsed


# ------------------------------------------------------------- asking Claude

SYSTEM_PROMPT = """\
You are an assistant embedded in a viewer for the "ouroboros" repository.

Ouroboros is an experiment: sandbox/organism.el is an Emacs Lisp program \
that, when run, sends its own source to a language model and overwrites \
itself with the reply. The instruction it sends (organism-prompt) is a \
defvar inside the same file, so a generation can rewrite the code, the \
prompt that governs future rewrites, or both. An immutable shell script \
(kernel.sh) outside the sandbox runs each generation under bwrap, commits \
every surviving generation to git, and reverts any generation that no \
longer parses or no longer defines organism-step. The journal \
(sandbox/journal.md) is appended by the kernel from whatever the organism \
leaves in organism-note, and lags one generation behind by construction.

The user is stepping through the git history of this experiment and is \
currently looking at the generation described below. Answer their questions \
about it: what changed, why it might have changed, what the organism seems \
to be doing, how the prompt is drifting, and so on. Quote the source or \
diff when it helps. Be concrete and honest about uncertainty — the whole \
point of the experiment is that intent must be inferred from text.
"""


def build_context(detail: dict) -> str:
    parts = [f"=== COMMIT ===\n{detail['short']} — {detail['subject']}"]
    if detail.get("date"):
        parts[0] += f" ({detail['date']})"
    gen = detail["parsed"]["generation"]
    if gen is not None:
        parts[0] += f"\norganism-generation at this commit: {gen}"
    parts.append("=== organism.el AT THIS GENERATION ===\n" + detail["organism"])
    if detail["diff"].strip():
        parts.append("=== WHAT THIS GENERATION CHANGED (diff vs parent) ===\n"
                     + detail["diff"])
    if detail["journal"]:
        parts.append("=== JOURNAL AS OF THIS GENERATION ===\n" + detail["journal"])
    return "\n\n".join(parts)


def ask_claude(question: str, detail: dict, history: list) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ApiError(503, "ANTHROPIC_API_KEY is not set in the server's "
                            "environment; export it and restart serve.py.")
    model = (os.environ.get("ANTHROPIC_MODEL")
             or detail["parsed"]["model"]
             or FALLBACK_MODEL)
    messages = [m for m in history
                if m.get("role") in ("user", "assistant") and m.get("content")]
    messages.append({
        "role": "user",
        "content": build_context(detail) + "\n\n=== QUESTION ===\n" + question,
    })
    body = json.dumps({
        "model": model,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL, data=body, method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            parsed = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail_text = e.read().decode(errors="replace")
        raise ApiError(502, f"Anthropic API returned {e.code}: {detail_text}")
    except urllib.error.URLError as e:
        raise ApiError(502, f"could not reach the Anthropic API: {e.reason}")
    reply = "".join(block.get("text", "")
                    for block in parsed.get("content", [])
                    if block.get("type") == "text")
    if not reply:
        raise ApiError(502, f"no text in reply: {json.dumps(parsed)[:500]}")
    return {"reply": reply, "model": model}


# ------------------------------------------------------------ running kernel

class KernelRun:
    """At most one kernel.sh run at a time; output buffered for polling."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.output: list[str] = []
        self.exit_code = None

    def start(self, steps: int) -> bool:
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return False
            self.output = []
            self.exit_code = None
            self.proc = subprocess.Popen(
                [os.path.join(ROOT, "kernel.sh"), "-n", str(steps)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        proc = self.proc
        for line in proc.stdout:
            with self.lock:
                self.output.append(line)
        proc.wait()
        with self.lock:
            self.exit_code = proc.returncode
            _detail_cache.clear()  # new commits may exist now

    def status(self) -> dict:
        with self.lock:
            return {
                "running": self.proc is not None and self.proc.poll() is None,
                "started": self.proc is not None,
                "exit_code": self.exit_code,
                "output": "".join(self.output),
            }


kernel_run = KernelRun()


# -------------------------------------------------------------- http plumbing

class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


MIME = {".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ouroboros-viewer/1"

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; the UI is the log

    # -- responses ------------------------------------------------------

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, name):
        path = os.path.normpath(os.path.join(STATIC, name))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            self.send_json({"error": "not found"}, 404)
            return
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1]
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError(400, "request body is not valid JSON")

    # -- routing --------------------------------------------------------

    def do_GET(self):
        try:
            path = self.path.split("?", 1)[0]
            if path == "/" or path == "/index.html":
                self.send_file("index.html")
            elif path.startswith("/static/"):
                self.send_file(path[len("/static/"):])
            elif path == "/api/generations":
                self.send_json(list_generations())
            elif path.startswith("/api/generation/"):
                self.send_json(self.get_generation(path.rsplit("/", 1)[1]))
            elif path == "/api/run/status":
                self.send_json(kernel_run.status())
            else:
                self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            self.send_json({"error": e.message}, e.status)
        except Exception as e:  # surface anything unexpected to the UI
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        try:
            if self.path == "/api/ask":
                self.send_json(self.post_ask())
            elif self.path == "/api/run":
                self.send_json(self.post_run())
            elif self.path == "/api/restore":
                self.send_json(self.post_restore())
            else:
                self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            self.send_json({"error": e.message}, e.status)
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    # -- handlers -------------------------------------------------------

    def get_generation(self, ref: str) -> dict:
        if ref == "WORKTREE":
            return worktree_detail()
        if ref != "HEAD" and not SHA_RE.match(ref):
            raise ApiError(400, f"not a commit sha: {ref!r}")
        try:
            full = git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        except RuntimeError:
            raise ApiError(404, f"no such commit: {ref}")
        return commit_detail(full)

    def post_ask(self) -> dict:
        body = self.read_body()
        question = (body.get("question") or "").strip()
        if not question:
            raise ApiError(400, "question is empty")
        detail = self.get_generation(body.get("sha") or "HEAD")
        history = body.get("history") or []
        if not isinstance(history, list):
            raise ApiError(400, "history must be a list")
        return ask_claude(question, detail, history[-20:])

    def post_run(self) -> dict:
        body = self.read_body()
        steps = body.get("steps", 1)
        if not isinstance(steps, int) or not 1 <= steps <= 50:
            raise ApiError(400, "steps must be an integer between 1 and 50")
        if not kernel_run.start(steps):
            raise ApiError(409, "a kernel run is already in progress")
        return {"started": True, "steps": steps}

    def post_restore(self) -> dict:
        body = self.read_body()
        sha = body.get("sha") or ""
        if not SHA_RE.match(sha):
            raise ApiError(400, f"not a commit sha: {sha!r}")
        git("checkout", sha, "--", ORGANISM_PATH)
        with _cache_lock:
            _detail_cache.clear()
        return {"restored": sha, "worktree_dirty": worktree_dirty()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7777)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    key = "set" if os.environ.get("ANTHROPIC_API_KEY") else "NOT set (chat disabled)"
    print(f"ouroboros viewer on http://{args.host}:{args.port}")
    print(f"repo: {ROOT}")
    print(f"ANTHROPIC_API_KEY: {key}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
