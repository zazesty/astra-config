#!/usr/bin/env python3
"""Run canned Photon intents. No LLM."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from classify import Intent

BUDGET_CMD = [
    "python3",
    "-m",
    "budget_bot",
    "budget-status",
]
TODOS = Path("/root/astra-config/scripts/standing-todos.sh")
TODOS_PATH = Path.home() / ".local/state/astra/standing-todos.json"
FINANCE_CWD = Path("/root/budget-bot")

_BAND = {"a": 0, "b": 1, "c": 2}
_PT = ZoneInfo("America/Los_Angeles")
_QUEUE_FACT = Path("/root/memory/budget-bot-buy-queue.md")
_ROW_ID_RE = re.compile(r"\bid:([a-z0-9-]+)", re.I)
_UPDATED_RE = re.compile(r"(?m)^updated:\s*(\S+)")
_APOS = "\u2019"
_EM = "\u2014"
_BULLET = "\u2022"


def _buy_queue_config_path() -> Path:
    for name in ("BUDGET_BOT_STATE", "HERMES_FINANCE_STATE"):
        val = os.environ.get(name)
        if val:
            return Path(val) / "config.json"
    new = Path.home() / ".local/state/budget-bot"
    old = Path.home() / ".local/state/hermes-finance"
    state = new if (new.exists() or not old.exists()) else old
    return state / "config.json"


def _table_order_key(pri: str, amount: int, name: str) -> tuple:
    label = (pri or "").strip().lower()
    if label in _BAND:
        return (0, _BAND[label], amount, name)
    if label.isdigit():
        return (1, int(label), amount, name)
    return (2, 0, amount, name)


def _format_dollars(cents: int) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    if frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:02d}"


def _split_table_line(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def pri_by_id_from_table(text: str) -> dict[str, str]:
    """Band by id from the Order-now table. The config rows do not store it."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = _split_table_line(line)
        if not cells:
            continue
        head = cells[0].lower()
        if head in {"rank", "pri"} and any(c.lower().startswith("item") for c in cells):
            start = i
            break
    if start is None:
        return {}
    found: dict[str, str] = {}
    for line in lines[start + 2 :]:
        if not line.strip().startswith("|"):
            break
        cells = _split_table_line(line)
        if len(cells) < 5:
            continue
        pri, name = cells[0], cells[1]
        if not name or name.lower() == "item":
            continue
        match = _ROW_ID_RE.search(" | ".join(cells[4:]))
        if not match:
            continue
        found[match.group(1).lower()] = pri.strip()
    return found


def _updated_label(text: str) -> str | None:
    match = _UPDATED_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip().strip("'\"")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_PT)
    return f"{local.strftime('%b')} {local.day}"


def _buy_header(updated: str | None) -> str:
    if updated:
        return f"Astra{_APOS}s buy list, last updated {updated}:"
    return f"Astra{_APOS}s buy list:"


def format_buy_queue(
    rows: list[Any],
    pri_by_id: dict[str, str] | None = None,
    updated: str | None = None,
) -> str:
    bands = pri_by_id or {}
    decorated = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        raw_pri = row.get("pri")
        if raw_pri is None or str(raw_pri).strip() == "":
            raw_pri = bands.get(str(row.get("id") or "").strip().lower())
        if raw_pri is None or str(raw_pri).strip() == "":
            raw_pri = row.get("rank")
        pri_str = str(raw_pri).strip() if raw_pri is not None else ""
        pri_disp = pri_str.upper() if pri_str.lower() in _BAND else (pri_str or "-")
        try:
            amount = int(row.get("amount_cents") or 0)
        except (TypeError, ValueError):
            amount = 0
        decorated.append(
            (_table_order_key(pri_str, amount, name.lower()), pri_disp, name, amount)
        )
    if not decorated:
        return "Buy list isn't available."
    decorated.sort(key=lambda item: item[0])
    groups: list[tuple[str, list[str]]] = []
    for _key, pri_disp, name, amount in decorated:
        label = "Other" if pri_disp == "-" else pri_disp
        bullet = f"{_BULLET} {name} {_EM} ${_format_dollars(amount)}"
        if not groups or groups[-1][0] != label:
            groups.append((label, []))
        groups[-1][1].append(bullet)
    chunks = [_buy_header(updated), ""]
    for i, (label, lines) in enumerate(groups):
        if i:
            chunks.append("")
        chunks.append(f"{label}:")
        chunks.extend(lines)
    return "\n".join(chunks)


def read_buy_list(path: Path | None = None, fact_path: Path | None = None) -> str:
    cfg_path = path if path is not None else _buy_queue_config_path()
    try:
        raw = cfg_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "Buy list isn't available."
    if not isinstance(data, dict):
        return "Buy list isn't available."
    queue = data.get("buy_queue")
    if not isinstance(queue, list) or not queue:
        return "Buy list isn't available."
    pri_by_id: dict[str, str] = {}
    updated = None
    # Only the live call reads the Astra table. Tests pass a config path alone.
    if path is None or fact_path is not None:
        src = fact_path if fact_path is not None else _QUEUE_FACT
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        pri_by_id = pri_by_id_from_table(text)
        updated = _updated_label(text)
    return format_buy_queue(queue, pri_by_id, updated)


def _todo_line(item: dict[str, Any]) -> str:
    plain = str(item.get("plain") or "").strip()
    if plain:
        return plain
    return str(item.get("title") or "").strip() or "Untitled"


def format_todo_list(items: list[Any]) -> str:
    open_items = [
        item for item in items if isinstance(item, dict) and item.get("status") == "open"
    ]
    count = len(open_items)
    if count == 0:
        return "No open items."
    header = "1 open item:" if count == 1 else f"{count} open items:"
    lines = [header, ""]
    for n, item in enumerate(open_items, start=1):
        lines.append(f"{n}. {_todo_line(item)}")
    return "\n".join(lines)


def read_todo_list(path: Path | None = None) -> str:
    src = path if path is not None else TODOS_PATH
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "Todo list isn't available."
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return "Todo list isn't available."
    return format_todo_list(items)


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 20) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        tail = err or out or f"exit {proc.returncode}"
        return f"Command failed: {tail[:300]}"
    return out


def handle(intent: Intent) -> str:
    if intent.kind == "budget":
        return _run(BUDGET_CMD, cwd=FINANCE_CWD)

    if intent.kind == "buy_list":
        return read_buy_list()

    if intent.kind == "todo_list":
        return read_todo_list()

    if intent.kind == "todo_add":
        title = (intent.title or "").strip()
        if not title:
            return "Need a title. Try: add X to the todo list"
        item_id = _run(
            [
                str(TODOS),
                "add",
                title,
                "--owner",
                "agent",
                "--priority",
                "3",
                "--next",
                "Grok Build: parked from Photon",
            ]
        )
        if item_id.startswith("Command failed"):
            return item_id
        return f"Parked: {title}"

    return "Unknown canned intent."

