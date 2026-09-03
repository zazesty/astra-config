#!/usr/bin/env python3
"""Hermes-Finance CLI — fixture watch, evaluate, dry-run notify."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import load_config, save_config, state_dir
from .rules import (
    budget_alerts,
    detect_anomalies,
    eom_leftover_event,
    evaluate_budget,
    evaluate_budget_both,
    make_digest,
    pending_spend_cents,
    pending_spend_count,
    prior_month_end,
    canned_cash_bills_cents,
)
from .store import (
    archive_digest,
    digest_sent_today,
    load_balances,
    load_fixture,
    load_last_run,
    load_txns,
    mark_digest_sent,
    record_period_series,
    save_last_run,
    upsert_txns,
)
from .notify import send_alert, send_alerts


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures" / "sample_txns.json"


def cmd_init(_args: argparse.Namespace) -> int:
    cfg = load_config()
    save_config(cfg)
    print(f"state: {state_dir()}")
    print(f"config: hardcap_cents={cfg['hardcap_cents']} mode={cfg.get('mode')} notify_enabled={cfg.get('notify_enabled')}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = load_config()
    txns = load_txns()
    tz = ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")
    as_of = datetime.now(tz).date()
    both = evaluate_budget_both(txns, cfg, as_of=as_of)
    # snapshot = calendar (notify SSOT); rolling_30d parallel for comparison
    print(json.dumps({
        "version": __version__,
        "config": {
            "hardcap_cents": cfg["hardcap_cents"],
            "notify_enabled": cfg.get("notify_enabled"),
            "mode": cfg.get("mode"),
            "notify_period": "calendar",
            "pace_v2_bills": True,
        },
        "txn_count": len(txns),
        "snapshot": both["calendar"].to_dict(),
        "snapshot_rolling_30d": both["rolling_30d"].to_dict(),
        "periods": {
            "calendar": both["calendar"].to_dict(),
            "rolling_30d": both["rolling_30d"].to_dict(),
        },
        "last_run": load_last_run(),
    }, indent=2))
    return 0


def cmd_budget_status(args: argparse.Namespace) -> int:
    """Human one-liner: calendar + rolling (Hermes / chat-friendly)."""
    from .balances import cash_on_hand_cents
    from .templates import budget_status_text

    cfg = load_config()
    txns = load_txns()
    tz = ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")
    as_of = date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else datetime.now(tz).date()
    both = evaluate_budget_both(txns, cfg, as_of=as_of)
    cal = both["calendar"]
    cash = cash_on_hand_cents(load_balances())
    bills_cents = canned_cash_bills_cents(
        cfg.get("bills"),
        txns,
        as_of,
        hardcap_cents=cal.hardcap_cents,
        days_in_period=cal.days_in_period,
        cash_cents=cash,
        exclude_pending=bool(cfg.get("exclude_pending", True)),
        fuzzy=bool(cfg.get("bill_fuzzy_match", True)),
        fuzzy_amount_tol_cents=int(cfg.get("bill_fuzzy_amount_tol_cents", 100)),
        fuzzy_day_slop=int(cfg.get("bill_fuzzy_day_slop", 2)),
        payment_grace_days=int(cfg.get("bill_payment_grace_days", 40)),
    )
    print(
        budget_status_text(
            both["calendar"],
            both["rolling_30d"],
            cash_cents=cash,
            upcoming_bills_cents=bills_cents,
        )
    )
    return 0


def cmd_load_fixture(args: argparse.Namespace) -> int:
    path = Path(args.path or DEFAULT_FIXTURE)
    if not path.exists():
        print(f"missing fixture: {path}", file=sys.stderr)
        return 2
    incoming = load_fixture(path)
    all_tx, new = upsert_txns(incoming)
    print(f"loaded {len(incoming)} from {path}; new={len(new)}; total={len(all_tx)}")
    return 0


def cmd_auto_review(args: argparse.Namespace) -> int:
    from .auto_review import run_and_persist

    summary = run_and_persist(auto_threshold=float(args.threshold))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_recurring(args: argparse.Namespace) -> int:
    from .recurring import run_and_persist

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    summary = run_and_persist(as_of=as_of)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_plaid_link(args: argparse.Namespace) -> int:
    """Start Link server; print URL; wait until linked or timeout."""
    import secrets
    import time

    from .plaid_link_server import run_link_server
    from .plaid_sync import list_items, load_access_token

    port = int(args.port)
    mount = args.mount or ("/hermes-link-" + secrets.token_hex(8))
    redirect = args.redirect_uri
    access = None
    heading = None
    blurb = None
    button = None
    update_id = getattr(args, "update_item", None)
    if update_id:
        match = next((i for i in list_items() if i.get("item_id") == update_id), None)
        if not match:
            print(json.dumps({"error": f"unknown_item:{update_id}"}))
            return 2
        access = load_access_token(match)
        inst = match.get("institution") or "item"
        heading = "NorCal re-login" if "nor" in inst.lower() else "Re-login"
        blurb = (
            "Update-mode Link — same Item, no duplicate. "
            "Log in to <strong>1st Northern California</strong> when prompted. "
            "Tokens stay on this box only."
        )
        button = "Re-login"
    info = run_link_server(
        port=port,
        mount=mount,
        redirect_uri=redirect,
        access_token=access,
        page_heading=heading,
        page_blurb=blurb,
        page_button=button,
    )
    # optional: expose via Tailscale Funnel (public HTTPS) + serve path
    public = None
    if args.funnel:
        import subprocess

        # Public Funnel path → local Link server (not tailnet-only serve)
        for cmd in ("serve", "funnel"):
            subprocess.run(
                [
                    "tailscale",
                    cmd,
                    "--bg",
                    "--yes",
                    f"--set-path={info['mount']}",
                    f"http://127.0.0.1:{port}",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        st = subprocess.run(
            ["tailscale", "funnel", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        host = "zaz-astra.tail5d74e1.ts.net"
        for line in (st.stdout or "").splitlines():
            # e.g. "https://zaz-astra.tail5d74e1.ts.net (Funnel on)" or bare host
            if "ts.net" not in line:
                continue
            for tok in line.replace("(", " ").split():
                if "ts.net" in tok:
                    host = tok.replace("https://", "").replace("http://", "").strip().rstrip("/")
                    break
            if host and host != "#":
                break
        public = f"https://{host}{info['mount']}"
    local = f"http://127.0.0.1:{port}{info['mount']}"
    print(json.dumps({
        "local_url": local,
        "public_url": public,
        "mount": info["mount"],
        "port": port,
        "expires": info.get("link_token_expiration"),
        "hint": (
            "Open public_url (needs Funnel on). Update-mode: just log in. "
            "Fresh Link: pick your bank (PayPal or 1st Nor Cal). Leave running until success."
        ),
    }, indent=2))
    deadline = time.time() + int(args.timeout)
    httpd = info["server"]
    while time.time() < deadline:
        if getattr(httpd, "result", None):
            result = httpd.result
            print(json.dumps({"linked": True, **result}, indent=2))
            # Update-mode: never /transactions/sync (or refresh) right after Link.
            # NorCal flakes ITEM_LOGIN_REQUIRED if we pull the FI immediately.
            if update_id:
                try:
                    from .plaid_sync import mark_item_repaired

                    mark_item_repaired(result.get("item_id") or update_id)
                except Exception as e:
                    print(json.dumps({"repaired_stamp": False, "error": str(e)[:200]}))
                print(json.dumps({
                    "synced_after_link": False,
                    "reason": "update_mode_no_sync",
                }, indent=2))
            else:
                try:
                    from .plaid_webhook import process_update

                    sync_out = process_update(
                        item_id=result.get("item_id"), source="link"
                    )
                    print(json.dumps({"synced_after_link": True, "new_txns": sync_out.get("new_txns")}, indent=2))
                except Exception as e:
                    print(json.dumps({"synced_after_link": False, "error": str(e)[:300]}))
            httpd.shutdown()
            return 0
        time.sleep(0.5)
    print(json.dumps({"linked": False, "error": "timeout waiting for Link"}, indent=2))
    httpd.shutdown()
    return 1


def cmd_plaid_sync(args: argparse.Namespace) -> int:
    from .plaid_sync import sync_all_items, sync_item

    force = bool(getattr(args, "force", False))
    item_id = getattr(args, "item_id", None)
    if item_id:
        # --force only applies to a single Item (bypass that Item's quarantine)
        summary = sync_item(item_id, force=force)
    else:
        # Never bulk-ingest quarantined Items via --force alone (scale footgun).
        # Intentional bulk: --include-quarantine (still dangerous if scale bad).
        include_q = bool(getattr(args, "include_quarantine", False))
        if force and not include_q:
            summary = sync_all_items(include_quarantine=False)
            summary["note"] = (
                "--force without --item-id no longer includes quarantined Items; "
                "pass --item-id X --force, or --include-quarantine (dangerous)"
            )
        else:
            summary = sync_all_items(include_quarantine=include_q)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_plaid_preview(args: argparse.Namespace) -> int:
    """Quarantine sync: no cursor/txn writes; scale-check vs import baseline."""
    from .plaid_sync import preview_item

    summary = preview_item(item_id=args.item_id)
    print(json.dumps(summary, indent=2, default=str))
    # non-zero if any item not promote_ok (and we have items)
    items = summary.get("items") or []
    if not items and summary.get("error"):
        return 2
    if items and not all(i.get("promote_ok") for i in items):
        return 1
    return 0


def cmd_plaid_promote(args: argparse.Namespace) -> int:
    """Clear quarantine (and optional amount_unit) then force-sync one Item."""
    from .plaid_sync import set_item_flags, sync_item

    unit = getattr(args, "amount_unit", None)
    flags = set_item_flags(
        args.item_id,
        quarantine=False,
        amount_unit=unit,
    )
    if not flags:
        print(json.dumps({"error": f"unknown_item:{args.item_id}"}))
        return 2
    if unit:
        set_item_flags(args.item_id, amount_unit=unit)
    summary = sync_item(args.item_id, force=True)
    print(json.dumps({"promoted": flags, "sync": summary}, indent=2, default=str))
    return 0


def cmd_plaid_quarantine(args: argparse.Namespace) -> int:
    from .plaid_sync import set_item_flags

    reason = args.reason or "manual quarantine"
    flags = set_item_flags(
        args.item_id,
        quarantine=True,
        quarantine_reason=reason,
        amount_unit=getattr(args, "amount_unit", None),
    )
    if not flags:
        print(json.dumps({"error": f"unknown_item:{args.item_id}"}))
        return 2
    print(json.dumps({"quarantined": flags}, indent=2, default=str))
    return 0


def cmd_plaid_status(_args: argparse.Namespace) -> int:
    idx = state_dir() / "tokens" / "items.json"
    if not idx.is_file():
        print(json.dumps({"items": [], "note": "no linked items"}))
        return 0
    items = json.loads(idx.read_text())
    # never print access_token
    safe = []
    for i in items:
        safe.append(
            {
                "item_id": i.get("item_id"),
                "institution": i.get("institution"),
                "quarantine": bool(i.get("quarantine")),
                "quarantine_reason": i.get("quarantine_reason"),
                "amount_unit": i.get("amount_unit") or "dollars",
            }
        )
    print(json.dumps({"items": safe}, indent=2))
    return 0


def cmd_plaid_webhook_serve(args: argparse.Namespace) -> int:
    from .plaid_webhook import serve_forever

    serve_forever(port=args.port, funnel=not args.no_funnel)
    return 0


def cmd_plaid_repair_serve(args: argparse.Namespace) -> int:
    from .plaid_link_server import serve_repair_forever

    serve_repair_forever(port=args.port)
    return 0


def cmd_plaid_webhook_register(_args: argparse.Namespace) -> int:
    from .plaid_webhook import public_webhook_url, register_webhooks_on_items

    summary = register_webhooks_on_items()
    print(json.dumps(summary, indent=2))
    # never print secret alone; URL path is credential — user has box access
    ok = all(i.get("ok") for i in summary.get("items") or []) or not summary.get("items")
    return 0 if ok else 1


def cmd_plaid_webhook_process(args: argparse.Namespace) -> int:
    """Manual near-instant cycle (same as webhook body)."""
    from .plaid_webhook import process_update

    out = process_update(item_id=args.item_id, source="cli")
    print(json.dumps(out, indent=2, default=str))
    return 0


def _dedupe_after_import() -> int:
    """Statement/XLSX NorCal wins over overlapping Plaid (date+amount)."""
    from .dedupe import persist_statement_ssot

    return persist_statement_ssot()


def cmd_import_statement_pdf(args: argparse.Namespace) -> int:
    from .import_statement_pdf import import_pdf
    from .store import upsert_txns

    path = Path(args.path)
    if not path.is_file():
        print(json.dumps({"error": f"missing {path}"}))
        return 2
    incoming = import_pdf(path, institution=args.institution)
    all_tx, new = upsert_txns(incoming)
    deduped = _dedupe_after_import()
    spendish = sum(
        1
        for t in incoming
        if t.amount_cents > 0 and not t.transfer and not t.pending
    )
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "parsed": len(incoming),
                "new": len(new),
                "total": len(all_tx),
                "spend_candidates": spendish,
                "transfers": sum(1 for t in incoming if t.transfer),
                "import_plaid_deduped": deduped,
            },
            indent=2,
        )
    )
    return 0


def cmd_import_xlsx(args: argparse.Namespace) -> int:
    from .import_xlsx import xlsx_to_transactions

    path = Path(args.path)
    if not path.exists():
        print(f"missing xlsx: {path}", file=sys.stderr)
        return 2
    incoming = xlsx_to_transactions(path, sheet=args.sheet)
    if args.replace:
        from .store import save_txns

        save_txns(incoming)
        deduped = _dedupe_after_import()
        print(
            f"replaced store with {len(incoming)} from {path} sheet={args.sheet}; "
            f"import_plaid_deduped={deduped}"
        )
        return 0
    all_tx, new = upsert_txns(incoming)
    deduped = _dedupe_after_import()
    print(
        f"imported {len(incoming)} from {path} sheet={args.sheet}; "
        f"new={len(new)}; total={len(all_tx)}; import_plaid_deduped={deduped}"
    )
    return 0


def _as_of(cfg: dict, s: str | None) -> date:
    tz = ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")
    if s:
        return date.fromisoformat(s)
    return datetime.now(tz).date()


def cmd_eom_leftover(args: argparse.Namespace) -> int:
    """Prior-month leftover = calendar STS. Pushover congrats if leftover > 0."""
    cfg = load_config()
    as_of = _as_of(cfg, args.as_of)
    force = bool(getattr(args, "force", False))
    # First-week grace so a Persistent timer miss still fires; mid-month CLI stays quiet.
    if as_of.day > 7 and not force:
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": "skipped_not_eom",
                    "as_of": as_of.isoformat(),
                }
            )
        )
        return 0

    close = prior_month_end(as_of)
    txns = load_txns()
    snap = evaluate_budget(txns, cfg, as_of=close, period_kind="calendar")
    pending_n = pending_spend_count(txns, snap.period_start, snap.period_end)
    pending_cents = pending_spend_cents(txns, snap.period_start, snap.period_end)
    ev = eom_leftover_event(
        snap, pending_spend_count=pending_n, pending_spend_cents=pending_cents
    )
    closed = snap.period_start.strftime("%Y-%m")
    if ev is None:
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": "skipped_no_leftover",
                    "closed_month": closed,
                    "safe_to_spend_cents": snap.safe_to_spend_cents,
                    "spend_to_date": snap.spend_to_date,
                    "remaining_cents": snap.remaining_cents,
                    "bills_reserved_cents": snap.bills_reserved_cents,
                }
            )
        )
        return 0

    live = bool(getattr(args, "live", False)) or bool(cfg.get("notify_enabled"))
    if bool(getattr(args, "dry_run", False)):
        live = False
    st = send_alert(ev, dry_run=not live, force=force)
    print(
        json.dumps(
            {
                "ok": True,
                "status": st,
                "closed_month": closed,
                "leftover_cents": ev.payload.get("leftover_cents"),
                "subject": ev.subject,
                "key": ev.key,
                "dry_run": not live,
            }
        )
    )
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    cfg = load_config()
    txns = load_txns()
    if args.fixture:
        txns = load_fixture(Path(args.fixture))
    as_of = _as_of(cfg, args.as_of)
    both = evaluate_budget_both(txns, cfg, as_of=as_of)
    snap = both["calendar"]  # notify SSOT
    anomalies = detect_anomalies(txns, cfg, as_of=as_of)
    balerts = budget_alerts(snap, cfg, prev_risk=None)
    print(json.dumps({
        "snapshot": snap.to_dict(),
        "snapshot_rolling_30d": both["rolling_30d"].to_dict(),
        "periods": {
            "calendar": both["calendar"].to_dict(),
            "rolling_30d": both["rolling_30d"].to_dict(),
        },
        "notify_period": "calendar",
        "budget_alerts": [a.to_dict() for a in balerts],
        "anomalies": [a.to_dict() for a in anomalies],
    }, indent=2))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Main job: sync/eval + rare interrupts (no daily digest by default)."""
    cfg = load_config()
    digest_enabled = bool(cfg.get("digest_enabled", False)) or bool(
        getattr(args, "force_digest", False)
    )
    live = bool(args.live) or bool(cfg.get("notify_enabled"))
    dry_run = not live
    sync_summary = None

    # Load data: fixture file override, else Plaid pull when Items exist, else state
    if args.fixture or (
        cfg.get("mode", "fixture") == "fixture"
        and not (state_dir() / "tokens" / "items.json").is_file()
    ):
        path = Path(args.fixture or DEFAULT_FIXTURE)
        if path.exists():
            incoming = load_fixture(path)
            all_tx, new_tx = upsert_txns(incoming)
        else:
            all_tx, new_tx = load_txns(), []
    else:
        try:
            from .plaid_sync import list_items, sync_all_items

            if list_items():
                sync_summary = sync_all_items()
        except Exception as e:
            sync_summary = {"error": str(e)[:300]}
        all_tx = load_txns()
        new_tx = []

    as_of = _as_of(cfg, args.as_of)
    both = evaluate_budget_both(all_tx, cfg, as_of=as_of)
    snap = both["calendar"]  # notify SSOT
    record_period_series(
        as_of, both["calendar"], both["rolling_30d"], source="watch"
    )
    prev_risk = load_last_run().get("last_risk")
    new_ids = list((sync_summary or {}).get("new_txn_ids") or [])
    by_id = {t.id: t for t in all_tx}
    new_tx_objs = [by_id[i] for i in new_ids if i in by_id]
    balerts = budget_alerts(
        snap,
        cfg,
        prev_risk=prev_risk,
        new_txn_ids=new_ids,
        new_txns=new_tx_objs,
    )
    anomalies = detect_anomalies(all_tx, cfg, as_of=as_of)
    coaching_anomalies = bool(cfg.get("coaching_anomalies", True))
    flags = list(balerts)
    if coaching_anomalies:
        flags.extend(anomalies)

    results = send_alerts(flags, dry_run=dry_run)

    # Daily digest OFF by default (owner: irregular coaching only)
    digest_status = "disabled"
    if digest_enabled:
        if not digest_sent_today(as_of) or bool(getattr(args, "force_digest", False)):
            dig = make_digest(snap, new_tx or all_tx[-12:], balerts + anomalies)
            archive_digest(as_of, dig.subject, dig.body)
            st = send_alert(dig, dry_run=dry_run, force=bool(args.force_digest))
            mark_digest_sent(as_of)
            digest_status = st
            results.append(
                {"kind": "digest", "key": dig.key, "status": st, "subject": dig.subject}
            )
        else:
            digest_status = "already_sent_today"

    save_last_run({
        "updated": datetime.now(ZoneInfo("UTC")).isoformat(),
        "as_of": as_of.isoformat(),
        "last_risk": snap.risk,
        "last_digest_date": load_last_run().get("last_digest_date"),
        "snapshot": snap.to_dict(),
        "snapshot_rolling_30d": both["rolling_30d"].to_dict(),
        "notify_results": results,
        "anomaly_count": len(anomalies),
        "dry_run": dry_run,
        "txn_count": len(all_tx),
        "mode": cfg.get("mode"),
    })

    out = {
        "ok": True,
        "risk": snap.risk,
        "spend": snap.spend_to_date,
        "hardcap": snap.hardcap_cents,
        "pace": round(snap.pace_ratio, 3),
        "alerts": len(flags),
        "anomalies_on_box": len(anomalies),
        "digest": digest_status,
        "dry_run": dry_run,
        "results": results,
    }
    if sync_summary is not None:
        out["plaid_sync"] = sync_summary
    print(json.dumps(out))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hermes-finance", description="Hermes-Finance watch CLI")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="Write default config into state dir")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("status", help="Print snapshot JSON")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser(
        "budget-status",
        help="Human one-liner: calendar + rolling 30d (Hermes-friendly)",
    )
    s.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today PT)")
    s.set_defaults(func=cmd_budget_status)

    s = sub.add_parser("load-fixture", help="Load sample txns into state store")
    s.add_argument("path", nargs="?", default=None)
    s.set_defaults(func=cmd_load_fixture)

    s = sub.add_parser("import-xlsx", help="Import consolidated 1H Excel (CU+PayPal export)")
    s.add_argument("path", help="Path to .xlsx")
    s.add_argument("--sheet", default="All_Transactions")
    s.add_argument(
        "--replace",
        action="store_true",
        help="Replace entire txn store (default: upsert by id)",
    )
    s.set_defaults(func=cmd_import_xlsx)

    s = sub.add_parser(
        "import-statement-pdf",
        help="Import 1st Nor Cal (pdftotext) monthly account statement PDF",
    )
    s.add_argument("path", help="Path to .pdf")
    s.add_argument("--institution", default="1st-norcal")
    s.set_defaults(func=cmd_import_statement_pdf)

    s = sub.add_parser(
        "auto-review",
        help="Apply automated category review priors (trained from rules + history)",
    )
    s.add_argument(
        "--threshold",
        type=float,
        default=0.9,
        help="Auto-accept confidence threshold (default 0.9)",
    )
    s.set_defaults(func=cmd_auto_review)

    s = sub.add_parser(
        "recurring",
        help="Detect recurring merchants (active = current + previous month)",
    )
    s.add_argument("--as-of", default=None, help="YYYY-MM-DD (default today PT-ish)")
    s.set_defaults(func=cmd_recurring)

    s = sub.add_parser("plaid-link", help="Start Plaid Link UI (PayPal / any bank)")
    s.add_argument("--port", default=8765, type=int)
    s.add_argument("--mount", default=None, help="URL path mount (default random secret)")
    s.add_argument("--redirect-uri", default=None, help="OAuth redirect_uri if required by Plaid dashboard")
    s.add_argument("--funnel", action="store_true", help="Expose mount via tailscale serve")
    s.add_argument("--timeout", default=900, type=int, help="Seconds to wait for Link success")
    s.add_argument(
        "--update-item",
        default=None,
        help="Item id to repair in update mode (ITEM_LOGIN_REQUIRED)",
    )
    s.set_defaults(func=cmd_plaid_link)

    s = sub.add_parser("plaid-sync", help="Sync transactions for all linked Items")
    s.add_argument("--item-id", default=None, help="sync only this Item")
    s.add_argument(
        "--force",
        action="store_true",
        help="with --item-id: sync even if that Item is quarantined (dangerous if scale bad)",
    )
    s.add_argument(
        "--include-quarantine",
        action="store_true",
        help="sync ALL Items including quarantined (dangerous; prefer --item-id --force)",
    )
    s.set_defaults(func=cmd_plaid_sync)

    s = sub.add_parser(
        "plaid-preview",
        help="Quarantine sync + scale check (no txn/cursor write) — use after NorCal Link",
    )
    s.add_argument("--item-id", default=None, help="preview only this Item (default: all)")
    s.set_defaults(func=cmd_plaid_preview)

    s = sub.add_parser(
        "plaid-promote",
        help="Clear quarantine and force-sync an Item (optionally set amount_unit=cents)",
    )
    s.add_argument("--item-id", required=True)
    s.add_argument(
        "--amount-unit",
        choices=("dollars", "cents"),
        default=None,
        help="dollars=normal Plaid (*100); cents=FI already emits cents-like amounts",
    )
    s.set_defaults(func=cmd_plaid_promote)

    s = sub.add_parser("plaid-quarantine", help="Mark an Item quarantined (skip auto-sync)")
    s.add_argument("--item-id", required=True)
    s.add_argument("--reason", default=None)
    s.add_argument("--amount-unit", choices=("dollars", "cents"), default=None)
    s.set_defaults(func=cmd_plaid_quarantine)

    s = sub.add_parser("plaid-status", help="List linked Items (no tokens)")
    s.set_defaults(func=cmd_plaid_status)

    s = sub.add_parser(
        "plaid-webhook-serve",
        help="Run Plaid webhook receiver (127.0.0.1; Funnel secret path)",
    )
    s.add_argument("--port", default=None, type=int, help="default 8766")
    s.add_argument("--no-funnel", action="store_true", help="skip tailscale funnel bind")
    s.set_defaults(func=cmd_plaid_webhook_serve)

    s = sub.add_parser(
        "plaid-webhook-register",
        help="Point all linked Items at the Funnel webhook URL",
    )
    s.set_defaults(func=cmd_plaid_webhook_register)

    s = sub.add_parser(
        "plaid-repair-serve",
        help="Long-lived update-mode Link server (24h email links)",
    )
    s.add_argument("--port", default=8765, type=int)
    s.set_defaults(func=cmd_plaid_repair_serve)

    s = sub.add_parser(
        "plaid-webhook-process",
        help="Run one near-instant cycle (sync+review+budget alerts)",
    )
    s.add_argument("--item-id", default=None, help="optional single Item id")
    s.set_defaults(func=cmd_plaid_webhook_process)

    s = sub.add_parser("evaluate", help="Evaluate rules (no notify)")
    s.add_argument("--fixture", default=None)
    s.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    s.set_defaults(func=cmd_evaluate)

    s = sub.add_parser(
        "eom-leftover",
        help="Prior-month leftover congrats (calendar STS). Timer: 1st 09:00 PT.",
    )
    s.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today PT)")
    s.add_argument(
        "--force",
        action="store_true",
        help="Send even mid-month / already notified",
    )
    s.add_argument("--live", action="store_true", help="Send even if notify_enabled is false")
    s.add_argument("--dry-run", action="store_true", help="Log only, do not push")
    s.set_defaults(func=cmd_eom_leftover)

    s = sub.add_parser("watch", help="Full watch cycle (default dry-run notify)")
    s.add_argument("--fixture", default=None)
    s.add_argument("--as-of", default=None)
    s.add_argument("--force-digest", action="store_true")
    s.add_argument("--live", action="store_true", help="Actually send push/email (also config notify_enabled)")
    s.set_defaults(func=cmd_watch)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
