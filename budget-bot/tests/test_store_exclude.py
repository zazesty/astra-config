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

from hermes_finance.models import Transaction
from hermes_finance import store


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


if __name__ == "__main__":
    unittest.main()
