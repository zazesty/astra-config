"""Notify: Pushover for budget interrupts + rare anomaly coaching; dry-run log."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import state_dir
from .models import AlertEvent
from .store import already_notified, mark_notified, notified_lock

# Kinds that may push when live (digest never)
PUSH_KINDS = frozenset(
    {"hardcap_breach", "pace_warn", "anomaly", "eom_leftover", "sync_break"}
)
SMS_KINDS = PUSH_KINDS


def notify_email_script() -> Path:
    return Path(
        os.environ.get(
            "HERMES_NOTIFY_CMD",
            "/root/astra-config/scripts/notify-email.sh",
        )
    )


def notify_push_script() -> Path:
    return Path(
        os.environ.get(
            "HERMES_PUSH_CMD",
            "/root/astra-config/scripts/notify-pushover.sh",
        )
    )


def notify_sms_script() -> Path:
    """Legacy Twilio path (optional override only)."""
    return Path(
        os.environ.get(
            "HERMES_SMS_CMD",
            "/root/astra-config/scripts/notify-sms.sh",
        )
    )


def _push_body(event: AlertEvent) -> str:
    """Push body: non-empty lines, truncated (script also caps at 900)."""
    lines = [ln.strip() for ln in (event.body or "").strip().splitlines() if ln.strip()]
    text = "\n".join(lines) if lines else (event.subject or "")
    return text[:900]


def _mark_event(event: AlertEvent) -> None:
    mark_notified(event.key)
    extras = (event.payload or {}).get("also_mark_keys") or []
    for k in extras:
        if k and k != event.key:
            mark_notified(k)


def coalesce_push_events(events: list[AlertEvent]) -> AlertEvent:
    """One Pushover for a same-sync dump (pace + anomaly, N identical pace, …)."""
    pending = [e for e in events if e.kind in PUSH_KINDS]
    if not pending:
        raise ValueError("coalesce_push_events requires at least one push event")
    pending = sorted(pending, key=_priority_for, reverse=True)
    primary = pending[0]
    bodies: list[str] = []
    seen: set[str] = set()
    also: list[str] = []
    txn_ids: list[str] = []
    for ev in pending:
        also.append(ev.key)
        payload = ev.payload or {}
        also.extend(payload.get("also_mark_keys") or [])
        if payload.get("txn_id"):
            txn_ids.append(str(payload["txn_id"]))
        txn_ids.extend(str(x) for x in (payload.get("txn_ids") or []))
        text = "\n".join(
            ln.strip()
            for ln in (ev.body or "").splitlines()
            if ln.strip() and not ln.strip().startswith("New txn:")
        )
        if text and text not in seen:
            seen.add(text)
            bodies.append(text)
    joined = "\n\n".join(bodies)
    return AlertEvent(
        kind=primary.kind,
        subject=primary.subject,
        body=joined.strip() + "\n",
        key=primary.key,
        payload={
            **(primary.payload or {}),
            "also_mark_keys": list(dict.fromkeys(k for k in also if k)),
            "coalesced_n": len(pending),
            "txn_ids": list(dict.fromkeys(txn_ids)),
            "push_priority": _priority_for(primary),
        },
    )


def _priority_for(event: AlertEvent) -> int:
    """
    Pushover priority:
      0 = soft (respects quiet hours / Focus) — default coaching
      1 = high (can interrupt Focus/sleep) — firm over-budget
      2 = emergency — hardcap breach
    """
    payload = event.payload or {}
    if "push_priority" in payload:
        try:
            p = int(payload["push_priority"])
            if p in (-2, -1, 0, 1, 2):
                return p
        except (TypeError, ValueError):
            pass
    if event.kind == "hardcap_breach":
        return 2
    if event.kind in ("pace_warn", "sync_break"):
        return 1
    return 0


def send_alert(
    event: AlertEvent,
    *,
    force: bool = False,
    dry_run: bool = True,
    channel: str | None = None,
) -> str:
    """
    Returns status: skipped_dedup | dry_run | sent | sent_push | sent_sms |
    skipped_channel | error:...

    channel: auto | push | pushover | sms | email | none
      auto = Pushover for PUSH_KINDS; digest/other → skip
    """
    log_path = state_dir() / "notify.log"
    priority = _priority_for(event)
    line = (
        f"kind={event.kind} key={event.key} priority={priority} "
        f"subject={event.subject!r}"
    )
    ch = channel or os.environ.get("HERMES_NOTIFY_CHANNEL", "auto")

    with notified_lock():
        return _send_alert_locked(
            event,
            force=force,
            dry_run=dry_run,
            log_path=log_path,
            priority=priority,
            line=line,
            ch=ch,
        )


def _send_alert_locked(
    event: AlertEvent,
    *,
    force: bool,
    dry_run: bool,
    log_path: Path,
    priority: int,
    line: str,
    ch: str,
) -> str:
    if not force and already_notified(event.key):
        return "skipped_dedup"

    if dry_run:
        with log_path.open("a") as f:
            f.write(f"DRY_RUN ch={ch} {line}\n")
            f.write(event.body)
            f.write("\n---\n")
        _mark_event(event)
        return "dry_run"

    use_push = ch in ("push", "pushover") or (
        ch == "auto" and event.kind in PUSH_KINDS
    )
    use_sms = ch == "sms"
    use_email = ch == "email"

    if not use_push and not use_sms and not use_email:
        with log_path.open("a") as f:
            f.write(f"SKIP_CHANNEL ch={ch} {line}\n")
        mark_notified(event.key)
        return "skipped_channel"

    if use_push:
        script = notify_push_script()
        if not script.is_file():
            return f"error:push_script_missing:{script}"
        title = event.subject or f"Budget Bot: {event.kind}"
        try:
            proc = subprocess.run(
                [str(script), title, _push_body(event), str(priority)],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            with log_path.open("a") as f:
                f.write(f"PUSH rc={proc.returncode} {line}\n")
            if proc.returncode == 0:
                _mark_event(event)
                return "sent_push"
            return f"error:push_rc={proc.returncode}:{proc.stderr[-200:]}"
        except Exception as e:
            return f"error:push:{e}"

    if use_sms:
        script = notify_sms_script()
        if not script.is_file():
            return f"error:sms_script_missing:{script}"
        body = _push_body(event)
        try:
            proc = subprocess.run(
                [str(script), body],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            with log_path.open("a") as f:
                f.write(f"SMS rc={proc.returncode} {line}\n")
            if proc.returncode == 0:
                _mark_event(event)
                return "sent_sms"
            return f"error:sms_rc={proc.returncode}:{proc.stderr[-200:]}"
        except Exception as e:
            return f"error:sms:{e}"

    script = notify_email_script()
    if not script.is_file():
        return f"error:notify_script_missing:{script}"
    try:
        proc = subprocess.run(
            [str(script), event.subject],
            input=event.body,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        with log_path.open("a") as f:
            f.write(f"SENT rc={proc.returncode} {line}\n")
        if proc.returncode == 0:
            _mark_event(event)
            return "sent"
        return f"error:rc={proc.returncode}:{proc.stderr[-200:]}"
    except Exception as e:
        return f"error:{e}"


def send_alerts(
    events: list[AlertEvent],
    *,
    force: bool = False,
    dry_run: bool = True,
    channel: str | None = None,
) -> list[dict[str, str]]:
    """Send a cycle of alerts. Same-sync push kinds collapse to one Pushover."""
    results: list[dict[str, str]] = []
    pushable: list[AlertEvent] = []
    for ev in events:
        if ev.kind not in PUSH_KINDS:
            st = send_alert(ev, force=force, dry_run=dry_run, channel=channel)
            results.append({"kind": ev.kind, "key": ev.key, "status": st, "subject": ev.subject})
            continue
        if not force and already_notified(ev.key):
            results.append(
                {
                    "kind": ev.kind,
                    "key": ev.key,
                    "status": "skipped_dedup",
                    "subject": ev.subject,
                }
            )
            continue
        pushable.append(ev)

    if not pushable:
        return results

    to_send = pushable[0] if len(pushable) == 1 else coalesce_push_events(pushable)
    st = send_alert(to_send, force=force, dry_run=dry_run, channel=channel)
    coalesced = len(pushable) > 1 and st.startswith("sent")
    for ev in pushable:
        results.append(
            {
                "kind": ev.kind,
                "key": ev.key,
                "status": "sent_push_coalesced" if coalesced else st,
                "subject": ev.subject,
            }
        )
    return results
