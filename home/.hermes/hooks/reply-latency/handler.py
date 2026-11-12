"""One JSONL row per Hermes reply. Wall clock from agent:start to agent:end.

That span is the user-visible turn, including tool and script time. The row
stores model, platform, duration, and step count. It does not store the
message, the reply, or the chat id.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

LOG = os.environ.get(
    "HERMES_REPLY_LATENCY_LOG",
    "/root/.local/state/astra/hermes-reply-latency.jsonl",
)

ALIAS_MAP_CACHE = "/root/.local/state/astra/openrouter-alias-map.json"
_STATIC_MAP = {
    "~openai/gpt-terra-latest": "openai/gpt-6.1-terra",
    "~openai/gpt-sol-latest": "openai/gpt-6.1-sol",
    "~google/gemini-pro-latest": "google/gemini-4-carbon",
    "~google/gemini-flash-latest": "google/gemini-4-hydrogen",
    "~anthropic/claude-sonnet-latest": "anthropic/claude-sonnet-5.5",
    "~anthropic/claude-opus-latest": "anthropic/claude-opus-5.5",
    "~x-ai/grok-latest": "x-ai/grok-4.7",
}

# session_id -> [monotonic_start, step_count]
_open: dict[str, list] = {}


def _resolve_model(model: str, ctx: dict) -> tuple[str, str]:
    """Returns (resolved_model, requested_model)."""
    requested = str(model or "").strip()
    served = str(ctx.get("served_model") or "").strip()
    if served and served != requested:
        return served, requested
    if not requested.startswith("~"):
        return requested, requested

    target = _STATIC_MAP.get(requested)
    try:
        if os.path.exists(ALIAS_MAP_CACHE):
            with open(ALIAS_MAP_CACHE) as f:
                data = json.load(f)
                if requested in data:
                    return str(data[requested]), requested
    except Exception:
        pass
    return target or requested, requested


def handle(event_type: str, context: dict) -> None:
    ctx = context or {}
    sid = str(ctx.get("session_id") or "")
    now = time.monotonic()
    if event_type == "agent:start":
        _open[sid] = [now, 0]
        return
    if event_type == "agent:step":
        rec = _open.get(sid)
        if rec is not None:
            rec[1] += 1
        return
    if event_type != "agent:end":
        return
    rec = _open.pop(sid, None)
    if rec is None:
        return
    response = str(ctx.get("response") or "")
    text = response.strip()
    silent = text == "" or text == "[SILENT]"
    model_raw = str(ctx.get("model") or "")
    resolved_model, requested_model = _resolve_model(model_raw, ctx)

    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": str(ctx.get("platform") or ""),
        "model": resolved_model,
        "provider": str(ctx.get("provider") or ""),
        "e2e_ms": int((now - rec[0]) * 1000),
        "steps": int(rec[1]),
        "ok": bool(text) and not silent,
        "silent": silent,
        "response_chars": len(response),
    }
    if requested_model and requested_model != resolved_model:
        row["requested_model"] = requested_model
    _append(row)


def _append(row: dict) -> None:
    os.makedirs(os.path.dirname(LOG), mode=0o700, exist_ok=True)
    fd = os.open(LOG, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)
