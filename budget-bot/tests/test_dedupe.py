#!/usr/bin/env python3
"""NorCal statement/XLSX wins over overlapping Plaid twins."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.dedupe import apply_import_plaid_dedupe
from hermes_finance.models import Transaction


def _t(
    id_: str,
    *,
    date: str,
    amount_cents: int,
    institution: str,
    excluded: bool = False,
    transfer: bool = False,
    name: str = "x",
    merchant_name: str | None = None,
) -> Transaction:
    return Transaction(
        id=id_,
        date=date,
        amount_cents=amount_cents,
        name=name,
        merchant_name=merchant_name,
        institution=institution,
        excluded=excluded,
        transfer=transfer,
    )


class TestImportPlaidDedupe(unittest.TestCase):
    def test_statement_wins_excludes_plaid(self):
        txns = [
            _t("imp-1", date="2026-08-07", amount_cents=1444, institution="norcal"),
            _t(
                "plaid-1",
                date="2026-08-07",
                amount_cents=1444,
                institution="1st-northern-california-credit-union",
            ),
            _t("imp-2", date="2026-08-06", amount_cents=500, institution="1st-norcal"),
        ]
        n = apply_import_plaid_dedupe(txns)
        self.assertEqual(n, 1)
        self.assertFalse(txns[0].excluded)
        self.assertTrue(txns[1].excluded)
        self.assertFalse(txns[2].excluded)

    def test_no_plaid_noop(self):
        txns = [
            _t("imp-1", date="2026-08-07", amount_cents=1444, institution="norcal"),
        ]
        n = apply_import_plaid_dedupe(txns)
        self.assertEqual(n, 0)
        self.assertFalse(txns[0].excluded)

    def test_reclaims_previously_excluded_statement(self):
        txns = [
            _t(
                "imp-1",
                date="2026-08-07",
                amount_cents=1444,
                institution="norcal",
                excluded=True,
            ),
            _t(
                "plaid-1",
                date="2026-08-07",
                amount_cents=1444,
                institution="1st-northern-california-credit-union",
            ),
        ]
        n = apply_import_plaid_dedupe(txns)
        self.assertEqual(n, 1)
        self.assertFalse(txns[0].excluded)
        self.assertTrue(txns[1].excluded)

    def test_transfer_statement_keeps_plaid_out(self):
        txns = [
            _t(
                "pdf-1",
                date="2026-08-09",
                amount_cents=-20000,
                institution="1st-norcal",
                transfer=True,
                name="Deposit PAYPAL - CO: PAYPAL",
            ),
            _t(
                "plaid-1",
                date="2026-08-09",
                amount_cents=-20000,
                institution="1st-northern-california-credit-union",
                name="Deposit PAYPAL",
            ),
        ]
        n = apply_import_plaid_dedupe(txns)
        self.assertEqual(n, 1)
        self.assertTrue(txns[0].transfer)
        self.assertTrue(txns[1].excluded)
        self.assertTrue(txns[1].transfer)


if __name__ == "__main__":
    unittest.main()
