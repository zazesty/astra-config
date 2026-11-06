#!/usr/bin/env python3
"""Email when this VPS needs cleanup or a bigger box.

Once a day at noon PT. One email when a condition starts, and a reminder
every 7 days while it holds. A clear day sends nothing. Email only.

One noon sample is enough for every line.

  disk    use >= 80% or free < 5 GiB. Drops off under 75% and at least 6 GiB free.
  inodes  use >= 80%. Drops off under 70%.
  memory  available < 256 MiB and swap used > 512 MiB.
  load    1-minute load above twice the CPU count.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("CAPACITY_WATCH_STATE", "/root/.local/state/astra/capacity-watch.json"))
NOTIFY = os.environ.get("CAPACITY_WATCH_NOTIFY", "/root/astra-config/scripts/notify-email.sh")
REMIND_S = int(os.environ.get("CAPACITY_WATCH_REMIND_S", str(7 * 24 * 3600)))

GIB = 1024**3
MIB = 1024**2


def sample() -> dict:
    disk = _df("/")
    inodes = _df_inodes("/")
    mem = _meminfo()
    load1 = float(Path("/proc/loadavg").read_text().split()[0])
    return {
        "disk_pct": disk["pct"],
        "disk_free": disk["avail"],
        "inode_pct": inodes["pct"],
        "mem_avail": mem["avail"],
        "swap_used": mem["swap_used"],
        "load1": load1,
        "nproc": os.cpu_count() or 1,
    }


def _pct(text: str) -> float:
    return float(text.strip().rstrip("%"))


def _df(path: str) -> dict:
    out = subprocess.check_output(["df", "-B1", "--output=avail,pcent", path], text=True)
    avail, pct = out.splitlines()[1].split()
    return {"avail": int(avail), "pct": _pct(pct)}


def _df_inodes(path: str) -> dict:
    # This df rejects -i together with --output. -P keeps one line per mount.
    out = subprocess.check_output(["df", "-Pi", path], text=True)
    pct = next(tok for tok in out.splitlines()[-1].split() if tok.endswith("%"))
    return {"pct": _pct(pct)}


def _meminfo() -> dict:
    vals = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":", 1)
        vals[key] = int(rest.strip().split()[0]) * 1024
    avail = vals.get("MemAvailable", 0)
    swap_used = max(0, vals.get("SwapTotal", 0) - vals.get("SwapFree", 0))
    return {"avail": avail, "swap_used": swap_used}


def evaluate(prev: dict, now: dict, ts: float) -> tuple[dict, str | None, str | None]:
    """Return (new_state, subject, body). subject is None when no mail is due."""
    latched = set(prev.get("latched") or [])
    streaks = dict(prev.get("streaks") or {})

    def latch(name: str, hot: bool, enter_after: int, clear: bool | None = None) -> None:
        streaks[name] = streaks.get(name, 0) + 1 if hot else 0
        if name in latched:
            if clear if clear is not None else not hot:
                latched.discard(name)
        elif streaks[name] >= enter_after:
            latched.add(name)

    disk_hot = now["disk_pct"] >= 80 or now["disk_free"] < 5 * GIB
    disk_clear = now["disk_pct"] < 75 and now["disk_free"] >= 6 * GIB
    inode_hot = now["inode_pct"] >= 80
    inode_clear = now["inode_pct"] < 70
    mem_hot = now["mem_avail"] < 256 * MIB and now["swap_used"] > 512 * MIB
    load_hot = now["load1"] > 2 * now["nproc"]

    latch("disk", disk_hot, 1, disk_clear)
    latch("inodes", inode_hot, 1, inode_clear)
    latch("memory", mem_hot, 1)
    latch("load", load_hot, 1)

    was = set(prev.get("latched") or [])
    last_mail = float(prev.get("last_mail_epoch") or 0)
    mailed = False
    kind = None
    if latched and latched != was:
        kind = "alert"
    elif latched and was and ts - last_mail >= REMIND_S:
        kind = "remind"
    if kind:
        mailed = True

    state = {
        "latched": sorted(latched),
        "streaks": streaks,
        "last_mail_epoch": ts if mailed else last_mail,
        "last_kind": kind or prev.get("last_kind"),
    }
    if not kind:
        return state, None, None
    subject, body = _message(kind, latched, now)
    return state, subject, body


def _fmt_bytes(n: int) -> str:
    if n >= GIB:
        return f"{n / GIB:.1f} GB"
    return f"{n / MIB:.0f} MB"


def _message(kind: str, latched: set[str], now: dict) -> tuple[str, str]:
    names = {
        "disk": "disk",
        "inodes": "inodes",
        "memory": "memory",
        "load": "CPU load",
    }
    label = ", ".join(names[n] for n in sorted(latched))
    subject = f"VPS capacity: {label}"
    if kind == "remind":
        subject += " (still)"
    body = "This needs cleanup or a bigger VPS.\n"
    lines = [
        body.rstrip(),
        "",
        f"Disk: {now['disk_pct']:.0f}% used, {_fmt_bytes(now['disk_free'])} free.",
        f"Inodes: {now['inode_pct']:.0f}% used.",
        f"Memory available: {_fmt_bytes(now['mem_avail'])}. Swap used: {_fmt_bytes(now['swap_used'])}.",
        f"Load: {now['load1']:.2f} on {now['nproc']} CPUs.",
        "",
    ]
    hints = []
    if "disk" in latched:
        hints.append("Disk is the one that fills the box. Clean up, or grow the volume.")
    if "inodes" in latched:
        hints.append("Inodes are file slots. Free gigabytes do not help once those run out.")
    if "memory" in latched:
        hints.append("RAM is tight and swap is in use. Add memory, or find what is holding it.")
    if "load" in latched:
        hints.append("Load is above twice the CPU count.")
    if hints:
        lines.extend(hints)
    return subject, "\n".join(lines).rstrip() + "\n"


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def _send(subject: str, body: str) -> None:
    subprocess.run([NOTIFY, subject], input=body.encode(), check=False)


def run(now: dict | None = None, ts: float | None = None, send=_send) -> int:
    now = sample() if now is None else now
    ts = time.time() if ts is None else ts
    state, subject, body = evaluate(_load_state(), now, ts)
    _save_state(state)
    if subject and body:
        send(subject, body)
        print(f"capacity-watch: mailed {subject}")
    else:
        print("capacity-watch: ok " + (", ".join(state["latched"]) or "clear"))
    return 0


def _self_test() -> int:
    fails = 0

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        if not cond:
            fails += 1

    base = {
        "disk_pct": 46, "disk_free": 20 * GIB, "inode_pct": 10,
        "mem_avail": 2 * GIB, "swap_used": 4 * MIB, "load1": 0.1, "nproc": 3,
    }

    def series(samples: list[tuple[int, dict | None]]) -> list[str | None]:
        state: dict = {}
        ts = 1_000_000.0
        subjects: list[str | None] = []
        for hours, mutate in samples:
            cur = dict(base)
            if mutate:
                cur.update(mutate)
            ts += hours * 3600
            state, subject, _body = evaluate(state, cur, ts)
            subjects.append(subject)
        return subjects

    disk = {"disk_pct": 82, "disk_free": 4 * GIB}
    quiet = series([(1, None)])
    check("quiet day sends nothing", quiet == [None])
    hot = series([(1, None), (1, disk), (1, disk), (24 * 8, disk), (1, {"disk_pct": 76, "disk_free": 7 * GIB}), (1, None), (1, disk)])
    check("one hot disk mails", hot[1] is not None and hot[1].startswith("VPS capacity: disk"))
    check("same disk stays quiet", hot[2] is None)
    check("week later reminds", hot[3] is not None and "still" in hot[3])
    check("hysteresis holds at 76%", hot[4] is None)
    check("clear day sends nothing", hot[5] is None)
    check("crossing again mails", hot[6] is not None and hot[6].startswith("VPS capacity: disk"))
    mem = {"mem_avail": 100 * MIB, "swap_used": 800 * MIB}
    mem_run = series([(1, mem), (1, None)])
    check("one memory noon mails", mem_run[0] is not None and "memory" in mem_run[0])
    check("memory clear stays quiet", mem_run[1] is None)
    load = {"load1": 9.0}
    load_run = series([(1, load)])
    check("one load noon mails", load_run[0] is not None and "CPU load" in load_run[0])
    inode = series([(1, {"inode_pct": 85})])
    check("inode line", inode[0] is not None and "inodes" in inode[0])
    print("capacity-watch self-test:", "pass" if not fails else "fail")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    sys.exit(run())
