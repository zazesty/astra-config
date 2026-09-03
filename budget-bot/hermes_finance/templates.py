"""Email / console copy — direct, no-fluff."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import AlertEvent, Transaction
    from .rules import BudgetSnapshot


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


def money_dollars(cents: int) -> str:
    """Nearest whole dollar for short push copy."""
    sign = "-" if cents < 0 else ""
    d = int(round(abs(cents) / 100.0))
    return f"{sign}${d:,}"


def eom_leftover_subject(leftover_cents: int) -> str:
    return f"Budget Bot: you saved {money_dollars(leftover_cents)}"


def eom_leftover_body(
    snap: BudgetSnapshot,
    leftover_cents: int,
    pending_spend_count: int = 0,
) -> str:
    del pending_spend_count  # leftover already nets pending spend
    month = snap.period_start.strftime("%B")
    return f"{month} leftover is {money_dollars(leftover_cents)} saved, well done!\n"


def digest_subject(snap: BudgetSnapshot) -> str:
    left = money(snap.remaining_cents)
    hard = money(snap.hardcap_cents)
    return f"Budget Bot: {left} left of {hard} · day {snap.days_elapsed}/{snap.days_in_period}"


def digest_body(
    snap: BudgetSnapshot,
    new_txns: list[Transaction],
    flags: list[AlertEvent],
) -> str:
    lines = [
        f"Period: {snap.period_start.strftime('%Y-%m')} (day {snap.days_elapsed}/{snap.days_in_period})",
        f"Hardcap: {money(snap.hardcap_cents)} · Spent: {money(snap.spend_to_date)} · Left: {money(snap.remaining_cents)} · Pace: {snap.pace_ratio:.2f}×",
        f"Safe-to-spend: {money(snap.safe_to_spend_cents)} · Risk: {snap.risk}",
        "",
        "Top merchants this period:",
    ]
    if snap.top_merchants:
        for name, cents in snap.top_merchants:
            lines.append(f"  - {name}: {money(cents)}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("New spend since last run:")
    shown = [
        t
        for t in new_txns
        if t.amount_cents > 0 and not t.transfer and not t.pending and not t.excluded
    ]
    if shown:
        for t in sorted(shown, key=lambda x: (x.date, x.id)):
            lines.append(
                f"  - {t.date} {t.display_name()} {money(t.amount_cents)} [{t.category}]"
            )
    else:
        lines.append("  (none)")

    if flags:
        lines.append("")
        lines.append("Flags:")
        for f in flags:
            if f.kind == "digest":
                continue
            lines.append(f"  - [{f.kind}] {f.subject}")

    lines.append("")
    lines.append("— Budget Bot (rules, not a lecture)")
    return "\n".join(lines)


def hardcap_subject(breach: bool) -> str:
    return "Budget Bot: over the monthly cap" if breach else "Budget Bot: near pace"


def _period_day_label(snap: BudgetSnapshot) -> str:
    if getattr(snap, "period_kind", "calendar") == "rolling_30d":
        return f"rolling day {snap.days_elapsed} of {snap.days_in_period}"
    month = snap.period_start.strftime("%b")  # Aug — avoid "7/31" date ambiguity
    return f"{month} day {snap.days_elapsed} of {snap.days_in_period}"


def _allotted_cents(snap: BudgetSnapshot) -> int:
    days = max(snap.days_in_period, 1)
    return int(round(snap.hardcap_cents * (snap.days_elapsed / days)))


def _committed_phrase(snap: BudgetSnapshot) -> str:
    """Whole-dollar spent (+ bills if any) for push copy."""
    spent = money_dollars(snap.spend_to_date)
    bills = int(getattr(snap, "bills_reserved_cents", 0) or 0)
    if bills <= 0:
        return f"Spent {spent}"
    committed = money_dollars(
        int(getattr(snap, "committed_cents", 0) or (snap.spend_to_date + bills))
    )
    return (
        f"Committed {committed} "
        f"(spent {spent} + {money_dollars(bills)} bills)"
    )


def days_over_cap(spend_cents: int, hardcap_cents: int, days_in_period: int) -> float:
    """Overage / daily allotment. Independent of day-of-month.

    $210 over $1k in a 31-day month → 6.51 ≈ 7. Do not use days_off_pace.
    """
    if hardcap_cents <= 0 or days_in_period <= 0:
        return 0.0
    return (spend_cents - hardcap_cents) / (hardcap_cents / days_in_period)


def _over_cap(snap: BudgetSnapshot) -> bool:
    return snap.hardcap_cents > 0 and snap.spend_to_date >= snap.hardcap_cents


def _pct_of_cap(snap: BudgetSnapshot) -> int:
    if snap.hardcap_cents <= 0:
        return 0
    return _round_half_away(100.0 * snap.spend_to_date / snap.hardcap_cents)


def _days_above_phrase(snap: BudgetSnapshot) -> str:
    """Over-cap 'N days above' — overage/daily allotment, not pace.

    Rounded 0 (barely over) uses the same 'on pace' wording as the under-cap line.
    """
    n = _round_half_away(
        days_over_cap(snap.spend_to_date, snap.hardcap_cents, snap.days_in_period)
    )
    if n <= 0:
        return "on pace"
    unit = "day" if n == 1 else "days"
    return f"{n} {unit} above"


def hardcap_body(
    snap: BudgetSnapshot,
    breach: bool,
    merchants: list[str] | None = None,
) -> str:
    """Warn reuses near-pace copy; breach is the same three-beat shape.

    Pace/breach copy is numbers-only — no merchant names (locked 2026-08-27).
    Over-cap: percent of cap + days-over, not $X vs $Y / days_off_pace.
    """
    del merchants
    if not breach:
        return pace_body(snap, soft=True)
    return (
        f"Over the monthly cap. {_days_above_phrase(snap)}. "
        f"Spent {_pct_of_cap(snap)}% of cap.\n"
    )


def pace_subject(snap: BudgetSnapshot, *, soft: bool = False) -> str:
    if soft:
        return "Budget Bot: near pace"
    return f"Budget Bot: {_days_off_phrase(snap)}"


def _round_half_away(x: float) -> int:
    """0.5 → +1, -0.5 → -1 (not banker's rounding)."""
    if x > 0:
        return int(math.floor(x + 0.5))
    if x < 0:
        return int(math.ceil(x - 0.5))
    return 0


def _days_off_raw(snap: BudgetSnapshot) -> float:
    raw = float(getattr(snap, "days_off_pace", 0.0) or 0.0)
    # Prefer recomputing from committed if days_off missing/zero but pace off
    if abs(raw) < 0.05 and snap.hardcap_cents > 0 and snap.days_in_period > 0:
        committed = int(getattr(snap, "committed_cents", 0) or snap.spend_to_date)
        expected_day = (committed / snap.hardcap_cents) * snap.days_in_period
        raw = expected_day - snap.days_elapsed
    return raw


def _days_off_from_raw(raw: float) -> str:
    days = _round_half_away(raw)
    if days >= 1:
        unit = "day" if days == 1 else "days"
        return f"{days} {unit} ahead of pace"
    if days <= -1:
        n = abs(days)
        unit = "day" if n == 1 else "days"
        return f"{n} {unit} under pace"
    return "on pace"


def _days_off_phrase(snap: BudgetSnapshot) -> str:
    """Human days ahead/under pace (whole days, min 0 shown as on pace)."""
    return _days_off_from_raw(_days_off_raw(snap))


def pertinent_txn_line(names: list[str] | None) -> str:
    """'Pertinent txn: Cafe.' — unique names, first-seen order."""
    cleaned = [n.strip() for n in (names or []) if n and str(n).strip()]
    if not cleaned:
        return ""
    uniq = list(dict.fromkeys(cleaned))
    if len(uniq) == 1:
        return f"Pertinent txn: {uniq[0]}."
    return f"Pertinent txns: {', '.join(uniq)}."


def pace_body(
    snap: BudgetSnapshot,
    *,
    merchants: list[str] | None = None,
    soft: bool = False,
) -> str:
    """Pace push: days off (or near allotted). Soft keeps allotted dollars; firm does not.

    No merchant names on pace/breach (locked 2026-08-27).
    """
    del merchants
    if soft:
        allotted = money_dollars(_allotted_cents(snap))
        committed = money_dollars(
            int(getattr(snap, "committed_cents", 0) or snap.spend_to_date)
        )
        return (
            f"Spend pace is near allotted. "
            f"Committed {committed} versus {allotted} allotted.\n"
        )
    return f"Spend pace is {_days_off_phrase(snap)}.\n"


def _status_pace(raw: float) -> str:
    """Canned status: 'above pace' not 'ahead of pace'."""
    return _days_off_from_raw(raw).replace("ahead of pace", "above pace")


def _status_window(snap: BudgetSnapshot) -> str:
    if _over_cap(snap):
        return _days_above_phrase(snap)
    return _status_pace(_days_off_raw(snap))


def _window_days_signed(snap: BudgetSnapshot) -> int:
    """Whole days as shown on that line (over-cap overage, else pace)."""
    if _over_cap(snap):
        n = _round_half_away(
            days_over_cap(snap.spend_to_date, snap.hardcap_cents, snap.days_in_period)
        )
        return max(n, 0)
    return _round_half_away(_days_off_raw(snap))


def _overall_days_signed(cal: BudgetSnapshot, roll: BudgetSnapshot) -> int:
    """Mean of the two line day-counts; round up if a fraction."""
    avg = (_window_days_signed(cal) + _window_days_signed(roll)) / 2.0
    if abs(avg) < 1e-12:
        return 0
    if avg > 0:
        return int(math.ceil(avg - 1e-12))
    return int(math.floor(avg + 1e-12))


def _overall_sts_cents(cal: BudgetSnapshot, roll: BudgetSnapshot) -> int:
    """Mean of calendar and rolling STS, rounded to whole dollars (half away)."""
    avg = (
        int(cal.safe_to_spend_cents) + int(roll.safe_to_spend_cents)
    ) / 2.0
    return _round_half_away(avg / 100.0) * 100


def _days_above_from_n(n: int) -> str:
    if n <= 0:
        return "on pace"
    unit = "day" if n == 1 else "days"
    return f"{n} {unit} above"


def _sts_clause(cents: int) -> str:
    """Overall leftover. Not 'safe' — gas etc. still come out of this."""
    if cents < 0:
        return f"over by {money_dollars(-cents)}"
    return f"{money_dollars(cents)} left"


def cash_vs_bills_line(cash_cents: int | None, bills_cents: int) -> str:
    """Terse cash vs bills. Empty when no unpaid dues in the canned horizon."""
    if bills_cents <= 0 or cash_cents is None:
        return ""
    if cash_cents > bills_cents:
        op = ">"
    elif cash_cents < bills_cents:
        op = "<"
    else:
        op = "="
    return f"{money_dollars(cash_cents)} cash {op} {money_dollars(bills_cents)} bills"


def budget_status_text(
    calendar_snap: BudgetSnapshot,
    rolling_snap: BudgetSnapshot,
    *,
    cash_cents: int | None = None,
    upcoming_bills_cents: int = 0,
) -> str:
    """Overall = avg of calendar + rolling days (ceil fraction) and STS.

    Leftover $ on Overall is the mean of the two windows, not the lesser. Over
    cap (calendar spend): percent of calendar cap, no leftover. Optional 4th
    line: `$84 cash > $72 bills` only if unpaid bills due in the next 5 days.
    """
    n = _overall_days_signed(calendar_snap, rolling_snap)
    if _over_cap(calendar_snap):
        overall = f"{_days_above_from_n(n)} · {_pct_of_cap(calendar_snap)}% of cap"
    else:
        overall = (
            f"{_status_pace(float(n))} · "
            f"{_sts_clause(_overall_sts_cents(calendar_snap, rolling_snap))}"
        )
    lines = [
        f"Overall: {overall}",
        f"Calendar: {_status_window(calendar_snap)}",
        f"Rolling: {_status_window(rolling_snap)}",
    ]
    extra = cash_vs_bills_line(cash_cents, upcoming_bills_cents)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def anomaly_subject(merchant: str, amount_cents: int) -> str:
    return f"Budget Bot: unusual {merchant} {money_dollars(amount_cents)}"


def anomaly_body(
    merchant: str,
    amount_cents: int,
    category: str,
    ratio: float,
    which: str,
    day: str,
) -> str:
    del category, which, day  # kept on the event payload; not in push copy
    amt = money_dollars(amount_cents)
    return (
        f"Unusual {merchant} spend. "
        f"{amt} versus recent baseline (~{ratio:.1f}×). "
        "Intentional?\n"
    )
