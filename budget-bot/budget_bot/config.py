"""Config load/save for Budget Bot."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


def _env_first(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def default_state_dir() -> Path:
    explicit = _env_first("BUDGET_BOT_STATE", "HERMES_FINANCE_STATE")
    if explicit:
        return Path(explicit)
    new = Path.home() / ".local/state/budget-bot"
    old = Path.home() / ".local/state/hermes-finance"
    if new.exists() or not old.exists():
        return new
    return old


def default_env_file() -> Path:
    explicit = _env_first("BUDGET_BOT_ENV", "HERMES_PLAID_ENV")
    if explicit:
        return Path(explicit)
    new = Path("/etc/budget-bot.env")
    old = Path("/etc/hermes-finance.env")
    if new.exists() or not old.exists():
        return new
    return old


def live_flag() -> bool:
    v = _env_first("BUDGET_BOT_LIVE", "HERMES_LIVE")
    return v == "1"


DEFAULT_STATE_DIR = default_state_dir()

DEFAULT_CONFIG: dict[str, Any] = {
    "hardcap_cents": 105_000,  # $1,050.00 monthly hardcap
    "period": "calendar_month",
    "timezone": "America/Los_Angeles",
    # bills: monthly reserve toward hardcap safe-to-spend.
    # Fields: name, amount_cents (monthly) OR annual_cents (/12),
    # cadence: "annual" → once-yearly due (month + day_of_month); leftover always
    # 1/12; cash-vs-bills uses the cash pull; charge month spend counts 1/12.
    # renews: false → term ends 12 months after the last in-band post (no next pull).
    # auto_annual: true → ~10× monthly post (named, or opaque in due window)
    # flips the row to annual cadence (Grok / US Mobile).
    # match (regex; when spend matches in period, reserve clears that month).
    # saas: true → fuzzy $ also allows ~10% over (CA tax-inclusive SaaS).
    "bills": [],
    "goals": [],  # [{name, amount_cents}]
    # Leftover-funded buy queue. Auto-pulled on high-conf named+amount spend.
    # [{id, name, amount_cents, match, merchant?, confirm?, amount_tol_cents?, not_before?}]
    # confirm (or merchant Costco): ask, do not auto-clear. Blank match: list only.
    "buy_queue": [],
    "anomaly": {
        # Loosened 2026-08-05: normal restocks (e.g. iHerb ~$100) were 2× noise.
        "merchant_mult_7d": 4.0,
        "category_mult_7d": 4.0,
        "min_abs_cents": 10000,  # floor for ratio path ($100 day)
        "over_abs_cents": 10000,  # $100 over baseline
        "min_baseline_samples": 3,
    },
    # Absolute soft floor: also soft-warn at this % of full hardcap (any day)
    "hardcap_warn_pct": 0.90,
    # Soft when spend > soft_pace_frac * (hardcap * month_frac)  e.g. 0.90 → 45% spend at mid-month
    "soft_pace_frac": 0.90,
    # Firm (interrupt) when spend_pct > month_frac  (strictly ahead of calendar)
    "pace_warn_ratio": 1.0,
    "pace_warn_min_over_cents": 0,
    "exclude_pending": True,
    "digest_hour_local": 8,
    # PINNED 2026-08-02: no daily digests
    "digest_enabled": False,
    "coaching_anomalies": True,
    "poll_hint": "webhook + 15m backup",
    "notify_enabled": False,
    "mode": "fixture",  # fixture | live
    # --- bills horizon / fuzzy clear ---
    # Calendar STS: rest of the current month (this key is ignored; kept for old configs).
    # Rolling 30d: upcoming bills due inside the 15d-ahead window.
    "bill_horizon_days_calendar": 0,
    # rolling display: all unposted bills with due date in the rolling window
    "bill_fuzzy_match": True,
    "bill_fuzzy_amount_tol_cents": 100,  # ±$1; SaaS bills also allow ~10% over
    "bill_fuzzy_day_slop": 2,  # due±2 days (US Mobile can post on the 3rd)
    # Unpaid past dues stack (each missed month) within lookback
    "bill_arrears_lookback_months": 6,
    "bill_payment_grace_days": 40,  # ~1 month late still clears original due
    # sync break: Pushover immediately with 24h re-login URL; email off
    "sync_break_email": False,
    "sync_break_pushover_after_days": 0,
    # /item/get probe: treat last_successful_update older than this as a break
    "item_stale_hours": 72,
    # Cash-vs-bills Pushover (once/day when any CU checking < that CU's 5d dues).
    "cash_bills_notify": True,
    # Culled Plaid Items (token gone). Webhooks ignored; does not free Trial slots.
    "deprecated_item_ids": [],
    # Firm-pace / breach Push SSOT. names_health never flips this.
    "notify_period": "calendar",
}


def state_dir() -> Path:
    p = default_state_dir()
    p.mkdir(parents=True, exist_ok=True)
    (p / "tokens").mkdir(exist_ok=True)
    (p / "digests").mkdir(exist_ok=True)
    try:
        os.chmod(p / "tokens", 0o700)
    except OSError:
        pass
    return p


def config_path() -> Path:
    return state_dir() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        cfg = deepcopy(DEFAULT_CONFIG)
        save_config(cfg)
        return cfg
    with path.open() as f:
        data = json.load(f)
    # merge defaults for new keys
    out = deepcopy(DEFAULT_CONFIG)
    out.update(data)
    if "anomaly" in data and isinstance(data["anomaly"], dict):
        out["anomaly"] = {**DEFAULT_CONFIG["anomaly"], **data["anomaly"]}
    return out


def save_config(cfg: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def persist_saas_bill_reserves(
    cfg: dict[str, Any],
    txns: list[Any],
    as_of: Any,
) -> list[dict[str, Any]]:
    """Rewrite saas bill amount_cents when an in-band tax-inclusive charge posts.

    No-op when the latest post is within ±$1 of the current reserve.
    """
    from datetime import date as date_cls

    from .rules import apply_saas_reserve_updates_to_bills, proposed_saas_reserve_updates

    if not isinstance(as_of, date_cls):
        as_of = date_cls.fromisoformat(str(as_of)[:10])
    updates = proposed_saas_reserve_updates(
        cfg.get("bills"),
        txns,
        as_of=as_of,
        exclude_pending=bool(cfg.get("exclude_pending", True)),
        fuzzy_amount_tol_cents=int(cfg.get("bill_fuzzy_amount_tol_cents", 100)),
        fuzzy_day_slop=int(cfg.get("bill_fuzzy_day_slop", 2)),
        payment_grace_days=int(cfg.get("bill_payment_grace_days", 40)),
    )
    if not updates:
        return []
    changed = apply_saas_reserve_updates_to_bills(cfg.get("bills"), updates)
    if changed:
        save_config(cfg)
    return updates


def persist_auto_annual_conversions(
    cfg: dict[str, Any],
    txns: list[Any],
    as_of: Any,
) -> list[dict[str, Any]]:
    """Flip `auto_annual` monthly bills to annual cadence on a ~10× prepay.

    Runs before SaaS reserve rewrite so the same lump amortizes 1/12.
    """
    from datetime import date as date_cls

    from .rules import (
        apply_auto_annual_conversions_to_bills,
        proposed_auto_annual_conversions,
    )

    if not isinstance(as_of, date_cls):
        as_of = date_cls.fromisoformat(str(as_of)[:10])
    updates = proposed_auto_annual_conversions(
        cfg.get("bills"),
        txns,
        as_of=as_of,
        exclude_pending=bool(cfg.get("exclude_pending", True)),
        fuzzy_day_slop=int(cfg.get("bill_fuzzy_day_slop", 2)),
        payment_grace_days=int(cfg.get("bill_payment_grace_days", 40)),
    )
    if not updates:
        return []
    changed = apply_auto_annual_conversions_to_bills(cfg.get("bills"), updates)
    if changed:
        save_config(cfg)
    return updates


def persist_bill_rewrites(
    cfg: dict[str, Any],
    txns: list[Any],
    as_of: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Annual prepay conversion first, then SaaS tax reserve follow."""
    from .buy_queue_sync import reconcile_buy_queue

    reconcile_buy_queue(cfg)
    annual = persist_auto_annual_conversions(cfg, txns, as_of)
    saas = persist_saas_bill_reserves(cfg, txns, as_of)
    from .buy_queue import persist_buy_queue_pulls

    pulled = persist_buy_queue_pulls(cfg, txns)
    reconcile_buy_queue(cfg)
    return {"auto_annual": annual, "saas": saas, "buy_queue": pulled}
