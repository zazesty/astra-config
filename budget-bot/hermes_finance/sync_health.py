"""Track Plaid sync breakage → Resend email ASAP, Pushover after N days."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load_config, state_dir
from .models import AlertEvent
from .notify import send_alert

UTC = ZoneInfo("UTC")

# Item errors that mean "you must re-Link" (or are about to).
ITEM_BREAK_CODES = frozenset(
    {
        "ITEM_LOGIN_REQUIRED",
        "INVALID_CREDENTIALS",
        "INVALID_MFA",
        "INVALID_OTP",
        "INSUFFICIENT_CREDENTIALS",
        "ITEM_LOCKED",
        "PASSWORD_RESET_REQUIRED",
        "USER_SETUP_REQUIRED",
        "ACCESS_NOT_GRANTED",
        "USER_PERMISSION_REVOKED",
    }
)

ITEM_BREAK_WEBHOOK_CODES = frozenset(
    {
        "ERROR",
        "PENDING_EXPIRATION",
        "USER_PERMISSION_REVOKED",
    }
)

ITEM_REPAIR_WEBHOOK_CODES = frozenset({"LOGIN_REPAIRED"})

_INST_LABELS = (
    (("northern-california", "norcal", "1st-nor", "1st nor"), "NORCAL"),
    (("paypal",), "PAYPAL"),
)


def _path() -> Path:
    return state_dir() / "sync_health.json"


def load_health() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"failures": {}}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {"failures": {}}
    if not isinstance(data, dict):
        return {"failures": {}}
    data.setdefault("failures", {})
    return data


def save_health(data: dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(p)


def _now() -> datetime:
    return datetime.now(UTC)


def _health_log_path() -> Path:
    return state_dir() / "sync_health.log"


def log_sync_break(item_id: str, institution: str, error: str) -> None:
    """Plaid dumps live here — never in the email."""
    line = (
        f"{_now().strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"item={item_id} inst={institution} error={error}\n"
    )
    try:
        p = _health_log_path()
        with p.open("a") as f:
            f.write(line)
        p.chmod(0o600)
    except OSError:
        pass


def friendly_institution(institution: str) -> str:
    raw = (institution or "").strip()
    low = raw.lower()
    for needles, label in _INST_LABELS:
        if any(n in low for n in needles):
            return label
    if not raw or raw in ("unknown", "*", "all"):
        return "PLAID"
    token = raw.replace("_", "-").split("-")[0]
    return token.upper()[:16]


_DISPLAY = {"NORCAL": "NorCal", "PAYPAL": "PayPal", "PLAID": "Plaid"}


def display_institution(institution: str) -> str:
    label = friendly_institution(institution)
    return _DISPLAY.get(label, label.title())


def break_kind(error: str) -> str:
    e = (error or "").upper()
    if "LOGIN" in e or "INVALID_CREDENTIAL" in e or "INVALID_MFA" in e:
        return "LOGIN"
    if "PENDING_EXPIRATION" in e or "EXPIR" in e:
        return "EXPIRING"
    if "REVOKED" in e or "ACCESS_NOT_GRANTED" in e:
        return "REVOKED"
    if "STALE" in e:
        return "STALE"
    return "BROKEN"


def break_phrase(error: str) -> str:
    return {
        "LOGIN": "needs a re-login",
        "EXPIRING": "login is expiring",
        "REVOKED": "access was revoked",
        "STALE": "hasn't synced",
        "BROKEN": "sync is broken",
    }[break_kind(error)]


def break_subject(institution: str, error: str) -> str:
    return f"Budget Bot: {display_institution(institution)} {break_phrase(error)}"


def break_email_body(
    institution: str, error: str, *, repair_url: str | None = None
) -> str:
    label = display_institution(institution)
    kind = break_kind(error)
    if kind == "LOGIN":
        action = f"{label} needs a re-login."
    elif kind == "EXPIRING":
        action = f"{label} login is about to expire."
    elif kind == "REVOKED":
        action = f"{label} access was revoked."
    elif kind == "STALE":
        action = f"{label} has not synced in 3+ days."
    else:
        action = f"{label} sync is broken."
    if repair_url:
        action += f" Re-login here (link expires in 24h):\n{repair_url}"
    elif kind in ("LOGIN", "EXPIRING", "REVOKED"):
        action += " Ask the box for an update-mode Link."
    elif kind == "STALE":
        action += " Re-link if it stays stuck."
    else:
        action += " Ask the box."
    return f"{action}\nDetail is on the box, not here.\n"


def break_notify_key(
    channel: str,
    item_key: str,
    first: datetime,
    *,
    wave: datetime | None = None,
) -> str:
    """One key per *episode*, not per UTC date.

    Date-only keys ate NorCal's same-day re-break after update-mode: a morning
    push consumed `...|YYYY-MM-DD`, then LOGIN_REPAIRED + flake reused that key
    and `skipped_dedup` was stored as `pushover_sent_at`.
    ``wave`` distinguishes a reminted 24h login URL on the same episode.
    """
    key = f"sync_break|{channel}|{item_key}|{first.isoformat()}"
    if wave is not None:
        key += f"|relink|{wave.isoformat()}"
    return key


def repair_link_expired(row: dict[str, Any], now: datetime | None = None) -> bool:
    """True when this episode's Funnel login URL is past expiry (or 24h since last push)."""
    now = now or _now()
    if not isinstance(row, dict):
        return False
    exp = parse_plaid_ts(row.get("repair_expires_at"))
    if exp is not None:
        return exp <= now
    sent = parse_plaid_ts(row.get("pushover_sent_at"))
    if sent is None:
        return False  # first push of the episode
    return (now - sent) >= timedelta(hours=24)


def break_push_body(
    institution: str,
    error: str,
    *,
    days: int = 0,
    repair_url: str | None = None,
) -> str:
    label = display_institution(institution)
    kind = break_kind(error)
    if kind == "LOGIN":
        line = f"{label} needs a re-login."
    elif kind == "EXPIRING":
        line = f"{label} login is about to expire."
    elif kind == "REVOKED":
        line = f"{label} access was revoked."
    elif kind == "STALE":
        line = f"{label} hasn't synced."
    else:
        line = f"{label} sync is broken."
    if days >= 1 and kind not in ("LOGIN", "EXPIRING", "REVOKED"):
        line = f"{label} still can't sync after {days}+ days."
    if repair_url:
        line += f" Re-login here (link expires in 24h):\n{repair_url}"
    else:
        line += " Re-link when you can."
    return line + "\n"


def parse_plaid_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def classify_item_health(
    *,
    item_id: str,
    institution: str,
    item_error: dict[str, Any] | None,
    last_successful_update: str | None,
    last_failed_update: str | None,
    now: datetime | None = None,
    stale_hours: int = 72,
    stale_grace_hours: int = 6,
) -> dict[str, str] | None:
    """Return a failure row if this Item needs a re-link / is stale. Pure."""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    err = item_error if isinstance(item_error, dict) else {}
    code = str(err.get("error_code") or "").strip()
    if code in ITEM_BREAK_CODES:
        msg = str(err.get("error_message") or code)[:300]
        return {
            "item_id": item_id,
            "institution": institution,
            "error": f"{code}: {msg}" if msg and msg != code else code,
        }

    ok_at = parse_plaid_ts(last_successful_update)
    fail_at = parse_plaid_ts(last_failed_update)
    stale_after = timedelta(hours=max(1, int(stale_hours)))
    grace = timedelta(hours=max(0, int(stale_grace_hours)))
    # Just re-linked: Plaid may still show an old last_success + a fresh fail.
    if fail_at is not None and (now - fail_at) < grace:
        return None
    # Deaf Item: no successful pull in stale_hours.
    if ok_at is None:
        if fail_at is not None and (now - fail_at) >= stale_after:
            return {
                "item_id": item_id,
                "institution": institution,
                "error": (
                    f"stale_transactions no_success last_fail={fail_at.isoformat()}"
                ),
            }
        return None
    if (now - ok_at) < stale_after:
        return None
    extra = ""
    if fail_at is not None and fail_at > ok_at:
        extra = f" last_fail={fail_at.isoformat()}"
    return {
        "item_id": item_id,
        "institution": institution,
        "error": (
            f"stale_transactions last_success={ok_at.isoformat()}{extra}"
        ),
    }


def probe_linked_items(*, stale_hours: int | None = None) -> list[dict[str, str]]:
    """Call /item/get for each linked Item. No /transactions/refresh."""
    from .plaid_client import item_get
    from .plaid_sync import list_items, load_access_token

    cfg = load_config()
    hours = int(
        stale_hours
        if stale_hours is not None
        else cfg.get("item_stale_hours", 72) or 72
    )
    now = _now()
    out: list[dict[str, str]] = []
    for item in list_items():
        iid = str(item.get("item_id") or "")
        inst = str(item.get("institution") or "unknown")
        if not iid:
            continue
        try:
            resp = item_get(load_access_token(item))
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:300]
            if any(c in msg for c in ITEM_BREAK_CODES) or "LOGIN" in msg.upper():
                out.append({"item_id": iid, "institution": inst, "error": msg})
            continue
        it = resp.get("item") or {}
        st = (resp.get("status") or {}).get("transactions") or {}
        row = classify_item_health(
            item_id=iid,
            institution=inst,
            item_error=it.get("error") if isinstance(it.get("error"), dict) else None,
            last_successful_update=st.get("last_successful_update"),
            last_failed_update=st.get("last_failed_update"),
            now=now,
            stale_hours=hours,
        )
        if row:
            out.append(row)
    return out


def attach_item_probes(sync_summary: dict[str, Any]) -> dict[str, Any]:
    """Merge /item/get breaks into a sync summary so poll reconcile sees them."""
    probes = probe_linked_items()
    if not probes:
        return sync_summary
    failed = list(sync_summary.get("failed_items") or [])
    seen = {str(f.get("item_id") or "") for f in failed}
    for p in probes:
        if p["item_id"] not in seen:
            failed.append(p)
    sync_summary["failed_items"] = failed
    return sync_summary


def handle_item_webhook(
    payload: dict[str, Any],
    *,
    dry_run: bool | None = None,
) -> list[dict[str, Any]]:
    """Treat ITEM ERROR / login-required as a sync break. No txn sync.

    Unknown (culled) item_ids are ignored so ghost Items don't email.
    LOGIN_REPAIRED clears that Item only (merge_only). The webhook layer
    then syncs **live** Items; a repair on a culled Item does not fix the live one.
    """
    from .plaid_sync import list_items

    code = str(payload.get("webhook_code") or "").upper()
    item_id = str(payload.get("item_id") or "")
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    err_code = str(err.get("error_code") or "").strip()
    match = next((i for i in list_items() if i.get("item_id") == item_id), None)
    if not match or not item_id:
        return []

    inst = str(match.get("institution") or "unknown")
    cfg = load_config()
    if dry_run is None:
        dry_run = not (
            bool(cfg.get("notify_enabled"))
            or __import__("os").environ.get("HERMES_LIVE", "0") == "1"
        )

    if code in ITEM_REPAIR_WEBHOOK_CODES:
        return process_sync_health(
            {"items": [], "failed_items": []},
            dry_run=dry_run,
            merge_only=[item_id],
        )

    is_break = code in ITEM_BREAK_WEBHOOK_CODES or err_code in ITEM_BREAK_CODES
    if not is_break:
        return []

    # Stale ITEM ERROR after a successful update-mode Link — confirm with /item/get.
    # Also skip during repair grace (NorCal flakes if we pull the FI too soon).
    try:
        from .plaid_sync import item_repair_grace_active, load_access_token

        if item_repair_grace_active(item_id):
            log_sync_break(item_id, inst, f"ignored_webhook_during_repair_grace {err_code or code}")
            return []
        from .plaid_client import item_get

        resp = item_get(load_access_token(match))
        live_err = (resp.get("item") or {}).get("error")
        live_code = (
            str(live_err.get("error_code") or "")
            if isinstance(live_err, dict)
            else ""
        )
        if live_code not in ITEM_BREAK_CODES:
            log_sync_break(
                item_id, inst, f"ignored_stale_webhook {err_code or code} item_get_ok"
            )
            return []
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if not any(c in msg for c in ITEM_BREAK_CODES) and "LOGIN" not in msg.upper():
            log_sync_break(item_id, inst, f"item_get_probe_failed {msg[:200]}")
            # Fall through — webhook still looks like a break.

    label = err_code or code
    msg = str(err.get("error_message") or label)[:300]
    err_s = f"{label}: {msg}" if msg and msg != label else label
    summary = {
        "failed_items": [
            {"item_id": item_id, "institution": inst, "error": err_s}
        ],
    }
    return process_sync_health(summary, dry_run=dry_run, merge_only=[item_id])


def collect_failures(sync_summary: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalize failures from sync summary (errors + LOGIN_REQUIRED quarantine)."""
    out: list[dict[str, str]] = []
    if not sync_summary:
        return out
    for it in sync_summary.get("items") or []:
        err = it.get("error") or it.get("error_code")
        if err:
            out.append(
                {
                    "item_id": str(it.get("item_id") or ""),
                    "institution": str(it.get("institution") or "unknown"),
                    "error": str(err)[:300],
                }
            )
    for it in sync_summary.get("failed_items") or []:
        out.append(
            {
                "item_id": str(it.get("item_id") or ""),
                "institution": str(it.get("institution") or "unknown"),
                "error": str(it.get("error") or "sync_failed")[:300],
            }
        )
    for it in sync_summary.get("skipped_quarantine") or []:
        reason = str(it.get("reason") or "")
        if "LOGIN" in reason.upper() or "ITEM_LOGIN" in reason.upper() or reason:
            # Quarantined for login/required action still counts as broken sync
            if "LOGIN" in reason.upper() or "login" in reason.lower() or "ITEM_" in reason:
                out.append(
                    {
                        "item_id": str(it.get("item_id") or ""),
                        "institution": str(it.get("institution") or "unknown"),
                        "error": reason[:300] or "quarantined",
                    }
                )
    # top-level error
    if sync_summary.get("error") and not out:
        out.append(
            {
                "item_id": "*",
                "institution": "all",
                "error": str(sync_summary.get("error"))[:300],
            }
        )
    return out


def process_sync_health(
    sync_summary: dict[str, Any] | None,
    *,
    dry_run: bool = True,
    merge_only: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Update health file; Pushover on new break (email opt-in, default off).

    Full reconcile (poll): any Item not in this summary's failures is recovered.
    ``merge_only`` (ITEM webhook): only upsert/clear those ids; leave others.
    """
    cfg = load_config()
    email_on = bool(cfg.get("sync_break_email", False))
    try:
        push_after = int(cfg.get("sync_break_pushover_after_days", 0))
    except (TypeError, ValueError):
        push_after = 0
    # push_after < 0 disables push; 0 = immediate. Email is opt-in.
    if not email_on and push_after < 0:
        return []

    failures = collect_failures(sync_summary)
    health = load_health()
    prev: dict[str, Any] = dict(health.get("failures") or {})
    now = _now()
    results: list[dict[str, Any]] = []
    active_ids = {f["item_id"] or f["institution"] for f in failures}
    merge_set = set(merge_only) if merge_only is not None else None

    # Clear recovered
    for key in list(prev.keys()):
        if merge_set is not None and key not in merge_set:
            continue
        if key not in active_ids:
            prev.pop(key, None)

    for f in failures:
        key = f["item_id"] or f["institution"]
        row = prev.get(key) or {}
        if not row.get("first_seen"):
            row = {
                "institution": f["institution"],
                "error": f["error"],
                "first_seen": now.isoformat(),
                "email_sent_at": None,
                "pushover_sent_at": None,
            }
            log_sync_break(key, f["institution"], f["error"])
        else:
            if f["error"] != row.get("error"):
                log_sync_break(key, f["institution"], f["error"])
            row["error"] = f["error"]
            row["institution"] = f["institution"]

        first = datetime.fromisoformat(row["first_seen"].replace("Z", "+00:00"))
        if first.tzinfo is None:
            first = first.replace(tzinfo=UTC)
        age = now - first

        # Skip 6h after update-mode so NorCal flaps don't re-page.
        skip_notify = False
        try:
            from .plaid_sync import item_repair_grace_active

            skip_notify = bool(key) and item_repair_grace_active(key)
        except Exception:
            skip_notify = False

        remint = (not skip_notify) and repair_link_expired(row, now)
        want_push = (
            push_after >= 0
            and age >= timedelta(days=push_after)
            and not skip_notify
            and (not row.get("pushover_sent_at") or remint)
        )
        repair_url = None
        repair_expires = None
        if not skip_notify and (
            (email_on and not row.get("email_sent_at")) or want_push
        ):
            try:
                from .plaid_link_server import mint_repair_link

                minted = mint_repair_link(key) or {}
                repair_url = minted.get("public_url")
                repair_expires = minted.get("expires_at")
            except Exception as e:  # noqa: BLE001
                log_sync_break(
                    key, row["institution"], f"mint_repair_link_failed {e}"[:200]
                )

        if email_on and not row.get("email_sent_at") and not skip_notify:
            ev = AlertEvent(
                kind="sync_break",
                key=break_notify_key("email", key, first),
                subject=break_subject(row["institution"], row["error"]),
                payload={"push_priority": 0},
                body=break_email_body(
                    row["institution"], row["error"], repair_url=repair_url
                ),
            )
            st = send_alert(ev, dry_run=dry_run, channel="email", force=False)
            results.append(
                {"kind": "sync_break_email", "key": ev.key, "status": st, "item": key}
            )
            if st in ("sent", "sent_push", "dry_run", "skipped_dedup") or (
                isinstance(st, str) and st.startswith("sent")
            ):
                row["email_sent_at"] = now.isoformat()
            elif st == "skipped_dedup":
                row["email_sent_at"] = row.get("email_sent_at") or now.isoformat()

        if want_push:
            wave = now if remint else None
            ev = AlertEvent(
                kind="sync_break",
                key=break_notify_key("push", key, first, wave=wave),
                subject=break_subject(row["institution"], row["error"]),
                body=break_push_body(
                    row["institution"],
                    row["error"],
                    days=push_after,
                    repair_url=repair_url,
                ),
                payload={"push_priority": 1},
            )
            st = send_alert(ev, dry_run=dry_run, channel="push", force=False)
            results.append(
                {"kind": "sync_break_push", "key": ev.key, "status": st, "item": key}
            )
            if st in ("sent_push", "dry_run", "skipped_dedup") or (
                isinstance(st, str) and st.startswith("sent")
            ):
                row["pushover_sent_at"] = now.isoformat()
                if repair_expires:
                    row["repair_expires_at"] = repair_expires
            elif st == "skipped_dedup":
                row["pushover_sent_at"] = row.get("pushover_sent_at") or now.isoformat()

        prev[key] = row

    health["failures"] = prev
    health["updated"] = now.isoformat()
    save_health(health)
    return results
