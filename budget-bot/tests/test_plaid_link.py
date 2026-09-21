#!/usr/bin/env python3
"""Plaid Link OAuth plumbing + new-item quarantine (no live Plaid)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.plaid_link_server import (
    configured_redirect_uri,
    page_html,
    redirect_mount,
    save_inflight,
    load_inflight,
    save_item,
)


class TestRedirectUri(unittest.TestCase):
    def test_configured_redirect_uri_from_env(self):
        uri = "https://example.ts.net/hermes-plaid-deadbeef"
        with patch.dict("os.environ", {"PLAID_REDIRECT_URI": uri}, clear=False):
            self.assertEqual(configured_redirect_uri({}), uri)
            self.assertEqual(redirect_mount(uri), "/hermes-plaid-deadbeef")

    def test_missing_uri_is_none(self):
        with patch.dict("os.environ", {"PLAID_REDIRECT_URI": ""}, clear=False):
            with patch(
                "budget_bot.plaid_link_server.load_plaid_env",
                return_value={},
            ):
                self.assertIsNone(configured_redirect_uri({}))


class TestPageHtmlOauth(unittest.TestCase):
    def test_page_handles_oauth_return(self):
        html = page_html(
            "link-sandbox-token",
            "/hermes-plaid-abc",
            heading="Plaid Link",
            blurb="hi",
            button="Connect",
        ).decode()
        self.assertIn("oauth_state_id", html)
        self.assertIn("receivedRedirectUri", html)
        self.assertIn("Finishing OAuth", html)
        self.assertIn("link-sandbox-token", html)


class TestSaveItemQuarantine(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {"BUDGET_BOT_STATE": self._td.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._td.cleanup()

    def test_alliant_new_item_quarantined_paypal_not(self):
        alliant = save_item("tok-a", "item-alliant", {"institution": "alliant-credit-union"})
        self.assertTrue(alliant.get("quarantine"))
        self.assertEqual(alliant.get("quarantine_reason"), "new_item_preview")
        paypal = save_item("tok-p", "item-paypal", {"institution": "paypal"})
        self.assertFalse(paypal.get("quarantine"))

    def test_update_does_not_requarantine(self):
        first = save_item("tok-a", "item-alliant", {"institution": "alliant-credit-union"})
        self.assertTrue(first.get("quarantine"))
        # promote
        items_path = Path(self._td.name) / "tokens" / "items.json"
        items = json.loads(items_path.read_text())
        items[0]["quarantine"] = False
        items[0]["promoted_at"] = "2026-09-09T00:00:00Z"
        items_path.write_text(json.dumps(items))
        again = save_item("tok-a2", "item-alliant", {"institution": "alliant-credit-union"})
        self.assertFalse(again.get("quarantine"))
        self.assertEqual(again.get("promoted_at"), "2026-09-09T00:00:00Z")

    def test_inflight_roundtrip(self):
        save_inflight({"link_token": "abc", "mode": "create"})
        self.assertEqual(load_inflight().get("link_token"), "abc")


if __name__ == "__main__":
    unittest.main()
