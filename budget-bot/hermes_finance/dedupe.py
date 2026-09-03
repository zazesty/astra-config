"""NorCal statement/XLSX wins over overlapping live Plaid rows."""

from __future__ import annotations

from collections import defaultdict

from .models import Transaction

# Statement / spreadsheet imports for the CU
IMPORT_INSTITUTIONS = frozenset({"norcal", "1st-norcal"})
# Live Plaid Item institution slug(s)
PLAID_NORCAL_INSTITUTIONS = frozenset({"1st-northern-california-credit-union"})


def is_import_institution(inst: str | None) -> bool:
    return (inst or "").lower() in IMPORT_INSTITUTIONS


def is_plaid_norcal_institution(inst: str | None) -> bool:
    return (inst or "").lower() in PLAID_NORCAL_INSTITUTIONS


def apply_import_plaid_dedupe(txns: list[Transaction]) -> int:
    """Statement/XLSX NorCal is SSOT on (date, amount_cents); Plaid twin is excluded.

    Plaid remains the live fill-in for days (and PayPal) with no statement row.
    Reclaims previously excluded statement twins so spend follows the PDF/xlsx.
    Transfer-tagged statement rows stay transfers. Returns count of Plaid rows
    newly marked excluded.
    """
    plaid_by_key: dict[tuple[str, int], list[Transaction]] = defaultdict(list)
    for t in txns:
        if is_plaid_norcal_institution(t.institution):
            plaid_by_key[(t.date or "", int(t.amount_cents))].append(t)

    if not plaid_by_key:
        return 0

    newly = 0
    for t in txns:
        if not is_import_institution(t.institution):
            continue
        twins = plaid_by_key.get((t.date or "", int(t.amount_cents))) or []
        if not twins:
            continue
        if t.transfer:
            for p in twins:
                p.transfer = True
                if not p.excluded:
                    p.excluded = True
                    newly += 1
            continue
        if t.excluded:
            t.excluded = False
        for p in twins:
            if not p.excluded:
                p.excluded = True
                newly += 1
    return newly


def persist_statement_ssot() -> int:
    """Load store, apply statement-wins, save (including reclaimed imports)."""
    from .store import load_txns, save_txns

    rows = load_txns()
    n = apply_import_plaid_dedupe(rows)
    save_txns(rows)
    return n
