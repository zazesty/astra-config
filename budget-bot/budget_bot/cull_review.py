"""Monthly spend cull review: aggregate prior-month merchants and surface trim/cull candidates."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import AlertEvent, Transaction

_DEFAULT_MODEL = "google/gemini-3.8-flash"
_GROK_ENV = Path("/etc/grok-mcp.env")


def _prior_month_ym(as_of: date) -> tuple[str, str]:
    """Return (target_ym, prev_ym) for the calendar month before `as_of` and the one before it."""
    first_this = as_of.replace(day=1)
    last_target = first_this - timedelta(days=1)
    first_target = last_target.replace(day=1)
    last_prev = first_target - timedelta(days=1)
    return last_target.strftime("%Y-%m"), last_prev.strftime("%Y-%m")


def _is_bill_merchant(name: str, bills: list[dict[str, Any]]) -> bool:
    low = name.lower()
    for bill in bills:
        pat = str(bill.get("match") or bill.get("name") or "").strip()
        if not pat:
            continue
        try:
            if re.search(pat, name, re.I):
                return True
        except re.error:
            if pat.lower() in low:
                return True
    return False


def build_cull_summary(
    txns: list[Transaction],
    bills: list[dict[str, Any]] | None,
    as_of: date,
) -> dict[str, Any]:
    """Aggregate prior-month spend by merchant/category without raw IDs or account numbers."""
    target_ym, prev_ym = _prior_month_ym(as_of)
    bill_list = list(bills or [])

    target_totals: dict[str, int] = defaultdict(int)
    target_counts: dict[str, int] = defaultdict(int)
    target_cats: dict[str, Counter[str]] = defaultdict(Counter)
    prev_totals: dict[str, int] = defaultdict(int)
    cat_totals: dict[str, int] = defaultdict(int)

    for t in txns:
        if t.pending or t.transfer or t.excluded or t.amount_cents <= 0:
            continue
        ym = t.date[:7]
        if ym not in (target_ym, prev_ym):
            continue
        merch = t.display_name()
        if ym == target_ym:
            target_totals[merch] += t.amount_cents
            target_counts[merch] += 1
            target_cats[merch][t.category or "Misc / Other"] += 1
            cat_totals[t.category or "Misc / Other"] += t.amount_cents
        else:
            prev_totals[merch] += t.amount_cents

    merchants = []
    for merch, cents in sorted(target_totals.items(), key=lambda kv: (-kv[1], kv[0])):
        cat = target_cats[merch].most_common(1)[0][0]
        prev_c = prev_totals.get(merch, 0)
        merchants.append(
            {
                "merchant": merch,
                "cents": cents,
                "count": target_counts[merch],
                "category": cat,
                "prev_cents": prev_c,
                "is_bill": _is_bill_merchant(merch, bill_list),
            }
        )

    repeat_drips = [
        m for m in merchants if m["count"] >= 3 and not m["is_bill"]
    ][:8]
    spikes = [
        m
        for m in merchants
        if m["cents"] >= 3000
        and (
            (m["prev_cents"] > 0 and m["cents"] >= int(m["prev_cents"] * 1.5))
            or (m["prev_cents"] == 0 and m["cents"] >= 5000 and not m["is_bill"])
        )
    ][:8]
    bills_seen = [m for m in merchants if m["is_bill"]][:10]

    return {
        "target_ym": target_ym,
        "prev_ym": prev_ym,
        "total_cents": sum(target_totals.values()),
        "top_merchants": merchants[:12],
        "repeat_drips": repeat_drips,
        "spikes": spikes,
        "bills_seen": bills_seen,
        "top_categories": sorted(cat_totals.items(), key=lambda kv: -kv[1])[:8],
    }


def _read_openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    if _GROK_ENV.is_file():
        for line in _GROK_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _deterministic_suggestions(summary: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for m in summary["repeat_drips"][:3]:
        lines.append(
            f"- Trim repeat drip: {m['merchant']} ({m['count']}x, ${m['cents'] / 100:.2f}, {m['category']})"
        )
    for m in summary["spikes"][:3]:
        prev_s = f" vs ${m['prev_cents'] / 100:.2f} prior" if m["prev_cents"] else " (new)"
        lines.append(
            f"- Review spike: {m['merchant']} (${m['cents'] / 100:.2f}{prev_s}, {m['category']})"
        )
    if not lines:
        lines.append("- No obvious repeat drips or spend spikes detected this month.")
    return lines


def llm_cull_suggestions(summary: dict[str, Any], *, timeout_s: int = 20) -> tuple[list[str], str]:
    """Ask OpenRouter for 3-5 concise cull suggestions; fall back deterministically on error."""
    key = _read_openrouter_key()
    if not key:
        return _deterministic_suggestions(summary), "deterministic (no OPENROUTER_API_KEY)"

    compact = {
        "month": summary["target_ym"],
        "total_usd": round(summary["total_cents"] / 100, 2),
        "top_merchants": [
            {
                "merchant": m["merchant"],
                "usd": round(m["cents"] / 100, 2),
                "count": m["count"],
                "cat": m["category"],
                "prev_usd": round(m["prev_cents"] / 100, 2),
                "bill": m["is_bill"],
            }
            for m in summary["top_merchants"]
        ],
        "repeat_drips": [
            {"merchant": m["merchant"], "usd": round(m["cents"] / 100, 2), "count": m["count"]}
            for m in summary["repeat_drips"]
        ],
        "spikes": [
            {
                "merchant": m["merchant"],
                "usd": round(m["cents"] / 100, 2),
                "prev_usd": round(m["prev_cents"] / 100, 2),
            }
            for m in summary["spikes"]
        ],
    }
    prompt = (
        "You are a concise personal-finance co-admin reviewing prior-month merchant totals. "
        "Give 3 to 5 bullet points of concrete spend to cull, pause, or cap (overlapping subs, "
        "repeat small drips, or unusual spikes). Skip rent/utilities/groceries unless there is a clear spike. "
        "One short line per bullet.\n\n"
        f"Data:\n{json.dumps(compact)}"
    )
    model = os.environ.get("CULL_REVIEW_MODEL", _DEFAULT_MODEL)
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 350,
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = str(data["choices"][0]["message"]["content"]).strip()
        bullets = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if bullets:
            return bullets[:6], f"llm ({model})"
    except Exception:
        pass
    return _deterministic_suggestions(summary), "deterministic (llm fallback)"


def cull_review_event(
    txns: list[Transaction],
    cfg: dict[str, Any],
    *,
    as_of: date,
    use_llm: bool = True,
) -> AlertEvent:
    """Build the monthly spend-cull review email AlertEvent."""
    summary = build_cull_summary(txns, cfg.get("bills"), as_of)
    ym = summary["target_ym"]
    if use_llm:
        suggestions, source = llm_cull_suggestions(summary)
    else:
        suggestions, source = _deterministic_suggestions(summary), "deterministic"

    lines = [
        f"Monthly spend cull review ({ym})",
        f"Total non-transfer spend: ${summary['total_cents'] / 100:,.2f}",
        "",
        f"Suggested culls / trims ({source}):",
        *suggestions,
        "",
        "Top merchants:",
    ]
    for m in summary["top_merchants"][:8]:
        tag = " [bill]" if m["is_bill"] else ""
        lines.append(
            f"  {m['merchant'][:34]:<34} ${m['cents'] / 100:>8,.2f} ({m['count']}x, {m['category']}){tag}"
        )
    if summary["repeat_drips"]:
        lines.append("")
        lines.append("Repeat discretionary drips (>=3x):")
        for m in summary["repeat_drips"][:5]:
            lines.append(f"  {m['merchant'][:34]:<34} ${m['cents'] / 100:>8,.2f} ({m['count']}x)")
    return AlertEvent(
        kind="cull_review",
        subject=f"Budget Bot: {ym} spend cull review",
        body="\n".join(lines) + "\n",
        key=f"cull_review|{ym}",
    )
