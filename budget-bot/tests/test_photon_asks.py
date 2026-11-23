"""Quiet unit tests for inline Photon binary asks (buy queue & interval candidates)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from budget_bot.buy_queue import notify_buy_queue_asks
from budget_bot.config import DEFAULT_CONFIG
from budget_bot.models import Transaction
from budget_bot.photon_asks import (
    ASK_TTL_DAYS,
    enqueue_photon_ask,
    list_pending_photon_asks,
    photon_asks_path,
    resolve_photon_ask,
)
from budget_bot.recurring import (
    load_interval_candidate_doc,
    notify_interval_candidates,
    persist_interval_candidates,
)


class PhotonAsksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.photon_log = self.state / "photon.log"
        self.push_log = self.state / "push.log"
        self.email_log = self.state / "email.log"

        photon_cmd = self.state / "photon.sh"
        push_cmd = self.state / "push.sh"
        email_cmd = self.state / "email.sh"
        photon_cmd.write_text(f"#!/usr/bin/env bash\ncat >> '{self.photon_log}'\nprintf -- '---\\n' >> '{self.photon_log}'\n")
        push_cmd.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> '{self.push_log}'\n")
        email_cmd.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$1\" >> '{self.email_log}'\n")
        for p in (photon_cmd, push_cmd, email_cmd):
            os.chmod(p, 0o755)

        self.old_env = {
            k: os.environ.get(k)
            for k in (
                "BUDGET_BOT_STATE",
                "BUDGET_BOT_PHOTON_CMD",
                "BUDGET_BOT_PUSH_CMD",
                "BUDGET_BOT_NOTIFY_CMD",
            )
        }
        os.environ["BUDGET_BOT_STATE"] = str(self.state)
        os.environ["BUDGET_BOT_PHOTON_CMD"] = str(photon_cmd)
        os.environ["BUDGET_BOT_PUSH_CMD"] = str(push_cmd)
        os.environ["BUDGET_BOT_NOTIFY_CMD"] = str(email_cmd)

    def tearDown(self):
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _write_cfg(self, buy_queue=None, bills=None):
        cfg = dict(DEFAULT_CONFIG)
        cfg["buy_queue"] = buy_queue if buy_queue is not None else []
        cfg["bills"] = bills if bills is not None else []
        (self.state / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
        return cfg

    def test_buy_queue_ask_routes_to_photon_and_yes_clears_no_keeps(self):
        cfg = self._write_cfg(
            buy_queue=[
                {"id": "tint", "name": "Window tinting", "merchant": "", "amount_cents": 50000, "confirm": True, "not_before": "2026-11-01"},
                {"id": "clamp", "name": "CatClamp", "merchant": "Amazon", "amount_cents": 22000, "match": "Amazon", "confirm": True, "not_before": "2026-11-01"},
            ]
        )
        txns = [
            Transaction(id="tx-tint", date="2026-11-20", amount_cents=50000, name="Bay Tint Shop", merchant_name="Bay Tint"),
            Transaction(id="tx-amz", date="2026-11-21", amount_cents=22000, name="Amazon", merchant_name="Amazon"),
        ]
        res = notify_buy_queue_asks(cfg, txns, dry_run=False)
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r["status"] == "sent_photon" for r in res))
        self.assertTrue(self.photon_log.exists())
        self.assertFalse(self.push_log.exists())
        self.assertFalse(self.email_log.exists())

        # Second call dedups without repeating Photon messages.
        res_dup = notify_buy_queue_asks(cfg, txns, dry_run=False)
        self.assertTrue(all(r["status"] == "skipped_dedup" for r in res_dup))

        # Ambiguous bare 'y' refuses cleanly without clearing either item.
        amb = resolve_photon_ask("y")
        self.assertFalse(amb["ok"])
        self.assertEqual(amb["error"], "ambiguous")
        self.assertEqual(len(list_pending_photon_asks()), 2)

        # Look up assigned codes: named-pattern match ("clamp") ranks before amount-only ("tint").
        by_id = {p["item_id"]: p["code"] for p in list_pending_photon_asks()}
        with patch("budget_bot.buy_queue_sync.reconcile_buy_queue", lambda c: c):
            r1 = resolve_photon_ask("yes", by_id["tint"])
            self.assertTrue(r1["ok"])
            self.assertIn("Cleared", r1["message"])
            r2 = resolve_photon_ask("no", by_id["clamp"])
            self.assertTrue(r2["ok"])
            self.assertIn("Left", r2["message"])

        updated_cfg = json.loads((self.state / "config.json").read_text())
        ids = [r["id"] for r in updated_cfg["buy_queue"]]
        self.assertEqual(ids, ["clamp"])
        self.assertEqual(list_pending_photon_asks(), [])

    def test_interval_candidate_photon_enroll_and_dismiss(self):
        self._write_cfg(bills=[])
        from datetime import date

        txns = [
            Transaction(id="y1", date="2026-10-23", amount_cents=3240, name="Yes In My Back Yard"),
            Transaction(id="y2", date="2026-11-23", amount_cents=3240, name="Yes In My Back Yard"),
            Transaction(id="n1", date="2026-09-06", amount_cents=899, name="Netflix"),
            Transaction(id="n2", date="2026-10-05", amount_cents=899, name="Netflix"),
        ]
        persist_interval_candidates(txns, [], as_of=date(2026, 11, 23))
        res = notify_interval_candidates(dry_run=False)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "sent_photon")
        self.assertFalse(self.push_log.exists())
        self.assertFalse(self.email_log.exists())

        pending = list_pending_photon_asks()
        self.assertEqual(len(pending), 2)
        by_id = {p["item_id"]: p["code"] for p in pending}
        yimby_code = by_id["yes-in-my-back-yard-1m-3240"]
        nflx_code = by_id["netflix-1m-899"]

        r_yes = resolve_photon_ask("y", yimby_code)
        self.assertTrue(r_yes["ok"])
        self.assertIn("Added", r_yes["message"])

        r_no = resolve_photon_ask("n", nflx_code)
        self.assertTrue(r_no["ok"])
        self.assertIn("Dismissed", r_no["message"])

        cfg_after = json.loads((self.state / "config.json").read_text())
        self.assertTrue(any(b["name"] == "Yes In My Back Yard" for b in cfg_after["bills"]))
        doc_after = load_interval_candidate_doc()
        self.assertIn("yes-in-my-back-yard-1m-3240", doc_after["enrolled"])
        self.assertIn("netflix-1m-899", doc_after["dismissed"])

    def test_expired_asks_prune_after_ttl(self):
        t0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        enqueue_photon_ask(
            kind="buy_queue_ask",
            key="k1",
            item_id="tint",
            name="Window tinting",
            summary="Window tinting",
            now=t0,
        )
        self.assertEqual(len(list_pending_photon_asks(now=t0 + timedelta(days=ASK_TTL_DAYS - 1))), 1)
        expired_now = t0 + timedelta(days=ASK_TTL_DAYS, seconds=1)
        self.assertEqual(list_pending_photon_asks(now=expired_now), [])
        res = resolve_photon_ask("yes", now=expired_now)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "no_pending")
        self.assertTrue(photon_asks_path().is_file())


if __name__ == "__main__":
    unittest.main()
