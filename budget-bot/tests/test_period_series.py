#!/usr/bin/env python3
"""Daily calendar vs rolling series: one row per as_of."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.rules import BudgetSnapshot
from hermes_finance.store import (
    compact_period_snap,
    load_period_series,
    record_period_series,
)


def _snap(*, spend: int, reserved: int, kind: str, as_of: date) -> BudgetSnapshot:
    committed = spend + reserved
    return BudgetSnapshot(
        as_of=as_of,
        period_start=as_of,
        period_end=as_of,
        days_in_period=30,
        days_elapsed=10,
        hardcap_cents=100_000,
        spend_to_date=spend,
        remaining_cents=100_000 - spend,
        pct=spend / 100_000,
        pace_ratio=1.1,
        safe_to_spend_cents=100_000 - committed,
        risk="warn",
        period_kind=kind,
        bills_reserved_cents=reserved,
        committed_cents=committed,
        days_off_pace=1.5,
    )


class TestPeriodSeries(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HERMES_FINANCE_STATE"] = self.tmp.name
        import hermes_finance.config as cfg

        cfg.DEFAULT_STATE_DIR = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_upsert_one_row_per_day(self) -> None:
        d = date(2026, 8, 13)
        record_period_series(
            d,
            _snap(spend=100, reserved=10, kind="calendar", as_of=d),
            _snap(spend=200, reserved=10, kind="rolling_30d", as_of=d),
            source="watch",
        )
        record_period_series(
            d,
            _snap(spend=29906, reserved=4399, kind="calendar", as_of=d),
            _snap(spend=64969, reserved=4399, kind="rolling_30d", as_of=d),
            source="webhook",
        )
        d2 = date(2026, 8, 14)
        record_period_series(
            d2,
            _snap(spend=40000, reserved=4399, kind="calendar", as_of=d2),
            _snap(spend=50000, reserved=4399, kind="rolling_30d", as_of=d2),
            source="webhook",
        )
        rows = load_period_series()
        self.assertEqual([r["as_of"] for r in rows], ["2026-08-13", "2026-08-14"])
        self.assertEqual(rows[0]["source"], "webhook")
        self.assertEqual(rows[0]["calendar"]["spend_to_date"], 29906)
        self.assertEqual(rows[0]["rolling_30d"]["spend_to_date"], 64969)
        self.assertNotIn("top_merchants", rows[0]["calendar"])
        lines = Path(self.tmp.name, "period_series.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        json.loads(lines[0])

    def test_compact_rounds(self) -> None:
        s = _snap(spend=1, reserved=2, kind="calendar", as_of=date(2026, 8, 13))
        c = compact_period_snap(s)
        self.assertEqual(c["bills_reserved_cents"], 2)
        self.assertEqual(c["pace_ratio"], 1.1)


if __name__ == "__main__":
    unittest.main()
