"""Drop leftover-funded buy-queue rows on a high-conf named charge.

Never matches opaque MasterMoney / bare POS #. Amount-only is not enough.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .config import save_config, state_dir
from .models import Transaction
from .transfers import is_opaque_mastermoney

DEFAULT_TOL_PCT = 0.15
MIN_TOL_CENTS = 1000  # $10 floor so a $90 row still has room


def buy_queue_pulled_path():
    return state_dir() / "buy_queue_pulled.jsonl"


def _blob(t: Transaction) -> str:
    return f"{t.merchant_name or ''} {t.name or ''}"


def _is_named_spend(t: Transaction) -> bool:
    if t.pending or t.excluded or t.transfer:
        return False
    if (t.amount_cents or 0) <= 0:
        return False
    return not is_opaque_mastermoney(name=t.name or "", merchant_name=t.merchant_name)


def _amount_tol_cents(row: dict[str, Any]) -> int:
    if row.get("amount_tol_cents") is not None:
        return max(0, int(row["amount_tol_cents"]))
    target = int(row.get("amount_cents") or 0)
    pct = float(row.get("amount_tol_pct") or DEFAULT_TOL_PCT)
    return max(MIN_TOL_CENTS, int(round(abs(target) * pct)))


def _amount_in_band(posted: int, row: dict[str, Any]) -> bool:
    target = int(row.get("amount_cents") or 0)
    if target <= 0 or posted <= 0:
        return False
    return abs(posted - target) <= _amount_tol_cents(row)


def _name_match(t: Transaction, row: dict[str, Any]) -> bool:
    pat = str(row.get("match") or "").strip()
    if not pat:
        return False
    try:
        return bool(re.search(pat, _blob(t), re.I))
    except re.error:
        return False


def proposed_buy_queue_pulls(
    queue: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
) -> list[dict[str, Any]]:
    """High-conf named+amount hits. One txn pulls at most one row (closest $)."""
    rows = [r for r in (queue or []) if r.get("id") and r.get("match")]
    if not rows:
        return []
    claimed_tx: set[str] = set()
    claimed_row: set[str] = set()
    pulls: list[dict[str, Any]] = []
    candidates: list[tuple[int, str, str, dict[str, Any]]] = []
    for t in txns or []:
        if not _is_named_spend(t):
            continue
        for row in rows:
            rid = str(row.get("id") or "")
            if not rid or not _name_match(t, row) or not _amount_in_band(t.amount_cents, row):
                continue
            gap = abs(int(t.amount_cents) - int(row.get("amount_cents") or 0))
            candidates.append((gap, t.date or "", t.id, row))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    for _gap, _d, tid, row in candidates:
        rid = str(row.get("id") or "")
        if tid in claimed_tx or rid in claimed_row:
            continue
        claimed_tx.add(tid)
        claimed_row.add(rid)
        pulls.append(
            {
                "id": rid,
                "name": row.get("name") or rid,
                "amount_cents": int(row.get("amount_cents") or 0),
                "txn_id": tid,
            }
        )
    return pulls


def apply_buy_queue_pulls(
    queue: list[dict[str, Any]] | None,
    pulls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    drop = {str(p.get("id") or "") for p in pulls if p.get("id")}
    if not drop:
        return list(queue or [])
    return [r for r in (queue or []) if str(r.get("id") or "") not in drop]


def persist_buy_queue_pulls(
    cfg: dict[str, Any],
    txns: list[Transaction] | None,
) -> list[dict[str, Any]]:
    pulls = proposed_buy_queue_pulls(cfg.get("buy_queue"), txns)
    if not pulls:
        return []
    cfg["buy_queue"] = apply_buy_queue_pulls(cfg.get("buy_queue"), pulls)
    save_config(cfg)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = buy_queue_pulled_path()
    with path.open("a") as f:
        for p in pulls:
            rec = dict(p)
            rec["pulled_at"] = ts
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return pulls
