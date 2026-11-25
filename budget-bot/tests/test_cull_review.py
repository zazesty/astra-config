"""Tests for monthly spend cull review (synthetic fixtures only)."""

from datetime import date

from budget_bot.cull_review import build_cull_summary, cull_review_event
from budget_bot.models import Transaction


def _tx(tid: str, dt: str, cents: int, merch: str, cat: str = "Dining") -> Transaction:
    return Transaction(id=tid, date=dt, amount_cents=cents, name=merch, merchant_name=merch, category=cat)


def test_cull_summary_and_event():
    txns = [
        _tx("1", "2026-10-02", 1500, "Coffee Shop", "Dining"),
        _tx("2", "2026-10-08", 1200, "Coffee Shop", "Dining"),
        _tx("3", "2026-10-15", 1400, "Coffee Shop", "Dining"),
        _tx("4", "2026-10-20", 9000, "Cloud Sub", "Software"),
        _tx("5", "2026-09-20", 2000, "Cloud Sub", "Software"),
        _tx("6", "2026-10-05", 10000, "City Water", "Utilities"),
    ]
    cfg = {"bills": [{"name": "City Water", "match": "city water"}]}
    summary = build_cull_summary(txns, cfg["bills"], date(2026, 11, 5))
    assert summary["target_ym"] == "2026-10"
    assert summary["prev_ym"] == "2026-09"
    assert summary["repeat_drips"][0]["merchant"] == "Coffee Shop"
    assert summary["spikes"][0]["merchant"] == "Cloud Sub"

    ev = cull_review_event(txns, cfg, as_of=date(2026, 11, 5), use_llm=False)
    assert ev.kind == "cull_review"
    assert ev.key == "cull_review|2026-10"
    assert "Coffee Shop" in ev.body
    assert "Cloud Sub" in ev.body
