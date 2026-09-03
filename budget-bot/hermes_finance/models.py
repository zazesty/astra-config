"""Normalized transaction + alert types."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# amount_cents: POSITIVE = money out (counts toward hardcap spend)
# negative = money in; refunds net spend, true income does not.

_REFUND_NAME_RE = re.compile(
    r"("
    r"\brefunds?\b|\brefunded\b|"
    r"credit\s*voucher|"
    r"merchandise\s*credit|"
    r"charge.?backs?|"
    r"\breversals?\b|"
    r"adjustment.*credit|credit.*adjustment|"
    r"returned?\s+(item|purchase|merchandise)"
    r")",
    re.I,
)


@dataclass
class Transaction:
    id: str
    date: str  # YYYY-MM-DD
    amount_cents: int
    name: str
    category: str = "Misc / Other"
    merchant_name: str | None = None
    institution: str = "paypal"
    account_id: str = ""
    pending: bool = False
    transfer: bool = False  # internal transfer — exclude from spend
    excluded: bool = False  # force exclude

    def display_name(self) -> str:
        return (self.merchant_name or self.name or "unknown").strip()

    def looks_like_refund(self) -> bool:
        """True for merchant returns / credit vouchers (not payroll, sales, checks)."""
        if self.amount_cents >= 0:
            return False
        blob = f"{self.merchant_name or ''} {self.name or ''}"
        return bool(_REFUND_NAME_RE.search(blob))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Transaction:
        return cls(
            id=str(d["id"]),
            date=str(d["date"]),
            amount_cents=int(d["amount_cents"]),
            name=str(d.get("name") or d.get("merchant_name") or "unknown"),
            category=str(d.get("category") or "Misc / Other"),
            merchant_name=d.get("merchant_name"),
            institution=str(d.get("institution") or "paypal"),
            account_id=str(d.get("account_id") or ""),
            pending=bool(d.get("pending", False)),
            transfer=bool(d.get("transfer", False)),
            excluded=bool(d.get("excluded", False)),
        )


AlertKind = Literal[
    "digest",
    "hardcap_warn",
    "hardcap_breach",
    "pace_warn",
    "anomaly",
    "eom_leftover",
    "item_error",
    "info",
    "sync_break",
]


@dataclass
class AlertEvent:
    kind: AlertKind
    subject: str
    body: str
    key: str  # dedup key
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "body": self.body,
            "key": self.key,
            "payload": self.payload,
        }
