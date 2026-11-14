"""Authenticated, no-model shortcuts for Hermes' native session-chat API."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid

from aiohttp import web

from actions import handle
from classify import classify

logger = logging.getLogger("plugins.photon-canned.api")
_CHAT_PATH = re.compile(r"^(?:/p/([^/]+))?/api/sessions/([^/]+)/chat(/stream)?$")
_AUTHOR = "Zavdi via Conduit"


def _error(message: str, status: int, code: str) -> web.Response:
    return web.json_response({"error": {"message": message, "code": code}}, status=status)


def _plain_message(body):
    if not isinstance(body, dict):
        return None
    # A shortcut must never consume an attachment-bearing request as if its text
    # were the whole request. Native multimodal arrays follow the normal route.
    if any(body.get(key) for key in ("attachments", "images", "files", "audio")):
        return None
    message = body.get("message") or body.get("input")
    return message if isinstance(message, str) else None


async def _execute(adapter, ctx, intent):
    db = await adapter._ensure_session_db_async()
    if db is None:
        return None, _error("Session history is unavailable; shortcut not run.", 503, "session_db_unavailable")
    session_id = ctx["session_id"]
    holder = f"canned:pid={os.getpid()}:{uuid.uuid4().hex}"
    acquired = await asyncio.to_thread(
        db.try_acquire_session_turn_lease, session_id, holder, ttl_seconds=60, patience_s=0.5)
    if not acquired:
        return None, _error("This conversation is busy; shortcut not run.", 409, "session_busy")

    def commit():
        effective_id = db.get_compression_tip(session_id) or session_id
        reply = handle(intent, author=_AUTHOR)
        if not isinstance(reply, str):
            raise TypeError("Canned action did not return text")
        messages = [{"role": "user", "content": ctx["user_message"]},
                    {"role": "assistant", "content": reply}]
        db.append_messages_batch(effective_id, messages, turn_lease_holder=holder)
        return effective_id, reply

    # A client disconnect cannot cancel a todo write halfway through and release
    # its lease while the worker is still committing the matching transcript.
    task = asyncio.create_task(asyncio.to_thread(commit))
    try:
        try:
            return await asyncio.shield(task), None
        except asyncio.CancelledError:
            await task
            raise
    finally:
        await asyncio.to_thread(db.release_session_turn_lease, session_id, holder)


async def _respond(request, adapter, ctx, intent, result, *, streaming):
    from gateway.platforms.api_server import _SessionEventQueue, _sse_frame

    session_id, reply = result
    runtime = {"canned": True, "shortcut": intent.kind, "model": None}
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "api_calls": 0}
    headers = adapter._session_headers(session_id, ctx["gateway_session_key"])
    headers["X-Hermes-Canned"] = intent.kind
    assistant = {"role": "assistant", "content": reply}
    if not streaming:
        return web.json_response(
            {"object": "hermes.session.chat.completion", "status": "completed",
             "session_id": session_id, "message": assistant, "usage": usage,
             "runtime": runtime}, headers=headers)

    response = web.StreamResponse(headers=adapter._sse_headers(request, extra=headers))
    await response.prepare(request)
    message_id, run_id = f"msg_{uuid.uuid4().hex}", f"run_{uuid.uuid4().hex}"
    events = _SessionEventQueue(session_id, run_id)
    terminal = {"completed": True, "partial": False, "interrupted": False}
    frames = [
        ("run.started", {"user_message": {"role": "user", "content": ctx["user_message"]}, "runtime": runtime}),
        ("message.started", {"message": {"id": message_id, "role": "assistant"}}),
        ("assistant.delta", {"message_id": message_id, "delta": reply}),
        ("assistant.completed", {"message_id": message_id, "content": reply, "runtime": runtime, **terminal}),
        ("run.completed", {"message_id": message_id, "messages": [assistant], "usage": usage, "runtime": runtime, **terminal}),
        ("done", {}),
    ]
    for name, payload in frames:
        name, stamped = events.payload(name, payload)
        await response.write(_sse_frame(stamped, event=name, ensure_ascii=False))
    await response.write_eof()
    return response


def wire_api_canned(app, adapter):
    """Platform-handler factory; install before aiohttp freezes the API app."""
    from gateway.platforms.api_server import _api_request_profile

    @web.middleware
    async def canned(request, handler):
        match = _CHAT_PATH.fullmatch(request.path)
        if request.method != "POST" or match is None:
            return await handler(request)
        if match[1] not in (None, "default") or _api_request_profile.get() not in (None, "default"):
            return await handler(request)
        # Native API's test-only no-key branch is intentionally unavailable here:
        # a canned budget read requires a real configured, authenticated key.
        if not adapter._expected_api_key():
            return adapter._auth_failed_response()
        auth_error = adapter._check_auth(request)
        if auth_error is not None:
            return auth_error
        draining = adapter._draining_response()
        if draining is not None:
            return draining
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return await handler(request)
        message = _plain_message(body)
        intent = classify(message) if message is not None else None
        if intent is None:
            return await handler(request)
        ctx, err = await adapter._prepare_session_chat(request)
        if err is not None:
            return err
        try:
            result, err = await _execute(adapter, ctx, intent)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never retry an action as a model turn: a todo may have been written
            # already. Keep command output and personal data out of error logs.
            logger.error("Canned shortcut failed (%s)", intent.kind)
            return _error("Canned shortcut failed; it was not retried through a model.", 500, "canned_failed")
        if err is not None:
            return err
        return await _respond(request, adapter, ctx, intent, result, streaming=bool(match[3]))

    app.middlewares.append(canned)
