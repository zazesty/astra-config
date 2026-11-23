"""Tests for separate monthly sinking-fund reserves ($30/mo glasses, etc.)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from budget_bot.config import DEFAULT_CONFIG
from budget_bot.models import Transaction
from budget_bot.rules import (
    canned_cash_bills_cents,
    effective_bills_reserve_cents,
    eom_leftover_event,
    evaluate_budget,
    evaluate_reserves,
    proposed_subscription_reserve_updates,
    reserve_spend_offset_cents,
    reserves_month_remaining_cents,
    upcoming_unpaid_bills_cents,
)
from budget_bot.run import main as cli_main
from budget_bot.templates import reserves_text


class ReserveTests(unittest.TestCase):
    def setUp(self):
        self.glasses = {
            "id": "glasses",
            "name": "Glasses",
            "monthly_cents": 3000,
            "start_month": "2026-11",
            "match": r"(?i)glasses|eyeglass|optometr|zenni|warby",
        }

    def test_accrues_monthly_from_start_month(self):
        before = evaluate_reserves([self.glasses], [], as_of=date(2026, 10, 31))
        self.assertEqual(before[0]["months_elapsed"], 0)
        self.assertEqual(before[0]["accrued_cents"], 0)
        self.assertEqual(before[0]["month_remaining_cents"], 0)
        self.assertEqual(before[0]["balance_cents"], 0)

        nov = evaluate_reserves([self.glasses], [], as_of=date(2026, 11, 21))
        self.assertEqual(nov[0]["months_elapsed"], 1)
        self.assertEqual(nov[0]["accrued_cents"], 3000)
        self.assertEqual(nov[0]["month_remaining_cents"], 3000)
        self.assertEqual(nov[0]["balance_cents"], 3000)

        jan = evaluate_reserves([self.glasses], [], as_of=date(2027, 1, 15))
        self.assertEqual(jan[0]["months_elapsed"], 3)
        self.assertEqual(jan[0]["accrued_cents"], 9000)
        self.assertEqual(jan[0]["balance_cents"], 9000)

    def test_transfers_never_double_count_or_pollute_spend(self):
        txns = [
            Transaction(
                id="xfer-1",
                date="2026-11-10",
                amount_cents=3000,
                name="Online Transfer to Glasses Savings",
                category="Transfer",
                transfer=True,
            ),
            Transaction(
                id="groceries-1",
                date="2026-11-12",
                amount_cents=10000,
                name="Trader Joe's",
                category="Groceries",
            ),
        ]
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 110_000
        cfg["bills"] = []
        cfg["goals"] = []
        cfg["reserves"] = [self.glasses]

        snap = evaluate_budget(txns, cfg, as_of=date(2026, 11, 21), period_kind="calendar")
        # Transfer ignored: spend is only $100, reserve is $30 once (not $60).
        self.assertEqual(snap.spend_to_date, 10_000)
        self.assertEqual(snap.bills_reserved_cents, 0)
        self.assertEqual(snap.committed_cents, 10_000)
        self.assertEqual(snap.reserves_reserved_cents, 3000)
        self.assertEqual(snap.safe_to_spend_cents, 110_000 - 10_000 - 3000)
        self.assertEqual(snap.reserves[0]["accrued_cents"], 3000)
        self.assertEqual(snap.reserves[0]["spent_cents"], 0)
        self.assertEqual(snap.reserves[0]["balance_cents"], 3000)

        # Even if transfer=False on a transfer-shaped line, reserve matching ignores it.
        unflagged = [
            Transaction(
                id="xfer-2",
                date="2026-11-15",
                amount_cents=3000,
                name="Share Transfer to Glasses Reserve",
                category="Misc / Other",
                transfer=False,
            )
        ]
        res = evaluate_reserves([self.glasses], unflagged, as_of=date(2026, 11, 21))
        self.assertEqual(res[0]["spent_cents"], 0)
        self.assertEqual(res[0]["balance_cents"], 3000)

        ev = eom_leftover_event(snap)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.payload["leftover_cents"], 97_000)

    def test_actual_glasses_spend_draws_reserve_without_double_charging_month(self):
        txns = [
            Transaction(
                id="zenni-1",
                date="2026-12-05",
                amount_cents=12_000,
                name="Zenni Optical",
                merchant_name="Zenni Optical",
                category="Medical",
            ),
        ]
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 110_000
        cfg["bills"] = []
        cfg["goals"] = []
        cfg["reserves"] = [self.glasses]

        snap = evaluate_budget(txns, cfg, as_of=date(2026, 12, 20), period_kind="calendar")
        # Prior accrued ($30 from Nov) offsets $30 of the $120 Zenni purchase.
        self.assertEqual(snap.reserves_offset_cents, 3000)
        self.assertEqual(snap.spend_to_date, 9000)
        # Dec's $30 set-aside is covered by the remaining $90 purchase, so STS is $1100 - $90.
        self.assertEqual(snap.reserves_reserved_cents, 0)
        self.assertEqual(snap.safe_to_spend_cents, 110_000 - 9000)
        # Raw actuals remain intact: 2 months accrued ($60) - $120 spent = -$60 balance.
        self.assertEqual(snap.reserves[0]["accrued_cents"], 6000)
        self.assertEqual(snap.reserves[0]["spent_cents"], 12_000)
        self.assertEqual(snap.reserves[0]["drawn_prior_cents"], 3000)
        self.assertEqual(snap.reserves[0]["balance_cents"], -6000)

    def _base_cfg(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg["hardcap_cents"] = 110_000
        cfg["bills"] = []
        cfg["goals"] = []
        cfg["reserves"] = [self.glasses]
        return cfg

    def test_full_12_month_drawdown_zeroes_double_count(self):
        txns = [
            Transaction(
                id="zenni-full",
                date="2027-10-15",
                amount_cents=36_000,
                name="Zenni Optical",
                merchant_name="Zenni Optical",
                category="Medical",
            )
        ]
        snap = evaluate_budget(txns, self._base_cfg(), as_of=date(2027, 10, 20), period_kind="calendar")
        self.assertEqual(snap.reserves_offset_cents, 33_000)
        self.assertEqual(snap.spend_to_date, 3000)
        self.assertEqual(snap.reserves_reserved_cents, 0)
        self.assertEqual(snap.safe_to_spend_cents, 110_000 - 3000)
        self.assertEqual(snap.reserves[0]["balance_cents"], 0)

    def test_partial_drawdown_keeps_current_month_set_aside(self):
        txns = [
            Transaction(
                id="zenni-partial",
                date="2027-10-15",
                amount_cents=20_000,
                name="Zenni Optical",
                merchant_name="Zenni Optical",
                category="Medical",
            )
        ]
        snap = evaluate_budget(txns, self._base_cfg(), as_of=date(2027, 10, 20), period_kind="calendar")
        self.assertEqual(snap.reserves_offset_cents, 20_000)
        self.assertEqual(snap.spend_to_date, 0)
        self.assertEqual(snap.reserves_reserved_cents, 3000)
        self.assertEqual(snap.safe_to_spend_cents, 110_000 - 3000)
        self.assertEqual(snap.reserves[0]["balance_cents"], 16_000)

    def test_overrun_drawdown_charges_only_excess_above_accrued(self):
        txns = [
            Transaction(
                id="zenni-over",
                date="2027-10-15",
                amount_cents=40_000,
                name="Zenni Optical",
                merchant_name="Zenni Optical",
                category="Medical",
            )
        ]
        snap = evaluate_budget(txns, self._base_cfg(), as_of=date(2027, 10, 20), period_kind="calendar")
        self.assertEqual(snap.reserves_offset_cents, 33_000)
        self.assertEqual(snap.spend_to_date, 7000)
        self.assertEqual(snap.reserves_reserved_cents, 0)
        self.assertEqual(snap.safe_to_spend_cents, 110_000 - 7000)
        self.assertEqual(snap.reserves[0]["balance_cents"], -4000)

    def test_rolling_30d_drawdown_prevents_false_pace_alarm(self):
        txns = [
            Transaction(
                id="zenni-r30",
                date="2027-10-10",
                amount_cents=36_000,
                name="Zenni Optical",
                merchant_name="Zenni Optical",
                category="Medical",
            )
        ]
        snap = evaluate_budget(txns, self._base_cfg(), as_of=date(2027, 10, 15), period_kind="rolling_30d")
        self.assertEqual(snap.reserves_offset_cents, 33_000)
        self.assertEqual(snap.spend_to_date, 3000)
        self.assertEqual(snap.risk, "ok")
        self.assertEqual(
            reserve_spend_offset_cents(
                [self.glasses],
                txns,
                date(2027, 9, 24),
                date(2027, 10, 15),
                date(2027, 10, 15),
            ),
            33_000,
        )

    def test_refund_and_pending_handling(self):
        txns = [
            Transaction(
                id="pending-glasses",
                date="2026-11-20",
                amount_cents=4500,
                name="Warby Parker",
                pending=True,
            ),
            Transaction(
                id="optom-1",
                date="2026-11-18",
                amount_cents=5000,
                name="Local Optometry",
            ),
            Transaction(
                id="optom-refund",
                date="2026-11-19",
                amount_cents=-2000,
                name="Local Optometry Refund",
            ),
        ]
        res = evaluate_reserves([self.glasses], txns, as_of=date(2026, 11, 21))
        self.assertEqual(res[0]["spent_cents"], 3000)
        self.assertEqual(res[0]["balance_cents"], 0)
        self.assertEqual(reserves_month_remaining_cents([self.glasses], txns, date(2026, 11, 21)), 0)

    def test_cli_reserves_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "config.json").write_text(
                json.dumps({"reserves": [self.glasses], "hardcap_cents": 110_000})
            )
            (state / "txns.json").write_text("[]")
            old = os.environ.get("BUDGET_BOT_STATE")
            os.environ["BUDGET_BOT_STATE"] = str(state)
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_main(["reserves", "--as-of", "2026-11-21", "--json"])
                self.assertEqual(rc, 0)
                data = json.loads(buf.getvalue())
                self.assertEqual(data["reserves"][0]["id"], "glasses")
                self.assertEqual(data["reserves"][0]["balance_cents"], 3000)
                self.assertIn("Glasses: $30.00 balance", reserves_text(data["reserves"]))
            finally:
                if old is None:
                    os.environ.pop("BUDGET_BOT_STATE", None)
                else:
                    os.environ["BUDGET_BOT_STATE"] = old

    def _iherb_cash_only_bills(self):
        base = {
            "day_of_month": 20,
            "institution": "alliant",
            "match": r"(?i)\biherb\b",
            "cash_only": True,
            "follow_amount": False,
        }
        return [
            {**base, "name": "iHerb 2mo", "amount_cents": 6100, "interval_months": 2, "anchor": "2026-12"},
            {**base, "name": "iHerb 3mo", "amount_cents": 4700, "interval_months": 3, "anchor": "2026-12"},
            {**base, "name": "iHerb 4mo (Jan)", "amount_cents": 1600, "interval_months": 4, "anchor": "2027-01"},
            {**base, "name": "iHerb 4mo (Mar)", "amount_cents": 3700, "interval_months": 4, "anchor": "2027-03"},
            {**base, "name": "iHerb 6mo", "amount_cents": 4200, "interval_months": 6, "anchor": "2027-03"},
        ]

    def test_cash_only_bills_excluded_from_sts_and_follow_updates(self):
        bills = self._iherb_cash_only_bills()
        self.assertEqual(
            effective_bills_reserve_cents(
                bills,
                [],
                period_start=date(2026, 12, 1),
                period_end=date(2026, 12, 31),
                as_of=date(2026, 12, 18),
                period_kind="calendar",
            ),
            0,
        )
        self.assertEqual(effective_bills_reserve_cents(bills, []), 0)
        txns = [
            Transaction(
                id="ih-dec",
                date="2026-12-20",
                amount_cents=11_800,
                name="Payment to iHerb, LLC",
                institution="alliant",
            )
        ]
        res = proposed_subscription_reserve_updates(bills, txns, as_of=date(2026, 12, 22))
        self.assertEqual(res, {"updates": [], "asks": []})

    def test_cash_only_sibling_pool_for_overlapping_iherb_dues(self):
        bills = self._iherb_cash_only_bills()
        # Dec 18 (within 3d horizon of Dec 20): $61 + $47 = $108
        self.assertEqual(
            upcoming_unpaid_bills_cents(bills, [], date(2026, 12, 18), horizon_days=3),
            10_800,
        )
        # Canned cash-vs-bills surfaces $108 when cash < 2x ($150), hides when >= 2x ($250).
        self.assertEqual(
            canned_cash_bills_cents(
                bills,
                [],
                date(2026, 12, 18),
                hardcap_cents=120_000,
                days_in_period=31,
                cash_cents=15_000,
            ),
            10_800,
        )
        self.assertEqual(
            canned_cash_bills_cents(
                bills,
                [],
                date(2026, 12, 18),
                hardcap_cents=120_000,
                days_in_period=31,
                cash_cents=25_000,
            ),
            0,
        )
        # One combined $118 checkout clears both Dec 20 bills.
        combined = [
            Transaction(
                id="ih-comb",
                date="2026-12-20",
                amount_cents=11_800,
                name="Payment to iHerb, LLC",
                institution="alliant",
            )
        ]
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills, combined, date(2026, 12, 21), horizon_days=3, include_arrears=True
            ),
            0,
        )
        # Only one $61 checkout clears the $61 bill and leaves the $47 bill unpaid.
        one_only = [
            Transaction(
                id="ih-one",
                date="2026-12-20",
                amount_cents=6100,
                name="Payment to iHerb, LLC",
                institution="alliant",
            )
        ]
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills, one_only, date(2026, 12, 21), horizon_days=3, include_arrears=True
            ),
            4700,
        )
        # Mar 20: prior Dec/Feb paid -> $47 + $37 + $42 = $126 due.
        prior_paid = [
            Transaction(id="p-dec", date="2026-12-20", amount_cents=10_800, name="iHerb", institution="alliant"),
            Transaction(id="p-jan", date="2027-01-20", amount_cents=1600, name="iHerb", institution="alliant"),
            Transaction(id="p-feb", date="2027-02-20", amount_cents=6100, name="iHerb", institution="alliant"),
        ]
        self.assertEqual(
            upcoming_unpaid_bills_cents(
                bills, prior_paid, date(2027, 3, 18), horizon_days=3, include_arrears=True
            ),
            12_600,
        )


if __name__ == "__main__":
    unittest.main()
