#!/usr/bin/env python3
"""Kernel-side model service for an ouroboros generation.

The broker is trusted instrument code. It owns the selected provider credential
and exposes one provider-independent operation over a private Unix socket:

    POST /generate
    X-Ouroboros-Max-Output-Tokens: N
    <raw UTF-8 prompt body>

The successful response body is raw UTF-8 model output. A provider-neutral
description of this ABI is published beside the socket as capabilities.json.
Provider names, model names, credentials, and provider response schemas never
cross into the sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socketserver
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable


CAPABILITY_MANIFEST_NAME = "capabilities.json"
CAPABILITY_MANIFEST = {
    "schema": "ouroboros-kernel-capabilities/v1",
    "capabilities": [
        {
            "name": "generate",
            "transport": "http-over-unix",
            "socket": "/kernel/model.sock",
            "method": "POST",
            "path": "/generate",
            "request": {
                "body": "raw UTF-8 prompt",
                "headers": {
                    "Content-Type": "text/plain; charset=utf-8",
                    "X-Ouroboros-Max-Output-Tokens": "positive integer",
                },
            },
            "success": {"status": 200, "body": "raw UTF-8 generated text"},
            "errors": {
                "400": "invalid request",
                "402": "generation spending budget exhausted",
                "502": "model unavailable",
            },
        },
        {
            "name": "journal",
            "kind": "read-only-file",
            "path": "/kernel/journal.md",
        }
    ],
}


class BrokerError(RuntimeError):
    """A provider request failed or returned no usable text."""


class BudgetExceeded(BrokerError):
    """The next provider request cannot fit in this generation's dollar budget."""


@dataclass(frozen=True)
class Reservation:
    maximum_cost: Decimal


class SpendingLedger:
    """Conservative generation-local accounting at configured USD token prices."""

    TOKEN_DENOMINATOR = Decimal(1_000_000)
    INPUT_OVERHEAD_TOKENS = 4096

    def __init__(
        self,
        budget_usd: Decimal,
        input_usd_per_mtok: Decimal,
        output_usd_per_mtok: Decimal,
    ) -> None:
        if min(budget_usd, input_usd_per_mtok, output_usd_per_mtok) <= 0:
            raise ValueError("spending budget and token prices must be positive")
        self.budget_usd = budget_usd
        self.remaining_usd = budget_usd
        self.input_usd_per_mtok = input_usd_per_mtok
        self.output_usd_per_mtok = output_usd_per_mtok

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.input_usd_per_mtok
            + Decimal(output_tokens) * self.output_usd_per_mtok
        ) / self.TOKEN_DENOMINATOR

    def reserve(self, prompt_bytes: int, output_tokens: int) -> Reservation:
        # For the supported text APIs, a prompt cannot tokenize to more tokens
        # than its UTF-8 byte length. The fixed allowance covers request framing.
        maximum = self._cost(
            prompt_bytes + self.INPUT_OVERHEAD_TOKENS, output_tokens
        )
        if maximum > self.remaining_usd:
            raise BudgetExceeded("generation spending budget exhausted")
        self.remaining_usd -= maximum
        return Reservation(maximum)

    def reconcile(
        self,
        reservation: Reservation,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> Decimal | None:
        if input_tokens is None or output_tokens is None:
            return None
        if input_tokens < 0 or output_tokens < 0:
            return None
        actual = self._cost(input_tokens, output_tokens)
        # Never refund more than was reserved. If a provider reports surprising
        # usage, the conservative reservation remains fully consumed.
        refund = max(Decimal(0), reservation.maximum_cost - actual)
        self.remaining_usd += refund
        return actual


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
        spending_ledger: SpendingLedger,
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
        self.spending_ledger = spending_ledger
        self.opener = opener
        self.last_observation: dict[str, object] = {}

    def generate(self, prompt: str, requested_tokens: int) -> str:
        self.last_observation = {}
        tokens = min(requested_tokens, self.max_output_tokens)
        if tokens <= 0:
            raise BrokerError("requested token count must be positive")
        reservation = self.spending_ledger.reserve(
            len(prompt.encode("utf-8")), tokens
        )

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
        usage = payload.get("usage") if isinstance(payload, dict) else None
        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        actual_cost = self.spending_ledger.reconcile(
            reservation,
            input_tokens if isinstance(input_tokens, int) else None,
            output_tokens if isinstance(output_tokens, int) else None,
        )
        status = payload.get("status") if isinstance(payload, dict) else None
        incomplete = payload.get("incomplete_details") if isinstance(payload, dict) else None
        if isinstance(incomplete, dict):
            stop_reason = incomplete.get("reason")
        else:
            stop_reason = payload.get("stop_reason") if isinstance(payload, dict) else None
        self.last_observation = {
            "provider_response_status": status if isinstance(status, str) else "completed",
            "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
            "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
            "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
            "reserved_usd": str(reservation.maximum_cost),
            "accounted_usd": str(actual_cost) if actual_cost is not None else None,
            "remaining_generation_budget_usd": str(
                self.spending_ledger.remaining_usd
            ),
            "effective_output_tokens": tokens,
        }
        return text


class UnixHTTPServer(socketserver.UnixStreamServer):
    allow_reuse_address = True


class AuditRecorder:
    """Append trusted, generation-local model-call observations as JSON Lines."""

    def __init__(self, path: Path, generation: int) -> None:
        self.path = path
        self.generation = generation
        self.request_index = 0
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)

    def record(
        self,
        *,
        started_at: str,
        duration_ms: int,
        prompt: str,
        requested_tokens: int,
        response: str | None,
        http_status: int,
        error: str | None,
        backend: object,
    ) -> None:
        self.request_index += 1
        prompt_bytes = prompt.encode("utf-8")
        response_bytes = response.encode("utf-8") if response is not None else None
        backend_fields = getattr(backend, "last_observation", {})
        if not isinstance(backend_fields, dict):
            backend_fields = {}
        payload = {
            "schema": "ouroboros-model-call/v1",
            "generation": self.generation,
            "request_index": self.request_index,
            "operation": "generate",
            "started_at": started_at,
            "duration_ms": duration_ms,
            "requested_output_tokens": requested_tokens,
            "prompt": prompt,
            "prompt_bytes": len(prompt_bytes),
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "response": response,
            "response_bytes": len(response_bytes) if response_bytes is not None else None,
            "response_sha256": (
                hashlib.sha256(response_bytes).hexdigest()
                if response_bytes is not None
                else None
            ),
            "http_status": http_status,
            "error": error,
            **backend_fields,
        }
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")


def write_capability_manifest(
    directory: Path, constraints: dict[str, object] | None = None
) -> Path:
    """Publish the provider-neutral ABI beside the socket for sandbox discovery."""
    path = directory / CAPABILITY_MANIFEST_NAME
    temporary = directory / f".{CAPABILITY_MANIFEST_NAME}.tmp"
    temporary.write_text(
        json.dumps(
            {**CAPABILITY_MANIFEST, "constraints": constraints or {}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    path.chmod(0o444)
    return path


def make_handler(
    backend: ModelBackend,
    max_prompt_bytes: int,
    audit_recorder: AuditRecorder | None = None,
):
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
                started_at = datetime.now(timezone.utc).isoformat()
                started_monotonic = time.monotonic_ns()
                text = backend.generate(prompt, requested_tokens)
            except BudgetExceeded as exc:
                if audit_recorder is not None:
                    audit_recorder.record(
                        started_at=started_at,
                        duration_ms=(time.monotonic_ns() - started_monotonic) // 1_000_000,
                        prompt=prompt,
                        requested_tokens=requested_tokens,
                        response=None,
                        http_status=402,
                        error="budget_exhausted",
                        backend=backend,
                    )
                print(f"model broker: {exc}", file=sys.stderr, flush=True)
                self._reply(402, b"generation budget exhausted\n")
                return
            except BrokerError as exc:
                if audit_recorder is not None:
                    audit_recorder.record(
                        started_at=started_at,
                        duration_ms=(time.monotonic_ns() - started_monotonic) // 1_000_000,
                        prompt=prompt,
                        requested_tokens=requested_tokens,
                        response=None,
                        http_status=502,
                        error="model_unavailable",
                        backend=backend,
                    )
                print(f"model broker: {exc}", file=sys.stderr, flush=True)
                self._reply(502, b"model unavailable\n")
                return
            if audit_recorder is not None:
                # The backend call dominates request time; monotonic timing keeps
                # wall-clock adjustments out of the duration measurement.
                audit_recorder.record(
                    started_at=started_at,
                    duration_ms=(time.monotonic_ns() - started_monotonic) // 1_000_000,
                    prompt=prompt,
                    requested_tokens=requested_tokens,
                    response=text,
                    http_status=200,
                    error=None,
                    backend=backend,
                )
            self._reply(200, text.encode("utf-8"))

    return ModelRequestHandler


def read_config() -> tuple[str, str, str, int, float, int, Decimal, Decimal, Decimal]:
    names = (
        "provider",
        "model",
        "API key",
        "maximum output tokens",
        "request timeout",
        "maximum prompt bytes",
        "generation budget in USD",
        "input USD per million tokens",
        "output USD per million tokens",
    )
    values: list[str] = []
    for name in names:
        value = sys.stdin.readline()
        if value == "":
            raise ValueError(f"missing {name} in broker configuration")
        values.append(value.rstrip("\n"))
    (
        provider,
        model,
        api_key,
        max_tokens,
        timeout,
        max_prompt,
        budget,
        input_price,
        output_price,
    ) = values
    try:
        prices = Decimal(budget), Decimal(input_price), Decimal(output_price)
    except InvalidOperation as exc:
        raise ValueError("invalid spending budget or token price") from exc
    return (
        provider,
        model,
        api_key,
        int(max_tokens),
        float(timeout),
        int(max_prompt),
        *prices,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--constraints-json", required=True)
    parser.add_argument("--audit-log", type=Path, required=True)
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()

    try:
        (
            provider,
            model,
            api_key,
            max_tokens,
            timeout,
            max_prompt,
            budget,
            input_price,
            output_price,
        ) = read_config()
        if max_prompt <= 0:
            raise ValueError("maximum prompt bytes must be positive")
        ledger = SpendingLedger(budget, input_price, output_price)
        backend = ModelBackend(
            provider, model, api_key, max_tokens, timeout, ledger
        )
        constraints = json.loads(args.constraints_json)
        if not isinstance(constraints, dict):
            raise ValueError("constraints must be a JSON object")
        if args.generation <= 0:
            raise ValueError("generation must be positive")
        audit_recorder = AuditRecorder(args.audit_log, args.generation)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"model broker: invalid configuration: {exc}", file=sys.stderr)
        return 2

    socket_path = Path(args.socket)
    manifest_path = socket_path.parent / CAPABILITY_MANIFEST_NAME
    if socket_path.exists() or socket_path.is_socket():
        socket_path.unlink()
    try:
        write_capability_manifest(socket_path.parent, constraints)
    except OSError as exc:
        print(
            f"model broker: could not publish capability manifest: {exc}",
            file=sys.stderr,
        )
        return 2
    try:
        server = UnixHTTPServer(
            str(socket_path),
            make_handler(backend, max_prompt, audit_recorder),
        )
    except OSError as exc:
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass
        print(f"model broker: could not open Unix socket: {exc}", file=sys.stderr)
        return 2
    def stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        os.chmod(socket_path, 0o600)
        server.serve_forever()
    finally:
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
