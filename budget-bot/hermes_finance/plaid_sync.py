"""Pull Plaid transactions into Hermes store."""

from __future__ import annotations

import json
import re
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import state_dir
from .models import Transaction
from .plaid_client import accounts_get, transactions_sync
from .store import load_balances, load_txns, remove_txns, save_balances, upsert_txns


def _tokens() -> list[dict[str, Any]]:
    idx = state_dir() / "tokens" / "items.json"
    if not idx.is_file():
        return []
    return json.loads(idx.read_text())


def list_items() -> list[dict[str, Any]]:
    """Public: linked Item headers (no access tokens)."""
    return list(_tokens())


def save_items(items: list[dict[str, Any]]) -> None:
    idx = state_dir() / "tokens" / "items.json"
    idx.parent.mkdir(parents=True, exist_ok=True)
    tmp = idx.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(idx)


def set_item_flags(
    item_id: str,
    *,
    quarantine: bool | None = None,
    quarantine_reason: str | None = None,
    amount_unit: str | None = None,
) -> dict[str, Any] | None:
    """Update per-Item flags in items.json. amount_unit: 'dollars' (default) | 'cents'."""
    items = _tokens()
    match = None
    for i in items:
        if i.get("item_id") == item_id:
            match = i
            break
    if not match:
        return None
    if quarantine is not None:
        match["quarantine"] = bool(quarantine)
        if quarantine:
            match["quarantine_since"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if quarantine_reason:
                match["quarantine_reason"] = quarantine_reason
        else:
            match.pop("quarantine_reason", None)
            match["promoted_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
    if amount_unit is not None:
        if amount_unit not in ("dollars", "cents"):
            raise ValueError("amount_unit must be dollars|cents")
        match["amount_unit"] = amount_unit
    save_items(items)
    return match


def item_is_quarantined(item: dict[str, Any]) -> bool:
    return bool(item.get("quarantine"))


REPAIR_GRACE_HOURS = 6


def mark_item_repaired(item_id: str) -> dict[str, Any] | None:
    """Stamp a successful update-mode Link. Do not sync immediately after."""
    items = _tokens()
    match = next((i for i in items if i.get("item_id") == item_id), None)
    if not match:
        return None
    match["repaired_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_items(items)
    return match


def item_repair_grace_active(item_id: str, *, hours: int | None = None) -> bool:
    """True if this Item was update-mode repaired within the grace window."""
    hours = REPAIR_GRACE_HOURS if hours is None else hours
    match = next((i for i in _tokens() if i.get("item_id") == item_id), None)
    if not match:
        return False
    raw = match.get("repaired_at")
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age.total_seconds() < max(0, hours) * 3600


def load_access_token(item: dict[str, Any]) -> str:
    p = state_dir() / "tokens" / item["token_file"]
    return json.loads(p.read_text())["access_token"]


def cursor_path(item_id: str) -> Path:
    return state_dir() / "tokens" / f"{item_id}.cursor"


def quarantine_dir() -> Path:
    d = state_dir() / "quarantine"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def normalize_amount_unit(amount_unit: str | None) -> str:
    """Return dollars|cents; raise on garbage (never silent-fallback)."""
    unit = (amount_unit or "dollars").lower().strip()
    if unit not in ("dollars", "cents"):
        raise ValueError(f"amount_unit must be dollars|cents, got {amount_unit!r}")
    return unit


def plaid_amount_to_cents(amount: Any, amount_unit: str | None = "dollars") -> int:
    """Same dollars|*100 vs cents|*1 knob as txns. If NorCal 100× flips again, flip Item amount_unit."""
    unit = normalize_amount_unit(amount_unit)
    val = float(amount)
    if unit == "cents":
        return int(round(val))
    return int(round(val * 100))


def norcal_balance_unit(raw_accounts: list[dict[str, Any]]) -> str:
    """Detect NorCal Accounts API still emitting cents-as-dollars (100×).

    Txn `amount_unit` can be dollars while balances stay 100×. Share ~500 / MM ~36
    → treat balances as cents. Share ~$5 / MM ~$0.36 → they fixed it; use dollars.
    """
    saw_100x = False
    saw_fixed = False
    for raw in raw_accounts or []:
        name = str(raw.get("name") or raw.get("official_name") or "").lower()
        cur = (raw.get("balances") or {}).get("current")
        if cur is None:
            continue
        cur_f = float(cur)
        if "share" in name or "prime" in name:
            if 200 <= cur_f <= 800:
                saw_100x = True
            elif 2 <= cur_f <= 15:
                saw_fixed = True
        if "money market" in name or "1st class" in name:
            if 15 <= cur_f <= 80:
                saw_100x = True
            elif 0.15 <= cur_f <= 2.0:
                saw_fixed = True
    if saw_100x and not saw_fixed:
        return "cents"
    if saw_fixed and not saw_100x:
        return "dollars"
    # Inconclusive: current observed feed is still 100×.
    return "cents"


def plaid_txn_to_hermes(
    raw: dict[str, Any],
    institution: str,
    *,
    amount_unit: str = "dollars",
) -> Transaction | None:
    """Map one Plaid txn → Hermes. Returns None if amount missing (skip, don't crash)."""
    from .import_xlsx import guess_merchant
    from .transfers import is_debit_card_purchase, looks_like_transfer

    # Plaid: positive amount = money out for depository (we use positive = spend)
    amount = raw.get("amount")
    if amount is None:
        return None
    # Normal: amount is dollars → store cents (*100).
    # Broken FI (NorCal): amount field looks like cents already → unit=cents (*1).
    amount_cents = plaid_amount_to_cents(amount, amount_unit)
    # Prefer original_description — NorCal MasterMoney often has MCC+merchant only there
    original = (raw.get("original_description") or "").strip()
    plaid_name = (raw.get("name") or "").strip()
    merchant = raw.get("merchant_name")
    # Build a rich name when Plaid collapses to opaque "MasterMoney Card"
    if original and (
        not plaid_name
        or is_debit_card_purchase(name=plaid_name, merchant_name=str(merchant or ""))
        or re.search(r"^mastermoney|^withdrawal debit card mastermoney", plaid_name, re.I)
    ):
        if plaid_name and original.lower() not in plaid_name.lower():
            name = f"{plaid_name} - {original}" if plaid_name else original
        else:
            name = original or plaid_name or "unknown"
    else:
        name = plaid_name or original or raw.get("merchant_name") or "unknown"
    if not merchant:
        guessed = guess_merchant(str(name))
        if guessed and not re.match(
            r"^(POS\s*#\d+|MASTERMONEY CARD.*|WITHDRAWAL DEBIT CARD MASTERMONEY.*)$",
            guessed,
            re.I,
        ):
            merchant = guessed
    pending = bool(raw.get("pending"))
    d = (raw.get("date") or raw.get("authorized_date") or date.today().isoformat())[:10]
    cats = raw.get("category") or raw.get("personal_finance_category") or {}
    if isinstance(cats, dict):
        category = cats.get("primary") or cats.get("detailed") or "Misc / Other"
    elif isinstance(cats, list) and cats:
        category = str(cats[0])
    else:
        category = "Misc / Other"
    transfer = looks_like_transfer(
        name=str(name),
        merchant_name=str(merchant) if merchant else None,
        category=str(category),
        plaid_raw=raw,
    )
    # Belt-and-suspenders: never treat CU debit-card purchases as transfers
    if is_debit_card_purchase(name=str(name), merchant_name=str(merchant or "")):
        transfer = False
    return Transaction(
        id="plaid-" + str(raw.get("transaction_id") or raw.get("pending_transaction_id")),
        date=d,
        amount_cents=amount_cents,
        name=str(name)[:200],
        merchant_name=str(merchant)[:80] if merchant else None,
        category=str(category)[:80],
        institution=institution,
        account_id=str(raw.get("account_id") or ""),
        pending=pending,
        transfer=transfer,
        excluded=False,
    )


def _removed_to_hermes_ids(removed_raw: list[Any]) -> list[str]:
    ids: list[str] = []
    for raw in removed_raw or []:
        if isinstance(raw, dict):
            tid = raw.get("transaction_id") or raw.get("pending_transaction_id")
        else:
            tid = raw
        if tid:
            ids.append("plaid-" + str(tid))
    return ids


def refresh_item_balances(item: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch /accounts/get and merge into balances.json. Fail-open (keep prior row)."""
    txn_unit = normalize_amount_unit(item.get("amount_unit"))
    inst = str(item.get("institution") or "").lower()
    norcalish = any(
        x in inst
        for x in ("norcal", "northern-california", "1st-nor", "credit-union")
    )
    try:
        resp = accounts_get(load_access_token(item))
    except Exception:
        return None
    raw_accounts = list(resp.get("accounts") or [])
    unit = norcal_balance_unit(raw_accounts) if norcalish else txn_unit
    accounts: list[dict[str, Any]] = []
    for raw in raw_accounts:
        bals = raw.get("balances") or {}
        avail = bals.get("available")
        cur = bals.get("current")
        accounts.append(
            {
                "account_id": raw.get("account_id"),
                "name": raw.get("name") or raw.get("official_name"),
                "mask": raw.get("mask"),
                "type": raw.get("type"),
                "subtype": raw.get("subtype"),
                "available_cents": (
                    None if avail is None else plaid_amount_to_cents(avail, unit)
                ),
                "current_cents": (
                    None if cur is None else plaid_amount_to_cents(cur, unit)
                ),
            }
        )
    row = {
        "item_id": item.get("item_id"),
        "institution": item.get("institution"),
        "amount_unit": txn_unit,
        "balance_unit": unit,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "accounts": accounts,
    }
    snap = load_balances()
    items = [i for i in (snap.get("items") or []) if i.get("item_id") != row["item_id"]]
    items.append(row)
    save_balances(
        {
            "updated": row["fetched_at"],
            "items": items,
        }
    )
    return row


def _refresh_balances_quiet(item: dict[str, Any]) -> None:
    try:
        refresh_item_balances(item)
    except Exception:
        return


def write_cursor(item_id: str, cursor: str) -> None:
    """Persist Plaid sync cursor after store writes succeed."""
    if not cursor:
        return
    cp = cursor_path(item_id)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(cursor + "\n")
    try:
        cp.chmod(0o600)
    except OSError:
        pass


def _sync_one(
    item: dict[str, Any],
    *,
    persist_cursor: bool = False,
    start_cursor: str | None = None,
) -> tuple[dict[str, Any], list[Transaction], list[str], str]:
    """Fetch one Item's txn delta.

    Returns (meta, batch, removed_hermes_ids, next_cursor).

    Default **does not** write the cursor — callers must upsert/remove first, then
    ``write_cursor`` so a failed store write cannot advance past lost data.
    Set ``persist_cursor=True`` only for rare debug paths.
    """
    item_id = item["item_id"]
    inst = item.get("institution") or "paypal"
    amount_unit = normalize_amount_unit(item.get("amount_unit"))
    access = load_access_token(item)
    cursor = ""
    cp = cursor_path(item_id)
    if start_cursor is not None:
        cursor = start_cursor
    elif cp.is_file():
        cursor = cp.read_text().strip()
    added = modified = removed = 0
    skipped_null_amount = 0
    batch: list[Transaction] = []
    removed_ids: list[str] = []
    has_more = True
    while has_more:
        resp = transactions_sync(access, cursor)
        for raw in resp.get("added") or []:
            t = plaid_txn_to_hermes(raw, inst, amount_unit=amount_unit)
            if t is None:
                skipped_null_amount += 1
                continue
            batch.append(t)
            added += 1
        # re-upsert modified too so merchant/pending flips stick
        for raw in resp.get("modified") or []:
            t = plaid_txn_to_hermes(raw, inst, amount_unit=amount_unit)
            if t is None:
                skipped_null_amount += 1
                continue
            batch.append(t)
            modified += 1
        rem_batch = _removed_to_hermes_ids(list(resp.get("removed") or []))
        removed_ids.extend(rem_batch)
        removed += len(rem_batch)
        cursor = resp.get("next_cursor") or cursor
        has_more = bool(resp.get("has_more"))
    if persist_cursor and cursor:
        write_cursor(item_id, cursor)
    meta = {
        "item_id": item_id,
        "institution": inst,
        "added": added,
        "modified": modified,
        "removed": removed,
        "skipped_null_amount": skipped_null_amount,
        "next_cursor_len": len(cursor or ""),
        "amount_unit": amount_unit,
        "quarantine": item_is_quarantined(item),
    }
    return meta, batch, removed_ids, cursor


def _baseline_median_cents(institution_hints: list[str]) -> float | None:
    """Median |amount_cents| from existing store for institutions matching hints."""
    hints = [h.lower() for h in institution_hints if h]
    amts: list[int] = []
    for t in load_txns():
        inst = (t.institution or "").lower()
        if hints and not any(h in inst or inst in h for h in hints):
            continue
        if t.amount_cents:
            amts.append(abs(int(t.amount_cents)))
    if len(amts) < 5:
        return None
    return float(statistics.median(amts))


def assess_scale(
    batch: list[Transaction],
    *,
    institution: str,
    balances: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Heuristic: flag if Plaid amounts look 100× too big/small vs import baseline.

    Plaid amounts are dollars → we store cents (*100). A broken FI feed that
    already emits cents would land ~100× high (user: 'cents shown as dollars').
    """
    inst_l = (institution or "").lower()
    if any(x in inst_l for x in ("norcal", "nor cal", "1st-nor", "credit-union", "cu")):
        hints = [institution, "norcal", "1st-norcal", "1st nor"]
    else:
        hints = [institution] if institution else []
    base = _baseline_median_cents(hints)
    # CU-shaped Item with no import baseline yet: fall back to known NorCal imports
    if base is None and any(x in inst_l for x in ("nor", "cal", "credit", "union")):
        base = _baseline_median_cents(["norcal", "1st-norcal"])
    amts = [abs(t.amount_cents) for t in batch if t.amount_cents]
    med = float(statistics.median(amts)) if amts else None
    ratio = (med / base) if (med and base and base > 0) else None
    verdict = "unknown"
    notes: list[str] = []
    if ratio is None:
        notes.append("need ≥5 baseline import txns and ≥1 plaid amt for scale check")
    elif 0.4 <= ratio <= 2.5:
        verdict = "ok"
        notes.append(f"median plaid {med:.0f}¢ ≈ baseline {base:.0f}¢ (ratio {ratio:.2f})")
    elif ratio >= 40:
        verdict = "likely_100x_high"
        notes.append(
            f"median plaid {med:.0f}¢ vs baseline {base:.0f}¢ (ratio {ratio:.1f}) — "
            "FI may emit cents already; do NOT promote without fix"
        )
    elif ratio <= 0.025:
        verdict = "likely_100x_low"
        notes.append(
            f"median plaid {med:.0f}¢ vs baseline {base:.0f}¢ (ratio {ratio:.4f}) — "
            "amounts may be dollars stored as cents"
        )
    else:
        verdict = "suspect"
        notes.append(f"median plaid {med:.0f}¢ vs baseline {base:.0f}¢ (ratio {ratio:.2f})")

    bal_notes: list[dict[str, Any]] = []
    # Known NorCal statement anchors (July 2026 end balances) — cents-as-dollars
    # shows exactly 100× on share + MM; checking moves so only flag if ~100× of recent.
    norcalish = any(
        x in inst_l for x in ("norcal", "nor cal", "1st-nor", "northern-california", "credit-union")
    )
    bal_100x_hits = 0
    bal_checked = 0
    if balances:
        for b in balances:
            cur = b.get("balances", {}).get("current")
            avail = b.get("balances", {}).get("available")
            name = b.get("name") or b.get("official_name") or b.get("account_id")
            name_l = str(name).lower()
            bal_notes.append(
                {
                    "name": name,
                    "type": b.get("type"),
                    "subtype": b.get("subtype"),
                    "current": cur,
                    "available": avail,
                    "mask": b.get("mask"),
                    "as_dollars_if_div100": (
                        None if cur is None else round(float(cur) / 100.0, 2)
                    ),
                }
            )
            if cur is None:
                continue
            cur_f = float(cur)
            # Anchor matches (exact 100× of known statement cents-as-dollars)
            if norcalish:
                bal_checked += 1
                if "share" in name_l or "prime" in name_l:
                    if abs(cur_f - 500) < 0.01 or abs(cur_f / 100 - 5.0) < 0.02:
                        bal_100x_hits += 1
                        notes.append(
                            f"balance {name} current={cur} → /100=${cur_f/100:.2f} "
                            "(matches ~$5 share savings = 100× high)"
                        )
                if "money market" in name_l or "mm" in name_l:
                    if abs(cur_f - 36) < 0.5 or (0.30 <= cur_f / 100 <= 0.50):
                        bal_100x_hits += 1
                        notes.append(
                            f"balance {name} current={cur} → /100=${cur_f/100:.2f} "
                            "(matches ~$0.36 MM = 100× high)"
                        )
                if "check" in name_l:
                    # plausible real checking $20–$400; 100× would show $2000–$40000
                    if 2000 <= cur_f <= 50000:
                        notes.append(
                            f"balance {name} current=${cur_f:.0f} "
                            f"(/100=${cur_f/100:.2f}) — plausible 100× if true bal ~${cur_f/100:.0f}"
                        )
                        bal_100x_hits += 1

    # Balance-only verdict when no txns yet
    if ratio is None and bal_100x_hits >= 2:
        verdict = "likely_100x_high"
        notes.append(
            f"balance anchors: {bal_100x_hits}/{bal_checked} look 100× high — "
            "hold promote; set amount_unit=cents if txns match"
        )
    elif ratio is None and bal_checked and bal_100x_hits == 0 and not batch:
        notes.append("balances present but no strong 100× anchor match; wait for txns")

    return {
        "verdict": verdict,
        "plaid_median_cents": med,
        "baseline_median_cents": base,
        "ratio": ratio,
        "txn_n": len(batch),
        "balance_100x_hits": bal_100x_hits,
        "notes": notes,
        "balances": bal_notes,
        "sample_txns": [
            {
                "date": t.date,
                "amount_cents": t.amount_cents,
                "name": (t.name or "")[:80],
            }
            for t in sorted(batch, key=lambda x: x.date, reverse=True)[:12]
        ],
    }


def preview_item(item_id: str | None = None) -> dict[str, Any]:
    """Sync without writing cursor or txns store; write quarantine report.

    Use after a fresh NorCal (or any) Link to judge scale corruption before promote.
    """
    items = _tokens()
    if item_id:
        items = [i for i in items if i.get("item_id") == item_id]
    if not items:
        return {"error": "no_items", "item_id": item_id}

    reports: list[dict[str, Any]] = []
    for item in items:
        iid = item["item_id"]
        inst = item.get("institution") or "unknown"
        access = load_access_token(item)
        balances: list[dict[str, Any]] = []
        bal_err = None
        try:
            bal_resp = accounts_get(access)
            balances = list(bal_resp.get("accounts") or [])
        except Exception as e:  # noqa: BLE001
            bal_err = str(e)[:300]

        # Full history preview: empty cursor, do not persist cursor or store
        meta, batch, _removed, _cursor = _sync_one(
            item, persist_cursor=False, start_cursor=""
        )
        scale = assess_scale(batch, institution=inst, balances=balances)
        report = {
            "item_id": iid,
            "institution": inst,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta,
            "scale": scale,
            "balance_error": bal_err,
            "promote_ok": scale.get("verdict") == "ok",
        }
        out = quarantine_dir() / f"preview-{iid[:12]}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        # never write access tokens
        safe_batch = [t.to_dict() for t in batch[:500]]
        payload = {**report, "txns_sample_n": min(500, len(batch)), "txns_sample": safe_batch}
        out.write_text(json.dumps(payload, indent=2) + "\n")
        try:
            out.chmod(0o600)
        except OSError:
            pass
        report["quarantine_path"] = str(out)
        report["txn_count"] = len(batch)
        reports.append(report)

    return {
        "items": reports,
        "hint": "If verdict=ok, run plaid-sync for that item. If likely_100x_*, keep quarantined; do not promote.",
    }


def sync_item(item_id: str, *, force: bool = False) -> dict[str, Any]:
    """Sync a single Item by id. Returns same shape as sync_all_items (one entry).

    Quarantined Items are skipped unless force=True (use after scale OK / amount_unit set).
    """
    summary: dict[str, Any] = {
        "items": [],
        "added": 0,
        "modified": 0,
        "removed": 0,
        "new_txn_ids": [],
    }
    match = next((i for i in _tokens() if i.get("item_id") == item_id), None)
    if not match:
        summary["error"] = f"unknown_item:{item_id}"
        summary["upserted_new"] = 0
        summary["upserted_total_batch"] = 0
        return summary
    if item_is_quarantined(match) and not force:
        summary["skipped_quarantine"] = True
        summary["items"].append(
            {
                "item_id": item_id,
                "institution": match.get("institution"),
                "skipped": "quarantine",
                "reason": match.get("quarantine_reason"),
            }
        )
        summary["upserted_new"] = 0
        summary["upserted_total_batch"] = 0
        summary["removed_applied"] = 0
        return summary
    if (not force) and item_repair_grace_active(item_id):
        summary["skipped_repair_grace"] = True
        summary["items"].append(
            {
                "item_id": item_id,
                "institution": match.get("institution"),
                "skipped": "repair_grace",
            }
        )
        summary["upserted_new"] = 0
        summary["upserted_total_batch"] = 0
        summary["removed_applied"] = 0
        return summary
    meta, batch, removed_ids, next_cursor = _sync_one(match, persist_cursor=False)
    # Store mutations first, then cursor — never advance past uncommitted data.
    removed_n = remove_txns(removed_ids) if removed_ids else 0
    summary["removed_applied"] = removed_n
    if batch:
        _, new = upsert_txns(batch)
        from .dedupe import persist_statement_ssot

        persist_statement_ssot()
        live = {
            t.id
            for t in load_txns()
            if not t.excluded and not t.transfer
        }
        # Only brand-new spend txns drive per-txn pace buzz
        new_spend = [
            t
            for t in new
            if t.id in live and t.amount_cents > 0 and not t.pending
        ]
        summary["new_txn_ids"] = [t.id for t in new_spend]
        summary["upserted_new"] = len(new)
        summary["upserted_total_batch"] = len(batch)
    else:
        summary["upserted_new"] = 0
        summary["upserted_total_batch"] = 0
    write_cursor(item_id, next_cursor)
    _refresh_balances_quiet(match)
    summary["items"].append(meta)
    summary["added"] = meta["added"]
    summary["modified"] = meta["modified"]
    summary["removed"] = meta["removed"]
    return summary


def sync_all_items(*, include_quarantine: bool = False) -> dict[str, Any]:
    """Sync all linked Items. Quarantined skipped unless include_quarantine=True."""
    summary: dict[str, Any] = {
        "items": [],
        "added": 0,
        "modified": 0,
        "removed": 0,
        "removed_applied": 0,
        "new_txn_ids": [],
        "skipped_quarantine": [],
        "skipped_repair_grace": [],
    }
    summary.setdefault("failed_items", [])
    for item in _tokens():
        iid = str(item.get("item_id") or "")
        if iid and item_repair_grace_active(iid):
            summary["skipped_repair_grace"].append(
                {
                    "item_id": iid,
                    "institution": item.get("institution"),
                    "reason": "repair_grace",
                }
            )
            continue
        if item_is_quarantined(item) and not include_quarantine:
            summary["skipped_quarantine"].append(
                {
                    "item_id": item.get("item_id"),
                    "institution": item.get("institution"),
                    "reason": item.get("quarantine_reason"),
                }
            )
            continue
        # Per-item commit: remove → upsert → cursor (same durability as sync_item)
        iid = item["item_id"]
        try:
            meta, batch, removed_ids, next_cursor = _sync_one(item, persist_cursor=False)
        except Exception as e:
            err = str(e)[:500]
            summary["failed_items"].append(
                {
                    "item_id": iid,
                    "institution": item.get("institution"),
                    "error": err,
                }
            )
            summary["items"].append(
                {
                    "item_id": iid,
                    "institution": item.get("institution"),
                    "error": err,
                    "added": 0,
                    "modified": 0,
                    "removed": 0,
                }
            )
            continue
        removed_n = remove_txns(removed_ids) if removed_ids else 0
        summary["removed_applied"] += removed_n
        if batch:
            _, new = upsert_txns(batch)
            from .dedupe import persist_statement_ssot

            persist_statement_ssot()
            live = {
                t.id
                for t in load_txns()
                if not t.excluded and not t.transfer
            }
            new_spend = [
                t
                for t in new
                if t.id in live and t.amount_cents > 0 and not t.pending
            ]
            summary["new_txn_ids"].extend(t.id for t in new_spend)
            summary["upserted_new"] = summary.get("upserted_new", 0) + len(new)
            summary["upserted_total_batch"] = (
                summary.get("upserted_total_batch", 0) + len(batch)
            )
        write_cursor(iid, next_cursor)
        _refresh_balances_quiet(item)
        summary["items"].append(meta)
        summary["added"] += meta["added"]
        summary["modified"] += meta["modified"]
        summary["removed"] += meta["removed"]
    if "upserted_new" not in summary:
        summary["upserted_new"] = 0
        summary["upserted_total_batch"] = 0
    return summary
