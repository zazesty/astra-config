"""Cash on hand from cached Plaid balances — NorCal checking only."""

from __future__ import annotations

from typing import Any

# Do not match bare "credit-union" / "cu" — that would treat Alliant as NorCal.
NORCAL_NEEDLES = (
    "norcal",
    "northern-california",
    "1st-nor",
    "1st nor",
)


def is_norcal_institution(institution: str | None) -> bool:
    inst = (institution or "").lower()
    return any(n in inst for n in NORCAL_NEEDLES)


def is_norcal_item(item: dict[str, Any] | None) -> bool:
    return is_norcal_institution(str((item or {}).get("institution") or ""))


def is_alliant_institution(institution: str | None) -> bool:
    return "alliant" in (institution or "").lower()


def is_alliant_item(item: dict[str, Any] | None) -> bool:
    return is_alliant_institution(str((item or {}).get("institution") or ""))


def cu_pile_id(item: dict[str, Any] | None) -> str | None:
    """norcal | alliant | None (PayPal and others are not bounce-cash)."""
    inst = str((item or {}).get("institution") or "")
    if is_norcal_institution(inst):
        return "norcal"
    if is_alliant_institution(inst):
        return "alliant"
    return None


CU_PILE_LABELS = {"norcal": "NorCal", "alliant": "Alliant"}


def is_cu_checking(acct: dict[str, Any], *, item: dict[str, Any] | None = None) -> bool:
    """True for a CU checking account (NorCal or Alliant). Not PayPal/savings/MM."""
    if item is not None and cu_pile_id(item) is None:
        return False
    t = str(acct.get("type") or "").lower()
    sub = str(acct.get("subtype") or "").lower()
    name = str(acct.get("name") or "").lower()
    if t in {"credit", "loan", "investment", "brokerage"}:
        return False
    if sub in {"savings", "money market", "cd", "paypal", "prepaid"}:
        return False
    if sub == "checking" or "checking" in name:
        return True
    return False


def is_norcal_checking(acct: dict[str, Any], *, item: dict[str, Any] | None = None) -> bool:
    """True only for 1st NorCal checking. PayPal/savings/MM never count (CSAA ACH)."""
    if item is not None and not is_norcal_item(item):
        return False
    t = str(acct.get("type") or "").lower()
    sub = str(acct.get("subtype") or "").lower()
    name = str(acct.get("name") or "").lower()
    if t in {"credit", "loan", "investment", "brokerage"}:
        return False
    if sub in {"savings", "money market", "cd", "paypal", "prepaid"}:
        return False
    if sub == "checking" or "checking" in name:
        return True
    return False


# Back-compat alias used by refresh debug prints / older tests.
def is_spendable_cash(acct: dict[str, Any], item: dict[str, Any] | None = None) -> bool:
    return is_norcal_checking(acct, item=item)


def cash_on_hand_cents(snapshot: dict[str, Any] | None) -> int | None:
    """NorCal checking available (else current). None if that account isn't cached."""
    if not snapshot:
        return None
    total = 0
    n = 0
    for item in snapshot.get("items") or []:
        if not is_norcal_item(item):
            continue
        for acct in item.get("accounts") or []:
            if not is_norcal_checking(acct, item=item):
                continue
            cents = acct.get("available_cents")
            if cents is None:
                cents = acct.get("current_cents")
            if cents is None:
                continue
            total += int(cents)
            n += 1
    if n == 0:
        return None
    return total


def checking_cash_by_cu(snapshot: dict[str, Any] | None) -> dict[str, int]:
    """Checking available (else current) per CU pile. Omits PayPal/savings/MM."""
    out: dict[str, int] = {}
    if not snapshot:
        return out
    for item in snapshot.get("items") or []:
        pile = cu_pile_id(item)
        if not pile:
            continue
        for acct in item.get("accounts") or []:
            if not is_cu_checking(acct, item=item):
                continue
            cents = acct.get("available_cents")
            if cents is None:
                cents = acct.get("current_cents")
            if cents is None:
                continue
            out[pile] = out.get(pile, 0) + int(cents)
    return out
