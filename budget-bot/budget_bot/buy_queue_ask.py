"""Yes/No page for an uncertain buy-queue charge.

127.0.0.1 only. A secret Funnel path plus a per-charge token is the link
on the Pushover. Yes clears the row. No leaves it.
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = int(os.environ.get("BUY_QUEUE_ASK_PORT", "8773"))
_TOKEN = re.compile(r"^[a-f0-9]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store_path():
    from .config import state_dir

    return state_dir() / "buy_queue_asks.json"


def _load() -> dict[str, Any]:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    if not path.is_file():
        return {"form_secret": "", "asks": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("form_secret", "")
    data.setdefault("asks", {})
    return data


def _save(data: dict[str, Any]) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def ensure_secret() -> str:
    data = _load()
    if not data.get("form_secret"):
        data["form_secret"] = secrets.token_hex(16)
        _save(data)
    return str(data["form_secret"])


def funnel_host() -> str:
    host = os.environ.get("BUDGET_BOT_FUNNEL_HOST", "").strip().strip(".")
    if host:
        return host
    try:
        raw = subprocess.check_output(
            ["tailscale", "status", "--json"], text=True, timeout=5
        )
        dns = str(json.loads(raw).get("Self", {}).get("DNSName") or "")
        dns = dns.strip().strip(".")
        if dns:
            return dns
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return "zaz-astra.tail5d74e1.ts.net"


def _url(secret: str, token: str) -> str:
    return f"https://{funnel_host()}/bq-{secret}/{token}"


def mint_ask(ask: dict[str, Any]) -> str:
    """One link per row+charge. Reuse it if that charge was already asked."""
    data = _load()
    secret = str(data.get("form_secret") or "") or ensure_secret()
    data = _load()
    rid = str(ask.get("id") or "")
    txn = str(ask.get("txn_id") or "")
    for token, row in (data.get("asks") or {}).items():
        if row.get("id") == rid and row.get("txn_id") == txn:
            return _url(secret, token)
    token = secrets.token_hex(16)
    data["asks"][token] = {
        "id": rid,
        "name": str(ask.get("name") or rid),
        "merchant": str(ask.get("merchant") or ""),
        "amount_cents": int(ask.get("amount_cents") or 0),
        "txn_id": txn,
        "txn_cents": int(ask.get("txn_cents") or 0),
        "txn_date": str(ask.get("txn_date") or "")[:10],
        "created": _now(),
        "answer": None,
    }
    _save(data)
    return _url(secret, token)


def token_from_path(path: str, secret: str) -> str | None:
    """Full mount or the path Funnel leaves after stripping the mount."""
    clean = urlparse(path).path.rstrip("/") or "/"
    mount = f"/bq-{secret}"
    if clean.startswith(mount + "/"):
        token = clean[len(mount) + 1 :]
    elif clean.count("/") == 1 and clean != "/":
        token = clean[1:]
    else:
        return None
    token = token.split("/", 1)[0]
    if token == "health" or not _TOKEN.fullmatch(token):
        return None
    return token


def _dollars(cents: int) -> str:
    from .buy_queue import _dollars as fmt

    return fmt(cents)


def page_html(
    ask: dict[str, Any] | None,
    *,
    message: str = "",
    buttons: bool = False,
) -> bytes:
    if ask is None:
        body = "<h1>Not found</h1>"
    else:
        name = html.escape(str(ask.get("name") or "This item"))
        merchant = html.escape(str(ask.get("merchant") or "That charge"))
        paid = html.escape(_dollars(int(ask.get("txn_cents") or 0)))
        listed = html.escape(_dollars(int(ask.get("amount_cents") or 0)))
        when = html.escape(str(ask.get("txn_date") or ""))
        when_bit = f" on {when}" if when else ""
        banner = f"<p class='msg'>{html.escape(message)}</p>" if message else ""
        actions = ""
        if buttons:
            actions = """
<form method="post">
  <button class="yes" name="answer" value="yes">Yes, that was it</button>
  <button class="no" name="answer" value="no">No, leave it</button>
</form>
"""
        body = f"""
<h1>{name}?</h1>
{banner}
<p>{merchant} {paid}{when_bit}.</p>
<p class="sub">Listed at {listed}.</p>
{actions}
"""
    note = ""
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Buy queue</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 24rem; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ font-size: 1.7rem; margin-bottom: 0.4rem; }}
    p {{ font-size: 1.15rem; margin: 0.3rem 0; }}
    .sub {{ color: #444; }}
    .msg {{ font-weight: 650; }}
    button {{ display: block; width: 100%; font-size: 1.15rem; padding: 0.95rem; margin-top: 0.8rem; border-radius: 0.7rem; border: 1px solid #111; }}
    .yes {{ background: #111; color: #fff; }}
    .no {{ background: #fff; color: #111; }}
  </style>
</head>
<body>
{body}
{note}
</body>
</html>
"""
    return doc.encode()


def apply_answer(token: str, answer: str) -> tuple[int, bytes]:
    data = _load()
    ask = (data.get("asks") or {}).get(token)
    if not isinstance(ask, dict):
        return 404, page_html(None)
    answer = (answer or "").strip().lower()
    if answer not in ("yes", "no"):
        return 400, page_html(ask, message="Tap Yes or No.")
    if ask.get("answer") == "yes" or answer == "yes":
        cleared = _clear_row(str(ask.get("id") or ""))
        ask["answer"] = "yes"
        ask["answered_at"] = _now()
        data["asks"][token] = ask
        _save(data)
        msg = "Cleared. It's off the list." if cleared else "Already off the list."
        return 200, page_html(ask, message=msg)
    ask["answer"] = "no"
    ask["answered_at"] = _now()
    data["asks"][token] = ask
    _save(data)
    return 200, page_html(ask, message="Left on the list.", buttons=True)


def _clear_row(row_id: str) -> bool:
    from .buy_queue import drop_buy_queue_row
    from .buy_queue_sync import reconcile_buy_queue
    from .config import load_config

    cfg = load_config()
    dropped = drop_buy_queue_row(cfg, row_id, reason="ask-yes")
    reconcile_buy_queue(load_config())
    return dropped


class Handler(BaseHTTPRequestHandler):
    server_version = "BuyQueueAsk/1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Path is the capability. Do not write it.
        sys.stderr.write("buy-queue-ask\n")

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _token(self) -> str | None:
        return token_from_path(self.path, ensure_secret())

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        secret = ensure_secret()
        if path in ("/health", f"/bq-{secret}/health"):
            self._send(200, b"ok", "text/plain")
            return
        token = self._token()
        if not token:
            self._send(404, page_html(None))
            return
        ask = (_load().get("asks") or {}).get(token)
        if not isinstance(ask, dict):
            self._send(404, page_html(None))
            return
        if ask.get("answer") == "yes":
            self._send(200, page_html(ask, message="Already off the list."))
            return
        if ask.get("answer") == "no":
            self._send(200, page_html(ask, message="Left on the list.", buttons=True))
            return
        self._send(200, page_html(ask, buttons=True))

    def do_POST(self) -> None:  # noqa: N802
        token = self._token()
        if not token:
            self._send(404, page_html(None))
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        qs = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        answer = (qs.get("answer") or [""])[0]
        code, body = apply_answer(token, answer)
        self._send(code, body)


def main() -> int:
    ensure_secret()
    httpd = HTTPServer((HOST, PORT), Handler)
    print(json.dumps({"host": HOST, "port": PORT}), flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
