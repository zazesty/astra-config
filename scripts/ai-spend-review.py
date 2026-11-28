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
  - Google Cloud billed cost (AI Studio + Vertex + grounding, net of credits)
    from the BigQuery standard usage cost export, once it is enabled. When it
    is live, OpenRouter BYOK is shown but not added (Google bills those calls).
  - Plans + overage from the Budget Bot ledger (card charges). Each vendor is
    counted from exactly one source: usage feeds for OpenRouter / xAI API /
    Google Cloud, so their card reloads are listed as a memo and never added;
    the card charge is the spend only where no usage feed exists (flat plans,
    Anthropic / OpenAI credits or extra usage). Ledger amounts stay in the mail
    and the 600 state file, never in /root/memory.

The archived weekly digest stays archived. This job does not read it, and
it does not read the ledger.

  ai-spend-review.py              # mail unless --no-send, write state
  ai-spend-review.py --no-send    # print the body, do not mail or write
  ai-spend-review.py --self-test  # parser checks, no network
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from astra_common import http_json, load_env, validate_xai_team

TZ = ZoneInfo("America/Los_Angeles")
CENTS = Decimal("0.01")
TOP_N = 5
ANCHOR = datetime(2026, 11, 13, tzinfo=TZ).date()  # first Friday of the cadence
PLAN_MONTHLY_USD = Decimal(os.environ.get("AI_PLAN_MONTHLY_USD", "20"))
CONSIDER_UPGRADE_MONTHLY_USD = Decimal("60")
ADVISE_UPGRADE_MONTHLY_USD = Decimal("80")
HARD_BLOCKS_TIEBREAKER_14D = 4  # >=2/week bumps stay->consider or consider->advise
STATE_DIR = Path(os.environ.get("AI_SPEND_STATE", str(Path.home() / ".local/state/astra")))
STATE_PATH = STATE_DIR / "ai-spend-review.json"
AI_STUDIO_LOG = Path(os.environ.get("ARGON_PROXY_USAGE_LOG", str(STATE_DIR / "ai-studio-usage.jsonl")))
SNAPSHOT_PATH = Path(os.environ.get("AI_SPEND_SNAPSHOT", "/root/memory/ai-coding-plans-snapshot.md"))
NOTIFY = os.environ.get("AI_SPEND_NOTIFY", "/root/astra-config/scripts/notify-email.sh")
XAI_ENV = Path(os.environ.get("XAI_MANAGEMENT_ENV", str(Path.home() / ".local/state/astra/xai-management.env")))
OR_ENV = Path(os.environ.get("OR_MANAGEMENT_ENV", str(Path.home() / ".local/state/astra/openrouter-management.env")))
GROK_ENV = Path(os.environ.get("GROK_ENV", "/etc/grok-mcp.env"))
GCLOUD = os.environ.get("GCLOUD", "/opt/google-cloud-sdk/bin/gcloud")
GCP_PROJECT = os.environ.get("AI_SPEND_GCP_PROJECT", "gen-lang-client-0789043233")
GCP_DATASET = os.environ.get("AI_SPEND_BQ_DATASET", "billing_export")
GCP_TABLE = os.environ.get("AI_SPEND_BQ_TABLE", "")  # project.dataset.table; auto-discovered if empty
GCP_TODO_ID = "ai-spend-review-enable-google-billing-ex-eb04cc"
TODOS_CLI = "/root/astra-config/scripts/standing-todos.sh"
LEDGER = Path(os.environ.get("AI_SPEND_LEDGER", str(Path.home() / ".local/state/budget-bot/txns.json")))
BB_CONFIG = Path(os.environ.get("AI_SPEND_BB_CONFIG", str(Path.home() / ".local/state/budget-bot/config.json")))
PLAN_LOOKBACK_DAYS = 40  # a plan is "active" if it posted in this many days
# Amount rules (cents). xAI plan tiers: Plus 8, Premium 30, Super 100, Ultra 200.
XAI_PLAN_CENTS = {800, 3000, 10000, 20000}
OPENAI_PLAN_CENTS = {2000, 10000, 20000}
# (label, regex on lowercased description + merchant, kind). First match wins.
CARD_VENDORS = [
    ("OpenRouter reloads", r"openrouter|openrout", "reload"),
    ("Google Cloud invoice", r"google \*?cloud|google cloud", "reload"),
    ("Google One", r"google one|google \*?one", "plan"),
    ("Anthropic", r"anthropic|claude", "anthropic"),
    ("OpenAI", r"openai|chatgpt", "openai"),
    ("xAI", r"\bx\.?ai\b|grok", "xai"),
    ("X Premium", r"x corp|twitter", "plan"),
    ("Cursor", r"\bcursor\b", "plan"),
    ("GitHub Copilot", r"copilot", "plan"),
    ("Perplexity", r"perplexity", "plan"),
]


def money(x: Decimal) -> str:
    return f"${x.quantize(CENTS, rounding=ROUND_HALF_UP):,.2f}"


def dec(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


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
    key = load_env(XAI_ENV).get("XAI_MANAGEMENT_KEY", "")
    team, err = validate_xai_team(key)
    if err or not team:
        return {"ok": False, "error": err, "by_model": {}}
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


def is_google_byok(row: dict) -> bool:
    provider = str(row.get("provider_name") or "").lower()
    model = str(row.get("model_permaslug") or row.get("model") or "").lower()
    return provider.startswith("google") or model.startswith("google/")


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
            "google_byok": Decimal("0"),
            "other_byok": Decimal("0"),
        }
    status, data = http_json("https://openrouter.ai/api/v1/activity", key)
    if status != 200 or not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"activity {status}",
            "by_model": {},
            "credits": Decimal("0"),
            "byok": Decimal("0"),
            "google_byok": Decimal("0"),
            "other_byok": Decimal("0"),
        }
    by_model: dict[str, Decimal] = {}
    by_tokens: dict[str, dict[str, int]] = {}
    credits = Decimal("0")
    byok = Decimal("0")
    google_byok = Decimal("0")
    other_byok = Decimal("0")
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
        if row_byok > 0:
            if is_google_byok(row):
                google_byok += row_byok
            else:
                other_byok += row_byok
        add_usd(by_model, model, row_credits + row_byok)
        tok = by_tokens.setdefault(
            model,
            {"reqs": 0, "prompt": 0, "cached": 0, "comp": 0, "reas": 0},
        )
        tok["reqs"] += int(row.get("requests") or 0)
        tok["prompt"] += int(row.get("prompt_tokens") or 0)
        tok["cached"] += int(row.get("cached_tokens") or 0)
        tok["comp"] += int(row.get("completion_tokens") or 0)
        tok["reas"] += int(row.get("reasoning_tokens") or 0)
    return {
        "ok": True,
        "error": None,
        "by_model": by_model,
        "by_tokens": by_tokens,
        "credits": credits,
        "byok": byok,
        "google_byok": google_byok,
        "other_byok": other_byok,
    }


def gcp_token() -> str:
    import subprocess
    try:
        proc = subprocess.run(
            [GCLOUD, "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def gcp_call(url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    import urllib.request
    import urllib.error
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("x-goog-user-project", GCP_PROJECT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:
            payload = {}
        return exc.code, payload
    except Exception as exc:
        return 0, {"error": {"message": str(exc)[:120]}}


def gcp_err(status: int, data: dict) -> str:
    msg = ((data or {}).get("error") or {}).get("message") or ""
    if "has not been used" in msg or "is disabled" in msg:
        return "BigQuery API is off in the project"
    if "Not found" in msg:
        return f"billing export dataset {GCP_DATASET} not found"
    return f"bigquery {status}: {msg[:80]}".strip()


def google_billing(start, end) -> dict:
    """Billed Google Cloud cost (AI Studio + Vertex + grounding) from the BigQuery
    standard usage cost export. Net of credits, grouped by service + SKU."""
    out = {"ok": False, "error": None, "by_model": {}, "gross": Decimal("0"), "credits": Decimal("0")}
    token = gcp_token()
    if not token:
        out["error"] = "no ADC token"
        return out
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{GCP_PROJECT}"
    table = GCP_TABLE
    if not table:
        status, data = gcp_call(f"{base}/datasets/{GCP_DATASET}/tables?maxResults=100", token)
        if status != 200:
            out["error"] = gcp_err(status, data)
            return out
        names = [t.get("tableReference", {}).get("tableId", "") for t in data.get("tables") or []]
        names = sorted(n for n in names if n.startswith("gcp_billing_export_v1_"))
        if not names:
            out["error"] = "waiting for first export (table not created yet)"
            return out
        table = f"{GCP_PROJECT}.{GCP_DATASET}.{names[0]}"
    sql = (
        "SELECT service.description AS svc, sku.description AS sku, currency, "
        "SUM(cost) AS gross, "
        "SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits "
        f"FROM `{table}` "
        "WHERE DATE(usage_start_time, 'America/Los_Angeles') BETWEEN @s AND @e "
        "GROUP BY svc, sku, currency"
    )
    body = {
        "query": sql,
        "useLegacySql": False,
        "timeoutMs": 50000,
        "parameterMode": "NAMED",
        "queryParameters": [
            {"name": "s", "parameterType": {"type": "DATE"}, "parameterValue": {"value": start.isoformat()}},
            {"name": "e", "parameterType": {"type": "DATE"}, "parameterValue": {"value": end.isoformat()}},
        ],
    }
    status, data = gcp_call(f"{base}/queries", token, body)
    if status != 200 or not data.get("jobComplete", False):
        out["error"] = gcp_err(status, data) if status != 200 else "query timed out"
        return out
    currencies = set()
    for row in data.get("rows") or []:
        vals = [cell.get("v") for cell in row.get("f") or []]
        svc, sku, cur, gross, credits = (vals + [None] * 5)[:5]
        currencies.add(cur)
        gross_d, credits_d = dec(gross or 0), dec(credits or 0)
        out["gross"] += gross_d
        out["credits"] += credits_d
        label = f"{(svc or '').replace('Vertex AI', 'Vertex').replace('Generative Language API', 'AI Studio')}: {sku or ''}"
        add_usd(out["by_model"], label.strip(": "), gross_d + credits_d)
    out["ok"] = True
    cur_other = sorted(c for c in currencies if c and c != "USD")
    if cur_other:
        out["error"] = f"currency {','.join(cur_other)}, not converted"
    return out


def classify_card(text: str, cents: int) -> tuple[str, str] | None:
    """(label, kind) for an AI-vendor ledger row, or None if not AI.
    kind: plan (flat subscription), overage (no usage feed sees it, so the card
    charge is the spend), reload (prepay for a vendor whose usage feed is
    already counted above; never added, or it would double count)."""
    t = text.lower()
    if "7-eleven" in t or "youtube" in t:
        return None
    for label, pattern, kind in CARD_VENDORS:
        if re.search(pattern, t):
            if kind == "xai":
                return ("X / Grok plan", "plan") if abs(cents) in XAI_PLAN_CENTS else ("xAI API credits", "reload")
            if kind == "anthropic":
                if re.search(r"claude\.ai subscription|anthropic\*? ?claude|claude sub", t):
                    return ("Claude plan", "plan")
                return ("Anthropic credits / extra usage", "overage")
            if kind == "openai":
                if abs(cents) in OPENAI_PLAN_CENTS and "chatgpt" in t:
                    return ("ChatGPT plan", "plan")
                return ("OpenAI credits / extra usage", "overage")
            return (label, kind)
    return None


def card_charges(start, end, today) -> dict:
    """Plans + overage from the Budget Bot ledger. Canonical rows only
    (transfer=no, excluded=no, posted), which drops the credit-union mirror rows."""
    out = {"ok": False, "error": None, "plans": {}, "overage": {}, "reloads": {}}
    try:
        rows = json.loads(LEDGER.read_text())
    except Exception as exc:
        out["error"] = f"ledger unreadable ({type(exc).__name__})"
        return out
    plan_from = today - timedelta(days=PLAN_LOOKBACK_DAYS)
    latest_plan: dict[str, tuple[str, Decimal]] = {}
    for row in rows if isinstance(rows, list) else []:
        if row.get("transfer") or row.get("excluded") or row.get("pending"):
            continue
        cents = int(row.get("amount_cents") or 0)
        if not cents:
            continue
        hit = classify_card(f"{row.get('name', '')} {row.get('merchant_name') or ''}", cents)
        if not hit:
            continue
        label, kind = hit
        day = str(row.get("date") or "")[:10]
        usd = Decimal(cents) / 100
        if kind == "plan":
            if day >= plan_from.isoformat() and cents > 0 and day >= latest_plan.get(label, ("", 0))[0]:
                latest_plan[label] = (day, usd)
            continue
        if not (start.isoformat() <= day <= end.isoformat()):
            continue
        bucket = out["overage"] if kind == "overage" else out["reloads"]
        bucket[label] = bucket.get(label, Decimal("0")) + usd
    out["plans"] = {label: usd for label, (_, usd) in latest_plan.items()}
    # Budget Bot's bills registry is the SSOT for which subscriptions are live.
    # An AI bill there wins over the ledger guess for the same plan; ledger
    # plans that are not enrolled stay, marked, so a new sub shows up at once.
    enrolled = ai_bills(today)
    out["bills_error"] = enrolled.pop("__error__", None)
    for label in list(out["plans"]):
        if label in enrolled:
            out["plans"].pop(label)
        else:
            out["plans"][f"{label} (not in bills)"] = out["plans"].pop(label)
    out["plans"].update(enrolled)
    out["ok"] = True
    return out


def ai_bills(today) -> dict:
    """Monthly cost of active AI subscriptions in Budget Bot config `bills`."""
    try:
        bills = json.loads(BB_CONFIG.read_text()).get("bills") or []
    except Exception as exc:
        return {"__error__": f"bills unreadable ({type(exc).__name__})"}
    out: dict = {}
    for bill in bills:
        if bill.get("renews") is False or bill.get("cash_only"):
            continue
        if str(bill.get("active_from") or "") > today.isoformat():
            continue
        text = f"{bill.get('name') or ''} {bill.get('match') or ''}".replace("\\b", " ").lower()
        label = None
        for vlabel, pattern, kind in CARD_VENDORS:
            if kind != "reload" and re.search(pattern, text):
                label = {"xai": "X / Grok plan", "anthropic": "Claude plan", "openai": "ChatGPT plan"}.get(kind, vlabel)
                break
        if not label:
            continue
        cents = bill.get("amount_cents")
        if cents is None and bill.get("annual_cents"):
            cents = round(int(bill["annual_cents"]) / 12)
        if cents:
            out[label] = out.get(label, Decimal("0")) + Decimal(int(cents)) / 100
    return out


def ai_studio_usage(start, end) -> dict[str, dict[str, int]]:
    by_model: dict[str, dict[str, int]] = {}
    if not AI_STUDIO_LOG.is_file():
        return by_model
    for line in AI_STUDIO_LOG.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = str(row.get("ts") or "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).date()
        except ValueError:
            continue
        if not (start <= dt <= end):
            continue
        model = short_model(str(row.get("model") or "unknown"))
        tok = by_model.setdefault(
            model,
            {"reqs": 0, "prompt": 0, "cached": 0, "comp": 0, "thoughts": 0},
        )
        tok["reqs"] += 1
        tok["prompt"] += int(row.get("prompt_tokens") or 0)
        tok["cached"] += int(row.get("cached_tokens") or 0)
        tok["comp"] += int(row.get("candidates_tokens") or 0)
        tok["thoughts"] += int(row.get("thoughts_tokens") or 0)
    return by_model


def format_snapshot_entry(start, end, xai: dict, oru: dict, studio: dict[str, dict[str, int]]) -> str:
    header = f"### {start.isoformat()} -> {end.isoformat()}"
    lines = [header, ""]
    if xai.get("ok"):
        xai_items = sorted((xai.get("by_model") or {}).items(), key=lambda x: -x[1])
        xai_str = ", ".join(f"`{m}` {money(u)}" for m, u in xai_items) or "$0.00"
        lines.append(f"- **xAI direct:** {xai_str}")
    if oru.get("ok"):
        by_m = oru.get("by_model") or {}
        by_t = oru.get("by_tokens") or {}
        lines.append(
            f"- **OpenRouter** (credits {money(oru['credits'])}, BYOK {money(oru['byok'])}):"
        )
        for m, u in sorted(by_m.items(), key=lambda x: -x[1]):
            t = by_t.get(m) or {}
            lines.append(
                f"  - `{m}`: {money(u)} ({t.get('reqs', 0)} reqs, prompt {t.get('prompt', 0):,}, "
                f"cached {t.get('cached', 0):,}, comp {t.get('comp', 0):,}, reas {t.get('reas', 0):,})"
            )
    if studio:
        lines.append("- **AI Studio (local proxy log):**")
        for m, t in sorted(studio.items()):
            lines.append(
                f"  - `{m}`: {t['reqs']} reqs, prompt {t['prompt']:,}, cached {t['cached']:,}, "
                f"cand {t['comp']:,}, thoughts {t['thoughts']:,}"
            )
    else:
        lines.append("- **AI Studio (local proxy log):** no calls logged")
    return "\n".join(lines) + "\n"


def append_snapshot(start, end, xai: dict, oru: dict, studio: dict[str, dict[str, int]]) -> bool:
    if not SNAPSHOT_PATH.is_file():
        return False
    text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    header = f"### {start.isoformat()} -> {end.isoformat()}"
    if header in text:
        return False
    entry = format_snapshot_entry(start, end, xai, oru, studio)
    if "## Biweekly snapshots" not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n## Biweekly snapshots\n\n" + entry
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + entry
    SNAPSHOT_PATH.write_text(text, encoding="utf-8")
    return True


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


def format_source_lines(xai: dict, oru: dict, goog: dict) -> list[str]:
    parts = []
    google_byok = oru.get("google_byok", Decimal("0")) if oru.get("ok") else Decimal("0")
    other_byok = oru.get("other_byok", Decimal("0")) if oru.get("ok") else Decimal("0")
    if xai.get("ok"):
        xai_total = sum((xai.get("by_model") or {}).values(), Decimal("0"))
        parts.append(f"xAI {money(xai_total)}")
    else:
        parts.append(f"xAI n/a ({xai.get('error')})")
    if oru.get("ok"):
        or_total = oru["credits"] + other_byok
        if other_byok > 0:
            parts.append(f"OpenRouter {money(or_total)} (credits {money(oru['credits'])} + BYOK {money(other_byok)})")
        else:
            parts.append(f"OpenRouter {money(or_total)}")
    else:
        parts.append(f"OpenRouter n/a ({oru.get('error')})")
    if goog.get("ok"):
        goog_total = sum((goog.get("by_model") or {}).values(), Decimal("0"))
        notes = []
        if google_byok > 0:
            notes.append(f"incl. {money(google_byok)} OR BYOK")
        if goog.get("credits", Decimal("0")) < 0:
            notes.append(f"{money(goog.get('gross', Decimal('0')))} gross net of credits")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        parts.append(f"Google {money(goog_total)}{suffix}")
    elif google_byok > 0:
        parts.append(
            f"Google {money(google_byok)} (OR BYOK only; BigQuery n/a — see todo {GCP_TODO_ID}: {goog.get('error')})"
        )
    else:
        parts.append(f"Google n/a (BigQuery still borked — see todo {GCP_TODO_ID}: {goog.get('error')})")
    return parts


def build(now: datetime | None = None) -> str:
    now = now or datetime.now(TZ)
    start, end = window(now)
    xai = xai_usage(start, end)
    oru = or_usage(start, end)
    goog = google_billing(start, end)
    card = card_charges(start, end, now.date())
    lines = [
        "AI spend review",
        f"Window: {start.isoformat()} -> {end.isoformat()} (PT, 14 days)",
        "All AI spend: billed usage plus plans and extra usage.",
        "",
    ]
    parts = format_source_lines(xai, oru, goog)
    lines.append("Top 5 across the billed sources")
    merged: dict[str, Decimal] = {}
    for result in (xai, oru, goog):
        if not result.get("ok"):
            continue
        for model, usd in (result.get("by_model") or {}).items():
            add_usd(merged, model, usd)
    if not merged:
        lines.append("  none this window")
    for model, usd in top(merged):
        lines.append(f"  {model[:42]:<42} {money(usd):>10}")
    lines.append("")
    lines.append("Usage by source")
    for part in parts:
        lines.append(f"  {part}")
    lines.append("")
    lines.append("Extra usage on card")
    if not card.get("ok"):
        lines.append(f"  unavailable ({card.get('error')})")
    elif not card["overage"]:
        lines.append("  none this window")
    for label, usd in sorted((card.get("overage") or {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"  {label[:42]:<42} {money(usd):>10}")
    lines.append("")
    lines.append("Subscriptions (monthly)")
    plans_usd = sum((card.get("plans") or {}).values(), Decimal("0"))
    if card.get("ok") and not card["plans"]:
        lines.append("  none seen")
    for label, usd in sorted((card.get("plans") or {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"  {label[:42]:<42} {money(usd):>10}")
    if card.get("plans"):
        lines.append(f"  {'total':<42} {money(plans_usd):>10}")
    if card.get("bills_error"):
        lines.append(f"  note: {card['bills_error']}; ledger guess only")
    lines.append("")
    variable_usd = sum(merged.values(), Decimal("0"))
    if goog.get("ok") and oru.get("ok"):
        variable_usd -= oru.get("google_byok", Decimal("0"))
    variable_usd += sum((card.get("overage") or {}).values(), Decimal("0"))
    variable_monthly = (variable_usd * Decimal("30") / Decimal("14")).quantize(CENTS, rounding=ROUND_HALF_UP)
    monthly_all = (variable_monthly + plans_usd).quantize(CENTS, rounding=ROUND_HALF_UP)
    lines.append("Summary")
    lines.append(f"  14-day API/usage spend: {money(variable_usd)} (~{money(variable_monthly)}/mo pace)")
    lines.append(f"  Monthly subscriptions:  {money(plans_usd)}/mo")
    lines.append(f"  All-in monthly pace:    ~{money(monthly_all)}/mo")
    lines.append("")
    hard_blocks_14d = int(os.environ.get("AI_HARD_BLOCKS", "0") or 0)
    dec_res = evaluate_decision(variable_usd, PLAN_MONTHLY_USD, hard_blocks_14d)
    lines.append(dec_res["message"])
    if goog.get("ok"):
        auto_close_gcp_todo()
    return "\n".join(lines) + "\n"


def auto_close_gcp_todo() -> None:
    """Close the BigQuery setup todo automatically once Google billing data flows."""
    import subprocess
    try:
        listed = subprocess.run([TODOS_CLI, "list"], capture_output=True, text=True, timeout=10)
        if listed.returncode == 0 and GCP_TODO_ID in listed.stdout:
            subprocess.run(
                [TODOS_CLI, "note", GCP_TODO_ID, "Auto-closed by ai-spend-review: BigQuery billing export returned live data."],
                capture_output=True, text=True, timeout=10,
            )
            subprocess.run([TODOS_CLI, "done", GCP_TODO_ID], capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def evaluate_decision(
    variable_usd: Decimal,
    base_monthly_usd: Decimal = PLAN_MONTHLY_USD,
    hard_blocks_14d: int = 0,
) -> dict:
    """Return plain-English upgrade guidance from 30d-paced spend (>$60 consider, >$80 advise)."""
    monthly_pace = (variable_usd * Decimal("30") / Decimal("14")).quantize(
        CENTS, rounding=ROUND_HALF_UP
    )
    if monthly_pace > ADVISE_UPGRADE_MONTHLY_USD:
        action = "advise-upgrade"
    elif monthly_pace > CONSIDER_UPGRADE_MONTHLY_USD:
        action = "consider-upgrade"
    else:
        action = "stay"
    if hard_blocks_14d >= HARD_BLOCKS_TIEBREAKER_14D:
        if action == "stay":
            action = "consider-upgrade"
        elif action == "consider-upgrade":
            action = "advise-upgrade"
    if action == "advise-upgrade":
        message = (
            f"Plan check: Advise upgrading AI plan — pay-as-you-go pace (~{money(monthly_pace)}/mo) "
            "is over the $80/mo threshold."
        )
    elif action == "consider-upgrade":
        message = (
            f"Plan check: Consider upgrading AI plan — pay-as-you-go pace (~{money(monthly_pace)}/mo) "
            "is over the $60/mo threshold. Tiebreaker is if rate limits interrupt workflow."
        )
    else:
        message = (
            f"Plan check: Stay on current AI plan — pay-as-you-go pace (~{money(monthly_pace)}/mo) "
            "is under the $60/mo threshold. Tiebreaker is if rate limits interrupt workflow."
        )
    return {
        "action": action,
        "message": message,
        "monthly_pace": monthly_pace,
        "variable_usd": variable_usd,
        "base_monthly": base_monthly_usd,
        "hard_blocks_14d": hard_blocks_14d,
    }


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
    assert evaluate_decision(Decimal("20"), Decimal("20"), 0)["action"] == "stay"
    assert evaluate_decision(Decimal("29"), Decimal("20"), 0)["action"] == "consider-upgrade"
    assert evaluate_decision(Decimal("38"), Decimal("20"), 0)["action"] == "advise-upgrade"
    assert evaluate_decision(Decimal("20"), Decimal("20"), 4)["action"] == "consider-upgrade"
    assert evaluate_decision(Decimal("29"), Decimal("20"), 4)["action"] == "advise-upgrade"
    assert classify_card("Withdrawal 11/13 Grok Xai Www.Grok.Com Xai", 800) == ("X / Grok plan", "plan")
    assert classify_card("GROK XAI 1450 PAGE MILL ROAD", 3000) == ("X / Grok plan", "plan")
    assert classify_card("Withdrawal 10/03 Grok Xai Www.Grok.Com Xai", 1000)[1] == "reload"
    assert classify_card("Openrouter, Inc Openrouter.Ai Ny", 1080)[1] == "reload"
    assert classify_card("CLAUDE.AI SUBSCRIPTION", 2000) == ("Claude plan", "plan")
    assert classify_card("ANTHROPIC SAN FRANCISCO", 5000)[1] == "overage"
    assert classify_card("OPENAI *CHATGPT SUBSCR", 2000) == ("ChatGPT plan", "plan")
    assert classify_card("OPENAI *CHATGPT SUBSCR", 1500)[1] == "overage"
    assert classify_card("Google One Support.Googl", 899) == ("Google One", "plan")
    assert classify_card("GOOGLE *CLOUD 7XK", 1000)[1] == "reload"
    assert classify_card("GOOGLE *YouTube Videos", 999) is None
    assert classify_card("7-ELEVEN 1234", 500) is None
    assert is_google_byok({"provider_name": "google-ai-studio", "model": "google/gemini-4-neon"})
    assert is_google_byok({"provider_name": "", "model_permaslug": "google/gemini-4-hydrogen"})
    assert not is_google_byok({"provider_name": "openai/fast", "model": "openai/gpt-6.1-sol"})
    src_ok = format_source_lines(
        {"ok": True, "by_model": {"grok-4.7": Decimal("0.70")}},
        {"ok": True, "credits": Decimal("18.07"), "byok": Decimal("4.84"), "google_byok": Decimal("4.84"), "other_byok": Decimal("0")},
        {"ok": True, "by_model": {}, "gross": Decimal("27.95"), "credits": Decimal("-27.95")},
    )
    assert src_ok == [
        "xAI $0.70",
        "OpenRouter $18.07",
        "Google $0.00 (incl. $4.84 OR BYOK; $27.95 gross net of credits)",
    ], src_ok
    src_nobq = format_source_lines(
        {"ok": True, "by_model": {"grok-4.7": Decimal("0.70")}},
        {"ok": True, "credits": Decimal("18.07"), "byok": Decimal("5.84"), "google_byok": Decimal("4.84"), "other_byok": Decimal("1.00")},
        {"ok": False, "error": "down"},
    )
    assert src_nobq[1] == "OpenRouter $19.07 (credits $18.07 + BYOK $1.00)", src_nobq
    assert src_nobq[2].startswith("Google $4.84 (OR BYOK only; BigQuery n/a"), src_nobq
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
    append_snapshot(start, end, xai_usage(start, end), or_usage(start, end), ai_studio_usage(start, end))
    subject = f"AI spend {start.isoformat()} – {end.isoformat()}"
    rc = send(subject, body)
    print(f"ai-spend-review: mailed rc={rc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
