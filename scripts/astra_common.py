#!/usr/bin/env python3
"""Shared helpers for astra-config Python scripts (load_env, http_json, validate_xai_team)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


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


def validate_xai_team(key: str) -> tuple[str | None, str | None]:
    """Validate xAI management key and return (team_id, error_reason)."""
    if not key:
        return None, "no XAI_MANAGEMENT_KEY"
    status, meta = http_json("https://management-api.x.ai/auth/management-keys/validation", key)
    team = meta.get("teamId") if isinstance(meta, dict) else None
    if status != 200 or not team:
        return None, f"validation {status}"
    return str(team), None
