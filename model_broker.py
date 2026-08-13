#!/usr/bin/env python3
"""Kernel-side model service for an ouroboros generation.

The broker is trusted instrument code. It owns the selected provider credential
and exposes one provider-independent operation over a private Unix socket:

    POST /generate
    X-Ouroboros-Max-Output-Tokens: N
    <raw UTF-8 prompt body>

The successful response body is raw UTF-8 model output. Provider names, model
names, credentials, and provider response schemas never cross into the sandbox.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socketserver
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable


class BrokerError(RuntimeError):
    """A provider request failed or returned no usable text."""


def anthropic_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    chunks = [
        block.get("text")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "".join(chunks) if chunks else None


def openai_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "output_text"
                and isinstance(block.get("text"), str)
            ):
                chunks.append(block["text"])
    return "".join(chunks) if chunks else None


class ModelBackend:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        max_output_tokens: int,
        request_timeout: float,
        opener: Callable[..., object] = urllib.request.urlopen,
    ) -> None:
        if provider not in {"anthropic", "openai"}:
            raise ValueError("unsupported model provider")
        if not model or not api_key:
            raise ValueError("model and selected API key are required")
        if max_output_tokens <= 0 or request_timeout <= 0:
            raise ValueError("broker limits must be positive")
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.request_timeout = request_timeout
        self.opener = opener

    def generate(self, prompt: str, requested_tokens: int) -> str:
        tokens = min(requested_tokens, self.max_output_tokens)
        if tokens <= 0:
            raise BrokerError("requested token count must be positive")

        if self.provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            body = {
                "model": self.model,
                "max_tokens": tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            extract = anthropic_text
        else:
            url = "https://api.openai.com/v1/responses"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.model,
                "max_output_tokens": tokens,
                "input": prompt,
            }
            extract = openai_text

        request = urllib.request.Request(
            url,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.request_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
            raise BrokerError(f"backend request failed: {type(exc).__name__}") from exc

        text = extract(payload)
        if text is None:
            raise BrokerError("backend returned no text")
        return text


class UnixHTTPServer(socketserver.UnixStreamServer):
    allow_reuse_address = True


def make_handler(backend: ModelBackend, max_prompt_bytes: int):
    class ModelRequestHandler(BaseHTTPRequestHandler):
        server_version = "ouroboros-kernel"
        sys_version = ""

        def log_message(self, _format: str, *args: object) -> None:
            return

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._reply(404, b"unknown operation\n")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/generate":
                self._reply(404, b"unknown operation\n")
                return

            try:
                content_length = int(self.headers.get("Content-Length", ""))
                requested_tokens = int(
                    self.headers.get("X-Ouroboros-Max-Output-Tokens", "")
                )
            except ValueError:
                self._reply(400, b"invalid request\n")
                return
            if (
                content_length < 0
                or content_length > max_prompt_bytes
                or requested_tokens <= 0
            ):
                self._reply(400, b"invalid request\n")
                return

            raw_prompt = self.rfile.read(content_length)
            try:
                prompt = raw_prompt.decode("utf-8")
            except UnicodeDecodeError:
                self._reply(400, b"invalid request\n")
                return

            try:
                text = backend.generate(prompt, requested_tokens)
            except BrokerError as exc:
                print(f"model broker: {exc}", file=sys.stderr, flush=True)
                self._reply(502, b"model unavailable\n")
                return
            self._reply(200, text.encode("utf-8"))

    return ModelRequestHandler


def read_config() -> tuple[str, str, str, int, float, int]:
    names = (
        "provider",
        "model",
        "API key",
        "maximum output tokens",
        "request timeout",
        "maximum prompt bytes",
    )
    values: list[str] = []
    for name in names:
        value = sys.stdin.readline()
        if value == "":
            raise ValueError(f"missing {name} in broker configuration")
        values.append(value.rstrip("\n"))
    provider, model, api_key, max_tokens, timeout, max_prompt = values
    return provider, model, api_key, int(max_tokens), float(timeout), int(max_prompt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()

    try:
        provider, model, api_key, max_tokens, timeout, max_prompt = read_config()
        if max_prompt <= 0:
            raise ValueError("maximum prompt bytes must be positive")
        backend = ModelBackend(provider, model, api_key, max_tokens, timeout)
    except ValueError as exc:
        print(f"model broker: invalid configuration: {exc}", file=sys.stderr)
        return 2

    socket_path = Path(args.socket)
    if socket_path.exists() or socket_path.is_socket():
        socket_path.unlink()
    server = UnixHTTPServer(str(socket_path), make_handler(backend, max_prompt))
    os.chmod(socket_path, 0o600)

    def stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
