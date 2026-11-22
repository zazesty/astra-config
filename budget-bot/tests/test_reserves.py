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
    eom_leftover_event,
    evaluate_budget,
    evaluate_reserves,
    reserves_month_remaining_cents,
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
        # Actual spend stays visible in spend_to_date ($120).
        self.assertEqual(snap.spend_to_date, 12_000)
        # Dec's $30 set-aside is covered by the $120 glasses purchase, so STS is $1100 - $120.
        self.assertEqual(snap.reserves_reserved_cents, 0)
        self.assertEqual(snap.safe_to_spend_cents, 110_000 - 12_000)
        # 2 months accrued ($60) - $120 spent = -$60 balance.
        self.assertEqual(snap.reserves[0]["accrued_cents"], 6000)
        self.assertEqual(snap.reserves[0]["spent_cents"], 12_000)
        self.assertEqual(snap.reserves[0]["balance_cents"], -6000)

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


if __name__ == "__main__":
    unittest.main()
