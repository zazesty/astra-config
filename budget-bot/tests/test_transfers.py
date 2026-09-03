#!/usr/bin/env python3
"""Transfer heuristic: real moves vs CU MasterMoney debit false positives."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.transfers import is_debit_card_purchase, looks_like_transfer


class TestDebitCardNotTransfer(unittest.TestCase):
    def test_mastermoney_ignores_plaid_transfer_out(self):
        raw = {
            "personal_finance_category": {
                "primary": "TRANSFER_OUT",
                "detailed": "TRANSFER_OUT_WITHDRAWAL",
                "confidence_level": "LOW",
            },
            "category": ["Transfer", "Withdrawal"],
            "transaction_type": "special",
        }
        for name in (
            "Recurring Withdrawal Debit Card MasterMoney Card",
            "Withdrawal Debit Card MasterMoney Card",
            "Withdrawal Debit Card MasterMoney Card REF#: ABC 5542 - CHEVRON",
        ):
            self.assertTrue(is_debit_card_purchase(name=name))
            self.assertFalse(
                looks_like_transfer(
                    name=name,
                    merchant_name=None,
                    category="TRANSFER_OUT",
                    plaid_raw=raw,
                ),
                msg=name,
            )

    def test_pos_not_transfer(self):
        self.assertFalse(
            looks_like_transfer(
                name="Withdrawal POS #353017 - 7-ELEVEN 1097 Mohr Ln",
                category="Transfer",
                plaid_raw={"category": ["Transfer", "Withdrawal"]},
            )
        )


class TestRealTransfers(unittest.TestCase):
    def test_share_and_home_banking(self):
        for name in (
            "Withdrawal Transfer To Share 10",
            "Deposit Transfer From Share 00",
            "Withdrawal Home Banking Transfer To Share 10",
            "Deposit Home Banking Transfer From Share 91",
        ):
            self.assertTrue(looks_like_transfer(name=name), msg=name)

    def test_csaa_bill_pay_is_not_transfer(self):
        self.assertFalse(
            looks_like_transfer(
                name="Withdrawal CSAA INSURANCE - CO: CSAA INSURANCE",
                merchant_name="CSAA INSURANCE",
                category="Transfer",
                plaid_raw={
                    "personal_finance_category": {"primary": "TRANSFER_OUT"},
                    "category": ["Transfer", "Withdrawal"],
                },
            )
        )

    def test_paypal_bridge(self):
        for name in (
            "Deposit PAYPAL",
            "Withdrawal PAYPAL",
            "Deposit PAYPAL - CO: PAYPAL",
        ):
            self.assertTrue(looks_like_transfer(name=name), msg=name)

    def test_plaid_transfer_still_counts_without_debit_name(self):
        raw = {
            "personal_finance_category": {
                "primary": "TRANSFER_IN",
                "detailed": "TRANSFER_IN_TRANSFER_IN_FROM_APPS",
            }
        }
        self.assertTrue(
            looks_like_transfer(
                name="Venmo",
                category="TRANSFER_IN",
                plaid_raw=raw,
            )
        )


if __name__ == "__main__":
    unittest.main()
