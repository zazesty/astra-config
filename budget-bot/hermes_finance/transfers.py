"""Detect internal transfers that must not count toward hardcap."""

from __future__ import annotations

import re
from typing import Any

# PayPal ↔ bank, CU savings/MM ↔ checking, generic transfer language (name/merchant).
_TRANSFER_RE = re.compile(
    r"("
    r"\bTRANSFER\b|\bXFER\b|\bTFR\b|"
    r"PAYPAL\s*(TRANSFER|INST\s*XFER|TO|FROM)|"
    r"(TO|FROM)\s*PAYPAL|"
    r"(DEPOSIT|WITHDRAWAL)\s+PAYPAL|"
    r"ONLINE\s*TRANSFER|"
    r"ACCT\s*TRANSFER|"
    r"INTERNAL\s*TRANSFER|"
    r"HOME\s*BANKING\s*TRANSFER|"
    r"MONEY\s*MARKET|\bMMKT\b|\bMMA\b|"
    r"SAVINGS\s*(TO|FROM)\s*CHECKING|"
    r"CHECKING\s*(TO|FROM)\s*SAVINGS|"
    r"SHARE\s*TRANSFER|"
    r"TRANSFER\s+(TO|FROM)\s+SHARE|"
    r"BETWEEN\s*ACCOUNTS|"
    r"CREDIT\s*UNION\s*TRANSFER|"
    r"MONEY\s*TRANSFER\s+(TO|FROM)"
    r")",
    re.I,
)

# 1st NorCal (and similar CUs): debit-card purchases often post as opaque
# "Debit Card MasterMoney Card" with Plaid primary=TRANSFER_OUT (LOW confidence).
# Those are real spend — do not trust Plaid TRANSFER* alone for them.
_DEBIT_CARD_PURCHASE_RE = re.compile(
    r"("
    r"debit\s*card\s*mastermoney|"
    r"mastermoney\s*card|"
    r"withdrawal\s*pos\s*#|"
    r"recurring\s*withdrawal\s*debit\s*card"
    r")",
    re.I,
)


def is_debit_card_purchase(*, name: str = "", merchant_name: str | None = None) -> bool:
    """True for CU debit-card / POS purchase lines (not internal account moves)."""
    blob = f"{merchant_name or ''} {name or ''}"
    return bool(_DEBIT_CARD_PURCHASE_RE.search(blob))


def looks_like_transfer(
    *,
    name: str = "",
    merchant_name: str | None = None,
    category: str = "",
    plaid_raw: dict[str, Any] | None = None,
) -> bool:
    """True if this should be excluded from spend (move money, not purchase)."""
    name_blob = f"{merchant_name or ''} {name or ''}"

    # Explicit transfer language on the name always wins (Share / Home Banking / PayPal ACH).
    if _TRANSFER_RE.search(name_blob):
        return True

    # ACH bill pay (CSAA etc.) is spend, not an internal transfer. Plaid often
    # labels it TRANSFER; "CO: CSAA INSURANCE" is the vendor, not PayPal.
    if re.search(r"\bCSAA\b|\bINSURANCE\b", name_blob, re.I):
        return False

    # Debit-card / POS purchases: ignore Plaid TRANSFER category (NorCal MasterMoney FP).
    if is_debit_card_purchase(name=name, merchant_name=merchant_name):
        return False

    if category and "transfer" in category.lower():
        return True

    raw = plaid_raw or {}
    # Plaid personal_finance_category
    pfc = raw.get("personal_finance_category") or {}
    if isinstance(pfc, dict):
        primary = str(pfc.get("primary") or "").upper()
        detailed = str(pfc.get("detailed") or "").upper()
        if "TRANSFER" in primary or "TRANSFER" in detailed:
            return True
    # Legacy category list
    cats = raw.get("category")
    if isinstance(cats, list):
        joined = " ".join(str(c) for c in cats).lower()
        if "transfer" in joined:
            return True
    # Plaid transaction_type / payment_meta
    ttype = str(raw.get("transaction_type") or raw.get("payment_channel") or "").lower()
    if ttype == "transfer":
        return True
    return False
