#!/usr/bin/env python3
"""NorCal statement PDF parser — register only, not ATM-glance recap."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.import_statement_pdf import parse_statement_text

_SNIPPET = """
                                                CHECKING
 Date            Transaction Description                                                                   Amount

ID : 10 CHECKING
 08/01/2026      Starting Balance                                                                            $124.65
 08/02/2026      Recurring Withdrawal Debit Card MasterMoney Card                                             -16.00
                 08/02/2026 REF#: 6214DJP4PHC5 5734 - GROK XAI 1450 PAGE MILL ROAD
                 PALO ALTO CA
 08/02/2026      Withdrawal Debit Card MasterMoney Card - THE SOCIETY OF ST. VIN 2815                          -10.13
                 CONTRA COSTA
 08/02/2026      Recurring Withdrawal Debit Card MasterMoney Card                                              -10.00
                 07/31/2026 REF#: 6212DJB8DU6M 5734 - GROK XAI 1450 PAGE MILL ROAD
                 PALO ALTO CA
 08/02/2026      Withdrawal Debit Card MasterMoney Card                                                           -0.92
                 07/31/2026 REF#: 6212DJFN3G14 5542 - CHEVRON 0094800 1700 CASTRO
                 ST OAKLAND CA
 08/03/2026      Deposit PAYPAL - CO: PAYPAL                                                     100.00
 08/31/2026      Ending Balance for CHECKING                                                           $218.75


                                        ATM ACTIVITY AT A GLANCE
  Date             Amount                 Date          Amount                       Date             Amount
  Withdrawal
  08/02/2026           16.00               08/12/2026             20.12               08/23/2026         30.93
  08/02/2026           10.13               08/12/2026             15.00               08/23/2026         32.10
  08/02/2026           10.00               08/13/2026             75.00               08/25/2026         10.00
  08/02/2026            0.92               08/14/2026             24.50               08/25/2026          2.50
                53 ATM Withdrawals and Other Debits: $1,565.51
  Deposit
  08/16/2026            7.50               08/28/2026            129.80
                2 ATM Deposits and Other Credits: $137.30


                                      Total For This Period   Total Year-to-Date   Total Last Year
         Total Returned Item Fees                     $0.00               $70.00          $112.00


                                              MONEY MARKET
 Date            Transaction Description                                                               Amount
 08/01/2026      Starting Balance                                                                        $0.36
 08/31/2026      Ending Balance for 1ST CLASS MONEY MARKET                                               $0.36
"""


class TestParseStatementSkipsAtmGlance(unittest.TestCase):
    def test_aug1_empty_aug2_four_register_rows(self):
        txns = parse_statement_text(_SNIPPET)
        self.assertEqual({t.date for t in txns}, {"2026-08-02", "2026-08-03"})
        aug2 = [t for t in txns if t.date == "2026-08-02"]
        self.assertEqual(len(aug2), 4)
        self.assertEqual(sorted(t.amount_cents for t in aug2), [92, 1000, 1013, 1600])
        self.assertTrue(any("GROK XAI" in (t.merchant_name or "") for t in aug2))
        self.assertTrue(any("CHEVRON" in (t.merchant_name or "") for t in aug2))
        # Recap inflows must not leak (unsigned glance amounts look like deposits).
        self.assertFalse(any(t.amount_cents < 0 and t.date == "2026-08-02" for t in txns))
        paypal = [t for t in txns if t.date == "2026-08-03"]
        self.assertEqual(len(paypal), 1)
        self.assertTrue(paypal[0].transfer)

    def test_july_glance_does_not_create_inflows(self):
        text = """
 07/01/2026      Withdrawal Debit Card MasterMoney Card - CLIPPER                                         -10.00
                                          ATM ACTIVITY AT A GLANCE
  07/01/2026           10.00                 07/20/2026             60.00                07/27/2026         50.00
  07/22/2026            2.77
                                              MONEY MARKET
 07/01/2026      Deposit Dividend Split Rate                                                                 0.09
"""
        txns = parse_statement_text(text)
        self.assertEqual(len(txns), 2)
        self.assertEqual(txns[0].amount_cents, 1000)
        self.assertEqual(txns[1].date, "2026-07-01")
        self.assertLess(txns[1].amount_cents, 0)  # dividend inflow

    def test_page_break_keeps_ref_merchant(self):
        text = """
 09/16/2026    Withdrawal Debit Card MasterMoney Card                                            -51.89
\f                                                                                    Account Statement
                                                                                    Page: 3 of 4

                                                                                    September 01 - September 30, 2026
                                                                                    Account Number xxxxxxxxx7
PO Box 509, Martinez, CA 94553-0144
888-387-8632 l 1stnorcalcu.org
 Date            Transaction Description                                                                Amount
                 09/16/2026 REF#: 625906637882 5542 - COSTCO GAS #066 CONCORD
                 CONCORD CA
 09/17/2026      Withdrawal Debit Card MasterMoney Card                                                    -9.34
                 09/17/2026 REF#: 626006630964 5542 - COSTCO GAS #066 CONCORD
                 CONCORD CA
"""
        txns = parse_statement_text(text)
        self.assertEqual(len(txns), 2)
        self.assertEqual(txns[0].amount_cents, 5189)
        self.assertIn("COSTCO GAS", txns[0].merchant_name or "")
        self.assertNotIn("Account Statement", txns[0].name)
        self.assertEqual(txns[1].amount_cents, 934)
        self.assertIn("COSTCO GAS", txns[1].merchant_name or "")

    def test_blank_line_does_not_merge_next_transaction(self):
        text = """
 09/16/2026    Withdrawal Debit Card MasterMoney Card - IHERB IHERB.COM                              -35.34

 09/17/2026    Deposit PAYPAL - CO: PAYPAL                                                     100.00
"""
        txns = parse_statement_text(text)
        self.assertEqual(len(txns), 2)
        self.assertIn("IHERB", txns[0].merchant_name or "")
        self.assertNotIn("PAYPAL", txns[0].name)
        self.assertTrue(txns[1].transfer)


if __name__ == "__main__":
    unittest.main()
