#!/usr/bin/env python3
"""Refresh Open WebUI's existing Astra connector after an MCP mount rotation.

The mount is sent on docker stdin. Dashboard credentials stay in the container;
neither credentials nor connector URLs are printed or passed in process argv.
"""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ENV_FILE = Path("/etc/grok-mcp.env")
CONTAINER = "open-webui"

CONTAINER_SCRIPT = r'''
import copy
import json
import os
import sqlite3
import sys
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import jwt


def sync():
    payload = json.load(sys.stdin)
    mount = payload["mount"]
    parsed_mount = urlsplit(mount)
    if (not isinstance(mount, str) or not mount.startswith("/") or mount.startswith("//")
            or parsed_mount.path != mount or parsed_mount.scheme or parsed_mount.netloc
            or parsed_mount.query or parsed_mount.fragment or any(c.isspace() for c in mount)):
        raise ValueError("Invalid mount")
    secret = os.environ.get("WEBUI_SECRET_KEY")
    if not secret:
        raise ValueError("Missing dashboard secret")
    db = sqlite3.connect("file:/app/backend/data/webui.db?mode=ro", uri=True)
    try:
        admin = db.execute(
            'SELECT u.id, a.session_stamp FROM "user" u JOIN auth a ON a.id = u.id '
            "WHERE u.role = 'admin' ORDER BY u.id LIMIT 1"
        ).fetchone()
    finally:
        db.close()
    if not admin:
        raise ValueError("No admin")
    now = int(time.time())
    token = jwt.encode({"id": admin[0], "session_stamp": admin[1],
                        "jti": uuid.uuid4().hex, "iat": now, "exp": now + 300},
                       secret, algorithm="HS256")
    endpoint = "http://127.0.0.1:8080/api/v1/configs/tool_servers"

    def api(body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(endpoint, data=data, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="GET" if body is None else "POST")
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    original = api()
    connections = original["TOOL_SERVER_CONNECTIONS"]
    if not isinstance(connections, list):
        raise ValueError("Invalid connections")
    matches = [i for i, connection in enumerate(connections)
               if isinstance(connection, dict) and isinstance(connection.get("info"), dict)
               and connection["info"].get("id") == "astra"]
    if len(matches) != 1:
        raise ValueError("Astra connection missing or ambiguous")
    index = matches[0]
    current = connections[index]["url"]
    parts = urlsplit(current)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Invalid connector URL")
    updated_url = urlunsplit((parts.scheme, parts.netloc, mount, parts.query, parts.fragment))
    if updated_url == current:
        return "unchanged"
    updated = copy.deepcopy(original)
    updated["TOOL_SERVER_CONNECTIONS"][index]["url"] = updated_url
    api(updated)
    verified = api()
    if verified.get("TOOL_SERVER_CONNECTIONS") != updated["TOOL_SERVER_CONNECTIONS"]:
        raise ValueError("Connector verification failed")
    return "updated"


try:
    print(json.dumps({"status": sync()}))
except Exception:
    print("Open WebUI Astra sync failed.", file=sys.stderr)
    raise SystemExit(1)
'''


def read_mount(path: Path = ENV_FILE) -> str:
    values = []
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "MCP_PATH":
            parsed = shlex.split(value, comments=True)
            if len(parsed) != 1:
                raise ValueError("Invalid mount setting")
            values.append(parsed[0].split(",", 1)[0].strip())
    if len(values) != 1:
        raise ValueError("Missing or ambiguous mount setting")
    mount = values[0]
    parts = urlsplit(mount)
    if (not mount.startswith("/") or mount.startswith("//") or parts.path != mount
            or parts.scheme or parts.netloc or parts.query or parts.fragment
            or any(char.isspace() for char in mount)):
        raise ValueError("Invalid mount setting")
    return mount


def main() -> int:
    try:
        status = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", CONTAINER],
            capture_output=True, text=True, timeout=10, check=False)
        if status.returncode:
            missing = any(word in status.stderr.lower() for word in ("no such object", "no such container"))
            if not missing:
                raise RuntimeError("Container inspection failed")
            print("sync-open-webui-astra-mcp: container absent; skipped")
            return 0
        if status.stdout.strip() != "true":
            raise RuntimeError("Container is not running")
        mount = read_mount()
        result = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "python", "-c", CONTAINER_SCRIPT],
            input=json.dumps({"mount": mount}), capture_output=True, text=True,
            timeout=70, check=False)
        if result.returncode:
            raise RuntimeError("Container sync failed")
        result_status = json.loads(result.stdout).get("status")
        if result_status not in ("updated", "unchanged"):
            raise RuntimeError("Invalid sync result")
        print(f"sync-open-webui-astra-mcp: Astra connector {result_status} (URL redacted)")
        return 0
    except FileNotFoundError:
        # A missing Docker binary and a missing env file are distinct: the latter
        # is a configured container whose connector cannot be synchronized.
        if not shutil.which("docker"):
            print("sync-open-webui-astra-mcp: Docker absent; skipped")
            return 0
    except Exception:
        pass
    print("sync-open-webui-astra-mcp: sync failed; connector needs attention", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
