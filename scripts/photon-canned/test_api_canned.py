"""Native API contract tests: canned shortcuts never construct a model turn."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


class NativeCannedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        test_home = os.environ.get("HERMES_HOME", "")
        if not test_home or not Path(test_home).name.startswith("canned-api-isolated-"):
            raise RuntimeError("Run these integration tests with a temporary HERMES_HOME")
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter, _api_request_profile
        from hermes_state import SessionDB
        import api_canned

        self.temp = tempfile.TemporaryDirectory(prefix="canned-api-test-")
        self.db = SessionDB(Path(self.temp.name) / "state.db")
        self.db.create_session("test-session", "api_server")
        self.db.append_messages_batch("test-session", [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
        ])
        self.adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
            "key": "synthetic-test-api-key", "model_name": "synthetic-model"}))
        self.adapter._session_db = self.db
        self.adapter._draining_response = lambda: None
        self.adapter._run_agent = AsyncMock(side_effect=AssertionError("Canned route reached model"))
        self.allow_passthrough = False
        self.passthrough_bodies = []

        @web.middleware
        async def scope(request, handler):
            token = _api_request_profile.set(request.match_info.get("profile"))
            try:
                return await handler(request)
            finally:
                _api_request_profile.reset(token)

        async def normal(request):
            if not self.allow_passthrough:
                raise AssertionError("Canned message fell through to normal/model handler")
            self.passthrough_bodies.append(await request.json())
            return web.json_response({"normal": True})

        app = web.Application(middlewares=[scope])
        api_canned.wire_api_canned(app, self.adapter)
        for prefix in ("", "/p/{profile}"):
            for suffix in ("", "/stream"):
                app.router.add_post(prefix + "/api/sessions/{session_id}/chat" + suffix, normal)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.action_patch = patch("api_canned.handle", side_effect=lambda intent, **kwargs: f"Synthetic {intent.kind}")
        self.action = self.action_patch.start()

    async def asyncTearDown(self):
        self.action_patch.stop()
        await self.client.close()
        self.db.close()
        self.temp.cleanup()

    async def post(self, body, suffix="", prefix="", *, auth=True, session="test-session"):
        headers = {"Authorization": "Bearer synthetic-test-api-key"} if auth else {}
        return await self.client.post(prefix + f"/api/sessions/{session}/chat" + suffix,
                                      json=body, headers=headers)

    def history(self):
        return [{"role": row["role"], "content": row["content"]}
                for row in self.db.get_messages("test-session")]

    async def test_all_shortcuts_json_preserve_history_and_never_call_model(self):
        examples = [("budget status", "budget"), ("buy list", "buy_list"),
                    ("todo list", "todo_list"), ("todo add Synthetic task", "todo_add")]
        for message, kind in examples:
            with self.subTest(kind=kind):
                response = await self.post({"message": message})
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["X-Hermes-Canned"], kind)
                data = await response.json()
                self.assertEqual(data["object"], "hermes.session.chat.completion")
                self.assertEqual(data["status"], "completed")
                self.assertEqual(data["message"], {"role": "assistant", "content": f"Synthetic {kind}"})
                self.assertEqual(data["runtime"], {"canned": True, "shortcut": kind, "model": None})
                self.assertTrue(all(value == 0 for value in data["usage"].values()))
        self.assertEqual(self.action.call_count, 4)
        self.assertTrue(all(call.kwargs == {"author": "Zavdi via Conduit"} for call in self.action.call_args_list))
        self.adapter._run_agent.assert_not_called()
        history = self.history()
        self.assertEqual(history[:2], [{"role": "user", "content": "Earlier question"},
                                       {"role": "assistant", "content": "Earlier answer"}])
        self.assertEqual(len(history), 10)
        self.assertEqual(history[-2:], [{"role": "user", "content": examples[-1][0]},
                                        {"role": "assistant", "content": "Synthetic todo_add"}])

    async def test_native_stream_events_reconcile_final_and_persist_transcript(self):
        response = await self.post({"input": "budget status"}, "/stream", "/p/default")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-Hermes-Canned"], "budget")
        self.assertEqual(response.headers["Content-Type"], "text/event-stream")
        frames = []
        for raw in (await response.text()).strip().split("\n\n"):
            name = next(line[7:] for line in raw.splitlines() if line.startswith("event: "))
            data = json.loads(next(line[6:] for line in raw.splitlines() if line.startswith("data: ")))
            frames.append((name, data))
        self.assertEqual([name for name, _ in frames], ["run.started", "message.started", "assistant.delta",
                                                      "assistant.completed", "run.completed", "done"])
        self.assertEqual([data["seq"] for _, data in frames], list(range(1, 7)))
        self.assertEqual(len({data["run_id"] for _, data in frames}), 1)
        self.assertTrue(all(data["session_id"] == "test-session" and isinstance(data["ts"], float)
                            for _, data in frames))
        self.assertEqual(frames[2][1]["delta"], "Synthetic budget")
        self.assertEqual(frames[3][1]["content"], "Synthetic budget")
        for _, payload in frames[3:5]:
            self.assertIs(payload["completed"], True)
            self.assertIs(payload["partial"], False)
            self.assertIs(payload["interrupted"], False)
        self.assertEqual(frames[4][1]["messages"], [{"role": "assistant", "content": "Synthetic budget"}])
        self.assertEqual(frames[4][1]["usage"]["api_calls"], 0)
        self.assertEqual(self.history()[-2:], [{"role": "user", "content": "budget status"},
                                             {"role": "assistant", "content": "Synthetic budget"}])

    async def test_authentication_and_configured_key_required_before_action(self):
        for configured, authorized in [(True, False), (False, True)]:
            with self.subTest(configured=configured, authorized=authorized):
                self.adapter._api_key = "synthetic-test-api-key" if configured else ""
                response = await self.post({"message": "budget status"}, auth=authorized)
                self.assertEqual(response.status, 401)
        self.action.assert_not_called()
        self.assertEqual(len(self.history()), 2)

    async def test_ordinary_multimodal_and_secondary_requests_pass_through_unchanged(self):
        self.allow_passthrough = True
        examples = [({"message": "Explain photosynthesis"}, ""),
                    ({"message": [{"type": "text", "text": "budget status"},
                                  {"type": "image_url", "image_url": {"url": "https://example.invalid/image"}}]}, ""),
                    ({"message": "budget status", "attachments": ["synthetic-file"]}, ""),
                    ({"message": "budget status"}, "/p/secondary")]
        for body, prefix in examples:
            response = await self.post(body, prefix=prefix)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"normal": True})
            self.assertEqual(self.passthrough_bodies[-1], body)
        self.action.assert_not_called()
        self.assertEqual(len(self.history()), 2)

    async def test_busy_session_refuses_before_action(self):
        self.assertTrue(self.db.try_acquire_session_turn_lease("test-session", "synthetic-busy-holder"))
        try:
            response = await self.post({"message": "todo add Synthetic task"})
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["error"]["code"], "session_busy")
            self.action.assert_not_called()
        finally:
            self.db.release_session_turn_lease("test-session", "synthetic-busy-holder")

    async def test_invalid_session_and_header_use_native_validation(self):
        response = await self.post({"message": "budget status"}, session="missing-session")
        self.assertEqual(response.status, 404)
        self.action.assert_not_called()
        response = await self.client.post("/api/sessions/test-session/chat", json={"message": "budget status"},
                                          headers={"Authorization": "Bearer synthetic-test-api-key",
                                                   "X-Hermes-Session-Key": "x" * 300})
        self.assertEqual(response.status, 400)
        self.action.assert_not_called()

    async def test_action_failure_is_not_retried_or_sent_to_model_and_releases_lease(self):
        self.action.side_effect = RuntimeError("Synthetic command failure")
        response = await self.post({"message": "todo add Synthetic task"})
        self.assertEqual(response.status, 500)
        self.assertEqual(self.action.call_count, 1)
        self.assertTrue(self.db.try_acquire_session_turn_lease("test-session", "next-synthetic-holder"))
        self.db.release_session_turn_lease("test-session", "next-synthetic-holder")
        self.assertEqual(len(self.history()), 2)
        self.adapter._run_agent.assert_not_called()


if __name__ == "__main__":
    # This file is run through Hermes' managed interpreter with a temporary
    # HERMES_HOME supplied by the caller; never load a live user's configuration.
    if not Path(os.environ.get("HERMES_HOME", "")).name.startswith("canned-api-isolated-"):
        raise SystemExit("Run with an isolated temporary HERMES_HOME")
    unittest.main()
