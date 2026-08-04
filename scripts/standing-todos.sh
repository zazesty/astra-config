#!/usr/bin/env bash
# standing-todos.sh — open work board (not system status).
# State: ~/.local/state/astra/standing-todos.json
#
#   standing-todos.sh list
#   standing-todos.sh add "title" [--owner agent|human|either] [--priority 1-5] [--blocked-on text]
#   standing-todos.sh done <id>
#   standing-todos.sh note <id> "text"
set -euo pipefail
export TODOS_PATH="${TODOS_PATH:-$HOME/.local/state/astra/standing-todos.json}"
exec python3 - "$TODOS_PATH" "$@" <<'PY'
import json, sys, re, uuid
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
args = sys.argv[2:]
path.parent.mkdir(parents=True, exist_ok=True)

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load():
    if not path.is_file():
        return {"schema": "standing-todos/v1", "updated": None, "items": []}
    return json.loads(path.read_text())

def save(doc):
    doc["updated"] = now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.replace(path)

def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[:40] or "item")

if not args:
    args = ["list"]

cmd = args[0]

if cmd == "list":
    doc = load()
    open_items = [i for i in doc["items"] if i.get("status") == "open"]
    done = [i for i in doc["items"] if i.get("status") != "open"]
    print(f"open={len(open_items)} done={len(done)} updated={doc.get('updated')}")
    for i in open_items:
        print(f"  [{i.get('priority',5)}] {i['id']}  owner={i.get('owner','either')}  {i['title']}")
        if i.get("blocked_on"):
            print(f"       blocked_on: {i['blocked_on']}")
        if i.get("next_action"):
            print(f"       next: {i['next_action']}")
    sys.exit(0)

if cmd == "add":
    rest = args[1:]
    if not rest:
        print("usage: standing-todos.sh add \"title\" [--owner agent|human|either] [--priority N] [--blocked-on x] [--next x]", file=sys.stderr)
        sys.exit(2)
    title = rest[0]
    owner, priority, blocked, nxt = "either", 3, None, None
    i = 1
    while i < len(rest):
        if rest[i] == "--owner" and i + 1 < len(rest):
            owner = rest[i + 1]; i += 2
        elif rest[i] == "--priority" and i + 1 < len(rest):
            priority = int(rest[i + 1]); i += 2
        elif rest[i] == "--blocked-on" and i + 1 < len(rest):
            blocked = rest[i + 1]; i += 2
        elif rest[i] == "--next" and i + 1 < len(rest):
            nxt = rest[i + 1]; i += 2
        else:
            i += 1
    doc = load()
    item_id = f"{slug(title)}-{uuid.uuid4().hex[:6]}"
    doc["items"].append({
        "id": item_id,
        "title": title,
        "status": "open",
        "owner": owner,
        "priority": priority,
        "blocked_on": blocked,
        "next_action": nxt,
        "created": now(),
        "updated": now(),
        "notes": [],
    })
    save(doc)
    print(item_id)
    sys.exit(0)

if cmd == "done":
    if len(args) < 2:
        print("usage: standing-todos.sh done <id>", file=sys.stderr)
        sys.exit(2)
    iid = args[1]
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

if cmd == "note":
    if len(args) < 3:
        print("usage: standing-todos.sh note <id> \"text\"", file=sys.stderr)
        sys.exit(2)
    iid, text = args[1], args[2]
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

print(f"unknown command: {cmd}", file=sys.stderr)
sys.exit(2)
PY
