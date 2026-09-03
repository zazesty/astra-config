#!/usr/bin/env python3
"""Recurring detect: two-month active rule."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.models import Transaction
from hermes_finance.recurring import detect_recurring, normalize_merchant_key


class TestRecurring(unittest.TestCase):
    def test_active_requires_cur_and_prev(self):
        txns = [
            Transaction(
                id="a", date="2026-06-05", amount_cents=699, name="Spotify", merchant_name="Spotify"
            ),
            Transaction(
                id="b", date="2026-07-05", amount_cents=699, name="Spotify", merchant_name="Spotify"
            ),
            Transaction(
                id="c", date="2026-05-05", amount_cents=2000, name="Old", merchant_name="OnlyMay"
            ),
            Transaction(
                id="d", date="2026-06-05", amount_cents=2000, name="Old", merchant_name="OnlyMay"
            ),
        ]
        hits = detect_recurring(txns, as_of=date(2026, 7, 15))
        by = {h.merchant_key: h for h in hits}
        self.assertTrue(by["SPOTIFY"].active)
        self.assertFalse(by["ONLYMAY"].active)  # Jun+May, not Jul+Jun as active? wait May+Jun, not Jul
        # OnlyMay has May+Jun only → not active as of July
        self.assertIn("ONLYMAY", by)

    def test_normalize_strips_noise(self):
        t = Transaction(
            id="1",
            date="2026-01-01",
            amount_cents=100,
            name="Withdrawal Debit Card MasterMoney Card - SPOTIFY USA INC 123 MAIN",
            merchant_name="SPOTIFY USA INC 123 MAIN",
        )
        self.assertIn("SPOTIFY", normalize_merchant_key(t))


if __name__ == "__main__":
    unittest.main()
