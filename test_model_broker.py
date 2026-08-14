#!/usr/bin/env python3

from __future__ import annotations

import http.client
import json
import socket
import stat
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

import model_broker


def spending_ledger(budget: str = "10.00") -> model_broker.SpendingLedger:
    return model_broker.SpendingLedger(
        Decimal(budget), Decimal("5.00"), Decimal("30.00")
    )


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class BackendTests(unittest.TestCase):
    def test_openai_request_is_capped_and_extracts_all_output_text(self) -> None:
        captured: dict[str, object] = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "output": [
                        {"type": "reasoning", "summary": []},
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "first"},
                                {"type": "output_text", "text": " second"},
                            ],
                        },
                    ]
                }
            )

        backend = model_broker.ModelBackend(
            "openai",
            "gpt-5.6",
            "openai-secret",
            12000,
            37,
            spending_ledger(),
            opener,
        )
        self.assertEqual(backend.generate("prompt", 99999), "first second")

        request = captured["request"]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer openai-secret")
        self.assertEqual(
            body,
            {"model": "gpt-5.6", "max_output_tokens": 12000, "input": "prompt"},
        )
        self.assertEqual(captured["timeout"], 37)

    def test_anthropic_request_uses_messages_api(self) -> None:
        captured: dict[str, object] = {}

        def opener(request, timeout):
            captured["request"] = request
            return FakeResponse(
                {
                    "usage": {"input_tokens": 9, "output_tokens": 4},
                    "content": [{"type": "text", "text": "successor"}],
                }
            )

        backend = model_broker.ModelBackend(
            "anthropic",
            "claude-opus-4-8",
            "anthropic-secret",
            12000,
            40,
            spending_ledger(),
            opener,
        )
        self.assertEqual(backend.generate("prompt", 8000), "successor")

        request = captured["request"]
        body = json.loads(request.data)
        self.assertEqual(
            request.full_url, "https://api.anthropic.com/v1/messages"
        )
        self.assertEqual(request.get_header("X-api-key"), "anthropic-secret")
        self.assertEqual(body["model"], "claude-opus-4-8")
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(
            body["messages"], [{"role": "user", "content": "prompt"}]
        )

    def test_dollar_budget_is_reserved_before_provider_call(self) -> None:
        called = False

        def opener(_request, timeout):
            del timeout
            nonlocal called
            called = True
            raise AssertionError("provider must not be called")

        backend = model_broker.ModelBackend(
            "openai",
            "gpt-5.6",
            "openai-secret",
            12000,
            37,
            spending_ledger("0.01"),
            opener,
        )
        with self.assertRaises(model_broker.BudgetExceeded):
            backend.generate("prompt", 12000)
        self.assertFalse(called)

    def test_provider_usage_refunds_unused_reservation(self) -> None:
        def opener(_request, timeout):
            del timeout
            return FakeResponse(
                {
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                }
            )

        ledger = spending_ledger("0.40")
        backend = model_broker.ModelBackend(
            "openai", "gpt-5.6", "secret", 12000, 37, ledger, opener
        )
        self.assertEqual(backend.generate("prompt", 12000), "ok")
        self.assertGreater(ledger.remaining_usd, Decimal("0.39"))
        self.assertEqual(backend.generate("prompt", 12000), "ok")


class CapabilityManifestTests(unittest.TestCase):
    def test_manifest_describes_only_the_provider_neutral_abi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = model_broker.write_capability_manifest(Path(directory))
            manifest = json.loads(path.read_text(encoding="utf-8"))
            serialized = json.dumps(manifest).lower()

            self.assertEqual(path.name, "capabilities.json")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
            self.assertEqual(manifest["capabilities"][0]["name"], "generate")
            self.assertEqual(
                manifest["capabilities"][0]["socket"], "/kernel/model.sock"
            )
            self.assertEqual(manifest["capabilities"][1]["name"], "journal")
            self.assertEqual(
                manifest["capabilities"][1]["path"], "/kernel/journal.md"
            )
            self.assertNotIn("anthropic", serialized)
            self.assertNotIn("openai", serialized)
            self.assertNotIn("secret", serialized)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def generate(self, prompt: str, requested_tokens: int) -> str:
        self.calls.append((prompt, requested_tokens))
        return "next organism"


class ExhaustedBackend:
    def generate(self, _prompt: str, _requested_tokens: int) -> str:
        raise model_broker.BudgetExceeded("generation spending budget exhausted")


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str) -> None:
        super().__init__("kernel")
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.socket_path = str(Path(self.tempdir.name) / "model.sock")
        self.backend = FakeBackend()
        try:
            self.server = model_broker.UnixHTTPServer(
                self.socket_path, model_broker.make_handler(self.backend, 1024)
            )
        except PermissionError as exc:
            self.tempdir.cleanup()
            self.skipTest(f"host forbids Unix sockets: {exc}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        Path(self.socket_path).unlink(missing_ok=True)
        self.tempdir.cleanup()

    def request(self, method: str, path: str, body: bytes = b"", **headers: str):
        connection = UnixHTTPConnection(self.socket_path)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_generate_contract_contains_only_prompt_tokens_and_text(self) -> None:
        status, body = self.request(
            "POST",
            "/generate",
            b"my prompt",
            **{"X-Ouroboros-Max-Output-Tokens": "321"},
        )
        self.assertEqual((status, body), (200, b"next organism"))
        self.assertEqual(self.backend.calls, [("my prompt", 321)])

    def test_rejects_unknown_operations_and_invalid_token_requests(self) -> None:
        self.assertEqual(self.request("POST", "/provider")[0], 404)
        self.assertEqual(
            self.request(
                "POST",
                "/generate",
                b"prompt",
                **{"X-Ouroboros-Max-Output-Tokens": "not-a-number"},
            )[0],
            400,
        )

    def test_reports_generation_budget_exhaustion_without_provider_details(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        Path(self.socket_path).unlink(missing_ok=True)
        self.server = model_broker.UnixHTTPServer(
            self.socket_path,
            model_broker.make_handler(ExhaustedBackend(), 1024),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, body = self.request(
            "POST",
            "/generate",
            b"prompt",
            **{"X-Ouroboros-Max-Output-Tokens": "100"},
        )
        self.assertEqual(status, 402)
        self.assertEqual(body, b"generation budget exhausted\n")


if __name__ == "__main__":
    unittest.main()
