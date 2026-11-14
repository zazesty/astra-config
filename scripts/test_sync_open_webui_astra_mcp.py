"""Synthetic Open WebUI rotation tests; never reads live env, DB or Docker."""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location(
    "webui_sync", Path(__file__).with_name("sync-open-webui-astra-mcp.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class WebUISyncTests(unittest.TestCase):
    def test_mount_reader_handles_quoted_first_mount_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.env"
            path.write_text('OTHER=untouched\nMCP_PATH="/mcp/synthetic-new/v2,/mcp/synthetic-old/v1"\n')
            self.assertEqual(sync.read_mount(path), "/mcp/synthetic-new/v2")
            for bad in ('MCP_PATH=https://example.invalid/secret\n', 'MCP_PATH=//evil/path\n',
                        'MCP_PATH=/mcp/one?unexpected=1\n', 'MCP_PATH=/one\nMCP_PATH=/two\n'):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    sync.read_mount(path)

    def test_host_passes_mount_only_on_stdin_and_redacts_outputs(self):
        mount = "/mcp/synthetic-secret/v2"
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1] == "inspect":
                return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
            self.assertNotIn(mount, " ".join(argv))
            self.assertEqual(json.loads(kwargs["input"]), {"mount": mount})
            return SimpleNamespace(returncode=0, stdout='{"status":"updated"}', stderr="")

        out = io.StringIO()
        with patch.object(sync, "read_mount", return_value=mount), patch.object(sync.subprocess, "run", side_effect=run), contextlib.redirect_stdout(out):
            self.assertEqual(sync.main(), 0)
        self.assertEqual(len(calls), 2)
        self.assertNotIn(mount, out.getvalue())
        self.assertIn("updated", out.getvalue())

    def test_absent_container_skips_without_reading_secret_file(self):
        with patch.object(sync.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout="", stderr="Error: No such object: open-webui")), \
             patch.object(sync, "read_mount") as reader, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(), 0)
        reader.assert_not_called()

    def test_container_errors_cannot_escape_into_logs(self):
        responses = [SimpleNamespace(returncode=0, stdout="true", stderr=""),
                     SimpleNamespace(returncode=1, stdout="synthetic-secret-url", stderr="synthetic-secret-token")]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sync.subprocess, "run", side_effect=responses), \
             patch.object(sync, "read_mount", return_value="/mcp/synthetic-new"), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(sync.main(), 1)
        self.assertNotIn("synthetic-secret", stdout.getvalue() + stderr.getvalue())

    def inner(self, original, *, verify_mismatch=False):
        state = copy.deepcopy(original)
        requests = []
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = ("synthetic-admin", 42)
        encode = MagicMock(return_value="synthetic-jwt")

        def api(request, **kwargs):
            nonlocal state
            requests.append(request)
            self.assertEqual(request.full_url, "http://127.0.0.1:8080/api/v1/configs/tool_servers")
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-jwt")
            if request.get_method() == "POST":
                state = json.loads(request.data)
                return io.BytesIO(b'{"ok":true}')
            data = copy.deepcopy(state)
            if verify_mismatch and len(requests) > 1:
                data["TOOL_SERVER_CONNECTIONS"][0]["url"] = "https://example.invalid/mcp/wrong"
            return io.BytesIO(json.dumps(data).encode())

        stdout, stderr = io.StringIO(), io.StringIO()
        result = 0
        with patch.dict("os.environ", {"WEBUI_SECRET_KEY": "synthetic-signing-key"}), \
             patch.dict("sys.modules", {"jwt": SimpleNamespace(encode=encode)}), \
             patch("sqlite3.connect", return_value=db) as connect, \
             patch("urllib.request.urlopen", side_effect=api), \
             patch("sys.stdin", io.StringIO('{"mount":"/mcp/synthetic-new/v2"}')), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(sync.CONTAINER_SCRIPT, {"__name__": "synthetic_container"})
            except SystemExit as exc:
                result = exc.code
        connect.assert_called_once_with("file:/app/backend/data/webui.db?mode=ro", uri=True)
        return result, state, requests, encode, stdout.getvalue() + stderr.getvalue()

    def test_inner_updates_only_astra_path_and_verifies_with_short_lived_jwt(self):
        original = {"TOOL_SERVER_CONNECTIONS": [
            {"url": "https://synthetic.invalid:9443/mcp/old/v1?keep=1#fragment",
             "info": {"id": "astra", "name": "Astra", "description": "keep"},
             "type": "mcp", "auth_type": "bearer", "key": "synthetic-connection-key",
             "config": {"enable": True}},
            {"url": "https://other.invalid/untouched", "info": {"id": "other"}}], "extra": "preserve"}
        code, state, requests, encode, output = self.inner(original)
        self.assertEqual(code, 0)
        expected = copy.deepcopy(original)
        expected["TOOL_SERVER_CONNECTIONS"][0]["url"] = "https://synthetic.invalid:9443/mcp/synthetic-new/v2?keep=1#fragment"
        self.assertEqual(state, expected)
        self.assertEqual([request.get_method() for request in requests], ["GET", "POST", "GET"])
        claims, secret = encode.call_args.args
        self.assertEqual(claims["id"], "synthetic-admin")
        self.assertEqual(claims["session_stamp"], 42)
        self.assertEqual(claims["exp"] - claims["iat"], 300)
        self.assertEqual(encode.call_args.kwargs, {"algorithm": "HS256"})
        self.assertEqual(secret, "synthetic-signing-key")
        self.assertNotIn("synthetic-", output)

    def test_missing_or_ambiguous_connection_refuses_without_post(self):
        for connections in [[], [{"info": {"id": "astra"}}, {"info": {"id": "astra"}}]]:
            with self.subTest(connections=connections):
                code, _, requests, _, output = self.inner({"TOOL_SERVER_CONNECTIONS": connections})
                self.assertEqual(code, 1)
                self.assertEqual([request.get_method() for request in requests], ["GET"])
                self.assertIn("sync failed", output)

    def test_readback_mismatch_fails(self):
        original = {"TOOL_SERVER_CONNECTIONS": [
            {"url": "https://synthetic.invalid/mcp/old", "info": {"id": "astra"}}]}
        code, _, _, _, output = self.inner(original, verify_mismatch=True)
        self.assertEqual(code, 1)
        self.assertNotIn("synthetic-", output)


if __name__ == "__main__":
    unittest.main()
