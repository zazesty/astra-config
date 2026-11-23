#!/usr/bin/env python3
"""Tests for $0 binary Photon reply classification and pre_gateway_dispatch hook."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import __init__ as plugin
import actions
import classify


def _event(text: str, platform: str = "photon"):
    plat = SimpleNamespace(value=platform)
    src = SimpleNamespace(platform=plat, chat_id="chat-1", user_id="u1")
    return SimpleNamespace(text=text, source=src)


class TestBinaryReply(unittest.TestCase):
    def test_parse_binary_reply_strict(self):
        self.assertEqual(classify.parse_binary_reply("y"), ("yes", None))
        self.assertEqual(classify.parse_binary_reply("Yes!"), ("yes", None))
        self.assertEqual(classify.parse_binary_reply("n"), ("no", None))
        self.assertEqual(classify.parse_binary_reply("no #2"), ("no", "2"))
        self.assertEqual(classify.parse_binary_reply("enroll 3"), ("yes", "3"))
        self.assertEqual(classify.parse_binary_reply("dismiss 1"), ("no", "1"))
        self.assertIsNone(classify.parse_binary_reply("yes please check my calendar"))
        self.assertIsNone(classify.parse_binary_reply("no worries"))

    def test_has_pending_binary_asks_respects_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "photon_asks.json"
            self.assertFalse(actions.has_pending_binary_asks(p))
            now = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)
            p.write_text(
                json.dumps(
                    {
                        "next_seq": 2,
                        "pending": [
                            {
                                "code": "1",
                                "expires_at": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            }
                        ],
                    }
                )
            )
            self.assertFalse(actions.has_pending_binary_asks(p, now=now))
            p.write_text(
                json.dumps(
                    {
                        "next_seq": 2,
                        "pending": [
                            {
                                "code": "1",
                                "expires_at": (now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            }
                        ],
                    }
                )
            )
            self.assertTrue(actions.has_pending_binary_asks(p, now=now))

    def test_hook_intercepts_only_when_pending_asks_exist(self):
        gw = SimpleNamespace(adapters={})
        with patch.object(plugin, "has_pending_binary_asks", return_value=False):
            self.assertIsNone(plugin.pre_gateway_dispatch(_event("yes"), gw))

        with patch.object(plugin, "has_pending_binary_asks", return_value=True), \
             patch.object(plugin, "handle_binary_reply", return_value='Cleared "Window tinting" from buy list.') as mocked:
            out = plugin.pre_gateway_dispatch(_event("y 1"), gw)
        self.assertEqual(out, {"action": "skip", "reason": "canned-binary-ask"})
        mocked.assert_called_once_with("yes", "1")

        # Non-binary message still falls through even when asks are pending.
        with patch.object(plugin, "has_pending_binary_asks", return_value=True):
            self.assertIsNone(plugin.pre_gateway_dispatch(_event("yes let's check PR 120"), gw))

        # Non-photon platform never intercepts.
        with patch.object(plugin, "has_pending_binary_asks", return_value=True):
            self.assertIsNone(plugin.pre_gateway_dispatch(_event("y", platform="cli"), gw))


if __name__ == "__main__":
    unittest.main()
