"""Tests: Ollama client error mapping (no network; mocked urlopen)."""

import io
import json
import socket
import threading
import unittest
import urllib.error
from unittest import mock

from trace_backend.config import TraceConfig
from trace_backend.ollama_client import (
    OllamaAuthError,
    OllamaCancelledError,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
)


def _cfg():
    return TraceConfig(ollama_host="http://127.0.0.1:11434",
                       ollama_model="gemma4:31b-cloud")


class FakeResp:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, *a):
        return self._buf.read(*a) if a else self._buf.read()

    def readline(self):
        return self._buf.readline()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestOllamaClient(unittest.TestCase):
    def test_connection_refused_gives_actionable_error(self):
        client = OllamaClient(_cfg())
        with mock.patch("urllib.request.urlopen",
                         side_effect=ConnectionRefusedError("refused")):
            with self.assertRaises(OllamaConnectionError) as cm:
                client.check_connection()
            self.assertIn("ollama serve", cm.exception.hint)
            self.assertIn("gemma4:31b-cloud", str(cm.exception))

    def test_model_not_found_hint(self):
        client = OllamaClient(_cfg())
        err = urllib.error.HTTPError("http://x/api/chat", 404, "Not Found", {},
                                     io.BytesIO(b'{"error": "model gemma4:31b-cloud not found"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(OllamaModelNotFoundError) as cm:
                client.generate("hi")
            self.assertIn("ollama pull", cm.exception.hint)

    def test_auth_error(self):
        client = OllamaClient(_cfg())
        err = urllib.error.HTTPError("http://x/api/tags", 401, "Unauthorized", {},
                                     io.BytesIO(b"unauthorized"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(OllamaAuthError):
                client.check_connection()

    def test_timeout_error(self):
        client = OllamaClient(_cfg())
        with mock.patch("urllib.request.urlopen",
                         side_effect=socket.timeout("timed out")):
            with self.assertRaises((OllamaTimeoutError, OllamaConnectionError)):
                client.generate("hi", timeout=0.01)

    def test_malformed_json_raises(self):
        client = OllamaClient(_cfg())
        with mock.patch("urllib.request.urlopen",
                         return_value=FakeResp(b"this is not json")):
            with self.assertRaises(OllamaError):
                client.generate("hi")

    def test_nonstream_chat_parses_content(self):
        client = OllamaClient(_cfg())
        payload = json.dumps({"message": {"content": "hello"}}).encode()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(payload)):
            self.assertEqual(client.generate("hi"), "hello")

    def test_streaming_concatenates_and_supports_cancel(self):
        client = OllamaClient(_cfg())
        ndjson = (b'{"message": {"content": "hel"}, "done": false}\n'
                  b'{"message": {"content": "lo"}, "done": true}\n')
        seen: list[str] = []
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(ndjson)):
            out = client.generate("hi", stream=True, on_token=seen.append)
        self.assertEqual(out, "hello")
        self.assertEqual(seen, ["hel", "lo"])

    def test_streaming_cancel_raises(self):
        client = OllamaClient(_cfg())
        ndjson = b'{"message": {"content": "x"}, "done": false}\n' * 50
        cancel = threading.Event()
        cancel.set()
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(ndjson)):
            with self.assertRaises(OllamaCancelledError):
                client.generate("hi", stream=True, cancel_event=cancel)

    def test_stream_disconnect_is_an_ollama_error(self):
        """A socket reset mid-stream (after connect) must surface as a
        mapped OllamaError, not a raw ConnectionResetError — callers turn the
        mapped error into an honest low-confidence report."""
        client = OllamaClient(_cfg())

        class DroppingResp(FakeResp):
            def readline(self):
                raise ConnectionResetError("reset by peer")

        with mock.patch("urllib.request.urlopen", return_value=DroppingResp(b"")):
            with self.assertRaises(OllamaConnectionError):
                client.generate("hi", stream=True)

    def test_nonstream_read_disconnect_is_an_ollama_error(self):
        client = OllamaClient(_cfg())

        class DroppingResp(FakeResp):
            def read(self, *a):
                raise ConnectionResetError("reset by peer")

        with mock.patch("urllib.request.urlopen",
                        return_value=DroppingResp(b'{"message": {"content": ""}}')):
            with self.assertRaises(OllamaConnectionError):
                client.generate("hi")

    def test_stream_incomplete_read_is_an_ollama_error(self):
        """http.client.IncompleteRead (chunked response dropped) is not an
        OSError subclass — it must still map, not escape."""
        import http.client as _hc
        client = OllamaClient(_cfg())

        class DroppingResp(FakeResp):
            def readline(self):
                raise _hc.IncompleteRead(b"partial")

        with mock.patch("urllib.request.urlopen", return_value=DroppingResp(b"")):
            with self.assertRaises(OllamaError):
                client.generate("hi", stream=True)


if __name__ == "__main__":
    unittest.main()
