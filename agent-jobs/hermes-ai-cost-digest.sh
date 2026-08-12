#!/usr/bin/env bash
# Weekly AI-lab card spend digest (Budget Bot) + Hermes online note.
# Email via Resend. Fail-open.
set -uo pipefail
export HERMES_FINANCE_STATE="${HERMES_FINANCE_STATE:-$HOME/.local/state/hermes-finance}"
export PYTHONPATH="/root/hermes-finance${PYTHONPATH:+:$PYTHONPATH}"
BODY="$(python3 <<'PY'
from __future__ import annotations
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hermes_finance.store import load_txns

TZ = ZoneInfo("America/Los_Angeles")
now = datetime.now(TZ)
# last 7 full days ending today
end = now.date()
start = end - timedelta(days=6)

RULES = [
    ("Anthropic", re.compile(r"anthropic|claude\.ai|claude\s+sub", re.I)),
    ("xAI", re.compile(r"\bxai\b|\bgrok\b", re.I)),
    ("OpenRouter", re.compile(r"openrouter", re.I)),
    ("OpenAI", re.compile(r"openai|chatgpt", re.I)),
    ("Google", re.compile(r"gemini|google\s*ai", re.I)),
    ("Cursor", re.compile(r"cursor|anysphere", re.I)),
    ("Other AI", re.compile(r"perplexity|mistral|midjourney|elevenlabs|copilot|deepseek", re.I)),
]

def classify(blob: str):
    for lab, pat in RULES:
        if pat.search(blob):
            return lab
    return None

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
    if "7-ELEVEN" in blob.upper():
        continue
    lab = classify(blob)
    if not lab:
        continue
    rows.append((d.isoformat(), lab, t.amount_cents, (t.merchant_name or t.name or "")[:50]))

by_lab = defaultdict(int)
for _d, lab, c, _m in rows:
    by_lab[lab] += c
total = sum(by_lab.values())

lines = []
lines.append(f"AI card spend digest (Budget Bot)")
lines.append(f"Window: {start.isoformat()} → {end.isoformat()} (PT)")
lines.append(f"Hermes gateway on this box since ~2026-08-04 23:11 PT")
lines.append("")
lines.append(f"Week total (labeled AI labs): ${total/100:,.2f}  ·  {len(rows)} charges")
lines.append("")
if by_lab:
    lines.append("By lab:")
    for lab, c in sorted(by_lab.items(), key=lambda x: -x[1]):
        lines.append(f"  {lab:12} ${c/100:,.2f}")
else:
    lines.append("No labeled AI-lab card charges this week.")
lines.append("")
if rows:
    lines.append("Line items:")
    for d, lab, c, m in sorted(rows):
        lines.append(f"  {d}  ${c/100:7.2f}  {lab:12}  {m}")
lines.append("")
lines.append("Caveats:")
lines.append("- Card labels ≠ xAI Console invoice (Grok app / other tools share xAI).")
lines.append("- Hermes-attributable $ needs Console usage or per-session token logs.")
lines.append("- Opaque MasterMoney lines without merchant are omitted unless pinned.")
lines.append("")
lines.append("— Budget Bot weekly AI digest")
print("\n".join(lines))
PY
)"
SUBJECT="Budget Bot: weekly AI spend $(date +%Y-%m-%d)"
if [ -z "${BODY// }" ]; then
  exit 0
fi
printf '%s\n' "$BODY" | /root/astra-config/scripts/notify-email.sh "$SUBJECT" || true
# also print for cron delivery
printf '%s\n' "$BODY"
