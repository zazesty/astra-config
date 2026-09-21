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
