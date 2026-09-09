"""One-shot Plaid Link server for PayPal. 127.0.0.1 only; funnel a secret path."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import state_dir
from .plaid_client import create_link_token, exchange_public_token, load_plaid_env

try:
    from .plaid_webhook import public_webhook_url
except Exception:  # pragma: no cover
    public_webhook_url = None  # type: ignore


def tokens_dir() -> Path:
    d = state_dir() / "tokens"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def save_item(access_token: str, item_id: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    p = tokens_dir() / f"{item_id}.json"
    data = {
        "access_token": access_token,
        "item_id": item_id,
        "institution": (meta or {}).get("institution") or "paypal",
        "meta": meta or {},
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(p)
    idx = tokens_dir() / "items.json"
    items: list[dict[str, Any]] = []
    if idx.is_file():
        try:
            items = json.loads(idx.read_text())
        except Exception:
            items = []
    prev = next((i for i in items if i.get("item_id") == item_id), None) or {}
    items = [i for i in items if i.get("item_id") != item_id]
    inst = data["institution"] or prev.get("institution") or "paypal"
    entry: dict[str, Any] = {
        "item_id": item_id,
        "institution": inst,
        "token_file": p.name,
    }
    for keep in (
        "quarantine",
        "quarantine_since",
        "quarantine_reason",
        "promoted_at",
        "amount_unit",
    ):
        if keep in prev:
            entry[keep] = prev[keep]
    # New non-PayPal Items stay quarantined until plaid-preview / plaid-promote.
    if not prev and "paypal" not in str(inst).lower():
        entry["quarantine"] = True
        entry["quarantine_since"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry["quarantine_reason"] = "new_item_preview"
    items.append(entry)
    idx.write_text(json.dumps(items, indent=2) + "\n")
    idx.chmod(0o600)
    return entry


OAUTH_PORT = int(os.environ.get("PLAID_OAUTH_PORT", "8764"))
DEFAULT_NEW_BLURB = (
    "Connect a bank or PayPal via Plaid. For the new CU, search "
    "<strong>Alliant Credit Union</strong>. Tokens stay on this box only."
)


def configured_redirect_uri(env: dict[str, str] | None = None) -> str | None:
    """Stable HTTPS OAuth return URL from env (Plaid dashboard must allow it)."""
    uri = (os.environ.get("PLAID_REDIRECT_URI") or "").strip()
    if not uri:
        try:
            env = env if env is not None else load_plaid_env()
        except FileNotFoundError:
            env = {}
        uri = (env.get("PLAID_REDIRECT_URI") or "").strip()
    return uri or None


def redirect_mount(uri: str | None = None) -> str | None:
    uri = uri if uri is not None else configured_redirect_uri()
    if not uri:
        return None
    path = urlparse(uri).path.rstrip("/")
    return path or None


def inflight_path() -> Path:
    return state_dir() / "plaid-link-inflight.json"


def load_inflight() -> dict[str, Any]:
    p = inflight_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_inflight(data: dict[str, Any]) -> None:
    p = inflight_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(p)


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    try:
        with socket.create_connection((host, port), 0.2):
            return True
    except OSError:
        return False


def _webhook_url() -> str | None:
    if public_webhook_url is None:
        return None
    try:
        return public_webhook_url()
    except Exception:
        return None


def create_link_token_for_link(
    *,
    access_token: str | None = None,
    redirect_uri: str | None = None,
    allow_redirect_fallback: bool = False,
) -> dict[str, Any]:
    uri = redirect_uri if redirect_uri is not None else configured_redirect_uri()
    webhook = _webhook_url()
    try:
        return create_link_token(
            redirect_uri=uri, webhook=webhook, access_token=access_token
        )
    except RuntimeError as e:
        if allow_redirect_fallback and uri and "redirect" in str(e).lower():
            return create_link_token(
                redirect_uri=None, webhook=webhook, access_token=access_token
            )
        raise


def complete_public_token_exchange(
    public_token: str, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    meta = meta or {}
    ex = exchange_public_token(public_token)
    access = ex["access_token"]
    item_id = ex["item_id"]
    inst_name = (meta.get("institution") or {}).get("name") or "unknown"
    inst_slug = inst_name.lower().replace(" ", "-")[:40]
    entry = save_item(
        access,
        item_id,
        {
            "institution": inst_slug,
            "institution_name": inst_name,
            "accounts": meta.get("accounts"),
        },
    )
    try:
        from .plaid_client import item_webhook_update

        wh = _webhook_url()
        if wh:
            item_webhook_update(access, wh)
    except Exception:
        pass
    result = {
        "item_id": item_id,
        "institution": inst_name,
        "quarantine": bool(entry.get("quarantine")),
        "quarantine_reason": entry.get("quarantine_reason"),
    }
    inf = load_inflight()
    inf["result"] = result
    save_inflight(inf)
    return result


def page_html(link_token: str, mount: str, *, heading: str, blurb: str, button: str) -> bytes:
    mount_js = json.dumps(mount.rstrip("/"))
    token_js = json.dumps(link_token)
    heading_h = heading.replace("<", "")
    blurb_h = blurb
    button_h = button.replace("<", "")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Budget Bot · {heading_h}</title>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; }}
    button {{ font-size: 1.1rem; padding: 0.75rem 1.25rem; cursor: pointer; }}
    #status {{ margin-top: 1rem; white-space: pre-wrap; }}
    .ok {{ color: #0a0; }} .err {{ color: #a00; }}
  </style>
</head>
<body>
  <h1>Budget Bot · {heading_h}</h1>
  <p>{blurb_h}</p>
  <button id="link-btn" type="button">{button_h}</button>
  <div id="status"></div>
  <script>
    // Prefer path from the page URL so Funnel strip/no-strip both work.
    const cfgMount = {mount_js};
    const pathMount = (location.pathname.replace(/\\/+$/, '') || '');
    const MOUNT = pathMount && pathMount !== '/' ? pathMount : cfgMount;
    const status = (t, cls) => {{
      const el = document.getElementById('status');
      el.textContent = t;
      el.className = cls || '';
    }};
    async function readJson(r) {{
      const text = await r.text();
      if (!text) throw new Error('empty response HTTP ' + r.status);
      try {{ return JSON.parse(text); }}
      catch (e) {{
        throw new Error('non-JSON HTTP ' + r.status + ': ' + text.slice(0, 180));
      }}
    }}
    const params = new URLSearchParams(location.search);
    const returning = params.has('oauth_state_id');
    const storageKey = 'hermes_plaid_link_token';
    try {{ if ({token_js}) sessionStorage.setItem(storageKey, {token_js}); }} catch (e) {{}}
    const token = {token_js} || (function(){{
      try {{ return sessionStorage.getItem(storageKey); }} catch (e) {{ return null; }}
    }})();
    if (!token) {{
      status('No Link session. Ask Budget Bot to start one.', 'err');
    }} else {{
      const cfg = {{
        token: token,
        onSuccess: async (public_token, metadata) => {{
          status('Exchanging token…');
          try {{
            const r = await fetch(MOUNT + '/exchange', {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json', 'Accept': 'application/json'}},
              body: JSON.stringify({{public_token, metadata}}),
            }});
            const j = await readJson(r);
            if (!r.ok) throw new Error(j.error || r.statusText);
            status('Linked OK.\\nitem_id=' + j.item_id + '\\nYou can close this tab.', 'ok');
          }} catch (e) {{
            status('Exchange failed: ' + (e && e.message ? e.message : e), 'err');
          }}
        }},
        onExit: (err) => {{
          if (err) status('Link exit: ' + JSON.stringify(err), 'err');
        }},
      }};
      if (returning) cfg.receivedRedirectUri = location.href;
      const handler = Plaid.create(cfg);
      if (returning) {{
        status('Finishing OAuth…');
        handler.open();
      }} else {{
        document.getElementById('link-btn').onclick = () => handler.open();
      }}
    }}
  </script>
</body>
</html>
"""
    return html.encode()


class LinkHandler(BaseHTTPRequestHandler):
    server_version = "HermesPlaidLink/0.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _paths(self) -> tuple[str, str]:
        """Return (request_path, mount). Tailscale --set-path may strip mount prefix."""
        path = urlparse(self.path).path.rstrip("/") or "/"
        mount = getattr(self.server, "mount", "").rstrip("/")
        return path, mount

    def _is_root(self, path: str, mount: str) -> bool:
        return path in (mount, "/", "")

    def _is_exchange(self, path: str, mount: str) -> bool:
        return path in (mount + "/exchange", "/exchange")

    def _is_health(self, path: str, mount: str) -> bool:
        return path in (mount + "/health", "/health")

    def do_GET(self) -> None:  # noqa: N802
        path, mount = self._paths()
        if self._is_health(path, mount):
            self._send(200, b"ok", "text/plain")
            return
        if not self._is_root(path, mount):
            self._send(404, b"not found", "text/plain")
            return
        token = getattr(self.server, "link_token", "")
        # Browser still needs public mount prefix for fetch() if path not stripped
        public_mount = mount if path == mount else mount
        self._send(
            200,
            page_html(
                token,
                public_mount,
                heading=getattr(self.server, "page_heading", "Plaid Link"),
                blurb=getattr(
                    self.server,
                    "page_blurb",
                    DEFAULT_NEW_BLURB,
                ),
                button=getattr(self.server, "page_button", "Connect account"),
            ),
        )

    def do_POST(self) -> None:  # noqa: N802
        path, mount = self._paths()
        if not self._is_exchange(path, mount):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode())
            result = complete_public_token_exchange(
                data["public_token"], data.get("metadata") or {}
            )
            self.server.result = result  # type: ignore[attr-defined]
            body = json.dumps({"ok": True, **result}).encode()
            self._send(200, body, "application/json")
        except Exception as e:
            # Always JSON — Safari r.json() on HTML/plain throws opaque SyntaxError
            err = str(e)
            body = json.dumps({"ok": False, "error": err[:500]}).encode()
            self._send(500, body, "application/json")


def run_link_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    mount: str | None = None,
    redirect_uri: str | None = None,
    access_token: str | None = None,
    page_heading: str | None = None,
    page_blurb: str | None = None,
    page_button: str | None = None,
) -> dict[str, Any]:
    if mount is None:
        mount = redirect_mount(redirect_uri or configured_redirect_uri()) or (
            "/hermes-link-" + secrets.token_hex(8)
        )
    if not mount.startswith("/"):
        mount = "/" + mount
    tok = create_link_token_for_link(
        access_token=access_token,
        redirect_uri=redirect_uri,
        allow_redirect_fallback=bool(access_token),
    )
    heading = page_heading or "Plaid Link"
    blurb = page_blurb or DEFAULT_NEW_BLURB
    button = page_button or "Connect account"
    save_inflight(
        {
            "link_token": tok["link_token"],
            "expiration": tok.get("expiration"),
            "mode": "update" if access_token else "create",
            "mount": mount,
            "heading": heading,
            "blurb": blurb,
            "button": button,
        }
    )
    httpd = HTTPServer((host, port), LinkHandler)
    httpd.link_token = tok["link_token"]  # type: ignore[attr-defined]
    httpd.mount = mount  # type: ignore[attr-defined]
    httpd.result = None  # type: ignore[attr-defined]
    httpd.page_heading = heading  # type: ignore[attr-defined]
    httpd.page_blurb = blurb  # type: ignore[attr-defined]
    httpd.page_button = button  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return {
        "host": host,
        "port": port,
        "mount": mount,
        "link_token_expiration": tok.get("expiration"),
        "server": httpd,
    }


REPAIR_PORT = int(os.environ.get("PLAID_REPAIR_PORT", "8765"))
REPAIR_TTL_HOURS = 24
FUNNEL_HOST = os.environ.get("HERMES_FUNNEL_HOST", "zaz-astra.tail5d74e1.ts.net")


def repair_sessions_path() -> Path:
    return state_dir() / "repair-links.json"


def load_repair_sessions() -> dict[str, Any]:
    p = repair_sessions_path()
    if not p.is_file():
        return {"sessions": {}}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {"sessions": {}}
    if not isinstance(data, dict):
        return {"sessions": {}}
    data.setdefault("sessions", {})
    return data


def save_repair_sessions(data: dict[str, Any]) -> None:
    p = repair_sessions_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(p)


def _funnel_path(mount: str, port: int) -> None:
    if not mount.startswith("/"):
        mount = "/" + mount
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


def mint_repair_link(
    item_id: str,
    *,
    ttl_hours: int = REPAIR_TTL_HOURS,
    port: int | None = None,
) -> dict[str, str]:
    """Create or refresh a 24h Funnel URL for update-mode Link. No txn sync."""
    from .plaid_sync import list_items

    port = port or REPAIR_PORT
    match = next((i for i in list_items() if i.get("item_id") == item_id), None)
    if not match:
        raise ValueError(f"unknown_item:{item_id}")
    data = load_repair_sessions()
    sessions: dict[str, Any] = data.setdefault("sessions", {})
    now = datetime.now(timezone.utc)
    row = sessions.get(item_id) or {}
    mount = str(row.get("mount") or ("/hermes-repair-" + secrets.token_hex(8)))
    if not mount.startswith("/"):
        mount = "/" + mount
    expires = now + timedelta(hours=max(1, int(ttl_hours)))
    sessions[item_id] = {
        "item_id": item_id,
        "mount": mount,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "institution": match.get("institution") or "unknown",
    }
    save_repair_sessions(data)
    _funnel_path(mount, port)
    return {
        "public_url": f"https://{FUNNEL_HOST}{mount}",
        "mount": mount,
        "expires_at": sessions[item_id]["expires_at"],
        "item_id": item_id,
    }


def _parse_exp(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def session_for_path(path: str) -> dict[str, Any] | None:
    """Match a request path to a non-expired repair session."""
    data = load_repair_sessions()
    now = datetime.now(timezone.utc)
    live: list[dict[str, Any]] = []
    raw_path = (path or "/").rstrip("/") or "/"
    for s in (data.get("sessions") or {}).values():
        exp = _parse_exp(str(s.get("expires_at") or ""))
        if exp is None or exp < now:
            continue
        live.append(s)
        mount = str(s.get("mount") or "").rstrip("/")
        if raw_path in (mount, mount + "/exchange", "/exchange"):
            return s
        if raw_path == mount:
            return s
    if raw_path in ("/", "/exchange") and live:
        live.sort(key=lambda s: str(s.get("expires_at") or ""), reverse=True)
        return live[0]
    return None


class RepairHandler(LinkHandler):
    """Long-lived update-mode Link. Mints a fresh Plaid token on each GET."""

    server_version = "HermesPlaidRepair/0.1"

    def _session(self) -> dict[str, Any] | None:
        path = urlparse(self.path).path
        return session_for_path(path)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.endswith("/health") or path == "/health":
            self._send(200, b"ok", "text/plain")
            return
        sess = self._session()
        if not sess:
            self._send(
                404,
                b"This login link expired. Ask Budget Bot for a new one.",
                "text/plain",
            )
            return
        from .plaid_sync import list_items, load_access_token

        item_id = str(sess.get("item_id") or "")
        match = next((i for i in list_items() if i.get("item_id") == item_id), None)
        if not match:
            self._send(404, b"unknown item", "text/plain")
            return
        tok = create_link_token_for_link(
            access_token=load_access_token(match),
            allow_redirect_fallback=True,
        )
        mount = str(sess.get("mount") or "")
        inst = str(sess.get("institution") or "item")
        heading = "NorCal re-login" if "nor" in inst.lower() else "Re-login"
        blurb = (
            "Update-mode Link — same Item, no duplicate. "
            "Log in when prompted. Tokens stay on this box only. "
            "This page is good for 24 hours."
        )
        save_inflight(
            {
                "link_token": tok["link_token"],
                "expiration": tok.get("expiration"),
                "mode": "update",
                "item_id": item_id,
                "mount": mount,
                "heading": heading,
                "blurb": blurb,
                "button": "Re-login",
            }
        )
        self._send(
            200,
            page_html(
                tok["link_token"],
                mount,
                heading=heading,
                blurb=blurb,
                button="Re-login",
            ),
        )

    def do_POST(self) -> None:  # noqa: N802
        sess = self._session()
        if not sess:
            self._send(404, b'{"error":"expired"}', "application/json")
            return
        path = urlparse(self.path).path
        mount = str(sess.get("mount") or "").rstrip("/")
        if path.rstrip("/") not in (mount + "/exchange", "/exchange"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode())
            result = complete_public_token_exchange(
                data["public_token"], data.get("metadata") or {}
            )
            try:
                from .plaid_sync import mark_item_repaired

                mark_item_repaired(result.get("item_id") or "")
            except Exception:
                pass
            # Do not /transactions/sync here — NorCal flakes if we pull the FI now.
            body = json.dumps({"ok": True, **result}).encode()
            self._send(200, body, "application/json")
        except Exception as e:
            body = json.dumps({"ok": False, "error": str(e)[:500]}).encode()
            self._send(500, body, "application/json")


class OauthHandler(LinkHandler):
    """Stable redirect_uri page on PLAID_OAUTH_PORT (Funnel strips path → /)."""

    server_version = "HermesPlaidOauth/0.1"

    def do_GET(self) -> None:  # noqa: N802
        path, mount = self._paths()
        if self._is_health(path, mount):
            self._send(200, b"ok", "text/plain")
            return
        if not self._is_root(path, mount):
            self._send(404, b"not found", "text/plain")
            return
        inf = load_inflight()
        token = str(inf.get("link_token") or "")
        if not token:
            self._send(
                200,
                b"No Link session in progress. Ask Budget Bot to start one.",
                "text/plain",
            )
            return
        self._send(
            200,
            page_html(
                token,
                mount,
                heading=str(inf.get("heading") or "Plaid Link"),
                blurb=str(inf.get("blurb") or DEFAULT_NEW_BLURB),
                button=str(inf.get("button") or "Connect account"),
            ),
        )


def serve_oauth_forever(*, port: int | None = None) -> HTTPServer:
    port = port or OAUTH_PORT
    mount = redirect_mount() or "/hermes-plaid-oauth"
    httpd = HTTPServer(("127.0.0.1", port), OauthHandler)
    httpd.mount = mount  # type: ignore[attr-defined]
    httpd.result = None  # type: ignore[attr-defined]
    httpd.link_token = ""  # type: ignore[attr-defined]
    httpd.page_heading = "Plaid Link"  # type: ignore[attr-defined]
    httpd.page_blurb = DEFAULT_NEW_BLURB  # type: ignore[attr-defined]
    httpd.page_button = "Connect account"  # type: ignore[attr-defined]
    _funnel_path(mount, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve_repair_forever(*, port: int | None = None) -> None:
    port = port or REPAIR_PORT
    data = load_repair_sessions()
    for s in (data.get("sessions") or {}).values():
        mount = s.get("mount")
        if mount:
            _funnel_path(str(mount), port)
    oauth = None
    oauth_err = None
    try:
        oauth = serve_oauth_forever()
    except OSError as e:
        oauth_err = str(e)[:200]
    httpd = HTTPServer(("127.0.0.1", port), RepairHandler)
    print(
        json.dumps(
            {
                "ok": True,
                "listen": f"127.0.0.1:{port}",
                "oauth_listen": f"127.0.0.1:{OAUTH_PORT}" if oauth else None,
                "oauth_configured": bool(redirect_mount()),
                "oauth_error": oauth_err,
            }
        ),
        flush=True,
    )
    httpd.serve_forever()
