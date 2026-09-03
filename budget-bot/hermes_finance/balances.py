"""Cash on hand from cached Plaid balances — NorCal checking only."""

from __future__ import annotations

from typing import Any


def is_norcal_item(item: dict[str, Any] | None) -> bool:
    inst = str((item or {}).get("institution") or "").lower()
    return any(
        x in inst
        for x in ("norcal", "northern-california", "1st-nor", "credit-union")
    )


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
