#!/usr/bin/env python3

from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import model_broker


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
            "openai", "gpt-5.6", "openai-secret", 12000, 37, opener
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
                {"content": [{"type": "text", "text": "successor"}]}
            )

        backend = model_broker.ModelBackend(
            "anthropic", "claude-opus-5", "anthropic-secret", 12000, 40, opener
        )
        self.assertEqual(backend.generate("prompt", 8000), "successor")

        request = captured["request"]
        body = json.loads(request.data)
        self.assertEqual(
            request.full_url, "https://api.anthropic.com/v1/messages"
        )
        self.assertEqual(request.get_header("X-api-key"), "anthropic-secret")
        self.assertEqual(body["model"], "claude-opus-5")
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(
            body["messages"], [{"role": "user", "content": "prompt"}]
        )


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def generate(self, prompt: str, requested_tokens: int) -> str:
        self.calls.append((prompt, requested_tokens))
        return "next organism"


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


if __name__ == "__main__":
    unittest.main()
