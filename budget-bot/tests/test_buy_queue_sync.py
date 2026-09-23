#!/usr/bin/env python3
"""Astra table and config.buy_queue reconcile both ways."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.buy_queue_sync import reconcile_buy_queue

FACT = """---
id: budget-bot-buy-queue
name: budget-bot-buy-queue
version: 4
updated: 2026-09-22T00:00:00.000Z
---
### Order now

| Rank | Item | ~$ | Merchant (auto-pull) | Notes |
|------|------|----|----------------------|-------|
| 1 | Maro toe socks 6pk | 90 | Maro | id:maro-toe-socks. 3 natural |
| 2 | Rice cooker | 100 | Costco | id:rice-cooker. juicy rice |

**Seeded matcher rows (box config, reconciled each pass):** stale.
"""

BUDGET = """---
id: budget-bot
name: budget-bot
version: 2
updated: 2026-09-22T00:00:00.000Z
---
**Seeded matcher rows (box config, reconciled each pass):** stale.
"""

QUEUE = [
    {
        "amount_cents": 9000,
        "id": "maro-toe-socks",
        "match": "MARO|TOE.?SOCK",
        "name": "Maro toe socks 6pk",
    },
    {
        "amount_cents": 10000,
        "id": "rice-cooker",
        "match": r"COSTCO WHSE|WWW\s*COSTCO|COSTCO\.COM",
        "not_before": "2026-09-22",
        "name": "Rice cooker",
    },
]


class TestReconcile(unittest.TestCase):
    def _run(self, root: Path, cfg: dict) -> dict:
        with patch("budget_bot.buy_queue_sync.state_dir", lambda: root), patch(
            "budget_bot.config.config_path", lambda: root / "config.json"
        ):
            return reconcile_buy_queue(
                cfg,
                queue_fact=root / "queue.md",
                budget_fact=root / "budget.md",
                regen=False,
            )

    def test_first_pass_stamps_costco_ask_and_keeps_regex(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "queue.md").write_text(FACT)
            (root / "budget.md").write_text(BUDGET)
            (root / "config.json").write_text(json.dumps({"buy_queue": QUEUE, "hardcap_cents": 1}))
            cfg = {"buy_queue": [dict(r) for r in QUEUE], "hardcap_cents": 1}
            out = self._run(root, cfg)
            rice = next(r for r in out["buy_queue"] if r["id"] == "rice-cooker")
            maro = next(r for r in out["buy_queue"] if r["id"] == "maro-toe-socks")
            self.assertTrue(rice["confirm"])
            self.assertEqual(rice["merchant"], "Costco")
            self.assertIn("COSTCO WHSE", rice["match"])
            self.assertEqual(rice["not_before"], "2026-09-22")
            self.assertFalse(maro["confirm"])
            self.assertEqual(maro["match"], "MARO|TOE.?SOCK")
            # Prose stays put on the stamp pass.
            self.assertIn("juicy rice", (root / "queue.md").read_text())
            again = self._run(root, json.loads((root / "config.json").read_text()))
            self.assertEqual(again["buy_queue"], out["buy_queue"])

    def test_table_price_edit_keeps_regex(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "queue.md").write_text(FACT)
            (root / "budget.md").write_text(BUDGET)
            (root / "config.json").write_text(json.dumps({"buy_queue": QUEUE}))
            cfg = {"buy_queue": [dict(r) for r in QUEUE]}
            self._run(root, cfg)
            text = (root / "queue.md").read_text().replace("| 90 |", "| 95 |")
            (root / "queue.md").write_text(text)
            cfg = json.loads((root / "config.json").read_text())
            out = self._run(root, cfg)
            maro = next(r for r in out["buy_queue"] if r["id"] == "maro-toe-socks")
            self.assertEqual(maro["amount_cents"], 9500)
            self.assertEqual(maro["match"], "MARO|TOE.?SOCK")

    def test_config_drop_removes_table_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "queue.md").write_text(FACT)
            (root / "budget.md").write_text(BUDGET)
            (root / "config.json").write_text(json.dumps({"buy_queue": QUEUE}))
            cfg = {"buy_queue": [dict(r) for r in QUEUE]}
            self._run(root, cfg)
            cfg = json.loads((root / "config.json").read_text())
            cfg["buy_queue"] = [r for r in cfg["buy_queue"] if r["id"] != "rice-cooker"]
            (root / "config.json").write_text(json.dumps(cfg))
            out = self._run(root, cfg)
            text = (root / "queue.md").read_text()
            self.assertNotIn("rice-cooker", text)
            self.assertIn("maro-toe-socks", text)
            self.assertIn("3 natural", text)
            self.assertEqual([r["id"] for r in out["buy_queue"]], ["maro-toe-socks"])
            self.assertIn("Maro toe socks", (root / "budget.md").read_text())
            self.assertNotIn("Rice cooker", (root / "budget.md").read_text())

    def test_blank_merchant_row_is_list_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            extra = FACT.replace(
                "| 2 | Rice cooker | 100 | Costco | id:rice-cooker. juicy rice |\n",
                "| 2 | Rice cooker | 100 | Costco | id:rice-cooker. juicy rice |\n"
                "| 3 | Headphones | 40 |  | id:headphones. someday |\n",
            )
            (root / "queue.md").write_text(extra)
            (root / "budget.md").write_text(BUDGET)
            (root / "config.json").write_text(json.dumps({"buy_queue": QUEUE}))
            cfg = {"buy_queue": [dict(r) for r in QUEUE]}
            # Baseline first, then the table gains a row.
            self._run(root, cfg)
            (root / "queue.md").write_text(extra)
            cfg = json.loads((root / "config.json").read_text())
            out = self._run(root, cfg)
            phones = next(r for r in out["buy_queue"] if r["id"] == "headphones")
            self.assertEqual(phones["match"], "")
            self.assertFalse(phones.get("confirm"))


if __name__ == "__main__":
    unittest.main()
