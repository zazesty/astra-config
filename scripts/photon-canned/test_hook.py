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


class TestMemoryAuto(unittest.TestCase):
    def test_marker_opens_the_review_gate(self):
        calls = []

        def stock(*args, **kwargs):
            calls.append(args)
            return "staged"

        with patch.object(plugin, "_auto_consolidate_enabled", return_value=True), \
             patch.object(plugin, "_stock_background_delete_gate", stock):
            out = plugin._open_gate(None, "patch", None, "memory", "new", "old")
        self.assertIsNone(out)
        self.assertEqual(calls, [])

    def test_missing_marker_keeps_the_stock_gate(self):
        def stock(*args, **kwargs):
            return "staged"

        with patch.object(plugin, "_auto_consolidate_enabled", return_value=False), \
             patch.object(plugin, "_stock_background_delete_gate", stock):
            out = plugin._open_gate(None, "remove", None)
        self.assertEqual(out, "staged")


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

    def test_natural_todo_list_skips(self):
        gw = SimpleNamespace(adapters={})
        with patch.object(plugin, "handle", return_value="open=1"):
            out = plugin.pre_gateway_dispatch(
                _event("Please list Astra todo items"), gw
            )
        self.assertEqual(out["action"], "skip")
        self.assertEqual(out["reason"], "canned-todo_list")

    def test_buy_list_skips(self):
        gw = SimpleNamespace(adapters={})
        with patch.object(plugin, "handle", return_value="A  Item  $10  list only"):
            out = plugin.pre_gateway_dispatch(
                _event("what's on my buy list"), gw
            )
        self.assertEqual(out["action"], "skip")
        self.assertEqual(out["reason"], "canned-buy_list")

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
