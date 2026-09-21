"""Compatibility alias — the package is now `budget_bot`. Prefer that import."""

from __future__ import annotations

import sys

import budget_bot

__version__ = getattr(budget_bot, "__version__", "0.1.0")

for _name in (
    "auto_review",
    "balances",
    "buy_queue",
    "config",
    "dedupe",
    "import_statement_pdf",
    "import_xlsx",
    "models",
    "names_health",
    "notify",
    "plaid_client",
    "plaid_link_server",
    "plaid_sync",
    "plaid_webhook",
    "recurring",
    "rules",
    "run",
    "store",
    "sync_health",
    "templates",
    "transfers",
):
    _mod = __import__(f"budget_bot.{_name}", fromlist=[_name])
    sys.modules[f"{__name__}.{_name}"] = _mod
    globals()[_name] = _mod

from budget_bot import *  # noqa: E402,F401,F403
