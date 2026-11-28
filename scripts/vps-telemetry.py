#!/usr/bin/env python3
"""Daily VPS ops telemetry aggregator (commits per human prompt, shipping vs firefighting, cache/cheap-seat share, box monthly total).

AI spend polling lives exclusively in ai-spend-review.py; this script reads
ai-spend-review.json for the AI + Hetzner box monthly total (Hetzner stays
out of the biweekly AI spend emails).

Writes quiet local snapshots under ~/.local/state/astra/ (mode 700/600, out of git):
  - vps-telemetry-config.json    (start date, Hetzner rate)
  - vps-telemetry-api-cache.json (incremental daily journal churn cache)
  - vps-telemetry.json           (latest full rollup: all-time, 30d, 7d)
  - vps-telemetry.jsonl          (one compact row per PT day, deduped on date)
  - vps-telemetry.md             (human-readable dashboard)

Privacy / security:
  - Never reads Budget Bot bank/ledger files (~/.local/state/budget-bot/).
  - Never reads or stores prompt text (display/text/content fields are ignored).
  - Never sends email or Pushover notifications.

Usage:
  vps-telemetry.py              # collect, update cache, write state + markdown
  vps-telemetry.py --dry-run    # compute and print markdown, do not write state
  vps-telemetry.py --json       # print latest/computed JSON to stdout (no write)
  vps-telemetry.py --self-test  # unit checks, no state writes
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
STATE_DIR = Path(os.environ.get("VPS_TELEMETRY_STATE", str(Path.home() / ".local/state/astra")))
CONFIG_PATH = STATE_DIR / "vps-telemetry-config.json"
CACHE_PATH = STATE_DIR / "vps-telemetry-api-cache.json"
JSON_PATH = STATE_DIR / "vps-telemetry.json"
JSONL_PATH = STATE_DIR / "vps-telemetry.jsonl"
MD_PATH = STATE_DIR / "vps-telemetry.md"

AI_SPEND_STATE = STATE_DIR / "ai-spend-review.json"
AI_STUDIO_LOG = STATE_DIR / "ai-studio-usage.jsonl"
ARGON_LATEST = STATE_DIR / "argon-usage.json"
PHOTON_HEAL_LOG = STATE_DIR / "photon-heal.log"

DEFAULT_CONFIG = {
    "vps_start_date": "2026-05-18",
    "vps_monthly_usd": 15.32,
}

CHEAP_MODEL_MARKERS = (
    "flash",
    "hydrogen",
    "argon",
    "terra",
    "build-0.1",
    "code-fast",
    "haiku",
    "mini",
    "nano",
    "lite",
)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
        except (OSError, ValueError):
            pass
    return cfg


def save_config_if_missing(cfg: dict) -> None:
    if CONFIG_PATH.exists():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)


def load_cache() -> dict:
    if CACHE_PATH.is_file():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"churn_daily": data.get("churn_daily") or {}}
        except (OSError, ValueError):
            pass
    return {"churn_daily": {}}


def save_cache(cache: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(CACHE_PATH, 0o600)


def load_box_monthly_pace(hetzner_monthly: float) -> dict | None:
    """Read latest AI monthly pace from ai-spend-review.json and add Hetzner."""
    if not AI_SPEND_STATE.is_file():
        return None
    try:
        data = json.loads(AI_SPEND_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bm = data.get("box_monthly_pace")
    if isinstance(bm, dict) and bm.get("ai_monthly_usd") is not None:
        ai_usd = float(bm["ai_monthly_usd"])
        hz_usd = float(bm.get("hetzner_monthly_usd", hetzner_monthly))
        return {
            "as_of": data.get("as_of"),
            "ai_monthly_usd": round(ai_usd, 2),
            "hetzner_monthly_usd": round(hz_usd, 2),
            "total_box_monthly_usd": round(ai_usd + hz_usd, 2),
        }
    hist = data.get("history") or []
    if hist and isinstance(hist[-1], dict) and hist[-1].get("monthly_all") is not None:
        ai_usd = float(hist[-1]["monthly_all"])
        return {
            "as_of": data.get("as_of"),
            "ai_monthly_usd": round(ai_usd, 2),
            "hetzner_monthly_usd": round(hetzner_monthly, 2),
            "total_box_monthly_usd": round(ai_usd + hetzner_monthly, 2),
        }
    return None


def parse_any_ts(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        num = float(val)
        if num <= 0:
            return None
        if num > 1e12:
            num /= 1000.0
        try:
            return datetime.fromtimestamp(num, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if s.isdigit():
            return parse_any_ts(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def collect_ai_studio(since_date: date | None = None) -> dict:
    out = {
        "reqs": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "thoughts_tokens": 0,
        "total_tokens": 0,
    }
    if not AI_STUDIO_LOG.is_file():
        return out
    for line in AI_STUDIO_LOG.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        dt = parse_any_ts(row.get("ts"))
        if dt is None:
            continue
        d_pt = dt.astimezone(TZ).date()
        if since_date and d_pt < since_date:
            continue
        prompt = int(row.get("prompt_tokens") or 0)
        cached = int(row.get("cached_tokens") or 0)
        cand = int(row.get("candidates_tokens") or 0)
        thoughts = int(row.get("thoughts_tokens") or 0)
        tot = prompt + cand + thoughts

        out["reqs"] += 1
        out["prompt_tokens"] += prompt
        out["cached_tokens"] += cached
        out["output_tokens"] += cand
        out["thoughts_tokens"] += thoughts
        out["total_tokens"] += tot
    return out


def collect_human_prompts_and_commits() -> dict:
    """Collect explicit human prompt timestamps (no prompt text) and non-auto git commits."""
    human_by_source: dict[str, list[datetime]] = defaultdict(list)
    extra_tokens = {
        "grok_prompt": 0,
        "grok_cached": 0,
        "grok_total": 0,
        "grok_cheap": 0,
    }

    # 1. Claude Code (root + dev)
    for p in ("/root/.claude/history.jsonl", "/home/dev/.claude/history.jsonl"):
        path = Path(p)
        if path.is_file():
            for line in path.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                dt = parse_any_ts(d.get("timestamp"))
                if dt:
                    human_by_source["claude"].append(dt)

    # 2. Codex (root + dev)
    for p in ("/root/.codex/history.jsonl", "/home/dev/.codex/history.jsonl"):
        path = Path(p)
        if path.is_file():
            for line in path.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                dt = parse_any_ts(d.get("ts"))
                if dt:
                    human_by_source["codex"].append(dt)

    # 3. Grok Build (root + dev)
    for root in ("/root/.grok/sessions", "/home/dev/.grok/sessions"):
        for fp in glob.glob(f"{root}/**/prompt_history.jsonl", recursive=True):
            try:
                with open(fp, errors="replace") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        dt = parse_any_ts(d.get("timestamp"))
                        if dt:
                            human_by_source["grok_build"].append(dt)
            except OSError:
                continue

        for fp in glob.glob(f"{root}/**/usage.json", recursive=True):
            if "/subagents/" in fp:
                continue
            try:
                d = json.loads(Path(fp).read_text(errors="replace"))
            except (OSError, ValueError):
                continue
            for turn in d.get("turns") or []:
                mu = turn.get("modelUsage") or {}
                if not mu and turn.get("primaryModelId"):
                    mu = {turn["primaryModelId"]: turn}
                for m_name, row in mu.items():
                    inp = int(row.get("inputTokens") or 0)
                    cached = int(row.get("cachedReadTokens") or 0)
                    out = int(row.get("outputTokens") or 0)
                    reas = int(row.get("reasoningTokens") or 0)
                    tot = inp + out + reas
                    extra_tokens["grok_prompt"] += inp
                    extra_tokens["grok_cached"] += cached
                    extra_tokens["grok_total"] += tot
                    if any(m in m_name.lower() for m in CHEAP_MODEL_MARKERS):
                        extra_tokens["grok_cheap"] += tot

    # 4. Antigravity (agy) — history.jsonl only (avoid double-counting transcript)
    ag_hist = Path("/root/.gemini/antigravity-cli/history.jsonl")
    if ag_hist.is_file():
        for line in ag_hist.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            dt = parse_any_ts(d.get("timestamp"))
            if dt:
                human_by_source["antigravity"].append(dt)

    # 5. Hermes
    hdb = Path("/root/.hermes/state.db")
    if hdb.is_file():
        try:
            conn = sqlite3.connect(f"file:{hdb}?mode=ro", uri=True)
            for (ts,) in conn.execute("SELECT timestamp FROM messages WHERE role = 'user' AND timestamp IS NOT NULL"):
                dt = parse_any_ts(ts)
                if dt:
                    human_by_source["hermes"].append(dt)
            conn.close()
        except sqlite3.Error:
            pass

    # 6. Non-auto git commits
    git_commits: list[datetime] = []
    for repo in ("/root/grok-mcp", "/root/astra-config"):
        if not Path(repo, ".git").exists():
            continue
        try:
            res = subprocess.run(
                ["git", "-C", repo, "log", "--format=%aI\t%s"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in res.stdout.splitlines():
                if "\t" not in line:
                    continue
                ts_s, subj = line.split("\t", 1)
                if subj.startswith("auto: config snapshot"):
                    continue
                dt = parse_any_ts(ts_s)
                if dt:
                    git_commits.append(dt)
        except Exception:
            pass

    return {
        "human_by_source": human_by_source,
        "git_commits": git_commits,
        "extra_tokens": extra_tokens,
    }


def collect_churn(cache: dict) -> dict[str, dict[str, int]]:
    """Collect service restarts & failures per PT day from systemd journal + photon-heal.log."""
    today = datetime.now(TZ).date()
    since_day = today - timedelta(days=7)
    units = [
        "grok-mcp.service",
        "grok-argon-proxy.service",
        "hermes-gateway.service",
        "budget-bot-poll.service",
        "budget-bot-plaid-webhook.service",
        "budget-bot-plaid-link.service",
        "budget-bot-buy-queue-ask.service",
    ]
    daily_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"restarts": 0, "failures": 0, "photon_heals": 0})

    try:
        cmd = ["journalctl", "--since", since_day.isoformat(), "--output=short-iso", "--no-pager"]
        for u in units:
            cmd.extend(["-u", u])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        for line in res.stdout.splitlines():
            if not line or line.startswith("--"):
                continue
            low = line.lower()
            is_fail = "failed with result" in low or "entered failed state" in low or "main process exited, code=exited" in low
            is_restart = "scheduled restart job" in low or "started " in low or "restarting " in low
            if not (is_fail or is_restart):
                continue
            ts_token = line.split()[0]
            dt = parse_any_ts(ts_token)
            if not dt:
                continue
            d_str = dt.astimezone(TZ).date().isoformat()
            if is_fail:
                daily_counts[d_str]["failures"] += 1
            elif is_restart:
                daily_counts[d_str]["restarts"] += 1
    except Exception:
        pass

    if PHOTON_HEAL_LOG.is_file():
        for line in PHOTON_HEAL_LOG.read_text(errors="replace").splitlines():
            low = line.lower()
            if "restarted" not in low and "heal" not in low and "failed" not in low:
                continue
            parts = line.split()
            if not parts:
                continue
            dt = parse_any_ts(parts[0])
            if dt and dt.astimezone(TZ).date() >= since_day:
                d_str = dt.astimezone(TZ).date().isoformat()
                if "restarted" in low:
                    daily_counts[d_str]["photon_heals"] += 1

    churn_cache = cache.setdefault("churn_daily", {})
    for d_str, counts in daily_counts.items():
        churn_cache[d_str] = counts
    return churn_cache


def filter_ts(items: list[datetime], cutoff: datetime | None) -> list[datetime]:
    if cutoff is None:
        return items
    return [t for t in items if t >= cutoff]


def commits_per_prompt(commits: int, prompts: int) -> float | None:
    if prompts <= 0:
        return None
    return round(commits / prompts, 3)


def compute_snapshot(cfg: dict, cache: dict, now: datetime | None = None) -> dict:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    today = now.date()
    start_date = date.fromisoformat(cfg["vps_start_date"])
    hetzner_monthly = float(cfg.get("vps_monthly_usd", 15.32))

    churn_daily = collect_churn(cache)
    ts_data = collect_human_prompts_and_commits()
    human_src = ts_data["human_by_source"]
    git_commits = ts_data["git_commits"]
    studio_all = collect_ai_studio()

    def build_window(days: int | None) -> dict:
        if days is None:
            w_start = start_date
            cutoff = None
        else:
            w_start = max(start_date, today - timedelta(days=days - 1))
            cutoff = datetime.combine(w_start, datetime.min.time(), tzinfo=TZ).astimezone(timezone.utc)

        elapsed_days = max(1, (today - w_start).days + 1)
        studio_win = collect_ai_studio(since_date=w_start)

        prompts_by_harness = {
            k: len(filter_ts(v, cutoff))
            for k, v in human_src.items()
            if filter_ts(v, cutoff)
        }
        prompts_n = sum(prompts_by_harness.values())
        commits_n = len(filter_ts(git_commits, cutoff))
        cpp = commits_per_prompt(commits_n, prompts_n)

        restarts_n = 0
        failures_n = 0
        heals_n = 0
        for d_str, c in churn_daily.items():
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                continue
            if w_start <= d <= today:
                restarts_n += int(c.get("restarts") or 0)
                failures_n += int(c.get("failures") or 0)
                heals_n += int(c.get("photon_heals") or 0)
        fire_events = failures_n + heals_n
        ship_ratio = round(commits_n / max(1, fire_events), 2)

        tot_prompt = studio_win["prompt_tokens"]
        tot_cached = studio_win["cached_tokens"]
        tot_tokens = studio_win["total_tokens"]
        tot_cheap = studio_win["total_tokens"]
        if days is None:
            et = ts_data["extra_tokens"]
            tot_prompt += et["grok_prompt"]
            tot_cached += et["grok_cached"]
            tot_tokens += et["grok_total"]
            tot_cheap += et["grok_cheap"]

        cache_hit_pct = round(100.0 * tot_cached / tot_prompt, 1) if tot_prompt > 0 else None
        cheap_seat_pct = round(100.0 * tot_cheap / tot_tokens, 1) if tot_tokens > 0 else None

        return {
            "window_days": elapsed_days,
            "start_date": w_start.isoformat(),
            "end_date": today.isoformat(),
            "throughput": {
                "human_prompts": prompts_n,
                "commits": commits_n,
                "commits_per_prompt": cpp,
                "prompts_by_harness": prompts_by_harness,
            },
            "shipping_vs_firefighting": {
                "commits": commits_n,
                "service_failures": failures_n,
                "photon_heals": heals_n,
                "service_restarts": restarts_n,
                "ship_to_fire_ratio": ship_ratio,
            },
            "efficiency": {
                "cache_hit_pct": cache_hit_pct,
                "cheap_seat_token_pct": cheap_seat_pct,
                "prompt_tokens_counted": tot_prompt,
                "total_tokens_counted": tot_tokens,
            },
        }

    argon_sub_ratio = None
    if ARGON_LATEST.is_file():
        try:
            argon_sub_ratio = json.loads(ARGON_LATEST.read_text(errors="replace")).get("subagent_ratio")
        except (OSError, ValueError):
            pass

    return {
        "as_of": now.isoformat(),
        "date_pt": today.isoformat(),
        "box_monthly_pace": load_box_monthly_pace(hetzner_monthly),
        "sources": {
            "ai_studio_proxy_all_time_tokens": studio_all["total_tokens"],
            "argon_subagent_ratio": argon_sub_ratio,
        },
        "config": cfg,
        "all_time": build_window(None),
        "last_30d": build_window(30),
        "last_7d": build_window(7),
    }


def render_markdown(snap: dict) -> str:
    a = snap["all_time"]
    w30 = snap["last_30d"]
    w7 = snap["last_7d"]
    cfg = snap["config"]

    def fmt_ratio(v):
        return f"{v:.2f}x" if v is not None else "n/a"

    def fmt_cpp(t: dict) -> str:
        c, p, r = t["commits"], t["human_prompts"], t["commits_per_prompt"]
        return f"{c} / {p} ({r:.2f})" if r is not None else f"{c} / {p} (n/a)"

    def fmt_pct(v):
        return f"{v:.1f}%" if v is not None else "n/a"

    lines = [
        f"# zaz-astra Running Ops Telemetry",
        f"",
        f"Updated: `{snap['as_of']}` (PT) · VPS start: `{cfg['vps_start_date']}`",
    ]
    bm = snap.get("box_monthly_pace")
    if bm:
        lines.append(
            f"Total box monthly pace: **${bm['total_box_monthly_usd']:,.2f}/mo** "
            f"(AI all-in ${bm['ai_monthly_usd']:,.2f} + Hetzner ${bm['hetzner_monthly_usd']:,.2f}; Hetzner excluded from biweekly AI emails)"
        )
    lines.extend([
        f"",
        f"| Metric | Last 7d | Last 30d | All-time ({a['window_days']}d) |",
        f"|---|---:|---:|---:|",
        f"| **Commits / human prompts** | {fmt_cpp(w7['throughput'])} | {fmt_cpp(w30['throughput'])} | {fmt_cpp(a['throughput'])} |",
        f"| **Commits vs. failures/heals** | {w7['shipping_vs_firefighting']['commits']} / {w7['shipping_vs_firefighting']['service_failures'] + w7['shipping_vs_firefighting']['photon_heals']} ({fmt_ratio(w7['shipping_vs_firefighting']['ship_to_fire_ratio'])}) | {w30['shipping_vs_firefighting']['commits']} / {w30['shipping_vs_firefighting']['service_failures'] + w30['shipping_vs_firefighting']['photon_heals']} ({fmt_ratio(w30['shipping_vs_firefighting']['ship_to_fire_ratio'])}) | {a['shipping_vs_firefighting']['commits']} / {a['shipping_vs_firefighting']['service_failures'] + a['shipping_vs_firefighting']['photon_heals']} ({fmt_ratio(a['shipping_vs_firefighting']['ship_to_fire_ratio'])}) |",
        f"| **Cache hit %** | {fmt_pct(w7['efficiency']['cache_hit_pct'])} | {fmt_pct(w30['efficiency']['cache_hit_pct'])} | {fmt_pct(a['efficiency']['cache_hit_pct'])} |",
        f"| **Cheap-seat token %** | {fmt_pct(w7['efficiency']['cheap_seat_token_pct'])} | {fmt_pct(w30['efficiency']['cheap_seat_token_pct'])} | {fmt_pct(a['efficiency']['cheap_seat_token_pct'])} |",
        f"",
        f"## All-time Human Prompts by Harness",
    ])
    for src, cnt in sorted(a["throughput"]["prompts_by_harness"].items(), key=lambda x: -x[1]):
        lines.append(f"- `{src}`: **{cnt}** prompts")
    sub_r = snap["sources"].get("argon_subagent_ratio")
    lines.append("")
    lines.append(f"Grok Build subagent ratio (latest `argon-usage.json`): `{sub_r}`")
    lines.append("")
    lines.append("Notes: Systemd journal (~7d upstream) is cached incrementally in `vps-telemetry-api-cache.json`. AI spend polling lives exclusively in `ai-spend-review.py`.")
    return "\n".join(lines) + "\n"


def upsert_jsonl(snap: dict) -> None:
    bm = snap.get("box_monthly_pace") or {}
    row = {
        "date_pt": snap["date_pt"],
        "as_of": snap["as_of"],
        "box_monthly_usd": bm.get("total_box_monthly_usd"),
        "d7_commits": snap["last_7d"]["throughput"]["commits"],
        "d7_prompts": snap["last_7d"]["throughput"]["human_prompts"],
        "d7_commits_per_prompt": snap["last_7d"]["throughput"]["commits_per_prompt"],
        "d7_ship_to_fire": snap["last_7d"]["shipping_vs_firefighting"]["ship_to_fire_ratio"],
        "d30_cache_hit_pct": snap["last_30d"]["efficiency"]["cache_hit_pct"],
        "d30_cheap_seat_pct": snap["last_30d"]["efficiency"]["cheap_seat_token_pct"],
    }
    rows: list[dict] = []
    if JSONL_PATH.is_file():
        for line in JSONL_PATH.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except ValueError:
                continue
            if old.get("date_pt") != row["date_pt"]:
                rows.append(old)
    rows.append(row)
    JSONL_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    os.chmod(JSONL_PATH, 0o600)


def write_outputs(snap: dict, md: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    JSON_PATH.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    os.chmod(JSON_PATH, 0o600)
    MD_PATH.write_text(md, encoding="utf-8")
    os.chmod(MD_PATH, 0o600)
    upsert_jsonl(snap)


def self_test() -> int:
    assert parse_any_ts(1794267852230).year == 2026
    assert parse_any_ts(1794265639).year == 2026
    assert parse_any_ts("2026-11-15T00:06:13.803196996Z").year == 2026
    assert commits_per_prompt(15, 100) == 0.15
    assert commits_per_prompt(5, 0) is None
    print("vps-telemetry self-test ok")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    cfg = load_config()
    cache = load_cache()
    dry = "--dry-run" in argv
    want_json = "--json" in argv
    snap = compute_snapshot(cfg, cache)
    md = render_markdown(snap)
    if want_json:
        sys.stdout.write(json.dumps(snap, indent=2) + "\n")
        return 0
    if dry:
        sys.stdout.write(md)
        return 0
    save_config_if_missing(cfg)
    save_cache(cache)
    write_outputs(snap, md)
    t7 = snap["last_7d"]["throughput"]
    print(
        f"vps-telemetry: ok date={snap['date_pt']} "
        f"d7_commits_per_prompt={t7['commits']}/{t7['human_prompts']} ({t7['commits_per_prompt']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
