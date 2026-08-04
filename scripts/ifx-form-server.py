#!/usr/bin/env python3
"""Minimal IFX cycle log form — 127.0.0.1 only; secret path via Funnel.

State: ~/.local/state/health/ifx-cycle/
  config.json  — last_infusion_date, form_secret
  log.jsonl    — symptom rows
"""

from __future__ import annotations

import csv
import json
import secrets
import sys
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

STATE = Path.home() / ".local/state" / "health" / "ifx-cycle"
CFG = STATE / "config.json"
JSONL = STATE / "log.jsonl"
CSV = STATE / "log.csv"
HOST = "127.0.0.1"
PORT = int(__import__("os").environ.get("IFX_FORM_PORT", "8767"))
TZ = ZoneInfo("America/Los_Angeles")

CSV_FIELDS = [
    "date",
    "days_since_infusion",
    "sleep_1_5",
    "energy_floaty_1_5",
    "stool_form_1_7",
    "cramp",
    "bm_count",
    "notes",
]

# Bristol-style 1–7 with memorable anchors (same vibe as energy “bad floaty”).
STOOL_LABELS = {
    1: "1 deer pellets",
    2: "2 lumpy / rocky",
    3: "3 formed, cracks",
    4: "4 smooth gold",
    5: "5 soft blobs",
    6: "6 mushy soft-serve",
    7: "7 taco bell / pure liquid",
}


def ensure_state() -> dict:
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        STATE.chmod(0o700)
    except OSError:
        pass
    data: dict = {}
    if CFG.is_file():
        try:
            data = json.loads(CFG.read_text())
        except Exception:
            data = {}
    if not data.get("form_secret"):
        data["form_secret"] = secrets.token_hex(16)
        data["form_secret_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_cfg(data)
    return data


def save_cfg(data: dict) -> None:
    tmp = CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(CFG)


def days_since_infusion(cfg: dict, as_of: date | None = None) -> int | None:
    inf = cfg.get("last_infusion_date")
    if not inf:
        return None
    as_of = as_of or datetime.now(TZ).date()
    try:
        return (as_of - date.fromisoformat(inf)).days
    except ValueError:
        return None


def _normalize_row(row: dict) -> dict:
    """Map legacy keys so old log lines still export cleanly."""
    out = dict(row)
    if "cramp" not in out and out.get("gi"):
        out["cramp"] = out["gi"]
    if "stool_form_1_7" not in out:
        out["stool_form_1_7"] = ""
    if "bm_count" not in out:
        out["bm_count"] = ""
    return out


def rewrite_csv_from_jsonl() -> None:
    """Rebuild CSV from JSONL (handles field migrations)."""
    rows: list[dict] = []
    if JSONL.is_file():
        for ln in JSONL.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                rows.append(_normalize_row(json.loads(ln)))
            except json.JSONDecodeError:
                continue
    with CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    CSV.chmod(0o600)


def append_row(row: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with JSONL.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    JSONL.chmod(0o600)
    rewrite_csv_from_jsonl()


def page_html(cfg: dict, *, msg: str = "", err: str = "") -> bytes:
    today = datetime.now(TZ).date().isoformat()
    days = days_since_infusion(cfg)
    days_s = "" if days is None else str(days)
    inf = cfg.get("last_infusion_date") or "(not set)"
    secret = cfg["form_secret"]
    mount = f"/ifx-log-{secret}"
    banner = ""
    if msg:
        banner = f'<p class="ok">{msg}</p>'
    if err:
        banner = f'<p class="err">{err}</p>'
    stool_opts = "\n".join(
        f'        <option value="{n}">{STOOL_LABELS[n]}</option>' for n in range(1, 8)
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>IFX cycle log</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 28rem; margin: 1.5rem auto; padding: 0 1rem; }}
    label {{ display: block; margin-top: 0.75rem; font-weight: 600; }}
    input, select, textarea {{ width: 100%; box-sizing: border-box; margin-top: 0.25rem; padding: 0.5rem; font-size: 1rem; }}
    button {{ margin-top: 1.25rem; font-size: 1.05rem; padding: 0.7rem 1.2rem; cursor: pointer; width: 100%; }}
    .meta {{ color: #444; font-size: 0.95rem; }}
    .ok {{ color: #0a0; }} .err {{ color: #a00; }}
    .hint {{ color: #666; font-size: 0.85rem; margin-top: 0.35rem; font-weight: 400; }}
    .foot {{ color: #666; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <h1>IFX cycle log</h1>
  <p class="meta">Last infusion: <strong>{inf}</strong><br/>
  Today (PT): <strong>{today}</strong>
  {f" · day <strong>+{days}</strong>" if days is not None else ""}</p>
  {banner}
  <form method="POST" action="{mount}">
    <label>Days since infusion
      <input name="days_since" type="number" min="0" max="120" value="{days_s}" required/>
    </label>
    <label>Sleep quality (1–5)
      <select name="sleep" required>
        <option value="">—</option>
        <option value="1">1 awful</option>
        <option value="2">2</option>
        <option value="3">3 ok</option>
        <option value="4">4</option>
        <option value="5">5 great</option>
      </select>
    </label>
    <label>Energy / floaty-dissociated (1–5, 5 = solid)
      <select name="energy" required>
        <option value="">—</option>
        <option value="1">1 bad floaty</option>
        <option value="2">2</option>
        <option value="3">3 mid</option>
        <option value="4">4</option>
        <option value="5">5 solid</option>
      </select>
    </label>
    <label>Stool form (softness)
      <select name="stool_form" required>
        <option value="">—</option>
{stool_opts}
      </select>
      <span class="hint">4 smooth gold = ideal · 1 deer pellets (hard) · 7 taco bell / pure liquid</span>
    </label>
    <label>Abdominal pain / cramping
      <select name="cramp" required>
        <option value="none">none</option>
        <option value="mild">mild</option>
        <option value="moderate">moderate</option>
      </select>
    </label>
    <label>Bowel movements today (optional)
      <input name="bm_count" type="number" min="0" max="30" placeholder="e.g. 1"/>
      <span class="hint">Leave blank if not tracking today</span>
    </label>
    <label>Notes
      <textarea name="notes" rows="3" placeholder="sleep, floaty, urgency, blood, anything else…"></textarea>
    </label>
    <input type="hidden" name="date" value="{today}"/>
    <button type="submit">Save entry</button>
  </form>
  <p class="foot">Private on-box log only (not Google, not git). Mon/Thu morning email links here.</p>
</body>
</html>
"""
    return html.encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "IfxForm/0.2"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cfg(self) -> dict:
        return ensure_state()

    def _mount(self) -> str:
        return "/ifx-log-" + self._cfg()["form_secret"]

    def _paths(self) -> tuple[str, str]:
        path = urlparse(self.path).path.rstrip("/") or "/"
        mount = self._mount().rstrip("/")
        return path, mount

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self, path: str, mount: str) -> bool:
        # Funnel may strip mount prefix → path is / or /health
        return path in (mount, "/", "") or path.startswith(mount + "/")

    def do_GET(self) -> None:  # noqa: N802
        path, mount = self._paths()
        if path in (mount + "/health", "/health"):
            self._send(200, b"ok", "text/plain")
            return
        if not self._allowed(path, mount):
            self._send(404, b"not found", "text/plain")
            return
        self._send(200, page_html(self._cfg()))

    def do_POST(self) -> None:  # noqa: N802
        path, mount = self._paths()
        if path not in (mount, "/", mount + "/"):
            self._send(404, b"not found", "text/plain")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        qs = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)

        def one(k: str, default: str = "") -> str:
            v = qs.get(k, [default])
            return (v[0] if v else default).strip()

        try:
            sleep = int(one("sleep"))
            energy = int(one("energy"))
            days = int(one("days_since"))
            stool = int(one("stool_form"))
            # cramp is the clear label; accept legacy "gi" if present
            cramp = (one("cramp") or one("gi")).lower()
            bm_raw = one("bm_count")
            notes = one("notes")
            d = one("date") or datetime.now(TZ).date().isoformat()
            if not (1 <= sleep <= 5 and 1 <= energy <= 5):
                raise ValueError("sleep/energy must be 1-5")
            if not (1 <= stool <= 7):
                raise ValueError("stool form must be 1-7")
            if cramp not in ("none", "mild", "moderate"):
                raise ValueError("cramp must be none|mild|moderate")
            bm_count: int | None = None
            if bm_raw != "":
                bm_count = int(bm_raw)
                if not (0 <= bm_count <= 30):
                    raise ValueError("bm_count must be 0-30")
            date.fromisoformat(d)
            row = {
                "date": d,
                "days_since_infusion": days,
                "sleep_1_5": sleep,
                "energy_floaty_1_5": energy,
                "stool_form_1_7": stool,
                "cramp": cramp,
                "bm_count": bm_count,
                "notes": notes,
                "logged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": "form",
            }
            append_row(row)
            stool_s = STOOL_LABELS.get(stool, str(stool))
            bm_s = f" · bm {bm_count}" if bm_count is not None else ""
            self._send(
                200,
                page_html(
                    self._cfg(),
                    msg=(
                        f"Saved {d} · d+{days} · sleep {sleep} · energy {energy} · "
                        f"stool {stool_s} · cramp {cramp}{bm_s}"
                    ),
                ),
            )
        except Exception as e:  # noqa: BLE001
            self._send(400, page_html(self._cfg(), err=str(e)))


def public_form_url(host: str = "zaz-astra.tail5d74e1.ts.net") -> str:
    cfg = ensure_state()
    return f"https://{host}/ifx-log-{cfg['form_secret']}/"


def main() -> int:
    cfg = ensure_state()
    mount = f"/ifx-log-{cfg['form_secret']}"
    httpd = HTTPServer((HOST, PORT), Handler)
    print(
        json.dumps(
            {
                "host": HOST,
                "port": PORT,
                "mount": mount,
                "public_url_hint": public_form_url(),
            }
        ),
        flush=True,
    )
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
