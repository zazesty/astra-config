#!/usr/bin/env python3
"""Every-other-Friday AI spend review. Dollars, not prepaid reloads.

Sources, in order:
  - xAI management usage API (billed USD, grouped by model). Key in
    ~/.local/state/astra/xai-management.env. Team id comes from key validation.
  - OpenRouter activity (credits + BYOK inference USD, per model per day).
    Needs a management key. An inference key is refused on that route.
  - AI Studio is named and omitted. Grok Build logs do not keep Gemini
    thinking tokens, and customers who dropped those undercounted the bill
    by 2-3x. No dollar figure until a billed source exists.

The archived weekly digest stays archived. This job does not read it, and
it does not read the ledger.

  ai-spend-review.py              # mail unless --no-send, write state
  ai-spend-review.py --no-send    # print the body, do not mail or write
  ai-spend-review.py --self-test  # parser checks, no network
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
CENTS = Decimal("0.01")
TOP_N = 5
ANCHOR = datetime(2026, 11, 13, tzinfo=TZ).date()  # first Friday of the cadence
STATE_DIR = Path(os.environ.get("AI_SPEND_STATE", str(Path.home() / ".local/state/astra")))
STATE_PATH = STATE_DIR / "ai-spend-review.json"
NOTIFY = os.environ.get("AI_SPEND_NOTIFY", "/root/astra-config/scripts/notify-email.sh")
XAI_ENV = Path(os.environ.get("XAI_MANAGEMENT_ENV", str(Path.home() / ".local/state/astra/xai-management.env")))
OR_ENV = Path(os.environ.get("OR_MANAGEMENT_ENV", str(Path.home() / ".local/state/astra/openrouter-management.env")))
GROK_ENV = Path(os.environ.get("GROK_ENV", "/etc/grok-mcp.env"))


def money(x: Decimal) -> str:
    return f"${x.quantize(CENTS, rounding=ROUND_HALF_UP):,.2f}"


def dec(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def load_env(path: Path) -> dict[str, str]:
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


def http_json(url: str, key: str, body: dict | None = None, timeout: int = 30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return type(e).__name__, None


def window(now: datetime) -> tuple:
    """14 days ending the day before `now`, so a Friday 09:00 mail is two full weeks."""
    end = now.date() - timedelta(days=1)
    start = end - timedelta(days=13)
    return start, end


def due(today, anchor=ANCHOR) -> bool:
    if today.weekday() != 4 or today < anchor:
        return False
    return (today - anchor).days % 14 == 0


def short_model(name: str) -> str:
    text = (name or "").strip()
    for prefix in ("API ", "Chat ", "Voice "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if "/" in text:
        text = text.split("/", 1)[1]
    return text or "(unnamed)"


def add_usd(bucket: dict[str, Decimal], model: str, usd: Decimal) -> None:
    if usd <= 0:
        return
    bucket[model] = bucket.get(model, Decimal("0")) + usd


def xai_usage(start, end) -> dict:
    env = load_env(XAI_ENV)
    key = env.get("XAI_MANAGEMENT_KEY", "")
    if not key:
        return {"ok": False, "error": "no XAI_MANAGEMENT_KEY", "by_model": {}}
    status, meta = http_json(
        "https://management-api.x.ai/auth/management-keys/validation", key
    )
    team = meta.get("teamId") if isinstance(meta, dict) else None
    if status != 200 or not team:
        return {"ok": False, "error": f"validation {status}", "by_model": {}}
    # endTime is exclusive. Noon the day after `end` covers that PT date.
    body = {
        "analyticsRequest": {
            "timeRange": {
                "startTime": f"{start.isoformat()} 00:00:00",
                "endTime": f"{(end + timedelta(days=1)).isoformat()} 00:00:00",
                "timezone": "America/Los_Angeles",
            },
            "timeUnit": "TIME_UNIT_NONE",
            "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
            "groupBy": ["description"],
            "filters": [],
        }
    }
    status, data = http_json(
        f"https://management-api.x.ai/v1/billing/teams/{team}/usage", key, body
    )
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "error": f"usage {status}", "by_model": {}}
    by_model: dict[str, Decimal] = {}
    for series in data.get("timeSeries") or []:
        labels = series.get("groupLabels") or series.get("group") or []
        label = short_model(labels[0] if labels else "")
        usd = Decimal("0")
        for point in series.get("dataPoints") or []:
            values = point.get("values") or []
            if values:
                usd += dec(values[0])
        add_usd(by_model, label, usd)
    note = "truncated" if data.get("limitReached") else None
    return {"ok": True, "error": note, "by_model": by_model}


def or_usage(start, end) -> dict:
    env = load_env(OR_ENV)
    key = env.get("OPENROUTER_MANAGEMENT_KEY", "")
    if not key:
        return {
            "ok": False,
            "error": "no management key (inference key cannot read per-model activity)",
            "by_model": {},
            "credits": Decimal("0"),
            "byok": Decimal("0"),
        }
    status, data = http_json("https://openrouter.ai/api/v1/activity", key)
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "error": f"activity {status}", "by_model": {}, "credits": Decimal("0"), "byok": Decimal("0")}
    by_model: dict[str, Decimal] = {}
    credits = Decimal("0")
    byok = Decimal("0")
    for row in data.get("data") or []:
        if not isinstance(row, dict):
            continue
        try:
            day = datetime.strptime(str(row.get("date") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= day <= end):
            continue
        model = short_model(str(row.get("model_permaslug") or row.get("model") or ""))
        row_credits = dec(row.get("usage"))
        row_byok = dec(row.get("byok_usage_inference"))
        credits += row_credits
        byok += row_byok
        add_usd(by_model, model, row_credits + row_byok)
    return {"ok": True, "error": None, "by_model": by_model, "credits": credits, "byok": byok}


def top(by_model: dict[str, Decimal], n: int = TOP_N) -> list[tuple[str, Decimal]]:
    return sorted(by_model.items(), key=lambda item: -item[1])[:n]


def format_source(title: str, result: dict, extra: str | None = None) -> list[str]:
    lines = [title]
    if not result.get("ok"):
        lines.append(f"  unavailable ({result.get('error')})")
        return lines
    rows = top(result.get("by_model") or {})
    if not rows:
        lines.append("  $0.00")
    for model, usd in rows:
        lines.append(f"  {model[:42]:<42} {money(usd):>10}")
    if extra:
        lines.append(f"  {extra}")
    if result.get("error"):
        lines.append(f"  note: {result['error']}")
    return lines


def build(now: datetime | None = None) -> str:
    now = now or datetime.now(TZ)
    start, end = window(now)
    xai = xai_usage(start, end)
    oru = or_usage(start, end)
    lines = [
        "AI spend review",
        f"Window: {start.isoformat()} -> {end.isoformat()} (PT, 14 days)",
        "Dollars spent. Card reloads are not in this mail.",
        "Covers keys this box can see. grok.com chat and Claude chat are absent.",
        "",
    ]
    lines.extend(format_source("xAI direct (billed)", xai))
    lines.append("")
    extra = None
    if oru.get("ok"):
        extra = f"of which OR credits {money(oru['credits'])}  ·  BYOK {money(oru['byok'])}"
    lines.extend(format_source("OpenRouter (credits + BYOK, per model)", oru, extra))
    lines.append("")
    lines.append("AI Studio (omitted)")
    lines.append("  No billed figure. Local logs drop thinking tokens, and")
    lines.append("  customers who priced without them undercounted by 2-3x.")
    lines.append("  Grounding fees are also missing from those logs.")
    lines.append("")
    lines.append("Top 5 across the billed sources")
    merged: dict[str, Decimal] = {}
    for result in (xai, oru):
        if not result.get("ok"):
            continue
        for model, usd in (result.get("by_model") or {}).items():
            add_usd(merged, model, usd)
    if not merged:
        lines.append("  none this window")
    for model, usd in top(merged):
        lines.append(f"  {model[:42]:<42} {money(usd):>10}")
    return "\n".join(lines) + "\n"


def write_state(now: datetime, body: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    payload = {"as_of": now.isoformat(), "body": body}
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(STATE_PATH, 0o600)


def send(subject: str, body: str) -> int:
    import subprocess
    proc = subprocess.run([NOTIFY, subject], input=body, text=True)
    return proc.returncode


def self_test() -> int:
    assert due(datetime(2026, 11, 13, tzinfo=TZ).date())
    assert not due(datetime(2026, 11, 20, tzinfo=TZ).date())
    assert due(datetime(2026, 11, 27, tzinfo=TZ).date())
    assert not due(datetime(2026, 11, 14, tzinfo=TZ).date())
    start, end = window(datetime(2026, 11, 13, 9, 0, tzinfo=TZ))
    assert start.isoformat() == "2026-10-30"
    assert end.isoformat() == "2026-11-12"
    assert short_model("API grok-4.7") == "grok-4.7"
    assert short_model("google/gemini-3.8-flash") == "gemini-3.8-flash"
    rows = top({"a": Decimal("1"), "b": Decimal("3"), "c": Decimal("2")}, 2)
    assert [name for name, _ in rows] == ["b", "c"]
    print("ai-spend-review self-test ok")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    now = datetime.now(TZ)
    force = "--force" in argv
    no_send = "--no-send" in argv
    if not force and not due(now.date()):
        print(f"ai-spend-review: not a review Friday ({now.date().isoformat()})")
        return 0
    body = build(now)
    if no_send:
        sys.stdout.write(body)
        return 0
    write_state(now, body)
    start, end = window(now)
    subject = f"AI spend {start.isoformat()} – {end.isoformat()}"
    rc = send(subject, body)
    print(f"ai-spend-review: mailed rc={rc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
