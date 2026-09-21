#!/usr/bin/env python3
"""Cash-on-hand from cached Plaid balances (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.balances import (
    cash_on_hand_cents,
    checking_cash_by_cu,
    is_spendable_cash,
)


class TestCashOnHand(unittest.TestCase):
    def test_norcal_checking_only_ignores_paypal_and_savings(self):
        snap = {
            "items": [
                {
                    "institution": "paypal",
                    "accounts": [
                        {
                            "name": "PayPal",
                            "type": "depository",
                            "subtype": "paypal",
                            "available_cents": 20_300,
                        }
                    ],
                },
                {
                    "institution": "1st-northern-california-credit-union",
                    "accounts": [
                        {
                            "name": "Share Savings",
                            "type": "depository",
                            "subtype": "savings",
                            "available_cents": 500,
                        },
                        {
                            "name": "Free Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": 5000,
                            "current_cents": 5000,
                        },
                    ],
                },
            ]
        }
        # $203 PayPal must not cover $50 checking — CSAA ACH hits NorCal
        self.assertEqual(cash_on_hand_cents(snap), 5000)
        norcal = snap["items"][1]
        self.assertFalse(
            is_spendable_cash(
                {"type": "depository", "subtype": "savings", "name": "Share"},
                item=norcal,
            )
        )
        self.assertFalse(
            is_spendable_cash(
                {"type": "depository", "subtype": "paypal", "name": "PayPal"},
                item=snap["items"][0],
            )
        )

    def test_none_when_empty(self):
        self.assertIsNone(cash_on_hand_cents(None))
        self.assertIsNone(cash_on_hand_cents({"items": []}))
        self.assertIsNone(
            cash_on_hand_cents(
                {
                    "items": [
                        {
                            "accounts": [
                                {
                                    "name": "Visa",
                                    "type": "credit",
                                    "subtype": "credit card",
                                    "available_cents": 50_000,
                                }
                            ]
                        }
                    ]
                }
            )
        )

    def test_alliant_checking_is_not_norcal_cash(self):
        from budget_bot.balances import is_norcal_institution, is_norcal_item

        alliant = {
            "institution": "alliant-credit-union",
            "accounts": [
                {
                    "name": "High-Rate Checking",
                    "type": "depository",
                    "subtype": "checking",
                    "available_cents": 80_000,
                }
            ],
        }
        snap = {
            "items": [
                alliant,
                {
                    "institution": "1st-northern-california-credit-union",
                    "accounts": [
                        {
                            "name": "Free Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": 5000,
                        }
                    ],
                },
            ]
        }
        self.assertFalse(is_norcal_institution("alliant-credit-union"))
        self.assertFalse(is_norcal_item(alliant))
        self.assertTrue(is_norcal_institution("1st-northern-california-credit-union"))
        # Alliant $800 must not pool into NorCal cash
        self.assertEqual(cash_on_hand_cents(snap), 5000)
        self.assertFalse(
            is_spendable_cash(
                {"type": "depository", "subtype": "checking", "name": "High-Rate Checking"},
                item=alliant,
            )
        )
        piles = checking_cash_by_cu(snap)
        self.assertEqual(piles.get("alliant"), 80_000)
        self.assertEqual(piles.get("norcal"), 5000)

    def test_falls_back_to_current(self):
        snap = {
            "items": [
                {
                    "institution": "1st-northern-california-credit-union",
                    "accounts": [
                        {
                            "name": "Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": None,
                            "current_cents": 1200,
                        }
                    ]
                }
            ]
        }
        self.assertEqual(cash_on_hand_cents(snap), 1200)


if __name__ == "__main__":
    unittest.main()
