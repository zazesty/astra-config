#!/usr/bin/env python3
"""Run canned Photon intents. No LLM."""

from __future__ import annotations

import subprocess
from pathlib import Path

from classify import Intent

BUDGET_CMD = [
    "python3",
    "-m",
    "hermes_finance",
    "budget-status",
]
TODOS = Path("/root/astra-config/scripts/standing-todos.sh")
FINANCE_CWD = Path("/root/hermes-finance")


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

    if intent.kind == "todo_list":
        return _run([str(TODOS), "list"])

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
