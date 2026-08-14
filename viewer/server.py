#!/usr/bin/env python3
"""Read-only web viewer for an ouroboros lineage."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from lineage_reader import GitReadError, LineageRepository, resolve_paths


class ViewerHandler(BaseHTTPRequestHandler):
    repository: LineageRepository
    index_html: bytes

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'",
        )

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        request = urlparse(self.path)
        try:
            if request.path == "/":
                self._send_bytes(200, "text/html; charset=utf-8", self.index_html)
                return
            if request.path == "/favicon.ico":
                self._send_bytes(204, "image/x-icon", b"")
                return
            if request.path == "/api/lineage":
                self._send_json(200, self.repository.summary())
                return
            if request.path.startswith("/api/generation/"):
                sha = unquote(request.path.removeprefix("/api/generation/"))
                self._send_json(200, self.repository.generation_details(sha))
                return
            if request.path == "/api/diff":
                query = parse_qs(request.query)
                before = query.get("from", [""])[0]
                after = query.get("to", [""])[0]
                self._send_json(200, self.repository.pairwise_diff(before, after))
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except GitReadError as exc:
            self._send_json(500, {"error": str(exc)})
        except BrokenPipeError:
            pass

    def log_message(self, template: str, *arguments: object) -> None:
        sys.stderr.write(f"[viewer] {self.address_string()} {template % arguments}\n")


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True


def parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worktree", help="path to the lineage worktree")
    parser.add_argument(
        "--git-dir",
        help="path to its git directory (default: <worktree>.git, then <worktree>/.git)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", default=8765, type=int, help="listen port")
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    options = parse_args(arguments)
    try:
        worktree, git_dir = resolve_paths(options.worktree, options.git_dir)
        repository = LineageRepository(worktree, git_dir)
        index_html = (Path(__file__).parent / "static" / "index.html").read_bytes()
    except (OSError, ValueError, GitReadError) as exc:
        print(f"viewer: {exc}", file=sys.stderr)
        return 2

    class BoundHandler(ViewerHandler):
        pass

    BoundHandler.repository = repository
    BoundHandler.index_html = index_html
    server = ViewerServer((options.host, options.port), BoundHandler)
    host, port = server.server_address[:2]
    print(f"lineage: {worktree}")
    print(f"git dir: {git_dir}")
    print(f"viewer:  http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
