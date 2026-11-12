#!/usr/bin/env python3
"""Tests for upserting budget bot pushover notifications into photon log."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot.models import AlertEvent
from budget_bot.photon_log import (
    load_recent_notifications,
    record_notification,
    record_notification_from_push,
)


class TestPhotonLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_dir = Path(self.tmp.name) / "hermes"
        self.hermes_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.hermes_dir / "state.db"
        self._init_mock_hermes_db()

    def tearDown(self):
        self.tmp.cleanup()

    def _init_mock_hermes_db(self):
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE gateway_routing (
                scope TEXT,
                session_key TEXT PRIMARY KEY,
                entry_json TEXT,
                updated_at TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT,
                started_at REAL,
                last_activity_at REAL,
                message_count INTEGER DEFAULT 0
            )"""
        )
        cur.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp REAL,
                display_metadata TEXT,
                active INTEGER DEFAULT 1,
                compacted INTEGER DEFAULT 0
            )"""
        )
        # Seed active photon session
        entry = json.dumps({"session_id": "test_photon_sess_123"})
        cur.execute(
            "INSERT INTO gateway_routing VALUES ('scope', 'agent:main:photon:dm:any;-;+15303766948', ?, '2026-11-11')",
            (entry,),
        )
        cur.execute(
            "INSERT INTO sessions (id, source, started_at, last_activity_at, message_count) VALUES ('test_photon_sess_123', 'photon', 1000.0, 1000.0, 0)"
        )
        conn.commit()
        conn.close()

    @patch("budget_bot.photon_log.state_dir")
    @patch("budget_bot.photon_log._get_hermes_db_path")
    def test_record_notification_writes_files_and_injects_db(self, mock_db_path, mock_sdir):
        mock_db_path.return_value = self.db_path
        mock_sdir.return_value = self.state_dir

        event = AlertEvent(
            kind="anomaly",
            subject="Budget Bot: unusual Transfer $57",
            body="Unusual Transfer spend. $57 this month, vs $0 last month.",
            key="anomaly|2026-11|Transfer",
            payload={"category": "Transfer", "amount_cents": 5700},
        )

        record_notification(
            event,
            title="Budget Bot: unusual Transfer $57",
            body="Unusual Transfer spend. $57 this month, vs $0 last month.",
            priority=0,
        )

        # Check jsonl
        jsonl = self.state_dir / "pushover_notifications.jsonl"
        self.assertTrue(jsonl.is_file())
        lines = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["key"], "anomaly|2026-11|Transfer")
        self.assertEqual(lines[0]["priority"], 0)

        # Check json
        recent = load_recent_notifications(10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["title"], "Budget Bot: unusual Transfer $57")

        # Check hermes sqlite db
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        rows = cur.execute("SELECT session_id, role, content, display_metadata FROM messages").fetchall()
        self.assertEqual(len(rows), 1)
        sess_id, role, content, meta = rows[0]
        self.assertEqual(sess_id, "test_photon_sess_123")
        self.assertEqual(role, "system")
        self.assertIn("Budget Bot: unusual Transfer $57", content)
        self.assertIn("Key: anomaly|2026-11|Transfer", content)
        self.assertEqual(json.loads(meta), {"model_only": True})

        sess_row = cur.execute("SELECT message_count FROM sessions WHERE id='test_photon_sess_123'").fetchone()
        self.assertEqual(sess_row[0], 1)
        conn.close()

    @patch("budget_bot.photon_log.state_dir")
    @patch("budget_bot.photon_log._get_hermes_db_path")
    def test_record_notification_dedup_in_session(self, mock_db_path, mock_sdir):
        mock_db_path.return_value = self.db_path
        mock_sdir.return_value = self.state_dir

        record_notification_from_push("Title 1", "Body 1", priority=1)
        with sqlite3.connect(str(self.db_path)) as conn:
            count1 = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        self.assertEqual(count1, 1)


if __name__ == "__main__":
    unittest.main()
