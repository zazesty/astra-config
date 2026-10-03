"""Detect recurring spend: must appear in current + previous calendar month.

Pinned decision (DECISIONS.md): cancel → drop immediately when the pair breaks.
Not used for hardcap math; coaching / on-box list only.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import state_dir
from .models import Transaction
from .rules import parse_ymd
from .store import load_txns


def normalize_merchant_key(t: Transaction) -> str:
    raw = (t.merchant_name or t.name or "unknown").upper().strip()
    # drop POS/REF noise prefixes
    raw = re.sub(r"^WITHDRAWAL\s+(POS\s*#\d+\s*-\s*|DEBIT CARD\s+)?", "", raw)
    raw = re.sub(r"^MASTERMONEY CARD(?:\s+REF#:\s*\S+(?:\s+\d{4})?)?\s*-\s*", "", raw)
    # trim store numbers / address-ish tails
    raw = re.sub(r"\s+#?\d{3,}.*$", "", raw)
    raw = re.sub(r"\s+\d{3,}\s+.*$", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:48] or "UNKNOWN"


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def prev_month(d: date) -> date:
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


@dataclass
class RecurringHit:
    merchant_key: str
    amount_cents_mode: int  # most common amount (or median-ish)
    months: list[str]
    n_txns: int
    category: str
    active: bool  # present in as_of month AND previous month
    sample_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant_key": self.merchant_key,
            "amount_cents_mode": self.amount_cents_mode,
            "months": self.months,
            "n_txns": self.n_txns,
            "category": self.category,
            "active": self.active,
            "sample_ids": self.sample_ids,
        }


def _mode_amount(amts: list[int]) -> int:
    bag: dict[int, int] = defaultdict(int)
    for a in amts:
        bag[a] += 1
    return max(bag.items(), key=lambda x: (x[1], -x[0]))[0]


def detect_recurring(
    txns: list[Transaction],
    as_of: date | None = None,
) -> list[RecurringHit]:
    """Return merchants with spend in ≥2 distinct months; flag active two-month pair."""
    if as_of is None:
        as_of = date.today()
    cur = _month_key(as_of)
    prev = _month_key(prev_month(as_of))

    # merchant -> month -> list of (amount, id, category)
    bag: dict[str, dict[str, list[tuple[int, str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for t in txns:
        if t.transfer or t.excluded or t.pending:
            continue
        if t.amount_cents <= 0:
            continue
        key = normalize_merchant_key(t)
        if key in ("UNKNOWN", "MASTERMONEY CARD", "POS"):
            continue
        # skip pure one-off opaque
        if re.match(r"^(POS\s*#|ATM)", key):
            continue
        ym = t.date[:7]
        bag[key][ym].append((t.amount_cents, t.id, t.category))

    hits: list[RecurringHit] = []
    for key, months_map in bag.items():
        months = sorted(months_map.keys())
        if len(months) < 2:
            continue
        all_amts: list[int] = []
        ids: list[str] = []
        cats: dict[str, int] = defaultdict(int)
        for ym, rows in months_map.items():
            for amt, tid, cat in rows:
                all_amts.append(amt)
                ids.append(tid)
                cats[cat] += 1
        cat = max(cats.items(), key=lambda x: x[1])[0] if cats else "Misc / Other"
        active = cur in months_map and prev in months_map
        hits.append(
            RecurringHit(
                merchant_key=key,
                amount_cents_mode=_mode_amount(all_amts),
                months=months,
                n_txns=len(all_amts),
                category=cat,
                active=active,
                sample_ids=ids[:6],
            )
        )
    hits.sort(key=lambda h: (-h.active, -len(h.months), h.merchant_key))
    return hits


def run_and_persist(as_of: date | None = None) -> dict[str, Any]:
    txns = load_txns()
    hits = detect_recurring(txns, as_of=as_of)
    active = [h for h in hits if h.active]
    dropped = [h for h in hits if not h.active]
    payload = {
        "as_of": (as_of or date.today()).isoformat(),
        "rule": "must appear in current + previous calendar month to be active",
        "active": [h.to_dict() for h in active],
        "history_multi_month": [h.to_dict() for h in dropped],
        "summary": {
            "active_n": len(active),
            "history_n": len(dropped),
            "multi_month_n": len(hits),
        },
    }
    out = state_dir() / "recurring.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    try:
        out.chmod(0o600)
    except OSError:
        pass
    return payload["summary"]


# iHerb autoship is every 1–6 months. Two same-amount posts at one of those
# gaps can become a Spotify-style bill, after a confirm. Not auto-enrolled.
INTERVAL_MONTHS = (1, 2, 3, 4, 5, 6)
SAME_AMOUNT_TOL_CENTS = 100  # ±$1, same slop as bill clear


def _fold_prefix(key: str, prefix: str) -> bool:
    if key == prefix:
        return True
    if not key.startswith(prefix):
        return False
    nxt = key[len(prefix) : len(prefix) + 1]
    return not nxt.isalnum()


def interval_merchant_key(t: Transaction) -> str:
    """Merchant key with order-id tails folded so one autoship is one vendor."""
    key = normalize_merchant_key(t)
    folds = (
        ("IHERB", "IHERB"),
        ("AMAZON PRIME", "AMAZON PRIME"),
        ("OPENROUTER", "OPENROUTER"),
        ("FASTRAK", "FASTRAK"),
        ("T-MOBILE", "T-MOBILE"),
        ("TMOBILE", "T-MOBILE"),
        ("NETFLIX", "NETFLIX"),
        ("VSP", "VSP"),
    )
    for prefix, folded in folds:
        if _fold_prefix(key, prefix):
            return folded
    return key


def _month_index(ym: str) -> int:
    year, month = ym.split("-")
    return int(year) * 12 + int(month) - 1


def _write_private_json(path: Any, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _bill_display_name(key: str) -> str:
    special = {
        "IHERB": "iHerb",
        "OPENROUTER": "OpenRouter",
        "FASTRAK": "FasTrak",
        "NETFLIX": "Netflix",
        "VSP": "VSP",
        "T-MOBILE": "T-Mobile",
        "AMAZON PRIME": "Amazon Prime",
    }
    return special.get(key, key.title())


def _pile_institution(raw: str) -> str:
    """CU pile for cash-vs-bills. A non-CU rail (PayPal) follows the Alliant default."""
    text = (raw or "").lower()
    if "alliant" in text:
        return "alliant"
    if "norcal" in text or "nor cal" in text:
        return "norcal"
    return "alliant"


def _covered_by_bill(key: str, sample: list[Transaction], bills: list[dict[str, Any]] | None) -> bool:
    from .rules import _bill_name_re

    blobs = [key]
    for txn in sample[:4]:
        blobs.append(f"{txn.merchant_name or ''} {txn.name or ''}")
    for bill in bills or []:
        cre = _bill_name_re(bill)
        if cre is None:
            continue
        if any(cre.search(blob) for blob in blobs):
            return True
    return False


def _amount_clusters(posts: list[Transaction], tol: int) -> list[list[Transaction]]:
    """Group charges within ±tol of the cluster's mode amount."""
    remaining = sorted(posts, key=lambda txn: (txn.amount_cents, txn.date, txn.id))
    clusters: list[list[Transaction]] = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        changed = True
        while changed:
            changed = False
            center = _mode_amount([txn.amount_cents for txn in group])
            still: list[Transaction] = []
            for txn in remaining:
                if abs(txn.amount_cents - center) <= tol:
                    group.append(txn)
                    changed = True
                else:
                    still.append(txn)
            remaining = still
        clusters.append(group)
    return clusters


def _one_delivery_per_month(posts: list[Transaction]) -> list[Transaction] | None:
    """One charge per month. Same-day duplicates and a 3-day settlement pair collapse.

    Two charges more than 3 days apart in the same month are not one autoship.
    """
    by_month: dict[str, list[Transaction]] = defaultdict(list)
    for txn in posts:
        by_month[txn.date[:7]].append(txn)
    picked: list[Transaction] = []
    for rows in by_month.values():
        rows = sorted(rows, key=lambda txn: (txn.date, txn.id))
        seen_day: set[str] = set()
        unique: list[Transaction] = []
        for txn in rows:
            day = txn.date[:10]
            if day in seen_day:
                continue
            seen_day.add(day)
            unique.append(txn)
        first = parse_ymd(unique[0].date)
        if any((parse_ymd(txn.date) - first).days > 3 for txn in unique):
            return None
        picked.append(unique[0])
    picked.sort(key=lambda txn: txn.date)
    return picked


def detect_interval_candidates(
    txns: list[Transaction],
    bills: list[dict[str, Any]] | None = None,
    as_of: date | None = None,
    *,
    amount_tol_cents: int = SAME_AMOUNT_TOL_CENTS,
) -> list[dict[str, Any]]:
    """Same vendor, same amount, constant gap of 1–6 months.

    A candidate is not a bill yet. Varying baskets (a merchant with more
    off-amount charges than cadence posts) are skipped. A monthly series
    must still have posted this month or last month. Merchants that already
    match a `bills[]` row are skipped.
    """
    if as_of is None:
        as_of = date.today()
    tol = max(0, int(amount_tol_cents))
    bag: dict[str, list[Transaction]] = defaultdict(list)
    for txn in txns:
        if txn.transfer or txn.excluded or txn.pending:
            continue
        if txn.amount_cents <= 0:
            continue
        key = interval_merchant_key(txn)
        if key in ("UNKNOWN", "MASTERMONEY CARD", "POS"):
            continue
        if re.match(r"^(POS\s*#|ATM)", key):
            continue
        bag[key].append(txn)

    found: list[dict[str, Any]] = []
    cur_idx = _month_index(_month_key(as_of))
    for key, posts in bag.items():
        if _covered_by_bill(key, posts, bills):
            continue
        for cluster in _amount_clusters(posts, tol):
            picked = _one_delivery_per_month(cluster)
            if not picked or len(picked) < 2:
                continue
            idxs = [_month_index(txn.date[:7]) for txn in picked]
            gaps = [b - a for a, b in zip(idxs, idxs[1:])]
            interval = gaps[0]
            if interval not in INTERVAL_MONTHS or any(gap != interval for gap in gaps):
                continue
            if interval == 1 and idxs[-1] < cur_idx - 1:
                continue
            picked_ids = {txn.id for txn in picked}
            # Same-day duplicates of the cadence amount are not "other" charges.
            extra = 0
            for txn in posts:
                if txn.id in picked_ids:
                    continue
                if any(
                    txn.date[:10] == kept.date[:10]
                    and abs(txn.amount_cents - kept.amount_cents) <= tol
                    for kept in picked
                ):
                    continue
                extra += 1
            if extra > len(picked):
                continue
            amount = _mode_amount([txn.amount_cents for txn in picked])
            days = sorted(parse_ymd(txn.date).day for txn in picked)
            day = days[len(days) // 2]
            latest = picked[-1]
            anchor = picked[0].date[:7]
            slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:32] or "bill"
            found.append(
                {
                    "id": f"{slug}-{interval}m-{amount}",
                    "name": _bill_display_name(key),
                    "merchant_key": key,
                    "match": re.escape(key),
                    "amount_cents": amount,
                    "interval_months": interval,
                    "anchor": anchor,
                    "day_of_month": day,
                    "months": [txn.date[:7] for txn in picked],
                    "institution": _pile_institution(latest.institution),
                    "sample_ids": [txn.id for txn in picked[:6]],
                }
            )
    found.sort(key=lambda row: (row["interval_months"], row["merchant_key"], row["amount_cents"]))
    return found


def interval_candidates_path():
    return state_dir() / "interval-candidates.json"


def load_interval_candidate_doc() -> dict[str, Any]:
    path = interval_candidates_path()
    if not path.is_file():
        return {"open": [], "dismissed": [], "enrolled": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"open": [], "dismissed": [], "enrolled": []}
    if not isinstance(data, dict):
        return {"open": [], "dismissed": [], "enrolled": []}
    return data


def persist_interval_candidates(
    txns: list[Transaction] | None = None,
    bills: list[dict[str, Any]] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Refresh the confirm queue. Dismissed and enrolled ids stay dismissed."""
    if txns is None:
        txns = load_txns()
    if bills is None:
        from .config import load_config

        bills = load_config().get("bills") or []
    if as_of is None:
        as_of = date.today()
    prev = load_interval_candidate_doc()
    held = set(prev.get("dismissed") or []) | set(prev.get("enrolled") or [])
    rows = [
        row
        for row in detect_interval_candidates(txns, bills, as_of=as_of)
        if row["id"] not in held
    ]
    payload = {
        "as_of": as_of.isoformat(),
        "rule": (
            "same merchant, same amount (±$1), one delivery a month, "
            "constant gap of 1–6 months; confirm before enroll"
        ),
        "open": rows,
        "dismissed": sorted(prev.get("dismissed") or []),
        "enrolled": sorted(prev.get("enrolled") or []),
    }
    _write_private_json(interval_candidates_path(), payload)
    return {"open_n": len(rows), "open_ids": [row["id"] for row in rows]}


def _set_candidate_hold(candidate_id: str, field: str) -> dict[str, Any]:
    doc = load_interval_candidate_doc()
    rows = [row for row in (doc.get("open") or []) if row.get("id") != candidate_id]
    found = len(rows) != len(doc.get("open") or [])
    if not found:
        known = {row.get("id") for row in (doc.get("open") or [])}
        known |= set(doc.get("dismissed") or [])
        known |= set(doc.get("enrolled") or [])
        if candidate_id not in known:
            return {"ok": False, "error": "no such candidate"}
    held = [item for item in (doc.get(field) or []) if item != candidate_id]
    held.append(candidate_id)
    doc["open"] = rows
    doc[field] = held
    _write_private_json(interval_candidates_path(), doc)
    return {"ok": True, "id": candidate_id}


def dismiss_interval_candidate(candidate_id: str) -> dict[str, Any]:
    return _set_candidate_hold(candidate_id, "dismissed")


def enroll_interval_candidate(candidate_id: str) -> dict[str, Any]:
    """Append the queued row to `bills[]`. Full cash pull on its cadence."""
    from .config import load_config, save_config

    doc = load_interval_candidate_doc()
    row = next((item for item in (doc.get("open") or []) if item.get("id") == candidate_id), None)
    if row is None:
        return {"ok": False, "error": "no such candidate"}
    cfg = load_config()
    bills = cfg.setdefault("bills", [])
    name = str(row.get("name") or "")
    if any(str(bill.get("name") or "") == name for bill in bills):
        return {"ok": False, "error": "bill already exists"}
    bills.append(
        {
            "name": name,
            "match": row["match"],
            "amount_cents": int(row["amount_cents"]),
            "interval_months": int(row["interval_months"]),
            "anchor": row["anchor"],
            "day_of_month": int(row["day_of_month"]),
            "institution": row.get("institution") or "alliant",
        }
    )
    save_config(cfg)
    held = _set_candidate_hold(candidate_id, "enrolled")
    return {"ok": True, "name": name, "interval_months": row["interval_months"], **held}
