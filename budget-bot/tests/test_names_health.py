#!/usr/bin/env python3
"""MasterMoney / Alliant name-health detector (no network)."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.models import Transaction
from hermes_finance.names_health import assess_names_health


def _t(i: str, d: str, name: str, inst: str, cents: int = 1000, **kw) -> Transaction:
    return Transaction(
        id=i,
        date=d,
        amount_cents=cents,
        name=name,
        institution=inst,
        **kw,
    )


class TestNamesHealth(unittest.TestCase):
    def test_sept_style_blanks_not_live(self):
        txns = [
            _t(
                f"plaid-{i}",
                "2026-09-0" + str((i % 8) + 1),
                "Withdrawal Debit Card MasterMoney Card",
                "1st-northern-california-credit-union",
            )
            for i in range(12)
        ]
        out = assess_names_health(txns, date(2026, 9, 9))
        self.assertFalse(out["names_live"])
        self.assertGreaterEqual(out["norcal_plaid_n"], 8)

    def test_named_tails_go_live(self):
        txns = [
            _t(
                f"plaid-{i}",
                "2026-09-0" + str((i % 8) + 1),
                "Withdrawal Debit Card MasterMoney Card - ACE PARKING",
                "1st-northern-california-credit-union",
            )
            for i in range(12)
        ]
        out = assess_names_health(txns, date(2026, 9, 9))
        self.assertTrue(out["names_live"])
        self.assertEqual(out["reason"], "norcal_named")

    def test_alliant_named_spend_goes_live(self):
        txns = [
            _t(
                f"plaid-a{i}",
                "2026-09-0" + str((i % 8) + 1),
                "Safeway",
                "alliant-credit-union",
            )
            for i in range(6)
        ]
        out = assess_names_health(txns, date(2026, 9, 9))
        self.assertTrue(out["names_live"])
        self.assertEqual(out["reason"], "alliant_named")


if __name__ == "__main__":
    unittest.main()
