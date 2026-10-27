#!/usr/bin/env python3
"""Formatter and reader tests for Photon canned buy_list."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import format_buy_queue, format_todo_list, handle, read_buy_list
from classify import Intent


class TestBuyListFormatAndRead(unittest.TestCase):
    def test_sort_order_and_formatting(self):
        rows = [
            {
                "id": "z-item",
                "pri": "X",
                "name": "Mystery Item",
                "amount_cents": 500,
                "merchant": "Target",
            },
            {
                "id": "num-2",
                "rank": "2",
                "name": "Legacy Two",
                "amount_cents": 1500,
                "merchant": "Amazon",
            },
            {
                "id": "num-1",
                "pri": "1",
                "name": "Legacy One",
                "amount_cents": 2500,
                "merchant": "",
            },
            {
                "id": "c-item",
                "pri": "C",
                "name": "Lamp",
                "amount_cents": 4500,
                "merchant": "IKEA",
            },
            {
                "id": "b-high",
                "pri": "b",
                "name": "Jacket",
                "amount_cents": 8999,
                "merchant": "REI",
                "confirm": True,
            },
            {
                "id": "b-low",
                "pri": "B",
                "name": "Gloves",
                "amount_cents": 2000,
                "merchant": "Costco",
            },
            {
                "id": "a-beta",
                "pri": "A",
                "name": "Beta Socks",
                "amount_cents": 1250,
                "merchant": "Amazon",
            },
            {
                "id": "a-alpha",
                "pri": "a",
                "name": "Alpha Socks",
                "amount_cents": 1250,
                "merchant": "",
            },
        ]
        out = format_buy_queue(rows)
        self.assertEqual(
            out.splitlines(),
            [
                "Astra\u2019s buy list:",
                "",
                "A:",
                "\u2022 Alpha Socks \u2014 $12.50",
                "\u2022 Beta Socks \u2014 $12.50",
                "",
                "B:",
                "\u2022 Gloves \u2014 $20",
                "\u2022 Jacket \u2014 $89.99",
                "",
                "C:",
                "\u2022 Lamp \u2014 $45",
                "",
                "1:",
                "\u2022 Legacy One \u2014 $25",
                "",
                "2:",
                "\u2022 Legacy Two \u2014 $15",
                "",
                "X:",
                "\u2022 Mystery Item \u2014 $5",
            ],
        )

    def test_skips_invalid_rows_and_empty_names(self):
        rows = [
            "not-a-dict",
            {"id": "blank", "name": "   ", "amount_cents": 1000},
            {"id": "ok", "pri": "A", "name": "Valid", "amount_cents": 1000, "merchant": "Shop"},
        ]
        self.assertEqual(
            format_buy_queue(rows),
            "Astra\u2019s buy list:\n\nA:\n\u2022 Valid \u2014 $10",
        )

    def test_band_comes_from_table_when_config_omits_it(self):
        table = "\n".join(
            [
                "| Pri | Item | ~$ | Merchant (auto-pull) | Notes |",
                "|-----|------|----|----------------------|-------|",
                "| B | Jacket | 90 | REI | id:jacket. ask |",
                "| A | Socks | 12 |  | id:socks |",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            fact = Path(tmp) / "queue.md"
            fact.write_text(table, encoding="utf-8")
            cfg.write_text(
                json.dumps(
                    {
                        "buy_queue": [
                            {
                                "id": "jacket",
                                "name": "Jacket",
                                "amount_cents": 9000,
                                "merchant": "REI",
                                "confirm": True,
                            },
                            {
                                "id": "socks",
                                "name": "Socks",
                                "amount_cents": 1200,
                                "merchant": "",
                            },
                            {
                                "id": "extra",
                                "name": "Extra",
                                "amount_cents": 100,
                                "merchant": "Shop",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                read_buy_list(cfg, fact).splitlines(),
                [
                    "Astra\u2019s buy list:",
                    "",
                    "A:",
                    "\u2022 Socks \u2014 $12",
                    "",
                    "B:",
                    "\u2022 Jacket \u2014 $90",
                    "",
                    "Other:",
                    "\u2022 Extra \u2014 $1",
                ],
            )

    def test_missing_band_sorts_by_price_not_file_order(self):
        rows = [
            {"name": "Expensive", "amount_cents": 10000, "merchant": "Shop"},
            {"name": "Cheap", "amount_cents": 500, "merchant": "Shop"},
        ]
        self.assertEqual(
            format_buy_queue(rows).splitlines(),
            [
                "Astra\u2019s buy list:",
                "",
                "Other:",
                "\u2022 Cheap \u2014 $5",
                "\u2022 Expensive \u2014 $100",
            ],
        )

    def test_zero_printable_rows(self):
        self.assertEqual(
            format_buy_queue(["bad", {"name": ""}]),
            "Buy list isn't available.",
        )

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nonexistent.json"
            self.assertEqual(read_buy_list(missing), "Buy list isn't available.")

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            p.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(read_buy_list(p), "Buy list isn't available.")

    def test_empty_or_non_list_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            p.write_text(json.dumps({"buy_queue": []}), encoding="utf-8")
            self.assertEqual(read_buy_list(p), "Buy list isn't available.")

            p.write_text(json.dumps({"buy_queue": "nope"}), encoding="utf-8")
            self.assertEqual(read_buy_list(p), "Buy list isn't available.")

            p.write_text(json.dumps({}), encoding="utf-8")
            self.assertEqual(read_buy_list(p), "Buy list isn't available.")

    def test_read_from_tmp_json_and_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            payload = {
                "buy_queue": [
                    {
                        "id": "item-1",
                        "pri": "A",
                        "name": " Kettle ",
                        "amount_cents": 3500,
                        "merchant": "costco.com",
                        "match": "COSTCO",
                        "notes": "id:item-1",
                    }
                ]
            }
            p.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                read_buy_list(p),
                "Astra\u2019s buy list:\n\nA:\n\u2022 Kettle \u2014 $35",
            )
            fact = Path(tmp) / "fact.md"
            fact.write_text(
                "---\nupdated: 2026-10-23T20:13:32.913Z\n---\n",
                encoding="utf-8",
            )
            with (
                patch("actions._buy_queue_config_path", return_value=p),
                patch("actions._QUEUE_FACT", fact),
            ):
                self.assertEqual(
                    handle(Intent("buy_list")),
                    "Astra\u2019s buy list, last updated Oct 23:\n\nA:\n\u2022 Kettle \u2014 $35",
                )


class TestTodoListFormat(unittest.TestCase):
    def test_plain_line_hides_the_work_order(self):
        items = [
            {
                "status": "open",
                "title": "abraunegg OneDrive client for VPS SSOT",
                "next_action": "Grok Build: Evaluate/install the whole client and write a long note.",
                "plain": "OneDrive sync \u2014 evaluate bidirectional sync with the VPS, keeping sensitive Budget Bot state out.",
            },
            {
                "status": "done",
                "title": "Already done",
                "plain": "do not show",
            },
            {
                "status": "open",
                "title": "check US Mobile annual",
                "next_action": "Grok Build: parked from Photon",
            },
        ]
        self.assertEqual(
            format_todo_list(items),
            "\n".join(
                [
                    "2 open items:",
                    "",
                    "1. OneDrive sync \u2014 evaluate bidirectional sync with the VPS, keeping sensitive Budget Bot state out.",
                    "2. check US Mobile annual",
                ]
            ),
        )

    def test_one_item(self):
        self.assertEqual(
            format_todo_list([{"status": "open", "title": "Only"}]),
            "1 open item:\n\n1. Only",
        )

    def test_none_open(self):
        self.assertEqual(format_todo_list([{"status": "done", "title": "X"}]), "No open items.")


if __name__ == "__main__":
    unittest.main()
