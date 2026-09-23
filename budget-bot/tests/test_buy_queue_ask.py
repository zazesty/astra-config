#!/usr/bin/env python3
"""Yes/No link for an uncertain buy-queue charge."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.buy_queue_ask import (
    Handler,
    apply_answer,
    ensure_secret,
    mint_ask,
    page_html,
    token_from_path,
)
from budget_bot.notify import _push_env
from budget_bot.models import AlertEvent
from http.server import HTTPServer


ASK = {
    "id": "rice-cooker",
    "name": "Rice <cooker>",
    "merchant": "Costco",
    "amount_cents": 10000,
    "txn_id": "txn-1",
    "txn_cents": 10500,
    "txn_date": "2026-09-23",
}


class TestAskPage(unittest.TestCase):
    def test_page_escapes_and_has_two_buttons(self):
        raw = page_html(ASK, buttons=True).decode()
        self.assertIn("Rice &lt;cooker&gt;?", raw)
        self.assertIn("Yes, that was it", raw)
        self.assertIn("No, leave it", raw)
        self.assertNotIn("Rice <cooker>", raw)

    def test_token_path_accepts_full_or_stripped(self):
        secret = "ab" * 16
        token = "cd" * 16
        self.assertEqual(token_from_path(f"/bq-{secret}/{token}", secret), token)
        self.assertEqual(token_from_path(f"/{token}", secret), token)
        self.assertIsNone(token_from_path(f"/bq-{secret}", secret))
        self.assertIsNone(token_from_path("/health", secret))

    def test_yes_clears_and_no_leaves(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = {
                "buy_queue": [
                    {
                        "id": "rice-cooker",
                        "name": "Rice cooker",
                        "amount_cents": 10000,
                        "merchant": "Costco",
                        "match": "COSTCO",
                        "confirm": True,
                    }
                ]
            }
            (root / "config.json").write_text(json.dumps(cfg))
            with (
                patch("budget_bot.config.state_dir", lambda: root),
                patch("budget_bot.buy_queue.state_dir", lambda: root),
                patch("budget_bot.config.config_path", lambda: root / "config.json"),
                patch("budget_bot.buy_queue_sync.reconcile_buy_queue", lambda c: c),
                patch("budget_bot.buy_queue_ask.funnel_host", lambda: "example.test"),
            ):
                url = mint_ask(ASK)
                self.assertTrue(url.startswith("https://example.test/bq-"))
                token = url.rsplit("/", 1)[-1]
                again = mint_ask(ASK)
                self.assertEqual(again, url)
                code, body = apply_answer(token, "no")
                self.assertEqual(code, 200)
                self.assertIn(b"Left on the list", body)
                left = json.loads((root / "config.json").read_text())
                self.assertEqual(left["buy_queue"][0]["id"], "rice-cooker")
                code, body = apply_answer(token, "yes")
                self.assertEqual(code, 200)
                self.assertIn(b"Cleared", body)
                left = json.loads((root / "config.json").read_text())
                self.assertEqual(left["buy_queue"], [])
                self.assertEqual(apply_answer("0" * 32, "yes")[0], 404)

    def test_http_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config.json").write_text(json.dumps({"buy_queue": []}))
            with (
                patch("budget_bot.config.state_dir", lambda: root),
                patch("budget_bot.buy_queue.state_dir", lambda: root),
                patch("budget_bot.config.config_path", lambda: root / "config.json"),
                patch("budget_bot.buy_queue_sync.reconcile_buy_queue", lambda c: c),
                patch("budget_bot.buy_queue_ask.funnel_host", lambda: "example.test"),
            ):
                secret = ensure_secret()
                url = mint_ask(ASK)
                token = url.rsplit("/", 1)[-1]
                srv = HTTPServer(("127.0.0.1", 0), Handler)
                port = srv.server_address[1]
                thread = threading.Thread(target=srv.serve_forever, daemon=True)
                thread.start()
                try:
                    page = urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/bq-{secret}/{token}"
                    ).read()
                    self.assertIn(b"Yes, that was it", page)
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/{token}",
                        data=b"answer=no",
                        method="POST",
                    )
                    answered = urllib.request.urlopen(req).read()
                    self.assertIn(b"Left on the list", answered)
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope")
                        self.fail("expected 404")
                    except HTTPError as e:
                        self.assertEqual(e.code, 404)
                finally:
                    srv.shutdown()
                    thread.join(timeout=2)
                    srv.server_close()

    def test_push_env_carries_https_link_only(self):
        ev = AlertEvent(
            kind="buy_queue_ask",
            subject="Rice cooker?",
            body="Costco $105\n",
            key="k",
            payload={"open_url": "https://example.test/bq-abc/def", "open_url_title": "Yes or no"},
        )
        env = _push_env(ev)
        self.assertEqual(env["PUSHOVER_URL"], "https://example.test/bq-abc/def")
        self.assertEqual(env["PUSHOVER_URL_TITLE"], "Yes or no")
        bad = AlertEvent(
            kind="info",
            subject="x",
            body="y",
            key="k2",
            payload={"open_url": "http://nope"},
        )
        self.assertNotIn("PUSHOVER_URL", _push_env(bad))


if __name__ == "__main__":
    unittest.main()
