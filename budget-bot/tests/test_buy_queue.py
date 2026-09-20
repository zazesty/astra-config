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

from hermes_finance.buy_queue import (
    apply_buy_queue_pulls,
    persist_buy_queue_pulls,
    proposed_buy_queue_pulls,
)
from hermes_finance.models import Transaction


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

    def test_apply_drops_pulled(self):
        left = apply_buy_queue_pulls(QUEUE, [{"id": "honda-oil"}])
        self.assertEqual([r["id"] for r in left], ["us-mobile-annual", "supergrok-annual"])


class TestPersist(unittest.TestCase):
    def test_persist_writes_config_and_log(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = {"buy_queue": list(QUEUE), "hardcap_cents": 105000}
            with (
                patch("hermes_finance.buy_queue.state_dir", lambda: root),
                patch("hermes_finance.config.state_dir", lambda: root),
                patch("hermes_finance.config.config_path", lambda: root / "config.json"),
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
