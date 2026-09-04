"""Plaid webhook receiver + near-instant sync → auto-review → budget interrupts.

Listens on 127.0.0.1 only; Tailscale Funnel exposes a secret path.
Secret path is the credential (same model as MCP_PATH).
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .config import load_config, state_dir
from .notify import PUSH_KINDS, send_alert, send_alerts
from .plaid_client import item_webhook_update, load_plaid_env
from .plaid_sync import list_items, load_access_token, sync_all_items, sync_item
from .rules import budget_alerts, evaluate_budget_both
from .store import load_last_run, load_txns, record_period_series, save_last_run

DEFAULT_PORT = 8766
FUNNEL_HOST = "zaz-astra.tail5d74e1.ts.net"
ENV_FILE = Path(os.environ.get("HERMES_PLAID_ENV", "/etc/hermes-finance.env"))

# TRANSACTIONS codes that mean "pull sync now".
# SYNC_UPDATES_AVAILABLE is the /transactions/sync webhook. DEFAULT_UPDATE /
# INITIAL_UPDATE / HISTORICAL_UPDATE are the old /transactions/get pair —
# Plaid often fires DEFAULT_UPDATE + SYNC_UPDATES_AVAILABLE together, which
# raced two process_update threads and double-Pushover'd a first breach.
SYNC_CODES = frozenset(
    {
        "SYNC_UPDATES_AVAILABLE",
        "TRANSACTIONS_REMOVED",
    }
)


def webhook_log_path() -> Path:
    return state_dir() / "webhook.log"


def whlog(msg: str) -> None:
    line = f"{datetime.now(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}\n"
    try:
        with webhook_log_path().open("a") as f:
            f.write(line)
    except OSError:
        pass


def ensure_webhook_secret() -> str:
    """Return PLAID_WEBHOOK_SECRET; generate + append to env file if missing."""
    env = {}
    try:
        env = load_plaid_env()
    except FileNotFoundError:
        pass
    sec = (env.get("PLAID_WEBHOOK_SECRET") or os.environ.get("PLAID_WEBHOOK_SECRET") or "").strip()
    if sec:
        return sec
    sec = secrets.token_hex(16)
    # append presence-only style secret to hermes env
    try:
        with ENV_FILE.open("a") as f:
            f.write(f"\n# Budget Bot Plaid webhook path secret (Funnel path credential)\n")
            f.write(f"PLAID_WEBHOOK_SECRET={sec}\n")
        os.chmod(ENV_FILE, 0o600)
    except OSError as e:
        whlog(f"warn could not persist PLAID_WEBHOOK_SECRET: {e}")
    os.environ["PLAID_WEBHOOK_SECRET"] = sec
    return sec


def mount_path(secret: str | None = None) -> str:
    sec = secret or ensure_webhook_secret()
    return f"/plaid-wh-{sec}"


def public_webhook_url(secret: str | None = None) -> str:
    host = os.environ.get("HERMES_FUNNEL_HOST", FUNNEL_HOST)
    return f"https://{host}{mount_path(secret)}"


def webhook_port() -> int:
    return int(os.environ.get("PLAID_WEBHOOK_PORT", DEFAULT_PORT))


def ensure_funnel(mount: str, port: int) -> None:
    """Best-effort: map Funnel path → local webhook server."""
    import subprocess

    for cmd in ("serve", "funnel"):
        subprocess.run(
            [
                "tailscale",
                cmd,
                "--bg",
                "--yes",
                f"--set-path={mount}",
                f"http://127.0.0.1:{port}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )


def register_webhooks_on_items(url: str | None = None) -> dict[str, Any]:
    """POST /item/webhook/update for every linked Item."""
    url = url or public_webhook_url()
    out: list[dict[str, Any]] = []
    for item in list_items():
        try:
            access = load_access_token(item)
            resp = item_webhook_update(access, url)
            out.append(
                {
                    "item_id": item.get("item_id"),
                    "institution": item.get("institution"),
                    "ok": True,
                    "request_id": resp.get("request_id"),
                }
            )
            whlog(f"registered webhook item={item.get('item_id')} inst={item.get('institution')}")
        except Exception as e:
            out.append(
                {
                    "item_id": item.get("item_id"),
                    "institution": item.get("institution"),
                    "ok": False,
                    "error": str(e)[:300],
                }
            )
            whlog(f"register fail item={item.get('item_id')}: {e}")
    return {"webhook": url, "items": out}


@contextmanager
def update_lock() -> Iterator[None]:
    """One sync→notify cycle at a time (webhook threads + 15m poll)."""
    path = state_dir() / "update.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def process_update(*, item_id: str | None = None, source: str = "webhook") -> dict[str, Any]:
    """
    Near-instant path: sync → auto-review → budget pressure alerts.
    No daily digest (that's the timer/job).
    """
    with update_lock():
        return _process_update_unlocked(item_id=item_id, source=source)


def _process_update_unlocked(
    *, item_id: str | None = None, source: str = "webhook"
) -> dict[str, Any]:
    cfg = load_config()
    live = bool(cfg.get("notify_enabled")) or os.environ.get("HERMES_LIVE", "0") == "1"
    dry_run = not live

    if item_id:
        sync_summary = sync_item(item_id)
        if sync_summary.get("error"):
            # fall back to all items if id unknown
            sync_summary = sync_all_items()
    else:
        sync_summary = sync_all_items()

    review_summary: dict[str, Any] = {}
    try:
        from .auto_review import run_and_persist

        review_summary = run_and_persist(auto_threshold=0.9)
    except Exception as e:
        review_summary = {"error": str(e)[:200]}
        whlog(f"auto-review error: {e}")

    # Auto-review clears false Plaid TRANSFER_OUT on MasterMoney, which also
    # revives statement twins. Re-apply SSOT after review, including empty syncs
    # (sync_item only persists when the batch is non-empty).
    try:
        from .dedupe import persist_statement_ssot

        review_summary["statement_ssot_excluded"] = persist_statement_ssot()
    except Exception as e:
        review_summary["statement_ssot_error"] = str(e)[:200]
        whlog(f"statement-ssot error: {e}")

    txns = load_txns()
    tz = ZoneInfo(cfg.get("timezone") or "America/Los_Angeles")
    as_of = datetime.now(tz).date()
    both = evaluate_budget_both(txns, cfg, as_of=as_of)
    snap = both["calendar"]  # notify SSOT
    record_period_series(
        as_of, both["calendar"], both["rolling_30d"], source="webhook"
    )
    prev_risk = load_last_run().get("last_risk")
    new_ids = list(sync_summary.get("new_txn_ids") or [])
    by_id = {t.id: t for t in txns}
    new_tx_objs = [by_id[i] for i in new_ids if i in by_id]
    alerts = budget_alerts(
        snap,
        cfg,
        prev_risk=prev_risk,
        new_txn_ids=new_ids,
        new_txns=new_tx_objs,
    )
    if bool(cfg.get("coaching_anomalies", True)):
        from .rules import detect_anomalies

        alerts = list(alerts) + detect_anomalies(txns, cfg, as_of=as_of)

    push_events = [ev for ev in alerts if ev.kind in PUSH_KINDS]
    results = send_alerts(push_events, dry_run=dry_run)
    pri_by_key = {ev.key: (ev.payload or {}).get("push_priority") for ev in push_events}
    for row in results:
        row["priority"] = pri_by_key.get(row["key"])

    # Sync-break: /item/get probe (login-required + stale) + sync errors
    try:
        from .sync_health import attach_item_probes, process_sync_health

        attach_item_probes(sync_summary)
        results.extend(process_sync_health(sync_summary, dry_run=dry_run))
    except Exception as e:
        whlog(f"sync_health error: {e}")

    new_n = int(sync_summary.get("upserted_new") or 0)
    save_last_run(
        {
            **load_last_run(),
            "updated": datetime.now(ZoneInfo("UTC")).isoformat(),
            "as_of": as_of.isoformat(),
            "last_risk": snap.risk,
            "last_webhook_source": source,
            "last_webhook_sync": sync_summary,
            "snapshot": snap.to_dict(),
            "snapshot_rolling_30d": both["rolling_30d"].to_dict(),
            "webhook_notify_results": results,
            "dry_run": dry_run,
            "txn_count": len(txns),
            "mode": cfg.get("mode"),
        }
    )
    ar_brief = {
        "n": review_summary.get("n"),
        "status_counts": review_summary.get("status_counts"),
        "needs_review_rate": review_summary.get("needs_review_rate"),
        "error": review_summary.get("error"),
    }
    out = {
        "ok": True,
        "source": source,
        "item_id": item_id,
        "sync": sync_summary,
        "auto_review": {k: v for k, v in ar_brief.items() if v is not None},
        "risk": snap.risk,
        "spend_cents": snap.spend_to_date,
        "new_txns": new_n,
        "notify": results,
        "dry_run": dry_run,
    }
    whlog(
        f"processed source={source} item={item_id or '*'} "
        f"new={new_n} risk={snap.risk} alerts={len(results)} dry_run={dry_run}"
    )
    return out


def _should_process(payload: dict[str, Any]) -> bool:
    wtype = (payload.get("webhook_type") or "").upper()
    code = (payload.get("webhook_code") or "").upper()
    if wtype == "TRANSACTIONS" and code in SYNC_CODES:
        return True
    # ITEM errors are handled separately (break email, no txn sync)
    if wtype == "ITEM":
        return False
    # Unknown: ignore quietly (Plaid sends probes occasionally)
    whlog(f"ignored webhook type={wtype} code={code}")
    return False


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "HermesPlaidWebhook/0.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _paths(self) -> tuple[str, str]:
        path = urlparse(self.path).path.rstrip("/") or "/"
        mount = getattr(self.server, "mount", "").rstrip("/")
        return path, mount

    def do_GET(self) -> None:  # noqa: N802
        path, mount = self._paths()
        if path in (mount + "/health", "/health", mount):
            body = json.dumps({"ok": True, "service": "hermes-plaid-webhook"}).encode()
            self._send(200, body)
            return
        self._send(404, b'{"error":"not_found"}')

    def do_POST(self) -> None:  # noqa: N802
        path, mount = self._paths()
        # Accept mount root or /hook under mount (Funnel may strip prefix)
        ok_paths = {mount, mount + "/hook", "/hook", "/"}
        if path not in ok_paths and not path.startswith(mount):
            self._send(404, b'{"error":"not_found"}')
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(max(0, min(length, 1_000_000))) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error":"bad_json"}')
            return

        # ACK fast — Plaid retries on slow/non-2xx
        self._send(200, b'{"received":true}')

        wtype = (payload.get("webhook_type") or "").upper()
        code = payload.get("webhook_code")
        item_id = payload.get("item_id")

        if wtype == "ITEM":
            whlog(
                f"item webhook code={code} item={item_id} err={payload.get('error')}"
            )

            def item_work() -> None:
                try:
                    from .sync_health import ITEM_REPAIR_WEBHOOK_CODES, handle_item_webhook

                    results = handle_item_webhook(payload)
                    whlog(
                        f"item webhook handled code={code} item={item_id} "
                        f"notify={results}"
                    )
                    if str(code or "").upper() in ITEM_REPAIR_WEBHOOK_CODES:
                        # Do not /transactions/sync on LOGIN_REPAIRED.
                        # NorCal flakes back to ITEM_LOGIN_REQUIRED if we pull
                        # the FI in the next few minutes. Wait for
                        # SYNC_UPDATES_AVAILABLE.
                        whlog(
                            f"LOGIN_REPAIRED item={item_id}; "
                            "skip immediate sync (NorCal flake)"
                        )
                except Exception:
                    whlog("item webhook error:\n" + traceback.format_exc()[:1500])

            threading.Thread(target=item_work, daemon=True).start()
            return

        if not _should_process(payload):
            return

        def work() -> None:
            try:
                process_update(item_id=item_id, source=f"webhook:{code}")
            except Exception:
                whlog("process error:\n" + traceback.format_exc()[:1500])

        threading.Thread(target=work, daemon=True).start()


def run_server(
    *,
    port: int | None = None,
    mount: str | None = None,
    funnel: bool = True,
) -> HTTPServer:
    secret = ensure_webhook_secret()
    mount = mount or mount_path(secret)
    port = port or webhook_port()
    if funnel:
        ensure_funnel(mount, port)
    httpd = HTTPServer(("127.0.0.1", port), WebhookHandler)
    httpd.mount = mount  # type: ignore[attr-defined]
    httpd.webhook_url = public_webhook_url(secret)  # type: ignore[attr-defined]
    whlog(f"listen 127.0.0.1:{port} mount={mount}")
    return httpd


def serve_forever(*, port: int | None = None, funnel: bool = True) -> None:
    httpd = run_server(port=port, funnel=funnel)
    print(
        json.dumps(
            {
                "ok": True,
                "local": f"http://127.0.0.1:{httpd.server_port}{httpd.mount}",  # type: ignore[attr-defined]
                "public_webhook": getattr(httpd, "webhook_url", None),
                "mount": httpd.mount,  # type: ignore[attr-defined]
            },
            indent=2,
        ),
        flush=True,
    )
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
