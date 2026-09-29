"""NorCal soft-close.

Plaid is unlinked immediately so sync and re-login nags stop. The ledger
stays. A NorCal statement with a row on or after 2026-09-25 deprecates the
institution and sets the close to the end of that statement's month.
Anything dated after the close is removed. A later statement import extends
the close through the end of its month, so a forgotten month can still be
added by hand. Plaid does not fill those dates.
"""

from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone
from typing import Any

from .balances import is_norcal_institution
from .config import state_dir
from .models import Transaction

# Month being closed. A statement counts once it contains a NorCal row in
# the last week of this month. Forward means the next calendar day.
CLOSE_THROUGH = "2026-09-30"
ARM_ON_OR_AFTER = "2026-09-25"
_STATE_NAME = "norcal-soft-close.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path():
    return state_dir() / _STATE_NAME


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(data: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(p)


def status() -> str:
    return str(load_state().get("status") or "")


def active() -> bool:
    """Unlinked or deprecated. Either way, do not sync or re-link NorCal."""
    return status() in ("unlinked", "deprecated")


def is_deprecated() -> bool:
    return status() == "deprecated"


def close_through() -> str | None:
    if not is_deprecated():
        return None
    return str(load_state().get("close_through") or CLOSE_THROUGH)


def is_forward(txn: Transaction) -> bool:
    cutoff = close_through()
    if not cutoff:
        return False
    if not is_norcal_institution(getattr(txn, "institution", None)):
        return False
    return str(getattr(txn, "date", "") or "") > cutoff


def without_forward(txns: list[Transaction]) -> tuple[list[Transaction], list[Transaction]]:
    keep: list[Transaction] = []
    drop: list[Transaction] = []
    for txn in txns:
        (drop if is_forward(txn) else keep).append(txn)
    return keep, drop


def _norcal_dates(txns: list[Transaction], institution: str | None) -> list[str]:
    forced = bool(institution and is_norcal_institution(institution))
    dates: list[str] = []
    for txn in txns:
        if not (forced or is_norcal_institution(txn.institution)):
            continue
        day = str(txn.date or "")[:10]
        if len(day) == 10:
            dates.append(day)
    return dates


def month_end(iso: str) -> str:
    year, month, _day = (int(part) for part in iso[:10].split("-"))
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last:02d}"


def closes_september(txns: list[Transaction], *, institution: str | None = None) -> bool:
    """True when this import reaches the last week of September 2026."""
    return any(ARM_ON_OR_AFTER <= day <= CLOSE_THROUGH for day in _norcal_dates(txns, institution))


def sweep_forward() -> int:
    """Drop stored NorCal rows dated after the close. No-op until deprecated."""
    if not is_deprecated():
        return 0
    from .store import load_txns, remove_txns

    drop_ids = [t.id for t in load_txns() if is_forward(t)]
    return remove_txns(drop_ids)


def _finish_board() -> None:
    """Close the September-upload todo once, on the real box only."""
    from pathlib import Path

    real = Path.home() / ".local/state/budget-bot"
    try:
        if state_dir().resolve() != real.resolve():
            return
    except OSError:
        return
    script = Path("/root/astra-config/scripts/standing-todos.sh")
    if not script.is_file():
        return
    import subprocess

    note = (
        "September statement imported. NorCal deprecated. "
        "A later statement import extends the close through that month. "
        "Plaid stays unlinked."
    )
    subprocess.run(
        [str(script), "note", "upload-september-norcal-statement", note],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(script), "done", "upload-september-norcal-statement"],
        check=False,
        capture_output=True,
        text=True,
    )


def arm_from_statement(txns: list[Transaction], *, source: str, institution: str | None = None) -> dict[str, Any]:
    """Deprecate on a September-or-later statement. A later statement extends the close."""
    dates = [day for day in _norcal_dates(txns, institution) if day >= ARM_ON_OR_AFTER]
    if not dates:
        if is_deprecated():
            removed = sweep_forward()
            return {
                "deprecated": True,
                "already": True,
                "extended": False,
                "removed_forward": removed,
                "close_through": close_through(),
            }
        return {"deprecated": False, "extended": False}
    data = load_state()
    current = str(data.get("close_through") or "")
    first = not is_deprecated()
    # The September file may contain a date or two into the next month.
    # That does not open the next month. A later import does.
    september = [day for day in dates if day <= CLOSE_THROUGH]
    if first and september:
        desired = CLOSE_THROUGH
    else:
        desired = month_end(max(dates))
    extended = bool(current and desired > current)
    if first or desired > current:
        data["status"] = "deprecated"
        data["close_through"] = desired
        if first:
            data["deprecated_at"] = _now()
            data["source"] = source
        else:
            data.setdefault("extensions", []).append(
                {"at": _now(), "source": source, "close_through": desired}
            )
        save_state(data)
    if first:
        _finish_board()
    removed = sweep_forward()
    return {
        "deprecated": True,
        "already": not first and not extended,
        "extended": extended,
        "removed_forward": removed,
        "close_through": close_through(),
    }


def _remote_gone(message: str) -> bool:
    text = message.upper()
    return "ITEM_NOT_FOUND" in text or "INVALID_ACCESS_TOKEN" in text or "INVALID_ACCESS" in text


def unlink_norcal() -> dict[str, Any]:
    """Remove the NorCal Item at Plaid and drop it locally. Ledger stays."""
    from .plaid_client import item_remove
    from .plaid_sync import load_access_token, save_items
    from .plaid_sync import _tokens
    from .sync_health import append_relogin_event

    items = _tokens()
    norcal = [i for i in items if is_norcal_institution(str(i.get("institution") or ""))]
    remote: list[dict[str, str]] = []
    for item in norcal:
        short = str(item.get("item_id") or "")[:8]
        token = ""
        try:
            token = load_access_token(item)
        except Exception:
            token = ""
        if not token:
            remote.append({"item_id": short, "remote": "no_token"})
        else:
            try:
                item_remove(token)
                remote.append({"item_id": short, "remote": "removed"})
            except RuntimeError as e:
                if not _remote_gone(str(e)):
                    return {"ok": False, "error": str(e)[:300], "item_id": short}
                remote.append({"item_id": short, "remote": "already_gone"})
        token_name = str(item.get("token_file") or "")
        if token_name:
            path = state_dir() / "tokens" / token_name
            if path.is_file():
                path.unlink()
        item_id = str(item.get("item_id") or "")
        if item_id:
            cursor = state_dir() / "tokens" / f"{item_id}.cursor"
            if cursor.is_file():
                cursor.unlink()
            try:
                append_relogin_event(
                    "unlinked",
                    item_id=item_id,
                    institution=str(item.get("institution") or ""),
                    extra={"via": "soft_close"},
                )
            except Exception:
                pass
    save_items([i for i in items if i not in norcal])
    data = load_state()
    if data.get("status") != "deprecated":
        data["status"] = "unlinked"
    data["unlinked_at"] = _now()
    data["unlinked_items"] = len(norcal)
    save_state(data)
    return {"ok": True, "removed_local": len(norcal), "remote": remote, "status": data["status"]}
