#!/usr/bin/env python3
"""Tax overlay: sticky character, spend rule, tie-out, quarterly window."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.auto_review import apply_review
from budget_bot.models import Transaction
from budget_bot.notify import PUSH_KINDS
from budget_bot.rules import counts_as_spend
from budget_bot import store
from budget_bot.tax import (
    load_book,
    prior_quarter,
    review_event,
    ruling_for,
    tie_out,
)


def _book(items: list[dict]) -> dict:
    return {"version": 1, "items": items}


def _write_book(root: Path, items: list[dict]) -> None:
    path = root / "accounting"
    path.mkdir(parents=True, exist_ok=True)
    (path / "tax-classifications.json").write_text(json.dumps(_book(items)))


class TestTaxBook(unittest.TestCase):
    def test_return_of_capital_does_not_move_spend(self):
        deposit = Transaction(
            id="sale-1",
            date="2026-10-20",
            amount_cents=-30000,
            name="Deposit from buyer",
            category="Misc / Other",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(
                root,
                [
                    {
                        "txn_id": "sale-1",
                        "character": "return_of_capital",
                        "date": "2026-10-20",
                    }
                ],
            )
            with patch("budget_bot.config.state_dir", lambda: root):
                self.assertFalse(counts_as_spend(deposit))
                ruling = ruling_for("sale-1")
                self.assertIsNotNone(ruling)
                self.assertFalse(ruling.taxable)
                self.assertFalse(ruling.counts_as_income)

    def test_refund_ruling_nets_spend_even_if_tagged_income(self):
        refund = Transaction(
            id="ref-1",
            date="2026-08-02",
            amount_cents=-1500,
            name="Deposit shop credit",
            category="Income",
            excluded=True,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(root, [{"txn_id": "ref-1", "character": "refund"}])
            with patch("budget_bot.config.state_dir", lambda: root):
                self.assertTrue(counts_as_spend(refund))

    def test_broken_file_falls_open_to_coach_rules(self):
        inflow = Transaction(
            id="x",
            date="2026-08-02",
            amount_cents=-1500,
            name="Deposit shop credit",
            category="Misc / Other",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "accounting"
            path.mkdir()
            (path / "tax-classifications.json").write_text("{")
            with patch("budget_bot.config.state_dir", lambda: root):
                book = load_book()
                self.assertIsNotNone(book.error)
                self.assertEqual(book.rulings, {})
                self.assertTrue(counts_as_spend(inflow))

    def test_unknown_character_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(root, [{"txn_id": "a", "character": "lottery"}])
            with patch("budget_bot.config.state_dir", lambda: root):
                book = load_book()
                self.assertIsNotNone(book.error)
                self.assertEqual(book.rulings, {})

    def test_auto_review_does_not_retag_locked_deposit(self):
        deposit = Transaction(
            id="sale-1",
            date="2026-10-20",
            amount_cents=-30000,
            name="Deposit Atm night drop",
            category="Kept",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(
                root,
                [{"txn_id": "sale-1", "character": "return_of_capital"}],
            )
            with patch("budget_bot.config.state_dir", lambda: root):
                txns, payload = apply_review([deposit])
                self.assertEqual(txns[0].category, "Kept")
                self.assertTrue(txns[0].excluded)
                self.assertFalse(txns[0].transfer)
                self.assertEqual(
                    payload["reviews"]["sale-1"]["review_status"], "tax_locked"
                )
                txns[0].excluded = False
                txns[0].category = "Misc / Other"
                self.assertFalse(counts_as_spend(txns[0]))

    def test_unlocked_deposit_still_tags_income(self):
        deposit = Transaction(
            id="unlocked-deposit",
            date="2026-10-20",
            amount_cents=-30000,
            name="Deposit paycheck",
            category="Misc / Other",
        )
        txns, payload = apply_review([deposit])
        self.assertEqual(txns[0].category, "Income")
        self.assertEqual(payload["reviews"]["unlocked-deposit"]["reason"], "inflow_incomeish")

    def test_upsert_keeps_coach_category_on_locked_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(root, [{"txn_id": "sale-1", "character": "return_of_capital"}])
            with (
                patch.object(store, "state_dir", lambda: root),
                patch("budget_bot.config.state_dir", lambda: root),
            ):
                store.save_txns(
                    [
                        Transaction(
                            id="sale-1",
                            date="2026-10-20",
                            amount_cents=-30000,
                            name="Deposit",
                            category="Kept",
                            excluded=True,
                        )
                    ]
                )
                incoming = Transaction(
                    id="sale-1",
                    date="2026-10-20",
                    amount_cents=-30000,
                    name="Deposit",
                    category="Income",
                    excluded=False,
                    transfer=False,
                )
                all_tx, new = store.upsert_txns([incoming])
            self.assertEqual(new, [])
            self.assertEqual(all_tx[0].category, "Kept")
            self.assertTrue(all_tx[0].excluded)

    def test_tie_out_buckets(self):
        txns = [
            Transaction(
                id="sale-1",
                date="2026-10-20",
                amount_cents=-30000,
                name="Deposit",
                category="Income",
                excluded=True,
            ),
            Transaction(
                id="spend-1",
                date="2026-10-05",
                amount_cents=899,
                name="Netflix",
                category="Entertainment & Subs",
            ),
            Transaction(
                id="twin-1",
                date="2026-08-02",
                amount_cents=500,
                name="Twin",
                excluded=True,
            ),
            Transaction(
                id="pend-1",
                date="2026-10-22",
                amount_cents=-100,
                name="Pending deposit",
                pending=True,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(
                root,
                [
                    {
                        "txn_id": "sale-1",
                        "character": "return_of_capital",
                        "date": "2026-10-20",
                        "note": "Personal sale under cost basis.",
                    }
                ],
            )
            with patch("budget_bot.config.state_dir", lambda: root):
                year = tie_out(txns, date(2026, 1, 1), date(2026, 12, 31))
                q4 = tie_out(txns, date(2026, 10, 1), date(2026, 12, 31))
        self.assertEqual(year["characters"]["return_of_capital"]["n"], 1)
        self.assertEqual(year["characters"]["return_of_capital"]["in_cents"], 30000)
        self.assertFalse(year["characters"]["return_of_capital"]["taxable"])
        self.assertEqual(year["unclassified"]["n"], 1)
        self.assertEqual(year["unclassified"]["out_cents"], 899)
        self.assertEqual(year["excluded_or_transfer"]["n"], 1)
        self.assertEqual(year["pending_unclassified"]["n"], 1)
        self.assertEqual(q4["unclassified"]["n"], 1)
        self.assertTrue(any("2026-06-25" in g["note"] for g in year["gaps"]))
        self.assertEqual(q4["gaps"], [])
        self.assertTrue(any("Netflix" in n["note"] for n in q4["notes"]))
        self.assertEqual(year["orphan_rulings"], [])

    def test_prior_quarter_and_window(self):
        self.assertEqual(
            prior_quarter(date(2027, 1, 3))[0:2],
            (2026, 4),
        )
        _year, _q, start, end = prior_quarter(date(2027, 1, 3))
        self.assertEqual(start, date(2026, 10, 1))
        self.assertEqual(end, date(2026, 12, 31))
        self.assertEqual(prior_quarter(date(2026, 10, 23))[1], 3)
        self.assertEqual(prior_quarter(date(2026, 4, 1))[1], 1)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("budget_bot.config.state_dir", lambda: root):
                self.assertIsNone(review_event([], date(2026, 10, 23)))
                ev = review_event([], date(2027, 1, 3))
        self.assertIsNotNone(ev)
        self.assertEqual(ev.subject, "Budget Bot tax review 2026 Q4")
        self.assertEqual(ev.key, "tax_review|2026Q4")
        self.assertEqual(ev.kind, "tax_review")
        self.assertNotIn("tax_review", PUSH_KINDS)
        self.assertIn("Pace and Pushover stay on the coach book", ev.body)

    def test_orphan_ruling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_book(
                root,
                [
                    {
                        "txn_id": "missing",
                        "character": "gift",
                        "date": "2026-11-02",
                    }
                ],
            )
            with patch("budget_bot.config.state_dir", lambda: root):
                report = tie_out([], date(2026, 10, 1), date(2026, 12, 31))
        self.assertEqual(report["orphan_rulings"], ["missing"])


if __name__ == "__main__":
    unittest.main()
