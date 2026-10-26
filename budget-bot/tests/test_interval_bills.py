#!/usr/bin/env python3
"""Every-N-month bills (iHerb-style 1–6) and the confirm queue."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.config import DEFAULT_CONFIG
from budget_bot.models import Transaction
from budget_bot.recurring import detect_interval_candidates
from budget_bot.rules import (
    bill_due_dates_in_range,
    effective_bills_reserve_cents,
    proposed_subscription_reserve_updates,
    upcoming_unpaid_bills_cents,
)


def _txn(tid: str, day: str, cents: int, merchant: str) -> Transaction:
    return Transaction(
        id=tid,
        date=day,
        amount_cents=cents,
        name=merchant,
        merchant_name=merchant,
        institution="alliant",
    )


def _every_two() -> dict:
    return {
        "name": "Example Autoship",
        "match": "EXAMPLE AUTOSHIP",
        "amount_cents": 4000,
        "interval_months": 2,
        "anchor": "2026-05",
        "day_of_month": 7,
        "institution": "alliant",
    }


class TestIntervalReserve(unittest.TestCase):
    def test_dues_land_on_the_cadence_only(self):
        dues = bill_due_dates_in_range(
            _every_two(), date(2026, 5, 1), date(2026, 11, 30)
        )
        self.assertEqual(
            dues,
            [date(2026, 5, 7), date(2026, 7, 7), date(2026, 9, 7), date(2026, 11, 7)],
        )

    def test_missing_anchor_reserves_nothing(self):
        bill = _every_two()
        del bill["anchor"]
        reserved = effective_bills_reserve_cents(
            [bill],
            [],
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            as_of=date(2026, 7, 1),
            period_kind="calendar",
        )
        self.assertEqual(reserved, 0)

    def test_off_month_is_clear_when_the_prior_due_posted(self):
        paid = [_txn("may", "2026-05-07", 4000, "EXAMPLE AUTOSHIP")]
        reserved = effective_bills_reserve_cents(
            [_every_two()],
            paid,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            as_of=date(2026, 6, 20),
            period_kind="calendar",
            arrears_lookback_months=6,
        )
        self.assertEqual(reserved, 0)

    def test_unpaid_due_stays_reserved_into_the_next_month(self):
        reserved = effective_bills_reserve_cents(
            [_every_two()],
            [],
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            as_of=date(2026, 6, 20),
            period_kind="calendar",
            arrears_lookback_months=6,
        )
        self.assertEqual(reserved, 4000)

    def test_due_month_reserves_the_full_pull_not_half(self):
        paid = [_txn("may", "2026-05-07", 4000, "EXAMPLE AUTOSHIP")]
        reserved = effective_bills_reserve_cents(
            [_every_two()],
            paid,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            as_of=date(2026, 7, 1),
            period_kind="calendar",
        )
        self.assertEqual(reserved, 4000)
        pull = upcoming_unpaid_bills_cents(
            [_every_two()],
            paid,
            date(2026, 7, 5),
            horizon_days=3,
        )
        self.assertEqual(pull, 4000)

    def test_posted_due_clears(self):
        paid = [
            _txn("may", "2026-05-07", 4000, "EXAMPLE AUTOSHIP"),
            _txn("jul", "2026-07-07", 4000, "EXAMPLE AUTOSHIP"),
        ]
        reserved = effective_bills_reserve_cents(
            [_every_two()],
            paid,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            as_of=date(2026, 7, 8),
            period_kind="calendar",
            arrears_lookback_months=6,
        )
        self.assertEqual(reserved, 0)

    def test_five_month_gap_is_quiet_until_the_due_month(self):
        bill = {
            "name": "Example Five",
            "match": "EXAMPLE FIVE",
            "amount_cents": 2500,
            "interval_months": 5,
            "anchor": "2026-04",
            "day_of_month": 8,
        }
        august = effective_bills_reserve_cents(
            [bill],
            [_txn("apr", "2026-04-08", 2500, "EXAMPLE FIVE")],
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            as_of=date(2026, 8, 15),
            period_kind="calendar",
            arrears_lookback_months=6,
        )
        september = effective_bills_reserve_cents(
            [bill],
            [_txn("apr", "2026-04-08", 2500, "EXAMPLE FIVE")],
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            as_of=date(2026, 9, 1),
            period_kind="calendar",
            arrears_lookback_months=6,
        )
        self.assertEqual(august, 0)
        self.assertEqual(september, 2500)

    def test_amount_follow_reaches_back_across_the_gap(self):
        bill = {
            "name": "Example Four",
            "match": "EXAMPLE FOUR",
            "amount_cents": 5000,
            "interval_months": 4,
            "anchor": "2026-01",
            "day_of_month": 10,
        }
        posted = _txn("may", "2026-05-10", 5300, "EXAMPLE FOUR")
        result = proposed_subscription_reserve_updates(
            [bill],
            [posted],
            as_of=date(2026, 8, 1),
        )
        self.assertEqual(result["asks"], [])
        self.assertEqual(result["updates"][0]["new_cents"], 5300)

    def test_monthly_bill_still_reserves_every_month(self):
        bill = {"name": "Spotify", "match": "SPOTIFY", "amount_cents": 699, "day_of_month": 19}
        reserved = effective_bills_reserve_cents(
            [bill],
            [],
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            as_of=date(2026, 8, 1),
            period_kind="calendar",
            arrears_lookback_months=0,
        )
        self.assertEqual(reserved, 699)
        self.assertEqual(DEFAULT_CONFIG["hardcap_cents"], 110_000)


class TestIntervalDetect(unittest.TestCase):
    def test_two_month_pair_is_a_candidate(self):
        txns = [
            _txn("a", "2026-06-23", 1080, "OPENROUTER, INC"),
            _txn("b", "2026-08-30", 1080, "OPENROUTER, INC"),
        ]
        rows = detect_interval_candidates(txns, [], as_of=date(2026, 10, 3))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["interval_months"], 2)
        self.assertEqual(rows[0]["amount_cents"], 1080)
        self.assertEqual(rows[0]["anchor"], "2026-06")

    def test_one_extra_charge_still_qualifies(self):
        txns = [
            _txn("a", "2026-06-23", 1080, "OPENROUTER, INC"),
            _txn("b", "2026-08-07", 780, "OPENROUTER, INC"),
            _txn("c", "2026-08-30", 1080, "OPENROUTER, INC"),
        ]
        rows = detect_interval_candidates(txns, [], as_of=date(2026, 10, 3))
        self.assertEqual([row["interval_months"] for row in rows], [2])

    def test_varying_basket_is_not_a_bill(self):
        txns = [
            _txn("a", "2026-05-19", 9015, "iHerb, LLC"),
            _txn("b", "2026-06-15", 3343, "iHerb LLC"),
            _txn("c", "2026-07-16", 3633, "IHERB IHERB.COM 86DI8"),
            _txn("d", "2026-08-04", 9667, "iHerb"),
            _txn("e", "2026-09-22", 5960, "iHerb"),
        ]
        self.assertEqual(detect_interval_candidates(txns, [], as_of=date(2026, 10, 3)), [])

    def test_existing_bill_is_skipped(self):
        txns = [
            _txn("a", "2026-05-07", 1000, "TMOBILE*AUTO PAY"),
            _txn("b", "2026-07-07", 1000, "TMOBILE*AUTO PAY"),
            _txn("c", "2026-09-06", 1000, "TMOBILE*AUTO PAY"),
        ]
        bills = [{"name": "T-Mobile", "match": r"T-?MOBILE|TMOBILE", "amount_cents": 1000}]
        self.assertEqual(detect_interval_candidates(txns, bills, as_of=date(2026, 10, 3)), [])

    def test_seven_month_gap_is_not_an_iherb_period(self):
        txns = [
            _txn("a", "2026-01-04", 2000, "EXAMPLE GAP"),
            _txn("b", "2026-08-04", 2000, "EXAMPLE GAP"),
        ]
        self.assertEqual(detect_interval_candidates(txns, [], as_of=date(2026, 10, 3)), [])

    def test_stale_monthly_pair_drops(self):
        txns = [
            _txn("a", "2026-01-28", 818, "AMAZON PRIME*IL8"),
            _txn("b", "2026-02-28", 818, "AMAZON PRIME*DI00"),
            _txn("c", "2026-03-29", 818, "AMAZON PRIME*Z00"),
        ]
        self.assertEqual(
            detect_interval_candidates(txns, [], as_of=date(2026, 10, 3)),
            [],
        )

    def test_two_deliveries_in_one_month_are_not_one_cadence(self):
        txns = [
            _txn("a", "2026-06-02", 3093, "VSP"),
            _txn("b", "2026-06-15", 3093, "VSP"),
            _txn("c", "2026-08-23", 3093, "VSP"),
        ]
        self.assertEqual(detect_interval_candidates(txns, [], as_of=date(2026, 10, 3)), [])


if __name__ == "__main__":
    unittest.main()
