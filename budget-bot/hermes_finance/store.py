"""Local JSON store for txns, baselines, notify dedup. No Plaid yet."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import state_dir
from .models import Transaction


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def txns_path() -> Path:
    return state_dir() / "txns.json"


def _txns_lock_path() -> Path:
    return state_dir() / "txns.lock"


@contextmanager
def txns_lock() -> Iterator[None]:
    """Exclusive lock for read-modify-write of txns.json (webhook + poll safe)."""
    path = _txns_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open for append so the file always exists; exclusive flock blocks others.
    with path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def load_txns() -> list[Transaction]:
    raw = _read_json(txns_path(), [])
    return [Transaction.from_dict(x) for x in raw]


def save_txns(txns: list[Transaction]) -> None:
    with txns_lock():
        _write_json(txns_path(), [t.to_dict() for t in txns])


def upsert_txns(incoming: list[Transaction]) -> tuple[list[Transaction], list[Transaction]]:
    """Merge by id. Returns (all, newly_added).

    Import↔Plaid dedupe is **not** applied here — only on PDF/XLSX import paths
    (see ``run.cmd_import_*``), so live Plaid syncs stay cheap and side-effect free.
    """
    with txns_lock():
        existing = {t.id: t for t in load_txns()}
        new: list[Transaction] = []
        for t in incoming:
            prev = existing.get(t.id)
            if prev is None:
                new.append(t)
            elif prev.excluded:
                # Keep a manual exclude across Plaid re-upsert (twin scrub).
                t.excluded = True
            existing[t.id] = t
        all_tx = sorted(existing.values(), key=lambda x: (x.date, x.id))
        _write_json(txns_path(), [t.to_dict() for t in all_tx])
        return all_tx, new


def remove_txns(ids: list[str] | set[str]) -> int:
    """Drop transactions by id. Returns count removed."""
    idset = {str(i) for i in ids if i}
    if not idset:
        return 0
    with txns_lock():
        existing = load_txns()
        kept = [t for t in existing if t.id not in idset]
        n = len(existing) - len(kept)
        if n:
            _write_json(txns_path(), [t.to_dict() for t in kept])
        return n


def load_fixture(path: Path) -> list[Transaction]:
    with path.open() as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "transactions" in raw:
        raw = raw["transactions"]
    return [Transaction.from_dict(x) for x in raw]


def notified_path() -> Path:
    return state_dir() / "notified_keys.json"


def _notified_lock_path() -> Path:
    return state_dir() / "notified_keys.lock"


@contextmanager
def notified_lock() -> Iterator[None]:
    """Exclusive lock around notify check-send-mark (webhook thread + poll)."""
    path = _notified_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def already_notified(key: str) -> bool:
    data = _read_json(notified_path(), {"keys": {}})
    return key in data.get("keys", {})


def mark_notified(key: str) -> None:
    data = _read_json(notified_path(), {"keys": {}})
    keys = data.setdefault("keys", {})
    keys[key] = datetime.now(timezone.utc).isoformat()
    # prune keys older than 60 days
    cutoff = datetime.now(timezone.utc).timestamp() - 60 * 86400
    pruned = {}
    for k, v in keys.items():
        try:
            ts = datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = cutoff + 1
        if ts >= cutoff:
            pruned[k] = v
    data["keys"] = pruned
    _write_json(notified_path(), data)


def last_run_path() -> Path:
    return state_dir() / "last_run.json"


def balances_path() -> Path:
    return state_dir() / "balances.json"


def load_balances() -> dict[str, Any]:
    return _read_json(balances_path(), {"items": []})


def save_balances(data: dict[str, Any]) -> None:
    _write_json(balances_path(), data)
    try:
        os.chmod(balances_path(), 0o600)
    except OSError:
        pass


def load_last_run() -> dict[str, Any]:
    return _read_json(last_run_path(), {})


def save_last_run(data: dict[str, Any]) -> None:
    _write_json(last_run_path(), data)


PERIOD_SERIES_KEEP_DAYS = 400
_PERIOD_SNAP_FIELDS = (
    "spend_to_date",
    "bills_reserved_cents",
    "committed_cents",
    "pace_ratio",
    "days_off_pace",
    "safe_to_spend_cents",
    "risk",
    "days_elapsed",
    "days_in_period",
    "hardcap_cents",
)


def period_series_path() -> Path:
    return state_dir() / "period_series.jsonl"


def compact_period_snap(snap: Any) -> dict[str, Any]:
    """Numeric pace fields only — no merchant lists."""
    src = snap.to_dict() if hasattr(snap, "to_dict") else dict(snap or {})
    out: dict[str, Any] = {}
    for k in _PERIOD_SNAP_FIELDS:
        v = src.get(k)
        if k == "risk":
            out[k] = str(v or "")
        elif k in ("pace_ratio", "days_off_pace"):
            out[k] = round(float(v or 0), 4)
        else:
            out[k] = int(v or 0)
    return out


def load_period_series() -> list[dict[str, Any]]:
    path = period_series_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def record_period_series(
    as_of: date,
    calendar: Any,
    rolling: Any,
    *,
    source: str = "watch",
) -> dict[str, Any]:
    """Upsert one row per as_of (PT day). Last write of the day wins.

    Webhook/poll can fire often; this file stays one line per day.
    """
    row = {
        "as_of": as_of.isoformat(),
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "calendar": compact_period_snap(calendar),
        "rolling_30d": compact_period_snap(rolling),
    }
    path = period_series_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".jsonl.lock")
    cutoff = as_of.toordinal() - PERIOD_SERIES_KEEP_DAYS
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            kept: list[dict[str, Any]] = []
            for old in load_period_series():
                key = str(old.get("as_of") or "")
                if key == row["as_of"]:
                    continue
                try:
                    if date.fromisoformat(key).toordinal() < cutoff:
                        continue
                except ValueError:
                    pass
                kept.append(old)
            kept.append(row)
            kept.sort(key=lambda r: str(r.get("as_of") or ""))
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w") as out:
                for r in kept:
                    out.write(json.dumps(r, separators=(",", ":")) + "\n")
                out.flush()
                os.fsync(out.fileno())
            tmp.replace(path)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return row


def digest_sent_today(as_of: date) -> bool:
    lr = load_last_run()
    return lr.get("last_digest_date") == as_of.isoformat()


def mark_digest_sent(as_of: date) -> None:
    lr = load_last_run()
    lr["last_digest_date"] = as_of.isoformat()
    lr["updated"] = datetime.now(timezone.utc).isoformat()
    save_last_run(lr)


def archive_digest(as_of: date, subject: str, body: str) -> Path:
    p = state_dir() / "digests" / f"{as_of.isoformat()}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"Subject: {subject}\n\n{body}\n")
    return p
