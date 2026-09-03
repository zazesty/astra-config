#!/usr/bin/env python3
"""Unit tests for Plaid amount_unit / quarantine / sync durability (no live Plaid)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.models import Transaction
from hermes_finance.plaid_client import transactions_sync
from hermes_finance.plaid_sync import (
    normalize_amount_unit,
    plaid_amount_to_cents,
    plaid_txn_to_hermes,
    set_item_flags,
    sync_all_items,
    sync_item,
    _removed_to_hermes_ids,
)
from hermes_finance import store


class TestAmountUnit(unittest.TestCase):
    def test_plaid_amount_to_cents_units(self):
        self.assertEqual(plaid_amount_to_cents(12.34, "dollars"), 1234)
        self.assertEqual(plaid_amount_to_cents(1234, "cents"), 1234)
        self.assertEqual(plaid_amount_to_cents(5.0, "dollars"), 500)

    def test_norcal_balance_unit_detects_100x_and_fix(self):
        from hermes_finance.plaid_sync import norcal_balance_unit

        still = [
            {
                "name": "PRIME SHARES",
                "balances": {"current": 500},
            },
            {
                "name": "1ST CLASS MONEY MARKET",
                "balances": {"current": 36},
            },
            {
                "name": "CHECKING",
                "balances": {"current": 10141, "available": 10141},
            },
        ]
        self.assertEqual(norcal_balance_unit(still), "cents")
        fixed = [
            {"name": "PRIME SHARES", "balances": {"current": 5.0}},
            {"name": "1ST CLASS MONEY MARKET", "balances": {"current": 0.36}},
        ]
        self.assertEqual(norcal_balance_unit(fixed), "dollars")

    def test_dollars_multiplies_by_100(self):
        t = plaid_txn_to_hermes(
            {"amount": 12.34, "transaction_id": "a", "name": "Coffee"},
            "paypal",
            amount_unit="dollars",
        )
        assert t is not None
        self.assertEqual(t.amount_cents, 1234)
        self.assertEqual(t.id, "plaid-a")

    def test_cents_no_multiply(self):
        t = plaid_txn_to_hermes(
            {"amount": 1234, "transaction_id": "b", "name": "CU"},
            "1st-norcal",
            amount_unit="cents",
        )
        assert t is not None
        self.assertEqual(t.amount_cents, 1234)

    def test_negative_spend_sign_preserved(self):
        t = plaid_txn_to_hermes(
            {"amount": -5.0, "transaction_id": "r", "name": "Refund"},
            "paypal",
            amount_unit="dollars",
        )
        assert t is not None
        self.assertEqual(t.amount_cents, -500)

    def test_none_amount_returns_none(self):
        t = plaid_txn_to_hermes(
            {"amount": None, "transaction_id": "n", "name": "x"},
            "paypal",
        )
        self.assertIsNone(t)

    def test_invalid_unit_raises(self):
        with self.assertRaises(ValueError):
            normalize_amount_unit("centz")
        with self.assertRaises(ValueError):
            plaid_txn_to_hermes(
                {"amount": 1, "transaction_id": "x", "name": "x"},
                "paypal",
                amount_unit="centz",
            )


class TestRemovedIds(unittest.TestCase):
    def test_removed_dict_and_str(self):
        ids = _removed_to_hermes_ids(
            [{"transaction_id": "abc"}, "def", {"pending_transaction_id": "p1"}]
        )
        self.assertEqual(ids, ["plaid-abc", "plaid-def", "plaid-p1"])


class TestStoreRemove(unittest.TestCase):
    def test_remove_txns(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"HERMES_FINANCE_STATE": td}):
                # re-import path uses env at call time via state_dir
                a = Transaction(id="plaid-1", date="2026-08-01", amount_cents=100, name="a")
                b = Transaction(id="plaid-2", date="2026-08-01", amount_cents=200, name="b")
                store.save_txns([a, b])
                n = store.remove_txns(["plaid-1"])
                self.assertEqual(n, 1)
                left = store.load_txns()
                self.assertEqual([t.id for t in left], ["plaid-2"])


class TestQuarantineAndCursor(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.state = self._td.name
        self.env = patch.dict("os.environ", {"HERMES_FINANCE_STATE": self.state})
        self.env.start()
        tokens = Path(self.state) / "tokens"
        tokens.mkdir(parents=True, exist_ok=True)
        # one clean + one quarantined
        items = [
            {
                "item_id": "item-ok",
                "institution": "paypal",
                "token_file": "tok-ok.json",
                "quarantine": False,
                "amount_unit": "dollars",
            },
            {
                "item_id": "item-q",
                "institution": "1st-norcal",
                "token_file": "tok-q.json",
                "quarantine": True,
                "quarantine_reason": "scale",
                "amount_unit": "dollars",
            },
        ]
        (tokens / "items.json").write_text(json.dumps(items, indent=2) + "\n")
        for name in ("tok-ok.json", "tok-q.json"):
            (tokens / name).write_text(json.dumps({"access_token": "access-test"}) + "\n")
        self._accounts = patch(
            "hermes_finance.plaid_sync.accounts_get",
            return_value={"accounts": []},
        )
        self._accounts.start()

    def tearDown(self):
        self._accounts.stop()
        self.env.stop()
        self._td.cleanup()

    def test_sync_item_skips_quarantine_without_force(self):
        with patch("hermes_finance.plaid_sync.transactions_sync") as ts:
            summary = sync_item("item-q", force=False)
            self.assertTrue(summary.get("skipped_quarantine"))
            ts.assert_not_called()

    def test_sync_all_skips_quarantine_by_default(self):
        with patch("hermes_finance.plaid_sync.transactions_sync") as ts:
            ts.return_value = {
                "added": [],
                "modified": [],
                "removed": [],
                "next_cursor": "c1",
                "has_more": False,
            }
            summary = sync_all_items(include_quarantine=False)
            skipped = {x["item_id"] for x in summary.get("skipped_quarantine") or []}
            self.assertIn("item-q", skipped)
            # only non-quarantined item synced
            self.assertEqual(len(summary.get("items") or []), 1)
            self.assertEqual(summary["items"][0]["item_id"], "item-ok")

    def test_cursor_written_only_after_upsert(self):
        """If upsert raises, cursor must not advance."""
        cursor_file = Path(self.state) / "tokens" / "item-ok.cursor"
        self.assertFalse(cursor_file.exists())

        def boom_upsert(batch):
            raise RuntimeError("store fail")

        with patch(
            "hermes_finance.plaid_sync.transactions_sync",
            return_value={
                "added": [
                    {
                        "amount": 1.0,
                        "transaction_id": "t1",
                        "name": "X",
                        "date": "2026-08-01",
                    }
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "CURSOR-NEW",
                "has_more": False,
            },
        ):
            with patch("hermes_finance.plaid_sync.upsert_txns", side_effect=boom_upsert):
                with self.assertRaises(RuntimeError):
                    sync_item("item-ok", force=False)
        self.assertFalse(
            cursor_file.exists(),
            "cursor must not be written when store upsert fails",
        )

    def test_removed_applied_before_cursor(self):
        store.save_txns(
            [
                Transaction(
                    id="plaid-old",
                    date="2026-07-01",
                    amount_cents=999,
                    name="gone",
                    institution="paypal",
                )
            ]
        )
        with patch(
            "hermes_finance.plaid_sync.transactions_sync",
            return_value={
                "added": [],
                "modified": [],
                "removed": [{"transaction_id": "old"}],
                "next_cursor": "C2",
                "has_more": False,
            },
        ):
            summary = sync_item("item-ok", force=False)
        self.assertEqual(summary.get("removed_applied"), 1)
        self.assertEqual(store.load_txns(), [])
        cursor_file = Path(self.state) / "tokens" / "item-ok.cursor"
        self.assertTrue(cursor_file.exists())
        self.assertEqual(cursor_file.read_text().strip(), "C2")

    def test_set_item_flags_preserves_unit_when_none(self):
        set_item_flags("item-q", amount_unit="cents")
        items = json.loads((Path(self.state) / "tokens" / "items.json").read_text())
        q = next(i for i in items if i["item_id"] == "item-q")
        self.assertEqual(q.get("amount_unit"), "cents")
        set_item_flags("item-q", quarantine=False)  # amount_unit omitted
        items = json.loads((Path(self.state) / "tokens" / "items.json").read_text())
        q = next(i for i in items if i["item_id"] == "item-q")
        self.assertEqual(q.get("amount_unit"), "cents")

    def test_transactions_sync_omits_empty_cursor(self):
        """Client omits falsy cursor from body (preview start_cursor='')."""
        with patch("hermes_finance.plaid_client.plaid_post") as post:
            post.return_value = {"added": [], "has_more": False, "next_cursor": ""}
            transactions_sync("tok", "")
            args, _kwargs = post.call_args
            body = args[1]
            self.assertNotIn("cursor", body)


class TestCmdForceFootgun(unittest.TestCase):
    def test_force_without_item_does_not_include_quarantine(self):
        import argparse
        from hermes_finance.run import cmd_plaid_sync

        with patch("hermes_finance.plaid_sync.sync_all_items") as sa:
            sa.return_value = {"items": [], "skipped_quarantine": []}
            args = argparse.Namespace(force=True, item_id=None, include_quarantine=False)
            with patch("builtins.print"):
                cmd_plaid_sync(args)
            sa.assert_called_once_with(include_quarantine=False)


if __name__ == "__main__":
    unittest.main()
