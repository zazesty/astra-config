"""Budget rules: hardcap, pace, safe-to-spend, anomalies. Pure functions."""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import AlertEvent, Transaction
from .templates import (
    anomaly_body,
    anomaly_subject,
    digest_body,
    digest_subject,
    eom_leftover_body,
    eom_leftover_subject,
    hardcap_body,
    hardcap_subject,
    pace_body,
    pace_subject,
)


def parse_ymd(s: str) -> date:
    return date.fromisoformat(s[:10])


def month_end(as_of: date) -> date:
    """Last calendar day of as_of's month."""
    return date(as_of.year, as_of.month, calendar.monthrange(as_of.year, as_of.month)[1])


def prior_month_end(as_of: date) -> date:
    """Last day of the calendar month before as_of."""
    return date(as_of.year, as_of.month, 1) - timedelta(days=1)


def period_bounds(as_of: date, tz_name: str = "America/Los_Angeles") -> tuple[date, date, int, int]:
    """Calendar month: (period_start, period_end, days_in_period, days_elapsed 1-based)."""
    start = date(as_of.year, as_of.month, 1)
    end = month_end(as_of)
    days_in = end.day
    days_elapsed = min(max(as_of.day, 1), days_in)
    return start, end, days_in, days_elapsed


def period_bounds_rolling(
    as_of: date,
    past_days: int = 15,
    future_days: int = 15,
) -> tuple[date, date, int, int]:
    """Centered rolling window (default 15 past + 15 future = 30 days).

    Window: [as_of - past_days, as_of + (future_days - 1)] inclusive.
    days_elapsed counts start..as_of (spend only accrues through as_of).
    Notify / alerts still use calendar period_bounds — this is parallel display.
    """
    if past_days < 0 or future_days < 1:
        raise ValueError("past_days >= 0 and future_days >= 1 required")
    start = as_of - timedelta(days=past_days)
    end = as_of + timedelta(days=future_days - 1)
    days_in = (end - start).days + 1
    days_elapsed = (as_of - start).days + 1
    return start, end, days_in, days_elapsed


def counts_as_spend(tx: Transaction, exclude_pending: bool = True) -> bool:
    """True if this txn is included in net spend (outflows add, refunds subtract).

    Transfers / excluded / pending (when exclude_pending) never count.
    True income (sales, payroll, check deposits) does not reduce spend.
    Merchant refunds and credit vouchers do — even if auto-review tagged them Income.
    """
    if tx.excluded or tx.transfer:
        return False
    if exclude_pending and tx.pending:
        return False
    if tx.amount_cents > 0:
        return True
    if tx.amount_cents < 0:
        if tx.looks_like_refund():
            return True
        cat = (tx.category or "").lower()
        return "income" not in cat
    return False


def spend_in_period(
    txns: list[Transaction],
    period_start: date,
    period_end: date,
    exclude_pending: bool = True,
) -> int:
    total = 0
    for t in txns:
        if not counts_as_spend(t, exclude_pending):
            continue
        d = parse_ymd(t.date)
        if period_start <= d <= period_end:
            total += t.amount_cents
    return total


def bill_monthly_reserve_cents(bill: dict[str, Any]) -> int:
    """Monthly amount reserved for a bill.

    Supports:
      - amount_cents: monthly reserve
      - annual_cents: annual total → monthly = round(annual/12)
    annual_cents wins for the monthly figure when both set (amount can mirror it).
    """
    if bill.get("annual_cents") is not None:
        return int(round(int(bill["annual_cents"]) / 12))
    return int(bill.get("amount_cents") or 0)


def bill_due_day(bill: dict[str, Any]) -> int | None:
    """Parse optional day_of_month (1–31); None if unset/invalid."""
    dom = bill.get("day_of_month")
    if dom is None:
        return None
    try:
        due = int(dom)
    except (TypeError, ValueError):
        return None
    if due < 1 or due > 31:
        return None
    return due


def bill_due_date_for_month(bill: dict[str, Any], year: int, month: int) -> date | None:
    """Concrete due date in a given month (clamped to month length)."""
    due = bill_due_day(bill)
    if due is None:
        return None
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(due, last))


def bill_due_dates_in_range(
    bill: dict[str, Any],
    range_start: date,
    range_end: date,
) -> list[date]:
    """All due dates for bill whose calendar day falls in [range_start, range_end]."""
    due = bill_due_day(bill)
    if due is None or range_start > range_end:
        return []
    active_from = None
    raw_start = bill.get("active_from") or bill.get("start")
    if raw_start:
        try:
            active_from = parse_ymd(str(raw_start))
        except ValueError:
            active_from = None
    out: list[date] = []
    y, m = range_start.year, range_start.month
    while True:
        d = bill_due_date_for_month(bill, y, m)
        if (
            d is not None
            and range_start <= d <= range_end
            and (active_from is None or d >= active_from)
        ):
            out.append(d)
        if y == range_end.year and m == range_end.month:
            break
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        # safety
        if y > range_end.year + 1:
            break
    return out


def _bill_name_re(bill: dict[str, Any]) -> re.Pattern[str] | None:
    pat = bill.get("match") or bill.get("name") or ""
    if not pat:
        return None
    try:
        return re.compile(pat, re.I)
    except re.error:
        return re.compile(re.escape(str(pat)), re.I)


def _bill_match_blob(t: Transaction) -> str:
    return f"{t.merchant_name or ''} {t.name or ''}"


def _bill_window(
    bill: dict[str, Any],
    due_d: date,
    *,
    fuzzy_day_slop: int,
    payment_grace_days: int,
) -> tuple[date, date]:
    slop = max(0, int(fuzzy_day_slop))
    grace = max(slop, int(payment_grace_days))
    w0 = due_d - timedelta(days=slop)
    ny, nm = _add_months(due_d.year, due_d.month, 1)
    next_due = bill_due_date_for_month(bill, ny, nm)
    if next_due is not None:
        w1 = max(due_d + timedelta(days=grace), next_due - timedelta(days=1))
    else:
        w1 = due_d + timedelta(days=grace)
    return w0, w1


def build_bill_payment_credits(
    bill: dict[str, Any],
    txns: list[Transaction],
    *,
    exclude_pending: bool = True,
    fuzzy_amount_tol_cents: int = 100,
) -> dict[str, int]:
    """Map txn_id → cents available to clear bill dues (after bounce netting).

    - Name-matching spends become credits (full amount; multi-month 2× autopay OK).
    - Split catch-ups count (e.g. $50 + $112). Only fee-sized crumbs (CSAA ~$20)
      are ignored — not "must be ≥ one full premium".
    - Name-matching refunds/reversals cancel a nearby equal spend (bounce).
    """
    cre = _bill_name_re(bill)
    if cre is None:
        return {}
    monthly = bill_monthly_reserve_cents(bill)
    tol = max(0, int(fuzzy_amount_tol_cents))
    spends: list[tuple[date, str, int]] = []
    refunds: list[tuple[date, str, int]] = []
    for t in txns:
        if t.transfer or t.excluded:
            continue
        if not cre.search(_bill_match_blob(t)):
            continue
        d = parse_ymd(t.date)
        if t.amount_cents > 0:
            if exclude_pending and t.pending:
                continue
            spends.append((d, t.id, int(t.amount_cents)))
        elif t.amount_cents < 0:
            refunds.append((d, t.id, abs(int(t.amount_cents))))

    dead: set[str] = set()
    for rd, _rid, ramt in sorted(refunds, key=lambda x: x[0]):
        best = None
        best_gap = 10**9
        for sd, sid, samt in spends:
            if sid in dead:
                continue
            if abs(samt - ramt) > tol:
                continue
            gap = abs((sd - rd).days)
            if gap <= 21 and gap < best_gap:
                best_gap = gap
                best = sid
        if best is not None:
            dead.add(best)

    credits: dict[str, int] = {}
    # Drop add-on/NSF-sized crumbs only. Floor is just over half a premium,
    # capped at $30, so a $50 partial on a ~$69 bill still counts and a $20
    # CSAA fee does not. (Old rule required ≥ full premium and ate catch-ups.)
    if monthly > 0:
        min_credit = min(monthly // 2 + 1, 3000)
    else:
        min_credit = 1
    for _d, sid, samt in spends:
        if sid in dead:
            continue
        if samt < min_credit:
            continue
        credits[sid] = samt
    return credits


def bill_occurrence_cleared(
    bill: dict[str, Any],
    due_d: date,
    txns: list[Transaction],
    *,
    claimed_ids: set[str],
    exclude_pending: bool = True,
    fuzzy: bool = True,
    fuzzy_amount_tol_cents: int = 100,
    fuzzy_day_slop: int = 2,
    payment_grace_days: int = 40,
    payment_credits: dict[str, int] | None = None,
) -> bool:
    """True if credits cover one monthly due; allocates from pool (supports 2× pays).

    Match window: (due − slop) .. max(due + grace, day before next monthly due).
    Leftover on a credit already applied to an earlier due rolls forward so a
    late-July catch-up can prepay August. Bounces netted in payment_credits;
    fee-sized name-matching crumbs stay ignored.
    """
    monthly = bill_monthly_reserve_cents(bill)
    if monthly <= 0:
        return False
    tol = max(0, int(fuzzy_amount_tol_cents))
    w0, w1 = _bill_window(
        bill, due_d, fuzzy_day_slop=fuzzy_day_slop, payment_grace_days=payment_grace_days
    )

    credits = payment_credits
    if credits is None:
        credits = build_bill_payment_credits(
            bill,
            txns,
            exclude_pending=exclude_pending,
            fuzzy_amount_tol_cents=tol,
        )

    dated: list[tuple[date, str]] = []
    for t in txns:
        have = credits.get(t.id, 0)
        if have <= 0:
            continue
        d = parse_ymd(t.date)
        if w0 <= d <= w1 or t.id in claimed_ids:
            dated.append((d, t.id))
    dated.sort(key=lambda x: x[0])
    need = monthly
    used_dates: list[date] = []
    cleared = False
    for _d, tid in dated:
        have = credits.get(tid, 0)
        if have <= 0:
            continue
        take = min(have, need)
        credits[tid] = have - take
        need -= take
        claimed_ids.add(tid)
        used_dates.append(_d)
        if need <= tol:
            cleared = True
            break
    # Split catch-up: unused sibling in this window within a few days of a
    # used credit (Jul 27 $50 + Jul 29 $112) can prepay the next due.
    # Do not roll a week-apart extra (Feb 12 endorsement) across the year.
    if used_dates:
        for t in txns:
            if credits.get(t.id, 0) <= 0 or t.id in claimed_ids:
                continue
            d = parse_ymd(t.date)
            if not (w0 <= d <= w1):
                continue
            if any(abs((d - ud).days) <= 4 for ud in used_dates):
                claimed_ids.add(t.id)
    if cleared:
        return True

    if not fuzzy:
        return False

    # Fuzzy $ ≈ 1× monthly only for opaque descriptors (no bill-name match).
    # Name-matching spends/refunds are handled solely via payment_credits so a
    # bounced CSAA charge cannot fuzzy-clear a due after netting.
    cre = _bill_name_re(bill)
    for t in txns:
        if t.id in claimed_ids:
            continue
        if cre is not None and cre.search(_bill_match_blob(t)):
            continue
        if not counts_as_spend(t, exclude_pending):
            continue
        if int(t.amount_cents) <= 0:
            continue
        d = parse_ymd(t.date)
        if not (w0 <= d <= w1):
            continue
        if abs(int(t.amount_cents) - monthly) <= tol:
            claimed_ids.add(t.id)
            return True
    return False



def bill_posted_in_period(
    bill: dict[str, Any],
    txns: list[Transaction],
    period_start: date,
    period_end: date,
    exclude_pending: bool = True,
    *,
    claimed_ids: set[str] | None = None,
    fuzzy: bool = True,
    fuzzy_amount_tol_cents: int = 100,
    fuzzy_day_slop: int = 2,
    as_of: date | None = None,
    payment_grace_days: int = 21,
) -> bool:
    """True if any occurrence in [period_start, period_end] is cleared (compat helper)."""
    claimed = claimed_ids if claimed_ids is not None else set()
    dues = bill_due_dates_in_range(bill, period_start, period_end)
    if not dues:
        # fall back: single window match (unscheduled / no DOM)
        cre = _bill_name_re(bill)
        if cre is None:
            return False
        for t in txns:
            if t.id in claimed or not counts_as_spend(t, exclude_pending):
                continue
            d = parse_ymd(t.date)
            if not (period_start <= d <= period_end):
                continue
            blob = f"{t.merchant_name or ''} {t.name or ''}"
            if cre.search(blob):
                claimed.add(t.id)
                return True
        return False
    for due_d in dues:
        if bill_occurrence_cleared(
            bill,
            due_d,
            txns,
            claimed_ids=claimed,
            exclude_pending=exclude_pending,
            fuzzy=fuzzy,
            fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
            fuzzy_day_slop=fuzzy_day_slop,
            payment_grace_days=payment_grace_days,
        ):
            return True
    return False


def bill_due_phase(bill: dict[str, Any], as_of: date, *, posted: bool) -> str:
    """Coaching label: posted | overdue | due_today | upcoming | unscheduled."""
    if posted:
        return "posted"
    due = bill_due_day(bill)
    if due is None:
        return "unscheduled"
    if as_of.day < due:
        return "upcoming"
    if as_of.day == due:
        return "due_today"
    return "overdue"


def bill_is_remaining(
    bill: dict[str, Any],
    as_of: date,
    *,
    posted: bool,
    period_kind: str = "calendar",
    period_start: date | None = None,
    period_end: date | None = None,
    horizon_days: int = 7,
) -> bool:
    """Whether a *single* unposted occurrence should count (legacy/single-shot).

    Prefer effective_bills_reserve_cents (per-occurrence arrears). Kept for tests/callers.
    """
    if posted:
        return False
    kind = (period_kind or "calendar").strip().lower()
    due_dom = bill_due_day(bill)

    if kind in ("rolling", "rolling_30d", "rolling30", "r30"):
        rs = period_start or as_of
        re_ = period_end or as_of
        if due_dom is None:
            return True
        dues = bill_due_dates_in_range(bill, rs, re_)
        if dues:
            return True
        d_this = bill_due_date_for_month(bill, as_of.year, as_of.month)
        if d_this is not None and d_this < as_of and rs <= d_this:
            return True
        return False

    if due_dom is None:
        return True
    due_d = bill_due_date_for_month(bill, as_of.year, as_of.month)
    if due_d is None:
        return True
    if due_d <= as_of:
        return True
    # Calendar upcoming = rest of this month (horizon_days ignored).
    return due_d <= month_end(as_of)


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def bill_reserve_range(
    as_of: date,
    *,
    period_kind: str,
    period_start: date | None,
    period_end: date | None,
    horizon_days: int,
    arrears_lookback_months: int,
) -> tuple[date, date]:
    """Inclusive due-date range to consider for reserves (past arrears + upcoming)."""
    lookback = max(0, int(arrears_lookback_months))
    y, m = _add_months(as_of.year, as_of.month, -lookback)
    range_start = date(y, m, 1)
    kind = (period_kind or "calendar").strip().lower()
    if kind in ("rolling", "rolling_30d", "rolling30", "r30"):
        range_end = period_end or as_of
        if period_start is not None and period_start < range_start:
            # still allow arrears before window start
            pass
        return range_start, range_end
    # calendar: rest of as_of's month (do not leak into next month via as_of+N)
    _ = horizon_days
    range_end = period_end or month_end(as_of)
    return range_start, range_end


def effective_bills_reserve_cents(
    bills: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
    period_start: date | None = None,
    period_end: date | None = None,
    exclude_pending: bool = True,
    as_of: date | None = None,
    *,
    period_kind: str = "calendar",
    horizon_days: int = 7,
    fuzzy: bool = True,
    fuzzy_amount_tol_cents: int = 100,
    fuzzy_day_slop: int = 2,
    window_end: date | None = None,
    arrears_lookback_months: int = 6,
    payment_grace_days: int = 40,
) -> int:
    """Sum reserves with **arrears stacking** (unpaid past dues accumulate).

    For each bill occurrence (monthly due date):
      - Past/today unpaid (within lookback) → +monthly each
      - Future: calendar if due later this month; rolling if due in window
    Clearing is per-occurrence (exact name or fuzzy $), FIFO oldest-first via claim set.
    """
    reserved = 0
    claimed: set[str] = set()
    ref = as_of or period_end or period_start
    if ref is None:
        # No date context (legacy callers): one monthly each, no stacking
        return sum(
            bill_monthly_reserve_cents(b)
            for b in (bills or [])
            if bill_monthly_reserve_cents(b) > 0
        )
    remain_end = window_end or period_end
    kind = (period_kind or "calendar").strip().lower()
    is_rolling = kind in ("rolling", "rolling_30d", "rolling30", "r30")
    range_start, range_end = bill_reserve_range(
        ref,
        period_kind=kind,
        period_start=period_start,
        period_end=remain_end,
        horizon_days=horizon_days,
        arrears_lookback_months=arrears_lookback_months,
    )
    txns = txns or []

    for b in bills or []:
        monthly = bill_monthly_reserve_cents(b)
        if monthly <= 0:
            continue

        pay_credits = build_bill_payment_credits(
            b,
            txns,
            exclude_pending=exclude_pending,
            fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
        )

        dues = bill_due_dates_in_range(b, range_start, range_end)
        if not dues and bill_due_day(b) is None:
            # Unscheduled: at most one reserve if not cleared in current spend window
            posted = False
            if period_start is not None and period_end is not None:
                posted = bill_posted_in_period(
                    b,
                    txns,
                    period_start,
                    period_end,
                    exclude_pending,
                    claimed_ids=claimed,
                    fuzzy=fuzzy,
                    fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
                    fuzzy_day_slop=fuzzy_day_slop,
                    as_of=ref,
                    payment_grace_days=payment_grace_days,
                )
            if not posted:
                reserved += monthly
            continue

        # Oldest first so payments clear arrears before current
        for due_d in sorted(dues):
            if due_d > ref:
                # upcoming
                if is_rolling:
                    rs = period_start or ref
                    re_ = remain_end or ref
                    if not (rs <= due_d <= re_):
                        continue
                else:
                    cap = remain_end or month_end(ref)
                    if due_d > cap:
                        continue
            # past/today always candidates when in range_start..range_end
            if bill_occurrence_cleared(
                b,
                due_d,
                txns,
                claimed_ids=claimed,
                exclude_pending=exclude_pending,
                fuzzy=fuzzy,
                fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
                fuzzy_day_slop=fuzzy_day_slop,
                payment_grace_days=payment_grace_days,
                payment_credits=pay_credits,
            ):
                continue
            reserved += monthly
    return reserved


def upcoming_unpaid_bills_cents(
    bills: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
    as_of: date,
    *,
    horizon_days: int = 7,
    exclude_pending: bool = True,
    fuzzy: bool = True,
    fuzzy_amount_tol_cents: int = 100,
    fuzzy_day_slop: int = 2,
    payment_grace_days: int = 40,
) -> int:
    """Unpaid bill dues in [as_of, as_of+horizon_days]. Not arrears outside that window.

    Canned cash-vs-bills uses horizon_days=5.
    """
    end = as_of + timedelta(days=max(0, int(horizon_days)))
    claimed: set[str] = set()
    total = 0
    txns = txns or []
    for b in bills or []:
        monthly = bill_monthly_reserve_cents(b)
        if monthly <= 0:
            continue
        pay_credits = build_bill_payment_credits(
            b,
            txns,
            exclude_pending=exclude_pending,
            fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
        )
        for due_d in bill_due_dates_in_range(b, as_of, end):
            if bill_occurrence_cleared(
                b,
                due_d,
                txns,
                claimed_ids=claimed,
                exclude_pending=exclude_pending,
                fuzzy=fuzzy,
                fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
                fuzzy_day_slop=fuzzy_day_slop,
                payment_grace_days=payment_grace_days,
                payment_credits=pay_credits,
            ):
                continue
            total += monthly
    return total


CASH_BILLS_HORIZON_DAYS = 5
CASH_BILLS_COVER_MULT = 2.0


def canned_cash_bills_cents(
    bills: list[dict[str, Any]] | None,
    txns: list[Transaction] | None,
    as_of: date,
    *,
    hardcap_cents: int,
    days_in_period: int,
    cash_cents: int | None,
    exclude_pending: bool = True,
    fuzzy: bool = True,
    fuzzy_amount_tol_cents: int = 100,
    fuzzy_day_slop: int = 2,
    payment_grace_days: int = 40,
) -> int:
    """Unpaid material dues in the next 5 days, else 0 (hide the cash line).

    Material = monthly due ≥ half a daily allotment (hardcap / days_in / 2).
    Hide when cash is None, nothing due, or cash ≥ 2× those dues.
    """
    if cash_cents is None:
        return 0
    days = max(int(days_in_period or 30), 1)
    cap = max(int(hardcap_cents or 0), 0)
    floor = int(round((cap / days) / 2.0))
    material = [
        b
        for b in (bills or [])
        if bill_monthly_reserve_cents(b) >= floor
    ]
    due = upcoming_unpaid_bills_cents(
        material,
        txns,
        as_of,
        horizon_days=CASH_BILLS_HORIZON_DAYS,
        exclude_pending=exclude_pending,
        fuzzy=fuzzy,
        fuzzy_amount_tol_cents=fuzzy_amount_tol_cents,
        fuzzy_day_slop=fuzzy_day_slop,
        payment_grace_days=payment_grace_days,
    )
    if due <= 0:
        return 0
    if cash_cents >= int(round(CASH_BILLS_COVER_MULT * due)):
        return 0
    return due


def safe_to_spend_cents(
    hardcap_cents: int,
    spend_to_date: int,
    bills: list[dict[str, Any]] | None = None,
    goals: list[dict[str, Any]] | None = None,
    txns: list[Transaction] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    exclude_pending: bool = True,
    as_of: date | None = None,
    *,
    period_kind: str = "calendar",
    horizon_days: int = 7,
    fuzzy: bool = True,
    window_end: date | None = None,
    arrears_lookback_months: int = 6,
    payment_grace_days: int = 21,
) -> int:
    """Hardcap − spend − remaining bill reserves − goals.

    Bill reserves clear once matched (exact or fuzzy); arrears stack unpaid months.
    """
    reserved = effective_bills_reserve_cents(
        bills,
        txns,
        period_start,
        period_end,
        exclude_pending,
        as_of=as_of or period_end,
        period_kind=period_kind,
        horizon_days=horizon_days,
        fuzzy=fuzzy,
        window_end=window_end,
        arrears_lookback_months=arrears_lookback_months,
        payment_grace_days=payment_grace_days,
    )
    for g in goals or []:
        reserved += int(g.get("amount_cents") or 0)
    return hardcap_cents - spend_to_date - reserved


def pace_ratio(
    spend_to_date: int,
    hardcap_cents: int,
    days_elapsed: int,
    days_in_period: int,
    *,
    bills_reserved_cents: int = 0,
) -> float:
    """Pace = committed / pro-rated hardcap.

    **v2:** committed = spend + remaining bill reserves (pre-charged).
    Hardcap breach still uses raw spend only (elsewhere).
    """
    if hardcap_cents <= 0 or days_in_period <= 0:
        return 0.0
    expected = hardcap_cents * (days_elapsed / days_in_period)
    if expected < 1:
        expected = 1.0
    committed = int(spend_to_date) + max(0, int(bills_reserved_cents or 0))
    return committed / expected


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


@dataclass
class BudgetSnapshot:
    as_of: date
    period_start: date
    period_end: date
    days_in_period: int
    days_elapsed: int
    hardcap_cents: int
    spend_to_date: int
    remaining_cents: int
    pct: float
    pace_ratio: float
    safe_to_spend_cents: int
    risk: str  # ok | warn | breach
    top_merchants: list[tuple[str, int]] = field(default_factory=list)
    period_kind: str = "calendar"  # calendar | rolling_30d
    # Spend window may be tighter than period_end (rolling includes future days).
    spend_through: date | None = None
    # Pace v2: unposted bill reserves pre-charged into committed/pace
    bills_reserved_cents: int = 0
    committed_cents: int = 0  # spend_to_date + bills_reserved_cents
    # Positive => ahead of spend schedule (over pace); negative => under pace.
    days_off_pace: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "period_kind": self.period_kind,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "spend_through": (self.spend_through or min(self.as_of, self.period_end)).isoformat(),
            "days_in_period": self.days_in_period,
            "days_elapsed": self.days_elapsed,
            "hardcap_cents": self.hardcap_cents,
            "spend_to_date": self.spend_to_date,
            "bills_reserved_cents": self.bills_reserved_cents,
            "committed_cents": self.committed_cents,
            "remaining_cents": self.remaining_cents,
            "pct": round(self.pct, 4),
            "pace_ratio": round(self.pace_ratio, 4),
            "days_off_pace": round(self.days_off_pace, 2),
            "safe_to_spend_cents": self.safe_to_spend_cents,
            "risk": self.risk,
            "top_merchants": [
                {"name": n, "amount_cents": c} for n, c in self.top_merchants
            ],
        }


def days_off_pace(
    committed_cents: int,
    hardcap_cents: int,
    days_elapsed: int,
    days_in_period: int,
) -> float:
    """Days ahead (+) / under (−) linear spend schedule from committed spend.

    expected_day_index = committed / hardcap * days_in_period
    days_off = expected_day_index - days_elapsed
    e.g. +2.0 ≈ two days ahead of schedule (burning budget faster).
    """
    if hardcap_cents <= 0 or days_in_period <= 0:
        return 0.0
    expected_day = (float(committed_cents) / float(hardcap_cents)) * float(days_in_period)
    return expected_day - float(days_elapsed)


def top_merchants(
    txns: list[Transaction],
    period_start: date,
    period_end: date,
    exclude_pending: bool = True,
    n: int = 8,
) -> list[tuple[str, int]]:
    bag: dict[str, int] = defaultdict(int)
    for t in txns:
        if not counts_as_spend(t, exclude_pending):
            continue
        d = parse_ymd(t.date)
        if period_start <= d <= period_end:
            bag[t.display_name()] += t.amount_cents
    return sorted(bag.items(), key=lambda x: -x[1])[:n]


def evaluate_budget(
    txns: list[Transaction],
    cfg: dict[str, Any],
    as_of: date | None = None,
    *,
    period_kind: str = "calendar",
) -> BudgetSnapshot:
    """Evaluate hardcap/pace for one period window.

    period_kind:
      - calendar (default): calendar month PT — **notify SSOT**
      - rolling_30d: centered 15 past + 15 future (parallel display only)
    """
    tz = ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")
    if as_of is None:
        as_of = datetime.now(tz).date()
    kind = (period_kind or "calendar").strip().lower()
    if kind in ("rolling", "rolling_30d", "rolling30", "r30"):
        kind = "rolling_30d"
        past = int(cfg.get("rolling_past_days", 15))
        future = int(cfg.get("rolling_future_days", 15))
        start, end, days_in, days_elapsed = period_bounds_rolling(as_of, past, future)
    else:
        kind = "calendar"
        start, end, days_in, days_elapsed = period_bounds(as_of)

    hardcap = int(cfg["hardcap_cents"])
    exclude_pending = bool(cfg.get("exclude_pending", True))
    # Unused for calendar (rest-of-month). Kept so callers/config still parse.
    horizon = int(cfg.get("bill_horizon_days_calendar", 7) or 0)
    fuzzy = bool(cfg.get("bill_fuzzy_match", True))
    fuzzy_tol = int(cfg.get("bill_fuzzy_amount_tol_cents", 100))
    fuzzy_slop = int(cfg.get("bill_fuzzy_day_slop", 2))
    arrears_months = int(cfg.get("bill_arrears_lookback_months", 6))
    pay_grace = int(cfg.get("bill_payment_grace_days", 21))
    # Spend only through as_of (never count future dates in a centered window).
    spend_through = min(as_of, end)
    spend = spend_in_period(txns, start, spend_through, exclude_pending)
    remaining = hardcap - spend
    pct = (spend / hardcap) if hardcap else 0.0
    # Bills: arrears stack + horizon/window upcoming
    bills_reserved = effective_bills_reserve_cents(
        cfg.get("bills"),
        txns,
        start,
        spend_through,
        exclude_pending,
        as_of=as_of,
        period_kind=kind,
        horizon_days=horizon,
        fuzzy=fuzzy,
        fuzzy_amount_tol_cents=fuzzy_tol,
        fuzzy_day_slop=fuzzy_slop,
        window_end=end,
        arrears_lookback_months=arrears_months,
        payment_grace_days=pay_grace,
    )
    committed = spend + bills_reserved
    pr = pace_ratio(
        spend,
        hardcap,
        days_elapsed,
        days_in,
        bills_reserved_cents=bills_reserved,
    )
    sts = safe_to_spend_cents(
        hardcap,
        spend,
        cfg.get("bills"),
        cfg.get("goals"),
        txns=txns,
        period_start=start,
        period_end=spend_through,
        exclude_pending=exclude_pending,
        as_of=as_of,
        period_kind=kind,
        horizon_days=horizon,
        fuzzy=fuzzy,
        window_end=end,
        arrears_lookback_months=arrears_months,
        payment_grace_days=pay_grace,
    )
    dop = days_off_pace(committed, hardcap, days_elapsed, days_in)

    # Pro-rated lines use **committed** (spend + unposted bills) for pace risk.
    # Hardcap breach remains raw spend only.
    month_frac = days_elapsed / max(days_in, 1)
    expected = hardcap * month_frac
    soft_pace = float(cfg.get("soft_pace_frac", 0.90))
    abs_soft = float(cfg.get("hardcap_warn_pct", 0.90))  # absolute % of full hardcap
    soft_line = soft_pace * expected
    committed_pct = (committed / hardcap) if hardcap else 0.0
    if spend >= hardcap:
        risk = "breach"
    elif (month_frac > 0 and committed_pct > month_frac) or pct >= abs_soft or (
        expected > 0 and committed >= soft_line
    ):
        risk = "warn"
    else:
        risk = "ok"

    return BudgetSnapshot(
        as_of=as_of,
        period_start=start,
        period_end=end,
        days_in_period=days_in,
        days_elapsed=days_elapsed,
        hardcap_cents=hardcap,
        spend_to_date=spend,
        remaining_cents=remaining,
        pct=pct,
        pace_ratio=pr,
        safe_to_spend_cents=sts,
        risk=risk,
        top_merchants=top_merchants(txns, start, spend_through, exclude_pending),
        period_kind=kind,
        spend_through=spend_through,
        bills_reserved_cents=bills_reserved,
        committed_cents=committed,
        days_off_pace=dop,
    )


def evaluate_budget_both(
    txns: list[Transaction],
    cfg: dict[str, Any],
    as_of: date | None = None,
) -> dict[str, BudgetSnapshot]:
    """Calendar (notify SSOT) + rolling_30d in parallel for comparison."""
    cal = evaluate_budget(txns, cfg, as_of=as_of, period_kind="calendar")
    roll = evaluate_budget(txns, cfg, as_of=as_of, period_kind="rolling_30d")
    return {"calendar": cal, "rolling_30d": roll}


def pending_spend_count(
    txns: list[Transaction],
    period_start: date,
    period_end: date,
) -> int:
    """Count of spend-like pending rows in the closed period."""
    return sum(1 for _cents in _pending_spend_rows(txns, period_start, period_end))


def pending_spend_cents(
    txns: list[Transaction],
    period_start: date,
    period_end: date,
) -> int:
    """Spend-like pending cents in the closed period (nets leftover)."""
    return sum(_pending_spend_rows(txns, period_start, period_end))


def _pending_spend_rows(
    txns: list[Transaction],
    period_start: date,
    period_end: date,
) -> list[int]:
    out: list[int] = []
    for t in txns:
        if not t.pending:
            continue
        if t.amount_cents <= 0:
            continue
        if not counts_as_spend(t, exclude_pending=False):
            continue
        d = parse_ymd(t.date)
        if period_start <= d <= period_end:
            out.append(int(t.amount_cents))
    return out


def eom_leftover_event(
    snap: BudgetSnapshot,
    *,
    pending_spend_count: int = 0,
    pending_spend_cents: int = 0,
) -> AlertEvent | None:
    """One-shot EOM leftover congrats. leftover = calendar STS − pending spend."""
    leftover = int(snap.safe_to_spend_cents) - max(0, int(pending_spend_cents))
    if leftover <= 0:
        return None
    period = snap.period_start.strftime("%Y-%m")
    return AlertEvent(
        kind="eom_leftover",
        subject=eom_leftover_subject(leftover),
        body=eom_leftover_body(snap, leftover, pending_spend_count),
        key=f"eom_leftover|{period}",
        payload={
            **snap.to_dict(),
            "leftover_cents": leftover,
            "pending_spend_count": pending_spend_count,
            "pending_spend_cents": int(pending_spend_cents),
            "push_priority": 0,
            "interrupt": False,
        },
    )


def category_prior_cents() -> dict[str, int]:
    """1H 2026 medians from fin-coach-baseline (cents). Used cold-start."""
    return {
        "Groceries / Food": 25374,
        "Misc / Other": 17723,
        "Shopping": 16231,
        "Insurance": 11510,
        "Medical & Health": 8802,
        "Personal Care": 7963,
        "Entertainment & Subs": 4596,
        "Charity / Donations": 4025,
        "Utilities & Phone": 3500,
        "Postage & Shipping": 3481,
        "Gas": 2828,
        "Dining Out": 2042,
        "Software & Tools": 1609,
        "Transportation": 1192,
        "Health & Fitness": 259,
        "Housing": 0,
    }


def _window_totals(
    txns: list[Transaction],
    end: date,
    days: int,
    exclude_pending: bool,
    key_fn,
) -> dict[str, int]:
    start = end - timedelta(days=days - 1)
    bag: dict[str, int] = defaultdict(int)
    for t in txns:
        if not counts_as_spend(t, exclude_pending):
            continue
        d = parse_ymd(t.date)
        if start <= d <= end:
            bag[key_fn(t)] += t.amount_cents
    return dict(bag)


def detect_anomalies(
    txns: list[Transaction],
    cfg: dict[str, Any],
    as_of: date | None = None,
    since: date | None = None,
) -> list[AlertEvent]:
    """Flag merchant/category spikes. Rare coaching: high floor + once/merchant/month."""
    tz = ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")
    if as_of is None:
        as_of = datetime.now(tz).date()
    an = cfg.get("anomaly") or {}
    # 2026-08-05: ($100 over baseline) OR (4× baseline, min $100 day) — was $40/2×/$15
    min_abs = int(an.get("min_abs_cents", 10000))  # floor for ratio path
    over_abs = int(an.get("over_abs_cents", 10000))  # $ over baseline
    m_mult = float(an.get("merchant_mult_7d", 4.0))
    c_mult = float(an.get("category_mult_7d", 4.0))
    exclude_pending = bool(cfg.get("exclude_pending", True))
    period = as_of.strftime("%Y-%m")

    if since is None:
        since = as_of - timedelta(days=1)

    recent = [
        t
        for t in txns
        if counts_as_spend(t, exclude_pending)
        and t.amount_cents > 0
        and parse_ymd(t.date) >= since
    ]
    if not recent:
        return []

    m7 = _window_totals(txns, as_of, 7, exclude_pending, lambda t: t.display_name())
    c7 = _window_totals(txns, as_of, 7, exclude_pending, lambda t: t.category)

    prior_end = as_of - timedelta(days=7)
    m_prior = _window_totals(
        txns, prior_end, 7, exclude_pending, lambda t: t.display_name()
    )
    c_prior = _window_totals(
        txns, prior_end, 7, exclude_pending, lambda t: t.category
    )

    priors = category_prior_cents()

    def cat_baseline(cat: str) -> int:
        if cat in c_prior and c_prior[cat] > 0:
            return c_prior[cat]
        monthly = priors.get(cat, priors.get("Misc / Other", 5000))
        return max(int(monthly * 7 / 30), 100)

    def merch_baseline(name: str, cat: str) -> int:
        if name in m_prior and m_prior[name] > 0:
            return m_prior[name]
        return max(cat_baseline(cat) // 2, 100)

    alerts: list[AlertEvent] = []
    seen: set[str] = set()

    by_key: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for t in recent:
        by_key[(t.display_name(), t.date)].append(t)

    for (merchant, day), group in by_key.items():
        day_total = sum(t.amount_cents for t in group)
        if day_total <= 0:
            continue
        cat = group[0].category
        m_base = merch_baseline(merchant, cat)
        c_base = cat_baseline(cat)
        m_now = m7.get(merchant, day_total)
        c_now = c7.get(cat, day_total)
        m_ratio = m_now / m_base if m_base else 99.0
        c_ratio = c_now / c_base if c_base else 99.0
        # OR logic: $N over baseline OR M× (with day-total floor)
        abs_over_m = day_total - m_base
        trip = (abs_over_m >= over_abs) or (
            day_total >= min_abs and (m_ratio >= m_mult or c_ratio >= c_mult)
        )
        if not trip:
            continue
        # Once per merchant per calendar month (rare coaching)
        key = f"anomaly|{period}|{merchant}"
        if key in seen:
            continue
        seen.add(key)
        ratio = max(m_ratio, c_ratio)
        which = "merchant" if m_ratio >= c_ratio else "category"
        alerts.append(
            AlertEvent(
                kind="anomaly",
                subject=anomaly_subject(merchant, day_total),
                body=anomaly_body(
                    merchant=merchant,
                    amount_cents=day_total,
                    category=cat,
                    ratio=ratio,
                    which=which,
                    day=day,
                ),
                key=key,
                payload={
                    "merchant": merchant,
                    "amount_cents": day_total,
                    "category": cat,
                    "ratio": round(ratio, 2),
                    "which": which,
                    "date": day,
                    "push_priority": 0,  # soft: respects quiet hours
                },
            )
        )
    return alerts


def _pace_lines(
    snap: BudgetSnapshot, cfg: dict[str, Any]
) -> tuple[float, float, float, bool, bool]:
    """Return (month_frac, expected_cents, soft_line_cents, is_firm, is_soft).

    **Pace v2:** firm/soft use **committed** = spend + remaining bill reserves.
    Absolute hardcap soft floor still uses raw spend % of hardcap.
    """
    month_frac = snap.days_elapsed / max(snap.days_in_period, 1)
    expected = snap.hardcap_cents * month_frac
    soft_pace = float(cfg.get("soft_pace_frac", 0.90))
    abs_soft = float(cfg.get("hardcap_warn_pct", 0.90))
    soft_line = soft_pace * expected
    committed = int(
        snap.committed_cents
        if snap.committed_cents
        else snap.spend_to_date + max(0, int(snap.bills_reserved_cents or 0))
    )
    committed_pct = (committed / snap.hardcap_cents) if snap.hardcap_cents else 0.0
    # Firm: committed% of hardcap strictly greater than month% elapsed
    is_firm = snap.hardcap_cents > 0 and month_frac > 0 and committed_pct > month_frac
    # Soft: at/over 90% of pro-rated line on committed, or absolute ≥ hardcap_warn_pct of full cap on spend
    is_soft = (not is_firm) and (
        (expected > 0 and committed >= soft_line) or snap.pct >= abs_soft
    )
    return month_frac, expected, soft_line, is_firm, is_soft


def budget_alerts(
    snap: BudgetSnapshot,
    cfg: dict[str, Any],
    prev_risk: str | None = None,
    new_txn_ids: list[str] | None = None,
    new_txns: list[Transaction] | None = None,
) -> list[AlertEvent]:
    """Hardcap / pace alerts (pro-rated calendar model).

    - **Firm (pri 1 interrupt):** committed% > month% elapsed
    - **Breach (pri 2 every time, including further txns while over):** spend ≥ full hardcap
    - Soft near-pace alerts are gone (superseded by firm).
    - Firm re-buzz **per new txn** while condition holds — but a Plaid
      dump of several new charges in one sync is **one** push, not N.
    """
    out: list[AlertEvent] = []
    period = snap.period_start.strftime("%Y-%m")
    month_frac, expected, _soft_line, is_firm, _is_soft = _pace_lines(snap, cfg)
    firm_pri = 1
    breach_pri = 2
    ids = list(new_txn_ids or [])
    tx_by_id = {t.id: t for t in (new_txns or [])}
    if not ids and new_txns:
        ids = [t.id for t in new_txns]
    def _merchants_for(tid_list: list[str]) -> list[str]:
        names: list[str] = []
        for tid in tid_list:
            t = tx_by_id.get(tid)
            if t:
                names.append(t.display_name())
        return names

    def _batch_line(n: int, *, still_over: bool = False) -> str:
        if n <= 1:
            return ""
        if still_over:
            return f" {n} more charges landed together."
        return f" {n} new charges landed together."

    def _id_key(prefix: str, tid_list: list[str]) -> tuple[str, dict]:
        """One event for a single txn or a same-sync dump."""
        if not tid_list:
            return prefix, {}
        if len(tid_list) == 1:
            return (
                f"{prefix}|{tid_list[0]}",
                {"txn_id": tid_list[0], "txn_ids": tid_list},
            )
        return (
            f"{prefix}|batch|{tid_list[0]}",
            {
                "txn_id": tid_list[0],
                "txn_ids": tid_list,
                "batch": True,
                "also_mark_keys": [f"{prefix}|{tid}" for tid in tid_list],
            },
        )

    if snap.risk == "breach" or snap.spend_to_date >= snap.hardcap_cents:
        # Every over-cap push is emergency (2), including further txns this month.
        # One push if several land in the same sync.
        first_breach = prev_risk != "breach"
        rest = list(ids)
        extra = _batch_line(len(ids), still_over=True) if ids else ""
        if first_breach:
            out.append(
                AlertEvent(
                    kind="hardcap_breach",
                    subject=hardcap_subject(breach=True),
                    body=hardcap_body(snap, breach=True, merchants=_merchants_for(ids))
                    + extra,
                    key=f"hardcap_breach|{period}",
                    payload={
                        **snap.to_dict(),
                        "push_priority": breach_pri,
                        "interrupt": True,
                        "txn_ids": ids,
                        "also_mark_keys": [f"hardcap_over|{period}|{tid}" for tid in ids],
                    },
                )
            )
            return out
        if rest:
            key, extra_payload = _id_key(f"hardcap_over|{period}", rest)
            out.append(
                AlertEvent(
                    kind="hardcap_breach",
                    subject=hardcap_subject(breach=True),
                    body=hardcap_body(snap, breach=True, merchants=_merchants_for(rest))
                    + extra,
                    key=key,
                    payload={
                        **snap.to_dict(),
                        "push_priority": breach_pri,
                        "interrupt": True,
                        "still_breached": True,
                        **extra_payload,
                    },
                )
            )
        return out

    if is_firm:
        # Ahead of calendar: interrupt; one buzz per sync (1 txn or a dump).
        merchants = _merchants_for(ids)
        if ids:
            key, extra_payload = _id_key(f"pace_firm|{period}", ids)
            out.append(
                AlertEvent(
                    kind="pace_warn",
                    subject=pace_subject(snap),
                    body=pace_body(snap, merchants=merchants),
                    key=key,
                    payload={
                        **snap.to_dict(),
                        "push_priority": firm_pri,
                        "interrupt": True,
                        "firm": True,
                        "expected_cents": int(expected),
                        "merchants": merchants,
                        **extra_payload,
                    },
                )
            )
        else:
            out.append(
                AlertEvent(
                    kind="pace_warn",
                    subject=pace_subject(snap),
                    body=pace_body(snap, merchants=merchants),
                    key=f"pace_firm|{period}",
                    payload={
                        **snap.to_dict(),
                        "push_priority": firm_pri,
                        "interrupt": True,
                        "firm": True,
                        "expected_cents": int(expected),
                    },
                )
            )
        return out

    return out


def make_digest(
    snap: BudgetSnapshot,
    new_txns: list[Transaction],
    flags: list[AlertEvent],
) -> AlertEvent:
    period = snap.period_start.strftime("%Y-%m")
    return AlertEvent(
        kind="digest",
        subject=digest_subject(snap),
        body=digest_body(snap, new_txns, flags),
        key=f"digest|{period}|{snap.as_of.isoformat()}",
        payload=snap.to_dict(),
    )
