"""Config load/save for Hermes-Finance."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path(
    os.environ.get("HERMES_FINANCE_STATE", Path.home() / ".local/state/hermes-finance")
)

DEFAULT_CONFIG: dict[str, Any] = {
    "hardcap_cents": 105_000,  # $1,050.00 monthly hardcap
    "period": "calendar_month",
    "timezone": "America/Los_Angeles",
    # bills: monthly reserve toward hardcap safe-to-spend.
    # Fields: name, amount_cents (monthly) OR annual_cents (/12),
    # match (regex; when spend matches in period, reserve clears that month).
    "bills": [],
    "goals": [],  # [{name, amount_cents}]
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
    "bill_fuzzy_amount_tol_cents": 100,  # ±$1
    "bill_fuzzy_day_slop": 2,  # due±2 days (US Mobile can post on the 3rd)
    # Unpaid past dues stack (each missed month) within lookback
    "bill_arrears_lookback_months": 6,
    "bill_payment_grace_days": 40,  # ~1 month late still clears original due
    # sync break: Pushover immediately with 24h re-login URL; email off
    "sync_break_email": False,
    "sync_break_pushover_after_days": 0,
    # /item/get probe: treat last_successful_update older than this as a break
    "item_stale_hours": 72,
}


def state_dir() -> Path:
    p = Path(os.environ.get("HERMES_FINANCE_STATE", DEFAULT_STATE_DIR))
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
