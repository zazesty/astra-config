#!/usr/bin/env python3
"""Pace push copy — days ahead + committed vs allotted; no merchants."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.rules import BudgetSnapshot
from hermes_finance.templates import (
    anomaly_body,
    anomaly_subject,
    budget_status_text,
    cash_vs_bills_line,
    days_over_cap,
    eom_leftover_body,
    eom_leftover_subject,
    hardcap_body,
    hardcap_subject,
    money_dollars,
    pace_body,
    pace_subject,
    pertinent_txn_line,
)


class TestPaceCopy(unittest.TestCase):
    def _snap(self) -> BudgetSnapshot:
        # Aug 16 / 31 · committed $632 · allotted ~$516 → ~4 days ahead
        return BudgetSnapshot(
            as_of=date(2026, 8, 16),
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            days_in_period=31,
            days_elapsed=16,
            hardcap_cents=100_000,
            spend_to_date=59_762,
            remaining_cents=40_238,
            pct=0.5976,
            pace_ratio=1.2237,
            safe_to_spend_cents=36_839,
            risk="warn",
            bills_reserved_cents=3399,
            committed_cents=63_161,
            days_off_pace=3.58,
        )

    def test_money_dollars_rounds(self):
        self.assertEqual(money_dollars(24_856), "$249")
        self.assertEqual(money_dollars(22_580), "$226")

    def test_subject_days(self):
        self.assertEqual(pace_subject(self._snap()), "Budget Bot: 4 days ahead of pace")

    def test_body_days_only_no_merchant(self):
        body = pace_body(self._snap(), merchants=["Cafe Nero"])
        self.assertEqual(body, "Spend pace is 4 days ahead of pace.\n")
        self.assertNotIn("Pertinent", body)
        self.assertNotIn("Cafe Nero", body)
        self.assertNotIn("% above", body)
        self.assertNotIn("Hardcap", body)
        self.assertNotIn("bills", body)
        self.assertNotIn("allotted", body)
        self.assertNotIn("Committed", body)

    def test_body_no_merchant_when_unknown(self):
        body = pace_body(self._snap())
        self.assertEqual(body, "Spend pace is 4 days ahead of pace.\n")
        self.assertNotIn("Committed", body)
        self.assertNotIn("allotted", body)
        self.assertNotIn("Pertinent txn", body)

    def test_pertinent_collapses_duplicate_names(self):
        self.assertEqual(
            pertinent_txn_line(["MasterMoney"] * 10),
            "Pertinent txn: MasterMoney.",
        )
        self.assertEqual(
            pertinent_txn_line(["Cafe", "iHerb", "Cafe"]),
            "Pertinent txns: Cafe, iHerb.",
        )

    def test_soft_subject_near_pace(self):
        self.assertEqual(pace_subject(self._snap(), soft=True), "Budget Bot: near pace")

    def test_soft_body_matches_firm_shape(self):
        body = pace_body(self._snap(), merchants=["Cafe Nero"], soft=True)
        self.assertEqual(
            body,
            "Spend pace is near allotted. "
            "Committed $632 versus $516 allotted.\n",
        )
        self.assertNotIn("Pertinent", body)
        self.assertNotIn("Cafe Nero", body)
        self.assertNotIn("Hardcap", body)
        self.assertNotIn("Safe-to-spend", body)
        self.assertNotIn("pro-rate", body)

    def test_hardcap_warn_reuses_soft_pace(self):
        self.assertEqual(hardcap_subject(False), "Budget Bot: near pace")
        body = hardcap_body(self._snap(), False, merchants=["Cafe Nero"])
        self.assertEqual(
            body,
            "Spend pace is near allotted. "
            "Committed $632 versus $516 allotted.\n",
        )
        self.assertNotIn("Cafe Nero", body)

    def test_days_over_cap_example_210(self):
        # $210 over $1k in 31d → 6.51 ≈ 7; independent of day-of-month
        d = days_over_cap(121_000, 100_000, 31)
        self.assertAlmostEqual(d, 6.51, places=2)

    def test_hardcap_breach_plain_english(self):
        self.assertEqual(hardcap_subject(True), "Budget Bot: over the monthly cap")
        snap = self._snap()
        snap.spend_to_date = 112_000
        snap.pct = 1.12
        body = hardcap_body(snap, True, merchants=["Big"])
        # 120 over / (1000/31) = 3.72 → 4 days; 112% of cap
        self.assertEqual(
            body,
            "Over the monthly cap. 4 days above. Spent 112% of cap.\n",
        )
        self.assertNotIn("Pertinent", body)
        self.assertNotIn("Big", body)
        self.assertNotIn("Hardcap", body)
        self.assertNotIn("Safe-to-spend", body)
        self.assertNotIn("versus", body)
        self.assertNotIn("ahead of pace", body)

    def test_hardcap_breach_uses_overage_not_pace(self):
        # Day 28/31, $1,210 spend: days_off_pace ≈ 9.5, overage days ≈ 6.51 → 7
        snap = self._snap()
        snap.as_of = date(2026, 8, 28)
        snap.days_elapsed = 28
        snap.spend_to_date = 121_000
        snap.committed_cents = 121_000
        snap.pct = 1.21
        snap.days_off_pace = 9.5
        snap.risk = "breach"
        body = hardcap_body(snap, True)
        self.assertEqual(
            body,
            "Over the monthly cap. 7 days above. Spent 121% of cap.\n",
        )
        self.assertNotIn("9 day", body)
        self.assertNotIn("10 day", body)
        self.assertNotIn("versus", body)
        self.assertNotIn("pace", body)

    def test_leftover_plain_english(self):
        self.assertEqual(eom_leftover_subject(24_856), "Budget Bot: you saved $249")
        body = eom_leftover_body(self._snap(), 24_856, pending_spend_count=2)
        self.assertEqual(
            body,
            "August leftover is $249 saved, well done!\n",
        )
        self.assertNotIn("Wooo", body)
        self.assertNotIn("🎉", body)

    def test_anomaly_plain_english(self):
        self.assertEqual(
            anomaly_subject("Cafe Nero", 14_845),
            "Budget Bot: unusual Cafe Nero $148",
        )
        body = anomaly_body(
            merchant="Cafe Nero",
            amount_cents=14_845,
            category="Dining Out",
            ratio=4.2,
            which="merchant",
            day="2026-08-16",
        )
        self.assertEqual(
            body,
            "Unusual Cafe Nero spend. $148 versus recent baseline (~4.2×). "
            "Intentional?\n",
        )
        self.assertNotIn("Dining Out", body)
        self.assertNotIn(" · ", body)

    def test_budget_status_omits_day_of(self):
        cal = self._snap()
        cal.days_off_pace = -2.0
        cal.safe_to_spend_cents = 29_300
        roll = self._snap()
        roll.period_kind = "rolling_30d"
        roll.days_off_pace = 2.0
        roll.safe_to_spend_cents = 40_800
        text = budget_status_text(cal, roll)
        self.assertEqual(
            text,
            "Overall: on pace · $351 left\n"
            "Calendar: 2 days under pace\n"
            "Rolling: 2 days above pace",
        )
        self.assertNotIn("spent", text)
        self.assertNotIn("$1,000", text)
        self.assertNotIn("warn", text)

    def test_overall_pace_rounds_half_away(self):
        cal = self._snap()
        cal.days_off_pace = -1.0
        cal.safe_to_spend_cents = 50_000
        roll = self._snap()
        roll.days_off_pace = 2.0
        roll.safe_to_spend_cents = 40_000
        text = budget_status_text(cal, roll)
        self.assertIn("Overall: 1 day above pace · $450 left", text)

    def test_budget_status_over_cap_percent_and_days(self):
        cal = self._snap()
        cal.as_of = date(2026, 8, 28)
        cal.days_elapsed = 28
        cal.spend_to_date = 121_000
        cal.committed_cents = 121_000
        cal.pct = 1.21
        cal.days_off_pace = 9.5
        cal.safe_to_spend_cents = -21_000
        cal.risk = "breach"
        roll = self._snap()
        roll.period_kind = "rolling_30d"
        roll.days_off_pace = 2.0
        roll.safe_to_spend_cents = 40_800
        text = budget_status_text(cal, roll)
        self.assertEqual(
            text,
            "Overall: 5 days above · 121% of cap\n"
            "Calendar: 7 days above\n"
            "Rolling: 2 days above pace",
        )
        self.assertNotIn("versus", text)
        self.assertNotIn("9 day", text)
        self.assertNotIn("safe", text)

    def test_overall_averages_line_days_round_up(self):
        # Shown 6 + 15 → 10.5 → 11
        cal = self._snap()
        cal.as_of = date(2026, 8, 28)
        cal.days_elapsed = 28
        cal.spend_to_date = 120_959
        cal.committed_cents = 120_959
        cal.pct = 1.20959
        cal.days_off_pace = 9.5
        cal.safe_to_spend_cents = -20_959
        cal.risk = "breach"
        roll = self._snap()
        roll.period_kind = "rolling_30d"
        roll.days_in_period = 30
        roll.days_elapsed = 16
        roll.spend_to_date = 88_249
        roll.committed_cents = 101_841
        roll.days_off_pace = 14.55
        roll.safe_to_spend_cents = -1_841
        text = budget_status_text(cal, roll)
        self.assertEqual(
            text,
            "Overall: 11 days above · 121% of cap\n"
            "Calendar: 6 days above\n"
            "Rolling: 15 days above pace",
        )

    def test_negative_sts_is_over_by_not_safe_minus(self):
        cal = self._snap()
        cal.days_in_period = 30
        cal.days_elapsed = 2
        cal.hardcap_cents = 105_000
        cal.spend_to_date = 14_900
        cal.committed_cents = 39_415
        cal.days_off_pace = 9.26
        cal.safe_to_spend_cents = 65_585
        roll = self._snap()
        roll.period_kind = "rolling_30d"
        roll.days_in_period = 30
        roll.days_elapsed = 16
        roll.hardcap_cents = 105_000
        roll.spend_to_date = 105_171
        roll.committed_cents = 126_487
        roll.days_off_pace = 20.14
        roll.safe_to_spend_cents = -21_487
        text = budget_status_text(cal, roll)
        self.assertNotIn("safe -", text)
        # mean of +$656 and -$215 → +$220, not the lesser (over by $215)
        self.assertIn("$220 left", text)
        self.assertNotIn("over by $215", text)
        self.assertIn("Rolling: on pace", text)
        self.assertIn("Calendar: 9 days above pace", text)

    def test_cash_vs_bills_silent_when_no_bills(self):
        self.assertEqual(cash_vs_bills_line(8_400, 0), "")
        self.assertEqual(cash_vs_bills_line(None, 7_200), "")
        cal = self._snap()
        roll = self._snap()
        roll.period_kind = "rolling_30d"
        text = budget_status_text(cal, roll, cash_cents=8_400, upcoming_bills_cents=0)
        self.assertEqual(text.count("\n"), 2)
        self.assertNotIn("cash", text)

    def test_cash_vs_bills_terse_ops(self):
        self.assertEqual(cash_vs_bills_line(8_400, 7_200), "$84 cash > $72 bills")
        self.assertEqual(cash_vs_bills_line(5_000, 7_200), "$50 cash < $72 bills")
        self.assertEqual(cash_vs_bills_line(7_200, 7_200), "$72 cash = $72 bills")
        cal = self._snap()
        roll = self._snap()
        roll.period_kind = "rolling_30d"
        text = budget_status_text(
            cal, roll, cash_cents=8_400, upcoming_bills_cents=7_200
        )
        self.assertTrue(text.endswith("$84 cash > $72 bills"))


if __name__ == "__main__":
    unittest.main()
