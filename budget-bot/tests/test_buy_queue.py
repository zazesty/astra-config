#!/usr/bin/env python3
"""Buy-queue auto-pull: named + amount only; never opaque MasterMoney."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.buy_queue import (
    apply_buy_queue_pulls,
    buy_queue_ask_events,
    persist_buy_queue_pulls,
    proposed_buy_queue_asks,
    proposed_buy_queue_pulls,
)
from budget_bot.models import Transaction


QUEUE = [
    {
        "id": "honda-oil",
        "name": "Mostly Honda oil change",
        "amount_cents": 12403,
        "match": r"MOSTLY\s*HONDA",
    },
    {
        "id": "us-mobile-annual",
        "name": "US Mobile annual",
        "amount_cents": 28000,
        "match": r"US MOBILE",
    },
    {
        "id": "supergrok-annual",
        "name": "SuperGrok annual",
        "amount_cents": 30000,
        "match": r"GROK|\\bXAI\\b",
    },
]


def _t(**kw) -> Transaction:
    base = dict(
        id="t1",
        date="2026-09-20",
        amount_cents=12403,
        name="MOSTLY HONDA",
        merchant_name="MOSTLY HONDA",
        institution="alliant-credit-union",
    )
    base.update(kw)
    return Transaction(**base)


class TestProposedPulls(unittest.TestCase):
    def test_named_amount_pulls(self):
        pulls = proposed_buy_queue_pulls(QUEUE, [_t()])
        self.assertEqual([p["id"] for p in pulls], ["honda-oil"])
        self.assertEqual(pulls[0]["txn_id"], "t1")

    def test_opaque_mastermoney_never_pulls(self):
        t = _t(
            name="Withdrawal Debit Card MasterMoney Card",
            merchant_name="Withdrawal Debit Card MasterMoney Card",
            amount_cents=12403,
        )
        self.assertEqual(proposed_buy_queue_pulls(QUEUE, [t]), [])

    def test_monthly_us_mobile_does_not_pull_annual(self):
        t = _t(
            id="usm",
            name="US MOBILE",
            merchant_name="US MOBILE",
            amount_cents=2700,
        )
        self.assertEqual(proposed_buy_queue_pulls(QUEUE, [t]), [])

    def test_named_annual_us_mobile_pulls(self):
        t = _t(
            id="usm-yr",
            name="US MOBILE",
            merchant_name="US MOBILE",
            amount_cents=28000,
        )
        pulls = proposed_buy_queue_pulls(QUEUE, [t])
        self.assertEqual([p["id"] for p in pulls], ["us-mobile-annual"])

    def test_amount_only_does_not_pull(self):
        t = _t(name="Amazon", merchant_name="Amazon", amount_cents=12403)
        self.assertEqual(proposed_buy_queue_pulls(QUEUE, [t]), [])

    def test_pending_and_transfer_skipped(self):
        self.assertEqual(
            proposed_buy_queue_pulls(QUEUE, [_t(pending=True)]),
            [],
        )
        self.assertEqual(
            proposed_buy_queue_pulls(QUEUE, [_t(transfer=True, excluded=True)]),
            [],
        )

    def test_one_txn_one_row_closest_amount(self):
        q = [
            {
                "id": "a",
                "name": "A",
                "amount_cents": 12000,
                "match": r"NORELCO",
            },
            {
                "id": "b",
                "name": "B",
                "amount_cents": 12500,
                "match": r"NORELCO",
            },
        ]
        t = _t(name="NORELCO", merchant_name="NORELCO", amount_cents=12400)
        pulls = proposed_buy_queue_pulls(q, [t])
        self.assertEqual([p["id"] for p in pulls], ["b"])

    def test_not_before_skips_older_ledger_hit(self):
        row = {
            "id": "rice-cooker",
            "name": "Rice cooker",
            "amount_cents": 10000,
            "match": r"COSTCO WHSE|WWW\s*COSTCO|COSTCO\.COM",
            "not_before": "2026-09-22",
        }
        old = _t(
            id="whse-apr",
            date="2026-04-01",
            name="COSTCO WHSE #06 CONCORD",
            merchant_name="COSTCO WHSE #06",
            amount_cents=10060,
        )
        gas = _t(
            id="gas",
            date="2026-09-23",
            name="COSTCO GAS #066",
            merchant_name="COSTCO GAS",
            amount_cents=10000,
        )
        bought = _t(
            id="whse-new",
            date="2026-09-22",
            name="COSTCO WHSE #06",
            merchant_name="COSTCO WHSE",
            amount_cents=9900,
        )
        self.assertEqual(proposed_buy_queue_pulls([row], [old, gas]), [])
        pulls = proposed_buy_queue_pulls([row], [old, bought])
        self.assertEqual([p["id"] for p in pulls], ["rice-cooker"])
        self.assertEqual(pulls[0]["txn_id"], "whse-new")

    def test_costco_confirm_asks_and_does_not_clear(self):
        row = {
            "id": "rice-cooker",
            "name": "Rice cooker",
            "amount_cents": 10000,
            "merchant": "Costco",
            "match": r"COSTCO WHSE|WWW\s*COSTCO|COSTCO\.COM",
            "not_before": "2026-09-22",
            "confirm": True,
        }
        old = _t(
            id="old",
            date="2026-04-01",
            name="COSTCO WHSE #06",
            merchant_name="COSTCO WHSE",
            amount_cents=10060,
        )
        gas = _t(
            id="gas",
            date="2026-09-23",
            name="COSTCO GAS #066",
            merchant_name="COSTCO GAS",
            amount_cents=10500,
        )
        trip = _t(
            id="whse",
            date="2026-09-23",
            name="COSTCO WHSE #06",
            merchant_name="COSTCO WHSE",
            amount_cents=10500,
        )
        philips = {
            "id": "norelco-bt7670",
            "name": "Philips Norelco Beard Trimmer 7000",
            "amount_cents": 12000,
            "merchant": "Philips",
            "match": "PHILIPS|NORELCO",
        }
        bought = _t(
            id="trim",
            date="2026-09-23",
            name="PHILIPS NORELCO",
            merchant_name="PHILIPS",
            amount_cents=12000,
        )
        queue = [row, philips]
        self.assertEqual(proposed_buy_queue_pulls(queue, [old, gas, trip]), [])
        self.assertEqual(
            [a["txn_id"] for a in proposed_buy_queue_asks(queue, [old, gas, trip])],
            ["whse"],
        )
        pulls = proposed_buy_queue_pulls(queue, [trip, bought])
        self.assertEqual([p["id"] for p in pulls], ["norelco-bt7670"])
        events = buy_queue_ask_events(queue, [trip])
        self.assertEqual(events[0].kind, "buy_queue_ask")
        self.assertEqual(events[0].payload["push_priority"], 0)
        self.assertIn("Costco $105", events[0].body)
        self.assertNotIn("WHSE", events[0].body)
        self.assertNotIn("Grok", events[0].body)
        self.assertEqual(events[0].payload["open_url_title"], "Yes or no")

    def test_blank_merchant_does_not_match(self):
        row = {
            "id": "headphones",
            "name": "Headphones",
            "amount_cents": 4000,
            "merchant": "",
            "match": "",
        }
        t = _t(name="AMAZON", merchant_name="AMAZON", amount_cents=4000)
        self.assertEqual(proposed_buy_queue_pulls([row], [t]), [])
        self.assertEqual(proposed_buy_queue_asks([row], [t]), [])

    def test_apply_drops_pulled(self):
        left = apply_buy_queue_pulls(QUEUE, [{"id": "honda-oil"}])
        self.assertEqual([r["id"] for r in left], ["us-mobile-annual", "supergrok-annual"])


class TestPersist(unittest.TestCase):
    def test_persist_writes_config_and_log(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = {"buy_queue": list(QUEUE), "hardcap_cents": 105000}
            with (
                patch("budget_bot.buy_queue.state_dir", lambda: root),
                patch("budget_bot.config.state_dir", lambda: root),
                patch("budget_bot.config.config_path", lambda: root / "config.json"),
            ):
                pulls = persist_buy_queue_pulls(cfg, [_t()])
                self.assertEqual([p["id"] for p in pulls], ["honda-oil"])
                saved = json.loads((root / "config.json").read_text())
                self.assertEqual(
                    [r["id"] for r in saved["buy_queue"]],
                    ["us-mobile-annual", "supergrok-annual"],
                )
                log = (root / "buy_queue_pulled.jsonl").read_text().strip().splitlines()
                self.assertEqual(len(log), 1)
                self.assertEqual(json.loads(log[0])["id"], "honda-oil")


if __name__ == "__main__":
    unittest.main()
