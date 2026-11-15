#!/usr/bin/env python3
"""Daily VPS telemetry aggregator (dollars, human vs agent hours, leverage, shipping vs firefighting, cache/cheap-seat share).

Writes quiet local snapshots under ~/.local/state/astra/ (mode 700/600, out of git):
  - vps-telemetry-config.json   (multipliers, VPS rate, exclusions)
  - vps-telemetry-api-cache.json (incremental daily xAI + OpenRouter + journal cache)
  - vps-telemetry.json          (latest full rollup: all-time, 30d, 7d)
  - vps-telemetry.jsonl         (one compact row per PT day, deduped on date)
  - vps-telemetry.md            (human-readable dashboard)

Privacy / security:
  - Never reads Budget Bot bank/ledger files (~/.local/state/budget-bot/).
  - Never reads or stores prompt text (display/text/content fields are ignored).
  - Never sends email or Pushover notifications.

Usage:
  vps-telemetry.py              # collect, update cache, write state + markdown
  vps-telemetry.py --dry-run    # compute and print markdown, do not write state
  vps-telemetry.py --json       # print latest/computed JSON to stdout (no write)
  vps-telemetry.py --self-test  # unit checks, no network or state writes
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
STATE_DIR = Path(os.environ.get("VPS_TELEMETRY_STATE", str(Path.home() / ".local/state/astra")))
CONFIG_PATH = STATE_DIR / "vps-telemetry-config.json"
CACHE_PATH = STATE_DIR / "vps-telemetry-api-cache.json"
JSON_PATH = STATE_DIR / "vps-telemetry.json"
JSONL_PATH = STATE_DIR / "vps-telemetry.jsonl"
MD_PATH = STATE_DIR / "vps-telemetry.md"

XAI_ENV = Path(os.environ.get("XAI_MANAGEMENT_ENV", str(STATE_DIR / "xai-management.env")))
OR_ENV = Path(os.environ.get("OR_MANAGEMENT_ENV", str(STATE_DIR / "openrouter-management.env")))
AI_STUDIO_LOG = STATE_DIR / "ai-studio-usage.jsonl"
ARGON_LATEST = STATE_DIR / "argon-usage.json"
PHOTON_HEAL_LOG = STATE_DIR / "photon-heal.log"

DEFAULT_CONFIG = {
    "vps_start_date": "2026-05-18",
    "vps_monthly_usd": 15.32,
    "vps_multiplier": 1.0,
    "api_overage_multiplier": 0.85,
    "subscriptions_multiplier": 0.0,
    "one_off_exclusions_usd": 0.0,
    "excluded_notes": "Personal subscriptions (e.g. Claude Max $100, SuperGrok/Plus) and ~$150 bankruptcy tokens excluded.",
    "human_gap_minutes": 15.0,
    "human_burst_floor_minutes": 3.0,
    "agent_gap_minutes": 15.0,
    "agy_turn_cap_minutes": 30.0,
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

# List rates ($ per 1M tokens) for non-billed / grant-covered AI Studio estimate only.
AI_STUDIO_RATES = {
    "hydrogen": (0.75, 0.075, 3.75),
    "flash-lite": (0.25, 0.025, 1.50),
    "flash": (0.75, 0.075, 3.75),
    "argon": (2.00, 0.20, 10.00),
}


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
                return data
        except (OSError, ValueError):
            pass
    return {"xai_daily": {}, "or_daily": {}, "churn_daily": {}}


def save_cache(cache: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(CACHE_PATH, 0o600)


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


def bucket_active_hours(
    timestamps: list[datetime],
    gap_minutes: float = 15.0,
    floor_minutes: float = 3.0,
    dedup_seconds: float = 30.0,
) -> float:
    """Cluster timestamps into active bursts and sum elapsed active time (hours).

    Events closer than `dedup_seconds` collapse (parallel CLIs).
    Gaps <= `gap_minutes` add exact elapsed time; gaps > `gap_minutes` close the
    previous burst (adding `floor_minutes` tail/isolation credit) and open a new one.
    """
    if not timestamps:
        return 0.0
    pts = sorted(t.astimezone(timezone.utc) for t in timestamps)
    deduped = [pts[0]]
    for t in pts[1:]:
        if (t - deduped[-1]).total_seconds() >= dedup_seconds:
            deduped.append(t)

    gap_sec = gap_minutes * 60.0
    floor_sec = floor_minutes * 60.0
    total_sec = 0.0
    burst_start = deduped[0]
    burst_last = deduped[0]

    for t in deduped[1:]:
        dt = (t - burst_last).total_seconds()
        if dt <= gap_sec:
            burst_last = t
        else:
            span = (burst_last - burst_start).total_seconds()
            total_sec += max(span, floor_sec)
            burst_start = t
            burst_last = t

    span = (burst_last - burst_start).total_seconds()
    total_sec += max(span, floor_sec)
    return round(total_sec / 3600.0, 2)


def fetch_xai_daily(start: date, end: date, cache: dict) -> tuple[bool, str | None]:
    env = load_env(XAI_ENV)
    key = env.get("XAI_MANAGEMENT_KEY", "")
    if not key:
        return False, "no XAI_MANAGEMENT_KEY"
    status, meta = http_json("https://management-api.x.ai/auth/management-keys/validation", key)
    team = meta.get("teamId") if isinstance(meta, dict) else None
    if status != 200 or not team:
        return False, f"validation {status}"

    body = {
        "analyticsRequest": {
            "timeRange": {
                "startTime": f"{start.isoformat()} 00:00:00",
                "endTime": f"{(end + timedelta(days=1)).isoformat()} 00:00:00",
                "timezone": "America/Los_Angeles",
            },
            "timeUnit": "TIME_UNIT_DAY",
            "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
            "groupBy": ["description"],
            "filters": [],
        }
    }
    status, data = http_json(f"https://management-api.x.ai/v1/billing/teams/{team}/usage", key, body)
    if status != 200 or not isinstance(data, dict):
        return False, f"usage {status}"

    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for series in data.get("timeSeries") or []:
        labels = series.get("groupLabels") or series.get("group") or []
        label = (labels[0] if labels else "unknown").strip()
        for pt in series.get("dataPoints") or []:
            ts_raw = pt.get("timestamp") or pt.get("time") or ""
            dt = parse_any_ts(ts_raw)
            day_str = dt.astimezone(TZ).date().isoformat() if dt else str(ts_raw)[:10]
            vals = pt.get("values") or []
            if len(day_str) == 10 and vals:
                try:
                    usd = float(vals[0])
                except (TypeError, ValueError):
                    usd = 0.0
                if usd > 0:
                    daily[day_str][label] += usd

    # Replace fetched range days cleanly; preserve any older cached days.
    xai_cache = cache.setdefault("xai_daily", {})
    for d_str, models in daily.items():
        xai_cache[d_str] = {
            "usd": round(sum(models.values()), 6),
            "by_model": {m: round(u, 6) for m, u in models.items()},
        }
    return True, "truncated" if data.get("limitReached") else None


def fetch_or_daily(cache: dict) -> tuple[bool, str | None]:
    env = load_env(OR_ENV)
    key = env.get("OPENROUTER_MANAGEMENT_KEY", "")
    if not key:
        return False, "no OPENROUTER_MANAGEMENT_KEY"
    status, data = http_json("https://openrouter.ai/api/v1/activity", key)
    if status != 200 or not isinstance(data, dict):
        return False, f"activity {status}"

    grouped: dict[str, dict] = {}
    for row in data.get("data") or []:
        if not isinstance(row, dict):
            continue
        day_str = str(row.get("date") or "")[:10]
        if len(day_str) != 10:
            continue
        entry = grouped.setdefault(
            day_str,
            {
                "credits": 0.0,
                "byok": 0.0,
                "usd": 0.0,
                "by_model": {},
                "reqs": 0,
                "prompt_tokens": 0,
                "cached_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "cheap_tokens": 0,
                "total_tokens": 0,
            },
        )
        model = str(row.get("model_permaslug") or row.get("model") or "unknown")
        cred = float(row.get("usage") or 0.0)
        byok = float(row.get("byok_usage_inference") or 0.0)
        entry["credits"] += cred
        entry["byok"] += byok
        entry["usd"] += cred + byok
        entry["by_model"][model] = round(entry["by_model"].get(model, 0.0) + cred + byok, 6)

        reqs = int(row.get("requests") or 0)
        prompt = int(row.get("prompt_tokens") or 0)
        cached = int(row.get("cached_tokens") or 0)
        comp = int(row.get("completion_tokens") or 0)
        reas = int(row.get("reasoning_tokens") or 0)
        tot = prompt + comp + reas
        entry["reqs"] += reqs
        entry["prompt_tokens"] += prompt
        entry["cached_tokens"] += cached
        entry["completion_tokens"] += comp
        entry["reasoning_tokens"] += reas
        entry["total_tokens"] += tot
        if any(m in model.lower() for m in CHEAP_MODEL_MARKERS):
            entry["cheap_tokens"] += tot

    or_cache = cache.setdefault("or_daily", {})
    for d_str, entry in grouped.items():
        entry["credits"] = round(entry["credits"], 6)
        entry["byok"] = round(entry["byok"], 6)
        entry["usd"] = round(entry["usd"], 6)
        or_cache[d_str] = entry
    return True, None


def collect_ai_studio(since_date: date | None = None) -> dict:
    out = {
        "reqs": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "thoughts_tokens": 0,
        "total_tokens": 0,
        "est_list_usd": 0.0,
        "by_day": defaultdict(lambda: {"reqs": 0, "prompt_tokens": 0, "cached_tokens": 0, "total_tokens": 0, "est_list_usd": 0.0}),
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
        model = str(row.get("model") or "").lower()
        prompt = int(row.get("prompt_tokens") or 0)
        cached = int(row.get("cached_tokens") or 0)
        fresh = max(0, prompt - cached)
        cand = int(row.get("candidates_tokens") or 0)
        thoughts = int(row.get("thoughts_tokens") or 0)
        tot = prompt + cand + thoughts

        rates = AI_STUDIO_RATES["flash"]
        for key, r in AI_STUDIO_RATES.items():
            if key in model:
                rates = r
                break
        usd = (fresh * rates[0] + cached * rates[1] + (cand + thoughts) * rates[2]) / 1_000_000.0

        out["reqs"] += 1
        out["prompt_tokens"] += prompt
        out["cached_tokens"] += cached
        out["output_tokens"] += cand
        out["thoughts_tokens"] += thoughts
        out["total_tokens"] += tot
        out["est_list_usd"] += usd

        d_str = d_pt.isoformat()
        day = out["by_day"][d_str]
        day["reqs"] += 1
        day["prompt_tokens"] += prompt
        day["cached_tokens"] += cached
        day["total_tokens"] += tot
        day["est_list_usd"] += usd

    out["est_list_usd"] = round(out["est_list_usd"], 4)
    return out


def collect_human_and_agent_timestamps() -> dict:
    """Collect timestamps (no prompt text) across all harnesses."""
    human_by_source: dict[str, list[datetime]] = defaultdict(list)
    agent_by_source: dict[str, list[datetime]] = defaultdict(list)
    extra_tokens = {
        "grok_prompt": 0,
        "grok_cached": 0,
        "grok_total": 0,
        "grok_cheap": 0,
        "codex_prompt": 0,
        "codex_cached": 0,
        "codex_total": 0,
        "codex_cheap": 0,
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

    for root in ("/root/.claude/projects", "/home/dev/.claude/projects"):
        for fp in glob.glob(f"{root}/**/*.jsonl", recursive=True):
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
                            agent_by_source["claude"].append(dt)
            except OSError:
                continue

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

    for root in ("/root/.codex/sessions", "/home/dev/.codex/sessions"):
        for fp in glob.glob(f"{root}/**/*.jsonl", recursive=True):
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
                            agent_by_source["codex"].append(dt)
            except OSError:
                continue

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

        for fp in glob.glob(f"{root}/**/events.jsonl", recursive=True):
            try:
                with open(fp, errors="replace") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        dt = parse_any_ts(d.get("ts") or d.get("timestamp"))
                        if dt:
                            agent_by_source["grok_build"].append(dt)
            except OSError:
                continue

        for fp in glob.glob(f"{root}/**/usage.json", recursive=True):
            is_subagent = "/subagents/" in fp
            try:
                d = json.loads(Path(fp).read_text(errors="replace"))
            except (OSError, ValueError):
                continue
            for turn in d.get("turns") or []:
                dt = parse_any_ts(turn.get("endedAt"))
                if dt:
                    agent_by_source["grok_build"].append(dt)
                if is_subagent:
                    continue
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

    # 4. Antigravity (agy)
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

    for fp in glob.glob("/root/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl"):
        try:
            with open(fp, errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    dt = parse_any_ts(d.get("created_at"))
                    if not dt:
                        continue
                    if d.get("source") == "USER_EXPLICIT" and d.get("type") == "USER_INPUT":
                        human_by_source["antigravity"].append(dt)
                    else:
                        agent_by_source["antigravity"].append(dt)
        except OSError:
            continue

    # 5. Hermes
    hdb = Path("/root/.hermes/state.db")
    if hdb.is_file():
        try:
            conn = sqlite3.connect(f"file:{hdb}?mode=ro", uri=True)
            for (role, ts) in conn.execute("SELECT role, timestamp FROM messages WHERE timestamp IS NOT NULL"):
                dt = parse_any_ts(ts)
                if not dt:
                    continue
                if role == "user":
                    human_by_source["hermes"].append(dt)
                else:
                    agent_by_source["hermes"].append(dt)
            conn.close()
        except sqlite3.Error:
            pass

    # 6. Non-auto git commits (only count as standalone human touchpoints when >20m from any agent prompt)
    all_prompt_pts = sorted(t for k, lst in human_by_source.items() for t in lst)
    git_commits: list[datetime] = []
    manual_git: list[datetime] = []
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
                    if not any(abs((dt - p).total_seconds()) <= 1200 for p in all_prompt_pts):
                        manual_git.append(dt)
        except Exception:
            pass
    if manual_git:
        human_by_source["git_manual"] = manual_git

    return {
        "human_by_source": human_by_source,
        "agent_by_source": agent_by_source,
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


def compute_snapshot(cfg: dict, cache: dict, now: datetime | None = None, skip_network: bool = False) -> dict:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    today = now.date()
    start_date = date.fromisoformat(cfg["vps_start_date"])

    xai_ok, xai_err = (True, "skipped") if skip_network else fetch_xai_daily(start_date, today, cache)
    or_ok, or_err = (True, "skipped") if skip_network else fetch_or_daily(cache)
    churn_daily = collect_churn(cache)

    ts_data = collect_human_and_agent_timestamps()
    human_src = ts_data["human_by_source"]
    agent_src = ts_data["agent_by_source"]
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
        vps_raw = round(elapsed_days * (float(cfg["vps_monthly_usd"]) / 30.4375), 2)
        vps_usd = round(vps_raw * float(cfg["vps_multiplier"]), 2)

        xai_raw = 0.0
        for d_str, entry in (cache.get("xai_daily") or {}).items():
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                continue
            if w_start <= d <= today:
                xai_raw += float(entry.get("usd") or 0.0)

        or_raw = 0.0
        or_credits = 0.0
        or_byok = 0.0
        or_prompt = 0
        or_cached = 0
        or_total = 0
        or_cheap = 0
        for d_str, entry in (cache.get("or_daily") or {}).items():
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                continue
            if w_start <= d <= today:
                or_raw += float(entry.get("usd") or 0.0)
                or_credits += float(entry.get("credits") or 0.0)
                or_byok += float(entry.get("byok") or 0.0)
                or_prompt += int(entry.get("prompt_tokens") or 0)
                or_cached += int(entry.get("cached_tokens") or 0)
                or_total += int(entry.get("total_tokens") or 0)
                or_cheap += int(entry.get("cheap_tokens") or 0)

        api_mult = float(cfg["api_overage_multiplier"])
        excl = float(cfg["one_off_exclusions_usd"]) if days is None else 0.0
        api_raw = round(xai_raw + or_raw, 2)
        api_weighted = round(max(0.0, api_raw - excl) * api_mult, 2)
        total_vps_usd = round(vps_usd + api_weighted, 2)

        studio_win = collect_ai_studio(since_date=w_start)

        # Hours
        gap_h = float(cfg["human_gap_minutes"])
        floor_h = float(cfg["human_burst_floor_minutes"])
        gap_a = float(cfg["agent_gap_minutes"])

        all_human = [t for lst in human_src.values() for t in filter_ts(lst, cutoff)]
        human_hours = bucket_active_hours(all_human, gap_minutes=gap_h, floor_minutes=floor_h)
        human_breakdown = {
            k: bucket_active_hours(filter_ts(v, cutoff), gap_minutes=gap_h, floor_minutes=floor_h)
            for k, v in human_src.items()
            if filter_ts(v, cutoff)
        }

        all_agent = [t for lst in agent_src.values() for t in filter_ts(lst, cutoff)]
        agent_hours = bucket_active_hours(all_agent, gap_minutes=gap_a, floor_minutes=2.0)
        agent_breakdown = {
            k: bucket_active_hours(filter_ts(v, cutoff), gap_minutes=gap_a, floor_minutes=2.0)
            for k, v in agent_src.items()
            if filter_ts(v, cutoff)
        }

        leverage_ratio = round(agent_hours / human_hours, 2) if human_hours > 0 else None
        usd_per_human_hour = round(total_vps_usd / human_hours, 2) if human_hours > 0 else None

        # Shipping vs firefighting
        commits_n = len(filter_ts(git_commits, cutoff))
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

        # Cheap seat & cache share (OpenRouter + AI Studio proxy + Grok Build when all-time)
        tot_prompt = or_prompt + studio_win["prompt_tokens"]
        tot_cached = or_cached + studio_win["cached_tokens"]
        tot_tokens = or_total + studio_win["total_tokens"]
        tot_cheap = or_cheap + studio_win["total_tokens"]  # AI Studio proxy seats are all flash/hydrogen/argon
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
            "dollars": {
                "vps_hosting_usd": vps_usd,
                "xai_raw_usd": round(xai_raw, 2),
                "openrouter_raw_usd": round(or_raw, 2),
                "openrouter_credits_usd": round(or_credits, 2),
                "openrouter_byok_usd": round(or_byok, 2),
                "api_raw_usd": api_raw,
                "api_weighted_usd": api_weighted,
                "api_multiplier": api_mult,
                "subscriptions_vps_usd": 0.0,
                "total_vps_usd": total_vps_usd,
                "ai_studio_proxy_est_list_usd": studio_win["est_list_usd"],
                "ai_studio_proxy_reqs": studio_win["reqs"],
            },
            "hours": {
                "human_hours": human_hours,
                "agent_hours": agent_hours,
                "leverage_ratio": leverage_ratio,
                "usd_per_human_hour": usd_per_human_hour,
                "human_by_harness": human_breakdown,
                "agent_by_harness": agent_breakdown,
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
        "sources": {
            "xai": {"ok": xai_ok, "note": xai_err},
            "openrouter": {"ok": or_ok, "note": or_err},
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

    def fmt_usd(v):
        return f"${v:,.2f}"

    def fmt_ratio(v):
        return f"{v:.2f}x" if v is not None else "n/a"

    def fmt_pct(v):
        return f"{v:.1f}%" if v is not None else "n/a"

    lines = [
        f"# zaz-astra Running Telemetry Log",
        f"",
        f"Updated: `{snap['as_of']}` (PT) · VPS start: `{cfg['vps_start_date']}`",
        f"Multipliers: VPS hosting `{cfg['vps_multiplier']*100:.0f}%`, box API/overages `{cfg['api_overage_multiplier']*100:.0f}%`, flat subs `{cfg['subscriptions_multiplier']*100:.0f}%` (personal)",
        f"Exclusions: {cfg['excluded_notes']}",
        f"",
        f"| Metric | Last 7d | Last 30d | All-time ({a['window_days']}d) |",
        f"|---|---:|---:|---:|",
        f"| **VPS-weighted total ($)** | {fmt_usd(w7['dollars']['total_vps_usd'])} | {fmt_usd(w30['dollars']['total_vps_usd'])} | {fmt_usd(a['dollars']['total_vps_usd'])} |",
        f"| — Hetzner VPS (100%) | {fmt_usd(w7['dollars']['vps_hosting_usd'])} | {fmt_usd(w30['dollars']['vps_hosting_usd'])} | {fmt_usd(a['dollars']['vps_hosting_usd'])} |",
        f"| — API weighted ({cfg['api_overage_multiplier']*100:.0f}% of xAI+OR) | {fmt_usd(w7['dollars']['api_weighted_usd'])} | {fmt_usd(w30['dollars']['api_weighted_usd'])} | {fmt_usd(a['dollars']['api_weighted_usd'])} |",
        f"| — Raw xAI / OpenRouter | {fmt_usd(w7['dollars']['xai_raw_usd'])} / {fmt_usd(w7['dollars']['openrouter_raw_usd'])} | {fmt_usd(w30['dollars']['xai_raw_usd'])} / {fmt_usd(w30['dollars']['openrouter_raw_usd'])} | {fmt_usd(a['dollars']['xai_raw_usd'])} / {fmt_usd(a['dollars']['openrouter_raw_usd'])} |",
        f"| — AI Studio proxy est. (grant/non-cash) | {fmt_usd(w7['dollars']['ai_studio_proxy_est_list_usd'])} | {fmt_usd(w30['dollars']['ai_studio_proxy_est_list_usd'])} | {fmt_usd(a['dollars']['ai_studio_proxy_est_list_usd'])} |",
        f"| **Human hands-on hours** | {w7['hours']['human_hours']:.2f}h | {w30['hours']['human_hours']:.2f}h | {a['hours']['human_hours']:.2f}h |",
        f"| **Agent wall-clock hours** | {w7['hours']['agent_hours']:.2f}h | {w30['hours']['agent_hours']:.2f}h | {a['hours']['agent_hours']:.2f}h |",
        f"| **Leverage ratio (agent / human)** | {fmt_ratio(w7['hours']['leverage_ratio'])} | {fmt_ratio(w30['hours']['leverage_ratio'])} | {fmt_ratio(a['hours']['leverage_ratio'])} |",
        f"| **$/human-hour** | {fmt_usd(w7['hours']['usd_per_human_hour'] or 0)} | {fmt_usd(w30['hours']['usd_per_human_hour'] or 0)} | {fmt_usd(a['hours']['usd_per_human_hour'] or 0)} |",
        f"| **Commits vs. failures/heals** | {w7['shipping_vs_firefighting']['commits']} / {w7['shipping_vs_firefighting']['service_failures'] + w7['shipping_vs_firefighting']['photon_heals']} ({fmt_ratio(w7['shipping_vs_firefighting']['ship_to_fire_ratio'])}) | {w30['shipping_vs_firefighting']['commits']} / {w30['shipping_vs_firefighting']['service_failures'] + w30['shipping_vs_firefighting']['photon_heals']} ({fmt_ratio(w30['shipping_vs_firefighting']['ship_to_fire_ratio'])}) | {a['shipping_vs_firefighting']['commits']} / {a['shipping_vs_firefighting']['service_failures'] + a['shipping_vs_firefighting']['photon_heals']} ({fmt_ratio(a['shipping_vs_firefighting']['ship_to_fire_ratio'])}) |",
        f"| **Cache hit %** | {fmt_pct(w7['efficiency']['cache_hit_pct'])} | {fmt_pct(w30['efficiency']['cache_hit_pct'])} | {fmt_pct(a['efficiency']['cache_hit_pct'])} |",
        f"| **Cheap-seat token %** | {fmt_pct(w7['efficiency']['cheap_seat_token_pct'])} | {fmt_pct(w30['efficiency']['cheap_seat_token_pct'])} | {fmt_pct(a['efficiency']['cheap_seat_token_pct'])} |",
        f"",
        f"## All-time Hours Breakdown by Harness",
    ]
    for src, hrs in sorted(a["hours"]["human_by_harness"].items(), key=lambda x: -x[1]):
        ag = a["hours"]["agent_by_harness"].get(src, 0.0)
        lines.append(f"- `{src}`: **{hrs:.2f}h** human · **{ag:.2f}h** agent")
    sub_r = snap["sources"].get("argon_subagent_ratio")
    lines.append("")
    lines.append(f"Grok Build subagent ratio (latest `argon-usage.json`): `{sub_r}`")
    lines.append("")
    lines.append("Notes: Antigravity direct OAuth/Vertex grounding fees and web chats (grok.com / claude.ai) are not visible to the box. OpenRouter activity (~30d upstream) and systemd journal (~7d upstream) are cached incrementally in `vps-telemetry-api-cache.json` going forward; pre-Nov Claude project `.jsonl`s were purged by `claude-journal-cleanup`, so `Last 7d` is the cleanest leverage & ship/fire baseline.")
    return "\n".join(lines) + "\n"


def upsert_jsonl(snap: dict) -> None:
    row = {
        "date_pt": snap["date_pt"],
        "as_of": snap["as_of"],
        "all_time_vps_usd": snap["all_time"]["dollars"]["total_vps_usd"],
        "all_time_human_hours": snap["all_time"]["hours"]["human_hours"],
        "all_time_agent_hours": snap["all_time"]["hours"]["agent_hours"],
        "all_time_leverage": snap["all_time"]["hours"]["leverage_ratio"],
        "d7_vps_usd": snap["last_7d"]["dollars"]["total_vps_usd"],
        "d7_human_hours": snap["last_7d"]["hours"]["human_hours"],
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
    t0 = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    # Single isolated prompt -> 3 min floor = 0.05h
    assert bucket_active_hours([t0], gap_minutes=15, floor_minutes=3) == 0.05
    # Two prompts 10 min apart -> 10 min = 0.17h
    assert bucket_active_hours([t0, t0 + timedelta(minutes=10)], gap_minutes=15, floor_minutes=3) == 0.17
    # Parallel duplicate within 10s collapses -> 0.05h
    assert bucket_active_hours([t0, t0 + timedelta(seconds=10)], gap_minutes=15, floor_minutes=3) == 0.05
    # Two separate bursts 2h apart -> 3m + 3m = 6m = 0.10h
    assert bucket_active_hours([t0, t0 + timedelta(hours=2)], gap_minutes=15, floor_minutes=3) == 0.10
    assert parse_any_ts(1794267852230).year == 2026
    assert parse_any_ts(1794265639).year == 2026
    assert parse_any_ts("2026-11-15T00:06:13.803196996Z").year == 2026
    print("vps-telemetry self-test ok")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    cfg = load_config()
    cache = load_cache()
    dry = "--dry-run" in argv
    want_json = "--json" in argv
    snap = compute_snapshot(cfg, cache, skip_network=False)
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
    print(
        f"vps-telemetry: ok date={snap['date_pt']} "
        f"vps_usd=${snap['all_time']['dollars']['total_vps_usd']:.2f} "
        f"human={snap['all_time']['hours']['human_hours']:.2f}h "
        f"agent={snap['all_time']['hours']['agent_hours']:.2f}h"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
