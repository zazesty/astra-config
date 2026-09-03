#!/usr/bin/env python3
"""Unit tests for auto-review rules, MCC, merchant parse."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.auto_review import apply_review, rule_review
from hermes_finance.import_xlsx import extract_mcc, guess_merchant
from hermes_finance.models import Transaction


class TestMerchantParse(unittest.TestCase):
    def test_ref_with_mcc(self):
        name = (
            "Withdrawal Debit Card MasterMoney Card REF#: 5365DJLCQTTI 5542 "
            "- CHEVRON 0094800 1700 CASTRO ST OAKLAND CA"
        )
        self.assertEqual(extract_mcc(name), "5542")
        self.assertIn("CHEVRON", guess_merchant(name).upper())

    def test_pos_merchant(self):
        name = "Withdrawal POS #025753 - RALEY’S 321 3360 SAN PABLO DAM RD. SAN"
        self.assertIsNone(extract_mcc(name))
        self.assertIn("RALEY", guess_merchant(name).upper())


class TestRules(unittest.TestCase):
    def test_seed_supplements(self):
        t = Transaction(
            id="1",
            date="2026-03-02",
            amount_cents=536,
            name="SEED.COM 615 HAMPTON",
            merchant_name="SEED.COM",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Medical & Health")
        self.assertEqual(r.review_status, "auto_accepted")

    def test_divine_dove_charity(self):
        t = Transaction(
            id="1",
            date="2026-01-06",
            amount_cents=200,
            name="SQ *DIVINE DOVE LLC.",
            merchant_name="DIVINE DOVE",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Charity / Donations")

    def test_vending_dining(self):
        t = Transaction(
            id="1",
            date="2026-02-04",
            amount_cents=400,
            name="CTLP*J AND J VENDING I",
            merchant_name="CTLP*J AND J VENDING",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Dining Out")

    def test_debtoredu_irregular(self):
        t = Transaction(
            id="1",
            date="2026-05-31",
            amount_cents=3990,
            name="001 DEBTOREDU LLC",
            merchant_name="DEBTOREDU",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Irregular Expenses")

    def test_monument_gas_vs_cstore(self):
        gas = Transaction(
            id="g",
            date="2026-02-09",
            amount_cents=4151,
            name="MONUMENT 76 2300 MONUMENT BLVD",
            merchant_name="MONUMENT 76",
        )
        small = Transaction(
            id="s",
            date="2026-04-18",
            amount_cents=325,
            name="MONUMENT 76 2300 MONUMENT BLVD",
            merchant_name="MONUMENT 76",
        )
        self.assertEqual(rule_review(gas).category, "Gas")
        self.assertEqual(rule_review(small).category, "Misc / Other")

    def test_venmo_under_20(self):
        t = Transaction(
            id="v",
            date="2026-01-07",
            amount_cents=1000,
            name="VENMO NAME: ZAVDI ZULIANI",
            merchant_name="VENMO NAME",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Misc / Other")
        self.assertEqual(r.review_status, "auto_accepted")

    def test_mcc_gas(self):
        t = Transaction(
            id="m",
            date="2026-01-01",
            amount_cents=2000,
            name=(
                "MasterMoney Card REF#: ABC123 5542 - SOME STATION 100 MAIN ST"
            ),
            merchant_name="MasterMoney Card REF#: ABC123 5542",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Gas")
        self.assertTrue(r.reason.startswith("mcc:") or r.category == "Gas")
        # merchant should be refreshed off the opaque REF# label
        self.assertNotIn("REF#", (t.merchant_name or "").upper())

    def test_credit_voucher_is_refund_not_income(self):
        t = Transaction(
            id="r",
            date="2026-08-16",
            amount_cents=-750,
            name="Withdrawal Adjustment Debit Card Credit Voucher",
        )
        r = rule_review(t)
        self.assertNotEqual(r.category, "Income")
        self.assertEqual(r.review_status, "auto_accepted")
        self.assertEqual(r.reason, "inflow_refund")

    def test_st_vin_charity_not_contra_costa_income(self):
        t = Transaction(
            id="sv",
            date="2026-08-02",
            amount_cents=1013,
            name=(
                "Withdrawal Debit Card MasterMoney Card - "
                "THE SOCIETY OF ST. VIN 2815 CONTRA COSTA"
            ),
            merchant_name="THE SOCIETY OF ST. VIN 2815 CONTRA COSTA",
            institution="1st-norcal",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Shopping")
        self.assertEqual(r.review_status, "auto_accepted")

    def test_co_contra_deposit_is_income(self):
        t = Transaction(
            id="cc",
            date="2026-03-11",
            amount_cents=-82400,
            name="Deposit CONTRA - CO: CONTRA",
            merchant_name="CONTRA",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Income")

    def test_import_dedupe_exclude_survives_review(self):
        t = Transaction(
            id="imp",
            date="2026-08-03",
            amount_cents=2700,
            name="Recurring Withdrawal Debit Card MasterMoney Card - US MOBILE",
            merchant_name="US MOBILE",
            institution="1st-norcal",
            excluded=True,
        )
        apply_review([t])
        self.assertTrue(t.excluded)

    def test_plaid_statement_twin_stays_excluded(self):
        t = Transaction(
            id="plaid-twin",
            date="2026-08-03",
            amount_cents=2700,
            name="Recurring Withdrawal Debit Card MasterMoney Card",
            merchant_name="MasterMoney Card",
            institution="1st-northern-california-credit-union",
            excluded=True,
            transfer=False,
        )
        apply_review([t])
        self.assertTrue(t.excluded)

    def test_apple_bill_is_saas(self):
        t = Transaction(
            id="a",
            date="2026-08-07",
            amount_cents=99,
            name="Recurring Withdrawal Debit Card MasterMoney Card - APPLE.COM/BILL",
            merchant_name="APPLE.COM/BILL ONE APPLE PARK CUPERTINO CA",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Software & Tools")
        self.assertEqual(r.review_status, "auto_accepted")

    def test_valve_entertainment(self):
        t = Transaction(
            id="v",
            date="2026-08-20",
            amount_cents=500,
            name="Valve Corporation",
            merchant_name="Valve Corporation",
            institution="paypal",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Entertainment & Subs")
        self.assertEqual(r.review_status, "auto_accepted")

    def test_miharu_dining(self):
        t = Transaction(
            id="m",
            date="2026-08-30",
            amount_cents=1350,
            name="TST*MIHARU ICE CREAM L 1951 TELEGRAPH AVE",
            merchant_name="TST*MIHARU ICE CREAM L 1951 TELEGRAPH AVE",
        )
        r = rule_review(t)
        self.assertEqual(r.category, "Dining Out")


if __name__ == "__main__":
    unittest.main()
