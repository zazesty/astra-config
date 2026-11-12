"""Upsert Budget Bot Pushover notifications into Hermes Photon session log and file state."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import state_dir
from .models import AlertEvent

logger = logging.getLogger("budget_bot.photon_log")


def _get_hermes_db_path() -> Path:
    hermes_home = Path.home() / ".hermes"
    if not hermes_home.exists():
        hermes_home = Path("/root/.hermes")
    return hermes_home / "state.db"


def _format_message(title: str, body: str, key: str, kind: str, priority: int) -> str:
    lines = [
        "[Budget Bot Notification (Pushover)]",
        f"Title: {title}",
        f"Body: {body.strip()}",
        f"Key: {key} | Kind: {kind} | Priority: {priority}",
    ]
    return "\n".join(lines)


def _find_active_photon_session(conn: sqlite3.Connection) -> Optional[str]:
    # 1. Look in gateway_routing
    try:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT entry_json FROM gateway_routing WHERE session_key LIKE '%photon%'"
        ).fetchall()
        for r in rows:
            if r and r[0]:
                try:
                    entry = json.loads(r[0])
                    sid = entry.get("session_id")
                    if sid:
                        return str(sid)
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Fallback to sessions table
    try:
        cur = conn.cursor()
        row = cur.execute(
            """SELECT id FROM sessions WHERE source='photon'
               ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 1"""
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass

    return None


def _inject_into_hermes_session(
    title: str, body: str, key: str, kind: str, priority: int
) -> bool:
    db_path = _get_hermes_db_path()
    if not db_path.is_file():
        return False

    try:
        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            session_id = _find_active_photon_session(conn)
            if not session_id:
                return False

            content = _format_message(title, body, key, kind, priority)

            # Avoid duplicate notification turns in the same session
            cur = conn.cursor()
            existing = cur.execute(
                "SELECT 1 FROM messages WHERE session_id=? AND content LIKE ? LIMIT 1",
                (session_id, f"%Key: {key} %"),
            ).fetchone()
            if existing:
                return True

            now = time.time()
            display_meta = json.dumps({"model_only": True})
            cur.execute(
                """INSERT INTO messages (
                    session_id, role, content, timestamp, display_metadata, active, compacted
                ) VALUES (?, 'system', ?, ?, ?, 1, 0)""",
                (session_id, content, now, display_meta),
            )
            cur.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
                (session_id,),
            )
            conn.commit()
            return True
    except Exception as e:
        logger.debug("Failed to inject notification into hermes session: %s", e)
        return False


def record_notification(
    event: AlertEvent | dict[str, Any],
    title: str,
    body: str,
    priority: int = 0,
) -> None:
    """Record pushover notification to state files and inject into active Photon session."""
    key = getattr(event, "key", None) or (event.get("key") if isinstance(event, dict) else "") or f"push-{int(time.time())}"
    kind = getattr(event, "kind", None) or (event.get("kind") if isinstance(event, dict) else "unknown")
    payload = getattr(event, "payload", None) or (event.get("payload") if isinstance(event, dict) else {})

    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": key,
        "kind": kind,
        "priority": priority,
        "title": title,
        "body": body,
        "payload": payload,
    }

    # 1. Append to jsonl
    try:
        sdir = state_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        jsonl_path = sdir / "pushover_notifications.jsonl"
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.debug("Failed to write to pushover_notifications.jsonl: %s", e)

    # 2. Maintain rolling JSON (last 50)
    try:
        sdir = state_dir()
        json_path = sdir / "pushover_notifications.json"
        items: list[dict[str, Any]] = []
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    items = data
            except Exception:
                pass
        # Upsert by key if key exists, else append
        updated = False
        for idx, item in enumerate(items):
            if item.get("key") == key:
                items[idx] = record
                updated = True
                break
        if not updated:
            items.append(record)
        if len(items) > 50:
            items = items[-50:]
        json_path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to write to pushover_notifications.json: %s", e)

    # 3. Inject into active Hermes Photon session
    _inject_into_hermes_session(title, body, key, str(kind), priority)


def record_notification_from_push(title: str, body: str, priority: int = 0) -> None:
    """Convenience helper for bash or external callers."""
    record_notification(
        {"key": f"push|{int(time.time())}", "kind": "direct_push"},
        title=title,
        body=body,
        priority=priority,
    )


def load_recent_notifications(limit: int = 20) -> list[dict[str, Any]]:
    """Load recent pushover notifications."""
    json_path = state_dir() / "pushover_notifications.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-limit:]
        except Exception:
            pass
    return []
