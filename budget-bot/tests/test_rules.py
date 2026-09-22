#!/usr/bin/env python3
"""Unit tests for Budget Bot rules (no network)."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.config import DEFAULT_CONFIG
from budget_bot.models import Transaction
from budget_bot.rules import (
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
    cash_bills_alert,
)
from budget_bot.store import load_fixture

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
        # 3-day window (Aug 29 → Sep 1) includes Grok, misses CSAA on the 5th
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                [grok, spot, csaa],
                [],
                date(2026, 8, 29),
                horizon_days=3,
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
        prior = [
            Transaction(id="g-aug", date="2026-08-01", amount_cents=3000, name="GROK"),
            Transaction(id="u-aug", date="2026-08-05", amount_cents=2700, name="US MOBILE"),
            Transaction(id="c-aug", date="2026-08-05", amount_cents=6892, name="CSAA"),
        ]
        # $1050 / 30 / 2 = $17.50 floor → Apple + Hetzner hidden
        as_of = date(2026, 9, 3)
        due = canned_cash_bills_cents(
            bills,
            prior,
            as_of,
            hardcap_cents=105_000,
            days_in_period=30,
            cash_cents=5_000,  # tight → show
        )
        self.assertEqual(due, 3000 + 2700 + 6892)  # Grok Sep arrears + USM + CSAA
        hidden = canned_cash_bills_cents(
            bills,
            prior,
            as_of,
            hardcap_cents=105_000,
            days_in_period=30,
            cash_cents=40_000,  # 2× $125.92 = $252; $400 covers → hide
        )
        self.assertEqual(hidden, 0)
        # 3d window: Sep 1 includes Grok, not yet CSAA/USM (due the 5th)
        grok_only = canned_cash_bills_cents(
            bills,
            prior,
            date(2026, 9, 1),
            hardcap_cents=105_000,
            days_in_period=30,
            cash_cents=5_000,
        )
        self.assertEqual(grok_only, 3000)

    def test_cash_bills_alert_disabled_by_default(self):
        cfg = {
            **DEFAULT_CONFIG,
            "cash_bills_notify": False,
            "bills": [
                {"name": "CSAA", "amount_cents": 6892, "day_of_month": 5, "match": r"CSAA"}
            ],
        }
        ev = cash_bills_alert(
            cfg, [], date(2026, 9, 3), cash_cents=5_000
        )
        self.assertIsNone(ev)

    def test_cash_bills_alert_only_when_short(self):
        cfg = {
            **DEFAULT_CONFIG,
            "cash_bills_notify": True,
            "hardcap_cents": 105_000,
            "bills": [
                {"name": "CSAA", "amount_cents": 6892, "day_of_month": 5, "match": r"CSAA"},
                {"name": "US Mobile", "amount_cents": 2700, "day_of_month": 5, "match": r"US MOBILE"},
            ],
        }
        as_of = date(2026, 9, 3)
        due = 6892 + 2700
        prior = [
            Transaction(id="c-aug", date="2026-08-05", amount_cents=6892, name="CSAA"),
            Transaction(id="u-aug", date="2026-08-05", amount_cents=2700, name="US MOBILE"),
        ]
        short = cash_bills_alert(cfg, prior, as_of, cash_cents=5_000)
        self.assertIsNotNone(short)
        assert short is not None
        self.assertEqual(short.kind, "cash_short")
        self.assertEqual(short.key, "cash_short|2026-09-03")
        self.assertIn("<", short.subject)
        # 3d 200% both: between 100% and 200% still pages
        mid = cash_bills_alert(cfg, prior, as_of, cash_cents=due + 100)
        self.assertIsNotNone(mid)
        covered = cash_bills_alert(cfg, prior, as_of, cash_cents=int(round(2 * due)) + 100)
        self.assertIsNone(covered)
        plenty = cash_bills_alert(cfg, prior, as_of, cash_cents=50_000)
        self.assertIsNone(plenty)

    def test_cash_bills_stays_until_named_post(self):
        csaa = {"name": "CSAA", "amount_cents": 6892, "day_of_month": 5, "match": r"CSAA"}
        grok = {"name": "Grok", "amount_cents": 3000, "day_of_month": 1, "match": r"GROK"}
        bills = [csaa, grok]
        kwargs = dict(hardcap_cents=105_000, days_in_period=30, cash_cents=5_000)
        prior = [
            Transaction(id="g-aug", date="2026-08-01", amount_cents=3000, name="GROK"),
            Transaction(id="c-aug", date="2026-08-05", amount_cents=6892, name="CSAA"),
        ]
        # 3d start: Sep 1 is Grok only, not yet CSAA
        self.assertEqual(
            canned_cash_bills_cents(bills, prior, date(2026, 9, 1), **kwargs),
            3000,
        )
        # morning after due, still unpaid → stay alive
        self.assertEqual(
            canned_cash_bills_cents(bills, prior, date(2026, 9, 6), **kwargs),
            3000 + 6892,
        )
        posted = prior + [
            Transaction(
                id="csaa-sep8",
                date="2026-09-08",
                amount_cents=7009,
                name="Withdrawal CSAA INSURANCE",
            )
        ]
        # named post clears; Grok still unpaid arrears
        self.assertEqual(
            canned_cash_bills_cents(bills, posted, date(2026, 9, 9), **kwargs),
            3000,
        )
        both = posted + [
            Transaction(
                id="grok-sep1",
                date="2026-09-01",
                amount_cents=3000,
                name="GROK XAI",
            )
        ]
        self.assertEqual(
            canned_cash_bills_cents(bills, both, date(2026, 9, 9), **kwargs),
            0,
        )
        # grace cap: Sep 5 due is gone by Oct 16; Oct 5 still unpaid arrears
        self.assertEqual(
            canned_cash_bills_cents([csaa], [], date(2026, 10, 16), **kwargs),
            6892,
        )

    def test_cash_bills_alliant_empty_without_alliant_bills_is_silent(self):
        cfg = {
            **DEFAULT_CONFIG,
            "cash_bills_notify": True,
            "hardcap_cents": 105_000,
            "bills": [
                {"name": "CSAA", "amount_cents": 6892, "day_of_month": 5, "match": r"CSAA"}
            ],
        }
        snap = {
            "items": [
                {
                    "institution": "alliant-credit-union",
                    "accounts": [
                        {
                            "name": "Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": 0,
                        }
                    ],
                },
                {
                    "institution": "1st-northern-california-credit-union",
                    "accounts": [
                        {
                            "name": "Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": 50_000,
                        }
                    ],
                },
            ]
        }
        ev = cash_bills_alert(cfg, [], date(2026, 9, 3), balances=snap)
        self.assertIsNone(ev)

    def test_cash_bills_either_cu_short_alerts(self):
        cfg = {
            **DEFAULT_CONFIG,
            "cash_bills_notify": True,
            "hardcap_cents": 105_000,
            "bills": [
                {
                    "name": "CSAA",
                    "amount_cents": 6892,
                    "day_of_month": 5,
                    "match": r"CSAA",
                    "institution": "norcal",
                },
                {
                    "name": "Something Alliant",
                    "amount_cents": 3000,
                    "day_of_month": 5,
                    "match": r"ALLIANTBILL",
                    "institution": "alliant",
                },
            ],
        }
        snap = {
            "items": [
                {
                    "institution": "alliant-credit-union",
                    "accounts": [
                        {
                            "name": "Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": 500,
                        }
                    ],
                },
                {
                    "institution": "1st-northern-california-credit-union",
                    "accounts": [
                        {
                            "name": "Checking",
                            "type": "depository",
                            "subtype": "checking",
                            "available_cents": 50_000,
                        }
                    ],
                },
            ]
        }
        ev = cash_bills_alert(cfg, [], date(2026, 9, 3), balances=snap)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertIn("Alliant", ev.subject)
        self.assertNotIn("NorCal", ev.subject)

    def test_active_from_skips_dues_before_start(self):
        from budget_bot.rules import bill_due_dates_in_range

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
        from budget_bot.rules import effective_bills_reserve_cents

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
        from budget_bot.rules import effective_bills_reserve_cents

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
        from budget_bot.rules import effective_bills_reserve_cents

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
        from budget_bot.rules import effective_bills_reserve_cents

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
        from budget_bot.rules import effective_bills_reserve_cents

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
        from budget_bot.rules import bill_posted_in_period

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
        from budget_bot.rules import bill_posted_in_period, effective_bills_reserve_cents

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

    def test_saas_tax_inclusive_opaque_clears(self):
        """CA SaaS tax ~10%: opaque SuperGrok $33 still clears the $30 reserve."""
        from budget_bot.rules import (
            amount_matches_bill,
            bill_amount_band,
            bill_posted_in_period,
            effective_bills_reserve_cents,
        )

        bill = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
        }
        under, over = bill_amount_band(bill)
        self.assertEqual(under, 100)
        self.assertEqual(over, 400)  # $1 + 10% of $30
        self.assertTrue(amount_matches_bill(3300, 3000, under, over))
        self.assertTrue(amount_matches_bill(3308, 3000, under, over))  # 10.25%
        self.assertFalse(amount_matches_bill(2700, 3000, under, over))  # US Mobile

        tx = [
            Transaction(
                id="grok-tax",
                date="2026-01-01",
                amount_cents=3300,
                name="Recurring Withdrawal Debit Card MasterMoney Card",
                merchant_name=None,
            )
        ]
        self.assertTrue(
            bill_posted_in_period(
                bill,
                tx,
                date(2026, 1, 1),
                date(2026, 1, 31),
                fuzzy=True,
                as_of=date(2026, 1, 2),
            )
        )
        self.assertEqual(
            effective_bills_reserve_cents(
                [bill],
                tx,
                as_of=date(2026, 1, 2),
                period_kind="calendar",
                arrears_lookback_months=0,
            ),
            0,
        )
        # without saas flag, $33 is outside ±$1
        plain = {**bill, "saas": False}
        self.assertFalse(
            bill_posted_in_period(
                plain,
                tx,
                date(2026, 1, 1),
                date(2026, 1, 31),
                fuzzy=True,
                as_of=date(2026, 1, 2),
            )
        )

    def test_saas_tax_does_not_steal_us_mobile(self):
        """$27 opaque on the 5th is US Mobile, not a discounted SuperGrok."""
        from budget_bot.rules import bill_posted_in_period, effective_bills_reserve_cents

        grok = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
        }
        usm = {
            "name": "US Mobile",
            "amount_cents": 2700,
            "day_of_month": 5,
            "match": r"US MOBILE",
        }
        tx = [
            Transaction(
                id="um",
                date="2026-01-05",
                amount_cents=2700,
                name="Recurring Withdrawal Debit Card MasterMoney Card",
                merchant_name=None,
            )
        ]
        self.assertFalse(
            bill_posted_in_period(
                grok,
                tx,
                date(2026, 1, 1),
                date(2026, 1, 31),
                fuzzy=True,
                as_of=date(2026, 1, 5),
            )
        )
        self.assertTrue(
            bill_posted_in_period(
                usm,
                tx,
                date(2026, 1, 1),
                date(2026, 1, 31),
                fuzzy=True,
                as_of=date(2026, 1, 5),
            )
        )
        self.assertEqual(
            effective_bills_reserve_cents(
                [grok, usm],
                tx,
                as_of=date(2026, 1, 5),
                period_kind="calendar",
                arrears_lookback_months=0,
            ),
            3000,  # Grok still reserved; US Mobile cleared
        )

    def test_named_saas_tax_inclusive_still_clears(self):
        """Name-matched SuperGrok $33 already credited in full; saas flag is extra."""
        from budget_bot.rules import bill_posted_in_period

        bill = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
        }
        tx = [
            Transaction(
                id="named",
                date="2026-01-01",
                amount_cents=3300,
                name="XAI SuperGrok",
                merchant_name="X.AI",
            )
        ]
        self.assertTrue(
            bill_posted_in_period(
                bill,
                tx,
                date(2026, 1, 1),
                date(2026, 1, 31),
                fuzzy=True,
                as_of=date(2026, 1, 2),
            )
        )

    def test_saas_reserve_follows_tax_inclusive_post(self):
        """Opaque $33 SuperGrok rewrites the $30 reserve; $15 usage does not."""
        from budget_bot.rules import (
            amount_matches_bill,
            apply_saas_reserve_updates_to_bills,
            bill_posted_in_period,
            proposed_saas_reserve_updates,
        )

        grok = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
        }
        # After the bump, a pre-tax $30 still matches the $33 reserve.
        self.assertTrue(
            amount_matches_bill(3000, 3300, 100, 430, tax_pct=0.10)
        )
        self.assertFalse(
            amount_matches_bill(2700, 3300, 100, 430, tax_pct=0.10)
        )

        tax = Transaction(
            id="grok-tax",
            date="2027-01-01",
            amount_cents=3300,
            name="Recurring Withdrawal Debit Card MasterMoney Card",
            merchant_name=None,
        )
        usage = Transaction(
            id="grok-use",
            date="2027-01-04",
            amount_cents=1500,
            name="XAI GROK",
            merchant_name="X.AI",
        )
        updates = proposed_saas_reserve_updates(
            [grok],
            [tax, usage],
            as_of=date(2027, 1, 4),
        )
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["new_cents"], 3300)
        self.assertEqual(updates[0]["old_cents"], 3000)
        n = apply_saas_reserve_updates_to_bills([grok], updates)
        self.assertEqual(n, 1)
        self.assertEqual(grok["amount_cents"], 3300)
        self.assertTrue(
            bill_posted_in_period(
                grok,
                [tax],
                date(2027, 1, 1),
                date(2027, 1, 31),
                fuzzy=True,
                as_of=date(2027, 1, 4),
            )
        )
        # same $33 next month: no further rewrite
        self.assertEqual(
            proposed_saas_reserve_updates(
                [grok],
                [
                    tax,
                    Transaction(
                        id="grok-tax-2",
                        date="2027-02-01",
                        amount_cents=3300,
                        name="Recurring Withdrawal Debit Card MasterMoney Card",
                    ),
                ],
                as_of=date(2027, 2, 2),
            ),
            [],
        )

    def test_saas_reserve_ignores_unrelated_opaque(self):
        from budget_bot.rules import proposed_saas_reserve_updates

        grok = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
        }
        tx = [
            Transaction(
                id="um",
                date="2027-01-05",
                amount_cents=2700,
                name="Recurring Withdrawal Debit Card MasterMoney Card",
            )
        ]
        self.assertEqual(
            proposed_saas_reserve_updates([grok], tx, as_of=date(2027, 1, 5)),
            [],
        )

    def test_days_off_pace(self):
        from budget_bot.rules import days_off_pace

        # day 10/30, committed half hardcap → expected day 15 → +5 days ahead
        d = days_off_pace(50_000, 100_000, days_elapsed=10, days_in_period=30)
        self.assertAlmostEqual(d, 5.0, places=2)

    def test_annual_bill_monthly_reserve(self):
        from budget_bot.rules import bill_monthly_reserve_cents, safe_to_spend_cents

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


class TestAnnualCadence(unittest.TestCase):
    nssi = {
        "name": "NSSI personal property",
        "cadence": "annual",
        "annual_cents": 10200,
        "month": 2,
        "day_of_month": 12,
        "match": r"NATLSTDNTSERV",
    }
    renters = {
        "name": "CSAA Renters",
        "cadence": "annual",
        "annual_cents": 11573,
        "month": 2,
        "day_of_month": 12,
        "match": r"CSAA",
    }
    auto = {
        "name": "CSAA Insurance",
        "amount_cents": 6892,
        "day_of_month": 5,
        "match": r"CSAA",
    }
    spot = {
        "name": "Spotify",
        "amount_cents": 699,
        "day_of_month": 19,
        "match": r"SPOTIFY",
        "saas": True,
    }

    def test_due_only_on_anniversary(self):
        from budget_bot.rules import bill_due_dates_in_range

        dues = bill_due_dates_in_range(
            self.nssi, date(2026, 1, 1), date(2026, 12, 31)
        )
        self.assertEqual(dues, [date(2026, 2, 12)])

    def test_sinking_fund_not_arrears_stack(self):
        bills = [self.nssi]
        # September, Feb $102 already in ledger — reserve 1/12 once, not 7×
        feb = [
            Transaction(
                id="nssi",
                date="2026-02-12",
                amount_cents=10200,
                name="NATLSTDNTSERVINSURANCE",
                merchant_name="NATLSTDNTSERVINSURANCE",
            )
        ]
        sep = effective_bills_reserve_cents(
            bills,
            feb,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            as_of=date(2026, 9, 5),
            period_kind="calendar",
            arrears_lookback_months=6,
        )
        self.assertEqual(sep, 850)
        empty = effective_bills_reserve_cents(
            bills,
            [],
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            as_of=date(2026, 9, 5),
            period_kind="calendar",
            arrears_lookback_months=0,
        )
        self.assertEqual(empty, 850)

    def test_charge_month_clears_reserve_and_amortizes_spend(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 105_000
        cfg["bills"] = [self.nssi]
        cfg["bill_arrears_lookback_months"] = 0
        tx = [
            Transaction(
                id="nssi",
                date="2026-02-12",
                amount_cents=10200,
                name="NATLSTDNTSERVINSURANCE",
                merchant_name="NATLSTDNTSERVINSURANCE",
            )
        ]
        snap = evaluate_budget(tx, cfg, as_of=date(2026, 2, 12))
        self.assertEqual(snap.spend_to_date, 850)
        self.assertEqual(snap.bills_reserved_cents, 0)
        self.assertLess(snap.spend_to_date, 105_000)
        self.assertNotEqual(snap.risk, "breach")

    def test_us_mobile_insurance_annual_amortizes(self):
        ins = {
            "name": "US Mobile insurance",
            "cadence": "annual",
            "annual_cents": 7500,
            "month": 8,
            "day_of_month": 13,
            "match": r"US MOBILE",
        }
        phone = {
            "name": "US Mobile",
            "amount_cents": 2700,
            "auto_annual": True,
            "day_of_month": 5,
            "match": r"US MOBILE",
        }
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 105_000
        cfg["bills"] = [ins, phone]
        cfg["bill_arrears_lookback_months"] = 0
        tx = [
            Transaction(
                id="usm-ins",
                date="2026-08-13",
                amount_cents=7500,
                name="US MOBILE 295 MADISON AVE FL 6 NEW YORK NY",
                merchant_name="US MOBILE",
                category="Utilities & Phone",
            )
        ]
        snap = evaluate_budget(tx, cfg, as_of=date(2026, 8, 13))
        self.assertEqual(snap.spend_to_date, 625)  # 7500/12
        # $75 is not ~10× of $27, so the service bill stays monthly
        from budget_bot.rules import proposed_auto_annual_conversions

        self.assertEqual(
            proposed_auto_annual_conversions(
                [phone], tx, as_of=date(2026, 8, 13)
            ),
            [],
        )

    def test_nonrenew_ends_twelve_months_after_last_post(self):
        from budget_bot.rules import bill_due_dates_in_range, bill_nonrenew_end

        nssi = {**self.nssi, "renews": False}
        feb = [
            Transaction(
                id="nssi",
                date="2026-02-12",
                amount_cents=10200,
                name="NATLSTDNTSERVINSURANCE",
                merchant_name="NATLSTDNTSERVINSURANCE",
            )
        ]
        self.assertEqual(bill_nonrenew_end(nssi, feb), date(2027, 2, 12))
        # The paid anniversary stays; the renewal date does not.
        self.assertEqual(
            bill_due_dates_in_range(nssi, date(2026, 1, 1), date(2027, 12, 31), feb),
            [date(2026, 2, 12)],
        )
        # No ledger post yet → do not invent an end.
        self.assertEqual(
            bill_due_dates_in_range(
                nssi, date(2027, 2, 1), date(2027, 2, 28), []
            ),
            [date(2027, 2, 12)],
        )
        # 1/12 through January 2027; gone once the renewal month starts.
        self.assertEqual(
            effective_bills_reserve_cents(
                [nssi],
                feb,
                period_start=date(2027, 1, 1),
                period_end=date(2027, 1, 31),
                as_of=date(2027, 1, 15),
                period_kind="calendar",
            ),
            850,
        )
        self.assertEqual(
            effective_bills_reserve_cents(
                [nssi],
                feb,
                period_start=date(2027, 2, 1),
                period_end=date(2027, 2, 28),
                as_of=date(2027, 2, 9),
                period_kind="calendar",
            ),
            0,
        )
        # Cash-vs-bills does not expect the renewal lump.
        self.assertEqual(
            canned_cash_bills_cents(
                [nssi],
                feb,
                date(2027, 2, 9),
                hardcap_cents=105_000,
                days_in_period=28,
                cash_cents=5_000,
            ),
            0,
        )

    def test_cash_vs_bills_sums_monthly_and_annual_in_one_window(self):
        # Feb 9: January auto already posted; February auto still unpaid (due the
        # 5th, inside the stay-alive grace) plus the renters lump on the 12th.
        prior = [
            Transaction(
                id="auto-jan",
                date="2026-01-05",
                amount_cents=6892,
                name="CSAA INSURANCE",
                merchant_name="CSAA INSURANCE",
            )
        ]
        shown = canned_cash_bills_cents(
            [self.auto, self.renters],
            prior,
            date(2026, 2, 9),
            hardcap_cents=105_000,
            days_in_period=28,
            cash_cents=5_000,
        )
        self.assertEqual(shown, 6892 + 11573)

    def test_cash_vs_bills_uses_cash_pull_in_window(self):
        bills = [self.nssi, self.renters, self.spot]
        # 5d before anniversary: full lumps, not 1/12; Spotify not in window
        due = upcoming_unpaid_bills_cents(
            bills, [], date(2026, 2, 8), horizon_days=5
        )
        self.assertEqual(due, 10200 + 11573)
        # September: nothing posting
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills, [], date(2026, 9, 5), horizon_days=5
            ),
            0,
        )
        # Material floor would hide 1/12 ($8.50); cash pull stays visible
        # 3d window: as_of Feb 9 → Feb 12 anniversary still in range
        shown = canned_cash_bills_cents(
            bills,
            [],
            date(2026, 2, 9),
            hardcap_cents=105_000,
            days_in_period=28,
            cash_cents=5_000,
        )
        self.assertEqual(shown, 10200 + 11573)

    def test_csaa_auto_and_renters_do_not_cross_clear(self):
        bills = [self.auto, self.renters]
        both = [
            Transaction(
                id="auto",
                date="2026-02-05",
                amount_cents=6892,
                name="CSAA INSURANCE",
                merchant_name="CSAA INSURANCE",
            ),
            Transaction(
                id="rent",
                date="2026-02-12",
                amount_cents=11573,
                name="CSAA INSURANCE",
                merchant_name="CSAA INSURANCE",
            ),
        ]
        # Auto due Feb 5 is before as_of Feb 8; renters due Feb 12 in 5d window
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills, both, date(2026, 2, 8), horizon_days=5
            ),
            0,
        )
        # $115.73 alone must not clear the monthly auto due
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills,
                [both[1]],
                date(2026, 2, 3),
                horizon_days=5,
            ),
            6892,
        )
        # $68.92 alone must not clear renters
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills,
                [both[0]],
                date(2026, 2, 8),
                horizon_days=5,
            ),
            11573,
        )

    def test_spotify_stays_monthly(self):
        from budget_bot.rules import bill_due_dates_in_range, bill_is_annual

        self.assertFalse(bill_is_annual(self.spot))
        dues = bill_due_dates_in_range(
            self.spot, date(2026, 1, 1), date(2026, 3, 31)
        )
        self.assertEqual(
            dues, [date(2026, 1, 19), date(2026, 2, 19), date(2026, 3, 19)]
        )

    def test_annual_post_skips_anomaly(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg["bills"] = [self.nssi]
        tx = [
            Transaction(
                id="nssi",
                date="2026-02-12",
                amount_cents=10200,
                name="NATLSTDNTSERVINSURANCE",
                merchant_name="NATLSTDNTSERVINSURANCE",
                category="Insurance",
            )
        ]
        alerts = detect_anomalies(tx, cfg, as_of=date(2026, 2, 12), since=date(2026, 2, 11))
        self.assertEqual(alerts, [])

    def test_auto_annual_named_10x_converts_and_amortizes(self):
        from budget_bot.rules import (
            apply_auto_annual_conversions_to_bills,
            bill_is_annual,
            proposed_auto_annual_conversions,
        )

        usm = {
            "name": "US Mobile",
            "amount_cents": 2700,
            "day_of_month": 5,
            "match": r"US MOBILE",
            "auto_annual": True,
        }
        grok = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
            "auto_annual": True,
        }
        spot = dict(self.spot)
        tx = [
            Transaction(
                id="usm-yr",
                date="2026-09-05",
                amount_cents=28000,
                name="US MOBILE",
                merchant_name="US MOBILE",
            ),
            Transaction(
                id="grok-yr",
                date="2026-09-01",
                amount_cents=30000,
                name="GROK XAI",
                merchant_name="GROK XAI",
            ),
        ]
        updates = proposed_auto_annual_conversions(
            [usm, grok, spot], tx, as_of=date(2026, 9, 5)
        )
        names = {u["name"]: u["new_cents"] for u in updates}
        self.assertEqual(names["US Mobile"], 28000)
        self.assertEqual(names["Grok / xAI"], 30000)
        self.assertNotIn("Spotify", names)
        n = apply_auto_annual_conversions_to_bills([usm, grok, spot], updates)
        self.assertEqual(n, 2)
        self.assertTrue(bill_is_annual(usm))
        self.assertTrue(bill_is_annual(grok))
        self.assertFalse(bill_is_annual(spot))
        self.assertEqual(usm["annual_cents"], 28000)
        self.assertEqual(usm["month"], 9)
        self.assertEqual(usm["day_of_month"], 5)
        cfg = dict(DEFAULT_CONFIG)
        cfg["bills"] = [usm, grok, spot]
        cfg["bill_arrears_lookback_months"] = 0
        snap = evaluate_budget(tx, cfg, as_of=date(2026, 9, 5))
        # 28000/12=2333, 30000/12=2500
        self.assertEqual(snap.spend_to_date, 2333 + 2500)
        self.assertEqual(snap.bills_reserved_cents, 699)  # Spotify only (due 19th)

    def test_auto_annual_opaque_prefers_closer_due(self):
        from budget_bot.rules import proposed_auto_annual_conversions

        usm = {
            "name": "US Mobile",
            "amount_cents": 2700,
            "day_of_month": 5,
            "match": r"US MOBILE",
            "auto_annual": True,
        }
        grok = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
            "auto_annual": True,
        }
        opaque_usm = Transaction(
            id="opaque-280",
            date="2026-09-05",
            amount_cents=28000,
            name="Recurring Withdrawal Debit Card MasterMoney Card",
        )
        opaque_grok = Transaction(
            id="opaque-300",
            date="2026-09-01",
            amount_cents=30000,
            name="Recurring Withdrawal Debit Card MasterMoney Card",
        )
        u = proposed_auto_annual_conversions(
            [usm, grok], [opaque_usm], as_of=date(2026, 9, 5)
        )
        self.assertEqual(len(u), 1)
        self.assertEqual(u[0]["name"], "US Mobile")
        g = proposed_auto_annual_conversions(
            [usm, grok], [opaque_grok], as_of=date(2026, 9, 1)
        )
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["name"], "Grok / xAI")

    def test_auto_annual_ignores_tax_and_usage_and_untagged(self):
        from budget_bot.rules import proposed_auto_annual_conversions

        grok = {
            "name": "Grok / xAI",
            "amount_cents": 3000,
            "day_of_month": 1,
            "match": r"GROK|\bXAI\b",
            "saas": True,
            "auto_annual": True,
        }
        apple = {
            "name": "Apple",
            "amount_cents": 99,
            "day_of_month": 6,
            "match": r"APPLE",
            "saas": True,
            # no auto_annual — $9.99 is ~10× of $0.99
        }
        tx = [
            Transaction(
                id="tax",
                date="2026-09-01",
                amount_cents=3300,
                name="GROK XAI",
            ),
            Transaction(
                id="use",
                date="2026-09-01",
                amount_cents=1500,
                name="GROK XAI",
            ),
            Transaction(
                id="icloud",
                date="2026-09-06",
                amount_cents=999,
                name="APPLE.COM/BILL",
            ),
        ]
        self.assertEqual(
            proposed_auto_annual_conversions(
                [grok, apple], tx, as_of=date(2026, 9, 6)
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
