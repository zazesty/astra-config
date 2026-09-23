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
# Warehouse and costco.com only. Gas posts as "COSTCO GAS" and must not ask.
COSTCO_MATCH = r"COSTCO WHSE|WWW\s*COSTCO|COSTCO\.COM"


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


def _on_or_after(t: Transaction, row: dict[str, Any]) -> bool:
    """Optional not_before (YYYY-MM-DD) skips older ledger hits.

    The pull walks the full ledger, so a merchant as broad as Costco needs
    a floor or a past in-band trip drops a row that was never bought.
    """
    floor = str(row.get("not_before") or "").strip()[:10]
    if not floor:
        return True
    posted = (t.date or "")[:10]
    return bool(posted) and posted >= floor


def is_costco_merchant(merchant: str) -> bool:
    return bool(re.search(r"\bcostco\b", merchant or "", re.I))


def needs_confirm(row: dict[str, Any]) -> bool:
    """Costco is too broad to auto-clear. Other named merchants clear themselves."""
    if bool(row.get("confirm")):
        return True
    return is_costco_merchant(str(row.get("merchant") or ""))


def _name_match(t: Transaction, row: dict[str, Any]) -> bool:
    pat = str(row.get("match") or "").strip()
    if not pat:
        return False
    try:
        return bool(re.search(pat, _blob(t), re.I))
    except re.error:
        return False


def _dollars(cents: int) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    if frac == 0:
        return f"{sign}${whole}"
    return f"{sign}${whole}.{frac:02d}"


def _match_rows(
    queue: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
    *,
    confirm: bool,
) -> list[tuple[Transaction, dict[str, Any]]]:
    """Named+amount hits. One txn claims at most one row (closest $)."""
    rows = [
        r
        for r in (queue or [])
        if r.get("id") and str(r.get("match") or "").strip() and needs_confirm(r) == confirm
    ]
    if not rows:
        return []
    claimed_tx: set[str] = set()
    claimed_row: set[str] = set()
    chosen: list[tuple[Transaction, dict[str, Any]]] = []
    candidates: list[tuple[int, str, str, Transaction, dict[str, Any]]] = []
    for t in txns or []:
        if not _is_named_spend(t):
            continue
        for row in rows:
            rid = str(row.get("id") or "")
            if (
                not rid
                or not _on_or_after(t, row)
                or not _name_match(t, row)
                or not _amount_in_band(t.amount_cents, row)
            ):
                continue
            gap = abs(int(t.amount_cents) - int(row.get("amount_cents") or 0))
            candidates.append((gap, t.date or "", t.id, t, row))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    for _gap, _d, tid, t, row in candidates:
        rid = str(row.get("id") or "")
        if tid in claimed_tx or rid in claimed_row:
            continue
        claimed_tx.add(tid)
        claimed_row.add(rid)
        chosen.append((t, row))
    return chosen


def proposed_buy_queue_pulls(
    queue: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
) -> list[dict[str, Any]]:
    """High-conf named+amount hits that auto-clear. Confirm rows are excluded."""
    return [
        {
            "id": str(row.get("id") or ""),
            "name": row.get("name") or row.get("id"),
            "amount_cents": int(row.get("amount_cents") or 0),
            "txn_id": t.id,
        }
        for t, row in _match_rows(queue, txns, confirm=False)
    ]


def proposed_buy_queue_asks(
    queue: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
) -> list[dict[str, Any]]:
    """In-band hits on confirm rows. The row stays until a person says yes."""
    asks = []
    for t, row in _match_rows(queue, txns, confirm=True):
        merchant = str(row.get("merchant") or "Costco")
        listed = int(row.get("amount_cents") or 0)
        asks.append(
            {
                "id": str(row.get("id") or ""),
                "name": row.get("name") or row.get("id"),
                "amount_cents": listed,
                "txn_id": t.id,
                "txn_cents": int(t.amount_cents or 0),
                "txn_date": (t.date or "")[:10],
                "merchant": merchant,
            }
        )
    return asks


def apply_buy_queue_pulls(
    queue: list[dict[str, Any]] | None,
    pulls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    drop = {str(p.get("id") or "") for p in pulls if p.get("id")}
    if not drop:
        return list(queue or [])
    return [r for r in (queue or []) if str(r.get("id") or "") not in drop]


def replace_buy_queue(cfg: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Write buy_queue back without reshuffling the rest of the config."""
    cfg["buy_queue"] = rows
    from .config import config_path

    path = config_path()
    if not path.exists():
        save_config(cfg)
        return
    data = json.loads(path.read_text())
    data["buy_queue"] = rows
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def persist_buy_queue_pulls(
    cfg: dict[str, Any],
    txns: list[Transaction] | None,
) -> list[dict[str, Any]]:
    pulls = proposed_buy_queue_pulls(cfg.get("buy_queue"), txns)
    if not pulls:
        return []
    replace_buy_queue(cfg, apply_buy_queue_pulls(cfg.get("buy_queue"), pulls))
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


def buy_queue_ask_events(
    queue: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
) -> list[Any]:
    """One pri-0 question per uncertain charge. Not coalesced with pace or breach."""
    from .models import AlertEvent

    events = []
    for ask in proposed_buy_queue_asks(queue, txns):
        name = str(ask["name"])
        paid = _dollars(int(ask["txn_cents"]))
        listed = _dollars(int(ask["amount_cents"]))
        when = ask["txn_date"] or "that charge"
        events.append(
            AlertEvent(
                kind="buy_queue_ask",
                subject=f"{name}?",
                body=(
                    f"{ask['merchant']} {paid} on {when}. "
                    f"Listed at {listed}. Still on the list.\n"
                ),
                key=f"buyq-ask:{ask['id']}:{ask['txn_id']}",
                payload={
                    "push_priority": 0,
                    "txn_id": ask["txn_id"],
                    "ask": ask,
                    "open_url_title": "Yes or no",
                },
            )
        )
    return events


def notify_buy_queue_asks(
    cfg: dict[str, Any],
    txns: list[Transaction] | None,
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    from .notify import already_notified, send_alert

    results = []
    for ev in buy_queue_ask_events(cfg.get("buy_queue"), txns):
        if not dry_run and not already_notified(ev.key):
            try:
                from .buy_queue_ask import mint_ask

                ev.payload["open_url"] = mint_ask(ev.payload.get("ask") or {})
            except Exception:
                pass
        st = send_alert(ev, dry_run=dry_run, channel="pushover")
        results.append(
            {"kind": ev.kind, "key": ev.key, "status": st, "subject": ev.subject}
        )
    return results


def drop_buy_queue_row(cfg: dict[str, Any], row_id: str, *, reason: str) -> bool:
    """Manual yes: remove one row and log it. Does not rewrite the Astra table."""
    rid = str(row_id or "").strip()
    rows = list(cfg.get("buy_queue") or [])
    kept = [r for r in rows if str(r.get("id") or "") != rid]
    if len(kept) == len(rows) or not rid:
        return False
    replace_buy_queue(cfg, kept)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = buy_queue_pulled_path()
    with path.open("a") as f:
        f.write(
            json.dumps(
                {"id": rid, "pulled_at": ts, "reason": reason},
                separators=(",", ":"),
            )
            + "\n"
        )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return True
