#!/usr/bin/env python3
"""pre_gateway_dispatch skip vs fall-through."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import __init__ as plugin


def _event(text: str, platform: str = "photon"):
    plat = SimpleNamespace(value=platform)
    src = SimpleNamespace(platform=plat, chat_id="chat-1", user_id="u1")
    return SimpleNamespace(text=text, source=src)


class TestHook(unittest.TestCase):
    def test_budget_skips(self):
        gw = SimpleNamespace(adapters={})
        with patch.object(plugin, "handle", return_value="STATUS"):
            out = plugin.pre_gateway_dispatch(_event("budget status"), gw)
        self.assertEqual(out["action"], "skip")
        self.assertEqual(out["reason"], "canned-budget")

    def test_todo_add_skips(self):
        gw = SimpleNamespace(adapters={})
        with patch.object(plugin, "handle", return_value="Parked: X"):
            out = plugin.pre_gateway_dispatch(_event("pls add Y to to do list"), gw)
        self.assertEqual(out["action"], "skip")
        self.assertEqual(out["reason"], "canned-todo_add")

    def test_copy_change_falls_through(self):
        gw = SimpleNamespace(adapters={})
        out = plugin.pre_gateway_dispatch(
            _event("please change the pushover notification style"),
            gw,
        )
        self.assertIsNone(out)

    def test_non_photon_falls_through(self):
        gw = SimpleNamespace(adapters={})
        out = plugin.pre_gateway_dispatch(_event("budget", platform="cli"), gw)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
