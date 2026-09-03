#!/usr/bin/env python3
"""Same-sync Pushover coalesce (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.models import AlertEvent
from hermes_finance.notify import _push_body, coalesce_push_events


def _ev(kind: str, key: str, subject: str, body: str, pri: int = 1, **payload) -> AlertEvent:
    return AlertEvent(
        kind=kind,  # type: ignore[arg-type]
        subject=subject,
        body=body,
        key=key,
        payload={"push_priority": pri, **payload},
    )


class TestCoalesce(unittest.TestCase):
    def test_dedupes_identical_pace_copy(self):
        events = [
            _ev(
                "pace_warn",
                f"pace_firm|2026-08|plaid-{i}",
                "Budget Bot: 4 days ahead of pace",
                "Spend pace is 4 days ahead of pace.\n",
                txn_id=f"plaid-{i}",
            )
            for i in range(10)
        ]
        events.append(
            _ev(
                "anomaly",
                "anomaly|2026-08|Cafe",
                "Budget Bot: unusual Cafe $148",
                "Unusual Cafe spend. $148 versus recent baseline (~4.2×). "
                "Intentional?\n",
                pri=0,
            )
        )
        combined = coalesce_push_events(events)
        self.assertEqual(combined.kind, "pace_warn")
        self.assertEqual(combined.subject, "Budget Bot: 4 days ahead of pace")
        self.assertEqual(combined.body.count("4 days ahead of pace"), 1)
        self.assertIn("Unusual Cafe spend", combined.body)
        self.assertIn("$148 versus recent baseline", combined.body)
        self.assertNotIn("Pertinent txn", combined.body)
        self.assertNotIn("MasterMoney", combined.body)
        self.assertNotIn("New txn:", combined.body)
        self.assertEqual(combined.payload["coalesced_n"], 11)
        self.assertGreaterEqual(len(combined.payload["also_mark_keys"]), 11)

    def test_push_body_omits_merchant(self):
        ev = _ev(
            "pace_warn",
            "pace_firm|2026-08|batch|a",
            "Budget Bot: 4 days ahead of pace",
            "Spend pace is 4 days ahead of pace.\n",
        )
        body = _push_body(ev)
        self.assertEqual(body, "Spend pace is 4 days ahead of pace.")
        self.assertNotIn("Pertinent", body)
        self.assertIn("4 days ahead of pace", body)


class TestSyncCodes(unittest.TestCase):
    def test_ignores_legacy_default_update(self):
        from hermes_finance.plaid_webhook import SYNC_CODES

        self.assertIn("SYNC_UPDATES_AVAILABLE", SYNC_CODES)
        self.assertIn("TRANSACTIONS_REMOVED", SYNC_CODES)
        self.assertNotIn("DEFAULT_UPDATE", SYNC_CODES)
        self.assertNotIn("INITIAL_UPDATE", SYNC_CODES)
        self.assertNotIn("HISTORICAL_UPDATE", SYNC_CODES)


if __name__ == "__main__":
    unittest.main()
