"""Detect when NorCal MasterMoney names (or Alliant spend) are live again.

Does **not** auto-flip calendar notify, statement SSOT, cash source, or DD.
Auto-releases: the biweekly MasterMoney nudge (skip when names_live).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .balances import is_alliant_institution, is_norcal_institution
from .config import state_dir
from .models import Transaction
from .transfers import is_debit_card_purchase, is_opaque_mastermoney

LOOKBACK_DAYS = 14
NORCAL_MIN_N = 8
NORCAL_NAMED_FRAC = 0.70
ALLIANT_NAMED_N = 5


def _is_spend(t: Transaction) -> bool:
    if t.pending or t.excluded or t.transfer:
        return False
    return (t.amount_cents or 0) > 0


def _is_norcal_plaid(t: Transaction) -> bool:
    inst = (t.institution or "").lower()
    return (t.id or "").startswith("plaid-") or "northern-california" in inst


def _is_named(t: Transaction) -> bool:
    return not is_opaque_mastermoney(name=t.name or "", merchant_name=t.merchant_name)


def names_health_path() -> Path:
    return state_dir() / "names_health.json"


def load_names_health() -> dict[str, Any]:
    p = names_health_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_names_health(data: dict[str, Any]) -> None:
    p = names_health_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(p)


def assess_names_health(
    txns: list[Transaction],
    as_of: date,
    *,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, Any]:
    start = as_of - timedelta(days=max(1, int(lookback_days)) - 1)
    norcal_n = 0
    norcal_named = 0
    alliant_named = 0
    for t in txns:
        if not _is_spend(t):
            continue
        d = t.date or ""
        if d < start.isoformat() or d > as_of.isoformat():
            continue
        inst = t.institution or ""
        if is_alliant_institution(inst):
            if _is_named(t):
                alliant_named += 1
            continue
        if not (is_norcal_institution(inst) or _is_norcal_plaid(t)):
            continue
        if not _is_norcal_plaid(t):
            continue  # statement rows still have tails; live Plaid is the question
        if not is_debit_card_purchase(name=t.name or "", merchant_name=t.merchant_name):
            continue
        norcal_n += 1
        if _is_named(t):
            norcal_named += 1
    frac = (norcal_named / norcal_n) if norcal_n else None
    norcal_live = bool(norcal_n >= NORCAL_MIN_N and frac is not None and frac >= NORCAL_NAMED_FRAC)
    alliant_live = alliant_named >= ALLIANT_NAMED_N
    live = norcal_live or alliant_live
    reason = "opaque"
    if norcal_live and alliant_live:
        reason = "norcal_and_alliant"
    elif norcal_live:
        reason = "norcal_named"
    elif alliant_live:
        reason = "alliant_named"
    elif norcal_n < NORCAL_MIN_N and alliant_named < ALLIANT_NAMED_N:
        reason = "not_enough_sample"
    return {
        "as_of": as_of.isoformat(),
        "lookback_days": lookback_days,
        "norcal_plaid_n": norcal_n,
        "norcal_named_n": norcal_named,
        "norcal_named_frac": None if frac is None else round(frac, 3),
        "alliant_named_n": alliant_named,
        "names_live": live,
        "reason": reason,
        # Pins this detector will never flip:
        "never_auto": ["notify_period_calendar", "statement_ssot", "cash_source", "dd"],
    }


def persist_names_health(
    txns: list[Transaction],
    as_of: date,
) -> dict[str, Any]:
    prev = load_names_health()
    now = assess_names_health(txns, as_of)
    now["was_live"] = bool(prev.get("names_live"))
    now["flipped"] = bool(now["names_live"]) != bool(prev.get("names_live"))
    save_names_health(now)
    return now
