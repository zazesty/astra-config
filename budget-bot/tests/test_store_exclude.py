#!/usr/bin/env python3
"""Manual exclude survives Plaid re-upsert."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.models import Transaction
from budget_bot import store


class TestExcludeSurvivesUpsert(unittest.TestCase):
    def test_keeps_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(store, "state_dir", lambda: root):
                existing = Transaction(
                    id="plaid-dup",
                    date="2026-06-18",
                    amount_cents=553,
                    name="MasterMoney",
                    institution="1st-northern-california-credit-union",
                    excluded=True,
                )
                store.save_txns([existing])
                incoming = Transaction(
                    id="plaid-dup",
                    date="2026-06-18",
                    amount_cents=553,
                    name="MasterMoney",
                    institution="1st-northern-california-credit-union",
                    excluded=False,
                )
                all_tx, new = store.upsert_txns([incoming])
                self.assertEqual(new, [])
                self.assertTrue(all_tx[0].excluded)

    def test_keeps_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(store, "state_dir", lambda: root):
                existing = Transaction(
                    id="plaid-share",
                    date="2026-09-08",
                    amount_cents=-500,
                    name="Deposit",
                    institution="alliant-credit-union",
                    transfer=True,
                    excluded=True,
                    category="Transfer",
                )
                store.save_txns([existing])
                incoming = Transaction(
                    id="plaid-share",
                    date="2026-09-08",
                    amount_cents=-500,
                    name="Deposit",
                    institution="alliant-credit-union",
                    transfer=False,
                    excluded=False,
                    category="Income",
                )
                all_tx, new = store.upsert_txns([incoming])
                self.assertEqual(new, [])
                self.assertTrue(all_tx[0].transfer)
                self.assertTrue(all_tx[0].excluded)


if __name__ == "__main__":
    unittest.main()
