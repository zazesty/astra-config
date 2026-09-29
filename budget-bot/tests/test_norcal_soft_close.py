#!/usr/bin/env python3
"""NorCal soft-close: September statement deprecates forward rows."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.models import Transaction
from budget_bot import norcal_soft_close as sc
from budget_bot import store


def _txn(day: str, institution: str = "1st-norcal", amount: int = 100) -> Transaction:
    return Transaction(
        id=f"t-{institution}-{day}",
        date=day,
        amount_cents=amount,
        name="Withdrawal",
        institution=institution,
    )


class TestNorcalSoftClose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for target in (
            "budget_bot.norcal_soft_close.state_dir",
            "budget_bot.store.state_dir",
        ):
            p = patch(target, lambda: self.root)
            p.start()
            self.addCleanup(p.stop)

    def test_mid_month_statement_does_not_deprecate(self):
        out = sc.arm_from_statement([_txn("2026-09-10")], source="sep-partial.pdf", institution="1st-norcal")
        self.assertFalse(out["deprecated"])
        self.assertEqual(sc.status(), "")

    def test_month_end_statement_drops_october_and_keeps_september(self):
        store.save_txns(
            [
                _txn("2026-09-23"),
                _txn("2026-10-02"),
                _txn("2026-10-02", institution="alliant-credit-union"),
            ]
        )
        incoming = [_txn("2026-09-28"), _txn("2026-10-01")]
        out = sc.arm_from_statement(incoming, source="sep.pdf", institution="1st-norcal")
        self.assertTrue(out["deprecated"])
        self.assertEqual(out["close_through"], "2026-09-30")
        self.assertEqual(out["removed_forward"], 1)
        left = {t.id for t in store.load_txns()}
        self.assertIn("t-1st-norcal-2026-09-23", left)
        self.assertNotIn("t-1st-norcal-2026-10-02", left)
        self.assertIn("t-alliant-credit-union-2026-10-02", left)
        kept, dropped = sc.without_forward(incoming)
        self.assertEqual([t.date for t in kept], ["2026-09-28"])
        self.assertEqual([t.date for t in dropped], ["2026-10-01"])

    def test_second_import_is_idempotent(self):
        sc.arm_from_statement([_txn("2026-09-30")], source="sep.pdf", institution="1st-norcal")
        again = sc.arm_from_statement([_txn("2026-09-30")], source="sep.pdf", institution="1st-norcal")
        self.assertTrue(again["already"])
        self.assertFalse(again["extended"])

    def test_later_statement_extends_the_close(self):
        sc.arm_from_statement([_txn("2026-09-28")], source="sep.pdf", institution="1st-norcal")
        store.save_txns(
            [
                _txn("2026-09-28"),
                _txn("2026-10-12"),
                _txn("2026-11-02"),
                _txn("2026-11-02", institution="alliant-credit-union"),
            ]
        )
        out = sc.arm_from_statement([_txn("2026-10-12")], source="oct.pdf", institution="1st-norcal")
        self.assertTrue(out["extended"])
        self.assertEqual(out["close_through"], "2026-10-31")
        left = {(t.institution, t.date) for t in store.load_txns()}
        self.assertIn(("1st-norcal", "2026-09-28"), left)
        self.assertIn(("1st-norcal", "2026-10-12"), left)
        self.assertNotIn(("1st-norcal", "2026-11-02"), left)
        self.assertIn(("alliant-credit-union", "2026-11-02"), left)

    def test_september_file_with_an_october_date_stays_at_september(self):
        store.save_txns([_txn("2026-10-03")])
        incoming = [_txn("2026-09-28"), _txn("2026-10-01")]
        out = sc.arm_from_statement(incoming, source="sep.pdf", institution="1st-norcal")
        self.assertFalse(out["extended"])
        self.assertEqual(out["close_through"], "2026-09-30")
        self.assertNotIn("t-1st-norcal-2026-10-03", {t.id for t in store.load_txns()})

    def test_later_file_can_jump_to_its_latest_month(self):
        sc.arm_from_statement([_txn("2026-09-30")], source="sep.pdf", institution="1st-norcal")
        out = sc.arm_from_statement(
            [_txn("2026-10-02"), _txn("2026-11-15")],
            source="nov.pdf",
            institution="1st-norcal",
        )
        self.assertTrue(out["extended"])
        self.assertEqual(out["close_through"], "2026-11-30")
