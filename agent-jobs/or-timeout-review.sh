#!/usr/bin/env bash
# or-timeout-review — monthly (or on-demand) assess OR/seat timeout policy vs baseline.
#
# Compares recent metrics JSONL under $STATE_DIRECTORY (or /var/lib/grok-mcp) to
# the baseline written at the 2026-08-09 A+B ship (or-timeout-baseline.json).
# Pure stats — no LLM. Hermes does NOT own this (budget/overseer domain).
#
# Usage:
#   agent-run.sh or-timeout-review [--force]
#   DAYS=14 bash agent-jobs/or-timeout-review.sh   # lookback window (default 30)
#
# Exit: 0 always (report written); non-zero only on hard script failure.
# Notify: Pushover pri 0 if a focus bucket timeout_rate is still >50% with n>=5
#         after the lookback (regression / insufficient improvement).
set -euo pipefail

DAYS="${DAYS:-30}"
METRICS_DIR="${GROK_MCP_STATE:-/var/lib/grok-mcp}"
BASELINE="${OR_TIMEOUT_BASELINE:-$METRICS_DIR/or-timeout-baseline.json}"
# fallback if service state not readable
if [[ ! -f "$BASELINE" && -f "${HOME}/.local/state/astra/or-timeout-baseline.json" ]]; then
  BASELINE="${HOME}/.local/state/astra/or-timeout-baseline.json"
fi
OUT_DIR="${HOME}/.local/state/astra/or-timeout-review"
mkdir -p "$OUT_DIR"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PT=$(TZ=America/Los_Angeles date +%Y-%m-%dT%H:%M:%S%z)
REPORT_JSON="$OUT_DIR/latest.json"
REPORT_MD="$OUT_DIR/latest.md"
JSONL="$OUT_DIR/reviews.jsonl"
NOTIFY="${ASTRA_REPO:-/root/astra-config}/scripts/notify-pushover.sh"

export DAYS METRICS_DIR BASELINE OUT_DIR TS PT REPORT_JSON REPORT_MD JSONL

python3 <<'PY'
import json, os, glob
from datetime import datetime, timezone, timedelta
from pathlib import Path

days = int(os.environ["DAYS"])
metrics_dir = Path(os.environ["METRICS_DIR"])
baseline_path = Path(os.environ["BASELINE"])
ts = os.environ["TS"]
pt = os.environ["PT"]
report_json = Path(os.environ["REPORT_JSON"])
report_md = Path(os.environ["REPORT_MD"])
jsonl_path = Path(os.environ["JSONL"])

def grounded(r):
    return bool(r.get("grounded_requested") or r.get("grounding_fired"))

def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

def stats(items):
    n = len(items)
    if not n:
        return {"n": 0}
    lats = [r["latency_ms"] for r in items if isinstance(r.get("latency_ms"), (int, float))]
    oks = sum(1 for r in items if r.get("ok"))
    tos = sum(1 for r in items if r.get("timed_out"))
    out = {
        "n": n,
        "ok_rate": round(oks / n, 4),
        "timeout_rate": round(tos / n, 4),
        "fail_rate": round((n - oks) / n, 4),
    }
    if lats:
        for p in (50, 75, 95, 100):
            out[f"p{p}_ms"] = round(pct(lats, p), 1)
    return out

FOCUS = {
    "panel_claude_ungnd_or": lambda r: r.get("tool") == "panel" and r.get("family") == "claude" and r.get("transport") == "or" and not grounded(r),
    "panel_openai_ungnd_or": lambda r: r.get("tool") == "panel" and r.get("family") == "openai" and r.get("transport") == "or" and not grounded(r),
    "panel_gemini_ungnd_or": lambda r: r.get("tool") == "panel" and r.get("family") == "gemini" and r.get("transport") == "or" and not grounded(r),
    "oracle_openai_ungnd_or": lambda r: r.get("tool") == "oracle" and r.get("family") == "openai" and r.get("transport") == "or" and not grounded(r),
    "oracle_gemini_ungnd_or": lambda r: r.get("tool") == "oracle" and r.get("family") == "gemini" and r.get("transport") == "or" and not grounded(r),
    "all_ungnd_or_ex_auto": lambda r: r.get("transport") == "or" and not grounded(r) and (r.get("family") or "") not in ("auto",),
}

cutoff = datetime.now(timezone.utc) - timedelta(days=days)
rows = []
for p in sorted(metrics_dir.glob("metrics-*.jsonl")):
    try:
        for line in open(p):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = o.get("ts")
            if not t:
                continue
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt >= cutoff:
                rows.append(o)
    except OSError:
        continue

baseline = None
if baseline_path.is_file():
    try:
        baseline = json.loads(baseline_path.read_text())
    except Exception as e:
        baseline = {"error": str(e)}

current = {name: stats([r for r in rows if pred(r)]) for name, pred in FOCUS.items()}

# Compare vs baseline: delta timeout_rate (negative = improvement)
comparisons = {}
alerts = []
base_buckets = (baseline or {}).get("buckets") or {}
for name, cur in current.items():
    base = base_buckets.get(name) or {}
    c_to = cur.get("timeout_rate")
    b_to = base.get("timeout_rate")
    entry = {
        "current": cur,
        "baseline": {k: base[k] for k in ("n", "ok_rate", "timeout_rate", "fail_rate", "p50_ms", "p95_ms") if k in base},
    }
    if c_to is not None and b_to is not None:
        entry["timeout_rate_delta"] = round(c_to - b_to, 4)
        entry["ok_rate_delta"] = round(cur.get("ok_rate", 0) - base.get("ok_rate", 0), 4)
    comparisons[name] = entry
    # Soft alert: still >50% timeout with enough sample
    if cur.get("n", 0) >= 5 and (cur.get("timeout_rate") or 0) > 0.5:
        alerts.append(f"{name}: timeout_rate={cur['timeout_rate']:.0%} n={cur['n']} (baseline was {b_to})")

report = {
    "kind": "or-timeout-review",
    "ts": ts,
    "pt": pt,
    "lookback_days": days,
    "n_rows_in_window": len(rows),
    "baseline_path": str(baseline_path),
    "baseline_shipped_at": (baseline or {}).get("shipped_at"),
    "change": (baseline or {}).get("change"),
    "comparisons": comparisons,
    "alerts": alerts,
}

report_json.write_text(json.dumps(report, indent=2) + "\n")
with open(jsonl_path, "a") as f:
    f.write(json.dumps(report) + "\n")

def pct_s(x):
    if x is None:
        return "—"
    return f"{100*x:.1f}%"

def ms_s(x):
    if x is None:
        return "—"
    return f"{x/1000:.1f}s" if x >= 1000 else f"{x:.0f}ms"

lines = [
    f"# OR timeout review — {pt}",
    f"",
    f"Lookback: **{days}d** · seats in window: **{len(rows)}** · baseline ship: **{(baseline or {}).get('shipped_at', '?')}**",
    f"",
    f"| bucket | n | ok | timeout | p50 | p95 | Δ timeout vs baseline |",
    f"|---|---:|---:|---:|---:|---:|---:|",
]
for name, entry in comparisons.items():
    c = entry["current"]
    d = entry.get("timeout_rate_delta")
    d_s = "—" if d is None else f"{d*100:+.1f}pp"
    lines.append(
        f"| {name} | {c.get('n',0)} | {pct_s(c.get('ok_rate'))} | {pct_s(c.get('timeout_rate'))} | "
        f"{ms_s(c.get('p50_ms'))} | {ms_s(c.get('p95_ms'))} | {d_s} |"
    )
lines.append("")
if alerts:
    lines.append("## Alerts (timeout still >50% with n≥5)")
    for a in alerts:
        lines.append(f"- {a}")
else:
    lines.append("## Alerts")
    lines.append("- none (no focus bucket over 50% timeout with n≥5)")
lines.append("")
lines.append("Baseline change note: " + str((baseline or {}).get("change", {}).get("notes", "—")))
report_md.write_text("\n".join(lines) + "\n")
print(report_md.read_text())
# machine-readable flag for shell notify
Path(os.environ["OUT_DIR"], "alert.flag").write_text("1\n" if alerts else "0\n")
if alerts:
    Path(os.environ["OUT_DIR"], "alert.txt").write_text(
        "OR timeout review: still hot\n" + "\n".join(alerts) + "\n"
    )
PY

# Optional soft pushover if still unhealthy (pri 0 — not interrupt)
if [[ -f "$OUT_DIR/alert.flag" && "$(cat "$OUT_DIR/alert.flag")" == "1" && -x "$NOTIFY" ]]; then
  # notify-pushover.sh TITLE MESSAGE [priority]
  bash "$NOTIFY" "OR timeout review" "$(head -c 900 "$OUT_DIR/alert.txt" 2>/dev/null || echo still hot)" 0 2>/dev/null || true
fi

echo "or-timeout-review: wrote $REPORT_MD"
