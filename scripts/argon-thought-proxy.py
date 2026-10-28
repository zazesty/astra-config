#!/usr/bin/python3
"""Local proxy that puts Gemini thought signatures back on tool calls.

Grok 1.0.60 keeps a tool call as id, name, and arguments. Gemini 4 rejects
the next request unless extra_content.google.thought_signature is present.
This process caches that field from Google's response and injects it when
the client omits it. A cache miss sends Google's
skip_thought_signature_validator sentinel, which gemini-4-argon accepts.

Binds 127.0.0.1 only. Does not log prompts, keys, or signature values.

  argon-thought-proxy.py              serve
  argon-thought-proxy.py --self-test  offline checks
  argon-thought-proxy.py --live-test  one tool round-trip through the proxy
  argon-thought-proxy.py --point-config [path]
                                      point [model.argon] and [model.flash]
                                      at this proxy; pin general-purpose to flash
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = os.environ.get("ARGON_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARGON_PROXY_PORT", "8791"))
UPSTREAM = os.environ.get(
    "ARGON_PROXY_UPSTREAM", "https://generativelanguage.googleapis.com"
).rstrip("/")
PROXY_BASE = f"http://{HOST}:{PORT}/v1beta/openai"
SENTINEL = "skip_thought_signature_validator"
CACHE_MAX = int(os.environ.get("ARGON_PROXY_CACHE_MAX", "5000"))
CACHE_TTL_S = int(os.environ.get("ARGON_PROXY_CACHE_TTL_S", "3600"))
HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}

# None means a known unsigned parallel sibling. Absent means a cache miss.
_cache: "OrderedDict[str, tuple[str | None, float]]" = OrderedDict()
_cache_lock = threading.Lock()


def cache_set(tool_id: str, signature: str | None) -> None:
    now = time.time()
    with _cache_lock:
        _cache[tool_id] = (signature, now + CACHE_TTL_S)
        _cache.move_to_end(tool_id)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def cache_get(tool_id: str) -> tuple[str, str | None]:
    """Return ('signed', sig), ('unsigned', None), or ('miss', None)."""
    now = time.time()
    with _cache_lock:
        item = _cache.get(tool_id)
        if item is None:
            return "miss", None
        signature, expiry = item
        if expiry < now:
            del _cache[tool_id]
            return "miss", None
        _cache.move_to_end(tool_id)
        if signature is None:
            return "unsigned", None
        return "signed", signature


def cache_len() -> int:
    with _cache_lock:
        return len(_cache)


def extract_signature(tool_call: Any) -> str | None:
    if not isinstance(tool_call, dict):
        return None
    extra = tool_call.get("extra_content")
    if isinstance(extra, dict):
        google = extra.get("google")
        if isinstance(google, dict):
            sig = google.get("thought_signature")
            if isinstance(sig, str) and sig.strip():
                return sig.strip()
    top = tool_call.get("thought_signature")
    if isinstance(top, str) and top.strip():
        return top.strip()
    return None


def _with_signature(tool_call: dict, signature: str) -> dict:
    extra = tool_call.get("extra_content")
    extra = dict(extra) if isinstance(extra, dict) else {}
    google = extra.get("google")
    google = dict(google) if isinstance(google, dict) else {}
    google["thought_signature"] = signature
    extra["google"] = google
    patched = dict(tool_call)
    patched.pop("thought_signature", None)
    patched["extra_content"] = extra
    return patched


def _commit_group(tool_calls: list) -> tuple[int, int]:
    collected: list[tuple[str, str | None]] = []
    for raw in tool_calls:
        if not isinstance(raw, dict):
            continue
        tool_id = raw.get("id")
        if not isinstance(tool_id, str) or not tool_id:
            continue
        collected.append((tool_id, extract_signature(raw)))
    if not any(sig is not None for _, sig in collected):
        return 0, 0
    signed = unsigned = 0
    for tool_id, sig in collected:
        cache_set(tool_id, sig)
        if sig is None:
            unsigned += 1
        else:
            signed += 1
    return signed, unsigned


def extract_json(body: Any) -> tuple[int, int]:
    if not isinstance(body, dict):
        return 0, 0
    choices = body.get("choices")
    if not isinstance(choices, list):
        return 0, 0
    signed = unsigned = 0
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            s, u = _commit_group(tool_calls)
            signed += s
            unsigned += u
    return signed, unsigned


class SseExtractor:
    """Pull signatures out of an OpenAI SSE stream. Commit on [DONE] only."""

    def __init__(self) -> None:
        self._buf = ""
        self._done = False
        self._finished = False
        self._stats = (0, 0)
        self._choices: dict[int, dict[int, dict]] = {}

    def feed(self, text: str) -> None:
        if self._finished or self._done:
            return
        self._buf += text
        while True:
            cut = self._buf.find("\n\n")
            sep = 2
            if cut < 0:
                cut = self._buf.find("\r\n\r\n")
                sep = 4
            if cut < 0:
                return
            frame = self._buf[:cut]
            self._buf = self._buf[cut + sep :]
            self._frame(frame)
            if self._done:
                return

    def finish(self) -> tuple[int, int]:
        if self._finished:
            return self._stats
        if not self._done and self._buf.strip():
            self.feed("\n\n")
        self._finished = True
        if not self._done:
            self._choices.clear()
            self._buf = ""
            return (0, 0)
        return self._stats

    def _frame(self, frame: str) -> None:
        data: list[str] = []
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            data.append(line[5:].removeprefix(" "))
        if not data:
            return
        joined = "\n".join(data).strip()
        if joined == "[DONE]":
            self._done = True
            self._stats = self._commit()
            return
        if "tool_calls" not in joined and "thought_signature" not in joined:
            return
        try:
            payload = json.loads(joined)
        except json.JSONDecodeError:
            return
        self._accumulate(payload)

    def _accumulate(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            index = choice.get("index")
            choice_index = index if isinstance(index, int) else 0
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            tool_calls = delta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            group = self._choices.setdefault(choice_index, {})
            for i, raw in enumerate(tool_calls):
                if not isinstance(raw, dict):
                    continue
                tc_index = raw.get("index")
                tc_index = tc_index if isinstance(tc_index, int) else i
                acc = group.setdefault(tc_index, {})
                tool_id = raw.get("id")
                if "id" not in acc and isinstance(tool_id, str) and tool_id:
                    acc["id"] = tool_id
                if "signature" not in acc:
                    sig = extract_signature(raw)
                    if sig is not None:
                        acc["signature"] = sig

    def _commit(self) -> tuple[int, int]:
        signed = unsigned = 0
        for group in self._choices.values():
            if not any("signature" in acc for acc in group.values()):
                continue
            for acc in group.values():
                tool_id = acc.get("id")
                if not isinstance(tool_id, str):
                    continue
                sig = acc.get("signature")
                cache_set(tool_id, sig if isinstance(sig, str) else None)
                if isinstance(sig, str):
                    signed += 1
                else:
                    unsigned += 1
        self._choices.clear()
        self._buf = ""
        return signed, unsigned


def patch_body(body: dict) -> tuple[dict, int, int]:
    """Return (body, injected_real, injected_sentinel). Same object if unchanged."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, 0, 0
    real = sentinel = 0
    changed = False
    new_messages = []
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or not isinstance(message.get("tool_calls"), list)
        ):
            new_messages.append(message)
            continue
        tool_calls = message["tool_calls"]
        patched_calls = []
        message_changed = False
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                patched_calls.append(tool_call)
                continue
            tool_id = tool_call.get("id")
            existing = extract_signature(tool_call)
            documented = (
                isinstance(tool_call.get("extra_content"), dict)
                and isinstance(tool_call["extra_content"].get("google"), dict)
                and isinstance(
                    tool_call["extra_content"]["google"].get("thought_signature"), str
                )
                and tool_call["extra_content"]["google"]["thought_signature"].strip()
            )
            if documented:
                patched_calls.append(tool_call)
                continue
            if existing is not None:
                patched_calls.append(_with_signature(tool_call, existing))
                message_changed = True
                real += 1
                continue
            if not isinstance(tool_id, str) or not tool_id:
                patched_calls.append(tool_call)
                continue
            state, sig = cache_get(tool_id)
            if state == "signed" and sig is not None:
                patched_calls.append(_with_signature(tool_call, sig))
                message_changed = True
                real += 1
            elif state == "unsigned":
                patched_calls.append(tool_call)
            else:
                patched_calls.append(_with_signature(tool_call, SENTINEL))
                message_changed = True
                sentinel += 1
        if message_changed:
            changed = True
            updated = dict(message)
            updated["tool_calls"] = patched_calls
            new_messages.append(updated)
        else:
            new_messages.append(message)
    if not changed:
        return body, 0, 0
    patched = dict(body)
    patched["messages"] = new_messages
    return patched, real, sentinel


def _log(line: str) -> None:
    sys.stderr.write(line.rstrip() + "\n")
    sys.stderr.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", "/health"):
            payload = json.dumps({"status": "ok", "cache": cache_len()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        path = self.path
        upstream_url = UPSTREAM + path
        body = raw
        real = sentinel = 0
        route = path.split("?", 1)[0]
        if self.command == "POST" and route.endswith("/chat/completions") and raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                parsed, real, sentinel = patch_body(parsed)
                if real or sentinel:
                    body = json.dumps(parsed, ensure_ascii=False).encode()
        headers = {}
        for key, value in self.headers.items():
            if key.lower() in HOP:
                continue
            headers[key] = value
        headers["Accept-Encoding"] = "identity"
        headers["Content-Length"] = str(len(body))
        req = urllib.request.Request(upstream_url, data=body or None, headers=headers, method=self.command)
        started = time.time()
        try:
            upstream = urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as err:
            upstream = err
        except Exception as err:
            msg = b'{"error":"argon proxy upstream failed"}'
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            _log(f"argon-proxy {self.command} {route} 502 {type(err).__name__}")
            return
        status = getattr(upstream, "status", None) or upstream.getcode()
        ctype = upstream.headers.get("Content-Type", "")
        streaming = "text/event-stream" in ctype
        signed = unsigned = 0
        try:
            if streaming:
                self.send_response(status)
                for key, value in upstream.headers.items():
                    if key.lower() in HOP:
                        continue
                    self.send_header(key, value)
                self.end_headers()
                extractor = SseExtractor()
                decoder_buf = b""
                client_gone = False
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    if not client_gone:
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            client_gone = True
                    decoder_buf += chunk
                    text, decoder_buf = _decode_prefix(decoder_buf)
                    if text:
                        extractor.feed(text)
                if decoder_buf:
                    extractor.feed(decoder_buf.decode("utf-8", "replace"))
                signed, unsigned = extractor.finish()
            else:
                payload = upstream.read()
                if route.endswith("/chat/completions") and payload:
                    try:
                        signed, unsigned = extract_json(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
                try:
                    self.send_response(status)
                    for key, value in upstream.headers.items():
                        if key.lower() in HOP:
                            continue
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            upstream.close()
        elapsed_ms = int((time.time() - started) * 1000)
        _log(
            f"argon-proxy {self.command} {route} {status} "
            f"inject={real} sentinel={sentinel} cached={signed} unsigned={unsigned} "
            f"{elapsed_ms}ms"
        )


def _decode_prefix(buf: bytes) -> tuple[str, bytes]:
    """Decode a UTF-8 prefix, keeping a trailing partial sequence."""
    if not buf:
        return "", b""
    try:
        return buf.decode("utf-8"), b""
    except UnicodeDecodeError as err:
        if err.start == 0:
            return buf[:1].decode("utf-8", "replace"), buf[1:]
        return buf[: err.start].decode("utf-8"), buf[err.start :]


def serve() -> None:
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("argon-thought-proxy refuses a non-loopback bind")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    _log(f"argon-thought-proxy listening http://{HOST}:{PORT}")
    httpd.serve_forever()


def _set_key(lines: list[str], section: str, key: str, value: str) -> list[str]:
    """Set key inside a toml table. Append the table when it is missing."""
    header = f"[{section}]"
    out: list[str] = []
    in_section = False
    found = False
    saw_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not found:
                out.append(f'{key} = "{value}"\n')
                found = True
            in_section = stripped == header
            if in_section:
                saw_section = True
            out.append(line if line.endswith("\n") else line + "\n")
            continue
        if in_section and stripped.startswith(f"{key} ") or (
            in_section and stripped.startswith(f"{key}=")
        ):
            out.append(f'{key} = "{value}"\n')
            found = True
            continue
        out.append(line if line.endswith("\n") else line + "\n")
    if in_section and not found:
        out.append(f'{key} = "{value}"\n')
        found = True
    if not saw_section:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if out and out[-1].strip():
            out.append("\n")
        out.append(header + "\n")
        out.append(f'{key} = "{value}"\n')
    return out


def point_config(path: str) -> None:
    with open(path, encoding="utf-8") as handle:
        original = handle.read()
    lines = original.splitlines(keepends=True)
    if not lines:
        lines = []
    lines = _set_key(lines, "model.argon", "base_url", PROXY_BASE)
    lines = _set_key(lines, "model.argon", "model", "gemini-4-argon")
    lines = _set_key(lines, "model.argon", "env_key", "GEMINI_API_KEY")
    lines = _set_key(lines, "model.argon", "api_backend", "chat_completions")
    lines = _set_key(lines, "model.argon", "stream_tool_calls", "false")
    lines = _set_key(lines, "model.flash", "base_url", PROXY_BASE)
    lines = _set_key(lines, "model.flash", "model", "gemini-3.8-flash")
    lines = _set_key(lines, "model.flash", "env_key", "GEMINI_API_KEY")
    lines = _set_key(lines, "model.flash", "api_backend", "chat_completions")
    lines = _set_key(lines, "model.flash", "stream_tool_calls", "false")
    lines = _set_key(lines, "subagents.models", "argon", "argon")
    lines = _set_key(lines, "subagents.models", "flash", "flash")
    lines = _set_key(lines, "subagents.models", "general-purpose", "flash")
    # _set_key quotes every value. stream_tool_calls is a bare bool.
    text = "".join(lines)
    text = text.replace('stream_tool_calls = "false"', "stream_tool_calls = false")
    updated = text
    if updated != original:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)
        _log(f"argon-thought-proxy pointed {path} at the local proxy")
    else:
        _log(f"argon-thought-proxy config already pointed ({path})")


def _self_test() -> int:
    global _cache
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        if not cond:
            ok = False
            print("FAIL", name)
        else:
            print("ok", name)

    _cache = OrderedDict()
    body = {
        "model": "gemini-4-argon",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "grep", "arguments": "{}"}},
                ],
            },
        ],
    }
    patched, real, sentinel = patch_body(body)
    check("miss uses sentinel", sentinel == 2 and real == 0)
    check(
        "sentinel location",
        patched["messages"][1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"]
        == SENTINEL,
    )

    _cache = OrderedDict()
    cache_set("call_a", "sig-real")
    cache_set("call_b", None)
    patched, real, sentinel = patch_body(body)
    calls = patched["messages"][1]["tool_calls"]
    check("real injected", real == 1 and calls[0]["extra_content"]["google"]["thought_signature"] == "sig-real")
    check("sibling stays unsigned", sentinel == 0 and "extra_content" not in calls[1])

    already = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                        "extra_content": {"google": {"thought_signature": "keep-me"}, "other": 1},
                    }
                ],
            }
        ]
    }
    same, real, sentinel = patch_body(already)
    check("documented signature untouched", same is already and real == 0 and sentinel == 0)

    _cache = OrderedDict()
    signed, unsigned = extract_json(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_z",
                                "extra_content": {"google": {"thought_signature": "from-google"}},
                            },
                            {"id": "call_y"},
                        ]
                    }
                }
            ]
        }
    )
    state, sig = cache_get("call_z")
    state_y, _ = cache_get("call_y")
    check("json extract", signed == 1 and unsigned == 1 and state == "signed" and sig == "from-google")
    check("json sibling null", state_y == "unsigned")

    _cache = OrderedDict()
    sse = SseExtractor()
    sse.feed(
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_s","extra_content":{"google":{"thought_signature":"stream-sig"}}}]}}]}\n\n'
    )
    sse.feed("data: [DONE]\n\n")
    s, u = sse.finish()
    state, sig = cache_get("call_s")
    check("sse commits on DONE", s == 1 and u == 0 and state == "signed" and sig == "stream-sig")

    _cache = OrderedDict()
    aborted = SseExtractor()
    aborted.feed(
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abort","extra_content":{"google":{"thought_signature":"nope"}}}]}}]}\n\n'
    )
    aborted.finish()
    check("sse discards without DONE", cache_get("call_abort")[0] == "miss")

    import tempfile

    fixture = (
        "[mcp_servers.astra]\n"
        'url = "http://127.0.0.1:9/mcp/secret-path"\n'
        "enabled = true\n"
        "\n"
        "[subagents.models]\n"
        'explore = "grok-4.7"\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(fixture)
        point_config(path)
        text = open(path, encoding="utf-8").read()
    check("mcp url preserved", 'url = "http://127.0.0.1:9/mcp/secret-path"' in text)
    check("base_url pointed", f'base_url = "{PROXY_BASE}"' in text)
    check("pins added", 'argon = "argon"' in text and 'flash = "flash"' in text and 'general-purpose = "flash"' in text)
    check("flash model", 'model = "gemini-3.8-flash"' in text)
    check("other pin kept", 'explore = "grok-4.7"' in text)
    check("stream flag bare", "stream_tool_calls = false" in text)
    print("argon-thought-proxy self-test:", "pass" if ok else "fail")
    return 0 if ok else 1


def _live_test() -> int:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("argon-thought-proxy live-test: GEMINI_API_KEY unset")
        return 1
    health = urllib.request.urlopen(f"http://{HOST}:{PORT}/healthz", timeout=5)
    if health.status != 200:
        print("argon-thought-proxy live-test: health", health.status)
        return 1
    url = f"http://{HOST}:{PORT}/v1beta/openai/chat/completions"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo_ok",
                "description": "Return ok.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    def post(payload: dict) -> tuple[int, dict | None]:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            snippet = err.read().decode("utf-8", "replace")
            missing = "thought_signature" in snippet
            print("live http", err.code, "missing_signature" if missing else "other")
            return err.code, None

    status, first = post(
        {
            "model": "gemini-4-argon",
            "messages": [{"role": "user", "content": "Call echo_ok now."}],
            "tools": tools,
            "tool_choice": {"type": "function", "function": {"name": "echo_ok"}},
            "max_tokens": 64,
        }
    )
    if status != 200 or not first:
        print("live first", status)
        return 1
    tool_call = first["choices"][0]["message"]["tool_calls"][0]
    had = extract_signature(tool_call) is not None
    bare = {k: tool_call[k] for k in ("id", "type", "function")}
    replay_status, _ = post(
        {
            "model": "gemini-4-argon",
            "messages": [
                {"role": "user", "content": "Call echo_ok now."},
                {"role": "assistant", "tool_calls": [bare]},
                {"role": "tool", "tool_call_id": bare["id"], "content": "ok"},
            ],
            "tools": tools,
            "max_tokens": 32,
        }
    )
    print(f"live had_signature={had} replay={replay_status}")
    return 0 if had and replay_status == 200 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    if "--live-test" in sys.argv:
        return _live_test()
    if "--point-config" in sys.argv:
        idx = sys.argv.index("--point-config")
        path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "/root/.grok/config.toml"
        point_config(path)
        return 0
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
