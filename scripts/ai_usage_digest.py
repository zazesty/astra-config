#!/usr/bin/env python3
"""Weekly AI usage digest: consumption, not prepaid reloads.

Sources:
  - Hermes session_model_usage x published xAI short-context rates
  - OpenRouter /api/v1/auth/key + /credits billed usage fields
  - Budget Bot card lines listed separately as credit purchases

No secrets, bank dumps, or MCP_PATH in output. Snapshot lives off-repo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
STATE_DIR = Path(os.environ.get("HERMES_AI_COST_STATE", str(Path.home() / ".local/state/hermes-ai-cost")))
SNAPSHOT = STATE_DIR / "snapshot.json"
HERMES_DB = Path.home() / ".hermes" / "state.db"
MILLION = Decimal("1000000")
CENTS = Decimal("0.01")

# docs.x.ai/developers/pricing — short-context (<200k prompt). Reasoning is
# treated as already inside output_tokens (OpenAI-compatible completion).
XAI_RATES: dict[str, dict[str, Decimal]] = {
    "grok-4.5": {"input": Decimal("2.00"), "cache": Decimal("0.30"), "output": Decimal("6.00")},
    "grok-4.6": {"input": Decimal("2.00"), "cache": Decimal("0.50"), "output": Decimal("6.00")},
    "grok-4": {"input": Decimal("3.00"), "cache": Decimal("0.75"), "output": Decimal("15.00")},
}

CARD_RULES = [
    ("Anthropic", re.compile(r"anthropic|claude\.ai|claude\s+sub", re.I)),
    ("xAI", re.compile(r"\bxai\b|\bgrok\b", re.I)),
    ("OpenRouter", re.compile(r"openrouter", re.I)),
    ("OpenAI", re.compile(r"openai|chatgpt", re.I)),
    ("Google", re.compile(r"gemini|google\s*ai", re.I)),
    ("Cursor", re.compile(r"cursor|anysphere", re.I)),
    ("Other AI", re.compile(r"perplexity|mistral|midjourney|elevenlabs|copilot|deepseek", re.I)),
]


def money(x: Decimal) -> str:
    return f"${x.quantize(CENTS, rounding=ROUND_HALF_UP):,.2f}"


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def env_get(*names: str) -> str:
    merged: dict[str, str] = {}
    merged.update(load_env_file(Path("/etc/grok-mcp.env")))
    merged.update(load_env_file(Path.home() / ".hermes" / ".env"))
    for n in names:
        if os.environ.get(n):
            return os.environ[n].strip()
        if merged.get(n):
            return merged[n]
    return ""


def classify_card(blob: str) -> str | None:
    if "7-ELEVEN" in blob.upper():
        return None
    for lab, pat in CARD_RULES:
        if pat.search(blob):
            return lab
    return None


def price_xai(model: str, inp: int, out: int, cache: int) -> Decimal | None:
    rates = XAI_RATES.get(model)
    if not rates:
        base = re.sub(r"-\d{4}.*$", "", model)
        rates = XAI_RATES.get(base)
    if not rates:
        return None
    return (
        Decimal(inp) * rates["input"]
        + Decimal(out) * rates["output"]
        + Decimal(cache) * rates["cache"]
    ) / MILLION


def hermes_usage() -> dict[str, Any]:
    empty = {"ok": False, "error": "no Hermes state db", "by_model": {}, "total": Decimal("0"), "unknown": []}
    if not HERMES_DB.is_file():
        return empty
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="hstate-", suffix=".db")
        os.close(fd)
        shutil.copy2(HERMES_DB, tmp)
        con = sqlite3.connect(tmp)
        con.row_factory = sqlite3.Row
        rows = list(
            con.execute(
                "SELECT model, api_call_count, input_tokens, output_tokens, "
                "cache_read_tokens, reasoning_tokens FROM session_model_usage"
            )
        )
        con.close()
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "by_model": {}, "total": Decimal("0"), "unknown": []}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    by: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    total = Decimal("0")
    for r in rows:
        model = (r["model"] or "unknown").strip()
        usd = price_xai(
            model,
            int(r["input_tokens"] or 0),
            int(r["output_tokens"] or 0),
            int(r["cache_read_tokens"] or 0),
        )
        if usd is None:
            unknown.append(model)
            continue
        b = by.setdefault(
            model,
            {"calls": 0, "inp": 0, "out": 0, "cache": 0, "reason": 0, "usd": Decimal("0")},
        )
        b["calls"] += int(r["api_call_count"] or 0)
        b["inp"] += int(r["input_tokens"] or 0)
        b["out"] += int(r["output_tokens"] or 0)
        b["cache"] += int(r["cache_read_tokens"] or 0)
        b["reason"] += int(r["reasoning_tokens"] or 0)
        b["usd"] += usd
        total += usd
    return {"ok": True, "error": None, "by_model": by, "total": total, "unknown": sorted(set(unknown))}


def dec_or_zero(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def openrouter_usage() -> dict[str, Any]:
    key = env_get("OPENROUTER_API_KEY")
    if not key:
        return {"ok": False, "error": "no OPENROUTER_API_KEY"}
    import urllib.error
    import urllib.request

    def get(url: str) -> tuple[int | str, Any]:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception as e:
            return "err", str(type(e).__name__)

    st, auth = get("https://openrouter.ai/api/v1/auth/key")
    st2, credits = get("https://openrouter.ai/api/v1/credits")
    if st != 200 or not isinstance(auth, dict):
        return {"ok": False, "error": f"auth/key {st}"}
    data = auth.get("data") or {}
    cred = (credits or {}).get("data") if isinstance(credits, dict) else {}
    return {
        "ok": True,
        "error": None,
        "usage": dec_or_zero(data.get("usage")),
        "usage_daily": dec_or_zero(data.get("usage_daily")),
        "usage_weekly": dec_or_zero(data.get("usage_weekly")),
        "usage_monthly": dec_or_zero(data.get("usage_monthly")),
        "byok_weekly": dec_or_zero(data.get("byok_usage_weekly")),
        "byok_monthly": dec_or_zero(data.get("byok_usage_monthly")),
        "total_credits": dec_or_zero((cred or {}).get("total_credits")),
        "total_usage": dec_or_zero((cred or {}).get("total_usage")),
    }


def card_reloads(start, end) -> list[tuple[str, str, int, str]]:
    try:
        from hermes_finance.store import load_txns
    except Exception:
        return []
    rows = []
    for t in load_txns():
        if t.amount_cents <= 0 or t.transfer or t.excluded or t.pending:
            continue
        try:
            d = datetime.strptime(t.date[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if not (start <= d <= end):
            continue
        blob = f"{t.merchant_name or ''} {t.name or ''}"
        lab = classify_card(blob)
        if not lab:
            continue
        label = (t.merchant_name or t.name or lab)[:40]
        rows.append((d.isoformat(), lab, int(t.amount_cents), label))
    return rows


def load_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT.is_file():
        return None
    try:
        return json.loads(SNAPSHOT.read_text())
    except Exception:
        return None


def write_snapshot(now: datetime, hermes: dict[str, Any], write: bool) -> None:
    if not write:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    payload = {
        "as_of": now.isoformat(),
        "pricing": "xai-docs-short-context-2026-07",
        "total_usd": format(hermes["total"], "f"),
        "by_model": {
            m: {
                "calls": b["calls"],
                "inp": b["inp"],
                "out": b["out"],
                "cache": b["cache"],
                "usd": format(b["usd"], "f"),
            }
            for m, b in hermes["by_model"].items()
        },
    }
    SNAPSHOT.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(SNAPSHOT, 0o600)


def _nonneg_delta(cur_v: Decimal, prev_v: Decimal) -> Decimal:
    d = cur_v - prev_v
    if d >= 0:
        return d
    if d > Decimal("-0.05"):
        return Decimal("0")
    return cur_v


def delta_hermes(cur: dict[str, Any], prev: dict[str, Any] | None) -> tuple[Decimal, dict[str, dict[str, Any]], str]:
    if not prev:
        week = {m: dict(b) for m, b in cur["by_model"].items()}
        return cur["total"], week, "cumulative since gateway (no prior snapshot)"
    prev_models = prev.get("by_model") or {}
    week: dict[str, dict[str, Any]] = {}
    total = Decimal("0")
    for m, b in cur["by_model"].items():
        p = prev_models.get(m) or {}
        usd = _nonneg_delta(b["usd"], dec_or_zero(p.get("usd")))
        week[m] = {
            "usd": usd,
            "calls": max(0, int(b["calls"]) - int(p.get("calls") or 0)),
            "inp": max(0, int(b["inp"]) - int(p.get("inp") or 0)),
            "out": max(0, int(b["out"]) - int(p.get("out") or 0)),
            "cache": max(0, int(b["cache"]) - int(p.get("cache") or 0)),
        }
        total += usd
    as_of = str(prev.get("as_of", "?"))[:19]
    return total, week, f"since snapshot {as_of}"


def build() -> str:
    now = datetime.now(TZ)
    end = now.date()
    start = end - timedelta(days=6)
    hermes = hermes_usage()
    prev = load_snapshot()
    write = os.environ.get("DIGEST_WRITE_SNAPSHOT", "1") != "0"
    write_snapshot(now, hermes, write and hermes["ok"])
    week_total, week_models, week_label = delta_hermes(hermes, prev)
    oru = openrouter_usage()
    reloads = card_reloads(start, end)

    lines: list[str] = []
    lines.append("AI usage digest")
    lines.append(f"Window: {start.isoformat()} -> {end.isoformat()} (PT)")
    lines.append("Hermes gateway on this box since ~2026-08-04 23:11 PT")
    lines.append("")
    lines.append("USAGE (consumption — not card reloads)")
    lines.append("")
    if hermes["ok"]:
        lines.append(f"xAI / Hermes  {money(week_total)}  ·  {week_label}")
        for m, b in sorted(week_models.items(), key=lambda x: -x[1]["usd"]):
            lines.append(
                f"  {m:12} {money(b['usd']):>8}   "
                f"in {b['inp']:,}  out {b['out']:,}  cache {b['cache']:,}  calls {b['calls']}"
            )
        lines.append(f"  lifetime    {money(hermes['total']):>8}  (Hermes token log, all sessions)")
        if hermes["unknown"]:
            lines.append(f"  unpriced models: {', '.join(hermes['unknown'])}")
    else:
        lines.append(f"xAI / Hermes  unavailable ({hermes['error']})")
    lines.append("")
    if oru["ok"]:
        lines.append(
            f"OpenRouter   week {money(oru['usage_weekly'])}  ·  "
            f"month {money(oru['usage_monthly'])}  ·  life {money(oru['usage'])}"
        )
        if oru["byok_weekly"] or oru["byok_monthly"]:
            lines.append(
                f"OR BYOK      week {money(oru['byok_weekly'])}  ·  "
                f"month {money(oru['byok_monthly'])}  (billed at provider, not OR credits)"
            )
    else:
        lines.append(f"OpenRouter   unavailable ({oru['error']})")
    lines.append("")
    lines.append("CREDITS BOUGHT this window (not usage)")
    if reloads:
        by_lab: dict[str, int] = defaultdict(int)
        for d, lab, cents, merch in sorted(reloads):
            by_lab[lab] += cents
            lines.append(f"  {d}  {money(Decimal(cents)/100):>8}  {lab:12}  {merch}")
        lines.append("  " + "  ".join(f"{lab} {money(Decimal(c)/100)}" for lab, c in sorted(by_lab.items())))
    else:
        lines.append("  none")
    lines.append("")
    lines.append("Notes")
    lines.append("- xAI $ is Hermes tokens x published short-context rates, not Console invoice.")
    lines.append("- No xAI management key on this box — cannot read Console usage.")
    lines.append("- grok-mcp / Grok Build / journal share the xAI key and may be missing here.")
    lines.append("- Reasoning tokens counted inside output (not added again). No server-tool fees.")
    lines.append("- OpenRouter week/month are their rolling windows, billed usage.")
    lines.append("- A $15 GROK XAI card hit is a credit purchase, not consumption.")
    lines.append("")
    lines.append("— Budget Bot weekly AI usage digest")
    return "\n".join(lines)


if __name__ == "__main__":
    print(build())
