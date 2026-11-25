#!/usr/bin/env bash
# standing-todos.sh — open work board (not system status).
# State: ~/.local/state/astra/standing-todos.json
#
#   standing-todos.sh list
#   standing-todos.sh add "title" --author "name / agent" [--owner agent|human|either] [--priority 1-5] [--blocked-on text] [--plain text]
#   standing-todos.sh plain <id> "text"
#   standing-todos.sh done <id>
#   standing-todos.sh note <id> "text"
#   standing-todos.sh priority <id> <1-5>
set -euo pipefail
export TODOS_PATH="${TODOS_PATH:-$HOME/.local/state/astra/standing-todos.json}"
exec python3 - "$TODOS_PATH" "$@" <<'PY'
import fcntl, json, os, sys, re, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
args = sys.argv[2:]
if os.environ.get("STANDING_TODOS_INPLACE") != "1":
    path.parent.mkdir(parents=True, exist_ok=True)

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load():
    if not path.is_file():
        return {"schema": "standing-todos/v1", "updated": None, "items": []}
    return json.loads(path.read_text())

@contextmanager
def board_lock():
    """Serialize read-modify-write across concurrent editors/agents via flock."""
    target = path.resolve()
    inplace = os.environ.get("STANDING_TODOS_INPLACE") == "1"
    if not inplace:
        target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        if not inplace:
            raise
        # Read-only parent fallback: lock the target file itself.
        fd = os.open(target, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

def save(doc):
    doc["updated"] = now()
    payload = (json.dumps(doc, indent=2) + "\n").encode()
    # The public path may be a symlink into the one writable directory.
    # Resolve first so replace() updates the file and leaves the link in place.
    target = path.resolve()
    inplace = os.environ.get("STANDING_TODOS_INPLACE") == "1"
    if not inplace:
        target.parent.mkdir(parents=True, exist_ok=True)
    # grok-mcp sets STANDING_TODOS_INPLACE=1. ProtectHome leaves the parent
    # read-only, so that mode must not create a sibling temp.
    if inplace:
        flags = os.O_WRONLY | os.O_TRUNC
        if not target.exists():
            flags |= os.O_CREAT
        fd = os.open(target, flags, 0o644)
        try:
            view = memoryview(payload)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)

def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[:40] or "item")

if not args:
    args = ["list"]

cmd = args[0]

def priority_key(item):
    raw = item.get("priority")
    try:
        return int(raw) if raw is not None else 5
    except (TypeError, ValueError):
        return 5

if cmd == "list":
    doc = load()
    open_items = sorted(
        (i for i in doc["items"] if i.get("status") == "open"),
        key=priority_key,
    )
    done = [i for i in doc["items"] if i.get("status") != "open"]
    print(f"open={len(open_items)} done={len(done)} updated={doc.get('updated')}")
    for i in open_items:
        print(f"  [{i.get('priority',5)}] {i['id']}  owner={i.get('owner','either')}  author={i.get('author') or 'unknown (legacy)'}  {i['title']}")
        if i.get("blocked_on"):
            print(f"       blocked_on: {i['blocked_on']}")
        if i.get("plain"):
            print(f"       plain: {i['plain']}")
        if i.get("next_action"):
            print(f"       next: {i['next_action']}")
    sys.exit(0)

if cmd == "add":
    rest = args[1:]
    if not rest:
        print("usage: standing-todos.sh add \"title\" --author \"name / agent\" [--owner agent|human|either] [--priority N] [--blocked-on x] [--next x] [--plain x]", file=sys.stderr)
        sys.exit(2)
    title = rest[0]
    owner, priority, blocked, nxt, plain, author = "either", 2, None, None, None, None
    i = 1
    while i < len(rest):
        if rest[i] == "--author" and i + 1 < len(rest):
            author = rest[i + 1].strip(); i += 2
        elif rest[i] == "--owner" and i + 1 < len(rest):
            owner = rest[i + 1]; i += 2
        elif rest[i] == "--priority" and i + 1 < len(rest):
            priority = int(rest[i + 1]); i += 2
        elif rest[i] == "--blocked-on" and i + 1 < len(rest):
            blocked = rest[i + 1]; i += 2
        elif rest[i] == "--next" and i + 1 < len(rest):
            nxt = rest[i + 1]; i += 2
        elif rest[i] == "--plain" and i + 1 < len(rest):
            plain = rest[i + 1]; i += 2
        else:
            i += 1
    if not author or len(author) > 100 or "\n" in author or "\r" in author:
        print("--author is required: sign with your name / agent identity (one line, up to 100 characters)", file=sys.stderr)
        sys.exit(2)
    with board_lock():
        doc = load()
        item_id = f"{slug(title)}-{uuid.uuid4().hex[:6]}"
        doc["items"].append({
            "id": item_id,
            "title": title,
            "status": "open",
            "owner": owner,
            "author": author,
            "priority": priority,
            "blocked_on": blocked,
            "next_action": nxt,
            "created": now(),
            "updated": now(),
            "notes": [],
        })
        if isinstance(plain, str) and plain.strip():
            doc["items"][-1]["plain"] = plain.strip()
        save(doc)
    print(item_id)
    sys.exit(0)

if cmd == "done":
    if len(args) < 2:
        print("usage: standing-todos.sh done <id>", file=sys.stderr)
        sys.exit(2)
    iid = args[1]
    with board_lock():
        doc = load()
        found = False
        for it in doc["items"]:
            if it["id"] == iid or it["id"].startswith(iid):
                it["status"] = "done"
                it["updated"] = now()
                found = True
                print(it["id"])
                break
        if not found:
            print(f"not found: {iid}", file=sys.stderr)
            sys.exit(1)
        save(doc)
    sys.exit(0)

if cmd == "plain":
    if len(args) < 3:
        print("usage: standing-todos.sh plain <id> \"text\"", file=sys.stderr)
        sys.exit(2)
    iid, text = args[1], args[2].strip()
    with board_lock():
        doc = load()
        for it in doc["items"]:
            if it["id"] == iid or it["id"].startswith(iid):
                if text:
                    it["plain"] = text
                else:
                    it.pop("plain", None)
                it["updated"] = now()
                save(doc)
                print(it["id"])
                sys.exit(0)
    print(f"not found: {iid}", file=sys.stderr)
    sys.exit(1)

if cmd == "note":
    if len(args) < 3:
        print("usage: standing-todos.sh note <id> \"text\"", file=sys.stderr)
        sys.exit(2)
    iid, text = args[1], args[2]
    with board_lock():
        doc = load()
        for it in doc["items"]:
            if it["id"] == iid or it["id"].startswith(iid):
                it.setdefault("notes", []).append({"ts": now(), "text": text})
                it["updated"] = now()
                save(doc)
                print(it["id"])
                sys.exit(0)
    print(f"not found: {iid}", file=sys.stderr)
    sys.exit(1)

if cmd == "priority":
    if len(args) < 3:
        print("usage: standing-todos.sh priority <id> <1-5>", file=sys.stderr)
        sys.exit(2)
    iid = args[1]
    try:
        prio = int(args[2])
    except ValueError:
        print("priority must be an integer 1-5", file=sys.stderr)
        sys.exit(2)
    if not 1 <= prio <= 5:
        print("priority must be between 1 and 5", file=sys.stderr)
        sys.exit(2)
    with board_lock():
        doc = load()
        for it in doc["items"]:
            if it["id"] == iid or it["id"].startswith(iid):
                it["priority"] = prio
                it["updated"] = now()
                save(doc)
                print(it["id"])
                sys.exit(0)
    print(f"not found: {iid}", file=sys.stderr)
    sys.exit(1)

print(f"unknown command: {cmd}", file=sys.stderr)
sys.exit(2)
PY
