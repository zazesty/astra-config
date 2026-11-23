"""Binary Photon inline asks for buy-queue confirms and interval-bill candidates.

Outbound asks go to the local Hermes Photon sidecar (/send). Inbound "y"/"n"
(or "y <code >" / "n <code >") replies are intercepted at $0 by photon-canned
and resolved here via `budget-bot photon-ask answer`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import state_dir

ASK_TTL_DAYS = 7

_YES = frozenset({"y", "yes", "yep", "yeah", "enroll"})
_NO = frozenset({"n", "no", "nope", "dismiss", "skip"})


def photon_asks_path() -> Path:
    return state_dir() / "photon_asks.json"


def _now_utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_doc(now: datetime | None = None) -> tuple[dict[str, Any], bool]:
    path = photon_asks_path()
    if not path.is_file():
        return {"next_seq": 1, "pending": [], "resolved": []}, False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    doc: dict[str, Any] = {
        "next_seq": max(1, int(raw.get("next_seq") or 1)),
        "pending": list(raw.get("pending") or []),
        "resolved": list(raw.get("resolved") or []),
    }
    cur = _now_utc(now)
    kept: list[dict[str, Any]] = []
    pruned = False
    for row in doc["pending"]:
        if not isinstance(row, dict):
            pruned = True
            continue
        exp = _parse_iso(row.get("expires_at"))
        if exp is not None and exp <= cur:
            pruned = True
            continue
        kept.append(row)
    doc["pending"] = kept
    return doc, pruned


def _save_doc(doc: dict[str, Any]) -> None:
    path = photon_asks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _hermes_home() -> Path:
    val = os.environ.get("HERMES_HOME", "").strip()
    return Path(val) if val else (Path.home() / ".hermes")


def _resolve_space_id(hermes: Path) -> str:
    direct = os.environ.get("PHOTON_HOME_CHANNEL", "").strip()
    if direct:
        return direct
    env_file = hermes / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "PHOTON_HOME_CHANNEL":
                    val = v.strip().strip("'\"")
                    if val:
                        return val
        except OSError:
            pass
    db_path = hermes / "state.db"
    if db_path.is_file():
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = con.execute(
                """SELECT chat_id FROM sessions WHERE source='photon'
                   ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 1"""
            ).fetchone()
            con.close()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
    return ""


def send_photon_text(text: str) -> tuple[bool, str]:
    """Send one plain-text message to the Photon home channel (or test override cmd)."""
    cmd = os.environ.get("BUDGET_BOT_PHOTON_CMD", "").strip()
    if cmd:
        try:
            proc = subprocess.run(
                [cmd],
                input=text,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                return True, "cmd_ok"
            return False, (proc.stderr or proc.stdout or f"rc={proc.returncode}").strip()[:200]
        except Exception as exc:
            return False, str(exc)[:200]

    hermes = _hermes_home()
    sidecar = hermes / "runtime" / "photon-sidecar.json"
    if not sidecar.is_file():
        return False, f"sidecar_missing:{sidecar}"
    try:
        rec = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"sidecar_bad:{exc}"
    port = rec.get("port")
    token = rec.get("token")
    if not port or not token:
        return False, "sidecar_incomplete"
    space_id = _resolve_space_id(hermes)
    if not space_id:
        return False, "photon_space_id_missing"

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/send",
        data=json.dumps({"spaceId": space_id, "text": text}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Hermes-Sidecar-Token": str(token),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                return True, "http_ok"
            return False, f"http_{resp.status}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)[:200]


def enqueue_photon_ask(
    *,
    kind: str,
    key: str,
    item_id: str,
    name: str,
    summary: str,
    meta: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add (or return existing) pending binary ask with a short sequential code."""
    cur = _now_utc(now)
    doc, _ = _load_doc(now=cur)
    for row in doc["pending"]:
        if row.get("key") == key:
            return dict(row)
    seq = int(doc.get("next_seq") or 1)
    entry = {
        "code": str(seq),
        "kind": str(kind),
        "key": str(key),
        "item_id": str(item_id),
        "name": str(name),
        "summary": str(summary),
        "meta": dict(meta or {}),
        "created_at": _iso(cur),
        "expires_at": _iso(cur + timedelta(days=ASK_TTL_DAYS)),
    }
    doc["next_seq"] = seq + 1
    doc["pending"].append(entry)
    _save_doc(doc)
    return dict(entry)


def list_pending_photon_asks(*, now: datetime | None = None) -> list[dict[str, Any]]:
    cur = _now_utc(now)
    doc, pruned = _load_doc(now=cur)
    if pruned:
        _save_doc(doc)
    rows = list(doc.get("pending") or [])
    rows.sort(key=lambda r: int(r.get("code") or 0) if str(r.get("code") or "").isdigit() else 0)
    return rows


def _mark_web_ask_answered(item_id: str, txn_id: str | None, answer: str, now: datetime) -> None:
    """Keep buy_queue_asks.json in sync if a web ask row already exists for this charge."""
    asks_path = state_dir() / "buy_queue_asks.json"
    if not asks_path.is_file():
        return
    try:
        data = json.loads(asks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    asks = data.get("asks")
    if not isinstance(asks, dict):
        return
    changed = False
    for row in asks.values():
        if not isinstance(row, dict):
            continue
        if row.get("id") == item_id and (not txn_id or row.get("txn_id") == txn_id):
            row["answer"] = answer
            row["answered_at"] = _iso(now)
            changed = True
    if changed:
        tmp = asks_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(asks_path)


def resolve_photon_ask(
    answer: str,
    code: str | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve a pending binary ask ('yes'/'no') with optional numeric code."""
    cur = _now_utc(now)
    raw_ans = re.sub(r"[.!?\s]+$", "", (answer or "").strip().lower())
    if raw_ans in _YES:
        norm_ans = "yes"
    elif raw_ans in _NO:
        norm_ans = "no"
    else:
        return {"ok": False, "error": "invalid_answer", "message": "Reply y or n."}

    doc, pruned = _load_doc(now=cur)
    pending = list(doc.get("pending") or [])
    if pruned:
        _save_doc(doc)
    if not pending:
        return {"ok": False, "error": "no_pending", "message": "No open asks."}

    clean_code = str(code).lstrip("#").strip() if code is not None and str(code).strip() else None
    if clean_code is not None:
        target = next((r for r in pending if str(r.get("code")) == clean_code), None)
        if target is None:
            return {
                "ok": False,
                "error": "not_found",
                "message": f"No open ask #{clean_code}.",
            }
    elif len(pending) > 1:
        lines = [f"{len(pending)} asks open — reply e.g. y {pending[0]['code']} or n {pending[0]['code']}:"]
        for r in pending:
            lines.append(f"{r['code']}. {r.get('summary') or r.get('name')}")
        return {"ok": False, "error": "ambiguous", "message": "\n".join(lines)}
    else:
        target = pending[0]

    kind = str(target.get("kind") or "")
    item_id = str(target.get("item_id") or "")
    name = str(target.get("name") or item_id)
    meta = dict(target.get("meta") or {})

    if kind == "buy_queue_ask":
        from .buy_queue import drop_buy_queue_row
        from .buy_queue_sync import reconcile_buy_queue
        from .config import load_config

        txn_id = str(meta.get("txn_id") or "") or None
        if norm_ans == "yes":
            dropped = drop_buy_queue_row(load_config(), item_id, reason="photon-yes")
            reconcile_buy_queue(load_config())
            _mark_web_ask_answered(item_id, txn_id, "yes", cur)
            msg = (
                f'Cleared "{name}" from buy list.'
                if dropped
                else f'"{name}" was already off the buy list.'
            )
        else:
            _mark_web_ask_answered(item_id, txn_id, "no", cur)
            msg = f'Left "{name}" on buy list.'
    elif kind == "interval_candidate":
        from .recurring import dismiss_interval_candidate, enroll_interval_candidate

        if norm_ans == "yes":
            res = enroll_interval_candidate(item_id)
            if not res.get("ok"):
                err = str(res.get("error") or "enroll failed")
                return {"ok": False, "error": "enroll_failed", "message": f"Could not add {name}: {err}"}
            amt = int(meta.get("amount_cents") or 0)
            interval = int(meta.get("interval_months") or 0)
            detail = f" (${amt / 100:.2f} every {interval}m)" if amt and interval else ""
            msg = f"Added {name}{detail} to bills."
        else:
            res = dismiss_interval_candidate(item_id)
            if not res.get("ok"):
                err = str(res.get("error") or "dismiss failed")
                return {"ok": False, "error": "dismiss_failed", "message": f"Could not dismiss {name}: {err}"}
            msg = f"Dismissed {name}."
    else:
        return {"ok": False, "error": "unknown_kind", "message": f"Unknown ask kind: {kind}"}

    doc["pending"] = [r for r in pending if str(r.get("code")) != str(target.get("code"))]
    resolved = list(doc.get("resolved") or [])
    resolved.append(
        {
            **target,
            "answer": norm_ans,
            "resolved_at": _iso(cur),
            "result_message": msg,
        }
    )
    doc["resolved"] = resolved[-40:]
    _save_doc(doc)
    return {
        "ok": True,
        "answer": norm_ans,
        "code": str(target.get("code")),
        "kind": kind,
        "item_id": item_id,
        "message": msg,
    }
