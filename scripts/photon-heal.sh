#!/usr/bin/env bash
# photon-heal.sh — restart hermes-gateway when Photon is deaf or cannot send.
#
# The in-process watchdog does not cover the two failures we have actually had:
#   * inbound iterator hangs while /healthz stays healthy and probes stay
#     inconclusive (2026-10-08)
#   * a text arrives, the canned path skips the agent, and the SMS dies on
#     upstream ECONNRESET (2026-10-13)
# Silence alone is not a restart. A quiet evening stays up.
#
# Timer: photon-heal.timer, every 5 minutes. Local log only. No Pushover.
# Cooldown 6h. Cap 3 restarts per Pacific day.
#
#   photon-heal.sh --dry-run     print the decision
#   photon-heal.sh --self-test   fixture checks, no systemd
set -euo pipefail

STATE_DIR="${PHOTON_HEAL_STATE_DIR:-/root/.local/state/astra}"
install -d -m 700 "$STATE_DIR"
exec 9>"$STATE_DIR/photon-heal.lock"
if ! flock -n 9; then
  echo "photon-heal: already running"
  exit 0
fi

exec python3 - "$@" <<'PY'
import json, os, re, subprocess, sys, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_PATH = Path(os.environ.get("PHOTON_HEAL_GATEWAY_LOG", "/root/.hermes/logs/gateway.log"))
STATE_DIR = Path(os.environ.get("PHOTON_HEAL_STATE_DIR", "/root/.local/state/astra"))
STATE_PATH = STATE_DIR / "photon-heal.json"
HEAL_LOG = STATE_DIR / "photon-heal.log"
SYNC = Path("/root/astra-config/scripts/sync-hermes-astra-mcp.sh")
PT = ZoneInfo("America/Los_Angeles")

COOLDOWN_S = 6 * 3600
DAILY_CAP = 3
DEAF_S = 36 * 3600
RECYCLE_UP_S = 72 * 3600
RECYCLE_IDLE_S = 20 * 60
MAX_LOG_BYTES = 8_000_000

SEND_ERR = re.compile(
    r"read ECONNRESET"
    r"|ConnectionError: \[upstream\]"
    r"|photon-canned send failed"
    r"|UPSTREAM_STREAM_DEGRADED"
)
DEGRADED = {"degraded", "failed", "down", "error"}


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pt_day(dt):
    return dt.astimezone(PT).date().isoformat()


def load_state():
    try:
        data = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {"restarts": []}
    if not isinstance(data, dict):
        return {"restarts": []}
    data.setdefault("restarts", [])
    return data


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2) + "\n"
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload)
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_PATH)


def append_log(line):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with HEAL_LOG.open("a") as fh:
        fh.write(line + "\n")
    try:
        if HEAL_LOG.stat().st_size > 1_000_000:
            lines = HEAL_LOG.read_text(errors="replace").splitlines()[-200:]
            HEAL_LOG.write_text("\n".join(lines) + "\n")
            os.chmod(HEAL_LOG, 0o600)
    except OSError:
        pass


def unresolved_send_error(log_text):
    """True when a send failure sits after the latest 'photon connected'."""
    lines = log_text.splitlines()
    last = -1
    for i, line in enumerate(lines):
        if "photon connected" in line:
            last = i
    if last < 0:
        return False
    return any(SEND_ERR.search(line) for line in lines[last + 1 :])


def parse_health(body):
    if not isinstance(body, dict):
        return None
    stream = body.get("stream") if isinstance(body.get("stream"), dict) else body
    st = stream.get("staleness") if isinstance(stream.get("staleness"), dict) else {}
    if "silentForMs" in stream and "silentForMs" not in st:
        st = stream
    # Sidecar reports silence in milliseconds. Thresholds below are seconds.
    silent_ms = st.get("silentForMs", stream.get("silentForMs"))
    if silent_ms is not None:
        silent = float(silent_ms) / 1000.0
    elif st.get("silentForHours") is not None:
        silent = float(st["silentForHours"]) * 3600
    elif stream.get("silentForHours") is not None:
        silent = float(stream["silentForHours"]) * 3600
    else:
        silent = None
    probe = st.get("lastProbeOutcome", stream.get("lastProbeOutcome"))
    zombie = bool(st.get("zombieSuspected", stream.get("zombieSuspected", False)))
    state = str(stream.get("state") or "")
    return {
        "state": state,
        "zombie": zombie,
        "probe": probe,
        "silent_s": None if silent is None else float(silent),
        "degraded": state in DEGRADED or stream.get("ok") is False,
    }


def decide(gateway_state, health, log_text, uptime_s, state, now):
    """Return (action, reason). action is restart, skip, or hold."""
    if gateway_state in {"activating", "deactivating"}:
        return "hold", "starting"
    if gateway_state != "active":
        return gate("inactive", state, now)
    if health is None:
        return gate("healthz-down", state, now)
    if health["zombie"]:
        return gate("zombie", state, now)
    if health["degraded"]:
        return gate("degraded", state, now)
    if unresolved_send_error(log_text or ""):
        return gate("send-reset", state, now)
    silent = health["silent_s"]
    probe = health["probe"]
    up = uptime_s or 0
    if silent is not None and silent >= DEAF_S and up >= DEAF_S and probe != "alive":
        return gate("deaf", state, now)
    if (
        silent is not None
        and silent >= RECYCLE_IDLE_S
        and up >= RECYCLE_UP_S
        and probe != "alive"
    ):
        return gate("recycle", state, now)
    return "hold", "ok"


def gate(reason, state, now):
    restarts = []
    for raw in state.get("restarts") or []:
        dt = parse_ts(raw)
        if dt is not None:
            restarts.append(dt)
    if restarts:
        latest = max(restarts)
        if (now - latest).total_seconds() < COOLDOWN_S:
            return "skip", "cooldown " + reason
    today = pt_day(now)
    if sum(1 for dt in restarts if pt_day(dt) == today) >= DAILY_CAP:
        return "skip", "cap " + reason
    return "restart", reason


def read_log():
    try:
        size = LOG_PATH.stat().st_size
    except OSError:
        return ""
    with LOG_PATH.open("rb") as fh:
        if size > MAX_LOG_BYTES:
            fh.seek(size - MAX_LOG_BYTES)
            fh.readline()
        return fh.read().decode("utf-8", "replace")


def gateway_state():
    proc = subprocess.run(
        ["systemctl", "is-active", "hermes-gateway"],
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip() or "unknown"


def uptime_s(now):
    proc = subprocess.run(
        ["systemctl", "show", "hermes-gateway", "-p", "ActiveEnterTimestamp", "--value"],
        capture_output=True,
        text=True,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    parsed = subprocess.run(
        ["date", "-d", raw, "+%s"],
        capture_output=True,
        text=True,
    )
    if parsed.returncode != 0 or not (parsed.stdout or "").strip():
        return None
    started = int(parsed.stdout.strip())
    return max(0, int(now.timestamp()) - started)


def sidecar_targets():
    found = []
    proc_root = Path("/proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ")
        except OSError:
            continue
        if b"photon/sidecar/index.mjs" not in cmd:
            continue
        try:
            env_raw = (entry / "environ").read_bytes().split(b"\x00")
        except OSError:
            continue
        env = {}
        for item in env_raw:
            if b"=" in item:
                key, val = item.split(b"=", 1)
                env[key.decode("utf-8", "replace")] = val.decode("utf-8", "replace")
        token = env.get("PHOTON_SIDECAR_TOKEN") or ""
        port = env.get("PHOTON_SIDECAR_PORT") or "8789"
        if token:
            found.append((int(entry.name), port, token))
    return found


def fetch_health():
    last_err = None
    for _pid, port, token in sidecar_targets():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/healthz",
            data=b"",
            method="POST",
            headers={"X-Hermes-Sidecar-Token": token},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
        except (OSError, urllib.error.URLError, ValueError, TimeoutError) as exc:
            last_err = exc
            continue
        parsed = parse_health(body)
        if parsed is not None:
            return parsed
    return None


def restart_gateway():
    if SYNC.is_file():
        subprocess.run(["bash", str(SYNC)], check=False)
    subprocess.run(["systemctl", "restart", "hermes-gateway"], check=True, timeout=150)


def fmt(action, reason, health, up):
    silent = None if health is None else health.get("silent_s")
    probe = "-" if health is None else (health.get("probe") or "-")
    state = "-" if health is None else (health.get("state") or "-")
    silent_h = "-" if silent is None else f"{silent / 3600:.2f}"
    up_h = "-" if up is None else f"{up / 3600:.1f}"
    verb = {"restart": "restart", "skip": "skip", "hold": "hold"}[action]
    return (
        f"photon-heal: {verb} reason={reason} "
        f"stream={state} probe={probe} silent_h={silent_h} uptime_h={up_h}"
    )


def live(dry):
    now = now_utc()
    state = load_state()
    gstate = gateway_state()
    health = fetch_health() if gstate == "active" else None
    up = uptime_s(now) if gstate == "active" else 0
    action, reason = decide(gstate, health, read_log(), up, state, now)
    line = fmt(action, reason, health, up)
    if dry:
        print(line)
        return 0
    if action == "hold":
        return 0
    append_log(now.isoformat() + " " + line)
    print(line)
    if action != "restart":
        return 0
    restart_gateway()
    state.setdefault("restarts", []).append(now.isoformat())
    # Keep two days of stamps so the cap and cooldown have something to read.
    kept = []
    for raw in state["restarts"]:
        dt = parse_ts(raw)
        if dt is not None and (now - dt).total_seconds() < 3 * 86400:
            kept.append(dt.isoformat())
    state["restarts"] = kept
    state["last_reason"] = reason
    save_state(state)
    return 0


def self_test():
    now = datetime(2026, 10, 13, 20, 40, tzinfo=timezone.utc)
    healthy = {
        "state": "healthy",
        "zombie": False,
        "probe": "inconclusive",
        "silent_s": 2 * 3600,
        "degraded": False,
    }
    empty = {"restarts": []}
    checks = []

    def check(name, got, want):
        ok = got == want
        checks.append(ok)
        print(("ok  " if ok else "FAIL") + " " + name + f" got={got!r} want={want!r}")

    log_after = "INFO gateway.run: ✓ photon connected\nread ECONNRESET chat=+153****6948\n"
    check(
        "send error after connect",
        decide("active", healthy, log_after, 10 * 3600, empty, now)[1],
        "send-reset",
    )
    check(
        "phone stays out of the reason",
        "153" in decide("active", healthy, log_after, 10 * 3600, empty, now)[1],
        False,
    )
    log_before = "read ECONNRESET\nINFO gateway.run: ✓ photon connected\n"
    check(
        "old send error is cleared by connect",
        decide("active", healthy, log_before, 10 * 3600, empty, now)[0],
        "hold",
    )
    zombie = dict(healthy, zombie=True)
    check("zombie", decide("active", zombie, "", 100, empty, now)[1], "zombie")
    degraded = dict(healthy, degraded=True, state="degraded")
    check("degraded", decide("active", degraded, "", 100, empty, now)[1], "degraded")
    check("healthz down", decide("active", None, "", 100, empty, now)[1], "healthz-down")
    check("inactive", decide("inactive", None, "", 0, empty, now)[1], "inactive")
    check("activating holds", decide("activating", None, log_after, 0, empty, now)[0], "hold")
    check(
        "quiet evening holds",
        decide("active", healthy, "", 10 * 3600, empty, now)[0],
        "hold",
    )
    deaf = dict(healthy, silent_s=40 * 3600, probe="inconclusive")
    check("deaf stream", decide("active", deaf, "", 40 * 3600, empty, now)[1], "deaf")
    alive = dict(deaf, probe="alive")
    check(
        "proven-alive long silence holds",
        decide("active", alive, "", 50 * 3600, empty, now)[0],
        "hold",
    )
    recycle = dict(healthy, silent_s=30 * 60, probe="inconclusive")
    check(
        "old process recycle",
        decide("active", recycle, "", 80 * 3600, empty, now)[1],
        "recycle",
    )
    recent = dict(healthy, silent_s=5 * 60)
    check(
        "recent inbound skips recycle",
        decide("active", recent, "", 80 * 3600, empty, now)[0],
        "hold",
    )
    proved = dict(recycle, probe="alive")
    check(
        "alive probe skips recycle",
        decide("active", proved, "", 80 * 3600, empty, now)[0],
        "hold",
    )
    check(
        "send-reset beats recycle",
        decide("active", recycle, log_after, 80 * 3600, empty, now)[1],
        "send-reset",
    )
    cooled = {"restarts": ["2026-10-13T19:00:00+00:00"]}
    check(
        "cooldown",
        decide("active", healthy, log_after, 10 * 3600, cooled, now)[0],
        "skip",
    )
    capped = {
        "restarts": [
            "2026-10-13T10:00:00+00:00",
            "2026-10-13T12:00:00+00:00",
            "2026-10-13T14:00:00+00:00",
        ]
    }
    check(
        "daily cap",
        decide("active", healthy, log_after, 10 * 3600, capped, now),
        ("skip", "cap send-reset"),
    )
    nested = parse_health(
        {
            "ok": True,
            "stream": {
                "ok": True,
                "state": "healthy",
                "staleness": {
                    "silentForMs": 120000,
                    "lastProbeOutcome": "inconclusive",
                    "zombieSuspected": False,
                },
            },
        }
    )
    check("parse nested health", (nested["state"], nested["silent_s"], nested["probe"]), ("healthy", 120.0, "inconclusive"))
    half_hour = parse_health(
        {
            "stream": {
                "state": "healthy",
                "ok": True,
                "staleness": {
                    "silentForMs": 2_040_000,
                    "lastProbeOutcome": "inconclusive",
                    "zombieSuspected": False,
                },
            }
        }
    )
    check("milliseconds become seconds", round(half_hour["silent_s"]), 2040)
    check(
        "half hour of quiet holds",
        decide("active", half_hour, "", 10 * 3600, empty, now)[0],
        "hold",
    )
    flat = parse_health({"state": "healthy", "silentForHours": 0.5, "lastProbeOutcome": "alive", "zombieSuspected": False})
    check("parse flat health", (flat["silent_s"], flat["probe"], flat["degraded"]), (1800.0, "alive", False))
    failed = [n for n, ok in enumerate(checks, 1) if not ok]
    print(f"photon-heal self-test: {sum(checks)}/{len(checks)}")
    return 0 if not failed else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if any(a not in {"--dry-run"} for a in argv):
        print("photon-heal: unknown arg", file=sys.stderr)
        return 2
    return live("--dry-run" in argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as exc:
        print(f"photon-heal: restart failed rc={exc.returncode}", file=sys.stderr)
        sys.exit(1)
PY
