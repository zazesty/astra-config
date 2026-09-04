#!/usr/bin/env python3
"""Unit tests for Hermes-Finance rules (no network)."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.config import DEFAULT_CONFIG
from hermes_finance.models import Transaction
from hermes_finance.rules import (
    bill_due_phase,
    bill_is_remaining,
    budget_alerts,
    counts_as_spend,
    detect_anomalies,
    effective_bills_reserve_cents,
    eom_leftover_event,
    evaluate_budget,
    evaluate_budget_both,
    pace_ratio,
    period_bounds_rolling,
    prior_month_end,
    safe_to_spend_cents,
    spend_in_period,
    upcoming_unpaid_bills_cents,
    canned_cash_bills_cents,
)
from hermes_finance.store import load_fixture

FIXTURE = ROOT / "fixtures" / "sample_txns.json"


class TestSpend(unittest.TestCase):
    def test_transfer_and_pending_excluded(self):
        t_transfer = Transaction(
            id="1", date="2026-07-01", amount_cents=5000, name="x", transfer=True
        )
        t_pending = Transaction(
            id="2", date="2026-07-01", amount_cents=5000, name="y", pending=True
        )
        t_ok = Transaction(id="3", date="2026-07-01", amount_cents=5000, name="z")
        self.assertFalse(counts_as_spend(t_transfer))
        self.assertFalse(counts_as_spend(t_pending, exclude_pending=True))
        self.assertTrue(counts_as_spend(t_ok))

    def test_refund_reduces_spend(self):
        spend = Transaction(id="1", date="2026-07-10", amount_cents=5000, name="Store")
        refund = Transaction(
            id="2", date="2026-07-12", amount_cents=-1200, name="Store refund"
        )
        voucher = Transaction(
            id="3",
            date="2026-07-16",
            amount_cents=-750,
            name="Withdrawal Adjustment Debit Card Credit Voucher",
            category="Income",
        )
        income = Transaction(
            id="4",
            date="2026-07-12",
            amount_cents=-8000,
            name="eBay",
            category="Income",
        )
        self.assertTrue(counts_as_spend(refund))
        self.assertTrue(counts_as_spend(voucher))
        self.assertFalse(counts_as_spend(income))
        total = spend_in_period(
            [spend, refund, voucher, income], date(2026, 7, 1), date(2026, 7, 31)
        )
        self.assertEqual(total, 5000 - 1200 - 750)

    def test_fixture_spend_excludes_transfer_pending(self):
        txns = load_fixture(FIXTURE)
        start, end = date(2026, 7, 1), date(2026, 7, 31)
        spend = spend_in_period(txns, start, end)
        # transfer 20000 + pending 9999 must not count
        self.assertNotIn(
            20000 + 9999,
            [spend],
        )
        expected = 0
        for t in txns:
            if not counts_as_spend(t):
                continue
            if t.date.startswith("2026-07"):
                expected += t.amount_cents
        self.assertEqual(spend, expected)


class TestBudget(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(DEFAULT_CONFIG)
        self.txns = load_fixture(FIXTURE)

    def test_hardcap_seed(self):
        self.assertEqual(self.cfg["hardcap_cents"], 105_000)

    def test_snapshot_as_of_mid_month(self):
        snap = evaluate_budget(self.txns, self.cfg, as_of=date(2026, 7, 21))
        self.assertEqual(snap.days_in_period, 31)
        self.assertEqual(snap.days_elapsed, 21)
        self.assertEqual(snap.hardcap_cents, 105_000)
        self.assertGreater(snap.spend_to_date, 0)
        self.assertLess(snap.spend_to_date, 105_000)  # fixture not over hardcap
        self.assertIn(snap.risk, ("ok", "warn", "breach"))
        self.assertEqual(snap.period_kind, "calendar")

    def test_rolling_period_bounds_30d(self):
        start, end, days_in, days_elapsed = period_bounds_rolling(date(2026, 8, 15))
        self.assertEqual(days_in, 30)
        self.assertEqual(days_elapsed, 16)  # 15 past + as_of
        self.assertEqual(start, date(2026, 7, 31))
        self.assertEqual(end, date(2026, 8, 29))

    def test_rolling_parallel_snapshot(self):
        both = evaluate_budget_both(self.txns, self.cfg, as_of=date(2026, 7, 21))
        cal, roll = both["calendar"], both["rolling_30d"]
        self.assertEqual(cal.period_kind, "calendar")
        self.assertEqual(roll.period_kind, "rolling_30d")
        self.assertEqual(cal.days_in_period, 31)
        self.assertEqual(roll.days_in_period, 30)
        self.assertEqual(roll.days_elapsed, 16)
        # both share hardcap; spends may differ
        self.assertEqual(cal.hardcap_cents, roll.hardcap_cents)
        d = roll.to_dict()
        self.assertEqual(d["period_kind"], "rolling_30d")
        self.assertIn("spend_through", d)

    def test_safe_to_spend_with_bills(self):
        sts = safe_to_spend_cents(100_000, 50_000, bills=[{"amount_cents": 10_000}])
        self.assertEqual(sts, 100_000 - 50_000 - 10_000)

    def test_bill_remaining_horizon_calendar(self):
        bill = {"name": "Grok", "amount_cents": 1000, "day_of_month": 1, "match": r"GROK"}
        # Day 15, unposted overdue → still remaining
        self.assertTrue(
            bill_is_remaining(bill, date(2026, 8, 15), posted=False, period_kind="calendar")
        )
        self.assertEqual(
            bill_due_phase(bill, date(2026, 8, 15), posted=False), "overdue"
        )
        self.assertEqual(
            bill_due_phase(bill, date(2026, 8, 1), posted=False), "due_today"
        )
        self.assertEqual(
            bill_due_phase(bill, date(2026, 8, 1), posted=True), "posted"
        )
        self.assertFalse(
            bill_is_remaining(bill, date(2026, 8, 15), posted=True, period_kind="calendar")
        )
        # Spotify due 19th is rest-of-month on Aug 10 (calendar ignores 7d horizon)
        spot = {"name": "Spotify", "amount_cents": 699, "day_of_month": 19, "match": r"SPOTIFY"}
        self.assertTrue(
            bill_is_remaining(spot, date(2026, 8, 12), posted=False, horizon_days=7)
        )
        self.assertTrue(
            bill_is_remaining(spot, date(2026, 8, 10), posted=False, horizon_days=7)
        )
        # safe-to-spend still reserves past-due unpaid (single month / no arrears stack)
        sts = safe_to_spend_cents(
            100_000,
            0,
            bills=[bill],
            txns=[],
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            as_of=date(2026, 8, 15),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=0,
        )
        self.assertEqual(sts, 100_000 - 1000)

    def test_calendar_sts_rest_of_month_not_next_month(self):
        """Calendar reserves rest of this month only — no September leak on Aug 31."""
        grok = {
            "name": "Grok",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK",
        }
        spot = {
            "name": "Spotify",
            "amount_cents": 699,
            "day_of_month": 19,
            "match": r"SPOTIFY",
        }
        # Aug 10: Spotify later this month is reserved
        self.assertEqual(
            effective_bills_reserve_cents(
                [spot],
                [],
                as_of=date(2026, 8, 10),
                period_kind="calendar",
                arrears_lookback_months=0,
            ),
            699,
        )
        # Aug 31: this month's unpaid Grok only — not Sep 1
        self.assertEqual(
            effective_bills_reserve_cents(
                [grok],
                [],
                as_of=date(2026, 8, 31),
                period_kind="calendar",
                arrears_lookback_months=0,
            ),
            3000,
        )
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 100_000
        cfg["bills"] = [grok, spot]
        cfg["goals"] = []
        cfg["bill_arrears_lookback_months"] = 0
        snap = evaluate_budget([], cfg, as_of=date(2026, 8, 31), period_kind="calendar")
        # spend 0; leftover = hardcap − Aug Grok − Aug Spotify (not Sep)
        self.assertEqual(snap.bills_reserved_cents, 3000 + 699)
        self.assertEqual(snap.safe_to_spend_cents, 100_000 - 3699)
        self.assertEqual(snap.remaining_cents - snap.bills_reserved_cents, snap.safe_to_spend_cents)

    def test_eom_leftover_congrats_and_skip(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 100_000
        cfg["bills"] = []
        cfg["goals"] = []
        self.assertEqual(prior_month_end(date(2026, 9, 1)), date(2026, 8, 31))
        good = evaluate_budget([], cfg, as_of=date(2026, 7, 31))
        ev = eom_leftover_event(good)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.kind, "eom_leftover")
        self.assertEqual(ev.key, "eom_leftover|2026-07")
        self.assertEqual(ev.subject, "Budget Bot: you saved $1,000")
        self.assertIn("July leftover is $1,000 saved, well done!", ev.body)
        self.assertNotIn("Unused after unpaid bills.", ev.body)
        self.assertNotIn("🎉", ev.subject)
        self.assertNotIn("Wooo", ev.body)
        over = [
            Transaction(id="x", date="2026-07-15", amount_cents=150_000, name="over")
        ]
        bad = evaluate_budget(over, cfg, as_of=date(2026, 7, 31))
        self.assertIsNone(eom_leftover_event(bad))
        netted = eom_leftover_event(good, pending_spend_cents=10_000)
        self.assertIsNotNone(netted)
        assert netted is not None
        self.assertEqual(netted.payload["leftover_cents"], 90_000)
        self.assertIn("$900 saved, well done!", netted.body)

    def test_upcoming_unpaid_bills_seven_day_window(self):
        grok = {
            "name": "Grok",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK",
        }
        spot = {
            "name": "Spotify",
            "amount_cents": 699,
            "day_of_month": 19,
            "match": r"SPOTIFY",
        }
        csaa = {
            "name": "CSAA",
            "amount_cents": 6892,
            "day_of_month": 5,
            "match": r"CSAA",
        }
        # Aug 29 → Sep 5: Grok Sep 1 + CSAA Sep 5; Spotify 19th and Aug 5 arrears excluded
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                [grok, spot, csaa],
                [],
                date(2026, 8, 29),
                horizon_days=7,
            ),
            3000 + 6892,
        )
        posted = [
            Transaction(
                id="g1",
                date="2026-09-01",
                amount_cents=3000,
                name="GROK XAI",
            )
        ]
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                [grok, csaa],
                posted,
                date(2026, 8, 29),
                horizon_days=7,
            ),
            6892,
        )
        # Mid-month with nothing due in 7 days
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                [grok, spot],
                [],
                date(2026, 8, 10),
                horizon_days=7,
            ),
            0,
        )
        # Canned cash line: 5-day window (Aug 29 → Sep 3) misses CSAA on the 5th
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                [grok, spot, csaa],
                [],
                date(2026, 8, 29),
                horizon_days=5,
            ),
            3000,
        )

    def test_canned_cash_bills_floor_and_cover(self):
        grok = {"name": "Grok", "amount_cents": 3000, "day_of_month": 1, "match": r"GROK"}
        usm = {
            "name": "US Mobile",
            "amount_cents": 2700,
            "day_of_month": 5,
            "match": r"US MOBILE",
        }
        csaa = {"name": "CSAA", "amount_cents": 6892, "day_of_month": 5, "match": r"CSAA"}
        apple = {
            "name": "Apple",
            "amount_cents": 99,
            "day_of_month": 6,
            "match": r"APPLE",
        }
        hetz = {
            "name": "Hetzner",
            "amount_cents": 1532,
            "day_of_month": 10,
            "match": r"HETZNER",
        }
        bills = [grok, usm, csaa, apple, hetz]
        # $1050 / 30 / 2 = $17.50 floor → Apple + Hetzner hidden
        as_of = date(2026, 9, 3)
        due = canned_cash_bills_cents(
            bills,
            [],
            as_of,
            hardcap_cents=105_000,
            days_in_period=30,
            cash_cents=5_000,  # tight → show
        )
        self.assertEqual(due, 2700 + 6892)  # USM + CSAA (Grok due 1st already passed)
        hidden = canned_cash_bills_cents(
            bills,
            [],
            as_of,
            hardcap_cents=105_000,
            days_in_period=30,
            cash_cents=30_000,  # 2× $95.92 = $192; $300 covers → hide
        )
        self.assertEqual(hidden, 0)

    def test_active_from_skips_dues_before_start(self):
        from hermes_finance.rules import bill_due_dates_in_range

        bill = {
            "name": "EFF",
            "amount_cents": 2500,
            "day_of_month": 31,
            "active_from": "2026-07-01",
        }
        dues = bill_due_dates_in_range(bill, date(2026, 5, 1), date(2026, 9, 30))
        self.assertEqual(
            dues,
            [date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)],
        )

    def test_eff_early_sept_clears_delayed_aug_eom_stays(self):
        """Early-Sept opaque debit clears Aug EOM; Sept EOM remains reserved."""
        bill = {
            "name": "EFF",
            "amount_cents": 2575,
            "day_of_month": 31,
            "match": r"(?i)electronic frontier|www\.eff\.org",
            "active_from": "2026-08-01",
        }
        unpaid = effective_bills_reserve_cents(
            [bill],
            [],
            as_of=date(2026, 9, 3),
            period_kind="calendar",
            arrears_lookback_months=6,
            payment_grace_days=40,
            fuzzy_day_slop=2,
            fuzzy_amount_tol_cents=100,
        )
        self.assertEqual(unpaid, 2575 * 2)
        delayed = [
            Transaction(
                id="eff-aug-late",
                date="2026-09-03",
                amount_cents=2575,
                name="Recurring Withdrawal Debit Card MasterMoney Card",
                merchant_name="Recurring Withdrawal Debit Card MasterMoney Card",
            )
        ]
        after = effective_bills_reserve_cents(
            [bill],
            delayed,
            as_of=date(2026, 9, 3),
            period_kind="calendar",
            arrears_lookback_months=6,
            payment_grace_days=40,
            fuzzy_day_slop=2,
            fuzzy_amount_tol_cents=100,
        )
        self.assertEqual(after, 2575)

    def test_arrears_stack_unpaid_months(self):
        from hermes_finance.rules import effective_bills_reserve_cents

        bill = {
            "name": "CSAA",
            "amount_cents": 6892,
            "day_of_month": 5,
            "match": r"CSAA",
        }
        r = effective_bills_reserve_cents(
            [bill],
            [],
            as_of=date(2026, 8, 10),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=1,  # Jul 1 .. → Jul5 + Aug5
        )
        self.assertEqual(r, 6892 * 2)
        # One payment clears oldest
        tx = [
            Transaction(
                id="p1",
                date="2026-07-06",
                amount_cents=6892,
                name="CSAA",
                merchant_name="CSAA",
            )
        ]
        r2 = effective_bills_reserve_cents(
            [bill],
            tx,
            as_of=date(2026, 8, 10),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=1,
        )
        self.assertEqual(r2, 6892)

    def test_csaa_bounce_then_double_pay_and_fee(self):
        """Bounce then next autopay: 2× premium + CSAA-tacked fee — not bank NSF."""
        from hermes_finance.rules import effective_bills_reserve_cents

        bill = {
            "name": "CSAA",
            "amount_cents": 6892,
            "day_of_month": 5,
            "match": r"CSAA",
        }
        tx = [
            Transaction(
                id="c1",
                date="2026-07-05",
                amount_cents=6892,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="r1",
                date="2026-07-07",
                amount_cents=-6892,
                name="CSAA RETURN",
                merchant_name="CSAA",
            ),
            Transaction(
                id="a",
                date="2026-08-05",
                amount_cents=6892,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="b",
                date="2026-08-05",
                amount_cents=6892,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="f",
                date="2026-08-05",
                amount_cents=2000,
                name="CSAA FEE",
                merchant_name="CSAA",
            ),
        ]
        # lookback 2: Jun+Jul+Aug; bounce cancels Jul attempt; 2 pays clear Jul+Aug → Jun left
        r = effective_bills_reserve_cents(
            [bill],
            tx,
            as_of=date(2026, 8, 10),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=2,
            payment_grace_days=40,
        )
        self.assertEqual(r, 6892)
        # single 2× line clears two months
        r2 = effective_bills_reserve_cents(
            [bill],
            [
                Transaction(
                    id="d",
                    date="2026-08-05",
                    amount_cents=6892 * 2,
                    name="CSAA",
                    merchant_name="CSAA",
                )
            ],
            as_of=date(2026, 8, 10),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=2,
            payment_grace_days=40,
        )
        self.assertEqual(r2, 6892)  # Jun still open; Jul+Aug covered by 2×
        # CSAA fee alone never clears a month
        r3 = effective_bills_reserve_cents(
            [bill],
            [
                Transaction(
                    id="fee",
                    date="2026-08-05",
                    amount_cents=2000,
                    name="CSAA FEE",
                    merchant_name="CSAA",
                )
            ],
            as_of=date(2026, 8, 10),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=2,
            payment_grace_days=40,
        )
        self.assertEqual(r3, 6892 * 3)

    def test_csaa_split_catchup_clears_this_and_next_month(self):
        """$50 + $111.86 in late July is Jul+Aug, not a dropped fee + one month."""
        from hermes_finance.rules import effective_bills_reserve_cents

        bill = {
            "name": "CSAA",
            "amount_cents": 6892,
            "day_of_month": 5,
            "match": r"CSAA",
        }
        tx = [
            Transaction(
                id="part",
                date="2026-07-27",
                amount_cents=5000,
                name="CSAA INSURANCE G P P",
                merchant_name="CSAA INSURANCE",
            ),
            Transaction(
                id="rest",
                date="2026-07-29",
                amount_cents=11186,
                name="CSAA INSURANCE G P P",
                merchant_name="CSAA INSURANCE",
            ),
        ]
        r = effective_bills_reserve_cents(
            [bill],
            tx,
            as_of=date(2026, 8, 12),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=1,
            payment_grace_days=40,
        )
        self.assertEqual(r, 0)

    def test_csaa_split_after_regular_june_still_prepays_aug(self):
        """Regular June + $50/$111.86 split: July takes the $50, leftover prepays Aug."""
        from hermes_finance.rules import effective_bills_reserve_cents

        bill = {
            "name": "CSAA",
            "amount_cents": 6892,
            "day_of_month": 5,
            "match": r"CSAA",
        }
        tx = [
            Transaction(
                id="jun",
                date="2026-06-05",
                amount_cents=6892,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="part",
                date="2026-07-27",
                amount_cents=5000,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="rest",
                date="2026-07-29",
                amount_cents=11186,
                name="CSAA",
                merchant_name="CSAA",
            ),
        ]
        r = effective_bills_reserve_cents(
            [bill],
            tx,
            as_of=date(2026, 8, 12),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=2,
            payment_grace_days=40,
        )
        self.assertEqual(r, 0)

    def test_csaa_split_after_overpay_leftover_still_uses_both_halves(self):
        """A $30 leftover from June plus $50 must not strand the $111.86 sibling."""
        from hermes_finance.rules import effective_bills_reserve_cents

        bill = {
            "name": "CSAA",
            "amount_cents": 6892,
            "day_of_month": 5,
            "match": r"CSAA",
        }
        tx = [
            Transaction(
                id="jun",
                date="2026-06-05",
                amount_cents=6892 + 3050,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="part",
                date="2026-07-27",
                amount_cents=5000,
                name="CSAA",
                merchant_name="CSAA",
            ),
            Transaction(
                id="rest",
                date="2026-07-29",
                amount_cents=11186,
                name="CSAA",
                merchant_name="CSAA",
            ),
        ]
        r = effective_bills_reserve_cents(
            [bill],
            tx,
            as_of=date(2026, 8, 12),
            period_kind="calendar",
            horizon_days=7,
            arrears_lookback_months=2,
            payment_grace_days=40,
        )
        self.assertEqual(r, 0)

    def test_fuzzy_bill_clear_by_amount_near_due(self):
        from hermes_finance.rules import bill_posted_in_period

        bill = {
            "name": "Spotify",
            "amount_cents": 699,
            "day_of_month": 19,
            "match": r"SPOTIFY",
        }
        # opaque descriptor, exact $6.99 on due day
        txns = [
            Transaction(
                id="x1",
                date="2026-08-19",
                amount_cents=699,
                name="Recurring Withdrawal Debit Card",
                merchant_name=None,
            )
        ]
        self.assertTrue(
            bill_posted_in_period(
                bill,
                txns,
                date(2026, 8, 1),
                date(2026, 8, 31),
                fuzzy=True,
                as_of=date(2026, 8, 19),
            )
        )
        # wrong amount → no clear
        txns2 = [
            Transaction(
                id="x2",
                date="2026-08-19",
                amount_cents=1500,
                name="Something else",
            )
        ]
        self.assertFalse(
            bill_posted_in_period(
                bill,
                txns2,
                date(2026, 8, 1),
                date(2026, 8, 31),
                fuzzy=True,
                as_of=date(2026, 8, 19),
            )
        )

    def test_opaque_us_mobile_two_days_early_clears(self):
        """NorCal MasterMoney $27 on the 3rd is US Mobile (due 5th)."""
        from hermes_finance.rules import bill_posted_in_period, effective_bills_reserve_cents

        bill = {
            "name": "US Mobile",
            "amount_cents": 2700,
            "day_of_month": 5,
            "match": r"US MOBILE",
        }
        tx = [
            Transaction(
                id="um",
                date="2026-08-03",
                amount_cents=2700,
                name="Recurring Withdrawal Debit Card MasterMoney Card",
                merchant_name=None,
            )
        ]
        self.assertTrue(
            bill_posted_in_period(
                bill,
                tx,
                date(2026, 8, 1),
                date(2026, 8, 31),
                fuzzy=True,
                fuzzy_day_slop=2,
                as_of=date(2026, 8, 18),
            )
        )
        self.assertEqual(
            effective_bills_reserve_cents(
                [bill],
                tx,
                as_of=date(2026, 8, 18),
                period_kind="calendar",
                arrears_lookback_months=0,
                fuzzy_day_slop=2,
            ),
            0,
        )

    def test_days_off_pace(self):
        from hermes_finance.rules import days_off_pace

        # day 10/30, committed half hardcap → expected day 15 → +5 days ahead
        d = days_off_pace(50_000, 100_000, days_elapsed=10, days_in_period=30)
        self.assertAlmostEqual(d, 5.0, places=2)

    def test_annual_bill_monthly_reserve(self):
        from hermes_finance.rules import bill_monthly_reserve_cents, safe_to_spend_cents

        self.assertEqual(
            bill_monthly_reserve_cents({"annual_cents": 120_000}),
            10_000,
        )
        # posted this month → reserve cleared
        txns = [
            Transaction(
                id="ins",
                date="2026-07-05",
                amount_cents=8_000,
                name="CSAA INSURANCE",
                merchant_name="CSAA",
                category="Insurance",
            )
        ]
        sts = safe_to_spend_cents(
            100_000,
            20_000,
            bills=[{"name": "CSAA", "amount_cents": 6892, "match": r"CSAA"}],
            txns=txns,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )
        self.assertEqual(sts, 100_000 - 20_000)  # no reserve on top of posted
        # not posted → reserve holds
        sts2 = safe_to_spend_cents(
            100_000,
            20_000,
            bills=[{"name": "CSAA", "amount_cents": 6892, "match": r"CSAA"}],
            txns=[],
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )
        self.assertEqual(sts2, 100_000 - 20_000 - 6892)

    def test_pace_ratio_linear(self):
        # half month half spend => ~1.0
        hardcap = 100_00
        r = pace_ratio(50_00, hardcap, days_elapsed=15, days_in_period=30)
        self.assertAlmostEqual(r, 1.0, places=2)

    def test_pace_v2_precharges_bills(self):
        # day 15/30, spend $0, $100 bill reserved → committed half of hardcap → pace ~1.0
        hardcap = 100_000
        r = pace_ratio(
            0,
            hardcap,
            days_elapsed=15,
            days_in_period=30,
            bills_reserved_cents=50_000,
        )
        self.assertAlmostEqual(r, 1.0, places=2)
        # evaluate path: unposted bill bumps risk even with low spend
        cfg = dict(self.cfg)
        cfg["bill_arrears_lookback_months"] = 0
        cfg["bills"] = [
            {
                "name": "Spotify",
                "amount_cents": 50_000,
                "match": r"SPOTIFY_NEVER",
                "day_of_month": 19,
            }
        ]
        snap = evaluate_budget([], cfg, as_of=date(2026, 8, 15))
        self.assertEqual(snap.bills_reserved_cents, 50_000)
        self.assertEqual(snap.committed_cents, 50_000)
        self.assertGreater(snap.pace_ratio, 0.9)

    def test_anthropic_anomaly(self):
        # Fixture Anthropic $40 is below 2026-08-05 floors ($100 / 4×) — no longer alerts.
        soft = detect_anomalies(
            self.txns, self.cfg, as_of=date(2026, 7, 20), since=date(2026, 7, 19)
        )
        self.assertNotIn("Anthropic", [a.payload.get("merchant") for a in soft])
        # Real spike: $250 software day against cold baseline should still fire.
        spike = self.txns + [
            Transaction(
                id="spike-llm",
                date="2026-07-22",
                amount_cents=25000,
                name="Anthropic",
                merchant_name="Anthropic",
                category="Software & Tools",
            )
        ]
        alerts = detect_anomalies(
            spike, self.cfg, as_of=date(2026, 7, 22), since=date(2026, 7, 21)
        )
        merchants = [a.payload.get("merchant") for a in alerts]
        self.assertIn("Anthropic", merchants)
        anth = next(a for a in alerts if a.payload.get("merchant") == "Anthropic")
        self.assertEqual(anth.kind, "anomaly")
        self.assertGreaterEqual(anth.payload["amount_cents"], 10000)

    def test_budget_breach_alert(self):
        # craft overspend
        heavy = [
            Transaction(
                id="big",
                date="2026-07-10",
                amount_cents=200_000,
                name="Big",
                merchant_name="Big",
                category="Shopping",
            )
        ]
        snap = evaluate_budget(heavy, self.cfg, as_of=date(2026, 7, 15))
        self.assertEqual(snap.risk, "breach")
        alerts = budget_alerts(snap, self.cfg, prev_risk="ok")
        kinds = [a.kind for a in alerts]
        self.assertIn("hardcap_breach", kinds)


class TestBudgetAlertBatch(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(DEFAULT_CONFIG)

    def _firm_snap(self):
        return evaluate_budget(
            [
                Transaction(
                    id="big",
                    date="2026-08-02",
                    amount_cents=70_000,
                    name="Cafe",
                    merchant_name="Cafe",
                    category="Dining Out",
                )
            ],
            self.cfg,
            as_of=date(2026, 8, 16),
        )

    def test_one_new_txn_one_firm_alert(self):
        snap = self._firm_snap()
        self.assertTrue(snap.pace_ratio > 1.0)
        txn = Transaction(
            id="plaid-aaa",
            date="2026-08-16",
            amount_cents=1200,
            name="Cafe Nero",
            merchant_name="Cafe Nero",
        )
        alerts = budget_alerts(
            snap,
            self.cfg,
            prev_risk="ok",
            new_txn_ids=["plaid-aaa"],
            new_txns=[txn],
        )
        pace = [a for a in alerts if a.kind == "pace_warn"]
        self.assertEqual(len(pace), 1)
        self.assertTrue(pace[0].key.endswith("|plaid-aaa"))
        self.assertNotIn("Pertinent", pace[0].body)
        self.assertNotIn("Cafe Nero", pace[0].body)
        self.assertNotIn("New txn:", pace[0].body)
        self.assertNotIn("landed together", pace[0].body)

    def test_dump_collapses_to_one_firm_alert(self):
        snap = self._firm_snap()
        ids = [f"plaid-{i}" for i in range(10)]
        txns = [
            Transaction(
                id=tid,
                date="2026-08-16",
                amount_cents=1000 + i,
                name="MasterMoney",
                merchant_name="MasterMoney",
            )
            for i, tid in enumerate(ids)
        ]
        alerts = budget_alerts(
            snap, self.cfg, prev_risk="ok", new_txn_ids=ids, new_txns=txns
        )
        pace = [a for a in alerts if a.kind == "pace_warn"]
        self.assertEqual(len(pace), 1)
        self.assertNotIn("Pertinent", pace[0].body)
        self.assertNotIn("MasterMoney", pace[0].body)
        self.assertNotIn("landed together", pace[0].body)
        self.assertIn("batch", pace[0].key)
        marked = pace[0].payload.get("also_mark_keys") or []
        self.assertEqual(len(marked), 10)
        self.assertNotIn("New txn:", pace[0].body)

    def test_soft_near_pace_does_not_alert(self):
        # Day 16/31 allotted ~$516; $480 committed is 90–100% of allotted → used to be soft
        snap = evaluate_budget(
            [
                Transaction(
                    id="mid",
                    date="2026-08-02",
                    amount_cents=48_000,
                    name="Cafe",
                    merchant_name="Cafe",
                    category="Dining Out",
                )
            ],
            self.cfg,
            as_of=date(2026, 8, 16),
        )
        txn = Transaction(
            id="plaid-soft",
            date="2026-08-16",
            amount_cents=800,
            name="Cafe Nero",
            merchant_name="Cafe Nero",
        )
        alerts = budget_alerts(
            snap,
            self.cfg,
            prev_risk="ok",
            new_txn_ids=["plaid-soft"],
            new_txns=[txn],
        )
        self.assertEqual([a.kind for a in alerts], [])

    def test_breach_dump_is_one_emergency(self):
        heavy = [
            Transaction(
                id="big",
                date="2026-08-02",
                amount_cents=200_000,
                name="Big",
                merchant_name="Big",
                category="Shopping",
            )
        ]
        snap = evaluate_budget(heavy, self.cfg, as_of=date(2026, 8, 16))
        ids = [f"plaid-{i}" for i in range(5)]
        alerts = budget_alerts(snap, self.cfg, prev_risk="ok", new_txn_ids=ids)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "hardcap_breach")
        self.assertEqual(alerts[0].payload.get("push_priority"), 2)
        self.assertIn("landed together", alerts[0].body)
        self.assertNotIn("Still over", alerts[0].body)

    def test_further_breach_stays_emergency(self):
        heavy = [
            Transaction(
                id="big",
                date="2026-08-02",
                amount_cents=200_000,
                name="Big",
                merchant_name="Big",
                category="Shopping",
            )
        ]
        snap = evaluate_budget(heavy, self.cfg, as_of=date(2026, 8, 16))
        txn = Transaction(
            id="plaid-more",
            date="2026-08-16",
            amount_cents=5000,
            name="Cafe",
            merchant_name="Cafe",
        )
        alerts = budget_alerts(
            snap,
            self.cfg,
            prev_risk="breach",
            new_txn_ids=["plaid-more"],
            new_txns=[txn],
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "hardcap_breach")
        self.assertEqual(alerts[0].payload.get("push_priority"), 2)
        self.assertNotIn("Still over", alerts[0].body)


class TestFixtureFile(unittest.TestCase):
    def test_json_loads(self):
        data = json.loads(FIXTURE.read_text())
        self.assertGreaterEqual(len(data["transactions"]), 5)


if __name__ == "__main__":
    unittest.main()
